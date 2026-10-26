#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""板上验收：track_preproc 的 RGB/掩码逐字节对拍。

板上运行：
    python3 tools/check_track_preproc_board.py --repo /home/xilinx/zynq-yolo
判据：末行 TRACK_PREPROC_BOARD_OK；失败打印第一个不一致位置。
"""
import argparse
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True

import numpy as np


def reference(cur, prev):
    W, H = cur.shape[1], cur.shape[0]
    xstep = (W << 16) // 320
    ystep = (H << 16) // 320
    xmax, ymax = (W - 1) << 16, (H - 1) << 16
    rgb = np.zeros((3, 320, 320), dtype=np.uint8)
    mask = np.zeros((320, 320), dtype=np.uint8)
    for oy in range(320):
        ypos = (ystep >> 1) - 32768 + oy * ystep
        sy = 0 if ypos < 0 else (ymax if ypos > ymax else ypos) >> 16
        for ox in range(320):
            xpos = (xstep >> 1) - 32768 + ox * xstep
            sx = 0 if xpos < 0 else (xmax if xpos > xmax else xpos) >> 16
            b, g, r = map(int, cur[sy, sx])
            pg = int(prev[sy, sx, 1])
            rgb[0, oy, ox], rgb[1, oy, ox], rgb[2, oy, ox] = r, g, b
            mx, mn = max(b, g, r), min(b, g, r)
            m = 1 if (mx > 150 and mx - mn < 40) else 0
            if abs(g - pg) > 12:
                m |= 2
            mask[oy, ox] = m
    return rgb, mask


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="/home/xilinx/zynq-yolo")
    ap.add_argument("--bit", default="", help="组合 overlay，如 board/overlay/track_display.bit")
    args = ap.parse_args()
    sys.path.insert(0, str(Path(args.repo) / "tools"))
    from track_preproc import TrackPreproc

    rng = np.random.default_rng(0)
    cur = rng.integers(0, 255, (64, 64, 3), dtype=np.uint8)
    prev = cur.copy()
    prev[20:30, 20:30] = (30, 30, 30)
    cur[20:30, 20:30] = (250, 250, 250)
    prev[40:44, 40:44] = (10, 10, 10)
    cur[40:44, 40:44] = (200, 200, 200)

    tp = TrackPreproc(args.repo, bitfile=(args.bit or None))
    rgb, mask, ms = tp.run(cur, prev)
    exp_rgb, exp_mask = reference(cur, prev)
    ok_rgb = np.array_equal(rgb, exp_rgb)
    ok_mask = np.array_equal(mask, exp_mask)
    print("TRACK_BOARD_RGB", ok_rgb)
    print("TRACK_BOARD_MASK", ok_mask)
    print("TRACK_BOARD_MS %.3f" % ms)
    if not (ok_rgb and ok_mask):
        bad = np.argwhere(rgb != exp_rgb)
        if bad.size:
            print("FIRST_RGB_DIFF", bad[0].tolist(), int(rgb[tuple(bad[0])]), int(exp_rgb[tuple(bad[0])]))
        bad = np.argwhere(mask != exp_mask)
        if bad.size:
            print("FIRST_MASK_DIFF", bad[0].tolist(), int(mask[tuple(bad[0])]), int(exp_mask[tuple(bad[0])]))
        print("TRACK_PREPROC_BOARD_FAIL")
        return 1

    big = np.zeros((720, 1280, 3), dtype=np.uint8)
    big[..., 1] = 128
    tp.run(big, big)
    t0 = time.perf_counter()
    n = 3
    for _ in range(n):
        tp.run(big, big)
    dt = (time.perf_counter() - t0) * 1000.0 / n
    print("TRACK_BOARD_720P_MS %.2f" % dt)
    print("TRACK_PREPROC_BOARD_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())