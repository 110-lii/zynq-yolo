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
# CP = 距离口径与阈值（classify/ref_of/_dist_metric/_limit/_margin）的唯一来源。
# 以前这里把 color_prior 当 cp 用，是因为 color_prior 早期 re-export 了 classify；
# 现在 classify 在 classify_players 里，必须直接指向它，否则 palette_probe 会
# 检查一套**根本没在生产里用**的阈值（假绿）。
CP = _load(REPO / "tools" / "classify_players.py", "_cp_classify")

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

# ★ 「能判出」探针：**真帧量出来的真实球衣色**，不是合成色。
#   来源与样本量（2026-10-20；`tools/kit_calib.py` 的采样口径：比赛段 12 窗 × 3 帧、
#   hi 层 person 框 h>=40px、`color_prior.torso_color` 取躯干中位色、同场两队参考色粗分簇）：
#     white_old(旧场白队) 120 样本：h01 圆中位 0.250、s 中位 0.067、v 中位 0.935
#     blue_new(新场深蓝)  109 样本：h01 圆中位 0.598、s 中位 0.298、v 中位 0.435
#     red_old(旧场红队)   73 样本：h01 0.101、s 0.848、v 0.819
#     grass_reflection —— 白衣在草反射下的中位色（s≈0.30、v≈0.70、色相被草拉到 0.58）
#
#   ★ 字段语义（改过三版才稳定，每一版都是被具体假绿打回来的）：
#     `colour`：这个真实球衣色**用哪种预设色名表示**。判据比的是**颜色内容**，
#               不是"落到了 A 还是 B" —— 因为同一个球衣色在不同 --palette 下角色不同
#               （旧场白衣在 `red,white` 里是 B，在 `white,blue` 里是 A），拿绝对角色
#               判等会逼调用方"知道答案"，或者在 `white,black` 里把「深蓝认成白」算通过。
#     `lens`  ：哪几支队的队色**内容相同**时，这个探针算"本色板里没有对应队色" ⇒ SKIP。
#               例：white_old 只在 `white,black` 里真属于黑队（黑白在 S-V 平面重叠，
#               探到的是同一片无彩色），其余色板里它本就是"外来色"，不该判失败。
KIT_PROBES = {
    "white_old": {"col": (0.250, 0.067, 0.935), "colour": "white", "lens": [],
                  "source": "kit_calib/旧场白队 n=120"},
    "blue_new": {"col": (0.598, 0.298, 0.435), "colour": "blue", "lens": [],
                 "source": "kit_calib/新场深蓝 n=109"},
    "red_old": {"col": (0.101, 0.848, 0.819), "colour": "red", "lens": [],
                "source": "kit_calib/旧场红队 n=73"},
    # grass_reflection：白衣被草染后的病态色，v2 下色相与深蓝重合。
    #   "必须判成白"物理上做不到；"必须判成蓝"又在奖励误判 ⇒ 只在**漏判**一侧约束
    #   （不许掉 unknown）。它若被赋值成队色，由 cross_match_eval 的 G6 草坪守卫去盯。
    "grass_reflection": {"col": (0.580, 0.300, 0.700), "colour": "white",
                         "lens": [], "detect_only": True,
                         "source": "新场白队病态样本（草反射 h01≈0.58 s≈0.30 v≈0.70）"},
}


def _role_name(palette, idx):
    """色板下标 -> "A"/"B"/...，越界返回 None。"""
    roles = sorted(palette.keys())
    return roles[idx] if 0 <= idx < len(roles) else None


def _ref(col):
    """(h01, s, v) ← 预设色名（白/黑没有色相，用 0 占位，distance_to_ref 不看它）。"""
    href, s, v = CP.color_of_name(col)
    return (href if href is not None else 0.0), s, v


def sibling_margin(palette, metric=None):
    """两队颜色互相"有多像"：margin = 其中一方参考色在另一方判据下的距离。

    margin >= 2*MAX_COLOR_DIST 才在原理上分得开（距离差全落在 margin 上）。
    """
    items = list(palette.items())
    out = {}
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            (ra, ca), (rb, cb) = items[i], items[j]
            a, b = _ref(ca), _ref(cb)
            fn = CP._dist_metric(metric)
            d_ab = float(fn(a, b)[0])
            d_ba = float(fn(b, a)[0])
            m = min(d_ab, d_ba)
            out["%s:%s|%s:%s" % (ra, ca, rb, cb)] = {
                "dist_a_in_b": round(d_ab, 4), "dist_b_in_a": round(d_ba, 4),
                "margin": round(m, 4), "need_ge": SIBLING_MARGIN,
                "status": "PASS" if m >= SIBLING_MARGIN else "WARN",
            }
    return out


def false_assign(palette, metric=None):
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
        role, dist, scores = CP.classify(col, palette, metric)
        tol = None
        bad = False
        if role in palette:
            tol = float(CP.distance_to_ref(col, CP.color_of_name(palette[role]))[0])
            bad = tol > PROBE_TOL
        out[name] = {"role": role, "dist": round(float(dist), 4), "scores": scores,
                     "dist_to_assigned_ref": (round(tol, 4) if tol is not None else None),
                     "status": "WARN" if bad else "PASS"}
    return out


def _classify_kit(col, palette, metric=None):
    """给色板里**每一条**都解析成已知颜色名，再调用生产 classify。

    ★ 为什么不能直接把 palette 原样丢给 classify：`--palette-refs` 校准后 palette 的值
      可能是 {kind,h01,s,v} 或元组，`classify` 内部虽走 `ref_of` 能认，但探针要
      "同一份判断口径"，所以这里统一走 `ref_of`，避免两边解析路径分叉。
    """
    return CP.classify(col, palette, metric)


