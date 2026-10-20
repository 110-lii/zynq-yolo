#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""stream_sender.py -- PC side of the board streaming bring-up test.

Sends already-resized RGB888 frames to the board over TCP, receives the
detection JSON (and optionally the raw output tensor), and compares it with
the reference produced by tools/make_board_stream_pack.py.

Board protocol (little endian):
    magic b"ZYOL" | version u16 | frame_id u32 | width u16 | height u16 |
    channels u8 | reserved u8 | payload_len u32 | payload RGB888 HWC

Reply:
    u32 json_len | UTF-8 JSON

Final token:
    STREAM_OK <frames> <mean_ms> <cosine>
    STREAM_FAIL <reason>
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import base64
import json
import socket
import struct
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
DEFAULT_IMAGE = REPO / "data" / "samples" / "zidane.jpg"
DEFAULT_REF = REPO / "build" / "stream" / "reference.json"
DEFAULT_OUT = REPO / "data" / "results" / "stream_client.json"
MAGIC = b"ZYOL"
VERSION = 1
INPUT_SIZE = 320
HEADER = struct.Struct("<4sHIHHBBI")


def _fail(reason: str) -> int:
    print("STREAM_FAIL %s" % reason)
    return 2


def _load_rgb(path: Path, size: int) -> np.ndarray:
    import cv2

    img = cv2.imread(str(path))
    if img is None:
        raise RuntimeError("cannot read image: %s" % path)
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    img = cv2.resize(img, (size, size), interpolation=cv2.INTER_LINEAR)
    return np.ascontiguousarray(img, dtype=np.uint8)


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    chunks = []
    got = 0
    while got < n:
        chunk = sock.recv(n - got)
        if not chunk:
            raise RuntimeError("connection closed after %d/%d bytes" % (got, n))
        chunks.append(chunk)
        got += len(chunk)
    return b"".join(chunks)


def _send_frame(sock: socket.socket, frame_id: int, rgb: np.ndarray) -> dict:
    h, w, c = rgb.shape
    payload = rgb.tobytes(order="C")
    header = HEADER.pack(MAGIC, VERSION, frame_id, w, h, c, 0, len(payload))
    sock.sendall(header)
    sock.sendall(payload)
    raw_len = _recv_exact(sock, 4)
    (json_len,) = struct.unpack("<I", raw_len)
    if json_len <= 0 or json_len > 8 * 1024 * 1024:
        raise RuntimeError("bad reply length %d" % json_len)
    raw = _recv_exact(sock, json_len)
    return json.loads(raw.decode("utf-8"))


def _compare(reply: dict, ref: dict, ref_bin: Path | None) -> dict:
    out = {"detections": reply.get("detections", {})}
    if ref_bin and ref_bin.is_file() and reply.get("output"):
        shape = tuple(reply["output"]["shape"])
        got = np.frombuffer(
            base64.b64decode(reply["output"]["data_b64"]), dtype="<f4"
        )
        want = np.fromfile(str(ref_bin), dtype="<f4")
        if got.size != want.size:
            out["error"] = "output size %d != reference %d" % (got.size, want.size)
            return out
        got = got.astype(np.float64)
        want = want.astype(np.float64)
        diff = np.abs(got - want)
        denom = (np.linalg.norm(got) * np.linalg.norm(want)) + 1e-12
        out.update(
            {
                "output_shape": list(shape),
                "max_abs_err": float(diff.max()) if diff.size else 0.0,
                "mean_abs_err": float(diff.mean()) if diff.size else 0.0,
                "cosine": float(np.dot(got, want) / denom),
            }
        )
    if ref.get("detections") is not None:
        a = ref["detections"]
        b = reply.get("detections", {})
        out["n_ref"] = len(a.get("scores", []))
        out["n_got"] = len(b.get("scores", []))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Send frames to the Zynq board and collect results.")
    ap.add_argument("--host", default="192.168.2.99")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--image", default=str(DEFAULT_IMAGE))
    ap.add_argument("--imgsz", type=int, default=INPUT_SIZE)
    ap.add_argument("--frames", type=int, default=1)
    ap.add_argument("--fps", type=float, default=1.0)
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--reference", default=str(DEFAULT_REF))
    ap.add_argument("--reference-bin", default=str(REPO / "build" / "stream" / "reference_output.bin"))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--keep-output", action="store_true",
                    help="keep the raw output0 base64 blob in the saved JSON")
    args = ap.parse_args(argv)

    image = Path(args.image)
    if not image.is_absolute():
        image = REPO / image
    if not image.is_file():
        return _fail("image_missing_%s" % image)

    ref = {}
    ref_path = Path(args.reference)
    if ref_path.is_file():
        with open(ref_path, "r", encoding="utf-8") as fh:
            ref = json.load(fh)
    ref_bin = Path(args.reference_bin)

    try:
        rgb = _load_rgb(image, args.imgsz)
    except Exception as exc:  # noqa: BLE001
        return _fail("image_load_%s" % exc)

    frames = []
    t0 = time.perf_counter()
    try:
        with socket.create_connection((args.host, args.port), timeout=args.timeout) as sock:
            sock.settimeout(args.timeout)
            for i in range(max(1, args.frames)):
                res = _send_frame(sock, i, rgb)
                frames.append(res)
                det = res.get("detections", {})
                n = len(det.get("scores", []))
                tm = res.get("timings_ms", {}).get("total", -1.0)
                print(
                    "FRAME_OK %d det=%d total_ms=%.1f" % (i, n, float(tm)),
                    flush=True,
                )
                if args.fps > 0 and i + 1 < args.frames:
                    time.sleep(1.0 / args.fps)
    except Exception as exc:  # noqa: BLE001
        return _fail("socket_%s" % exc)

    elapsed = time.perf_counter() - t0
    mean_ms = 1000.0 * elapsed / max(1, len(frames))
    cmp = _compare(frames[-1], ref, ref_bin) if frames else {}
    last = frames[-1] if frames else None
    if last is not None and not args.keep_output:
        last = dict(last)
        last["output"] = dict(last.get("output") or {})
        last["output"].pop("data_b64", None)
    report = {
        "tool": "tools/stream_sender.py",
        "image": str(image),
        "host": args.host,
        "port": args.port,
        "frames": len(frames),
        "wall_s": elapsed,
        "mean_ms": mean_ms,
        "reply": last,
        "compare": cmp,
    }
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = REPO / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(report, fh, indent=2, sort_keys=True, ensure_ascii=True)
        fh.write("\n")

    cosine = cmp.get("cosine")
    if cosine is None:
        print("STREAM_OK %d %.1f n/a" % (len(frames), mean_ms))
    else:
        if cosine < 0.999:
            print("STREAM_FAIL cosine_%.6f" % cosine)
            return 2
        print("STREAM_OK %d %.1f %.6f" % (len(frames), mean_ms, cosine))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
