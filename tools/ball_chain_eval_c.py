"""ball_chain_eval_c.py —— 用 C 分类器评测完整足球链路的确认率

对每个标注正样本，在其时间点附近按 25fps 逐帧跑：
    白块候选 -> C 分类器(>=th) -> 轨迹确认
统计确认轨迹与标注点的距离。

用法：python tools/ball_chain_eval_c.py --n 20 --win 8 --th 0.6
"""
import argparse
import json
import os
import subprocess
import tempfile

import cv2
import numpy as np

from ball_cv_probe import candidates
from ball_track import BallTracker

CLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "build", "ball_cls_c.exe")


def scores(patches):
    if not patches:
        return []
    arr = np.stack([cv2.cvtColor(cv2.resize(p, (32, 32), interpolation=cv2.INTER_AREA),
                                 cv2.COLOR_BGR2RGB) for p in patches]).astype(np.uint8)
    fd, raw = tempfile.mkstemp(suffix=".raw")
    os.close(fd)
    try:
        arr.tofile(raw)
        r = subprocess.run([CLS, raw, str(len(patches))], capture_output=True, text=True)
    finally:
        try:
            os.unlink(raw)
        except OSError:
            pass
    return [float(l.split()[2]) for l in r.stdout.splitlines() if l.startswith("SCORE")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=r"data/ball_roi_full2")
    ap.add_argument("--video", default=r"D:\yt-dlp\downloads\migu_5106947698\migu_5106947698.mp4")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--win", type=int, default=8, help="前后帧数")
    ap.add_argument("--th", type=float, default=0.6)
    args = ap.parse_args()

    lab = json.load(open(os.path.join(args.dir, "labels.json"), encoding="utf-8"))
    man = json.load(open(os.path.join(args.dir, "manifest.json"), encoding="utf-8"))
    by = {c["idx"]: c for c in man["crops"]}
    pos = [by[i] for i in lab["positives"]][: args.n]

    cap = cv2.VideoCapture(args.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    hit = 0
    for p in pos:
        f0 = int(round(p["t_s"] * fps))
        tr = BallTracker(min_hits=2, max_miss=3, min_speed=3.0)
        best = None
        for f in range(max(1, f0 - args.win), f0 + args.win + 1):
            cap.set(cv2.CAP_PROP_POS_FRAMES, f - 1)
            ok0, a = cap.read()
            ok1, b = cap.read()
            if not (ok0 and ok1):
                continue
            diff = cv2.GaussianBlur(cv2.absdiff(cv2.cvtColor(b, cv2.COLOR_BGR2GRAY),
                                                cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)), (3, 3), 0)
            cs = candidates(b, diff)
            pat, sel = [], []
            for j, c in enumerate(cs):
                x, y = int(c["x"]), int(c["y"])
                x0, y0 = max(0, x - 32), max(0, y - 32)
                patch = b[y0:y0 + 64, x0:x0 + 64]
                if patch.shape[0] == 64 and patch.shape[1] == 64:
                    pat.append(patch)
                    sel.append(c)
            sc = scores(pat)
            keep = [c for c, s in zip(sel, sc) if s >= args.th]
            for t in tr.step(keep, dt=1.0):
                d = ((t.x - p["x"]) ** 2 + (t.y - p["y"]) ** 2) ** 0.5
                if best is None or d < best:
                    best = d
        ok = best is not None and best <= 15.0
        hit += 1 if ok else 0
        print("idx=%d best=%s %s" % (p["idx"], "none" if best is None else round(best, 1),
                                     "HIT" if ok else ""))
    cap.release()
    print("CHAIN_C_HIT %d/%d = %.1f%%" % (hit, len(pos), 100.0 * hit / max(1, len(pos))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())