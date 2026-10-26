#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""result_store.py —— 板端增量结果存储（SD 卡安全写）。

设计目标
--------
* **不丢帧**：每帧/每事件一行 JSONL，追加写；`fsync_every` 控制落盘频率；
* **可恢复**：运行中崩溃最多丢最后 `fsync_every` 行，`summary.json` 记录已写计数；
* **可轮转**：单文件超过 `max_mb` 自动切新文件，避免 FAT32 单文件/写放大问题；
* **可回放**：PC 端 `pc_dashboard.py` 直接读 JSONL。

板上典型用法::

    store = ResultStore("/mnt/sd/zynq-yolo", fsync_every=30)
    store.append({"type": "frame", "frame": 0, ...})
    store.close(summary={"frames": 202, "ball_frames": 144})

自检::

    python tools/result_store.py --selftest
    # 末行 RESULT_STORE_OK
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.dont_write_bytecode = True


class ResultStore:
    def __init__(self, root, prefix="run", fsync_every=30, max_mb=64):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.prefix = prefix
        self.fsync_every = max(1, int(fsync_every))
        self.max_bytes = max(1, int(max_mb)) * 1024 * 1024
        self._fh = None
        self._path = None
        self._bytes = 0
        self._since_sync = 0
        self._rot = 0
        self._opened_at = time.time()
        self.counts = {}
        self._open_new()

    def _open_new(self):
        if self._fh is not None:
            self._close_fh()
        stamp = time.strftime("%Y%m%d_%H%M%S")
        name = "%s_%s_%02d.jsonl" % (self.prefix, stamp, self._rot)
        self._rot += 1
        self._path = self.root / name
        self._fh = open(self._path, "a", encoding="utf-8", buffering=1)
        self._bytes = self._path.stat().st_size if self._path.exists() else 0
        self._since_sync = 0

    def _close_fh(self):
        try:
            self._fh.flush()
            os.fsync(self._fh.fileno())
        except Exception:
            pass
        try:
            self._fh.close()
        except Exception:
            pass
        self._fh = None

    def append(self, record):
        if not isinstance(record, dict):
            raise TypeError("record must be a dict")
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        data = (line + "\n").encode("utf-8")
        self._fh.write(data.decode("utf-8"))
        self._bytes += len(data)
        self._since_sync += 1
        typ = str(record.get("type", "record"))
        self.counts[typ] = self.counts.get(typ, 0) + 1
        if self._since_sync >= self.fsync_every:
            self.flush()
        if self._bytes >= self.max_bytes:
            self._open_new()

    def flush(self):
        if self._fh is None:
            return
        self._fh.flush()
        try:
            os.fsync(self._fh.fileno())
        except OSError:
            pass
        self._since_sync = 0

    def close(self, summary=None):
        self.flush()
        files = sorted(str(p.name) for p in self.root.glob(self.prefix + "_*.jsonl"))
        out = {
            "type": "summary",
            "created": self._opened_at,
            "closed": time.time(),
            "elapsed_s": round(time.time() - self._opened_at, 3),
            "files": files,
            "counts": dict(self.counts),
        }
        if summary:
            out.update(summary)
        tmp = self.root / "summary.json.tmp"
        tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.root / "summary.json")
        self._close_fh()
        return self.root / "summary.json"


def _selftest():
    tmp = Path(tempfile.mkdtemp(prefix="result_store_"))
    try:
        st = ResultStore(tmp, prefix="t", fsync_every=2, max_mb=1)
        for i in range(5):
            st.append({"type": "frame", "frame": i, "players": [], "ball": None})
        st.append({"type": "event", "event": "goal", "frame": 5})
        summary = st.close(summary={"frames": 5})
        data = json.loads(summary.read_text(encoding="utf-8"))
        lines = []
        for p in sorted(tmp.glob("t_*.jsonl")):
            lines.extend(p.read_text(encoding="utf-8").splitlines())
        ok = (data["counts"].get("frame") == 5
              and data["counts"].get("event") == 1
              and len(lines) == 6
              and all(json.loads(x) for x in lines))
        print("RESULT_STORE_SELFTEST", "OK" if ok else "FAIL",
              "files=%d lines=%d" % (len(data["files"]), len(lines)))
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        raise SystemExit(_selftest())
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
