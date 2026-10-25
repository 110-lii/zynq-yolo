"""mine_ball_hard_neg.py —— 难负样本自动挖掘

用当前分类器扫已知球所在帧，把 score>=0.8 且离真球 >48px 的窗口存成负样本，
追加到 data/ball_roi_full2/{crops,labels.json}（原 labels 先备份）。

用法：python tools/mine_ball_hard_neg.py --n 60 --stride 16 --th 0.8
"""
import argparse
import json
import os
import shutil

import cv2

from ball_cls_np import BallClassifier

SZ, CTX = 96, 64


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=r"data/ball_roi_full2")
    ap.add_argument("--video", default=r"D:\yt-dlp\downloads\migu_5106947698\migu_5106947698.mp4")
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--stride", type=int, default=16)
    ap.add_argument("--th", type=float, default=0.8)
    ap.add_argument("--tol", type=float, default=48.0)   # 原图像素
    args = ap.parse_args()

    root = args.dir
    clf = BallClassifier(os.path.join(root, "model", "ball_roi.npz"))
    lab = json.load(open(os.path.join(root, "labels.json"), encoding="utf-8"))
    shutil.copy(os.path.join(root, "labels.json"), os.path.join(root, "labels.before_hard.json"))
    man = json.load(open(os.path.join(root, "manifest.json"), encoding="utf-8"))
    by = {c["idx"]: c for c in man["crops"]}
    pos = [by[i] for i in lab["positives"]][: args.n]

    cap = cv2.VideoCapture(args.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    next_idx = max(by) + 1
    added = 0
    for p in pos:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(p["t_s"] * fps)))
        ok, f = cap.read()
        if not ok:
            continue
        small = cv2.resize(f, (640, 360))
        for y in range(0, 360 - 32, args.stride):
            for x in range(0, 640 - 32, args.stride):
                sx, sy = (x + 16) * 2, (y + 16) * 2          # 回到原图坐标
                if (sx - p["x"]) ** 2 + (sy - p["y"]) ** 2 <= args.tol ** 2:
                    continue
                w = small[y:y + 32, x:x + 32]
                if clf.prob(w) < args.th:
                    continue
                x0, y0 = max(0, sx - CTX // 2), max(0, sy - CTX // 2)
                patch = f[y0:y0 + CTX, x0:x0 + CTX]
                if patch.shape[0] < CTX or patch.shape[1] < CTX:
                    continue
                big = cv2.resize(patch, (SZ, SZ), interpolation=cv2.INTER_AREA)
                cv2.imwrite(os.path.join(root, "crops", "%04d.png" % next_idx), big)
                lab["negatives"].append(next_idx)
                next_idx += 1
                added += 1
    cap.release()
    json.dump(lab, open(os.path.join(root, "labels.json"), "w", encoding="utf-8"), indent=2)
    print("HARD_NEG_ADDED", added, "pos", len(lab["positives"]), "neg", len(lab["negatives"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())