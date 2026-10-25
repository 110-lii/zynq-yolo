"""make_ball_rois.py -- 生成"球/非球"候选 ROI 供人工标注。

为什么需要
----------
`tools/fast_events.py` 的白块候选会把白袜、白线、广告牌一起抓进来；要靠一个
极小判别器把它们分开，而判别器需要标签。这里把候选裁成统一尺度（8x 候选尺寸，
最小 32、最大 96，再缩到 64x64）并放大保存，标注者只需要回答"正中间那个是不是球"。

产出（都不入库，标注结果才入库）：
    build/ball_rois/<id>.png        256x256（64x64 的 4x 最近邻放大）
    build/ball_rois.csv             id,img,source,frame,cx,cy,w,h,score,grass,label
    data/annot/ball_rois_labels.csv 由 tools/label_ball.py 写（只有 id,label,note）

用法：
    python tools/make_ball_rois.py --n 200
"""
import argparse
import csv
import random
from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[1]
OUT_IMG = REPO / "build" / "ball_rois"
OUT_CSV = REPO / "build" / "ball_rois.csv"
NATIVE = REPO / ".research" / "native"
CLIPS = REPO / "data" / "samples" / "incidents"


def candidates(bgr):
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, 145, 255, cv2.THRESH_BINARY)
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cnts:
        a = float(cv2.contourArea(c))
        if not (6.0 <= a <= 900.0):
            continue
        x, y, w, h = [int(v) for v in cv2.boundingRect(c)]
        if not (2 <= w <= 40 and 2 <= h <= 40):
            continue
        asp = w / float(h)
        if not (0.4 <= asp <= 2.5):
            continue
        per = cv2.arcLength(c, True)
        circ = float(4 * np.pi * a / (per * per)) if per > 0 else 0.0
        cx, cy = x + w / 2.0, y + h / 2.0
        r = max(w, h) / 2.0
        r1 = int(r * 3.2) + 3
        y0, y1 = max(0, int(cy) - r1), min(bgr.shape[0], int(cy) + r1 + 1)
        x0, x1 = max(0, int(cx) - r1), min(bgr.shape[1], int(cx) + r1 + 1)
        grass = 0.0
        if y1 - y0 >= 5 and x1 - x0 >= 5:
            hsv = cv2.cvtColor(bgr[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
            grass = float(((hsv[:, :, 0] > 30) & (hsv[:, :, 0] < 95) &
                           (hsv[:, :, 1] > 60) & (hsv[:, :, 2] > 40)).mean())
        score = circ + min(a / 400.0, 1.0) + grass
        out.append(dict(cx=cx, cy=cy, w=w, h=h, area=a, circ=circ, grass=grass, score=score))
    out.sort(key=lambda d: -d["score"])
    return out


def sources(all_clips=False):
    frames = []
    for p in sorted(NATIVE.glob("match_*.jpg")):
        frames.append(("native", p.name, cv2.imread(str(p))))
    clips = sorted(CLIPS.glob("inc_*.mp4"))
    stamps = (0, 25, 50, 75, 100, 125) if all_clips else (0, 40, 90)
    for p in (clips if all_clips else clips[:3]):
        cap = cv2.VideoCapture(str(p))
        for idx in stamps:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, im = cap.read()
            if ok:
                frames.append(("clip", "%s#%d" % (p.name, idx), im))
        cap.release()
    return [f for f in frames if f[2] is not None]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--out-dir", default=str(OUT_IMG))
    ap.add_argument("--out-csv", default=str(OUT_CSV))
    ap.add_argument("--rule-only", action="store_true",
                    help="只采草地>=0.6 & score>=1.8 & 最大边<=30 的候选")
    ap.add_argument("--all-clips", action="store_true")
    args = ap.parse_args(argv)

    out_img = Path(args.out_dir)
    out_csv = Path(args.out_csv)
    out_img.mkdir(parents=True, exist_ok=True)
    for old in out_img.glob("*.png"):
        old.unlink()

    pool = []
    for kind, name, img in sources(all_clips=args.all_clips):
        for c in candidates(img):
            if args.rule_only and not (c["grass"] >= 0.6 and c["score"] >= 1.8
                                       and max(c["w"], c["h"]) <= 30):
                continue
            pool.append(dict(source=kind, frame=name, img=img, **c))
    if not pool:
        print("BALL_ROIS_FAIL no_candidates")
        return 2

    # 分层采样：高分 45% / 随机 35% / 低分 20%
    pool.sort(key=lambda d: -d["score"])
    n_hi = int(args.n * 0.45)
    n_lo = int(args.n * 0.20)
    n_rand = args.n - n_hi - n_lo
    hi = pool[:max(1, min(len(pool) // 3, n_hi))]
    lo = pool[-n_lo:]
    rest = pool[len(hi):len(pool) - len(lo)]
    random.Random(20261021).shuffle(rest)
    mid = rest[:n_rand]
    chosen = hi + mid + lo
    random.Random(7).shuffle(chosen)

    rows = []
    for i, c in enumerate(chosen):
        img = c["img"]
        hh, ww = img.shape[:2]
        side = int(max(24, min(96, 6 * max(c["w"], c["h"]))))
        x0 = int(np.clip(c["cx"] - side / 2, 0, max(0, ww - side)))
        y0 = int(np.clip(c["cy"] - side / 2, 0, max(0, hh - side)))
        crop = img[y0:y0 + side, x0:x0 + side]
        if crop.shape[0] < 8 or crop.shape[1] < 8:
            continue
        small = cv2.resize(crop, (64, 64), interpolation=cv2.INTER_AREA)
        big = cv2.resize(small, (256, 256), interpolation=cv2.INTER_NEAREST)
        fn = "r%04d.png" % (len(rows) + 1)
        cv2.imwrite(str(out_img / fn), big)
        rows.append(dict(id=len(rows) + 1, img=fn, source=c["source"], frame=c["frame"],
                         cx=round(c["cx"], 1), cy=round(c["cy"], 1), w=c["w"], h=c["h"],
                         score=round(c["score"], 3), grass=round(c["grass"], 3), label=""))

    with open(out_csv, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print("BALL_ROIS_OK %d %s" % (len(rows), out_csv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())