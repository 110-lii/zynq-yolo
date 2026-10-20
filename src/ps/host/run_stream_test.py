#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_stream_test.py -- board-side TCP server for full-network streaming.

Runs on the ZYNQ-7020 board (PYNQ 2.7, Python 3.8, numpy 1.17.4).  It loads
the overlay once, allocates one reusable max-size PYNQ buffer set for all
convolutions (CmaFree is only about 1.4 MB), then serves RGB frames over TCP:

    header (little endian, 20 bytes):
        magic b"ZYOL" | version u16 | frame_id u32 | width u16 | height u16 |
        channels u8 | reserved u8 | payload_len u32
    payload:
        raw RGB888 HWC, width*height*3 bytes

    reply:
        u32 json_len | UTF-8 JSON
        {"frame_id", "ok", "detections", "timings_ms", "output"}

The output tensor (output0, float32) is sent as base64 little-endian bytes.

stdout tokens (ASCII only):
    STREAM_LISTENING <host>:<port>
    STREAM_FRAME_OK <frame_id> <n_det> <total_ms>
    STREAM_BOARD_OK <frames> <mean_ms>
    STREAM_BOARD_FAIL <reason>            (exit code 2)
    STREAM_BOARD_DETAIL <text>            (alloc/init failure diagnostics)

Usage (board, from the repo root):
    sudo python3 src/ps/host/run_stream_test.py --frames 1
    sudo python3 src/ps/host/run_stream_test.py --host 0.0.0.0 --port 5000
