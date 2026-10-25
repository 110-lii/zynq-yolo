"""ball_cv_probe.py —— 足球候选（传统 CV）：绿场上的白色小圆块

不是最终检测器，只用来判断“候选生成”这一步是否可行；精度需要真值才能评。
用法：python tools/ball_cv_probe.py --video build/match_clip.mp4 --every 25
"""
import argparse
import json

import cv2
import numpy as np


def grass_mask(bgr):
    """草场掩膜：在 1/4 分辨率上算连通域再放大，比全分辨率快约 10 倍。"""
    h, w = bgr.shape[:2]
    sm = cv2.resize(bgr, (320, 180), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(sm, cv2.COLOR_BGR2HSV)
    hh, ss, vv = cv2.split(hsv)
    g = ((hh >= 30) & (hh <= 90) & (ss >= 50) & (vv >= 35)).astype(np.uint8) * 255
    g = cv2.morphologyEx(g, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(g, 8)
    if n <= 1:
        return np.zeros((h, w), np.uint8)
    k = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
    m = (lab == k).astype(np.uint8) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    m = cv2.dilate(m, np.ones((3, 3), np.uint8))
    return cv2.resize(m, (w, h), interpolation=cv2.INTER_NEAREST)


def _grass_mask_full(bgr):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)
    g = ((h >= 30) & (h <= 90) & (s >= 50) & (v >= 35)).astype(np.uint8) * 255
    g = cv2.morphologyEx(g, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(g, 8)
    if n <= 1:
        return np.zeros_like(g)
    k = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
    m = (lab == k).astype(np.uint8) * 255
    return cv2.dilate(m, np.ones((5, 5), np.uint8))


def white_mask(bgr):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    s, v = hsv[..., 1], hsv[..., 2]
    return ((s < 60) & (v > 150)).astype(np.uint8) * 255


def motion_score(bgr, prev):
    if prev is None:
        return None
    d = cv2.absdiff(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY),
                    cv2.cvtColor(prev, cv2.COLOR_BGR2GRAY))
    return cv2.GaussianBlur(d, (3, 3), 0)


def _in_corner(x, y, w, h):
    """转播画面四角常放比分牌/台标，直接排除。"""
    cw, ch = 0.25 * w, 0.22 * h
    return ((x < cw and y < ch) or (x > w - cw and y < ch) or
            (x < cw and y > h - ch) or (x > w - cw and y > h - ch))


def candidates(bgr, diff=None, reject_corners=True, min_area=12, max_area=1500, roi=None):
    ox = oy = 0
    if roi is not None:
        rx0, ry0, rx1, ry1 = [int(v) for v in roi]
        rx0 = max(0, rx0); ry0 = max(0, ry0)
        rx1 = min(bgr.shape[1], rx1); ry1 = min(bgr.shape[0], ry1)
        if rx1 - rx0 < 64 or ry1 - ry0 < 64:
            return []
        bgr = bgr[ry0:ry1, rx0:rx1]
        ox, oy = rx0, ry0
    grass = grass_mask(bgr)
    # 只在草场包围盒内算白掩膜：1280x720 全幅 HSV 是主要开销
    x0, y0, bw, bh = cv2.boundingRect(grass)
    if bw <= 0 or bh <= 0:
        return []
    sub = bgr[y0:y0 + bh, x0:x0 + bw]
    wsub = white_mask(sub)
    white = np.zeros_like(grass)
    white[y0:y0 + bh, x0:x0 + bw] = wsub
    white = cv2.bitwise_and(white, grass)
    cnts, _ = cv2.findContours(white, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cnts:
        a = cv2.contourArea(c)
        if a < min_area or a > max_area:
            continue
        x, y, w, h = cv2.boundingRect(c)
        if h == 0 or w == 0:
            continue
        ar = w / h
        if not (0.5 <= ar <= 2.0):
            continue
        per = cv2.arcLength(c, True)
        circ = 4 * np.pi * a / (per * per) if per > 0 else 0
        if circ < 0.45:
            continue
        if diff is not None:
            patch = diff[max(0, y - 2):y + h + 2, max(0, x - 2):x + w + 2]
            if patch.size == 0 or float(patch.mean()) < 6.0:
                continue   # 帧差太小：静态白块（线/广告牌/鞋）
        cx, cy = x + w / 2 + ox, y + h / 2 + oy
        if reject_corners and roi is None and _in_corner(cx, cy, bgr.shape[1], bgr.shape[0]):
            continue
        out.append({"x": cx, "y": cy, "w": w, "h": h,
                    "area": round(a, 1), "circ": round(circ, 2)})
    out.sort(key=lambda d: -d["area"])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default=r"build/match_clip.mp4")
    ap.add_argument("--every", type=int, default=25)
    ap.add_argument("--max-frames", type=int, default=0)
    args = ap.parse_args()

    cap = cv2.VideoCapture(args.video)
    idx = 0
    rows = []
    prev = None
    prev_small = None
    while True:
        ok, f = cap.read()
        if not ok:
            break
        if args.max_frames and idx >= args.max_frames:
            break
        if idx % args.every == 0:
            diff = None
            if prev_small is not None:
                diff = motion_score(f, prev_small)
            c = candidates(f, diff)
            prev_small = f
            rows.append({"frame": idx, "n": len(c), "cands": c[:5]})
        idx += 1
    cap.release()
    n = [r["n"] for r in rows]
    print(json.dumps({"frames_checked": len(rows), "cand_total": sum(n),
                      "cand_mean": round(float(np.mean(n)), 1) if n else 0,
                      "sample": rows[:5]}, ensure_ascii=False)[:800])


if __name__ == "__main__":
    main()