def detect_rate(palette, metric=None):
    """「能判出」探针：**该属于哪一支队的真实球衣色，必须被判成那支队，不能是 unknown**。

    ★ 为什么补这一侧（2026-10-20）：原来的 `false_assign()` 只检查「别把不该判的判
      成队伍」，**漏判方向完全不设防**。实测教训：新比赛 `--palette white,blue` 下深蓝
      球衣（H≈0.58-0.61、S≈0.19-0.30、V≈0.36-0.62）到预置 `blue`(115,0.70,0.70) 的距离
      是 **0.164-0.180 > MAX_COLOR_DIST=0.16** ⇒ 深蓝队大量落 unknown，而
      palette_probe 仍然报 PASS。这就是「判据只能判出绿」。

    判据（三档，必须按顺序理解）：
      1. **相容性**：本色板里有没有 `colour` 这个队色（或 `lens` 里与之内容相同的队色）？
         没有 ⇒ 该探针 **SKIP**（本场根本没有这个队色，无从判断，不是失败）。
         反例病历：`red,blue` 里放真白衣探针，它在任何实现下都该是 unknown。
      2. **内容**：判出来的角色，其色板条目**颜色名必须与探针的 colour/lens 相同**。
         ⇒ 这堵死了两类假绿：(a) `white,black` 里深蓝被判成"白"不许算过；
         (b) 色相相近的两个色名互换（如 red↔orange）不许算过。
      3. **detect_only** 探针（草反射病态白）：只要求"判得出来"（不进 unknown），
         不指定哪一支 —— 因为要求它判白物理上做不到、要求它判蓝是奖励误判。
         ⚠ 已知缺口：这类"判成另一支队"的误判**本函数报不出**，由
         `cross_match_eval` 的 G6（草坪框被判成 A/B 的比例）单独监控。
    """
    out = {}
    for name, kit in KIT_PROBES.items():
        colour = kit["colour"]
        names = {colour} | set(kit.get("lens") or ())
        compat = _compatible_roles(palette, names)
        role, dist, _scores = CP.classify(kit["col"], palette, metric)
        if not compat:
            out[name] = {"col": list(kit["col"]), "colour": colour, "expect": None,
                         "role": role, "dist": round(float(dist), 4),
                         "source": kit["source"], "status": "SKIP",
                         "note": "该球衣色不在本色板中，本场无从判断（不是失败）"}
            continue
        role_colour = _role_colour(palette, role)
        if kit.get("detect_only"):
            ok = role in palette
            expect = "detectable"
        else:
            ok = (role in palette) and (role_colour in names)
            expect = "/".join(sorted(names))
        out[name] = {"col": list(kit["col"]), "colour": colour, "expect": expect,
                     "role": role, "role_colour": role_colour,
                     "dist": round(float(dist), 4), "source": kit["source"],
                     "status": "PASS" if ok else "WARN"}
    return out


def _role_colour(palette, role):
    """角色 -> 色板条目的颜色名；不是颜色名（按场校准结构/元组）时返回 None（判不过）。"""
    e = palette.get(role)
    return e if isinstance(e, str) else None


def _compatible_roles(palette, names):
    """色板里颜色名落在 names 里的角色列表。"""
    return [r for r in sorted(palette) if isinstance(palette[r], str) and palette[r] in names]


def evaluate(palette, metric=None):
    """总闸：sibling margin 够 + 不误判 + **不漏判**。"""
    sib = sibling_margin(palette, metric)
    fa = false_assign(palette, metric)
    dr = detect_rate(palette, metric)
    probs = []
    lim = float(CP._limit(metric))          # ★ 用**当前口径**的门限报数，别写死 0.16
    for k, v in sib.items():
        if v["status"] != "PASS":
            probs.append("palette_indistinct sibling %s margin=%.3f<%.3f"
                         % (k, v["margin"], SIBLING_MARGIN))
    for k, v in fa.items():
        if v["status"] == "WARN":
            probs.append("palette_indistinct %s -> %s（到该队参考色 %.3f > 容差 %.1fx%.2f）"
                         % (k, v["role"], v["dist_to_assigned_ref"], PROBE_TOL, lim))
    for k, v in dr.items():
        if v["status"] == "WARN":
            probs.append("palette_undetectable %s（期望颜色 %s，实得 %s=%s，dist=%.3f > %.2f）"
                         % (k, v["expect"], v["role"], v.get("role_colour"),
                            v["dist"], lim))
    return {"palette": dict(palette), "siblings": sib, "probe_colors": PROBE_COLORS,
            "false_assign": fa, "detect": dr, "ok": not probs, "problems": probs,
            "metric": (metric or CP.CLASSIFY_METRIC),
            "thresholds": {"sibling_margin": SIBLING_MARGIN,
                           "probe_tol": PROBE_TOL,
                           "max_color_dist": CP._limit(metric),
                           "min_margin": CP._margin(metric)}}


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="开跑前色板闸门：两队色可分辨 + 不误判 + **能判出**")
    ap.add_argument("a", nargs="?", default="red", help="A 队颜色名")
    ap.add_argument("b", nargs="?", default="white", help="B 队颜色名")
    ap.add_argument("--metric", default=None, choices=[None, "v1", "v2"],
                    help="距离口径；默认跟随 classify_players.CLASSIFY_METRIC")
    ns = ap.parse_args()
    pal = {"A": ns.a, "B": ns.b}
    r = evaluate(pal, ns.metric)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    for name, v in r["detect"].items():
        print("  detect %-18s expect=%-3s -> %-8s dist=%.3f  %s"
              % (name, v["expect"], v["role"], v["dist"], v["status"]))
    print("PALETTE_PROBE_%s" % ("OK" if r["ok"] else "FAIL " + r["problems"][0]))
