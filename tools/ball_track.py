"""ball_track.py —— 足球候选轨迹确认（纯 CV，无训练）

思路：单帧候选误检太多，但球是“连续、近似匀速”运动的。要求一个候选
在连续 3 次检测里出现、且相邻速度方向一致（cos>0.5），才确认成球。

用法：python tools/ball_track.py --video build/match_clip.mp4 --every 25
"""
import argparse

import numpy as np

from ball_cv_probe import candidates


class BallTrack:
    __slots__ = ("tid", "x", "y", "vx", "vy", "hits", "miss", "age")

    def __init__(self, tid, x, y):
        self.tid = tid
        self.x, self.y = x, y
        self.vx = self.vy = 0.0
        self.hits = 1
        self.miss = 0
        self.age = 1

    def predict(self):
        return self.x + self.vx, self.y + self.vy


class BallTracker:
    def __init__(self, gate=220.0, size_ratio=(0.4, 2.5), min_hits=2, max_miss=2, min_speed=15.0):
        self.gate = gate
        self.size_ratio = size_ratio
        self.min_hits = min_hits
        self.max_miss = max_miss
        self.min_speed = min_speed
        self.next_id = 1
        self.tracks = []

    def step(self, cands, dt=1.0):
        used = set()
        for t in self.tracks:
            px, py = t.predict()
            best = None
            for i, c in enumerate(cands):
                if i in used:
                    continue
                d = ((c["x"] - px) ** 2 + (c["y"] - py) ** 2) ** 0.5
                if d < self.gate and (best is None or d < best[0]):
                    best = (d, i)
            if best:
                i = best[1]
                c = cands[i]
                nvx = (c["x"] - t.x) / max(1.0, dt)
                nvy = (c["y"] - t.y) / max(1.0, dt)
                speed_prev = (t.vx ** 2 + t.vy ** 2) ** 0.5
                speed_new = (nvx ** 2 + nvy ** 2) ** 0.5
                ok_dir = True
                if speed_prev > 5 and speed_new > 5:
                    cos = (t.vx * nvx + t.vy * nvy) / (speed_prev * speed_new)
                    ok_dir = cos > 0.5
                if ok_dir:
                    t.vx, t.vy = 0.5 * t.vx + 0.5 * nvx, 0.5 * t.vy + 0.5 * nvy
                    t.x, t.y = c["x"], c["y"]
                    t.hits += 1
                    t.miss = 0
                    used.add(i)
                else:
                    t.miss += 1
                    t.x, t.y = t.predict()
                    t.vx *= 0.6
                    t.vy *= 0.6
            else:
                t.miss += 1
                t.x, t.y = t.predict()
                t.vx *= 0.6
                t.vy *= 0.6
        for i, c in enumerate(cands):
            if i not in used:
                self.tracks.append(BallTrack(self.next_id, c["x"], c["y"]))
            self.next_id += 1
        self.tracks = [t for t in self.tracks if t.miss <= self.max_miss]
        out = []
        for t in self.tracks:
            if t.hits < self.min_hits:
                continue
            speed = (t.vx ** 2 + t.vy ** 2) ** 0.5
            if speed < self.min_speed:   # 静止目标不是球（阈值随步长变）
                continue
            out.append(t)
        return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default=r"build/match_clip.mp4")
    ap.add_argument("--every", type=int, default=25)
    ap.add_argument("--max-frames", type=int, default=0)
    args = ap.parse_args()

    import cv2
    cap = cv2.VideoCapture(args.video)
    tr = BallTracker()
    prev = None
    out = []
    idx = 0
    while True:
        ok, f = cap.read()
        if not ok:
            break
        if args.max_frames and idx >= args.max_frames:
            break
        if idx % args.every == 0:
            diff = None
            if prev is not None:
                g1 = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
                g0 = cv2.cvtColor(prev, cv2.COLOR_BGR2GRAY)
                diff = cv2.GaussianBlur(cv2.absdiff(g1, g0), (3, 3), 0)
            cands = candidates(f, diff)
            confirmed = tr.step(cands, dt=1.0)
            if confirmed:
                out.append({"frame": idx, "balls": [
                    {"x": round(t.x, 1), "y": round(t.y, 1), "hits": t.hits,
                     "vx": round(t.vx, 1), "vy": round(t.vy, 1)} for t in confirmed]})
            prev = f
        idx += 1
    cap.release()
    print("CONFIRMED_FRAMES", len(out))
    for r in out[:8]:
        print(r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())