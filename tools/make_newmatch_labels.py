"""新比赛（跨场）球员/非球员标注包：在**新场**上按取色失败原因分层抽样，出图 + 空标签 CSV。

为什么需要它
------------
`docs/cross_match_eval.md` §12.2/§12.6 已经量出「换场后取色失败率塌了约 30 pt」，
并且按场校准只补回约 1/3。**但整条链路上一个真值都没有**：
旧场的层内非球员率（purity 97.4% / rect 67.6% / color 43.6% / too_small 12.9%）
是**旧比赛**标出来的，跨场能不能搬是本次要问的问题，而不是已知结论。

要回答「门控在新场还成不成立」，必须在新场上人工标一批**同样按原因分层**的框。
本工具就是生成这一批：新比赛 → 比赛段 → 检测 → 按 reason 分层 → 每层抽 K 个出图。

与已有三批的关系
----------------
| 批 | 素材 | 采样 | 用途 |
|---|---|---|---|
| 1 | 旧场 | hi / lo_kept / lo_drop | 颜色先验 A/B（已标 73） |
| 2 | 旧场 | hi 层颜色-unknown 按 4 原因分层 | 层内非球员率（已标 159） |
| 3 | 旧场 | 批1/批2 抽 60 个**重新出图** | 标注一致性（**尚未标**） |
| **4（本工具）** | **新场** | hi 层按 5 层（4 原因 + colored） | **跨场门控是否成立**（待标） |

做法
----
1. 用 `tools/cross_match_eval.py` 的 `scan_play_windows` 找比赛段（避开广告/演播室）；
2. 在比赛段内抽窗口，逐帧检测（`classes=[0], conf=HI_CONF, imgsz=640`）；
3. 每个框交给 `color_prior._classify_box(..., why=[])` 拿原因码 —— **与跨场评估逐框等价**，
   所以这批标注能直接与 §12.2 的占比并排看；
4. 每层均匀抽 K 个（固定种子），出 320×320 单框裁剪图到 `data/annot/frames4/`；
5. 写 `data/annot/labels4.csv`（label 空）+ `data/annot/MANIFEST4.json`（来源只记这里）。

诚实边界
--------
- **抽样是分层的**：层内比例可以直接读，**总体比例不能**直接乘（各层抽样率不同）。
- 框的身份是 `(clip, frame, conf, h_px)`；`box_idx` 只是该 stratum 内的枚举下标
  （旧批那次「37 个重叠框」的错误就是拿 `box_idx` 当身份，见 `hi_layer_color_gate.md` §6.4）。
- 出图用的 pad=0.25 与旧批一致，但**新场分辨率是 854×480**，同 h_px 的框在视觉上
  比旧场（640×360 切片）更"糊"，跨场读图时别拿两批的清晰度互相比较。

用法
----
    # 建包（约 2-4 分钟，取决于 --n-windows）
    & D:\\python\\python.exe tools\\make_newmatch_labels.py --build `
        --video "D:\\yt-dlp\\downloads\\migu_968460165\\<全场 mp4>" `
        --palette white,blue --palette-refs data\\results\\kit_calib.json

    # 标（网页，键盘 p/n/u）
    & D:\\python\\python.exe tools\\label_web.py --port 8765 --batch 4

    # 标完算分层判决（标之前会明确报「未标完」而不给数字）
    & D:\\python\\python.exe tools\\make_newmatch_labels.py --score

产物
----
    data/annot/frames4/*.jpg、data/annot/labels4.csv、data/annot/MANIFEST4.json
    data/results/newmatch_labels.json（--score 的机读结果）

判据令牌
--------
    NEWLABEL_PACK_OK <n> <purity> <color> <too_small> <rect> <colored>
    NEWLABEL_SCORE_OK <n> <purity_Nrate> <color_Nrate> <too_small_Nrate> <rect_Nrate>
    NEWLABEL_FAIL <reason>          # 未标完 / 样本不足 / 打不开
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import csv
import importlib.util
import json
import math
import random
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools"
ANNOT = REPO / "data" / "annot"
FRAMES4 = ANNOT / "frames4"
LABELS4 = ANNOT / "labels4.csv"
MANIFEST4 = ANNOT / "MANIFEST4.json"
SCORE_JSON = REPO / "data" / "results" / "newmatch_labels.json"

MODEL_DEFAULT = REPO / "data" / "models" / "yolov8n.pt"

STRATA = ("purity", "color", "too_small", "rect", "colored")
# CSV 的 stratum 名与第 2 批保持同一套命名，方便同一套判读脚本迁移
STRATUM_CSV = {"purity": "hi_unk_purity", "color": "hi_unk_color",
               "too_small": "hi_unk_toosmall", "rect": "hi_unk_rect",
               "colored": "hi_colored"}
# 旧场层内非球员率（真源 data/annot/labels2.csv，见 hi_layer_color_gate.md §6.1）
OLD_LAYER_NRATE = {"purity": 0.974, "rect": 0.676, "color": 0.436, "too_small": 0.129}

SEED = 20261020
CELL = 320
PAD = 0.25


def _load(path, name):
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cp = _load(TOOLS / "color_prior.py", "_nl_color_prior")
cm = _load(TOOLS / "cross_match_eval.py", "_nl_cross_match")


def _crop(frame, bb, pad=PAD, cell=CELL):
    """单框裁剪 → 正方形 → 缩放 cell。与 make_player_labels.py 同口径（pad 0.25）。"""
    import cv2
    H, W = frame.shape[:2]
    x1, y1, x2, y2 = bb
    w, h = x2 - x1, y2 - y1
    side = max(w, h) * (1.0 + 2.0 * pad)
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    sx1, sy1 = int(max(0, cx - side / 2)), int(max(0, cy - side / 2))
    sx2, sy2 = int(min(W, cx + side / 2)), int(min(H, cy + side / 2))
    if sx2 - sx1 < 8 or sy2 - sy1 < 8:
        return None
    crop = frame[sy1:sy2, sx1:sx2]
    return cv2.resize(crop, (cell, cell), interpolation=cv2.INTER_AREA)


def _slug(video_path):
    """视频名 → 纯 ASCII 短标识（§1：文件名只允许 a-z 0-9 _ - .）。

    踩过的坑：直接拿 `Path(video).stem` 拼进文件名，遇到中文片名（migu 下载的全场
    回放就是中文）会生成 `b4_2026中超…_f77240_sb01.jpg` —— 违反命名硬约束，
    而且 CSV 的 `clip` 列会变成非 ASCII，后续按 clip 分组的脚本极易踩编码问题。
    ★ 从中文片名里只能剥出数字（实测 "2026中超第28轮…" → "2026_28_3_1"），
      可读性差 ⇒ 调用方应显式给 `--slug`；这里只在没给时兜底。
    """
    import re
    stem = Path(video_path).stem
    s = re.sub(r"[^A-Za-z0-9]+", "_", stem).strip("_")
    s = re.sub(r"_+", "_", s)[:24].strip("_")
    if not re.search(r"[A-Za-z]", s):
        return "newmatch"
    return s


def collect_candidates(video, palette, args):
    """扫比赛段 → 抽帧检测 → 给每个框打 reason 码。返回 {stratum: [cand]} + 统计。"""
    import cv2
    from ultralytics import YOLO

    windows = cm.scan_play_windows(video, step_s=args.play_step,
                                   grass_min=args.play_grass_min)
    if windows is None:
        return None, None, "play_windows_scan_failed"
    plan, err = cm.plan_clips_in_windows(windows, video, args.n_windows, args.clip_len)
    if err:
        return None, windows, err

    model = YOLO(str(args.model))
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        return None, windows, "video_unreadable"
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
    slug = (args.slug.strip() or _slug(video))
    buckets = {s: [] for s in STRATA}
    n_frames = 0
    n_boxes = 0
    try:
        for item in plan:
            f0 = int(round(item["start_s"] * fps))
            n_det = int(round(item["dur_s"] * fps))
            for k in range(0, n_det, args.stride):
                cap.set(cv2.CAP_PROP_POS_FRAMES, f0 + k)
                ok, frame = cap.read()
                if not ok:
                    continue
                n_frames += 1
                res = model.predict(frame, classes=[0], conf=cp.HI_CONF, iou=0.45,
                                    imgsz=640, verbose=False)[0]
                for i in range(len(res.boxes)):
                    bb = tuple(float(v) for v in res.boxes.xyxy[i].tolist())
                    conf = float(res.boxes.conf[i].item())
                    why = []
                    role, dist, purity, col = cp._classify_box(frame, bb, palette, why=why)
                    # ★ 分层口径必须与 color_prior.py 逐框口径**完全一致**（否则本批
                    #   标注不能与 §12.2 的占比并排看）：
                    #     role ∈ {A,B}      ⇒ "colored"
                    #     role == unknown   ⇒ why[0] 若有（too_small/rect/color(取不到)/
                    #                          purity），否则 **REASON_COLOR**（取到色但离两队都远）
                    #   踩过的坑：曾写成 `why[0] if why else "colored"`，把 4234 个
                    #   「取到色但离两队都远」的框错记成 colored，候选分布整个反过来。
                    if role in ("A", "B"):
                        stratum = "colored"
                    else:
                        stratum = why[0] if why else cp.REASON_COLOR
                    if stratum not in buckets:
                        continue
                    n_boxes += 1
                    buckets[stratum].append({
                        "clip": "%s@%07.1fs" % (slug, item["start_s"]),
                        "clip_path": str(video),
                        "frame": f0 + k,
                        "t_s": round((f0 + k) / fps, 2),
                        "conf": round(conf, 4),
                        "h_px": round(bb[3] - bb[1], 1),
                        "role": role,
                        "color_dist": (round(float(dist), 4) if dist < 9.0 else None),
                        "purity": (round(float(purity), 4) if purity else None),
                        "stratum": stratum,
                        "bb": [round(v, 1) for v in bb],
                    })
    finally:
        cap.release()
    return buckets, windows, None


def sample_and_write(buckets, args, t0):
    """每层抽 K 个 → 出图 → 写 CSV + MANIFEST。"""
    import cv2

    rng = random.Random(args.seed)
    FRAMES4.mkdir(parents=True, exist_ok=True)
    picked = []
    counts_raw = {s: len(buckets.get(s, [])) for s in STRATA}
    for s in STRATA:
        cand = buckets.get(s, [])
        if not cand:
            continue
        rng.shuffle(cand)
        picked.extend(cand[:args.per_stratum])

    rows, boxes = [], []
    by_clip_cache = {}
    for idx, c in enumerate(picked, 1):
        path = c["clip_path"]
        if path not in by_clip_cache:
            cap = cv2.VideoCapture(path)
            by_clip_cache[path] = cap
        cap = by_clip_cache[path]
        cap.set(cv2.CAP_PROP_POS_FRAMES, c["frame"])
        ok, frame = cap.read()
        if not ok:
            continue
        crop = _crop(frame, c["bb"])
        if crop is None:
            continue
        name = "b4_%s_f%04d_sb%02d.jpg" % (c["clip"].split("@")[0].replace(".", "_"),
                                          c["frame"], idx)
        cv2.imwrite(str(FRAMES4 / name), crop)
        rows.append({"id": idx, "stratum": STRATUM_CSV[c["stratum"]],
                     "clip": c["clip"], "frame": c["frame"], "box_idx": idx,
                     "conf": "%.4f" % c["conf"], "h_px": "%.1f" % c["h_px"],
                     "color_role": c["role"],
                     "color_dist": ("" if c["color_dist"] is None else "%.4f" % c["color_dist"]),
                     "img": "data/annot/frames4/%s" % name, "label": "", "note": ""})
        boxes.append({"id": idx, "stratum": c["stratum"], "clip": c["clip"],
                      "frame": c["frame"], "t_s": c["t_s"], "conf": c["conf"],
                      "h_px": c["h_px"], "purity": c["purity"],
                      "bb_xyxy": c["bb"], "img": "data/annot/frames4/%s" % name})
    for cap in by_clip_cache.values():
        cap.release()

    if not rows:
        return None, "no_crop_written"

    fields = ["id", "stratum", "clip", "frame", "box_idx", "conf", "h_px",
              "color_role", "color_dist", "img", "label", "note"]
    with LABELS4.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    counts = {}
    for r in rows:
        counts[r["stratum"]] = counts.get(r["stratum"], 0) + 1
    MANIFEST4.write_text(json.dumps({
        "tool": "tools/make_newmatch_labels.py --build",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "purpose": "跨场标注：新比赛按取色失败原因分层抽样，检验旧场层内非球员率能否搬过来",
        "label_scheme": {"P": "球员（含守门员）",
                         "N": "非球员（裁判/教练/替补/观众/球童/摄像师）",
                         "U": "看不清"},
        "sampling": {"per_stratum": args.per_stratum, "seed": args.seed,
                     "n_windows": args.n_windows, "clip_len": args.clip_len,
                     "stride": args.stride, "pad": PAD, "cell": CELL,
                     "det_conf": cp.HI_CONF, "det_imgsz": 640},
        "n_candidates_total": sum(counts_raw.values()),
        "n_candidates_by_stratum": counts_raw,
        "counts": counts,
        "n_boxes": len(rows),
        "identity_key": "(clip, frame, conf, h_px) —— box_idx 只是本批枚举下标，不是框身份",
        "boxes": boxes,
    }, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    return {"n": len(rows), "counts": counts, "raw": counts_raw}, None


def wilson(k, n, z=1.96):
    if n == 0:
        return None
    p = k / float(n)
    d = 1.0 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return [round((c - r) / d, 4), round((c + r) / d, 4)]


def score_pack():
    """标完后的分层判决：每层 N 率 + Wilson CI，并与旧场层内 N 率并排。"""
    if not LABELS4.exists():
        return None, "labels4_missing（先 --build 再标）"
    rows = list(csv.DictReader(LABELS4.open(encoding="utf-8")))
    valid = {"P", "N", "U"}
    labeled = [r for r in rows if (r.get("label") or "").strip() in valid]
    if len(labeled) < len(rows) or not rows:
        return None, "not_all_labeled %d/%d（未标完不给数字，docs/repo_spec.md §5）" % (
            len(labeled), len(rows))
    inv = {v: k for k, v in STRATUM_CSV.items()}
    out = {"tool": "tools/make_newmatch_labels.py --score",
           "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "n_labeled": len(labeled), "layers": {}, "thresholds": {"z": 1.96}}
    for key in STRATA:
        s = [r for r in labeled if r["stratum"] == inv[key]]
        n = len(s)
        nP = sum(1 for r in s if r["label"] == "P")
        nN = sum(1 for r in s if r["label"] == "N")
        nU = sum(1 for r in s if r["label"] == "U")
        denom = nP + nN
        out["layers"][key] = {
            "n_labeled": n, "n_P": nP, "n_N": nN, "n_U": nU,
            "N_rate_pn": (round(nN / denom, 4) if denom else None),
            "N_rate_pn_ci95": wilson(nN, denom),
            "old_match_N_rate": OLD_LAYER_NRATE.get(key),
        }
    # 判据：只看「层间相对高低」是否保序（旧场 purity > rect > color > too_small）
    order_old = [k for k in sorted(OLD_LAYER_NRATE, key=lambda k: -OLD_LAYER_NRATE[k])]
    got = [(k, out["layers"][k]["N_rate_pn"]) for k in order_old
           if out["layers"][k]["N_rate_pn"] is not None]
    order_got = [k for k, _v in sorted(got, key=lambda kv: -kv[1])]
    out["order_old"] = order_old
    out["order_new"] = order_got
    out["order_preserved"] = (order_got == order_old)
    SCORE_JSON.parent.mkdir(parents=True, exist_ok=True)
    SCORE_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=1),
                          encoding="utf-8", newline="\n")
    return out, None


def main(argv=None):
    ap = argparse.ArgumentParser(description="新比赛（跨场）标注包：按取色失败原因分层抽样")
    ap.add_argument("--build", action="store_true", help="建包（出图 + 空 CSV + MANIFEST）")
    ap.add_argument("--score", action="store_true", help="标完后算分层判决")
    ap.add_argument("--video", default="", help="新比赛视频（整场 mp4）")
    ap.add_argument("--palette", default="white,blue", help="双方队服颜色名（人工录入）")
    ap.add_argument("--palette-refs", default="", help="kit_calib.json（按场校准参考色）")
    ap.add_argument("--n-windows", type=int, default=12)
    ap.add_argument("--clip-len", type=float, default=20.0)
    ap.add_argument("--stride", type=int, default=12, help="每 N 帧取 1 帧（默认 12）")
    ap.add_argument("--per-stratum", type=int, default=20, help="每层抽几个（默认 20 ⇒ 共 100）")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--slug", default="",
                    help="文件名/CSV clip 列的 ASCII 标识（默认从视频名剥，剥不出字母就用 "
                         "newmatch）；中文片名建议显式给，如 --slug migu968460165")
    ap.add_argument("--play-step", type=float, default=cm.PLAY_STEP_S)
    ap.add_argument("--play-grass-min", type=float, default=cm.PLAY_GRASS_MIN)
    ap.add_argument("--model", default=str(MODEL_DEFAULT))
    args = ap.parse_args(argv)

    if args.score and not args.build:
        out, err = score_pack()
        if err:
            print("NEWLABEL_FAIL %s" % err)
            return 2
        print("分层判决（新场 vs 旧场层内非球员率）：")
        for k in STRATA:
            L = out["layers"][k]
            print("  %-10s n=%-3d P/N/U=%d/%d/%d  N率(P/N)=%s  CI95=%s  旧场=%s"
                  % (k, L["n_labeled"], L["n_P"], L["n_N"], L["n_U"], L["N_rate_pn"],
                     L["N_rate_pn_ci95"], L["old_match_N_rate"]))
        print("  旧场排序 %s / 新场排序 %s ⇒ 保序=%s"
              % (out["order_old"], out["order_new"], out["order_preserved"]))
        print("RESULT_JSON %s" % SCORE_JSON)
        print("NEWLABEL_SCORE_OK %d %s" % (out["n_labeled"], " ".join(
            "%s=%s" % (k, out["layers"][k]["N_rate_pn"]) for k in STRATA)))
        return 0

    if not args.build:
        print("NEWLABEL_FAIL 需要 --build 或 --score")
        return 2
    if not args.video:
        print("NEWLABEL_FAIL video_missing（--build 需要 --video）")
        return 2
    video = Path(args.video)
    if not video.exists():
        print("NEWLABEL_FAIL video_missing %s" % video)
        return 2
    if not Path(args.model).exists():
        print("NEWLABEL_FAIL model_missing %s" % args.model)
        return 2
    palette, err = cm.parse_palette(args.palette)
    if err:
        print("NEWLABEL_FAIL %s" % err)
        return 2
    if args.palette_refs:
        refs, err = cm.load_measured_refs(args.palette_refs)
        if err:
            print("NEWLABEL_FAIL %s" % err)
            return 2
        missing = [n for n in palette.values() if n not in refs["refs"]]
        if missing:
            print("NEWLABEL_FAIL palette_refs_missing_colour %s" % ",".join(missing))
            return 2
        palette = {role: refs["refs"][name] for role, name in palette.items()}
        print("  按场校准参考色来自 %s" % refs["source"])

    t0 = time.time()
    buckets, windows, err = collect_candidates(video, palette, args)
    if err:
        print("NEWLABEL_FAIL %s" % err)
        return 2
    print("  比赛段 %d 段 / 合计 %.0fs（视频 %.0fs）"
          % (len(windows["intervals"]), windows["total_play_s"], windows["video_s"]))
    print("  候选框：%s" % json.dumps({k: len(v) for k, v in buckets.items()},
                                     ensure_ascii=False))
    got, err = sample_and_write(buckets, args, t0)
    if err:
        print("NEWLABEL_FAIL %s" % err)
        return 2
    print("  出图 %d 张 → %s" % (got["n"], FRAMES4))
    print("  CSV %s（label 列留空，等人工标）" % LABELS4)
    print("  MANIFEST %s（来源只记这里）" % MANIFEST4)
    print("  用时 %.1fs" % (time.time() - t0))
    print("NEWLABEL_PACK_OK %d %s" % (got["n"], " ".join(
        str(got["counts"].get(STRATUM_CSV[s], 0)) for s in STRATA)))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:                        # noqa: BLE001 - 兜底也要留令牌
        print("NEWLABEL_FAIL uncaught %s: %s" % (type(exc).__name__, exc))
        raise SystemExit(2)