"""从整场视频均匀抽样生成足球候选 ROI（只抽、不顺序解码，速度快）"""
import json
import os

import cv2
import numpy as np

from ball_cv_probe import candidates

VIDEO = r"D:\yt-dlp\downloads\migu_5106947698\migu_5106947698.mp4"
OUT = r"D:\zynq-yolo\data\ball_roi_full2"
N = 800
CTX, SZ, GRID = 64, 96, 8

os.makedirs(os.path.join(OUT, "crops"), exist_ok=True)
cap = cv2.VideoCapture(VIDEO)
dur = cap.get(cv2.CAP_PROP_FRAME_COUNT) / (cap.get(cv2.CAP_PROP_FPS) or 25.0)
print("duration_s", round(dur, 1))

rows, crops = [], []
for k in range(N):
    t = dur * k / N
    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
    ok, f = cap.read()
    if not ok:
        continue
    for c in candidates(f, None):
        x, y = int(c["x"]), int(c["y"])
        x0, y0 = max(0, x - CTX // 2), max(0, y - CTX // 2)
        patch = f[y0:y0 + CTX, x0:x0 + CTX]
        if patch.shape[0] < CTX or patch.shape[1] < CTX:
            continue
        big = cv2.resize(patch, (SZ, SZ), interpolation=cv2.INTER_NEAREST)
        n = len(rows)
        cv2.imwrite(os.path.join(OUT, "crops", "%04d.png" % n), big)
        rows.append({"idx": n, "frame": int(t * 25), "t_s": round(t, 1),
                     "x": x, "y": y, "w": c["w"], "h": c["h"],
                     "area": c["area"], "circ": c["circ"]})
        crops.append(big)
cap.release()

for k in range(0, len(crops), GRID * GRID):
    chunk = crops[k:k + GRID * GRID]
    sheet = np.zeros((GRID * SZ, GRID * SZ, 3), np.uint8)
    for i, im in enumerate(chunk):
        r, c = divmod(i, GRID)
        tile = im.copy()
        cv2.putText(tile, str(k + i), (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)
        sheet[r * SZ:(r + 1) * SZ, c * SZ:(c + 1) * SZ] = tile
    cv2.imwrite(os.path.join(OUT, "sheet_%02d.jpg" % (k // (GRID * GRID))), sheet)

json.dump({"video": VIDEO, "samples": N, "crops": rows},
          open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8"), ensure_ascii=False)
print("FULL_ROI_OK crops=%d sheets=%d" % (len(rows), (len(crops) + 63) // 64))