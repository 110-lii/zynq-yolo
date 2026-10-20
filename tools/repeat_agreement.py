"""重复标注一致性：同一批框、同一张图，两次标注到底有多一致。

要回答的问题
------------
`docs/hi_layer_color_gate.md` §6.4 记了一组「重复标注一致性」（37 个框、完全一致 16、
P/N 真冲突 10、kappa 0.226）。但本仓库后来复查发现**那组数字不成立**：

  · `box_idx` 不是框的身份 —— 它是 `make_player_labels.py` 里 `enumerate(items)` 的下标，
    而 `items` 是**该 stratum 在该帧内的框**；两批又用了不同的 conf 门限
    （批1=0.08，批2=0.25），于是同一个 `box_idx` 常常指向**完全不同的人**
    （实测 inc_06 f0 的 lo_drop 框 conf=0.1870 在整帧检测列表里排第 16，`box_idx` 却是 0）。
  · 两批的图还来自不同帧、不同裁剪。

所以那组数字既混进了「两次看的是不同东西」，又混进了「小图本身有歧义」，无法解释。

本工具只做一件事：**在真正同一批框上算一致性**。它认的键是
`(clip, frame, conf, h_px)` —— 不是 `box_idx`。批次3（`tools/batch3.py`）就是按这个键
重新出图的，出图与原图**逐像素相同**（60/60，见 `MANIFEST3.json` 的 `pixel_check`）。

用法
----
    & D:\\python\\python.exe tools\\repeat_agreement.py

    # 只看某一对批次 / 导出明细
    ... --pairs 1,3 2,3
    ... --json data/results/repeat_agreement.json

判据令牌
    REPEAT_AGREE_OK <kappa> <n>         三分类 kappa 达标且有足够样本
    REPEAT_AGREE_FAIL <reason>          why not

★ 仓库纪律（`docs/repo_spec.md` §5）：labels3 没标完就**必须报「未标完」**，
  不许拿已标的那几个框算出一个 kappa 冒充结论。所以：
    · 批次3 有任何一个 label 为空 ⇒ 只报 `REPEAT_AGREE_FAIL not_all_labeled`，
      并单独给出「已标子集上」的数字，且**明确标注它不是结论**。

κ 的口径
--------
用 Cohen's kappa，观察一致率 P_o 与「按两次边际分布独立时应有一致率」P_e：
    κ = (P_o - P_e) / (1 - P_e)
κ = 1 完全一致；κ = 0 与随机一致无差别；κ < 0 比随机还差。
两套口径都算：
    三分类 P/N/U      —— 含「看不清」，反映整体判断稳定性
    只看 P/N（丢掉 U）—— 反映「真去区分球员/非球员」时稳不稳
⚠️ 只看 P/N 的那套**有选择偏差**：把 U 丢掉等于假设「标 U 的那几次也是随机丢的」，
   而实际上 U 往往集中在本身模糊的框上。两套口径必须一起看，不能只报好看的那个。
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ANNOT = REPO / "data" / "annot"

BATCH_FILES = {
    1: ANNOT / "labels.csv",
    2: ANNOT / "labels2.csv",
    3: ANNOT / "labels3.csv",
}
BATCH_DESC = {1: "第一批 73 框（分层 hi/lo_kept/lo_drop）",
              2: "第二批 159 框（hi 层颜色失败按原因分层）",
              3: "第三批 60 框（严格重复标注，同一批框同一张图）"}

VALID = ("P", "N", "U")
# 判据阈值：三分类 kappa 低于这个值就认为「标注噪声大到不能支撑精确率之类的结论」
KAPPA_MIN = 0.60
# 至少要这么多对齐样本才下结论（n 太小 κ 没有判别力）
MIN_N = 30


# --------------------------------------------------------------------------
# 读数据
# --------------------------------------------------------------------------

def load_batch(b):
    p = BATCH_FILES[b]
    if not p.exists():
        return []
    with open(p, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def ident_key(r):
    """框的身份：clip + frame + conf + h_px。

    ★ 不用 box_idx：它是「该 stratum 在该帧内的序号」，随 strat 划分和 conf 门限漂移，
      两批同一个 box_idx 常常是不同的人（详见模块 docstring）。
    conf 保留 4 位、h_px 保留 1 位，与 CSV 的存储精度一致。
    """
    return (r["clip"], int(r["frame"]),
            round(float(r["conf"]), 4), round(float(r["h_px"]), 1))


# --------------------------------------------------------------------------
# kappa
# --------------------------------------------------------------------------

def cohen_kappa(a, b, categories):
    """Cohen's kappa。a/b 等长标签序列。返回 (kappa, n, p_o, p_e)。

    有类别完全没出现时（边际为 0）它不参与求和，与常规实现一致。
    """
    n = len(a)
    if n == 0:
        return None, 0, None, None
    idx = {c: i for i, c in enumerate(categories)}
    k = len(categories)
    mat = [[0] * k for _ in range(k)]
    for x, y in zip(a, b):
        mat[idx[x]][idx[y]] += 1
    p_o = sum(mat[i][i] for i in range(k)) / n
    rows = [sum(mat[i]) for i in range(k)]
    cols = [sum(mat[i][j] for i in range(k)) for j in range(k)]
    p_e = sum(rows[i] * cols[i] for i in range(k)) / (n * n)
    if abs(1.0 - p_e) < 1e-12:
        # 两边都只用了一个类别 —— 一致率是 1 但不是「学到了什么」，κ 无定义
        return None, n, p_o, p_e
    return (p_o - p_e) / (1.0 - p_e), n, p_o, p_e


def confusion(a, b, categories):
    """原始列联表，便于人工复核每一个冲突。"""
    out = {}
    for x in categories:
        out[x] = {y: 0 for y in categories}
    for x, y in zip(a, b):
        out[x][y] += 1
    return out


# --------------------------------------------------------------------------
# 对齐
# --------------------------------------------------------------------------

def align(rows_x, rows_y):
    """按身份键对齐两批。返回 (pairs, x_only, y_only)。

    pairs: [(key, row_x, row_y)]，只保留**两边都标了**的（P/N/U）。
    """
    def index(rows):
        d = defaultdict(list)
        for r in rows:
            d[ident_key(r)].append(r)
        return d

    ix, iy = index(rows_x), index(rows_y)
    pairs, x_only, y_only = [], [], []
    for k in ix:
        if k in iy:
            # 同键多行（理论上不该有）：按顺序两两配，多出来的算单边
            a, b = ix[k], iy[k]
            m = min(len(a), len(b))
            for i in range(m):
                pairs.append((k, a[i], b[i]))
            x_only.extend(a[m:])
        else:
            x_only.extend(ix[k])
    for k in iy:
        if k not in ix:
            y_only.extend(iy[k])
    both = [(k, a, b) for k, a, b in pairs
            if a["label"] in VALID and b["label"] in VALID]
    return both, x_only, y_only


def stats(pairs):
    """一对批次的对齐统计。pairs 已保证两边都标了。"""
    a = [x[1]["label"] for x in pairs]
    b = [x[2]["label"] for x in pairs]

    k3, n3, po3, pe3 = cohen_kappa(a, b, VALID)
    sel = [(x, y) for x, y in zip(a, b) if x in ("P", "N") and y in ("P", "N")]
    ap = [x for x, _ in sel]
    bp = [y for _, y in sel]
    k2, n2, po2, pe2 = cohen_kappa(ap, bp, ("P", "N"))

    n_all = len(pairs)
    n_exact = sum(1 for x, y in zip(a, b) if x == y)
    # P/N 真冲突：一次说球员、一次说非球员（U 不算冲突，那是"看不清"）
    pn_conf = sum(1 for x, y in zip(a, b)
                  if {x, y} == {"P", "N"})
    u_dis = sum(1 for x, y in zip(a, b) if (x == "U") != (y == "U"))

    return {
        "n_aligned": n_all,
        "n_exact": n_exact,
        "exact_rate": (n_exact / n_all) if n_all else None,
        "kappa3": k3, "n3": n3, "p_o3": po3, "p_e3": pe3,
        "kappa_pn": k2, "n_pn": n2, "p_o_pn": po2, "p_e_pn": pe2,
        "n_pn_conflict": pn_conf,
        "pn_conflict_rate": (pn_conf / n_all) if n_all else None,
        "n_u_disagree": u_dis,
        "u_disagree_rate": (u_dis / n_all) if n_all else None,
        "confusion_3way": confusion(a, b, VALID),
        "confusion_pn": confusion(ap, bp, ("P", "N")),
        "marginals": {"first": dict(Counter(a)), "second": dict(Counter(b))},
        "rows": [{"clip": k[0], "frame": k[1], "conf": k[2], "h_px": k[3],
                  "a": x["label"], "b": y["label"],
                  "stratum_a": x.get("stratum", ""), "stratum_b": y.get("stratum", ""),
                  "img_a": x.get("img", ""), "img_b": y.get("img", "")}
                 for (k, x, y) in pairs],
    }


def by_stratum(pairs, which):
    """按某一侧的 stratum 拆开，看是不是某一类框特别飘。"""
    groups = defaultdict(list)
    for p in pairs:
        groups[p[1 if which == "first" else 2].get("stratum", "?")].append(p)
    out = {}
    for s, ps in sorted(groups.items()):
        a = [x[1]["label"] for x in ps]
        b = [x[2]["label"] for x in ps]
        k, n, _po, _pe = cohen_kappa(a, b, VALID)
        out[s] = {
            "n": n, "kappa3": k,
            "exact_rate": (sum(1 for x, y in zip(a, b) if x == y) / n) if n else None,
            "pn_conflict_rate": (sum(1 for x, y in zip(a, b) if {x, y} == {"P", "N"}) / n)
                                if n else None,
            "n_pn_conflict": sum(1 for x, y in zip(a, b) if {x, y} == {"P", "N"}),
        }
    return out


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description="重复标注一致性（Cohen's kappa + 冲突率）")
    ap.add_argument("--pairs", nargs="*", default=None,
                    help="要算的批次对，如 1,3 2,3；默认 1,3 与 2,3（存在才算）")
    ap.add_argument("--json", default=None, help="把明细写进这个 JSON，便于复核")
    args = ap.parse_args(argv)

    data = {b: load_batch(b) for b in BATCH_FILES}

    print("== 三批概况 ==")
    for b, rows in data.items():
        if not rows:
            print("  batch %d: 文件不存在 (%s)" % (b, BATCH_FILES[b]))
            continue
        lab = [r["label"] for r in rows if r["label"] in VALID]
        print("  batch %d: %-40s %3d 框，已标 %3d" % (b, BATCH_DESC[b], len(rows), len(lab)))

    # ★ 仓库纪律：批次3 未标完就明确报「未标完」，不给假结论
    b3 = data.get(3) or []
    b3_unlabeled = [r for r in b3 if r["label"] not in VALID]
    b3_done = bool(b3) and not b3_unlabeled
    if not b3:
        print()
        print("REPEAT_AGREE_FAIL batch3_missing")
        print("  批次3 还没生成或文件不存在：%s" % BATCH_FILES[3])
        print("  生成：python tools\\batch3.py")
        return 1
    if not b3_done:
        print()
        print("REPEAT_AGREE_FAIL not_all_labeled")
        print("  批次3 **未标完**：%d/%d 框还没标（这是个中间状态，不是结论）。"
              % (len(b3_unlabeled), len(b3)))
        print("  标完再算。标注入口：python tools\\label_web.py --batch 3"
              " → http://127.0.0.1:8765/?batch=3")
        print("  下面只打印「已标子集」的参考数字，**不能当作结论**。")

    # 默认只算涉及批次3 的对；1,2 的对已知口径不可靠，但保留可选
    wanted = args.pairs or ["1,3", "2,3"]
    out = {"batches": {b: {"n": len(rows),
                           "n_labeled": sum(1 for r in rows if r["label"] in VALID)}
                       for b, rows in data.items()},
           "b3_complete": b3_done,
           "b3_unlabeled": len(b3_unlabeled),
           "pairs": {}}

    any_ok = False
    for spec in wanted:
        try:
            x, y = [int(t) for t in spec.split(",")]
        except ValueError:
            print("跳过无法解析的批次对 %r" % spec)
            continue
        if not data.get(x) or not data.get(y):
            print("跳过 %d,%d：批次文件不存在" % (x, y))
            continue
        pairs, x_only, y_only = align(data[x], data[y])
        st = stats(pairs)
        st["n_batch_x"] = len(data[x])
        st["n_batch_y"] = len(data[y])
        st["n_x_only"] = len(x_only)
        st["n_y_only"] = len(y_only)
        st["by_stratum_first"] = by_stratum(pairs, "first")
        st["by_stratum_second"] = by_stratum(pairs, "second")
        st["x"], st["y"] = x, y
        out["pairs"]["%d,%d" % (x, y)] = st

        print()
        print("== 批次 %d vs %d ==" % (x, y))
        print("  两批各 %d / %d 框，对齐上 %d 个（两边都标了）；"
              "只在 %d 里 %d 个，只在 %d 里 %d 个"
              % (len(data[x]), len(data[y]), st["n_aligned"],
                 x, st["n_x_only"], y, st["n_y_only"]))
        if st["n_aligned"] == 0:
            print("  没有可对齐的框 —— 这两个批次不重叠，算不出一致性。")
            continue
        print("  完全一致 %d/%d = %.1f%%"
              % (st["n_exact"], st["n_aligned"], 100 * st["exact_rate"]))
        if st["kappa3"] is None:
            print("  三分类 kappa: 无定义（两边都只用了一个类别）")
        else:
            print("  三分类 kappa(P/N/U) = %.4f  (n=%d, P_o=%.4f, P_e=%.4f)"
                  % (st["kappa3"], st["n3"], st["p_o3"], st["p_e3"]))
        if st["kappa_pn"] is None:
            print("  仅 P/N kappa: 无定义或样本不足 (n=%d)" % st["n_pn"])
        else:
            print("  仅 P/N kappa     = %.4f  (n=%d, P_o=%.4f, P_e=%.4f)"
                  % (st["kappa_pn"], st["n_pn"], st["p_o_pn"], st["p_e_pn"]))
        print("  P/N 真冲突 %d/%d = %.1f%%；U 分歧 %d/%d = %.1f%%"
              % (st["n_pn_conflict"], st["n_aligned"], 100 * st["pn_conflict_rate"],
                 st["n_u_disagree"], st["n_aligned"], 100 * st["u_disagree_rate"]))

        print("  三分类列联表（行=批次%d，列=批次%d）:" % (x, y))
        print("        " + "".join("%6s" % c for c in VALID))
        for r in VALID:
            print("    %-3s " % r + "".join("%6d" % st["confusion_3way"][r][c] for c in VALID))

        print("  按层拆开（用批次%d 的 stratum）:" % x)
        print("    %-10s %4s %8s %10s %10s" % ("stratum", "n", "kappa3", "完全一致", "P/N冲突"))
        for s, v in st["by_stratum_first"].items():
            print("    %-10s %4d %8s %9.1f%% %9.1f%%"
                  % (s, v["n"],
                     "None" if v["kappa3"] is None else "%.3f" % v["kappa3"],
                     100 * v["exact_rate"], 100 * v["pn_conflict_rate"]))

        # 冲突明细，方便人工复核
        conf_rows = [r for r in st["rows"] if {r["a"], r["b"]} == {"P", "N"}]
        if conf_rows:
            print("  P/N 冲突明细（逐个可查图）:")
            for r in conf_rows[:20]:
                print("    %-20s f%-4d conf=%-7s h=%-6s  %s→%s   %s | %s"
                      % (Path(r["clip"]).stem, r["frame"], r["conf"], r["h_px"],
                         r["a"], r["b"], Path(r["img_a"]).name, Path(r["img_b"]).name))
            if len(conf_rows) > 20:
                print("    … 另有 %d 条，见 --json 明细" % (len(conf_rows) - 20))

        if x == 1 and y == 3 or x == 2 and y == 3:
            if b3_done and st["n_aligned"] >= MIN_N and st["kappa3"] is not None:
                if st["kappa3"] >= KAPPA_MIN:
                    any_ok = True

    # ---- 判据 ----
    print()
    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8",
                     newline="\n")
        print("REPEAT_AGREE_JSON %s" % p)

    if not b3_done:
        print("REPEAT_AGREE_FAIL not_all_labeled")
        print("  批次3 未标完（%d/%d 未标）—— 不做判决。" % (len(b3_unlabeled), len(b3)))
        print("  已标子集上的 kappa 只能做参考，不能写进报告当结论。")
        return 1

    # 用 1,3 作为主判据（批1 是分层大样本，覆盖面最广）
    primary = out["pairs"].get("1,3") or out["pairs"].get("2,3")
    if not primary:
        print("REPEAT_AGREE_FAIL no_usable_pair")
        return 1
    if primary["n_aligned"] < MIN_N:
        print("REPEAT_AGREE_FAIL too_few_aligned n=%d < %d"
              % (primary["n_aligned"], MIN_N))
        return 1
    if primary["kappa3"] is None:
        print("REPEAT_AGREE_FAIL kappa_undefined")
        return 1
    if any_ok:
        print("REPEAT_AGREE_OK %.4f %d" % (primary["kappa3"], primary["n3"]))
        return 0
    print("REPEAT_AGREE_FAIL kappa_low %.4f < %.2f (n=%d)"
          % (primary["kappa3"], KAPPA_MIN, primary["n3"]))
    print("  ⇒ 同一张图重复标的一致性没有达到 %.2f，"
          "「精确率提升了多少」这类数字仍不可信，先把口径对齐再谈指标。" % KAPPA_MIN)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())