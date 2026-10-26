#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_track_preproc.py —— 在 PC 上对拍 track_preproc 的算法（不跑 HLS）。

用 g++ + Vitis_HLS 的 ap_int.h 编译 `src/hls/track/tb/tb_track_preproc.cpp`，
生成 64×64 的合成 cur/prev，逐字节比较 RGB 平面与白/运动掩码。

依赖：g++（MinGW 即可）、Vitis_HLS 2020.2 的头文件目录（默认 D:\\Xilinx\\...）。
判据：末行 TRACK_PREPROC_REF OK。

用法：
    python tools/check_track_preproc.py
"""
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "hls" / "track"
DEFAULT_INC = Path(r"D:\Xilinx\Vitis_HLS\2020.2\include")


def build_and_run(inc_dir, work):
    exe = work / "tb_track_preproc.exe"
    cmd = ["g++", "-O2", "-std=c++11", "-I", str(inc_dir), "-I", str(SRC / "src"),
           str(SRC / "tb" / "tb_track_preproc.cpp"), "-o", str(exe)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        print("COMPILE_FAIL")
        print(p.stderr[-1000:])
        return None
    return exe


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
    ap.add_argument("--include", default=str(DEFAULT_INC))
    args = ap.parse_args()
    inc = Path(args.include)
    if not (inc / "ap_int.h").exists():
        print("TRACK_PREPROC_REF FAIL: 找不到 ap_int.h:", inc)
        return 1

    work = Path(tempfile.mkdtemp(prefix="track_ref_"))
    rng = np.random.default_rng(0)
    cur = rng.integers(0, 255, (64, 64, 3), dtype=np.uint8)
    prev = cur.copy()
    prev[20:30, 20:30] = (30, 30, 30)
    cur[20:30, 20:30] = (250, 250, 250)
    prev[40:44, 40:44] = (10, 10, 10)
    cur[40:44, 40:44] = (200, 200, 200)
    (work / "cur.raw").write_bytes(cur.tobytes())
    (work / "prev.raw").write_bytes(prev.tobytes())

    exe = build_and_run(inc, work)
    if exe is None:
        return 1
    p = subprocess.run([str(exe), str(work / "cur.raw"), str(work / "prev.raw"),
                        str(work / "rgb.raw"), str(work / "mask.raw"), "64", "64"],
                       capture_output=True, text=True)
    if "TRACK_TB_OK" not in p.stdout:
        print("TRACK_PREPROC_REF FAIL: TB 未通过")
        print(p.stdout[-500:])
        return 1
    rgb = np.frombuffer((work / "rgb.raw").read_bytes(), dtype=np.uint8).reshape(3, 320, 320)
    mask = np.frombuffer((work / "mask.raw").read_bytes(), dtype=np.uint8).reshape(320, 320)
    exp_rgb, exp_mask = reference(cur, prev)
    ok = np.array_equal(rgb, exp_rgb) and np.array_equal(mask, exp_mask)
    print("RGB_MATCH", bool(np.array_equal(rgb, exp_rgb)))
    print("MASK_MATCH", bool(np.array_equal(mask, exp_mask)))
    print("TRACK_PREPROC_REF", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
