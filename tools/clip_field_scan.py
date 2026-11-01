"""clip_field_scan.py —— 一批片段里，哪些真的能算出「球场坐标球」，有没有越线

用途：回答「出界事件能不能在这批素材上成立」。球链路与板上同一套
（`ball_cv_probe` 候选 + `ball_cls_c.exe` + `BallTracker` + 单应 + `events.step`），
只跑球、不跑 YOLO，所以一段 750 帧的片段约 30 s，适合先全库过一遍。

判据读法：
  · `in_field` 接近 0 ⇒ 这段素材跟这份标定**不配对**（不是"没有球"）；
  · `events` 里只有孤立单帧 ⇒ 先怀疑假球：看 `nearest`（离边线距离）与帧号是否连续。

用法
    py tools/clip_field_scan.py build/ball_clip.mp4 "data/samples/incidents/inc_*.mp4"
    py tools/clip_field_scan.py --calib data/calib/field_calib.json data/samples/scenes/c0*.mp4
"""
import glob
import json
import os
import subprocess
import sys
import tempfile

# 保证从仓库根目录 import tools 下的模块
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))

import cv2
import numpy as np

from ball_cv_probe import candidates
from ball_track import BallTracker
from events import step as ev_step
from field_coords import box_to_field

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLS = os.path.join(REPO, "build", "ball_cls_c.exe")
CAL = os.path.join(REPO, "data", "calib", "field_calib_ball.json")
H = json.load(open(CAL, encoding="utf-8"))["H"]


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


def scan(path):
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return {"path": path, "err": "open"}
    tr = BallTracker(min_hits=2, max_miss=4, min_speed=3.0)
    n = in_field = 0
    evs = []
    best = None          # 离边线最近的一次（米, 帧, x, y）
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
        if not conf:
            continue
        b = max(conf, key=lambda t: t.hits)
        xy = box_to_field(H, [b.x - 1, b.y - 1, b.x + 1, b.y + 1])
        if not xy or not (-5 <= xy[0] <= 110 and -5 <= xy[1] <= 73):
            continue
        in_field += 1
        e = ev_step(xy, [])
        if e["out_of_play"] or e["goal"]:
            evs.append((n, round(xy[0], 1), round(xy[1], 1), e["out_side"] or e["goal_side"]))
        d = min(abs(xy[0]), abs(xy[0] - 105), abs(xy[1]), abs(xy[1] - 68))
        if best is None or d < best[0]:
            best = (round(d, 2), n, round(xy[0], 1), round(xy[1], 1))
    cap.release()
    return {"path": os.path.relpath(path, REPO), "frames": n, "in_field": in_field,
            "events": evs, "nearest_line": best}


def main(argv):
    global H
    if argv and argv[0] == "--calib":
        H = json.load(open(argv[1], encoding="utf-8"))["H"]
        argv = argv[2:]
    paths = []
    for a in argv:
        paths.extend(sorted(glob.glob(a)))
    for p in paths:
        r = scan(p)
        if r.get("err"):
            print("SCAN_FAIL %s %s" % (r["path"], r["err"]), flush=True)
            continue
        print("SCAN %-40s frames=%3d in_field=%3d events=%d nearest=%s"
              % (r["path"], r["frames"], r["in_field"], len(r["events"]),
                 r["nearest_line"]), flush=True)
        for e in r["events"][:6]:
            print("     EVT frame=%d field=(%s,%s) side=%s" % e, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
