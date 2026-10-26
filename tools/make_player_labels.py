"""生成「球员 / 非球员」人工标注包 —— 让"颜色是否真的提升了识别"可被测量。

为什么必须做人工标注
--------------------
D013-a 的目标是「用颜色更好地识别球员」。要证明"更好"，必须有**真值**；而
`data/results/color_prior.json` 里的 keep_ratio / 队色距离都只是**结构量**，
按本仓库纪律不能当 precision/recall 用。所以先用这个脚本产出一个人能标的包。

标注口径（决定 precision 怎么算，必须先说死）
--------------------------------------------
本标注包**只标「这个框里的人是球员吗」**，不标"哪个队"：
    P (player)     场上球员（含守门员）
    N (non-player) 裁判、教练、替补、看台观众、球童、摄像师
    U (unsure)     看不清 / 只有半个人 / 太小无法判断
为什么不分队：分队是**另一个**问题（白色队靠饱和度判不可靠，见 docs/color_prior_v1.md），
混在一起标会让"球员识别"这个主问题被分队误差污染。

★ 采样必须**分层**，否则算出来的 precision 有偏
  分三层采样，各层都给足样本：
    hi      （conf >= 0.25）     —— 常规检测层的质量
    lo_kept （0.08<=conf<0.25，且颜色判明属于某队）—— 颜色先验**保留**的那批
    lo_drop （0.08<=conf<0.25，颜色判不明）        —— 颜色先验**丢弃**的那批
  三层的比例与总数都写进 MANIFEST，算 precision 时按层加权，不能用总体平均糊过去。

★ 判据必须先能失败
  MANIFEST 里 `n_labeled=0` 时，本脚本拒绝声称任何结论（只打印 "等待人工标注"）。

用法
----
    # 1) 生成标注包（默认每层 25 个框，共约 75 个）
    & D:\\python\\python.exe tools\\make_player_labels.py --per-stratum 25

    # 2) 人工看图，在 data/annot/labels.csv 的 `label` 列填 P/N/U

    # 3) 评分（颜色先验 vs 无先验），输出 precision/recall
    & D:\\python\\python.exe tools\\make_player_labels.py --score

产物
----
    data/annot/frames/<clip>_f<NNNN>_s<NN>.jpg   每框一张放大图（含上下 25% 上下文）
    data/annot/labels.csv                         待填：stratum,clip,frame,box_idx,conf,team,label
    data/annot/MANIFEST.json                      采样参数与分层计数
    data/annot/scores.json                        --score 后产生

判据令牌
    LABELS_PACK_OK <n_boxes> <n_hi> <n_lo_kept> <n_lo_drop>
    LABELS_SCORE_OK <n_labeled> <prec_color> <prec_nocolor>
    LABELS_FAIL <reason>
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import csv
import importlib.util
import json
import time
from pathlib import Path

import cv2

REPO = Path(__file__).resolve().parents[1]
RES = REPO / "data" / "results"
ANNOT = REPO / "data" / "annot"
FRAMES_DIR = ANNOT / "frames"
LABELS_CSV = ANNOT / "labels.csv"
MANIFEST = ANNOT / "MANIFEST.json"
SCORES = ANNOT / "scores.json"
MODEL = REPO / "data" / "models" / "yolov8n.pt"

# ★ 第一批（73 框，已标完）的产物必须留在原地：人工标注是不可重建的。
#   第二批（hi 层颜色-unknown，见 docs/hi_layer_color_gate.md §4）写到单独的文件，
#   **不覆盖**第一批的 labels.csv / frames/ / scores.json。
BATCH1_FRAMES = FRAMES_DIR
BATCH1_CSV = LABELS_CSV
BATCH1_SCORES = SCORES
BATCH2_FRAMES = ANNOT / "frames2"
BATCH2_CSV = ANNOT / "labels2.csv"
BATCH2_MANIFEST = ANNOT / "MANIFEST2.json"
BATCH2_SCORES = ANNOT / "scores2.json"

# 复用已校准的量测（importlib 加载）。
# ★ 光在自己文件顶部设 sys.dont_write_bytecode 不够：**被加载的模块**如果自己
#   没设，importlib 仍会为它写 __pycache__。所以在加载前后都设一次，且
#   classify_players.py 顶部也有同样一行（双保险）。check_repo.py 的 check d)
#   是扫盘的，不看 .gitignore ⇒ 漏一个就 FAIL。
sys.dont_write_bytecode = True
_spec = importlib.util.spec_from_file_location(
    "_cp", str(REPO / "tools" / "classify_players.py"))
_cp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cp)

HI_CONF = 0.25
LO_CONF = 0.08
PAD = 0.25          # 放大图的上下文比例（框外扩 25%）
CELL = 320          # 每张图的目标宽度


def _iou(a, b):
    x1 = max(a[0], b[0]); y1 = max(a[1], b[1])
    x2 = min(a[2], b[2]); y2 = min(a[3], b[3])
    iw = max(0.0, x2 - x1); ih = max(0.0, y2 - y1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    aa = max(1e-6, (a[2] - a[0]) * (a[3] - a[1]))
    bb = max(1e-6, (b[2] - b[0]) * (b[3] - b[1]))
    return inter / (aa + bb - inter)


def _crop_with_context(frame, bb):
    H, W = frame.shape[:2]
    x1, y1, x2, y2 = [float(v) for v in bb]
    w, h = x2 - x1, y2 - y1
    px, py = w * PAD, h * PAD
    cx1 = int(max(0, x1 - px)); cx2 = int(min(W, x2 + px))
    cy1 = int(max(0, y1 - py)); cy2 = int(min(H, y2 + py))
    if cx2 - cx1 < 8 or cy2 - cy1 < 8:
        return None
    crop = frame[cy1:cy2, cx1:cx2].copy()
    # 在原框位置画细框，便于人确认"要看的就是这个"
    bx1, by1 = int(x1 - cx1), int(y1 - cy1)
    bx2, by2 = int(x2 - cx1), int(y2 - cy1)
    cv2.rectangle(crop, (bx1, by1), (bx2, by2), (0, 255, 0), 1)
    sc = CELL / float(crop.shape[1])
    return cv2.resize(crop, (CELL, max(1, int(crop.shape[0] * sc))),
                      interpolation=cv2.INTER_CUBIC)


def build(args):
    from ultralytics import YOLO
    model = YOLO(str(MODEL))
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)
    ANNOT.mkdir(parents=True, exist_ok=True)

    clips = sorted((REPO / "data" / "samples" / "incidents").glob("*.mp4"))
    if args.max_clips:
        clips = clips[: args.max_clips]
    if not clips:
        print("LABELS_FAIL no_clips")
        return 2

    palette = {"A": "red", "B": "white"}
    strata = {"hi": [], "lo_kept": [], "lo_drop": []}

    # 分两轮：第一轮收集候选，第二轮按层采样并出图（避免边采边改配额）
    for clip in clips:
        cap = cv2.VideoCapture(str(clip))
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        frame_no = -1
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frame_no += 1
            if frame_no % args.stride:
                continue
            if frame_no % args.every_frame:      # 再稀一层，控制出图量
                continue

            r_lo = model.predict(frame, classes=[0], conf=LO_CONF, iou=0.45,
                                 imgsz=args.imgsz, verbose=False)
            boxes = r_lo[0].boxes if r_lo and r_lo[0].boxes is not None else None
            if boxes is None or len(boxes) == 0:
                continue
            bb_all = boxes.xyxy.cpu().numpy()
            cf_all = boxes.conf.cpu().numpy()

            for i, (bb, cf) in enumerate(zip(bb_all, cf_all)):
                h = float(bb[3] - bb[1])
                if h < 18:
                    continue                     # 太小，人眼也难判
                role, dist, purity = "unknown", 9.9, 0.0
                try:
                    role, dist, purity, _col = _cp_classify(frame, bb, palette)
                except Exception:
                    pass
                if cf >= HI_CONF:
                    stratum = "hi"
                elif role in ("A", "B") and dist < _cp.MAX_COLOR_DIST:
                    stratum = "lo_kept"
                else:
                    stratum = "lo_drop"
                info = {"clip": clip.name, "frame": frame_no,
                        "fps": fps, "box": [float(v) for v in bb],
                        "conf": float(cf), "role": role, "dist": float(dist),
                        "h": h}
                if len(strata[stratum]) < args.pool_per_stratum:
                    strata[stratum].append(info)
        cap.release()

    rows = []
    for stratum, pool in strata.items():
        if not pool:
            continue
        # 均匀采样（按 clip 轮转，避免全来自同一段）
        by_clip = {}
        for it in pool:
            by_clip.setdefault(it["clip"], []).append(it)
        picked = []
        order = sorted(by_clip)
        idx = 0
        while len(picked) < args.per_stratum and any(by_clip.values()):
            c = order[idx % len(order)]
            idx += 1
            if by_clip.get(c):
                picked.append(by_clip[c].pop(0))
        picked = picked[: args.per_stratum]

        # 出图（逐帧读，命中就裁）
        need = {}
        for it in picked:
            need.setdefault(it["clip"], {}).setdefault(it["frame"], []).append(it)
        for clip_name, frames_map in need.items():
            cap = cv2.VideoCapture(str(REPO / "data" / "samples" / "incidents" / clip_name))
            targets = set(frames_map)
            fn = -1
            got = {}
            while targets:
                ok, frame = cap.read()
                if not ok:
                    break
                fn += 1
                if fn in targets:
                    got[fn] = frame.copy()
                    targets.discard(fn)
            cap.release()
            for fn, items in frames_map.items():
                frame = got.get(fn)
                if frame is None:
                    continue
                for k, it in enumerate(items):
                    crop = _crop_with_context(frame, it["box"])
                    if crop is None:
                        continue
                    stem = "%s_f%04d_%s_%02d" % (Path(clip_name).stem, fn, stratum, len(rows))
                    p = FRAMES_DIR / (stem + ".jpg")
                    cv2.imwrite(str(p), crop, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
                    rows.append({
                        "id": len(rows) + 1,
                        "stratum": stratum,
                        "clip": clip_name,
                        "frame": fn,
                        "box_idx": k,
                        "conf": round(it["conf"], 4),
                        "h_px": round(it["h"], 1),
                        "color_role": it["role"],
                        "color_dist": round(it["dist"], 4),
                        "img": str(p.relative_to(REPO)).replace("\\", "/"),
                        "label": "",          # ← 人工填 P / N / U
                        "note": "",
                    })

    with open(LABELS_CSV, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["id"])
        w.writeheader()
        w.writerows(rows)

    MANIFEST.write_text(json.dumps({
        "tool": "tools/make_player_labels.py",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "purpose": "球员/非球员人工标注，用于评估颜色先验是否提升识别（D013-a）",
        "label_scheme": {"P": "球员（含守门员）", "N": "非球员（裁判/教练/替补/观众/球童/摄像师）",
                         "U": "看不清"},
        "strata": {"hi": "conf>=0.25", "lo_kept": "0.08<=conf<0.25 且颜色判明属某队",
                   "lo_drop": "0.08<=conf<0.25 且颜色判不明"},
        "sampling": {"per_stratum": args.per_stratum, "stride": args.stride,
                     "every_frame": args.every_frame, "imgsz": args.imgsz},
        "counts": {k: sum(1 for r in rows if r["stratum"] == k) for k in strata},
        "n_boxes": len(rows),
        "n_labeled": 0,
        "note": "n_labeled=0 时禁止声称任何 precision/recall；填完 CSV 再跑 --score",
    }, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")

    print("LABELS_CSV %s" % LABELS_CSV)
    print("FRAMES_DIR %s" % FRAMES_DIR)
    n = {k: sum(1 for r in rows if r["stratum"] == k) for k in strata}
    print("LABELS_PACK_OK %d %d %d %d" % (len(rows), n["hi"], n["lo_kept"], n["lo_drop"]))
    return 0


def _cp_classify(frame, bb, palette):
    h = float(bb[3] - bb[1])
    if h < _cp.MIN_H_PX:
        return "unknown", 9.9, 0.0, None
    rect = _cp.torso_rect(bb, frame.shape)
    if rect is None:
        return "unknown", 9.9, 0.0, None
    col = _cp.torso_color(frame, rect)
    if col is None:
        return "unknown", 9.9, 0.0, None
    if col[4] < _cp.MIN_PURITY:
        return "unknown", 9.9, col[4], col
    role, dist, _s = _cp.classify(col, palette)
    return role, dist, col[4], col


# ---------------------------------------------------------------------------
# 第二批：hi 层「颜色 unknown」的框
#
# 为什么要这一批（docs/hi_layer_color_gate.md §4）
#   全量 12 段实测：hi 层 12 585 个框里有 2 596 个（20.6%）判不出队色，其中
#       color 44.4% / purity 37.1% / too_small 14.7% / rect 3.8%
#   ⇒ 85% 的 unknown 是「框够大但颜色不合格」，不是信息不足，所以颜色阈值有可能救。
#   **但救回来的框到底对不对，没有任何数据能回答。** 要回答的只有一个问题：
#       这 2 596 个取色失败的 hi 框里，球员和非球员各占多少？
#   若非球员占比显著高于 hi 层整体（24 个里 6 个 = 25%）⇒ unknown 本身就是信号，
#   不用救颜色，直接降权；若球员占比和整体一样 ⇒ 颜色在 hi 层没信息量，该放弃这条路。
#
# 分层（按失败原因分，因为它们的「可救性」不同）：
#   hi_unk_purity   框够大，躯干里球衣像素太少（MIN_PURITY 门限）
#   hi_unk_color    取到色了，但离两队参考色都远（MAX_COLOR_DIST 门限）
#   hi_unk_toosmall 框高 < MIN_H_PX，躯干只有十几像素（信息不足，阈值救不了）
#   hi_unk_rect     取不出躯干矩形
# 按原因分层 = 按「可救性」分层，这样每一层的结论能直接指导要不要动对应阈值。
# ---------------------------------------------------------------------------

B2_REASONS = ("purity", "color", "too_small", "rect")
B2_STRATUM = {"purity": "hi_unk_purity", "color": "hi_unk_color",
              "too_small": "hi_unk_toosmall", "rect": "hi_unk_rect"}


def _classify_with_reason(frame, bb, palette):
    """返回 (role, dist, purity, reason)。reason=None 表示判出了队色。"""
    h = float(bb[3] - bb[1])
    if h < _cp.MIN_H_PX:
        return "unknown", 9.9, 0.0, "too_small"
    rect = _cp.torso_rect(bb, frame.shape)
    if rect is None:
        return "unknown", 9.9, 0.0, "rect"
    col = _cp.torso_color(frame, rect)
    if col is None:
        return "unknown", 9.9, 0.0, "rect"
    if col[4] < _cp.MIN_PURITY:
        return "unknown", 9.9, col[4], "purity"
    role, dist, _s = _cp.classify(col, palette)
    if role not in ("A", "B") or dist >= _cp.MAX_COLOR_DIST:
        return "unknown", float(dist), col[4], "color"
    return role, dist, col[4], None


def build_hi_unknown(args):
    from ultralytics import YOLO
    model = YOLO(str(MODEL))
    BATCH2_FRAMES.mkdir(parents=True, exist_ok=True)
    ANNOT.mkdir(parents=True, exist_ok=True)

    clips = sorted((REPO / "data" / "samples" / "incidents").glob("*.mp4"))
    if args.max_clips:
        clips = clips[: args.max_clips]
    if not clips:
        print("LABELS_FAIL no_clips")
        return 2

    palette = {"A": "red", "B": "white"}
    pools = {k: [] for k in B2_REASONS}

    for clip in clips:
        cap = cv2.VideoCapture(str(clip))
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        frame_no = -1
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frame_no += 1
            if frame_no % max(1, args.stride):
                continue
            if args.every_frame > 1 and frame_no % args.every_frame:
                continue
            r = model.predict(frame, classes=[0], conf=HI_CONF, iou=0.45,
                              imgsz=args.imgsz, verbose=False)
            boxes = r[0].boxes if r and r[0].boxes is not None else None
            if boxes is None or len(boxes) == 0:
                continue
            for bb, cf in zip(boxes.xyxy.cpu().numpy(), boxes.conf.cpu().numpy()):
                h = float(bb[3] - bb[1])
                role, dist, purity, reason = _classify_with_reason(frame, bb, palette)
                if reason is None:
                    continue                      # 颜色判得出来，不属本批
                if h < args.min_h:                # 人眼也判不了的不出图
                    continue
                pools[reason].append({
                    "clip": clip.name, "frame": frame_no, "fps": fps,
                    "box": [float(v) for v in bb], "conf": float(cf),
                    "role": role, "dist": float(dist), "purity": float(purity),
                    "h": h, "reason": reason,
                })
        cap.release()
        print("  [%s] pools=%s" % (clip.name, {k: len(v) for k, v in pools.items()}),
              flush=True)

    # 每层均匀采样（按 clip 轮转，避免全来自同一段）
    rows = []
    for reason in B2_REASONS:
        pool = pools[reason]
        if not pool:
            continue
        by_clip = {}
        for it in pool:
            by_clip.setdefault(it["clip"], []).append(it)
        picked = []
        order = sorted(by_clip)
        idx = 0
        while len(picked) < args.per_stratum and any(by_clip.values()):
            c = order[idx % len(order)]
            idx += 1
            if by_clip.get(c):
                picked.append(by_clip[c].pop(0))
        picked = picked[: args.per_stratum]

        need = {}
        for it in picked:
            need.setdefault(it["clip"], {}).setdefault(it["frame"], []).append(it)
        for clip_name, frames_map in need.items():
            cap = cv2.VideoCapture(str(REPO / "data" / "samples" / "incidents" / clip_name))
            targets = set(frames_map)
            fn = -1
            got = {}
            while targets:
                ok, frame = cap.read()
                if not ok:
                    break
                fn += 1
                if fn in targets:
                    got[fn] = frame.copy()
                    targets.discard(fn)
            cap.release()
            for fn, items in frames_map.items():
                frame = got.get(fn)
                if frame is None:
                    continue
                for k, it in enumerate(items):
                    crop = _crop_with_context(frame, it["box"])
                    if crop is None:
                        continue
                    stem = "b2_%s_f%04d_%s_%03d" % (
                        Path(clip_name).stem, fn, reason, len(rows))
                    p = BATCH2_FRAMES / (stem + ".jpg")
                    cv2.imwrite(str(p), crop, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
                    rows.append({
                        "id": len(rows) + 1,
                        "stratum": B2_STRATUM[reason],
                        "fail_reason": reason,
                        "clip": clip_name,
                        "frame": fn,
                        "box_idx": k,
                        "conf": round(it["conf"], 4),
                        "h_px": round(it["h"], 1),
                        "purity": round(it["purity"], 3),
                        "color_role": it["role"],
                        "color_dist": round(it["dist"], 4),
                        "img": str(p.relative_to(REPO)).replace("\\", "/"),
                        "label": "",
                        "note": "",
                    })

    if not rows:
        print("LABELS_FAIL no_hi_unknown_rows")
        return 2

    with open(BATCH2_CSV, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    n = {k: sum(1 for r in rows if r["fail_reason"] == k) for k in B2_REASONS}
    BATCH2_MANIFEST.write_text(json.dumps({
        "tool": "tools/make_player_labels.py --hi-unknown",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "purpose": ("hi 层颜色-unknown 的框里球员/非球员各占多少（D013-a 第二步）；"
                    "决定颜色在 hi 层有没有信息量，见 docs/hi_layer_color_gate.md §4"),
        "label_scheme": {"P": "球员（含守门员）",
                         "N": "非球员（裁判/教练/替补/观众/球童/摄像师）",
                         "U": "看不清"},
        "strata": {
            "hi_unk_purity": "框够大但躯干里球衣像素占比 < MIN_PURITY(0.30)",
            "hi_unk_color": "取到色但离两队参考色都 >= MAX_COLOR_DIST(0.16)",
            "hi_unk_toosmall": "框高 < MIN_H_PX(22)，信息不足，阈值救不了",
            "hi_unk_rect": "取不出躯干矩形",
        },
        "params": {"hi_conf": HI_CONF, "per_stratum": args.per_stratum,
                   "stride": args.stride, "every_frame": args.every_frame,
                   "imgsz": args.imgsz, "min_h": args.min_h,
                   "min_purity": _cp.MIN_PURITY,
                   "max_color_dist": _cp.MAX_COLOR_DIST},
        "pool_sizes": {k: len(v) for k, v in pools.items()},
        "counts": n,
        "n_total": len(rows),
        "does_not_overwrite_batch1": True,
    }, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")

    print("LABELS_CSV %s" % BATCH2_CSV)
    print("FRAMES_DIR %s" % BATCH2_FRAMES)
    print("LABELS_PACK_OK %d %d %d %d %d"
          % (len(rows), n["purity"], n["color"], n["too_small"], n["rect"]))
    return 0


def _read_csv(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def score_hi_unknown(args):
    """第二批的判决：unknown 里的非球员占比 vs hi 层整体，以及三种策略的 precision。"""
    if not BATCH2_CSV.exists():
        print("LABELS_FAIL labels2_csv_missing %s" % BATCH2_CSV)
        return 2
    rows = _read_csv(BATCH2_CSV)
    labeled = [r for r in rows if r["label"] in ("P", "N", "U")]
    used = [r for r in labeled if r["label"] in ("P", "N")]
    if not used:
        print("LABELS_FAIL no_labels_yet (先在 %s 的 label 列填 P/N/U)" % BATCH2_CSV)
        return 2

    # 参照：hi 层整体里非球员占多少（第一批标注，24 个里 6 个）
    ref = {"n": 24, "n_nonplayer": 6, "nonplayer_rate": 0.25,
           "source": "data/annot/scores.json by_stratum.hi（第一批标注）",
           "note": "参照值来自第一批 24 个 hi 框；若第一批被重标过需同步更新"}

    def _rate(sel):
        sub = [r for r in used if sel(r)]
        if not sub:
            return {"n": 0}
        n_np = sum(1 for r in sub if r["label"] == "N")
        return {"n": len(sub), "n_player": sum(1 for r in sub if r["label"] == "P"),
                "n_nonplayer": n_np,
                "nonplayer_rate": round(n_np / len(sub), 3),
                "precision_if_kept": round(sum(1 for r in sub if r["label"] == "P") / len(sub), 3)}

    by_stratum = {}
    for s in ("hi_unk_purity", "hi_unk_color", "hi_unk_toosmall", "hi_unk_rect"):
        by_stratum[s] = _rate(lambda r, s=s: r["stratum"] == s)
    unk_all = _rate(lambda r: True)

    # 可选动作的对照（假设：hi 层判不出颜色的框要不要留）
    strat = {
        "全部保留": _rate(lambda r: True),
        "丢掉 purity+rect+too_small（只救 color）": _rate(lambda r: r["fail_reason"] == "color"),
        "只丢掉 purity": _rate(lambda r: r["fail_reason"] == "purity"),
    }

    out = {
        "tool": "tools/make_player_labels.py --score-hi-unknown",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "n_labeled": len(labeled), "n_used": len(used),
        "n_unsure_excluded": len(labeled) - len(used),
        "reference_hi_layer": ref,
        "unknown_overall": unk_all,
        "by_stratum": by_stratum,
        "by_action": strat,
        "verdict": None,
        "caveats": [
            "样本内精确率；分层抽样 ⇒ 不能当总体比例",
            "参照值 25% 来自第一批 24 个 hi 框，样本量小",
            "n 小，置信区间宽；只看方向",
        ],
    }

    # 判决：unknown 里非球员占比是否显著高于 hi 层整体
    if unk_all.get("n"):
        gap = unk_all["nonplayer_rate"] - ref["nonplayer_rate"]
        out["gap_vs_hi_layer"] = round(gap, 3)
        if gap >= 0.10:
            out["verdict"] = ("unknown 里非球员占比 %.1f%%，显著高于 hi 层整体的 %.1f%% "
                              "⇒ 「颜色判不出」本身可作非球员信号，不必去救颜色"
                              % (100 * unk_all["nonplayer_rate"], 100 * ref["nonplayer_rate"]))
        elif gap <= -0.10:
            out["verdict"] = ("unknown 里非球员占比 %.1f%%，**低于** hi 层整体的 %.1f%% "
                              "⇒ 颜色判不出反而偏向球员，丢掉 unknown 会伤 recall，颜色在 hi 层无信息量"
                              % (100 * unk_all["nonplayer_rate"], 100 * ref["nonplayer_rate"]))
        else:
            out["verdict"] = ("unknown 里非球员占比 %.1f%% 与 hi 层整体 %.1f%% 无显著差异 "
                              "⇒ 颜色在 hi 层没有信息量，这条路应放弃"
                              % (100 * unk_all["nonplayer_rate"], 100 * ref["nonplayer_rate"]))

    BATCH2_SCORES.write_text(json.dumps(out, ensure_ascii=False, indent=1),
                             encoding="utf-8", newline="\n")
    print("SCORES %s" % BATCH2_SCORES)
    print("LABELS_SCORE2_OK %d %s" % (len(used),
                                      (out["verdict"] or "")[:60]))
    for k, v in by_stratum.items():
        if v.get("n"):
            print("  %-18s n=%-3d 非球员=%-3d 非球员率=%4.0f%% 保留时精度=%4.0f%%"
                  % (k, v["n"], v["n_nonplayer"], 100 * v["nonplayer_rate"],
                     100 * v["precision_if_kept"]))
    print("  VERDICT: %s" % out["verdict"])
    return 0


def score(args):
    if not LABELS_CSV.exists():
        print("LABELS_FAIL labels_csv_missing")
        return 2
    with open(LABELS_CSV, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    labeled = [r for r in rows if r["label"] in ("P", "N", "U")]
    used = [r for r in labeled if r["label"] in ("P", "N")]
    if not used:
        print("LABELS_FAIL no_labels_yet (先在 %s 的 label 列填 P/N/U)" % LABELS_CSV)
        return 2

    def _acc(rows_):
        tp = sum(1 for r in rows_ if r["label"] == "P")
        fp = sum(1 for r in rows_ if r["label"] == "N")
        return tp, fp

    # 颜色先验的效果只在"它会改变决策"的层上体现：
    #   lo_kept -> 颜色说"是球员" -> 保留
    #   lo_drop -> 颜色说"不明" -> 丢弃
    strata_report = {}
    for s in ("hi", "lo_kept", "lo_drop"):
        sub = [r for r in used if r["stratum"] == s]
        if not sub:
            continue
        tp, fp = _acc(sub)
        strata_report[s] = {"n": len(sub), "n_player": tp, "n_nonplayer": fp,
                            "precision_in_stratum": round(tp / len(sub), 3) if sub else None}

    # 颜色先验策略：hi 全收 + lo_kept 全收 + lo_drop 全丢
    keep_color = [r for r in used if r["stratum"] in ("hi", "lo_kept")]
    # 无颜色策略：hi + 所有 lo（不做颜色过滤）
    keep_nocolor = [r for r in used if r["stratum"] in ("hi", "lo_kept", "lo_drop")]

    def _prec(rows_):
        if not rows_:
            return None
        tp = sum(1 for r in rows_ if r["label"] == "P")
        return round(tp / len(rows_), 4)

    def _recall_proxy(rows_):
        """召回代理：真球员中，被该策略保留的比例。

        ⚠️ 这不是 recall：真球员全集未知（未被检出的球员不在样本里）。
        这里只能说明"在被标出来的球员里，策略留下了多少"。
        """
        allp = sum(1 for r in used if r["label"] == "P")
        if not allp:
            return None
        kept = sum(1 for r in rows_ if r["label"] == "P")
        return round(kept / allp, 4)

    out = {
        "tool": "tools/make_player_labels.py --score",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "n_labeled": len(labeled), "n_used": len(used),
        "n_unsure_excluded": len(labeled) - len(used),
        "by_stratum": strata_report,
        "keep_color_prior": {"n": len(keep_color),
                             "precision_among_kept": _prec(keep_color),
                             "recall_proxy": _recall_proxy(keep_color)},
        "keep_no_color": {"n": len(keep_nocolor),
                          "precision_among_kept": _prec(keep_nocolor),
                          "recall_proxy": _recall_proxy(keep_nocolor)},
        "caveats": [
            "precision 是**样本内**精确率，样本是分层抽的，不是随机抽的 ⇒ 不能当总体精确率",
            "recall_proxy 不是 recall：未被检测出的球员不在样本中",
            "每层约 25 个，95% 置信区间宽约 ±9 pt；差值方向可信、幅度不可信",
            "标注只有一人一轮，无第二人复核，标注者偏差未量化",
            "颜色阈值（MAX_COLOR_DIST 等）由 257 个躯干样本校准，不是在本测试集上调的 ⇒ 无过拟合",
        ],
    }

    # ---- 框高 vs 是否球员（与颜色正交的一条线索，首轮发现见 docs/player_labels_v1.md §4）----
    BANDS = ((0, 30, "0-30"), (30, 50, "30-50"), (50, 100, "50-100"),
             (100, 150, "100-150"), (150, 1e9, "150+"))
    band_n, band_pct = {}, {}
    for lo, hi, name in BANDS:
        sub = [r for r in used if lo <= float(r["h_px"]) < hi]
        if not sub:
            continue
        band_n[name] = len(sub)
        band_pct[name] = round(100.0 * sum(1 for r in sub if r["label"] == "P") / len(sub))
    out["by_box_height"] = {"note": "P/N 共 %d 个；用于检验「大框=非球员」这条与颜色正交的线索" % len(used),
                            "counts": band_n, "pct_player": band_pct}

    # hi 层内部按 100 px 切一刀：首轮 hi 层 6 个非球员里 5 个 h>=100
    for tag, sel in (("hi_h_lt_100", lambda r: float(r["h_px"]) < 100),
                     ("hi_h_ge_100", lambda r: float(r["h_px"]) >= 100)):
        sub = [r for r in used if r["stratum"] == "hi" and sel(r)]
        out[tag] = {"n": len(sub),
                    "n_player": sum(1 for r in sub if r["label"] == "P"),
                    "precision": round(sum(1 for r in sub if r["label"] == "P") / len(sub), 3) if sub else None}
    SCORES.write_text(json.dumps(out, ensure_ascii=False, indent=1),
                      encoding="utf-8", newline="\n")
    print("SCORES %s" % SCORES)
    print("LABELS_SCORE_OK %d %s %s"
          % (len(used), out["keep_color_prior"]["precision_among_kept"],
             out["keep_no_color"]["precision_among_kept"]))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="球员/非球员人工标注包（D013-a 评估用）")
    ap.add_argument("--per-stratum", type=int, default=25)
    ap.add_argument("--pool-per-stratum", type=int, default=400)
    ap.add_argument("--max-clips", type=int, default=0)
    ap.add_argument("--stride", type=int, default=2)
    ap.add_argument("--every-frame", type=int, default=25,
                    help="每 N 个处理帧才取一帧做候选（控制出图量）")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--score", action="store_true",
                    help="第一批（73 框）评分")
    # ---- 第二批：hi 层颜色-unknown（docs/hi_layer_color_gate.md §4）----
    ap.add_argument("--hi-unknown", action="store_true",
                    help="构建第二批标注包：hi 层「颜色判定失败」的框")
    ap.add_argument("--score-hi-unknown", action="store_true",
                    help="第二批评分：unknown 里的非球员占比 vs hi 层整体")
    ap.add_argument("--min-h", type=float, default=18.0,
                    help="第二批出图的框高下限（太小的人眼也判不了）")
    args = ap.parse_args(argv)

    # 三个动作互斥，且**第二批绝不覆盖第一批的 labels.csv / frames/**
    n_act = sum(bool(x) for x in (args.score, args.hi_unknown, args.score_hi_unknown))
    if n_act > 1:
        print("LABELS_FAIL multiple_actions")
        return 2
    if args.score_hi_unknown:
        return score_hi_unknown(args)
    if args.hi_unknown:
        return build_hi_unknown(args)
    return score(args) if args.score else build(args)


if __name__ == "__main__":
    raise SystemExit(main())
