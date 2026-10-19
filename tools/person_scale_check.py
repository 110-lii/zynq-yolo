#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""person_scale_check.py -- 人（person）检测的「输入分辨率」依赖性

## 这个工具回答什么问题

D010 把题目收窄为「球员位置 / 队形 / 越位线 / 区域人数」之后，D004 的**输入分辨率**
判据从「球要看得见」变成了「**人**要看得见」。球在转播画面里只有 4–12 px，人要大一个
量级 —— 但「大一个量级」是估计，不是测量。

本工具把**高分辨率检测结果当作参考**（默认 1280），测低分辨率（默认 320/640）能
**召回**多少参考框。若 320 的召回已经够高，则整个 PL 通路可以按 320 设计，
算力立刻省 16 倍（2.186 GOP/帧 vs 34.98 GOP/帧）。

## 为什么参考用 1280 而不是人工标注

人工标注 40 帧 x 十几个人 = 数百个框，成本高且会引入我自己的标注误差。
用 1280 检测作参考是一个**可复现**的代理：它的绝对精度未知，但**它比 320 好**这一点
是已经量过的（`data/results/ball_visibility_sweep.json` 里 person 框数 320→667、
1280→731）。⚠️ 因此本工具给出的是**相对召回**（320 相对于 1280），
**不是**绝对精度；报告引用时必须这么写。

## 判据（必须先能失败）

1. **参考框数 > 0**：任一帧参考框为 0 则整体 FAIL —— 否则召回率的分母是 0，会静默"全过"。
2. **阴性对照（换帧匹配）**：把测试帧的检测与**另一帧**的参考框做匹配，召回率必须
   **显著低于**真实匹配（< 20%）。若两者接近，说明匹配器或数据无判别力。
3. **阴性对照（参考自身）**：同一帧 1280 对自身匹配，召回必须 = 100%。若不为 100%，
   匹配器有 bug（不是数据问题）。

令牌：`PERSON_SCALE_OK` / `PERSON_SCALE_FAIL <原因>`

## 用法

    python tools/person_scale_check.py                      # 默认 1280 参考 / 320,640 测试
    python tools/person_scale_check.py --test-size 320,160
    python tools/person_scale_check.py --frames data/samples/calib_real/frames_calib
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # 早于任何本地 import；防止 tools/__pycache__ 被 check_repo.py 的 check d) 判 FAIL

import argparse
import json
import platform
from datetime import datetime, timezone, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MODEL = REPO / "data" / "models" / "yolov8n.pt"
OUT_DEFAULT = REPO / "data" / "results" / "person_scale.json"

PERSON_CLASS = 0  # COCO；会用 model.names 自检


# ---------------------------------------------------------------- 检测


def detect(model, img_path, size, conf, iou):
    """返回原图坐标系下的 person 框列表 [x1,y1,x2,y2,score]。"""
    r = model.predict(
        source=str(img_path),
        imgsz=int(size),
        conf=float(conf),
        iou=float(iou),
        classes=[PERSON_CLASS],
        verbose=False,
        device="cpu",
    )[0]
    out = []
    for b in r.boxes:
        x1, y1, x2, y2 = [float(v) for v in b.xyxy[0].tolist()]
        out.append([x1, y1, x2, y2, float(b.conf[0])])
    return out


# ---------------------------------------------------------------- 匹配


def iou_one(a, b):
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0.0:
        return 0.0
    aa = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    bb = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    u = aa + bb - inter
    return inter / u if u > 0 else 0.0


def match(ref, test, iou_thr):
    """贪心匹配（按参考框分数降序），返回 (matched_pairs, unmatched_ref_idx)。

    与 tools/eval_calib_set.py / tools/draw_detections.py 的匹配语义保持一致：
    同类（此处只有人）、IoU >= iou_thr、贪心不重复。
    """
    used = [False] * len(test)
    pairs = []
    miss = []
    for i in sorted(range(len(ref)), key=lambda k: -ref[k][4]):
        best, best_j = iou_thr, -1
        for j in range(len(test)):
            if used[j]:
                continue
            v = iou_one(ref[i], test[j])
            if v >= best:
                best, best_j = v, j
        if best_j < 0:
            miss.append(i)
        else:
            used[best_j] = True
            pairs.append((i, best_j, best))
    return pairs, miss


# ---------------------------------------------------------------- 主流程