"""

import sys

# Must be set before importing board_net / pynq_net: no __pycache__ on board.
sys.dont_write_bytecode = True

import argparse
import base64
import json
import os
import socket
import struct
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
TOOLS_DIR = os.path.join(REPO_ROOT, "tools")
DRIVER_DIR = os.path.join(REPO_ROOT, "src", "ps", "pynq_driver")

if HERE not in sys.path:
    sys.path.insert(0, HERE)
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import board_net  # noqa: E402  (adds TOOLS_DIR itself as well)

DEFAULT_BITFILE = os.path.join(REPO_ROOT, "board", "overlay", "conv_engine.bit")
DEFAULT_PLAN = os.path.join(REPO_ROOT, "data", "layers", "board_net_plan.json")
DEFAULT_LAYERS_DIR = os.path.join(REPO_ROOT, "data", "layers")
DEFAULT_OUT = os.path.join(REPO_ROOT, "data", "results", "board_stream.json")

MAGIC = b"ZYOL"
STREAM_VERSION = 1
HEADER = struct.Struct("<4sHIHHBBI")
U32 = struct.Struct("<I")
MAX_PAYLOAD_BYTES = 64 * 1024 * 1024
MAX_REPLY_BYTES = 8 * 1024 * 1024

EXIT_OK = 0
EXIT_FAIL = 2


class StreamError(Exception):
    """Failure with an ASCII-safe token plus an optional JSON detail."""

    def __init__(self, token, detail=""):
        Exception.__init__(self, detail or token)
        self.token = token
        self.detail = detail or token


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _now_utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _bad_token(reason):
    text = str(reason) or "unknown"
    token = "".join(
        ch if (("0" <= ch <= "9") or ("a" <= ch <= "z")
               or ("A" <= ch <= "Z") or ch in "._-") else "_"
        for ch in text)
    return token or "unknown"


def _ascii_line(text):
    """Printable ASCII for stdout; the exact text stays in the JSON detail."""
    return "".join(
        ch if 32 <= ord(ch) < 127 else "?" for ch in str(text))


def _write_json(path, obj):
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, indent=2, sort_keys=True, ensure_ascii=True)
        fh.write("\n")
    return path


def _parse_args(argv):
    ap = argparse.ArgumentParser(
        description="Board-side full-network streaming server (PYNQ only).")
    ap.add_argument("--host", default="0.0.0.0",
                    help="listen address (default: 0.0.0.0)")
    ap.add_argument("--port", type=int, default=5000,
                    help="listen port (default: 5000)")
    ap.add_argument("--frames", type=int, default=1,
                    help="number of frames to serve before exiting (default: 1)")
    ap.add_argument("--bitfile", default=DEFAULT_BITFILE,
                    help="overlay .bit path (default: board/overlay/conv_engine.bit)")
    ap.add_argument("--plan", default=DEFAULT_PLAN,
                    help="board plan JSON (default: data/layers/board_net_plan.json)")
    ap.add_argument("--layers-dir", default=DEFAULT_LAYERS_DIR,
                    help="directory that plan file paths are relative to "
                         "(default: data/layers)")
    ap.add_argument("--out", default=DEFAULT_OUT,
                    help="result JSON path (default: data/results/board_stream.json)")
    ap.add_argument("--conf", type=float, default=0.25,
                    help="detection score threshold (default: 0.25)")
    ap.add_argument("--iou", type=float, default=0.45,
                    help="NMS IoU threshold (default: 0.45)")
    args = ap.parse_args(argv)
    if args.frames < 1:
        ap.error("--frames must be >= 1")
    return args


def _import_conv_engine():
    if DRIVER_DIR not in sys.path:
        sys.path.insert(0, DRIVER_DIR)
    from conv_engine import ConvEngineOverlay
    return ConvEngineOverlay


# ---------------------------------------------------------------------------
# Wire protocol
# ---------------------------------------------------------------------------
def _recv_exact(sock, n, label):
    """Read exactly n bytes; return b'' only for a clean EOF before byte 0."""
    if n <= 0:
        return b""
    chunks = []
    got = 0
    try:
        while got < n:
            chunk = sock.recv(min(65536, n - got))
            if not chunk:
                if got == 0:
                    return b""
                raise StreamError(
                    "short_%s" % label,
                    "connection closed after %d/%d bytes" % (got, n))
            chunks.append(chunk)
            got += len(chunk)
    except OSError as exc:
        raise StreamError("recv_error", repr(exc))
    return b"".join(chunks)


def _read_header(conn):
    raw = _recv_exact(conn, HEADER.size, "header")
    if raw == b"":
        return None
    magic, version, frame_id, width, height, channels, reserved, payload_len = \
        HEADER.unpack(raw)
    if magic != MAGIC:
        raise StreamError("bad_magic", "magic=%r" % (magic,))
    if version != STREAM_VERSION:
        raise StreamError(
            "bad_version", "version=%d expected %d"
            % (version, STREAM_VERSION))
    if channels != 3:
        raise StreamError("bad_channels", "channels=%d expected 3" % channels)
    if width <= 0 or height <= 0:
        raise StreamError(
            "bad_dimensions", "width=%d height=%d" % (width, height))
    expected = int(width) * int(height) * 3
    if payload_len != expected:
        raise StreamError(
            "bad_payload_len",
            "payload_len=%d expected width*height*3=%d"
            % (payload_len, expected))
    if payload_len > MAX_PAYLOAD_BYTES:
        raise StreamError(
            "payload_too_large",
            "payload_len=%d > %d" % (payload_len, MAX_PAYLOAD_BYTES))
    return {
        "frame_id": int(frame_id),
        "width": int(width),
        "height": int(height),
        "channels": int(channels),
        "reserved": int(reserved),
        "payload_len": int(payload_len),
    }


def _send_reply(conn, obj):
    data = json.dumps(
        obj, ensure_ascii=True, separators=(",", ":"),
        allow_nan=False).encode("utf-8")
    if len(data) > MAX_REPLY_BYTES:
        raise StreamError(
            "reply_too_large", "reply JSON %d bytes > %d"
            % (len(data), MAX_REPLY_BYTES))
    try:
        conn.sendall(U32.pack(len(data)))
        conn.sendall(data)
    except OSError as exc:
        raise StreamError("send_error", repr(exc))


def _reply_obj(frame_id, result):
    det = result["detections"]
    timings = result["timings"]
    out = np.ascontiguousarray(result["output"], dtype="<f4")
    return {
        "frame_id": int(frame_id),
        "ok": True,
        "detections": {
            "boxes": np.asarray(det["boxes"]).tolist(),
            "scores": np.asarray(det["scores"]).tolist(),
            "cls": [int(v) for v in np.asarray(det["cls"]).reshape(-1)],
            "names": [str(v) for v in det["names"]],
        },
        "timings_ms": {
            "total": float(timings["total_ms"]),
            "conv": float(timings["conv_ms"]),
            "other": float(timings["other_ms"]),
        },
        "output": {
            "shape": [int(v) for v in out.shape],
            "dtype": "float32",
            "data_b64": base64.b64encode(
                out.tobytes(order="C")).decode("ascii"),
        },
    }


def _frame_record(frame_id, result):
    """Compact per-frame record for the results JSON (no output blob)."""
    det = result["detections"]
    timings = result["timings"]
    out = np.asarray(result["output"])
    return {
        "frame_id": int(frame_id),
        "n_det": int(len(det["names"])),
        "detections": {
            "boxes": np.asarray(det["boxes"]).tolist(),
            "scores": np.asarray(det["scores"]).tolist(),
            "cls": [int(v) for v in np.asarray(det["cls"]).reshape(-1)],
            "names": [str(v) for v in det["names"]],
        },
        "timings_ms": {
            "total": float(timings["total_ms"]),
            "conv": float(timings["conv_ms"]),
            "other": float(timings["other_ms"]),
        },
        "output": {
            "shape": [int(v) for v in out.shape],
            "dtype": "float32",
        },
    }


# ---------------------------------------------------------------------------
# Setup + server loop
# ---------------------------------------------------------------------------
def _base_result(args):
    return {
        "tool": "src/ps/host/run_stream_test.py",
        "status": "",
        "reason": "",
        "detail": "",
        "generated_utc": _now_utc(),
        "repo_root": REPO_ROOT,
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "bitfile": args.bitfile,
        "plan": args.plan,
        "layers_dir": args.layers_dir,
        "listen": "%s:%d" % (args.host, int(args.port)),
        "frames_requested": int(args.frames),
        "frames_done": 0,
        "mean_total_ms": 0.0,
        "conf": float(args.conf),
        "iou": float(args.iou),
        "frames": [],
        "clients": [],
        "notes": [
            "total_ms is measured around one whole frame "
            "(preprocess + network + decode + NMS).",
            "conv/other timings come from pynq_net.run_network stats.",
            "Reply carries output0 as base64 little-endian float32; "
            "the saved JSON keeps only its shape to stay small.",
            "Unsupported conv layers fall back to pynq_net.NumpyBackend.",
            "One reusable max-size PYNQ buffer set is used because CmaFree "
            "on the running board is only about 1.4 MB.",
            "If PYNQ allocation fails, the exact exception is printed after "
            "STREAM_BOARD_FAIL and stored verbatim in this JSON.",
        ],
    }


def _setup(args, result):
    if not os.path.isfile(args.bitfile):
        raise StreamError("overlay_missing", args.bitfile)
    try:
        ConvEngineOverlay = _import_conv_engine()
    except ImportError as exc:
        raise StreamError("not_on_board", str(exc))

    try:
        overlay = ConvEngineOverlay(args.bitfile)
    except Exception as exc:  # noqa: BLE001 - board errors are not enumerable
        raise StreamError("overlay_load_error", repr(exc))

    try:
        result["windows"] = overlay.describe()
    except Exception as exc:  # noqa: BLE001
        result["windows"] = "describe() unavailable: %r" % (exc,)
    try:
        result["ip_dict_keys"] = list(overlay.ip_keys)
    except Exception:  # noqa: BLE001
        result["ip_dict_keys"] = []

    try:
        plan = board_net.load_plan(args.plan)
    except Exception as exc:  # noqa: BLE001
        raise StreamError("plan_load_error", repr(exc))
    result["input_shape"] = [int(v) for v in plan.graph.input_shape]
    result["output_name"] = plan.output_name

    try:
        entries = plan.conv_entries()
        plans = [board_net.make_layer_plan(entry, args.layers_dir)
                 for entry in entries]
    except Exception as exc:  # noqa: BLE001
        raise StreamError("layer_plan_error", repr(exc))

    try:
        driver = board_net.BoardConvDriver(overlay, plan, args.layers_dir)
    except board_net.BoardBufferAllocError as exc:
        raise StreamError("buffer_alloc_error", str(exc))
    except Exception as exc:  # noqa: BLE001
        raise StreamError("driver_init_error", repr(exc))

    backend = board_net.BoardBackend(driver)
    result["conv_layers"] = len(plans)
    result["layers"] = int(driver.layer_count)
    result["buffers"] = dict(driver.buffer_bytes)
    return overlay, plan, plans, backend


def _serve(args, plan, plans, backend, result):
    graph = plan.graph
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    frames_done = 0
    total_ms_sum = 0.0
    try:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind((args.host, int(args.port)))
            server.listen(1)
        except OSError as exc:
            raise StreamError("listen_error", repr(exc))

        # Contract: this exact line is printed and flushed before accept().
        print("STREAM_LISTENING %s:%d" % (args.host, int(args.port)),
              flush=True)

        while frames_done < args.frames:
            try:
                conn, addr = server.accept()
            except OSError as exc:
                raise StreamError("accept_error", repr(exc))
            result["clients"].append("%s:%d" % (str(addr[0]), int(addr[1])))
            try:
                while frames_done < args.frames:
                    header = _read_header(conn)
                    if header is None:
                        break  # client closed cleanly between frames

                    payload = _recv_exact(
                        conn, header["payload_len"], "payload")
                    if len(payload) != header["payload_len"]:
                        raise StreamError(
                            "short_payload",
                            "got %d of %d bytes"
                            % (len(payload), header["payload_len"]))
                    rgb = np.frombuffer(
                        payload, dtype=np.uint8).reshape(
                            header["height"], header["width"], 3)

                    try:
                        res = board_net.run_frame(
                            rgb, graph, plans, backend,
                            conf=args.conf, iou=args.iou)
                    except Exception as exc:  # noqa: BLE001
                        raise StreamError(
                            "frame_error", "frame %d: %r"
                            % (header["frame_id"], exc))

                    reply = _reply_obj(header["frame_id"], res)
                    _send_reply(conn, reply)

                    frames_done += 1
                    total_ms = float(res["timings"]["total_ms"])
                    total_ms_sum += total_ms
                    result["frames_done"] = int(frames_done)
                    result["mean_total_ms"] = float(
                        total_ms_sum / frames_done)
                    n_det = int(len(res["detections"]["names"]))
                    result["frames"].append(
                        _frame_record(header["frame_id"], res))
                    print("STREAM_FRAME_OK %d %d %.3f" % (
                        header["frame_id"], n_det, total_ms), flush=True)
            finally:
                try:
                    conn.close()
                except OSError:
                    pass
    finally:
        try:
            server.close()
        except OSError:
            pass

    mean_ms = (total_ms_sum / frames_done) if frames_done else 0.0
    result["frames_done"] = int(frames_done)
    result["mean_total_ms"] = float(mean_ms)
    return frames_done, mean_ms


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(argv=None):
    args = _parse_args(argv)
    result = _base_result(args)

    try:
        _overlay, plan, plans, backend = _setup(args, result)
        frames_done, mean_ms = _serve(args, plan, plans, backend, result)
    except StreamError as exc:
        result["status"] = "STREAM_BOARD_FAIL"
        result["reason"] = str(exc.token)
        result["detail"] = str(exc.detail)
        try:
            _write_json(args.out, result)
        except Exception as write_exc:  # noqa: BLE001
            result["detail"] = "%s; write_json failed: %r" % (
                result["detail"], write_exc)
        print("STREAM_BOARD_FAIL %s" % _bad_token(exc.token), flush=True)
        if (exc.token in ("buffer_alloc_error", "driver_init_error")
                and exc.detail):
            # Report the exact PYNQ/board exception; the JSON keeps it verbatim.
            print("STREAM_BOARD_DETAIL %s" % _ascii_line(exc.detail),
                  flush=True)
        return EXIT_FAIL
    except Exception as exc:  # noqa: BLE001 - never leak a traceback to stdout
        result["status"] = "STREAM_BOARD_FAIL"
        result["reason"] = "unexpected_error"
        result["detail"] = repr(exc)
        try:
            _write_json(args.out, result)
        except Exception:  # noqa: BLE001
            pass
        print("STREAM_BOARD_FAIL unexpected_error", flush=True)
        return EXIT_FAIL

    result["status"] = "STREAM_BOARD_OK"
    result["reason"] = ""
    result["detail"] = ""
    result["frames_done"] = int(frames_done)
    result["mean_total_ms"] = float(mean_ms)
    try:
        _write_json(args.out, result)
    except Exception as exc:  # noqa: BLE001
        print("STREAM_BOARD_FAIL write_json_error", flush=True)
        result["reason"] = "write_json_error"
        result["detail"] = repr(exc)
        return EXIT_FAIL

    print("STREAM_BOARD_OK %d %.3f" % (frames_done, mean_ms), flush=True)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
