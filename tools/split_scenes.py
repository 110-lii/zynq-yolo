#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""split_scenes.py —— 把整场转播切成「争抢场景」与「普通场景」两类片段。

为什么要有它
------------
上板测试一直只用 `data/samples/incidents/*.mp4`（12 段），而那些片段是
`find_incidents.py` 按「导播切回放 = 有争议事件」挑出来的 —— 它们**只代表
最难的一类画面**。拿只在难画面上跑通的系统去说「整场能用」，是拿不出证据的。
反过来，只测平淡画面也证明不了关键帧不丢。所以测试集要按**画面性质**分层：

    争抢  多人聚在球周围，框互相遮挡、球常年不可见 —— 检测最容易漏
    普通  阵型展开、球权清晰 —— 检测最容易过

★ 判据是**几何代理**，不是「争抢」的严格定义
  转播机位永远跟着球走，所以「球在画面中心附近」这一条信息量几乎为零；
  真正能区分两类画面的是**人员聚集度**：
      把检出的人按 x 排序，取含 50% 人的最窄区间，宽度 w（归一化到画面宽）
      争抢 ⇒ w 小（人都挤在一团）
      普通 ⇒ w 大（阵型铺开）
  这个量对相机平移/变焦不敏感（用的是相对宽度），但**会**被「画面里只有 4 个人」
  污染 —— 4 个人也可以很挤。所以同时要求人数下限，并用人数做次级排序。
  它给出的是**候选分层**，不是人工真值；片段最终仍需人眼确认（MANIFEST 留空列）。

用法
----
    py tools/split_scenes.py --plan                       # 只扫描打分，不切片
    py tools/split_scenes.py --extract --n-contest 4 --n-normal 4
    py tools/split_scenes.py --t0 600 --t1 2400 --probe-step 10
    py tools/split_scenes.py --video x.mp4 --extract      # 换片子（比赛 2）

