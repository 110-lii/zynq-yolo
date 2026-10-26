#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""result_stream.py —— 板端把 ResultStore 的 JSONL 逐行推给 PC Dashboard。

特性：断线重连、按文件尾随（--follow）、可重放历史（--from-start）、限速。
协议：TCP + 每行一个 UTF-8 JSON（与 `pc_dashboard.py --listen` 对应）。

板上：
    python3 tools/result_stream.py --file /mnt/sd/zynq-yolo/run_*.jsonl \
        --host 192.168.2.100 --port 5005 --follow

PC：
    python tools/pc_dashboard.py --listen 0.0.0.0:5005
"""
import argparse
import socket
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True


def _connect(host, port, timeout=5.0):
    s = socket.create_connection((host, int(port)), timeout=timeout)
    s.settimeout(5.0)
    return s


def _send_lines(path, host, port, follow, from_start, rate, max_lines=0):
    path = Path(path)
    sent = 0
    sock = None
    f = None
    try:
        while True:
            if sock is None:
                try:
                    sock = _connect(host, port)
                    print("RESULT_STREAM_CONNECTED %s:%s" % (host, port))
                except OSError as exc:
                    print("RESULT_STREAM_WAIT %s" % exc)
                    time.sleep(1.0)
                    continue
            if f is None:
                while not path.exists():
                    print("RESULT_STREAM_WAIT_FILE %s" % path)
                    time.sleep(0.5)
                f = open(path, "r", encoding="utf-8", errors="replace")
                if not from_start:
                    f.seek(0, 2)
            line = f.readline()
            if not line:
                if not follow:
                    break
                time.sleep(0.2)
                continue
            if not line.strip():
                continue
            try:
                sock.sendall(line.encode("utf-8"))
                sent += 1
            except OSError as exc:
                print("RESULT_STREAM_RESET %s" % exc)
                try:
                    sock.close()
                except OSError:
                    pass
                sock = None
            if rate > 0:
                time.sleep(rate)
            if max_lines and sent >= max_lines:
                break
    finally:
        if f is not None:
            f.close()
        if sock is not None:
            sock.close()
    print("RESULT_STREAM_OK sent=%d" % sent)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--host", required=True)
    ap.add_argument("--port", type=int, default=5005)
    ap.add_argument("--follow", action="store_true")
    ap.add_argument("--from-start", action="store_true")
    ap.add_argument("--rate", type=float, default=0.0, help="每行间隔秒（0=全速）")
    ap.add_argument("--max-lines", type=int, default=0)
    args = ap.parse_args()
    return _send_lines(args.file, args.host, args.port, args.follow,
                       args.from_start, args.rate, args.max_lines)


if __name__ == "__main__":
    raise SystemExit(main())
