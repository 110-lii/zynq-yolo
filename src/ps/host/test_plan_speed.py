#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_plan_speed.py -- 在板上跑任意 board plan（PL 卷积 + PS 非卷积），测单帧速度。

用法（板上，仓库根目录）：
    sudo -E python3 src/ps/host/test_plan_speed.py \
        --plan data/layers/pb160_plan.json --layers-dir data/layers \
        --bitfile board/overlay/conv_engine.bit \
        --clip data/samples/incidents/inc_01_t01154.9.mp4 --runs 3
判据：PLAN_SPEED_OK <ms> <fps> <dets> / PLAN_SPEED_FAIL <reason>
"""
import argparse
import os
import sys
import time

sys.dont_write_bytecode = True

import cv2
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(REPO, "src", "ps", "host"))
sys.path.insert(0, os.path.join(REPO, "tools"))

import board_net  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default=os.path.join(REPO, "data", "layers", "pb160_plan.json"))
    ap.add_argument("--layers-dir", default=os.path.join(REPO, "data", "layers"))
    ap.add_argument("--bitfile", default=os.path.join(REPO, "board", "overlay", "conv_engine.bit"))
    ap.add_argument("--clip", default=os.path.join(REPO, "data", "samples", "incidents",
                                                   "inc_01_t01154.9.mp4"))
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()

    try:
        plan = board_net.load_plan(args.plan)
        plans = [board_net.make_layer_plan(e, args.layers_dir) for e in plan.conv_entries()]
    except Exception as exc:  # noqa: BLE001
        print("PLAN_SPEED_FAIL plan %r" % (exc,))
        return 2

    in_shape = [int(v) for v in plan.graph.input_shape]
    h, w = in_shape[2], in_shape[3]
    cap = cv2.VideoCapture(args.clip)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        print("PLAN_SPEED_FAIL clip")
        return 2
    rgb = cv2.cvtColor(cv2.resize(frame, (w, h)), cv2.COLOR_BGR2RGB)

    sys.path.insert(0, os.path.join(REPO, "src", "ps", "pynq_driver"))
    from conv_engine import ConvEngineOverlay
    ovl = ConvEngineOverlay(args.bitfile)
    try:
        driver = board_net.BoardConvDriver(ovl, plan, args.layers_dir)
    except board_net.BoardBufferAllocError as exc:
        print("PLAN_SPEED_FAIL alloc %s" % exc)
        return 3
    backend = board_net.BoardBackend(driver)

    res = board_net.run_frame(rgb, plan.graph, plans, backend)
    ts = []
    for _ in range(args.runs):
        t = time.perf_counter()
        res = board_net.run_frame(rgb, plan.graph, plans, backend)
        ts.append((time.perf_counter() - t) * 1000.0)
    med = float(np.median(ts))
    det = res["detections"]
    print("PLAN_SPEED_OK %.1f %.2f %d" % (med, 1000.0 / med, len(det["scores"])))
    print("PLAN_SPEED_DETAIL conv_ms=%.1f other_ms=%.1f layers=%d dets=%s" % (
        res["timings"]["conv_ms"], res["timings"]["other_ms"], driver.layer_count,
        [(round(float(s), 3), int(c)) for s, c in zip(det["scores"][:8], det["cls"][:8])]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())