"""make_ball_rois_v2.py —— 从视频生成足球候选 ROI 标注集

输出：
    <out>/crops/<idx>.png    候选周围 64x64 的上下文（放大到 96x96）
    <out>/manifest.json      每条候选的来源与坐标
    <out>/sheet_<k>.jpg      缩略图拼版（带编号，便于人工挑出真球）

用法：
    python tools/make_ball_rois_v2.py --video build/match_clip.mp4 --every 10 --out data/ball_roi
"""
import argparse
import json
import os

import cv2
import numpy as np

from ball_cv_probe import candidates

CTX = 64          # 上下文边长（原图像素）
OUT = 96          # 输出边长
GRID = 8          # 每张拼版 8x8


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default=r"build/match_clip.mp4")
    ap.add_argument("--every", type=int, default=10)
    ap.add_argument("--out", default=r"data/ball_roi")
    ap.add_argument("--max-crops", type=int, default=400)
    args = ap.parse_args()

    os.makedirs(os.path.join(args.out, "crops"), exist_ok=True)
    cap = cv2.VideoCapture(args.video)
    prev = None
    idx = 0
    rows = []
    crops = []

    while True:
        ok, f = cap.read()
        if not ok:
            break
        if idx % args.every == 0:
            diff = None
            if prev is not None:
                g1 = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
                g0 = cv2.cvtColor(prev, cv2.COLOR_BGR2GRAY)
                diff = cv2.GaussianBlur(cv2.absdiff(g1, g0), (3, 3), 0)
            for c in candidates(f, diff):
                x, y = int(c["x"]), int(c["y"])
                x0, y0 = max(0, x - CTX // 2), max(0, y - CTX // 2)
                patch = f[y0:y0 + CTX, x0:x0 + CTX]
                if patch.shape[0] < CTX or patch.shape[1] < CTX:
                    continue
                big = cv2.resize(patch, (OUT, OUT), interpolation=cv2.INTER_NEAREST)
                n = len(rows)
                cv2.imwrite(os.path.join(args.out, "crops", "%04d.png" % n), big)
                rows.append({"idx": n, "frame": idx, "x": x, "y": y,
                             "w": c["w"], "h": c["h"], "area": c["area"],
                             "circ": c["circ"]})
                crops.append(big)
                if len(rows) >= args.max_crops:
                    break
            prev = f
        if len(rows) >= args.max_crops:
            break
        idx += 1
    cap.release()

    # 拼版
    for k in range(0, len(crops), GRID * GRID):
        chunk = crops[k:k + GRID * GRID]
        sheet = np.zeros((GRID * OUT, GRID * OUT, 3), np.uint8)
        for i, im in enumerate(chunk):
            r, c = divmod(i, GRID)
            tile = im.copy()
            cv2.putText(tile, str(k + i), (4, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
            sheet[r * OUT:(r + 1) * OUT, c * OUT:(c + 1) * OUT] = tile
        cv2.imwrite(os.path.join(args.out, "sheet_%02d.jpg" % (k // (GRID * GRID))), sheet)

    with open(os.path.join(args.out, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"video": args.video, "every": args.every, "crops": rows}, fh, ensure_ascii=False)
    print("ROI_OK crops=%d sheets=%d out=%s" % (len(rows), (len(crops) + GRID * GRID - 1) // (GRID * GRID), args.out))


if __name__ == "__main__":
    main()