"""ball_slide_eval.py —— 单帧滑窗检测可行性实验

在 640x360 降采样画面上，以 stride=16、窗口 32x32 跑分类器，
取最高分位置与标注球位置比较；统计命中率与每帧耗时。

用法：python tools/ball_slide_eval.py --n 5
"""
import argparse
import json
import os
import time

import cv2

from ball_cls_np import BallClassifier

TOL = 24.0     # 降采样后的容差（约等于原图 48 px）


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=r"data/ball_roi_full2")
    ap.add_argument("--video", default=r"D:\yt-dlp\downloads\migu_5106947698\migu_5106947698.mp4")
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--stride", type=int, default=16)
    args = ap.parse_args()

    root = args.dir
    clf = BallClassifier(os.path.join(root, "model", "ball_roi.npz"))
    lab = json.load(open(os.path.join(root, "labels.json"), encoding="utf-8"))
    man = json.load(open(os.path.join(root, "manifest.json"), encoding="utf-8"))
    by = {c["idx"]: c for c in man["crops"]}
    pos = [by[i] for i in lab["positives"]][: args.n]

    cap = cv2.VideoCapture(args.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    hit = 0
    for p in pos:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(p["t_s"] * fps)))
        ok, f = cap.read()
        if not ok:
            continue
        small = cv2.resize(f, (640, 360))
        t0 = time.perf_counter()
        best = (-1.0, 0, 0)
        for y in range(0, 360 - 32, args.stride):
            for x in range(0, 640 - 32, args.stride):
                w = small[y:y + 32, x:x + 32]
                s = clf.prob(w)
                if s > best[0]:
                    best = (s, x + 16, y + 16)
        dt = time.perf_counter() - t0
        # 标注点换算到 640x360
        bx, by_ = p["x"] * 0.5, p["y"] * 0.5
        d = ((best[1] - bx) ** 2 + (best[2] - by_) ** 2) ** 0.5
        ok_hit = d <= TOL
        hit += 1 if ok_hit else 0
        print("idx=%d score=%.2f at(%d,%d) ball(%.0f,%.0f) d=%.0f %s  %.1fs"
              % (p["idx"], best[0], best[1], best[2], bx, by_, d,
                 "HIT" if ok_hit else "MISS", dt))
    cap.release()
    print("SLIDE_HIT %d/%d" % (hit, len(pos)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())