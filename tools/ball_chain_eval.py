"""ball_chain_eval.py —— 在已知球位置附近跑完整候选链，看能否确认

对每个标注正样本：
    取 t±0.4 s 的相邻帧，跑 候选 -> 分类器(>=0.5) -> 轨迹确认(3 次)
    若确认轨迹在 t 时刻落在标注点 15 px 内，记为命中
用法：python tools/ball_chain_eval.py --n 30
"""
import argparse
import json
import os

import cv2

from ball_cls_np import BallClassifier
from ball_cv_probe import candidates
from ball_track import BallTracker

TOL = 15.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=r"data/ball_roi_full2")
    ap.add_argument("--video", default=r"D:\yt-dlp\downloads\migu_5106947698\migu_5106947698.mp4")
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--th", type=float, default=0.5)
    ap.add_argument("--hits", type=int, default=3)
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
    details = []
    for p in pos:
        f0 = int(round(p["t_s"] * fps))
        tr = BallTracker(min_hits=args.hits)
        prev = None
        best = None
        for f in range(max(1, f0 - 10), f0 + 11, 1):
            cap.set(cv2.CAP_PROP_POS_FRAMES, f - 1)
            ok0, a = cap.read()
            ok1, b = cap.read()
            if not (ok0 and ok1):
                continue
            diff = cv2.GaussianBlur(cv2.absdiff(cv2.cvtColor(b, cv2.COLOR_BGR2GRAY),
                                                cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)), (3, 3), 0)
            cs = candidates(b, diff)
            keep = []
            for c in cs:
                x, y = int(c["x"]), int(c["y"])
                x0, y0 = max(0, x - 32), max(0, y - 32)
                patch = b[y0:y0 + 64, x0:x0 + 64]
                if patch.shape[0] != 64 or patch.shape[1] != 64:
                    continue
                if clf.prob(patch) >= args.th:
                    keep.append(c)
            conf = tr.step(keep, dt=1.0)
            for t in conf:
                d = ((t.x - p["x"]) ** 2 + (t.y - p["y"]) ** 2) ** 0.5
                if best is None or d < best:
                    best = d
        if best is not None and best <= TOL:
            hit += 1
        details.append((p["idx"], None if best is None else round(best, 1)))
    cap.release()
    print("CHAIN_HIT %d/%d = %.1f%%" % (hit, len(pos), 100.0 * hit / max(1, len(pos))))
    print("DETAIL", details[:20])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())