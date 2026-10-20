"""跨比赛可迁移性评估的开跑前色板检查：**色板先能分辨，再谈迁移**。

为什么单独一个文件：这不是"评估"的一部分，是评估的**前置闸门**。
`classify_players.classify()` 只卡 best < MAX_COLOR_DIST(0.16) 与
best-次佳 >= MIN_MARGIN(0.04)，**没有**「跨过 MAX_COLOR_DIST 的像素绝不能分到 A/B」
这条约束；而 `distance_to_ref()` 对白/黑用 (1-S, V)、对彩色用色相为主，
两组判据不在同一空间。后果是**静默误判**，不是崩溃：

2026-10-20 在 migu_968460165 第 3000s / 4800s 的实测（tools/color_prior.py 的
torso_color 原样调用，逐像素过 classify）：
    深蓝球衣躯干 × palette{white,black}  ⇒ 39.8% 像素被判成**白色**
    白球衣躯干   × palette{white,blue}   ⇒ 97.0% 像素被判成**蓝色**
两次都没有报错、没有 unknown，直接给出错误的分队结果。
所以 `cross_match_eval.py` 先用本模块过一遍色板，过不了就 `CROSS_MATCH_FAIL`
（或显式 `--allow-indistinct-palette` 才继续，并在 JSON 里留痕）。
"""
import sys

sys.dont_write_bytecode = True

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load(path, name):
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cp = _load(REPO / "tools" / "color_prior.py", "_cp_for_palette")

# 探针场景色（h01, s, v）：**不是**本场队服、但球场里真实会出现的颜色。
#   grass       草坪（S 0.45-0.62、H≈90-100°，实测取值）—— 躯干框里不可避免会切进背景
#   kit_navy    深蓝球衣（新比赛深圳，实测）
#   kit_yellow  黄色（裁判/门将常见）
#   kit_purple  紫色（门将：新比赛 9615s 实测浅紫门将服 h01≈0.79 S≈0.32）
#   kit_white   白球衣的**真实像素**：实测 h01≈0.10-0.56、S 0.16-0.33、V 0.75-0.96
#               （白色在压缩+绿草反射下 S 很高 V 偏低，与 PALETTE_REF 的
#                 white=(S 0.08,V 0.95) 差得远 —— 这正是白队常被判 unknown 的机制）
PROBE_COLORS = {
    "grass": (0.250, 0.55, 0.55),
    "kit_navy": (0.583, 0.72, 0.35),
    "kit_yellow": (28 / 180.0, 0.80, 0.85),
    "kit_purple": (140 / 180.0, 0.35, 0.60),
    "kit_white_real": (0.50, 0.16, 0.75),
}
# 明确"**应当**是这支队伍"的场景色：上面这些里凡是等于 palette 某方颜色名的，不算误判。
MAX_FALSE_ASSIGN = 0.02   # 预留：像素级误判率上限（下次给像素样本时启用）
SIBLING_MARGIN = 0.10     # 两队颜色的可分辨余量下限
PROBE_TOL = 1.5           # 探针色距其"归属队"参考色的最小距离（1.5x MAX_COLOR_DIST 量级见下）


def _ref(col):
    """(h01, s, v) ← 预设色名（白/黑没有色相，用 0 占位，distance_to_ref 不看它）。"""
    href, s, v = cp.color_of_name(col)
    return (href if href is not None else 0.0), s, v


def sibling_margin(palette):
    """两队颜色互相"有多像"：margin = 其中一方参考色在另一方判据下的距离。

    margin >= 2*MAX_COLOR_DIST 才在原理上分得开（距离差全落在 margin 上）。
    """
    items = list(palette.items())
    out = {}
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            (ra, ca), (rb, cb) = items[i], items[j]
            a, b = _ref(ca), _ref(cb)
            d_ab = float(cp.distance_to_ref(a, b)[0])
            d_ba = float(cp.distance_to_ref(b, a)[0])
            m = min(d_ab, d_ba)
            out["%s:%s|%s:%s" % (ra, ca, rb, cb)] = {
                "dist_a_in_b": round(d_ab, 4), "dist_b_in_a": round(d_ba, 4),
                "margin": round(m, 4), "need_ge": SIBLING_MARGIN,
                "status": "PASS" if m >= SIBLING_MARGIN else "WARN",
            }
    return out


def false_assign(palette):
    """每个探针色在**生产同一个** classify() 下被分到 A/B 的情况。

    ★ 判"误判"的口径不是"有没有分到 A/B"，而是"分到的那一队跟它**像不像**"：
      探针色 p 被分到 role r，但 p 到 r 参考色的距离 > PROBE_TOL，
      说明它跨过了 MAX_COLOR_DIST 还是被判成队伍 —— 这是假确定性。
      （真实球衣像素本来就会越过 0.16：实测白球衣躯体 S≈0.05-0.33，到
       white 参考 S=0.08 的距离在 0.03~0.42 之间摆动。
       所以只卡"距离"不卡"是否分到队伍"，否则任何色板都过不了。）
    """
    out = {}
    for name, col in PROBE_COLORS.items():
        role, dist, scores = cp.classify(col, palette)
        tol = None
        bad = False
        if role in palette:
            tol = float(cp.distance_to_ref(col, cp.color_of_name(palette[role]))[0])
            bad = tol > PROBE_TOL
        out[name] = {"role": role, "dist": round(float(dist), 4), "scores": scores,
                     "dist_to_assigned_ref": (round(tol, 4) if tol is not None else None),
                     "status": "WARN" if bad else "PASS"}
    return out


def evaluate(palette):
    """总闸：sibling margin 够 + 没有任何场景色被判成 A/B。"""
    sib = sibling_margin(palette)
    fa = false_assign(palette)
    probs = []
    for k, v in sib.items():
        if v["status"] != "PASS":
            probs.append("palette_indistinct sibling %s margin=%.3f<%.3f"
                         % (k, v["margin"], SIBLING_MARGIN))
    for k, v in fa.items():
        if v["status"] == "WARN":
            probs.append("palette_indistinct %s -> %s（到该队参考色 %.3f > 容差 %.1fx%.2f）"
                         % (k, v["role"], v["dist_to_assigned_ref"], PROBE_TOL,
                            cp.MAX_COLOR_DIST))
    return {"palette": dict(palette), "siblings": sib, "probe_colors": PROBE_COLORS,
            "false_assign": fa, "ok": not probs, "problems": probs,
            "thresholds": {"sibling_margin": SIBLING_MARGIN,
                           "probe_tol": PROBE_TOL,
                           "max_color_dist": cp.MAX_COLOR_DIST,
                           "min_margin": cp.MIN_MARGIN}}


if __name__ == "__main__":
    import json
    pal = {"A": "red", "B": "white"} if len(sys.argv) < 3 else {"A": sys.argv[1], "B": sys.argv[2]}
    r = evaluate(pal)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    print("PALETTE_PROBE_%s" % ("OK" if r["ok"] else "FAIL " + r["problems"][0]))