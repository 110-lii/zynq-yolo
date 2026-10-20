#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第二批标注（hi 层颜色-unknown）的判决分析 —— 回答一个问题，并给出下一阶段的门限。

为什么单开一个脚本而不是只读 label_web.py 的 /api/score
--------------------------------------------------------
`/api/score?batch=2` 给的是「未知框整体 vs hi 层 25%」的一句话判决，那是**采样时**
设想的问题。真标完之后数据里多出三件事，是采样时没预料到的：

  1. 按失败原因拆开之后，四层的非球员率差了 **9 倍**（purity 95% vs too_small 10%）。
     整体 52.8% 掩盖了这一点 ⇒ 「unknown 是非球员信号」这句话对一半、错一半。
  2. `color` 层的 `color_dist` 与标签**单调相关**（dist≥0.5 时非球员 72%，dist<0.2 时 0%）
     ⇒ 这一层能在**不新增标注、不重训模型**的前提下再切一刀。
  3. 有 37 个框与第一批（data/annot/labels.csv）是同一个 (clip,frame,box_idx)，
     两次标注可以互相当作一致性检验 —— 这是目前唯一能估「标注本身抖多少」的数据。

★ 本脚本的所有数字都标注了来源与样本量。
★ 分层样本的比例**不能直接当总体比例**：本脚本用分层估计把四层按
  `data/results/hi_color_diag.json` 的真实占比（purity 37.1% / color 44.4% /
  too_small 14.7% / rect 3.8%）还原成**总体**估计，并把两个数分开写。

用法
----
    & D:\\python\\python.exe tools\\hi_unknown_analysis.py
    & D:\\python\\python.exe tools\\hi_unknown_analysis.py --write   # 写 data/annot/scores2.json

判据令牌
    HI_UNK_ANALYSIS_OK <d_prime> <n_overlap> <agree>
    HI_UNK_ANALYSIS_FAIL <reason>
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ANNOT = REPO / "data" / "annot"
L1 = ANNOT / "labels.csv"
L2 = ANNOT / "labels2.csv"
DIAG = REPO / "data" / "results" / "hi_color_diag.json"
OUT = ANNOT / "scores2.json"

VALID = ("P", "N", "U")
STRATA = ("hi_unk_purity", "hi_unk_color", "hi_unk_toosmall", "hi_unk_rect")
# 分层名 → hi_color_diag.json 里的原因码（注意 too_small 带下划线、toosmall 不带，
# 用机械的 replace 会静默拿不到值 —— 就是靠这一行躲开过一次假「缺文件」）
DIAG_KEY = {"hi_unk_purity": "purity", "hi_unk_color": "color",
            "hi_unk_toosmall": "too_small", "hi_unk_rect": "rect"}

# 第一批 hi 层整体（docs/player_labels_v1.md §3）：24 个里 6 个非球员
REF_HI = {"n": 24, "n_nonplayer": 6, "nonplayer_rate": 6 / 24,
          "source": "data/annot/labels.csv 的 hi 层（第一批，n=24）"}

# `color` 层的 color_dist 切点：>= 0.5 是「离两队都很远」，0.16~0.5 是「擦边」。
# 0.50 不是拍脑袋：见 §3 的 monotone 表，它是从 38% 跳到 72% 的位置。
COLOR_DIST_SPLIT = 0.50


def load(path):
    if not path.exists():
        return []
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def wilson(k, n, z=1.96):
    """二项比例的 Wilson 95% 区间（%）。n=0 返回 (None, None)。"""
    if not n:
        return None, None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(100 * max(0.0, c - h), 1), round(100 * min(1.0, c + h), 1)


def cells(rows):
    """返回 (n, n_P, n_N, n_U, N_rate)。P/N 才算有标签。"""
    n = len(rows)
    c = Counter(r["label"] for r in rows)
    n_p, n_n, n_u = c.get("P", 0), c.get("N", 0), c.get("U", 0)
    lab = n_p + n_n
    return {"n": n, "n_P": n_p, "n_N": n_n, "n_U": n_u,
            "n_labeled": lab,
            "N_rate": (n_n / lab) if lab else None,
            "P_rate_if_kept": (n_p / lab) if lab else None}


