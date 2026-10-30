#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""contest_fill.py —— 把争抢 8 段的「门限后目标数」补齐（板子一活就接着跑）。

为什么要单独一个循环：2026-10-30 板子每 5–15 分钟断一次，
一份 8 段 × 85 s 的活一次跑不完；而 `scene_suite_board --resume` 已经能续，
缺的只是一层「掉了就等、等了再续、直到补齐」的外壳。
"""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import run_board_acceptance as r  # noqa: E402

OUT = ROOT / "build" / "_contest_rows.json"
LOG = ROOT / "build" / "contest_fill.log"
PY = sys.executable
CLIPS = ",".join("c%02d" % i for i in range(1, 9))


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def done_rows():
    try:
        return [x for x in json.loads(OUT.read_text(encoding="utf-8"))["rows"] if "rowsok" in x]
    except (OSError, ValueError):
        return []


def main():
    for attempt in range(12):
        got = done_rows()
        if len(got) >= 8:
            log("已补齐 8/8，收工")
            return 0
        if not r.board_alive(timeout=20):
            log("板子离线（已有 %d/8），20 秒后再看" % len(got))
            time.sleep(20)
            continue
        log("板子在线（已有 %d/8），续跑" % len(got))
        p = subprocess.run([PY, str(ROOT / "tools" / "scene_suite_board.py"),
                            "--clips", CLIPS, "--resume", "--no-push",
                            "--max-frames", "250", "--every", "10",
                            "--out", str(OUT)],
                           cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, encoding="utf-8", errors="replace", timeout=3600)
        tail = [ln for ln in (p.stdout or "").splitlines() if ln.startswith("SCENE_BOARD_")]
        log("  " + (tail[-1] if tail else "无输出"))
        time.sleep(10)
    log("试了 12 轮仍未补齐：%d/8" % len(done_rows()))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
