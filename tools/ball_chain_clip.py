"""ball_chain_clip.py -- 在一段成片视频上单跑「球链路」（候选 -> 分类器 -> 轨迹确认）。

为什么需要它
------------
板上主线 `tools/board_pipeline.py` 的球链路依赖板端 ncnn 检测器，PC 上跑不起来；
而 `tools/ball_chain_eval.py` 只在**已知球位置附近**抽样，不覆盖一段连续视频。
两段真实「出界」片段是否能把球跟到越线时刻，之前没有任何工具能直接回答。

本工具只跑球那一条链（不含 YOLO、不含事件），输入一段视频，输出逐帧确认结果：
  · 每帧候选数、进分类器的候选数、通过阈值的候选数、轨迹器输出的球
  · 汇总：球确认帧数 / 覆盖率 / 最长连续确认段
  · 可选：在指定时刻周围出标注帧（球用红圈标出），供人眼核对

用法
----
    & D:\\python\\python.exe tools\\ball_chain_clip.py \\
        --video data/samples/incidents/inc_06_t01901.6.mp4 \\
        --out data/results/ball_chain_inc06.json --viz-dir data/results/viz/ball_chain

判据令牌：BALL_CHAIN_CLIP_OK <frames> <ball_frames> / BALL_CHAIN_CLIP_FAIL <reason>
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import cv2

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from ball_cls_np import BallClassifier  # noqa: E402
from ball_cv_probe import candidates as ball_cands  # noqa: E402
from ball_track import BallTracker  # noqa: E402

NPZ = REPO / "build" / "models" / "ball_roi.npz"


def run(video, every, th, max_frames, out_json, viz_dir, viz_ts, grass_fallback=False):
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        print("BALL_CHAIN_CLIP_FAIL open %s" % video)
        return 2
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    clf = BallClassifier(str(NPZ)) if NPZ.exists() else None
    if clf is None:
        print("BALL_CHAIN_CLIP_FAIL missing %s" % NPZ)
        cap.release()
        return 2
    tr = BallTracker(min_hits=2, max_miss=2, min_speed=3.0)
    prev = None
    rows = []
    viz_at = {}
    for t in viz_ts:
        viz_at[int(round(t * fps))] = t
    if viz_dir:
        os.makedirs(viz_dir, exist_ok=True)
    idx = 0
    n_conf = 0
    run_start = run_len = run_best = 0
    t0 = time.perf_counter()
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if max_frames and idx >= max_frames:
            break
        diff = None
        if prev is not None:
            g1 = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            g0 = cv2.cvtColor(prev, cv2.COLOR_BGR2GRAY)
            diff = cv2.GaussianBlur(cv2.absdiff(g1, g0), (3, 3), 0)
        bc = ball_cands(frame, diff, grass_fallback=grass_fallback) if (idx % max(1, every) == 0) else []
        n_cand = len(bc)
        kept = []
        for c in bc:
            x, y = int(c["x"]), int(c["y"])
            x0, y0 = max(0, x - 32), max(0, y - 32)
            patch = frame[y0:y0 + 64, x0:x0 + 64]
            if patch.shape[0] != 64 or patch.shape[1] != 64:
                continue
            p = clf.prob(patch)
            if p >= th:
                kept.append(dict(c, p=round(float(p), 3)))
        bt = tr.step(kept, dt=float(every))
        ball = None
        if bt:
            b = max(bt, key=lambda t: t.hits)
            ball = {"x": round(b.x, 1), "y": round(b.y, 1),
                    "hits": b.hits, "miss": b.miss,
                    "conf": round(min(1.0, b.hits / 5.0), 2)}
            if b.miss == 0:
                n_conf += 1
                run_len += 1
                run_best = max(run_best, run_len)
            else:
                run_len = 0
        else:
            run_len = 0
        rows.append({"i": idx, "t": round(idx / fps, 3),
                     "n_cand": n_cand, "n_kept": len(kept), "ball": ball})
        if viz_dir and idx in viz_at:
            vis = frame.copy()
            c = ball
            if c is not None:
                cv2.circle(vis, (int(c["x"]), int(c["y"])), 18, (0, 0, 255), 2)
                cv2.putText(vis, "ball hits=%d" % c["hits"],
                            (int(c["x"]) + 20, int(c["y"]) - 12),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
            else:
                cv2.putText(vis, "no ball", (20, 40),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2)
            cv2.putText(vis, "t=%.1fs frame=%d cand=%d kept=%d"
                        % (idx / fps, idx, n_cand, len(kept)),
                        (20, 72), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)
            name = "%s_t%06.1f.jpg" % (Path(video).stem, idx / fps)
            cv2.imwrite(os.path.join(viz_dir, name), vis)
        prev = frame
        idx += 1
    cap.release()
    dt = time.perf_counter() - t0
    summary = {
        "tool": "tools/ball_chain_clip.py",
        "video": str(video),
        "fps": round(float(fps), 3),
        "frames": idx,
        "every": every,
        "th": th,
        "grass_fallback": bool(grass_fallback),
        "cand_total": sum(r["n_cand"] for r in rows),
        "kept_total": sum(r["n_kept"] for r in rows),
        "ball_frames": n_conf,
        "cover": round(n_conf / idx, 4) if idx else 0.0,
        "longest_run": run_best,
        "median_ms": round(1000.0 * dt / max(1, idx), 2),
        "rows": rows,
    }
    if out_json:
        os.makedirs(os.path.dirname(os.path.abspath(out_json)), exist_ok=True)
        with open(out_json, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=1)
    print("BALL_CHAIN_CLIP_OK %d %d" % (idx, n_conf))
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"},
                     ensure_ascii=False))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--every", type=int, default=1)
    ap.add_argument("--th", type=float, default=0.5)
    ap.add_argument("--max-frames", type=int, default=0)
    ap.add_argument("--out", default="")
    ap.add_argument("--viz-dir", default="")
    ap.add_argument("--viz-ts", default="", help="逗号分隔的秒数，出标注帧")
    ap.add_argument("--grass-fallback", action="store_true",
                    help="草场占比过小时改为全幅找白块（出界场景用）")
    args = ap.parse_args()
    ts = [float(s) for s in args.viz_ts.split(",") if s.strip()]
    return run(args.video, args.every, args.th, args.max_frames,
               args.out, args.viz_dir, ts, args.grass_fallback)


if __name__ == "__main__":
    sys.exit(main())
