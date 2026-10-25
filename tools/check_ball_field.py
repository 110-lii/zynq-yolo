import json
import os
import subprocess
import sys
import tempfile

import cv2
import numpy as np

sys.path.insert(0, "tools")
from ball_cv_probe import candidates
from ball_track import BallTracker
from events import step as ev_step
from field_coords import box_to_field, inside_hull

CLS = r"build\ball_cls_c.exe"
CAL = json.load(open(r"data\calib\field_calib_ball.json"))
H = CAL["H"]
hull = json.load(open(r"data/calib/pairs_ball.json", encoding="utf-8"))["image"]


def scores(patches):
    arr = np.stack([cv2.cvtColor(cv2.resize(p, (32, 32), interpolation=cv2.INTER_AREA),
                                 cv2.COLOR_BGR2RGB) for p in patches]).astype(np.uint8)
    fd, raw = tempfile.mkstemp(suffix=".raw")
    os.close(fd)
    try:
        arr.tofile(raw)
        r = subprocess.run([CLS, raw, str(len(patches))], capture_output=True, text=True)
    finally:
        os.unlink(raw)
    return [float(l.split()[2]) for l in r.stdout.splitlines() if l.startswith("SCORE")]


cap = cv2.VideoCapture(r"build\ball_clip.mp4")
tr = BallTracker(min_hits=2, max_miss=4, min_speed=3.0)
n = in_field = 0
ev_count = 0
samples = []
while True:
    ok, f = cap.read()
    if not ok:
        break
    n += 1
    cs = candidates(f, None)
    pat, sel = [], []
    for c in cs:
        x, y = int(c["x"]), int(c["y"])
        p = f[max(0, y - 32):y + 32, max(0, x - 32):x + 32]
        if p.shape[0] == 64 and p.shape[1] == 64:
            pat.append(p)
            sel.append(c)
    keep = [c for c, sc in zip(sel, scores(pat)) if sc >= 0.5] if pat else []
    conf = tr.step(keep, dt=1.0)
    if conf:
        b = max(conf, key=lambda t: t.hits)
        if True:
            xy = box_to_field(H, [b.x - 1, b.y - 1, b.x + 1, b.y + 1])
            if xy and -5 <= xy[0] <= 110 and -5 <= xy[1] <= 73:
                in_field += 1
                e = ev_step(xy, [])
                if e["out_of_play"] or e["goal"]:
                    ev_count += 1
                if len(samples) < 8:
                    samples.append((n, round(xy[0], 1), round(xy[1], 1)))
cap.release()
print("FRAMES", n, "BALL_IN_FIELD", in_field, "EVENTS", ev_count)
print("SAMPLES", samples)