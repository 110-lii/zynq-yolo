#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_scene_suite.py —— 按「画面性质」分层的测试集跑一遍，给出可比较的表。

为什么要有它
------------
`split_scenes.py` 把片段分成 `contest`（争抢：人挤在球周围）和 `normal`
（普通：阵型展开）。分层本身没有价值，价值在于**同一套检测在两层上的表现差多少** ——
如果两层差不多，说明分层没切到点子上；如果差很多，就有了「系统在哪里最脆」的量化证据。

这里量的都是能在主机上复现的量（不需要板子）：
    players_mean   每帧检出人数
    ball_pct       COCO sports-ball 类别检出率（争抢场景球被遮挡，应当明显更低）
    bunch_median   人物 x 轴聚集度（与 split_scenes 同一口径，用来反证分层确实分开了）
    det_ms_mean    单帧推理耗时（板子要按这个反推能达到多高帧率）

★ 这些是**检测器层面**的统计，不是裁判结论。判得准不准仍然要人眼看录像。

用法
----
    py tools/check_scene_suite.py
    py tools/check_scene_suite.py --dir data/samples/scenes --fps 5
    py tools/check_scene_suite.py --clips c01,n01     # 只跑指定片段

产物
----
    data/results/scene_suite.json

判据令牌
--------
    SCENE_SUITE_ROW <id> <kind> frames=<n> players=<f> ball_pct=<f> bunch=<f> det_ms=<f>
    SCENE_SUITE_OK <n_clips> contest=<f> normal=<f>  （ball_pct 两种场景的均值）
    SCENE_SUITE_FAIL <reason>
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[1]
DIR_DEFAULT = REPO / "data" / "samples" / "scenes"
OUT_DEFAULT = REPO / "data" / "results" / "scene_suite.json"
MODEL_DEFAULT = REPO / "data" / "models" / "yolov8n.pt"
PERSON, BALL = 0, 32


def bunch_width(xs, frac=0.5):
    n = len(xs)
    if n == 0:
        return None
    k = max(1, int(round(n * frac)))
    best = 1.0
    for i in range(0, n - k + 1):
        best = min(best, xs[i + k - 1] - xs[i])
    return float(best)


def kind_of(name):
    base = Path(name).name
    if base[:1] == "c":
        return "contest"
    if base[:1] == "n":
        return "normal"
    return "unknown"


def run_clip(model, path, fps, imgsz, conf):
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return None
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    step = max(1, int(round(src_fps / max(0.5, fps))))
    w = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
    n_pl, n_ball, bunches, dts, frames = [], 0, [], [], 0
    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if i % step == 0:
            t0 = time.perf_counter()
            res = model.predict(frame, imgsz=imgsz, conf=conf, verbose=False)[0]
            dts.append((time.perf_counter() - t0) * 1000.0)
            xs = []
            has_ball = False
            for b in res.boxes:
                c = int(b.cls.item())
                if c == PERSON:
                    x1, _y1, x2, _y2 = [float(v) for v in b.xyxy[0].tolist()]
                    xs.append(((x1 + x2) / 2.0) / max(1.0, float(w)))
                elif c == BALL:
                    has_ball = True
            n_pl.append(len(xs))
            n_ball += int(has_ball)
            if xs:
                bunches.append(bunch_width(sorted(xs)))
            frames += 1
        i += 1
    cap.release()
    if frames == 0:
        return None
    return {"frames": frames, "players_mean": float(np.mean(n_pl)),
            "players_max": int(np.max(n_pl)),
            "ball_pct": 100.0 * n_ball / frames,
            "bunch_median": (float(np.median([b for b in bunches if b is not None]))
                             if bunches else None),
            "det_ms_mean": float(np.mean(dts)), "src_fps": src_fps}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(DIR_DEFAULT))
    ap.add_argument("--fps", type=float, default=5.0)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--model", default=str(MODEL_DEFAULT))
    ap.add_argument("--clips", default="", help="只跑这些 id（逗号分隔，如 c01,n01）")
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    args = ap.parse_args()

    d = Path(args.dir)
    files = sorted(d.glob("*.mp4"))
    if args.clips:
        want = [s.strip() for s in args.clips.split(",") if s.strip()]
        files = [f for f in files if any(f.name.startswith(w) for w in want)]
    if not files:
        print("SCENE_SUITE_FAIL no_clips dir=%s" % d)
        return 2

    from ultralytics import YOLO
    model = YOLO(args.model)

    rows = []
    for f in files:
        r = run_clip(model, f, args.fps, args.imgsz, args.conf)
        if r is None:
            print("  SKIP %s" % f.name)
            continue
        row = dict(r, clip=f.name, kind=kind_of(f.name))
        rows.append(row)
        print("SCENE_SUITE_ROW %-8s %-7s frames=%3d players=%5.2f ball_pct=%5.1f%% "
              "bunch=%s det_ms=%5.1f"
              % (f.stem.split("_")[0], row["kind"], row["frames"], row["players_mean"],
                 row["ball_pct"],
                 "-" if row["bunch_median"] is None else "%.3f" % row["bunch_median"],
                 row["det_ms_mean"]))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"fps": args.fps, "imgsz": args.imgsz,
                               "conf": args.conf, "rows": rows},
                              ensure_ascii=False, indent=1), encoding="utf-8")

    agg = {}
    for k in ("contest", "normal"):
        sel = [r for r in rows if r["kind"] == k]
        if sel:
            agg[k] = {"n": len(sel),
                      "ball_pct": round(float(np.mean([r["ball_pct"] for r in sel])), 2),
                      "players": round(float(np.mean([r["players_mean"] for r in sel])), 2),
                      "bunch": round(float(np.mean([r["bunch_median"] or 0 for r in sel])), 3),
                      "det_ms": round(float(np.mean([r["det_ms_mean"] for r in sel])), 1)}
    print("SCENE_SUITE_AGG %s" % json.dumps(agg, ensure_ascii=False))
    if len(agg) < 2:
        print("SCENE_SUITE_FAIL need_both_kinds got=%s" % ",".join(agg) or "-")
        return 1
    print("SCENE_SUITE_OK %d contest=%.1f normal=%.1f  json=%s"
          % (len(rows), agg["contest"]["ball_pct"], agg["normal"]["ball_pct"], out.name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
