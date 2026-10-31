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


def candidates(bgr, diff=None, reject_corners=True, min_area=12, max_area=1500, roi=None,
               grass_fallback=False, grass_min_frac=0.08,
               air_search=False, air_min_area=25, air_max_area=420, air_max_dim=28):
    """白球候选。

    grass_fallback：草场包围盒占画面不足 grass_min_frac 时，改为**全幅**找白块。
    这一条是为「球出界」场景加的：球越过边线后落在广告牌/看台背景上，
    原逻辑（只在草场包围盒内找）会直接返回空表 —— 而那正是最需要看见球的时刻。
    默认关闭，保持板上主线的行为不变。

    air_search：再扫一遍**草场掩膜之外**的小白块（默认关闭）。
    ★ 2026-10-31 查「球从来没找到」的直接原因：不是球看不见，是它飞在了草场外面。
      c03 第 140–158 帧肉眼可见一个 10×10 px 的白块从 (769,182) 平滑下落到
      (857,235) —— 正落在广告牌上方的深色背景里。`white & grass` 把它整块抹掉，
      所以候选表一直是 0。空中球用更紧的形状门限（小、接近圆、不细长），
      静态的广告字/看台细节交给帧差（diff）和跟踪器的 min_hits 挡。
    """
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
    hh, ww = bgr.shape[:2]
    full_frame = False
    if bw <= 0 or bh <= 0:
        if not grass_fallback and not air_search:
            return []
        full_frame = True
    elif bw * bh < grass_min_frac * ww * hh:
        full_frame = grass_fallback
    if full_frame:
        white = white_mask(bgr)
        x0, y0, bw, bh = 0, 0, ww, hh
    else:
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

    if air_search:
        if full_frame:
            wair = white
        else:
            # 草场外的小白块：形状门限比场内更紧（球在空中时孤立、圆、直径 6–16 px）
            wair = cv2.bitwise_and(white_mask(bgr),
                                   cv2.bitwise_not(cv2.dilate(grass, np.ones((5, 5), np.uint8))))
        acnts, _ = cv2.findContours(wair, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in acnts:
            a = cv2.contourArea(c)
            if a < air_min_area or a > air_max_area:
                continue
            x, y, w, h = cv2.boundingRect(c)
            if w == 0 or h == 0 or w > air_max_dim or h > air_max_dim:
                continue
            ar = w / float(h)
            if not (0.6 <= ar <= 1.7):
                continue
            per = cv2.arcLength(c, True)
            circ = 4 * np.pi * a / (per * per) if per > 0 else 0
            if circ < 0.55:
                continue
            if diff is not None:
                patch = diff[max(0, y - 2):y + h + 2, max(0, x - 2):x + w + 2]
                if patch.size == 0 or float(patch.mean()) < 4.0:
                    continue   # 静止的白点：广告字/看台/鞋
            cx, cy = x + w / 2 + ox, y + h / 2 + oy
            if reject_corners and roi is None and _in_corner(cx, cy, bgr.shape[1], bgr.shape[0]):
                continue
            out.append({"x": cx, "y": cy, "w": w, "h": h,
                        "area": round(a, 1), "circ": round(circ, 2), "src": "air"})

    out.sort(key=lambda d: (-d["area"]))
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