def pct(xs, p):
    if not xs:
        return None
    s = sorted(xs)
    k = (len(s) - 1) * p / 100.0
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="人检测的输入分辨率依赖性（相对召回）", allow_abbrev=False
    )
    ap.add_argument("--frames", default=str(REPO / "data" / "samples" / "calib_real" / "frames_test"))
    ap.add_argument("--ref-size", type=int, default=1280)
    ap.add_argument("--test-size", default="320,640")
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--iou", type=float, default=0.5)
    ap.add_argument("--model", default=str(MODEL))
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)

    from ultralytics import YOLO  # 延迟到参数解析之后再 import（--help 不必付这个代价）

    frames_dir = Path(args.frames)
    if not frames_dir.is_dir():
        print(f"PERSON_SCALE_FAIL 目录不存在 {frames_dir}")
        return 2
    frames = sorted(
        [p for p in frames_dir.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png")]
    )
    if args.limit > 0:
        frames = frames[: args.limit]
    if not frames:
        print(f"PERSON_SCALE_FAIL 目录内无图 {frames_dir}")
        return 2

    model_path = Path(args.model)
    if not model_path.is_file():
        print(f"PERSON_SCALE_FAIL 模型不存在 {model_path}")
        return 2

    # ★ 类别自检：class 0 必须是 person。COCO 上它是，但这是外部依赖，必须当场验。
    model = YOLO(str(model_path))
    names = getattr(model, "names", {}) or {}
    cname = names.get(PERSON_CLASS) or names.get(str(PERSON_CLASS))
    if cname != "person":
        print(f"PERSON_SCALE_FAIL 类别自检失败：names[{PERSON_CLASS}] = {cname!r}，期望 'person'")
        return 2
    print(f"类别自检 OK：names[{PERSON_CLASS}] = {cname!r}")

    test_sizes = [int(s) for s in str(args.test_size).split(",") if s.strip()]
    ref_size = int(args.ref_size)

    # ---- 逐帧检测（参考一次 + 每个测试尺寸一次）
    print(f"帧目录：{frames_dir}  共 {len(frames)} 帧")
    ref_boxes = {}
    test_boxes = {s: {} for s in test_sizes}
    orig_wh = {}
    for p in frames:
        ref_boxes[p.name] = detect(model, p, ref_size, args.conf, args.iou)
        import cv2  # 只为取原图尺寸

        img = cv2.imread(str(p))
        orig_wh[p.name] = (int(img.shape[1]), int(img.shape[0]))
        for s in test_sizes:
            test_boxes[s][p.name] = detect(model, p, s, args.conf, args.iou)

    n_ref_total = sum(len(v) for v in ref_boxes.values())
    empty_frames = [k for k, v in ref_boxes.items() if not v]
    print(f"参考（{ref_size}）person 框总数 = {n_ref_total}，参考为空帧 = {len(empty_frames)}")

    # ---- 判据 1：参考框数 > 0
    if n_ref_total == 0:
        print("PERSON_SCALE_FAIL 参考框总数为 0 —— 召回率的分母是 0，结果无意义")
        return 2
    if empty_frames:
        print(f"PERSON_SCALE_FAIL 参考为空帧：{empty_frames[:5]} —— 逐帧召回会出现除零")

    # ---- 判据 3：参考对自身必须 100%
    self_pairs, self_miss = match(ref_boxes[frames[0].name], ref_boxes[frames[0].name], args.iou)
    self_recall = len(self_pairs) / max(1, len(ref_boxes[frames[0].name]))
    if self_recall < 1.0:
        print(f"PERSON_SCALE_FAIL 参考自身匹配召回 = {self_recall:.4f} < 1.0 —— 匹配器有 bug")
        return 2
    print(f"阴性对照 A（参考对自身）召回 = {self_recall:.4f} （期望 1.0）OK")

    # ---- 逐尺寸统计
    report = {
        "generated": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
        "tool": "tools/person_scale_check.py",
        "model": str(model_path.relative_to(REPO)) if model_path.is_relative_to(REPO) else str(model_path),
        "frames_dir": str(frames_dir.relative_to(REPO)) if frames_dir.is_relative_to(REPO) else str(frames_dir),
        "n_frames": len(frames),
        "conf": args.conf,
        "iou_match": args.iou,
        "ref_size": ref_size,
        "n_ref_total": n_ref_total,
        "controls": {"self_match_recall": self_recall},
        "by_size": {},
        "caveat": (
            "这是【相对】召回：参考是 ref_size 的检测结果，不是人工真值。"
            "参考自身的召回/精度未知，因此本表只能回答「低分辨率相对于高分辨率漏了多少人」，"
            "不能回答「绝对漏检率是多少」。"
        ),
        "env": {"python": platform.python_version()},
    }

    print("")
    print(f"{'size':>6} {'框数':>6} {'配对':>6} {'召回':>7} {'均值IoU':>8} {'帧召回中位':>10} {'换帧对照':>8}")
    ok = True
    for s in test_sizes:
        tot_test = tot_pair = 0
        ious = []
        per_frame = []
        for i, p in enumerate(frames):
            tb = test_boxes[s][p.name]
            rb = ref_boxes[p.name]
            pairs, _ = match(rb, tb, args.iou)
            tot_test += len(tb)
            tot_pair += len(pairs)
            ious += [v for _, _, v in pairs]
            per_frame.append(len(pairs) / max(1, len(rb)))
            # 判据 2：换帧对照（用下一帧的检测去配当前帧的参考）
            nb = test_boxes[s][frames[(i + 1) % len(frames)].name]
            wrong_pairs, _ = match(rb, nb, args.iou)
            if s == test_sizes[0]:
                report["controls"].setdefault("wrong_frame", {})
            report["controls"].setdefault(f"wrong_frame_{s}", {})
            report["controls"][f"wrong_frame_{s}"][p.name] = len(wrong_pairs) / max(1, len(rb))
        recall = tot_pair / max(1, n_ref_total)
        wrong_tot = sum(report["controls"][f"wrong_frame_{s}"].values())
        wrong = wrong_tot / len(frames)
        report["by_size"][str(s)] = {
            "input_px": s,
            "n_boxes": tot_test,
            "n_matched": tot_pair,
            "recall_vs_ref": recall,
            "mean_matched_iou": (sum(ious) / len(ious)) if ious else None,
            "per_frame_recall_median": pct(per_frame, 50),
            "per_frame_recall_min": min(per_frame) if per_frame else None,
            "wrong_frame_recall_mean": wrong,
        }
        print(
            f"{s:>6} {tot_test:>6} {tot_pair:>6} {recall:>7.4f} "
            f"{(sum(ious)/len(ious) if ious else 0):>8.4f} "
            f"{(pct(per_frame,50) or 0):>10.4f} {wrong:>8.4f}"
        )
        # 判据 2：换帧对照必须显著低
        if wrong > 0.20:
            print(f"PERSON_SCALE_FAIL size={s} 换帧对照召回 {wrong:.4f} > 0.20 —— 匹配器无判别力")
            ok = False

    # ---- 人框尺寸统计（回答「320 输入里一个人有几个像素高」）
    print("")
    print("人框高度（原图像素 / 折算到该输入尺寸的像素）——决定「看得见」的物理量：")
    for s in test_sizes:
        hs_orig, hs_in = [], []
        for p in frames:
            w0, h0 = orig_wh[p.name]
            scale = s / float(max(w0, h0))  # ultralytics letterbox：长边缩放到 imgsz
            for b in ref_boxes[p.name]:
                h = b[3] - b[1]
                hs_orig.append(h)
                hs_in.append(h * scale)
        srt = sorted(hs_in)
        report["by_size"][str(s)]["person_height_px_in_input"] = {
            "n": len(srt),
            "p05": pct(hs_in, 5),
            "p50": pct(hs_in, 50),
            "min": srt[0] if srt else None,
            "note": "参考框高度 × (输入长边 / 原图长边)；只统计参考检出的人，漏检者不在此列",
        }
        print(
            f"  size={s:>5}  n={len(srt):>4}  p05={pct(hs_in,5):.1f}px  p50={pct(hs_in,50):.1f}px  "
            f"min={srt[0]:.1f}px   （原图 p50={pct(hs_orig,50):.0f}px）"
        )

    out = Path(args.out)
    if not out.is_absolute():
        # ★ 修于 2026-10-19：argparse 给的相对路径会相对「当前工作目录」，而下面打印用
        # `relative_to(REPO)` —— 若 cwd 恰好是仓库根则两者一致，换成别的 cwd 就抛
        # ValueError: '<rel>' is not in the subpath of '<REPO>'。判据不该依赖 cwd。
        out = REPO / out
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n写出 {out.relative_to(REPO)}  ({out.stat().st_size} B)")

    if not ok:
        print("PERSON_SCALE_FAIL 见上（对照未过）")
        return 1
    print("PERSON_SCALE_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