产物
----
    data/samples/scenes/MANIFEST.csv   入库（只记时间/分数/哈希，可复现）
    data/samples/scenes/*.mp4          **不入库**（第三方转播内容，.gitignore 已排除）
    data/samples/scenes/_probe.json    逐点打分，便于复核判据

判据令牌
----
    SCENES_PROBE <n_points> <n_contest> <n_normal>
    SCENES_OK contest=<n> normal=<n> dir=<path>
    SCENES_FAIL <reason>
"""
import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[1]
VIDEO_DEFAULT = Path(r"D:\yt-dlp\downloads\migu_5106947698\migu_5106947698.mp4")
FFMPEG = Path(r"D:\yt-dlp\ffmpeg\ffmpeg.exe")
OUT_DEFAULT = REPO / "data" / "samples" / "scenes"
MODEL_DEFAULT = REPO / "data" / "models" / "yolov8n.pt"

PERSON_CLASS = 0
MIN_PLAYERS = 5          # 人数下限：低于它，聚集度没有意义（4 个人也能很挤）
COLS = ["scene_id", "kind", "t_start_s", "t_end_s", "n_probe_frames",
        "bunch_median", "players_median", "score", "clip_file", "sha256_16",
        "human_ok", "notes"]


def bunch_width(xs, frac=0.5):
    """含 frac 比例点的最窄 x 区间宽度（已归一化到 [0,1]）。xs 需已排序。"""
    n = len(xs)
    k = max(1, int(round(n * frac)))
    best = 1.0
    for i in range(0, n - k + 1):
        best = min(best, xs[i + k - 1] - xs[i])
    return float(best)


def green_frac(frame):
    """草皮占比。转播的球场镜头以绿为主；名单图/球员通道/看台特写不是。"""
    small = cv2.resize(frame, (320, 180), interpolation=cv2.INTER_AREA)
    b, g, r = (small[..., 0].astype(np.int16), small[..., 1].astype(np.int16),
               small[..., 2].astype(np.int16))
    grass = (g > 55) & (g > r + 12) & (g > b + 12)
    return float(grass.mean())


def probe_frame(model, frame, imgsz, conf):
    """返回 (n_players, xs 归一化数组, 框高中位数/画面高)。只看 person 类。

    框高的中位数用来排**特写**：转播远景里人只有画面高的几个百分点，
    一旦中位框高超过 ~30%，说明是替补席/通道/庆祝近景，这时「人挤」没有意义。
    """
    h, w = frame.shape[:2]
    res = model.predict(frame, imgsz=imgsz, conf=conf, verbose=False)[0]
    xs, hs = [], []
    for b in res.boxes:
        if int(b.cls.item()) != PERSON_CLASS:
            continue
        x1, y1, x2, y2 = [float(v) for v in b.xyxy[0].tolist()]
        xs.append(((x1 + x2) / 2.0) / max(1.0, w))
        hs.append((y2 - y1) / max(1.0, h))
    med_h = float(np.median(hs)) if hs else 1.0
    return len(xs), np.array(sorted(xs), dtype=np.float64), med_h


def scan(video, t0, t1, step, model, imgsz, conf, min_green, max_box, verbose=True):
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        print("SCENES_FAIL cannot_open %s" % video)
        return None
    pts = []
    t = t0
    while t < t1:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
        ok, frame = cap.read()
        if not ok or frame is None:
            t += step
            continue
        gf = green_frac(frame)
        n, xs, med_h = probe_frame(model, frame, imgsz, conf)
        rec = {"t": float(t), "n": int(n), "bunch": None, "score": 0.0,
               "green": round(gf, 4), "box_h": round(med_h, 4), "valid": False,
               "why": ""}
        if gf < min_green:
            rec["why"] = "not_pitch"
        elif med_h > max_box:
            rec["why"] = "closeup"
        elif n < MIN_PLAYERS:
            rec["why"] = "too_few_players"
        else:
            rec["valid"] = True
            bw = bunch_width(xs)
            rec["bunch"] = bw
            # 挤 + 人多 都算「争抢」：(1-bw) 是拥挤度，人数项封顶在 12 人
            rec["score"] = (1.0 - bw) * min(1.0, n / 12.0)
        pts.append(rec)
        if verbose and len(pts) % 20 == 0:
            print("  probe t=%6.0fs n=%2d green=%.2f h=%.2f bunch=%s score=%.3f %s"
                  % (rec["t"], rec["n"], rec["green"], rec["box_h"],
                     "-" if rec["bunch"] is None else "%.3f" % rec["bunch"],
                     rec["score"], rec["why"]))
        t += step
    cap.release()
    return pts


def pick(pts, kind, k, clip_len, sep, max_span, probe_step, exclude=()):
    """按分数挑 k 个互不重叠、彼此至少隔 sep 秒的窗口。返回 [(center_t, score, bunch, n)]。

    exclude：另一类已选的中心点。两类窗口不能叠在同一段画面上 —— 否则「争抢」
    和「普通」会指向同一秒，分层就自己把自己否掉了。
    """
    cand = [p for p in pts if p["bunch"] is not None]
    if not cand:
        return []
    if kind == "contest":
        cand.sort(key=lambda p: (-p["score"], -p["n"]))
    else:
        # 普通场景：人不少，但铺得开 —— 以 bunch 从大到小排，人数作为下限已满足
        cand.sort(key=lambda p: (-(p["bunch"] or 0), -p["n"]))
    chosen = []
    for p in cand:
        if len(chosen) >= k:
            break
        if all(abs(p["t"] - c["t"]) >= sep for c in chosen) and \
           all(abs(p["t"] - e) >= sep for e in exclude):
            chosen.append(p)
    # 片段以中心为准，向两侧各扩 clip_len/2；越界就整体平移
    out = []
    for p in chosen:
        half = clip_len / 2.0
        # 用窗口内的中位数据重申一次，避免单帧噪声决定整段。
        # ★ 取 ±(half + step/2)：探针间隔就是 step，只取 ±half 时如果 half < step，
        #   窗口里就只剩中心探针自己 —— 那「中位数」是假的（2026-10-30 踩过：
        #   MANIFEST 里 n_probe_frames 全是 1）。
        span = half + probe_step / 2.0
        near = [q for q in pts if abs(q["t"] - p["t"]) <= span and q["bunch"] is not None]
        bs = [q["bunch"] for q in near] or [p["bunch"]]
        ns = [q["n"] for q in near] or [p["n"]]
        start = min(max(0.0, p["t"] - half), max(0.0, max_span - clip_len))
        out.append({"center": p["t"], "start": start, "end": start + clip_len,
                    "bunch_median": float(np.median(bs)),
                    "players_median": float(np.median(ns)),
                    "score": float(p["score"]), "n_probe": len(near)})
    out.sort(key=lambda r: r["start"])
    return out


def cut(video, seg, dst):
    cmd = [str(FFMPEG), "-y", "-ss", "%.3f" % seg["start"], "-i", str(video),
           "-t", "%.3f" % (seg["end"] - seg["start"]),
           "-vf", "scale=1280:-2", "-c:v", "libx264", "-preset", "veryfast",
           "-crf", "23", "-pix_fmt", "yuv420p", "-an", str(dst)]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return p.returncode == 0 and dst.is_file() and dst.stat().st_size > 10000


def sha16(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default=str(VIDEO_DEFAULT))
    ap.add_argument("--t0", type=float, default=300.0, help="扫描起点（秒）")
    ap.add_argument("--t1", type=float, default=2700.0, help="扫描终点（秒）")
    ap.add_argument("--probe-step", type=float, default=10.0, help="每几秒探一帧")
    ap.add_argument("--n-contest", type=int, default=4)
    ap.add_argument("--n-normal", type=int, default=4)
    ap.add_argument("--clip-len", type=float, default=8.0, help="每段多长（秒）")
    ap.add_argument("--sep", type=float, default=40.0, help="同类窗口之间至少隔多远")
    ap.add_argument("--imgsz", type=int, default=640, help="检测输入尺寸（D010：640 才够召回）")
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--min-green", type=float, default=0.35,
                    help="草皮占比下限：低于它判定为非球场镜头（名单图/通道/看台）")
    ap.add_argument("--max-box", type=float, default=0.30,
                    help="人物框中位高度上限（占画面高）：超过它判定为特写")
    ap.add_argument("--model", default=str(MODEL_DEFAULT))
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    ap.add_argument("--plan", action="store_true", help="只扫描打分，不切片")
    ap.add_argument("--extract", action="store_true", help="扫描 + 切片 + 写 MANIFEST")
    ap.add_argument("--prefix", default="", help="片段文件名前缀（换片子时用，如 m2_）")
    args = ap.parse_args()

    if not (args.plan or args.extract):
        args.plan = True
    video = Path(args.video)
    if not video.is_file():
        print("SCENES_FAIL no_video %s" % video)
        return 2
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    try:
        from ultralytics import YOLO
    except Exception as e:                                   # noqa: BLE001
        print("SCENES_FAIL no_ultralytics %s" % e)
        return 2
    model = YOLO(args.model)

    t_start = time.time()
    print("SCENES_BEGIN video=%s range=%.0f..%.0fs step=%.0fs model=%s"
          % (video.name, args.t0, args.t1, args.probe_step, Path(args.model).name))
    pts = scan(video, args.t0, args.t1, args.probe_step, model, args.imgsz, args.conf,
               args.min_green, args.max_box)
    if pts is None:
        return 2
    (out / "_probe.json").write_text(
        json.dumps({"video": str(video), "t0": args.t0, "t1": args.t1,
                    "step": args.probe_step, "imgsz": args.imgsz,
                    "conf": args.conf, "min_players": MIN_PLAYERS,
                    "min_green": args.min_green, "max_box": args.max_box,
                    "points": pts}, ensure_ascii=False, indent=1), encoding="utf-8")

    scored = [p for p in pts if p["bunch"] is not None]
    contest = pick(pts, "contest", args.n_contest, args.clip_len, args.sep, args.t1,
                   args.probe_step)
    normal = pick(pts, "normal", args.n_normal, args.clip_len, args.sep, args.t1,
                  args.probe_step, exclude=[c["center"] for c in contest])
    from collections import Counter
    why = Counter(p["why"] for p in pts if not p["valid"])
    print("SCENES_PROBE %d %d %d  (scored=%d of %d points, %.0fs; 剔除 %s)"
          % (len(pts), len(contest), len(normal), len(scored), len(pts),
             time.time() - t_start, dict(why) or "0"))
    for kind, segs in (("contest", contest), ("normal", normal)):
        for s in segs:
            print("  %-7s t=%7.1f..%7.1f bunch=%.3f players=%.1f score=%.3f"
                  % (kind, s["start"], s["end"], s["bunch_median"],
                     s["players_median"], s["score"]))

    if not args.extract:
        print("SCENES_PLAN_OK（未切片；加 --extract 才写文件）")
        return 0

    rows = []
    for kind, segs in (("contest", contest), ("normal", normal)):
        for i, s in enumerate(segs, 1):
            sid = "%s%s%02d" % (args.prefix, "c" if kind == "contest" else "n", i)
            dst = out / ("%s_t%07.1f.mp4" % (sid, s["center"]))
            ok = cut(video, s, dst)
            rows.append({"scene_id": sid, "kind": kind,
                         "t_start_s": "%.2f" % s["start"], "t_end_s": "%.2f" % s["end"],
                         "n_probe_frames": s["n_probe"],
                         "bunch_median": "%.4f" % s["bunch_median"],
                         "players_median": "%.1f" % s["players_median"],
                         "score": "%.4f" % s["score"],
                         "clip_file": dst.name if ok else "",
                         "sha256_16": sha16(dst) if ok else "",
                         "human_ok": "", "notes": "" if ok else "cut_failed"})
            print("  CUT %-10s %s %s" % (sid, "OK" if ok else "FAIL", dst.name))

    man = out / "MANIFEST.csv"
    new = not man.is_file()
    with open(man, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS)
        if new:
            w.writeheader()
        for r in rows:
            w.writerow(r)

    good = [r for r in rows if r["clip_file"]]
    nc = sum(1 for r in good if r["kind"] == "contest")
    nn = sum(1 for r in good if r["kind"] == "normal")
    if nc == 0 or nn == 0:
        print("SCENES_FAIL no_clip contest=%d normal=%d" % (nc, nn))
        return 1
    print("SCENES_OK contest=%d normal=%d dir=%s manifest=%s"
          % (nc, nn, out, man.name))
    print("SCENES_NOTE 两类标签是**几何代理**（聚集度 + 人数），不是人工真值；"
          "MANIFEST 的 human_ok 列留给人填。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
