"""倒地候选复核表 —— 把「候选」变成**人能一眼核对**的图，而不是靠一批未验证的阴性算精度。

为什么需要这个
--------------
2026-10-20 用户指出：「有的片段可能有倒地情况，但是我没有说明」。
这一句直接否掉了我此前的一个做法 —— 我拿 `data/samples/incidents/MANIFEST.csv` 的
notes 列当倒地真值，把「notes 没写倒地」的 8 段当阴性，算出「召回 4/4 / 精度 4/8」。
**那个精度是假的**：阴性集根本没有被标注过，「没写」不等于「没有」。
按本仓库纪律（判据必须先能失败、数字必须标来源、没有原文不登记），该数字作废。

可用的真值只有单边的：**notes 明确写了倒地的 4 段**（inc_01/04/09/10）是**阳性**，
其余 8 段是**未验证**（不是阴性）。所以本工具不产出精度，只产出**复核材料**：

    对每个候选：截「候选前 / 候选峰值 / 候选后」三帧，画框、标 track id、
    在大图上放大球员区域，并排成一张联系表（contact sheet）。
    人看一眼就能判：真倒地 / 遮挡 / 画幅裁切 / 远处小人 / 演播室字幕。

只有人把复核结果写回去（`--decide`），才算有标注；在此之前
`n_fall_*` 一律是**候选数**，不是检出数。

用法
----
    # 1) 生成复核图（默认对 strong 候选全做，weak 只做前 N 个）
    & D:\\python\\python.exe tools/fall_review.py --clips data/samples/incidents

    # 2) 人工看图后写回裁定（一行一个候选；judgement 只允许 pos/neg/unsure）
    & D:\\python\\python.exe tools/fall_review.py --decide data/results/fall_review.csv

产物
----
    data/results/fall_candidates.csv     候选清单（clip, track, 帧段, 几何量, 复核图路径）
    data/results/viz/fall/<cand_id>.jpg  每个候选一张联系表（.gitignore 排除 viz/）
    data/results/fall_review.json        人裁定汇总（**只有做了 --decide 才有**）

判据令牌
    FALL_REVIEW_DONE <n_candidates> <n_strong> <n_weak>
    FALL_REVIEW_FAIL <reason>
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import csv
import json
import time
from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[1]
RESULTS = REPO / "data" / "results"
TRACKS_DIR = RESULTS / "tracks"
VIZ_DIR = RESULTS / "viz" / "fall"
CAND_CSV = RESULTS / "fall_candidates.csv"
REVIEW_JSON = RESULTS / "fall_review.json"

PANEL_W = 260          # 联系表每格宽度
CROP_PAD = 0.35        # 放大格在框外扩多少（相对框尺寸）


def _read_clip_frames(path, want_frames):
    """只解码需要的帧（顺序读一遍，命中就留）。返回 {frame_no: frame}。"""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError("cannot open %s" % path)
    want = set(want_frames)
    out = {}
    i = -1
    while want:
        ok, frame = cap.read()
        if not ok:
            break
        i += 1
        if i in want:
            out[i] = frame.copy()
            want.discard(i)
    cap.release()
    return out


def _panel(frame, pt, label, color):
    """把一个人裁剪放大 + 画框，返回固定宽度的 panel（高度按比例）。"""
    h, w = frame.shape[:2]
    cx, cy, bw, bh = pt["cx"], pt["cy"], pt["w"], pt["h"]
    pad_w, pad_h = bw * CROP_PAD, bh * CROP_PAD
    x1 = int(max(0, cx - bw / 2 - pad_w))
    x2 = int(min(w, cx + bw / 2 + pad_w))
    y1 = int(max(0, cy - bh / 2 - pad_h))
    y2 = int(min(h, cy + bh / 2 + pad_h))
    if x2 <= x1 or y2 <= y1:
        return None
    crop = frame[y1:y2, x1:x2].copy()
    # 在 crop 里画原框
    bx1, by1 = int(cx - bw / 2 - x1), int(cy - bh / 2 - y1)
    bx2, by2 = int(cx + bw / 2 - x1), int(cy + bh / 2 - y1)
    cv2.rectangle(crop, (bx1, by1), (bx2, by2), color, 2)
    scale = PANEL_W / float(crop.shape[1])
    crop = cv2.resize(crop, (PANEL_W, max(1, int(crop.shape[0] * scale))))
    bar = np.zeros((22, PANEL_W, 3), np.uint8)
    cv2.putText(bar, label, (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                (255, 255, 255), 1, cv2.LINE_AA)
    return np.vstack([bar, crop])


def _contact_sheet(panels):
    """横向拼成联系表；高度取最高的 panel，其余补黑。"""
    panels = [p for p in panels if p is not None]
    if not panels:
        return None
    H = max(p.shape[0] for p in panels)
    fixed = []
    for p in panels:
        if p.shape[0] < H:
            pad = np.zeros((H - p.shape[0], p.shape[1], 3), np.uint8)
            p = np.vstack([p, pad])
        fixed.append(p)
    return np.hstack(fixed)


def build_candidates(args):
    summary_path = RESULTS / "player_tracks.json"
    if not summary_path.exists():
        raise RuntimeError("missing %s (run tools/track_players.py first)" % summary_path)
    clips = json.loads(summary_path.read_text(encoding="utf-8"))["clips"]
    wanted = {c["clip"] for c in clips}
    if args.clips:
        want_dir = Path(args.clips)
        wanted = {p.name for p in want_dir.glob("*.mp4")}

    VIZ_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for tp in sorted(TRACKS_DIR.glob("*.json")):
        r = json.loads(tp.read_text(encoding="utf-8"))
        if r["clip"] not in wanted:
            continue
        picks = []
        n_weak = 0
        for t in r["tracks"]:
            for fc in t["fall_candidates"]:
                if fc["tier"] == "weak":
                    n_weak += 1
                    if n_weak > args.max_weak:
                        continue
                picks.append((t, fc))
        if not picks:
            continue

        need = set()
        for t, fc in picks:
            for f in (fc["f_start"], (fc["f_start"] + fc["f_end"]) // 2, fc["f_end"]):
                need.add(f)
        frames = _read_clip_frames(r["clip_path"], sorted(need))
        print("  %s: %d candidates, %d frames decoded" % (r["clip"], len(picks), len(frames)),
              flush=True)

        for t, fc in picks:
            mid = (fc["f_start"] + fc["f_end"]) // 2
            pts = {p["f"]: p for p in t["points"]}
            panels = []
            for tag, f in (("before", fc["f_start"]), ("peak", mid), ("after", fc["f_end"])):
                frame = frames.get(f)
                pt = pts.get(f)
                if frame is None or pt is None:
                    panels.append(None)
                    continue
                color = (0, 0, 255) if fc["tier"] == "strong" else (0, 200, 255)
                panels.append(_panel(frame, pt, "%s f=%d h=%.0f" % (tag, f, pt["h"]), color))
            sheet = _contact_sheet(panels)
            if sheet is None:
                continue
            cid = "%s_tid%d_f%d-%d_%s" % (Path(r["clip"]).stem, t["track_id"],
                                          fc["f_start"], fc["f_end"], fc["tier"])
            outp = VIZ_DIR / ("%s.jpg" % cid)
            cv2.imwrite(str(outp), sheet, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
            rows.append({
                "cand_id": cid,
                "clip": r["clip"],
                "track_id": t["track_id"],
                "tier": fc["tier"],
                "f_start": fc["f_start"], "f_end": fc["f_end"],
                "n_frames": fc["n_frames"],
                "t_start_s": fc["t_start_s"], "t_end_s": fc["t_end_s"],
                "h_min": fc.get("h_min"), "h_base_p90": fc.get("h_base_p90"),
                "aspect_max": fc.get("aspect_max"),
                "track_len": t["n_frames"],
                "viz": str(outp.relative_to(REPO)).replace("\\", "/"),
                "judgement": "",        # 人工填：pos / neg / unsure
                "reviewer_note": "",
            })

    RESULTS.mkdir(parents=True, exist_ok=True)
    with open(CAND_CSV, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["cand_id"])
        w.writeheader()
        w.writerows(rows)
    return rows


def apply_decisions(rows):
    """把人工裁定写回 JSON，并**只用被裁定过的候选**给出单边统计。"""
    pos = [r for r in rows if r["judgement"] == "pos"]
    neg = [r for r in rows if r["judgement"] == "neg"]
    unsure = [r for r in rows if r["judgement"] == "unsure"]
    undecided = [r for r in rows if r["judgement"] == ""]
    by_clip = {}
    for r in rows:
        b = by_clip.setdefault(r["clip"], {"pos": 0, "neg": 0, "unsure": 0, "undecided": 0})
        b[r["judgement"] or "undecided"] += 1
    out = {
        "tool": "tools/fall_review.py",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "note": ("这是**人工复核**结果。n_pos 是「人确认的倒地候选数」，"
                 "**不是**召回率：没有标注过的片段不能当阴性，"
                 "所以本文件刻意不给 precision/recall。"),
        "counts": {"n_candidates": len(rows), "n_pos": len(pos), "n_neg": len(neg),
                   "n_unsure": len(unsure), "n_undecided": len(undecided)},
        "by_clip": by_clip,
        "decided": [{"cand_id": r["cand_id"], "clip": r["clip"],
                     "judgement": r["judgement"], "note": r["reviewer_note"]}
                    for r in rows if r["judgement"]],
    }
    REVIEW_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=1),
                           encoding="utf-8", newline="\n")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="倒地候选人工复核表（D011）")
    ap.add_argument("--clips", default="", help="只处理该目录下的片段")
    ap.add_argument("--max-weak", type=int, default=4,
                    help="每段最多出几张弱候选图（强候选全出）")
    ap.add_argument("--decide", default="", help="已填好 judgement 的 CSV 路径")
    args = ap.parse_args(argv)

    if args.decide:
        p = Path(args.decide)
        if not p.exists():
            print("FALL_REVIEW_FAIL csv_missing %s" % p)
            return 2
        with open(p, encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
        bad = [r for r in rows if r.get("judgement", "") not in ("", "pos", "neg", "unsure")]
        if bad:
            print("FALL_REVIEW_FAIL bad_judgement %d rows" % len(bad))
            return 2
        out = apply_decisions(rows)
        c = out["counts"]
        print("RESULT_JSON %s" % REVIEW_JSON)
        print("FALL_REVIEW_DONE %d %d %d" % (c["n_candidates"], c["n_pos"], c["n_neg"]))
        return 0

    rows = build_candidates(args)
    n_strong = sum(1 for r in rows if r["tier"] == "strong")
    print("RESULT_CSV %s" % CAND_CSV)
    print("VIZ_DIR %s" % VIZ_DIR)
    print("FALL_REVIEW_DONE %d %d %d" % (len(rows), n_strong, len(rows) - n_strong))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
