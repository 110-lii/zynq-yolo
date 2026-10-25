"""ball_demo.py —— 在已知球附近生成带标注的视频，验证球跟踪

用法：python tools/ball_demo.py --t 915.7 --dur 8 --out build/viz/ball_demo.mp4
"""
import argparse
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
    ap.add_argument("--video", default=r"D:\yt-dlp\downloads\migu_5106947698\migu_5106947698.mp4")
    ap.add_argument("--t", type=float, required=True)
    ap.add_argument("--dur", type=float, default=8.0)
    ap.add_argument("--th", type=float, default=0.5)
    ap.add_argument("--out", default=r"build/viz/ball_demo.mp4")
    args = ap.parse_args()

    cap = cv2.VideoCapture(args.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    f0 = max(0, int((args.t - args.dur / 2) * fps))
    n = int(args.dur * fps)
    cap.set(cv2.CAP_PROP_POS_FRAMES, f0)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    vw = cv2.VideoWriter(args.out, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))

    tr = BallTracker(min_hits=2, max_miss=4, min_speed=3.0)
    trail = []
    cur_id = None
    hits = 0
    for i in range(n):
        ok, fr = cap.read()
        if not ok:
            break
        cs = candidates(fr, None)
        pat, sel = [], []
        for c in cs:
            x, y = int(c["x"]), int(c["y"])
            x0, y0 = max(0, x - 32), max(0, y - 32)
            patch = fr[y0:y0 + 64, x0:x0 + 64]
            if patch.shape[0] == 64 and patch.shape[1] == 64:
                pat.append(patch)
                sel.append(c)
        sc = scores(pat)
        keep = [c for c, s in zip(sel, sc) if s >= args.th]
        conf = tr.step(keep, dt=1.0)
        if conf:
            b = max(conf, key=lambda t: t.hits)     # 只跟最稳的那条
            if b.tid != cur_id:
                if trail:
                    lx, ly = trail[-1]
                    near = ((b.x - lx) ** 2 + (b.y - ly) ** 2) ** 0.5 < 80.0
                else:
                    near = False
                if not near:
                    trail = []                      # 只有接不上时才重置
                cur_id = b.tid
            trail.append((int(b.x), int(b.y)))
            if len(trail) > 30:
                trail.pop(0)
            hits += 1
            cv2.circle(fr, (int(b.x), int(b.y)), 12, (0, 255, 255), 2)
            cv2.putText(fr, "ball %.2f" % min(1.0, b.hits / 5.0), (int(b.x) + 14, int(b.y) - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        for j in range(1, len(trail)):
            cv2.line(fr, trail[j - 1], trail[j], (0, 128, 255), 1)
        cv2.putText(fr, "frame %d  conf_tracks %d" % (i, len(conf)), (10, 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
        vw.write(fr)
    cap.release()
    vw.release()
    print("DEMO_OK frames=%d ball_frames=%d out=%s" % (n, hits, args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())