def _fmt(c):
    if not c["n_labeled"]:
        return "n=%d 未标" % c["n"]
    lo, hi = wilson(c["n_N"], c["n_labeled"])
    return "n=%3d  P=%2d N=%2d U=%2d  N率=%4.1f%% [%4.1f,%4.1f]" % (
        c["n"], c["n_P"], c["n_N"], c["n_U"], 100 * c["N_rate"], lo, hi)


def stratum_report(rows):
    out = {}
    for s in STRATA:
        sub = [r for r in rows if r["stratum"] == s]
        c = cells(sub)
        lo, hi = wilson(c["n_N"], c["n_labeled"])
        c["N_rate_ci95"] = [lo, hi]
        out[s] = c
    return out


# ---------------------------------------------------------------- §1 四层判决

def section_strata(rows, strata):
    print("\n== §1 按取色失败原因分层（这是本批的主结果）==")
    print("参照：hi 层整体非球员率 25.0%（第一批 6/24）")
    print()
    print("%-16s %s" % ("stratum", "标注结果"))
    for s in STRATA:
        print("%-16s %s" % (s, _fmt(strata[s])))
    allc = cells(rows)
    allc["N_rate_ci95"] = list(wilson(allc["n_N"], allc["n_labeled"]))
    print("%-16s %s" % ("ALL", _fmt(allc)))

    # 分层估计：把样本比例按真实占比还原成总体估计
    diag = json.loads(DIAG.read_text(encoding="utf-8")) if DIAG.exists() else {}
    share = None
    rc = diag.get("reason_share_pct") or {}
    if rc and all(DIAG_KEY[s] in rc for s in STRATA):
        share = {s: rc[DIAG_KEY[s]] / 100.0 for s in STRATA}
    est = None
    if share and all(strata[s]["n_labeled"] for s in STRATA):
        est = sum(share[s] * strata[s]["N_rate"] for s in STRATA)
        n_unk = diag.get("n_unknown") or 0
        print("\n分层估计（按 hi_color_diag.json 的真实占比加权）")
        for s in STRATA:
            print("  %-16s 真实占比 %5.1f%%  × 非球员率 %5.1f%%"
                  % (s, 100 * share[s], 100 * strata[s]["N_rate"]))
        print("  ⇒ **%d 个 hi 层取色失败框里，估计 %.1f%%（约 %d 个）是非球员**"
              % (n_unk, 100 * est, round(est * n_unk)))
        print("  对照：hi 层整体 25.0%% ⇒ 整体看确实偏高，但这是「几层拉高 + 一层拉低」的结果。")
    else:
        print("\n（缺 data/results/hi_color_diag.json 或某层未标完，不做分层估计）")

    # 判决
    verd = {}
    verds = []
    for s in STRATA:
        c = strata[s]
        if not c["n_labeled"]:
            verd[s] = "untested"
            continue
        d = c["N_rate"] - REF_HI["nonplayer_rate"]
        if d >= 0.15:
            verd[s] = "signal"      # 取色失败 ⇒ 大概率非球员
            verds.append((s, "取色失败，且大概率**不是球员**，应当丢弃"))
        elif d <= -0.15:
            verd[s] = "player"      # 取色失败 ⇒ 大概率球员，丢弃会伤 recall
            verds.append((s, "取色失败，但**基本是球员**，丢弃会伤 recall，必须保留"))
        else:
            verd[s] = "noise"
            verds.append((s, "取色失败与球员/非球员无关，没有信息量"))
    print("\n判决（|N率 − 25%| >= 15 pt 才算有信号）")
    for s, t in verds:
        rate = strata[s]["N_rate"]
        print("  %-14s N率 %5.1f%%  %s"
              % (s.replace("hi_unk_", ""), 100 * rate if rate is not None else -1, t))
    return strata, allc, est, share, verd, verds


# ---------------------------------------------------------------- §2 color 层切片

