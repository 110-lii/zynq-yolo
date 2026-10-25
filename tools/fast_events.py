"""fast_events.py -- 速度优先的球员/足球事件原型（纯 PS，OpenCV + numpy）。

设计依据（板端实测，Zynq-7020 / 2xA9 / OpenCV 4.2）：
    帧差+形态学+连通域 320x180   9.6 ms   (104 FPS)
    MOG2 320x180                57  ms   (17.5 FPS)
    全局补偿 160x90             20  ms
    白球候选 10x(128x128) ROI    4.1 ms
⇒ 整帧预算 ~35 ms（含补偿），速度优先路线放在 PS；PL 留给后续的流式像素管线。

输出：标注视频 + JSON（每帧球员框、球候选、倒地/出界标志、耗时）。
判据令牌：FAST_EVENTS_OK <frames> <fps> / FAST_EVENTS_FAIL <reason>

用法（板上）：
    python3 tools/fast_events.py --clip data/samples/incidents/inc_01_t01154.9.mp4 --out /tmp/ev.mp4
"""
import argparse
import json
from pathlib import Path
import sys
import time

import cv2
import numpy as np

GATE = None
PGATE = None


def load_gate(path):
    global GATE
    try:
        z = np.load(path)
        GATE = dict(w=z["w"], b=float(z["b"]), mu=z["mu"], sd=z["sd"],
                    thr=float(z["threshold"]))
    except Exception:
        GATE = None


MASK_OVL = None


