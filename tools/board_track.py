"""board_track.py —— 轻量 SORT 跟踪（检测 1 FPS + 中间帧外推）

设计目标：板上 CPU 只用很小一部分，检测框进来就更新，没检测的帧用常速模型外推。
不依赖 numpy 以外的库。

用法（库）：
    tr = MultiTracker(iou_th=0.3, max_missed=5)
    for each frame:
        dets = [...]                      # [[x1,y1,x2,y2,score], ...]
        tracks = tr.step(dets)            # [[id,x1,y1,x2,y2,score], ...]
        if 该帧没有检测: tracks = tr.predict()   # 仅外推

自检：
    python3 tools/board_track.py --selftest
"""
import argparse
import math


def _iou(a, b):
    ix1 = max(a[0], b[0])
    iy1 = max(a[1], b[1])
    ix2 = min(a[2], b[2])
    iy2 = min(a[3], b[3])
    iw = max(0.0, ix2 - ix1)
    ih = max(0.0, iy2 - iy1)
    inter = iw * ih
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


class Track:
    __slots__ = ("tid", "box", "vx", "vy", "score", "missed", "hits")

    def __init__(self, tid, box, score):
        self.tid = tid
        self.box = list(box)
        self.vx = 0.0
        self.vy = 0.0
        self.score = score
        self.missed = 0
        self.hits = 1

    def predict(self, dt=1.0, dx=0.0, dy=0.0):
        cx = (self.box[0] + self.box[2]) * 0.5 + self.vx * dt + dx
        cy = (self.box[1] + self.box[3]) * 0.5 + self.vy * dt + dy
        w = self.box[2] - self.box[0]
        h = self.box[3] - self.box[1]
        self.box = [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]
        return self.box

    def update(self, box, score, dx=0.0, dy=0.0, dt=1.0):
        ocx = (self.box[0] + self.box[2]) * 0.5
        ocy = (self.box[1] + self.box[3]) * 0.5
        ncx = (box[0] + box[2]) * 0.5
        ncy = (box[1] + box[3]) * 0.5
        _dt = max(1.0, float(dt))
        self.vx = 0.5 * self.vx + 0.5 * (ncx - ocx - dx) / _dt
        self.vy = 0.5 * self.vy + 0.5 * (ncy - ocy - dy) / _dt
        self.box = list(box)
        self.score = score
        self.missed = 0
        self.hits += 1


class MultiTracker:
    def __init__(self, iou_th=0.3, max_missed=5, min_hits=2, max_dist=40.0):
        self.iou_th = iou_th
        self.max_missed = max_missed
        self.min_hits = min_hits
        self.max_dist = max_dist
        self.tracks = []
        self.next_id = 1

    def predict(self, dx=0.0, dy=0.0):
        for t in self.tracks:
            t.predict(1.0, dx, dy)
            t.missed += 1
        self.tracks = [t for t in self.tracks if t.missed <= self.max_missed]
        return self._out()

    def step(self, dets, dx=0.0, dy=0.0, dt=1.0):
        # 贪婪 IoU 匹配（人数不多，O(n*m) 足够）
        pairs = []
        for ti, t in enumerate(self.tracks):
            for di, d in enumerate(dets):
                v = _iou(t.box, d[:4])
                if v >= self.iou_th:
                    pairs.append((v, ti, di))
        pairs.sort(reverse=True)
        used_t, used_d = set(), set()
        for v, ti, di in pairs:
            if ti in used_t or di in used_d:
                continue
            self.tracks[ti].update(dets[di][:4], dets[di][4], dx, dy, dt)
            used_t.add(ti)
            used_d.add(di)
        # 第二遍：IoU 没匹配上的，用“预测中心距离”兜底（相机已做运动补偿）
        for ti, t in enumerate(self.tracks):
            if ti in used_t:
                continue
            bh = max(8.0, t.box[3] - t.box[1])
            tcx = (t.box[0] + t.box[2]) * 0.5
            tcy = (t.box[1] + t.box[3]) * 0.5
            best = None
            for di, d in enumerate(dets):
                if di in used_d:
                    continue
                dcx = (d[0] + d[2]) * 0.5
                dcy = (d[1] + d[3]) * 0.5
                dist = ((dcx - tcx) ** 2 + (dcy - tcy) ** 2) ** 0.5
                dw = d[2] - d[0]
                dh = d[3] - d[1]
                ratio = (dw * dh) / max(1.0, (t.box[2] - t.box[0]) * (t.box[3] - t.box[1]))
                if dist < self.max_dist and 0.6 <= ratio <= 1.6 and (best is None or dist < best[0]):
                    best = (dist, di)
            if best is not None:
                di = best[1]
                t.update(dets[di][:4], dets[di][4], dx, dy, dt)
                used_t.add(ti)
                used_d.add(di)
        for di, d in enumerate(dets):
            if di in used_d:
                continue
            self.tracks.append(Track(self.next_id, d[:4], d[4]))
            self.next_id += 1
        for ti, t in enumerate(self.tracks):
            if ti not in used_t:
                t.predict(1.0, dx, dy)
                t.missed += 1
        self.tracks = [t for t in self.tracks if t.missed <= self.max_missed]
        return self._out()

    def _out(self):
        keep = [t for t in self.tracks if t.hits >= self.min_hits]
        if not keep:
            keep = self.tracks          # 冷启动阶段先全给，避免前几帧空
        return [[t.tid, t.box[0], t.box[1], t.box[2], t.box[3], t.score] for t in keep]


def _selftest():
    tr = MultiTracker(iou_th=0.3, max_missed=3)
    ok = True

    a = tr.step([[100, 100, 140, 180, 0.9]])
    ok &= len(a) == 1 and a[0][0] == 1

    b = tr.step([[104, 100, 144, 180, 0.9]])       # 平移 4px，应保持 ID
    ok &= len(b) == 1 and b[0][0] == 1

    c = tr.predict()                                # 无检测帧，外推
    ok &= len(c) == 1 and c[0][0] == 1 and c[0][1] > b[0][1] - 1

    for _ in range(6):
        d = tr.predict()
    ok &= len(d) == 0                               # 超过 max_missed 应删除

    e = tr.step([[500, 100, 540, 180, 0.8], [100, 100, 140, 180, 0.7]])
    ok &= len(e) == 2 and sorted(x[0] for x in e) == [2, 3]

    print("TRACK_SELFTEST", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        raise SystemExit(_selftest())
    ap.print_help()