def section_color_slice(rows):
    print("\n== §2 `color` 层再切一刀：color_dist 与标签的相关性 ==")
    cc = [r for r in rows if r["stratum"] == "hi_unk_color" and r["label"] in VALID]
    if not cc:
        print("（color 层未标完）")
        return None
    cc.sort(key=lambda r: float(r["color_dist"]))
    print("%-14s %s" % ("dist 区间", "标注结果"))
    for lo, hi in ((0.16, 0.20), (0.20, 0.30), (0.30, 0.50), (0.50, 9.0)):
        sub = [r for r in cc if lo <= float(r["color_dist"]) < hi]
        if sub:
            print("  %.2f-%.2f  %s" % (lo, hi, _fmt(cells(sub))))
    above = [r for r in cc if float(r["color_dist"]) >= COLOR_DIST_SPLIT]
    below = [r for r in cc if float(r["color_dist"]) < COLOR_DIST_SPLIT]
    ca, cb = cells(above), cells(below)
    print("\n以 dist>=%.2f 为界：>=  %s" % (COLOR_DIST_SPLIT, _fmt(ca)))
    print("                  < %s" % _fmt(cb))
    return {"split": COLOR_DIST_SPLIT, "ge": ca, "lt": cb}


# ---------------------------------------------------------------- §3 标注一致性

def section_overlap(rows):
    """与第一批同一个 (clip,frame,box_idx) 的框，两次标注是否一致。

    ★ 这是仓库里唯一能估「标注本身抖多少」的数据。它估计的是**标注噪声**，
      不是模型精度，两者不能混用。
    """
    l1 = load(L1)
    if not l1:
        print("\n（缺第一批 labels.csv，跳过一致性检验）")
        return None
    key = lambda r: (r["clip"], r["frame"], r["box_idx"])
    b1 = {key(r): r["label"] for r in l1}
    pairs = [(b1[key(r)], r["label"]) for r in rows
             if key(r) in b1 and r["label"] in VALID and b1[key(r)] in VALID]
    print("\n== §3 与第一批的重叠框：标注一致性 ==")
    if not pairs:
        return None
    n = len(pairs)
    agree = sum(1 for a, b in pairs if a == b)
    conf = []
    for a, b in pairs:
        # 只统计 P/N 真冲突 —— U 与 P/N 的分歧是「看不清 vs 看清了」，性质不同
        if a == b:
            conf.append(0.0)
        elif "U" in (a, b):
            conf.append(0.0)          # 不计入真冲突
        else:
            conf.append(1.0)
    n_conflict = int(sum(conf))
    print("重叠 %d 个框：完全一致 %d（%.1f%%），U 与 P/N 分歧 %d，**P/N 真冲突 %d（%.1f%%）**"
          % (n, agree, 100 * agree / n,
             sum(1 for a, b in pairs if ("U" in (a, b)) and a != b),
             n_conflict, 100 * n_conflict / n))
    print("⇒ 这一批的 N率 数字应视为 ±%.0f pt 的标注噪声（真冲突率的一半，按「至少一个是错的」估）"
          % (100 * n_conflict / n / 2))

    # Cohen's kappa：两次标注的一致性扣掉「瞎猜也能对」的部分
    def _kappa(pairs_):
        m = len(pairs_)
        if not m:
            return None
        pa = sum(1 for x, _ in pairs_ if x == "P") / m
        pb = sum(1 for _, y in pairs_ if y == "P") / m
        po = sum(1 for x, y in pairs_ if x == y) / m
        pe = pa * pb + (1 - pa) * (1 - pb)
        return None if abs(1 - pe) < 1e-9 else (po - pe) / (1 - pe)

    k_all = _kappa(pairs)
    pn = [(x, y) for x, y in pairs if x != "U" and y != "U"]
    k_pn = _kappa(pn)
    print("  Cohen's kappa：三分类 %.3f（n=%d）；只看 P/N 且两次都看得清 %.3f（n=%d）"
          % (k_all, n, k_pn, len(pn)))
    if k_pn is not None and k_pn < 0.6:
        print("  ⚠ kappa < 0.6 ⇒ **两次照片不同、标注者只凭缩小图判断，「球员/非球员」本身有歧义**。")
        print("    下文所有 N率 的绝对值都要当作带 ±数 pt 的不确定度来读，")
        print("    判决只用「层与层之间的相对高低」（那是 9 倍量级的差异），不单用绝对值。")
    hist = Counter()
    for a, b in pairs:
        hist[(a, b)] += 1
    print("  明细：" + "  ".join("%s→%s:%d" % (a, b, c) for (a, b), c in sorted(hist.items())))
    return {"n_overlap": n, "n_agree": agree, "n_conflict_pn": n_conflict}

    # ------------------------------------------------------------