def _gate_features(crop64):
    g = cv2.cvtColor(crop64, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(g, (16, 16), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    h, w = crop64.shape[:2]
    c = crop64[h // 3:2 * h // 3, w // 3:2 * w // 3]
    ch = cv2.cvtColor(c, cv2.COLOR_BGR2HSV)
    sat = float(ch[:, :, 1].mean()) / 255.0
    vmax = float(ch[:, :, 2].max()) / 255.0
    gm = cv2.cvtColor(c, cv2.COLOR_BGR2GRAY)
    _, m = cv2.threshold(gm, 150, 255, cv2.THRESH_BINARY)
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    fill = 0.0
    size = 0.0
    if cnts:
        cc = max(cnts, key=cv2.contourArea)
        a = float(cv2.contourArea(cc))
        x, y, ww, hh = cv2.boundingRect(cc)
        fill = a / float(max(1, ww * hh))
        size = max(ww, hh) / 64.0
    return np.concatenate([[sat, vmax, fill, size], small.ravel()]).astype(np.float32)


def _crop64(bgr, cx, cy, side):
    h, w = bgr.shape[:2]
    side = int(max(16, min(96, side)))
    x0 = int(np.clip(cx - side / 2, 0, max(0, w - side)))
    y0 = int(np.clip(cy - side / 2, 0, max(0, h - side)))
    c = bgr[y0:y0 + side, x0:x0 + side]
    if c.shape[0] < 8 or c.shape[1] < 8:
        return None
    return cv2.resize(c, (64, 64), interpolation=cv2.INTER_AREA)


def load_person_gate(path):
    global PGATE
    try:
        z = np.load(path)
        PGATE = dict(w=z["w"], b=float(z["b"]), mu=z["mu"], sd=z["sd"], thr=float(z["threshold"]))
    except Exception:
        PGATE = None


def _person_features(crop):
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, (24, 48), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hist = []
    for ch in range(3):
        h = cv2.calcHist([hsv], [ch], None, [16], [0, 256]).ravel()
        hist.append(h / max(1.0, h.sum()))
    return np.concatenate([g.ravel(), np.concatenate(hist)]).astype(np.float32)


def _person_window(bgr, cx, cy, h):
    H = max(24.0, 1.3 * h)
    W = H / 2.0
    x0 = int(np.clip(cx - W / 2, 0, max(0, bgr.shape[1] - W)))
    y0 = int(np.clip(cy - H / 2, 0, max(0, bgr.shape[0] - H)))
    c = bgr[y0:y0 + int(H), x0:x0 + int(W)]
    if c.shape[0] < 8 or c.shape[1] < 4:
        return None
    return cv2.resize(c, (32, 64), interpolation=cv2.INTER_AREA)


def person_prob(crop):
    f = _person_features(crop)
    z = (f - PGATE["mu"]) / PGATE["sd"]
    return float(1.0 / (1.0 + np.exp(-(z @ PGATE["w"] + PGATE["b"]))))


def gate_prob(crop64):
    f = _gate_features(crop64)
    z = (f - GATE["mu"]) / GATE["sd"]
    return float(1.0 / (1.0 + np.exp(-(z @ GATE["w"] + GATE["b"]))))


def white_candidates(bgr, wmask=None):
    """找白块，再只在小窗口上查饱和度和草地环绕（避免整块 HSV，板端 1.8 ms/ROI）。

    板端实测（128x128）：BGR2HSV 1.79 ms + inRange 0.78 + morph 0.82 = 3.4 ms；
    灰度阈值 0.14 ms + findContours 0.61 ms ⇒ 快 5 倍。
    `wmask` 非空时直接用 PL 掩码的 bit0，省掉阈值。
    """
    if wmask is not None:
        mask = ((wmask & 1) * 255).astype(np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    else:
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        _, mask = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    # ROI 里白块可能很多，逐候选查 HSV 很贵；按面积取前 12 个
    cnts = sorted(cnts, key=cv2.contourArea, reverse=True)[:12]
    grass_roi = None
    if wmask is not None:
        # PL 掩码已经是「高亮 + 低饱和」，不必逐候选再查饱和度；
        # 草地环绕也只需在整块 ROI 上算一次（逐候选查 HSV 是 v4 板端 36 ms 的来源）。
        hsv_roi = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        grass_roi = float(((hsv_roi[:, :, 0] > 30) & (hsv_roi[:, :, 0] < 95) &
                           (hsv_roi[:, :, 1] > 60) & (hsv_roi[:, :, 2] > 40)).mean())
        if grass_roi < 0.5:
            return None
    best = None
    for c in cnts:
        a = float(cv2.contourArea(c))
        if not (8.0 <= a <= 400.0):
            continue
        x, y, w, h = [int(v) for v in cv2.boundingRect(c)]
        if not (3 <= w <= 26 and 3 <= h <= 26):
            continue
        asp = w / float(h)
        if not (0.55 <= asp <= 1.8):
            continue
        per = cv2.arcLength(c, True)
        if per <= 0:
            continue
        circ = float(4 * np.pi * a / (per * per))
        if circ < 0.45:
            continue
        if max(w, h) > 30:
            continue
        if wmask is not None:
            gr = grass_roi
        else:
            # 低饱和：只查这个小框
            patch = bgr[max(0, y - 1):y + h + 1, max(0, x - 1):x + w + 1]
            hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
            if float(hsv[:, :, 1].mean()) > 80:
                continue
            # 草地环绕：半径 1.6~3 倍内绿色占比
            cx, cy = x + w / 2.0, y + h / 2.0
            r = max(w, h) / 2.0
            r1 = int(r * 3.2) + 3
            y0, y1 = max(0, int(cy) - r1), min(bgr.shape[0], int(cy) + r1 + 1)
            x0, x1 = max(0, int(cx) - r1), min(bgr.shape[1], int(cx) + r1 + 1)
            if y1 - y0 < 5 or x1 - x0 < 5:
                continue
            ring = cv2.cvtColor(bgr[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
            gr = ((ring[:, :, 0] > 30) & (ring[:, :, 0] < 95) &
                  (ring[:, :, 1] > 60) & (ring[:, :, 2] > 40)).mean()
            if gr < 0.60:
                continue
        score = circ + a / 400.0 + float(gr)
        if best is None or score > best["score"]:
            best = dict(x=x, y=y, w=w, h=h, area=a, circ=circ, grass=float(gr), score=score)
    if GATE is not None and best is not None:
        # PL 掩码路径下候选多，闸门只验规则分最高的一个（板端 1.8 ms/次）
        c64 = _crop64(bgr, best["x"] + best["w"] / 2.0, best["y"] + best["h"] / 2.0,
                      6 * max(best["w"], best["h"]))
        if c64 is None or gate_prob(c64) < GATE["thr"]:
            return None
    return best


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--clip", required=True)
    ap.add_argument("--out", default="/tmp/fast_events.mp4")
    ap.add_argument("--json", default="/tmp/fast_events.json")
    ap.add_argument("--frames", type=int, default=200)
    ap.add_argument("--imgsz", type=int, default=320)
    ap.add_argument("--rois", type=int, default=10)
    ap.add_argument("--compensate", action="store_true", default=True)
    ap.add_argument("--no-compensate", dest="compensate", action="store_false")
    ap.add_argument("--pitch-inset", type=float, default=0.08)
    ap.add_argument("--pitch", default="", help="JSON with {\"polygon\": [[x,y],...]}; 缺省则不做出界判定")
    ap.add_argument("--no-video", action="store_true")
    ap.add_argument("--profile", action="store_true")
    ap.add_argument("--gate", default="", help="path to ball_gate.npz")
    ap.add_argument("--person-gate", default="", help="path to person_gate.npz")
    ap.add_argument("--mask-pl", default="", help="path to mask_preproc.bit (PL masks)")
    ap.add_argument("--dump-dir", default="", help="save annotated JPG every --dump-every frames")
    ap.add_argument("--dump-every", type=int, default=25)
    args = ap.parse_args(argv)
    if args.gate:
        load_gate(args.gate)
    if args.person_gate:
        load_person_gate(args.person_gate)

    cap = cv2.VideoCapture(args.clip)
    if not cap.isOpened():
        print("FAST_EVENTS_FAIL clip_open")
        return 2
    w_full = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h_full = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps_src = cap.get(cv2.CAP_PROP_FPS) or 25.0
    sw = args.imgsz
    sh = int(round(sw * h_full / float(w_full) / 2.0)) * 2

    if args.mask_pl:
        global MASK_OVL
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "ps" / "pynq_driver"))
        from mask_preproc import MaskPreprocOverlay
        MASK_OVL = MaskPreprocOverlay(args.mask_pl)
        MASK_OVL.prepare(w_full, h_full)

    writer = None if args.no_video else cv2.VideoWriter(args.out, cv2.VideoWriter_fourcc(*"mp4v"), fps_src, (w_full, h_full))
    prev = None
    prev_small = None
    prev_full = None
    pts = None
    if args.pitch:
        with open(args.pitch, encoding="utf-8") as fh:
            pts = np.float32(json.load(fh)["polygon"])
    out_enabled = pts is not None

    times = []
    prof = []
    events = []
    frames = []
    tracks = {}
    next_id = 0
    out_streak = 0
    n = 0
    while n < args.frames:
        ok, bgr = cap.read()
        if not ok:
            break
        t0 = time.perf_counter()
        small = cv2.resize(bgr, (sw, sh), interpolation=cv2.INTER_AREA)
        marks = [t0, time.perf_counter()]
        mask_full = None
        if MASK_OVL is None:
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            if prev_small is None:
                prev_small = gray
                prev = bgr
                n += 1
                continue

            if args.compensate:
                g16 = cv2.resize(gray, (sw // 2, sh // 2), interpolation=cv2.INTER_AREA)
                p16 = cv2.resize(prev_small, (sw // 2, sh // 2), interpolation=cv2.INTER_AREA)
                (dx, dy), _ = cv2.phaseCorrelate(np.float32(p16), np.float32(g16))
                M = np.float32([[1, 0, dx * 2], [0, 1, dy * 2]])
                prev_w = cv2.warpAffine(prev_small, M, (sw, sh), flags=cv2.INTER_LINEAR)
            else:
                prev_w = prev_small
            marks.append(time.perf_counter())

            d = cv2.absdiff(gray, prev_w)
            prev_small = gray
            _, mask = cv2.threshold(d, 10, 255, cv2.THRESH_BINARY)
        else:
            if prev_full is None:
                prev_full = bgr
                n += 1
                continue
            mask_full, _ = MASK_OVL.run(bgr, prev_full, white_thr=180)
            prev_full = bgr
            msmall = cv2.resize((mask_full & 2).astype(np.uint8), (sw, sh),
                                interpolation=cv2.INTER_NEAREST)
            mask = (msmall * 255).astype(np.uint8)
            marks.append(time.perf_counter())

        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        mask = cv2.dilate(mask, np.ones((5, 5), np.uint8), iterations=2)
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        boxes = []
        for c in cnts:
            a = cv2.contourArea(c)
            if a < 40:
                continue
            x, y, w, h = cv2.boundingRect(c)
            if h < 8 or not (0.25 <= w / float(h) <= 3.5):
                continue
            sx = w_full / float(sw)
            sy = h_full / float(sh)
            boxes.append([int(x * sx), int(y * sy), int(w * sx), int(h * sy)])
        boxes.sort(key=lambda b: -b[2] * b[3])
        boxes = boxes[:args.rois]
        marks.append(time.perf_counter())
        if PGATE is not None:
            kept = []
            for gx, gy, gw, gh in boxes[:6]:
                c = _person_window(bgr, gx + gw / 2.0, gy + gh / 2.0, gh)
                if c is not None and person_prob(c) >= PGATE["thr"]:
                    kept.append([gx, gy, gw, gh])
            boxes = kept
        marks.append(time.perf_counter())

        # 球员框简单关联（最近中心）
        det_out = []
        used = set()
        for b in boxes:
            cx, cy = b[0] + b[2] / 2.0, b[1] + b[3] / 2.0
            best_id, best_d = None, 1e9
            for tid, st in tracks.items():
                if tid in used:
                    continue
                dd = ((st["cx"] - cx) ** 2 + (st["cy"] - cy) ** 2) ** 0.5
                if dd < best_d and dd < 0.25 * max(w_full, h_full):
                    best_id, best_d = tid, dd
            if best_id is None:
                best_id = next_id
                next_id += 1
                tracks[best_id] = dict(cx=cx, cy=cy, h=b[3], hs=[], fall=0, last=n, fall_start=None)
            st = tracks[best_id]
            used.add(best_id)
            st.update(cx=cx, cy=cy)
            st["last"] = n
            st["hs"].append(b[3])
            if len(st["hs"]) > 30:
                st["hs"].pop(0)
            med_h = float(np.median(st["hs"]))
            fallen = (b[2] > 1.15 * b[3]) and (b[3] < 0.7 * med_h)
            st["fall"] = st["fall"] + 1 if fallen else 0
            if st["fall"] >= 5 and st["fall_start"] is None:
                st["fall_start"] = n
            elif st["fall"] == 0 and st["fall_start"] is not None:
                events.append(dict(type="fall", track=best_id, start=st["fall_start"], end=n))
                st["fall_start"] = None
            det_out.append(dict(track=best_id, box=b, fallen=st["fall"] >= 5))

        for tid in [t for t, s in tracks.items() if n - s["last"] > 25]:
            del tracks[tid]
        marks.append(time.perf_counter())

        # 球：在球员脚部 ROI 内找白色圆块，取全局最高分
        ball = None
        for b in boxes:
            cx = b[0] + b[2] // 2
            cy = b[1] + b[3]
            r = 64
            x0, y0 = max(0, cx - r), max(0, cy - r)
            x1, y1 = min(w_full, cx + r), min(h_full, cy + r)
            if x1 - x0 < 32 or y1 - y0 < 32:
                continue
            # 球走 PS 灰度阈值：PL 白掩码在 ROI 上没有形态学，候选处理贵 4.5 倍
            c = white_candidates(bgr[y0:y1, x0:x1])
            if c is None:
                continue
            c["cx"] = x0 + c["x"] + c["w"] / 2.0
            c["cy"] = y0 + c["y"] + c["h"] / 2.0
            # 白袜/白鞋几乎总在球员框内；球在脚边，通常在框外或框底
            inside_box = False
            for d_ in det_out:
                bx, by, bw, bh = d_["box"]
                if (bx + 0.15 * bw) <= c["cx"] <= (bx + 0.85 * bw) and \
                   (by + 0.15 * bh) <= c["cy"] <= (by + 0.95 * bh):
                    inside_box = True
                    break
            if inside_box:
                continue
            if ball is None or c["score"] > ball["score"] + 0.15:
                ball = c

        marks.append(time.perf_counter())
        outside = False
        if out_enabled and ball is not None:
            inside = cv2.pointPolygonTest(pts, (ball["cx"], ball["cy"]), False) >= 0
            outside = not inside
            out_streak = out_streak + 1 if outside else 0

        vis = bgr.copy()
        if out_enabled:
            cv2.polylines(vis, [pts.astype(np.int32)], True, (0, 255, 255), 1)
        for d_ in det_out:
            x, y, w, h = d_["box"]
            col = (0, 0, 255) if d_["fallen"] else (0, 255, 0)
            cv2.rectangle(vis, (x, y), (x + w, y + h), col, 2)
            cv2.putText(vis, "#%d%s" % (d_["track"], " FALL" if d_["fallen"] else ""),
                        (x, max(10, y - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.4, col, 1)
        if ball is not None:
            cv2.circle(vis, (int(ball["cx"]), int(ball["cy"])), 8, (255, 0, 255), 2)
        if out_streak >= 3:
            cv2.putText(vis, "OUT", (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        if writer is not None:
            writer.write(vis)
        if args.dump_dir and (n % args.dump_every) == 0:
            import os as _os
            _os.makedirs(args.dump_dir, exist_ok=True)
            cv2.imwrite(_os.path.join(args.dump_dir, "f%04d.jpg" % n), vis)

        dt = (time.perf_counter() - t0) * 1000.0
        marks.append(time.perf_counter())
        if args.profile and len(marks) >= 5:
            prof.append([(marks[i] - marks[i - 1]) * 1000.0 for i in range(1, len(marks))])
        times.append(dt)
        frames.append(dict(i=n, ms=round(dt, 2), players=len(det_out),
                           ball=None if ball is None else [round(ball["cx"], 1), round(ball["cy"], 1)],
                           out=out_streak >= 3,
                           fall=[d_["track"] for d_ in det_out if d_["fallen"]]))
        prev = bgr
        n += 1

    cap.release()
    if writer is not None:
        writer.release()
    if not times:
        print("FAST_EVENTS_FAIL no_frames")
        return 2
    med = float(np.median(times))
    res = dict(tool="tools/fast_events.py", clip=args.clip, frames=len(times),
               imgsz=[sw, sh], rois=args.rois, compensate=args.compensate,
               median_ms=med, fps=1000.0 / med,
               players_median=float(np.median([f["players"] for f in frames])),
               ball_frames=sum(1 for f in frames if f["ball"] is not None),
               out_frames=sum(1 for f in frames if f["out"]),
               fall_frames=sum(1 for f in frames if f["fall"]),
               events=events,
               per_frame=frames)
    with open(args.json, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    print("FAST_EVENTS_OK %d %.1f" % (len(times), 1000.0 / med))
    if args.profile and prof:
        a = np.array(prof)
        names = ["resize", "compensate", "contours", "person_gate", "track", "ball_roi", "draw"]
        print("PROFILE_MS " + " ".join("%s=%.1f" % (names[i], float(np.median(a[:, i])))
                                       for i in range(min(len(names), a.shape[1]))))
    print(json.dumps({k: v for k, v in res.items() if k != "per_frame"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
