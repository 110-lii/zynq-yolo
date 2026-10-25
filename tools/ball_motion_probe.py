"""ball_motion_probe.py —— 运动法候选 + 在标注集上评测召回率

思路：球在相邻两帧间位移明显，而场地线/广告牌是静止的。
    1) 相邻帧灰度差 -> 阈值 -> 连通域
    2) 面积 8~400、长宽比 0.5~2、绿场附近、局部对比度高
    3) 与“白块法”在同一批标注上比召回（容差 12 px）

用法：
    python tools/ball_motion_probe.py --eval
"""
import argparse
import json
import os

import cv2
import numpy as np

from ball_cv_probe import candidates as white_cands, grass_mask

TOL = 12.0


def motion_cands(f_prev, f_cur, min_area=8, max_area=400):
    g0 = cv2.cvtColor(f_prev, cv2.COLOR_BGR2GRAY)
    g1 = cv2.cvtColor(f_cur, cv2.COLOR_BGR2GRAY)
    d = cv2.absdiff(g1, g0)
    d = cv2.GaussianBlur(d, (3, 3), 0)
    _, m = cv2.threshold(d, 22, 255, cv2.THRESH_BINARY)
    grass = grass_mask(f_cur)
    m = cv2.bitwise_and(m, grass)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, lab, st, cen = cv2.connectedComponentsWithStats(m, 8)
    out = []
    h, w = g1.shape
    for i in range(1, n):
        a = st[i, cv2.CC_STAT_AREA]
        if a < min_area or a > max_area:
            continue
        bw, bh = st[i, cv2.CC_STAT_WIDTH], st[i, cv2.CC_STAT_HEIGHT]
        if bh == 0 or bw / bh < 0.4 or bw / bh > 2.5:
            continue
        cx, cy = cen[i]
        # 局部对比度：球通常比周围草亮
        x0, y0 = int(max(0, cx - 8)), int(max(0, cy - 8))
        patch = g1[y0:y0 + 16, x0:x0 + 16]
        if patch.size == 0 or float(patch.max()) - float(patch.mean()) < 25:
            continue
        out.append({"x": float(cx), "y": float(cy), "area": int(a), "src": "motion"})
    return out


def _hit(cands, x, y, tol=TOL):
    return any((c["x"] - x) ** 2 + (c["y"] - y) ** 2 <= tol * tol for c in cands)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=r"data/ball_roi_full2")
    ap.add_argument("--video", default=r"D:\yt-dlp\downloads\migu_5106947698\migu_5106947698.mp4")
    ap.add_argument("--eval", action="store_true")
    args = ap.parse_args()

    if not args.eval:
        ap.print_help()
        return 0

    lab = json.load(open(os.path.join(args.dir, "labels.json"), encoding="utf-8"))
    man = json.load(open(os.path.join(args.dir, "manifest.json"), encoding="utf-8"))
    by = {c["idx"]: c for c in man["crops"]}
    pos = [by[i] for i in lab["positives"]]
    print("positives", len(pos))

    cap = cv2.VideoCapture(args.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    hit_m = hit_w = 0
    miss = []
    for p in pos:
        f = int(round(p["t_s"] * fps))
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(1, f - 1))
        ok0, prev = cap.read()
        ok1, cur = cap.read()
        if not (ok0 and ok1):
            continue
        cm = motion_cands(prev, cur)
        cw = white_cands(cur, None)
        if _hit(cm, p["x"], p["y"]):
            hit_m += 1
        else:
            miss.append(p["idx"])
        if _hit(cw, p["x"], p["y"]):
            hit_w += 1
    cap.release()
    print("RECALL motion %d/%d = %.1f%%" % (hit_m, len(pos), 100.0 * hit_m / len(pos)))
    print("RECALL white  %d/%d = %.1f%%" % (hit_w, len(pos), 100.0 * hit_w / len(pos)))
    print("MISS_IDX", miss[:30])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())