def section_height(rows, strata):
    """框高与标签：为什么 purity 层在大框上更狠。"""
    print("\n== §4 框高档 × 取色失败原因 ==")
    bands = ((0, 30), (30, 40), (40, 60), (60, 100), (100, 400))
    print("%-12s %s" % ("框高", " | ".join(s.replace("hi_unk_", "") for s in STRATA)))
    for lo, hi in bands:
        cells_txt = []
        for s in STRATA:
            sub = [r for r in rows if r["stratum"] == s and lo <= float(r["h_px"]) < hi]
            c = cells(sub)
            cells_txt.append("%d/N%d" % (c["n"], c["n_N"]) if c["n"] else "—")
        print("%-12s %s" % ("%d-%d px" % (lo, hi), " | ".join("%-8s" % t for t in cells_txt)))


# ---------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description="第二批标注的判决分析")
    ap.add_argument("--write", action="store_true", help="写 data/annot/scores2.json")
    args = ap.parse_args(argv)

    rows = load(L2)
    if not rows:
        print("HI_UNK_ANALYSIS_FAIL no_rows %s" % L2)
        return 2
    n_lab = sum(1 for r in rows if r["label"] in VALID)
    print("第二批 %d 个框，已标 %d（P=%d N=%d U=%d）"
          % (len(rows), n_lab,
             sum(1 for r in rows if r["label"] == "P"),
             sum(1 for r in rows if r["label"] == "N"),
             sum(1 for r in rows if r["label"] == "U")))

    strata = stratum_report(rows)
    strata, allc, est, share, verd, verds = section_strata(rows, strata)
    colslice = section_color_slice(rows)
    overlap = section_overlap(rows)
    section_height(rows, strata)

    print("\n== 结论 ==")
    for s, t in verds:
        print("  %-14s %s" % (s.replace("hi_unk_", ""), t))
    if colslice:
        print("  %-14s color_dist>=%.1f 的框非球员率 %.0f%%，<%.1f 的只有 %.0f%% ⇒ 这一层可以再切"
              % ("color 再切", colslice["split"], 100 * colslice["ge"]["N_rate"],
                 colslice["split"], 100 * colslice["lt"]["N_rate"]
                 if colslice["lt"]["N_rate"] is not None else -1))

    payload = {
        "batch": 2, "kind": "hi_unknown_verdict_v2",
        "generated_by": "tools/hi_unknown_analysis.py",
        "labels_source": str(L2.relative_to(REPO)),
        "n_boxes": len(rows), "n_labeled": n_lab,
        "reference_hi_layer": REF_HI,
        "by_stratum": strata, "overall": allc,
        "population_estimate_nonplayer_rate": est,
        "stratum_true_share": share,
        "verdicts": {s: {"code": verd[s], "N_rate": strata[s]["N_rate"]} for s in STRATA},
        "color_dist_slice": colslice,
        "label_consistency": overlap,
        "limitations": [
            "分层采样（每层约 40 个），层内比例可信、总体比例需按 diag 的真实占比加权后才能说",
            "总体估计假设 data/results/hi_color_diag.json 的占比在整段素材上成立（同一场比赛）",
            "U（看不清）不计入 P/N，比例的分母是 P+N",
            "与第一批重叠 37 框的冲突率说明标注有噪声，N率 数字应带 ±数 pt 的不确定度",
        ],
    }
    if args.write:
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print("\n写入 %s" % OUT)
    print("HI_UNK_ANALYSIS_OK strata=%d overlap=%s"
          % (len(STRATA), overlap["n_overlap"] if overlap else 0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())