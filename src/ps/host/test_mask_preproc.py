#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_mask_preproc.py -- 板端验证 PL 掩码预处理器（逐位 + 速度）。

判据：
    MASK_OVL_OK <mismatch> <ms> <fps>
    MASK_OVL_FAIL <reason>

用法（板上，仓库根目录）：
    sudo -E python3 src/ps/host/test_mask_preproc.py \
        --bitfile board/overlay/mask_preproc.bit \
        --clip data/samples/incidents/inc_01_t01154.9.mp4
"""
import argparse
import os
import sys

sys.dont_write_bytecode = True

import cv2
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(REPO, "src", "ps", "pynq_driver"))

from mask_preproc import MaskPreprocOverlay  # noqa: E402


def ref_mask(cur, prev, white_thr=150, sat_thr=40, diff_thr=12):
    b = cur[:, :, 0].astype(np.int16)
    g = cur[:, :, 1].astype(np.int16)
    r = cur[:, :, 2].astype(np.int16)
    mx = np.maximum(np.maximum(b, g), r)
    mn = np.minimum(np.minimum(b, g), r)
    pg = prev[:, :, 1].astype(np.int16)
    m = np.zeros(cur.shape[:2], np.uint8)
    m[(mx > white_thr) & ((mx - mn) < sat_thr)] |= 1
    m[np.abs(g - pg) > diff_thr] |= 2
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bitfile", default=os.path.join(REPO, "board", "overlay", "mask_preproc.bit"))
    ap.add_argument("--clip", default=os.path.join(REPO, "data", "samples", "incidents",
                                                   "inc_01_t01154.9.mp4"))
    ap.add_argument("--w", type=int, default=640)
    ap.add_argument("--h", type=int, default=360)
    ap.add_argument("--runs", type=int, default=30)
    args = ap.parse_args()

    cap = cv2.VideoCapture(args.clip)
    ok1, f1 = cap.read()
    ok2, f2 = cap.read()
    cap.release()
    if not (ok1 and ok2):
        print("MASK_OVL_FAIL clip_read")
        return 2
    cur = cv2.resize(f1, (args.w, args.h), interpolation=cv2.INTER_AREA)
    prev = cv2.resize(f2, (args.w, args.h), interpolation=cv2.INTER_AREA)

    ovl = MaskPreprocOverlay(args.bitfile)
    ovl.prepare(args.w, args.h)
    out, dt = ovl.run(cur, prev)
    ref = ref_mask(cur, prev)
    mism = int(np.count_nonzero(out != ref))

    ts = []
    for _ in range(args.runs):
        _, dt = ovl.run(cur, prev)
        ts.append(dt * 1000.0)
    med = float(np.median(ts))
    print("MASK_OVL_OK %d %.2f %.1f" % (mism, med, 1000.0 / med))
    print("MASK_OVL_DETAIL ip=%s shape=%s p50_ms=%.2f p90_ms=%.2f" %
          (ovl.name, out.shape, med, float(np.percentile(ts, 90))))
    return 0 if mism == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())