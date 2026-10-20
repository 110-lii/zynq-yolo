"""hi 层「颜色先验能不能用上」的实测诊断（不是为了调阈值）。

问题从哪来
----------
`docs/player_labels_v1.md` §3 的结论是：颜色先验在 lo 层有效（lo_kept 精确率 95.7%），
但 **hi 层精确率只有 75%，而颜色完全没插手 hi 层**。所以下一步很自然是
「把颜色用到 hi 层」。在写策略之前，先量一件事：

    hi 层被判为"颜色 unknown"的框，**到底是为什么 unknown**？

因为 unknown 有两种性质完全不同的来源，而一种能救、一种不能：

  (a) too_small —— 框高 < MIN_H_PX，躯干里只有十几像素。
      **这是信息不足，不是"颜色不像任何队"**。调 MAX_COLOR_DIST / MIN_PURITY
      永远不会让它们变成 A/B。之前所有"调阈值"的想法在这一类上全是白做。

  (b) purity / rect / color —— 框够大，但躯干里的"球衣相似"像素太少，
      或连不出躯干矩形。**这一类才是"颜色在手但有歧义"**，阈值能起作用。

标注给出的线索（但这只是 6 个样本，不能当结论）
-----------------------------------------------
hi 层 6 个非球员里有 4 个的 unknown 是 dist=9.9 的取色失败，而且它们的框高
是 136.1 / 137.4 / 139.4 / 144.2 px —— **超大框反而取色失败**。如果这在全量
数据上成立，那"大框取色失败"本身就是非球员信号，颜色路线就不必去救它。

本工具就是把这条线索放在 12 段全量帧上检验。

用法
----
    & D:\\python\\python.exe tools/hi_color_diag.py
    ... --stride 8 --max-clips 3

产物
----
    data/results/hi_color_diag.json      hi 层 unknown 的原因分层 + 框高分布

判据令牌
    HI_COLOR_DIAG_OK <n_hi> <n_unknown> <pct_too_small> <pct_purity>
    HI_COLOR_DIAG_FAIL <reason>

诚实边界
--------
- 这里**没有真值**：能算出"unknown 的构成"，**算不出**"unknown 里多少是球员"。
  要回答后者必须再标一轮（见文末建议）。
- 原因分层只说明"带宽是否存在"，不说明"救回来的框对不对"。
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import importlib.util
import json
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
RESULTS = REPO / "data" / "results"
OUT = RESULTS / "hi_color_diag.json"
MODEL_DEFAULT = REPO / "data" / "models" / "yolov8n.pt"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True
    spec.loader.exec_module(mod)
    return mod


cp = _load(REPO / "tools" / "color_prior.py", "_cp_prior")

H_BANDS = ((0, 30, "0-30"), (30, 50, "30-50"), (50, 100, "50-100"),
           (100, 150, "100-150"), (150, 1e9, "150+"))


def main(argv=None):
    ap = argparse.ArgumentParser(description="hi 层颜色 unknown 原因诊断")
    ap.add_argument("--clips", default=str(REPO / "data" / "samples" / "incidents"))
    ap.add_argument("--max-clips", type=int, default=0)
    ap.add_argument("--stride", type=int, default=8)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--model", default=str(MODEL_DEFAULT))
    ap.add_argument("--palette", default="red,white")
    args = ap.parse_args(argv)

    import cv2
    from ultralytics import YOLO

    pal = {}
    names = [s.strip().lower() for s in args.palette.split(",") if s.strip()]
    if len(names) < 2:
        print("HI_COLOR_DIAG_FAIL palette_needs_2")
        return 2
    # ★ palette 传的是**颜色名字**，不是 color_of_name() 的返回值。
    #   2026-10-20 修过的"二次解析" bug（docs/color_prior_v1.md §1 第 1 条）就是
    #   把三元组又喂回解析器 —— 这里保持和 color_prior.py 完全一样的口径。
    for i, nm in enumerate(names):
        if nm not in cp.PALETTE_REF:
            print("HI_COLOR_DIAG_FAIL bad_palette %s" % nm)
            return 2
        pal["AB"[i]] = nm

    if not Path(args.model).exists():
        print("HI_COLOR_DIAG_FAIL model_missing %s" % args.model)
        return 2
    clips = sorted(Path(args.clips).glob("*.mp4"))
    if args.max_clips:
        clips = clips[:args.max_clips]
    if not clips:
        print("HI_COLOR_DIAG_FAIL no_clips")
        return 2

    model = YOLO(args.model)
    reasons = {cp.REASON_TOO_SMALL: 0, cp.REASON_PURITY: 0,
               cp.REASON_RECT: 0, cp.REASON_COLOR: 0, "colored": 0}
    n_hi = 0
    unk_h = []
    per_band = {name: {"n": 0, "unknown": 0, "too_small": 0, "purity": 0}
                for _lo, _hi, name in H_BANDS}
    t0 = time.time()

    for ci, clip in enumerate(clips, 1):
        cap = cv2.VideoCapture(str(clip))
        if not cap.isOpened():
            continue
        frame_no = -1
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frame_no += 1
            if frame_no % max(1, args.stride):
                continue
            r = model.predict(frame, classes=[0], conf=cp.HI_CONF, iou=0.45,
                              imgsz=args.imgsz, verbose=False)
            boxes = r[0].boxes if r and r[0].boxes is not None else None
            if boxes is None or len(boxes) == 0:
                continue
            for bb in boxes.xyxy.cpu().numpy():
                n_hi += 1
                h = float(bb[3] - bb[1])
                why = []
                role, _d, _p, _c = cp._classify_box(frame, bb, pal, why)
                if role in ("A", "B"):
                    reasons["colored"] += 1
                    band = next((nm for lo, hi, nm in H_BANDS if lo <= h < hi), None)
                    if band:
                        per_band[band]["n"] += 1
                    continue
                rk = why[0] if why else cp.REASON_COLOR
                reasons[rk] += 1
                unk_h.append(h)
                band = next((nm for lo, hi, nm in H_BANDS if lo <= h < hi), None)
                if band:
                    per_band[band]["n"] += 1
                    per_band[band]["unknown"] += 1
                    if rk == cp.REASON_TOO_SMALL:
                        per_band[band]["too_small"] += 1
                    elif rk == cp.REASON_PURITY:
                        per_band[band]["purity"] += 1
        cap.release()
        print("[%d/%d] %s  hi_so_far=%d unknown_so_far=%d"
              % (ci, len(clips), clip.name, n_hi, len(unk_h)), flush=True)

    n_unk = len(unk_h)
    if n_unk == 0:
        print("HI_COLOR_DIAG_FAIL no_unknown_boxes")
        return 2

    def pct(x):
        return round(100.0 * x / n_unk, 2)

    # 每段的 band 占比：大框在 unknown 里是不是被过度代表
    band_report = {}
    for name in (nm for _lo, _hi, nm in H_BANDS):
        b = per_band[name]
        band_report[name] = {
            "n": b["n"],
            "unknown": b["unknown"],
            "unknown_rate": round(b["unknown"] / b["n"], 3) if b["n"] else None,
            "too_small": b["too_small"],
            "purity": b["purity"],
        }

    arr = np.array(unk_h, dtype=float)
    out = {
        "tool": "tools/hi_color_diag.py",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "params": {"stride": args.stride, "imgsz": args.imgsz,
                   "hi_conf": cp.HI_CONF, "min_h_px": cp.MIN_H_PX,
                   "min_purity": cp.MIN_PURITY, "palette": names},
        "n_clips": len(clips),
        "elapsed_s": round(time.time() - t0, 1),
        "n_hi": n_hi,
        "n_unknown": n_unk,
        "unknown_rate": round(n_unk / n_hi, 3) if n_hi else None,
        "reason_counts": dict(reasons),
        "reason_share_pct": {k: pct(v) for k, v in reasons.items() if k != "colored"},
        "unknown_h": {"median": round(float(np.median(arr)), 1),
                      "mean": round(float(arr.mean()), 1),
                      "p90": round(float(np.percentile(arr, 90)), 1),
                      "p99": round(float(np.percentile(arr, 99)), 1),
                      "max": round(float(arr.max()), 1)},
        "by_box_height": band_report,
        "conclusion": [],
        "caveats": [
            "无真值：能算 unknown 的构成，算不出 unknown 里多少是球员",
            "原因分层只说明带宽是否存在，不说明救回来的框对不对",
        ],
    }

    # 把结论写成一句话（数字来自上面，不手填）
    share = out["reason_share_pct"]
    if share.get(cp.REASON_TOO_SMALL, 0) >= 25:
        out["conclusion"].append(
            "too_small 占 %.1f%%：这部分的 unknown 是信息不足，调颜色阈值不可能改善，"
            "只能靠降 MIN_H_PX 或提高输入分辨率" % share[cp.REASON_TOO_SMALL])
    else:
        out["conclusion"].append(
            "too_small 只占 %.1f%%：hi 层颜色 unknown 主要不是「框太小」造成的"
            % share.get(cp.REASON_TOO_SMALL, 0))
    big = band_report.get("100-150", {})
    if big.get("n"):
        out["conclusion"].append(
            "100-150 px 带：unknown 率 %s，其中 purity 失败 %d 个 —— 与标注里"
            "「大框反而取色失败」的线索对照看"
            % (big.get("unknown_rate"), big.get("purity", 0)))

    RESULTS.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1),
                   encoding="utf-8", newline="\n")
    print("RESULT_JSON %s" % OUT)
    print("HI_COLOR_DIAG_OK %d %d %.2f %.2f"
          % (n_hi, n_unk, share.get(cp.REASON_TOO_SMALL, 0.0),
             share.get(cp.REASON_PURITY, 0.0)))
    for k in ("reason_counts", "reason_share_pct", "unknown_h"):
        print("  %s = %s" % (k, json.dumps(out[k], ensure_ascii=False)))
    for k, v in band_report.items():
        print("  band %-8s n=%-5d unknown=%-5d rate=%-6s purity_fail=%d"
              % (k, v["n"], v["unknown"], v["unknown_rate"], v["purity"]))
    for c in out["conclusion"]:
        print("  ⇒ %s" % c)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())