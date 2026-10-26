"""统一的球员判定层（player gate）—— 后续球权 / 轨迹 / 队形的**唯一入口**。

为什么要有这一层（而不是继续在 color_prior.py 里加 if）
--------------------------------------------------------
D013-a 的目标是「用球衣颜色**更好地识别球员**」。两轮人工标注（`data/annot/labels.csv`
73 框、`data/annot/labels2.csv` 159 框）得到的结论不是「颜色好用/不好用」，而是：

    **颜色取不到的原因不同，信息量差 9 倍。**

| 取色失败原因 | 含义 | 第二批标注非球员率 | 该不该丢 |
|---|---|---|---|
| `purity`  | 框够大但躯干里球衣像素 < MIN_PURITY(0.30) | **97.4%**（38/39, n=40） | 丢 |
| `rect`    | 连躯干矩形都切不出来 | 67.6%（25/37, n=39） | 丢（较软） |
| `color`   | 取到色了但离两队都 >= MAX_COLOR_DIST(0.16) | 43.6%（17/39, n=40） | **不丢，降为 unknown** |
| `too_small` | 框高 < MIN_H_PX(22) | **12.9%**（4/31, n=40） | **不许丢** |

来源：`docs/hi_layer_color_gate.md` §6.1 / `data/annot/scores2.json`。

⚠️ 但这张表的**绝对值不可信**（第二批与第一批重叠的 37 框，P/N 真冲突 27%、
kappa 0.226），只有**层与层之间的相对高低**（9 倍量级）可信。所以本层的设计原则是：

  1. 规则里只用「相对高低」，不用「绝对非球员率」；
  2. 代码本身必须能**独立复算**这三组对照（`--ablate` / `--compare`），
     且对照量全部与标注无关（位置、轨迹碎片化、conf/框高分布）。

它做什么
--------
输入逐帧检测框（以及像素），输出每框：

    role     ∈ {player, staff, unknown}
               player  = 结构上像场上球员（在场地内 / 有成组规模 / 不属明显非球员）
               staff   = 结构上像场边人员（场外、或尺度远离人群、或取色失败的强证据）
               unknown = 有颜色证据缺失但结构上没有相反证据 —— **不是"没测"，是"低置信球员"**
    reason   给下游看的依据码（REASON_*，见下方常量）
    score    role 的把握，0..1
    color_role / color_dist / purity / h_px / conf / in_frame  供下游再切

`unknown` **不是丢弃**：调用方可以按 `score` 降权，但本层不替它决定。

与 `color_prior.py` 的关系
--------------------------
`color_prior.py` 是**评估/诊断脚本**（跑全量、出对照、写 json）；本文件是**判定层**，
两者共用同一套 `GateConfig` / `judge_frame` / `apply_gate`。`color_prior.py` 通过
`--gate` 打开本层，`--gate-rules too_small` 复现「too_small 也当丢弃理由」的旧行为
（旧行为 A/B 对照就是靠这条开关复算的）。

用法（脚本模式）
----------------
    # 只算门控的前后对照（不跑跟踪，快）
    & D:\\python\\python.exe tools/player_gate.py --ablate --stride 8

    # 加上轨迹碎片化对照（B 组，走自己的贪心跟踪器，口径与 track_players 对齐）
    & D:\\python\\python.exe tools/player_gate.py --ablate --track --stride 8

    # 全量、写 data/results/player_gate.json
    & D:\\python\\python.exe tools/player_gate.py --ablate --track --write

用法（import 模式，**这是主要用法**）
------------------------------------
    from player_gate import GateConfig, judge_frame, apply_gate
    cfg = GateConfig(palette={"A": "red", "B": "white"})
    res = judge_frame(frame_bgr, boxes_xyxy, confs, cfg)      # 逐帧
    for d in res:
        print(d.role, d.reason, d.score)

判据令牌
    PLAYER_GATE_OK <n_kept> <n_dropped> <metric>
    PLAYER_GATE_FAIL <reason>

★ 令牌按本仓库纪律（docs/repo_spec.md §5）认**日志令牌**，不认退出码。
★ `--write` 时若发现某组对照的结论与标注结论冲突，令牌里带 `CONFLICT`，
  文档 §4 必须照写冲突，不许挑指标。
"""

from __future__ import annotations

import sys

# 禁止 __pycache__：tools/check_repo.py 的 check d) 是**扫盘**的，不看 .gitignore。
# 必须在任何本地模块 import **之前**置位；被 importlib 加载的模块也各自置位（双保险）。
sys.dont_write_bytecode = True

import argparse
import importlib.util as _ilu
import json
import math
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[1]
RESULTS = REPO / "data" / "results"
DIAG_JSON = RESULTS / "hi_color_diag.json"
TRACKS_JSON = RESULTS / "player_tracks.json"
TRACKS_DIR = RESULTS / "tracks"
OUT_JSON = RESULTS / "player_gate.json"
MODEL_DEFAULT = REPO / "data" / "models" / "yolov8n.pt"
CLIPS_DEFAULT = REPO / "data" / "samples" / "incidents"


def _load(name, path):
    """importlib 加载仓库内模块（不写 __pycache__ 的前提：先置 dont_write_bytecode）。"""
    sys.dont_write_bytecode = True
    spec = _ilu.spec_from_file_location(name, str(path))
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_cp = _load("_cp_gate", REPO / "tools" / "classify_players.py")
_bcv = _load("_bcv_gate", REPO / "tools" / "ball_cv.py")

# 与 classify_players.py / color_prior.py 共用的同一份阈值（不许在本文件里另立一套）
PALETTE_REF = _cp.PALETTE_REF
MAX_COLOR_DIST = _cp.MAX_COLOR_DIST
MIN_MARGIN = _cp.MIN_MARGIN
MIN_H_PX = _cp.MIN_H_PX
MIN_PURITY = _cp.MIN_PURITY
color_of_name = _cp.color_of_name
classify = _cp.classify
torso_color = _cp.torso_color
torso_rect = _cp.torso_rect
distance_to_ref = _cp.distance_to_ref

HI_CONF = 0.25            # 常规检测阈值（全仓库口径）
LO_CONF = 0.08            # 补检下限
FIELD_Y_MIN = 0.35        # 画面高度 35% 以上基本是看台/广告牌（color_prior.py 的旧判据）

# ----------------------------------------------------------------------------- 原因码
REASON_TOO_SMALL = "too_small"
REASON_PURITY = "purity"
REASON_RECT = "rect"
REASON_COLOR = "color"
REASON_COLORED = "colored"        # 取到色且属某队（不是失败原因，是成功码）
FAIL_REASONS = (REASON_TOO_SMALL, REASON_PURITY, REASON_RECT, REASON_COLOR)

# 输出的 reason 里还要有"不是取色失败"的那些：结构证据 / 对照组 / 后置改造。
# ★ 这几个不是色层的原因码，不进 rules 表（rules 只决定**色层失败**怎么处理）。
R_DROP_RECT_ABSENT = "color_drop_rect_absent"      # rect 失败 + 像素上取到色 ⇒ 顺延用色
R_HULL_OK = "purity_hull_ok"                        # purity 失败 + 面积大 + 靠躯干中线 ⇒ 降级不丢
R_FAR_CROWD = "far_crowd"                           # 结构：尺度远大于群体
R_OUT_OF_FRAME = "out_of_frame"                     # 结构：出画（横向被裁）
R_NO_COLOR_PRIOR = "no_color_prior"                 # 对照组：完全不用颜色
R_GEO_RESCUE = "geo_rescue"                         # 结构守卫把"本该丢"的翻成 unknown

# ----------------------------------------------------------------------------- 规则表
# 每条原因对应的动作。**这是本层唯一的策略真源**，改策略只改这张表。
#   drop   : 判 staff（丢弃）
#   demote : 判 unknown（保留，但降权；不丢弃）
#   keep   : 判 player（不做颜色门控）
#
# ★ 默认值来自第二批标注的**层间相对高低**（purity 97.4% ≫ rect 67.6% ≫ color 43.6%
#   ≫ too_small 12.9%），不是来自任何一个绝对数字：
#     purity  → drop     （与其它层差 30 pt 以上，是唯一"压倒性"的层）
#     rect    → drop     （比 purity 低 30 pt，比 color 高 24 pt；软信号，可被结构证据抵消）
#     color   → demote   （高于 hi 层整体 25% 但离球员不远；§6.2 事后切点 0.50 不可编码）
#     too_small → keep   （它是**最像球员**的一层；丢弃会伤 recall）
#
# ★ 为什么 color 不 drop：§6.2 的 `color_dist >= 0.50 → 76.5% 非球员` 是**事后**切点，
#   而当前 MAX_COLOR_DIST=0.16 已把它切掉；如果把 0.50 写进代码，就是把标注集当成
#   调参集（过拟合）。§6.4 的冲突率 27% 也不支持更细的切法。所以先 demote，不丢。
DEFAULT_RULES = {
    REASON_PURITY: "drop",
    REASON_RECT: "drop",
    REASON_COLOR: "demote",
    REASON_TOO_SMALL: "keep",
}

# 旧的 color_prior 行为：取不到颜色就丢（too_small 也算丢弃理由）。
# 保留它是为了**复算** before/after 对照，不是推荐配置。
LEGACY_RULES = {
    REASON_PURITY: "drop",
    REASON_RECT: "drop",
    REASON_COLOR: "drop",
    REASON_TOO_SMALL: "drop",
}

SCORE_EST = {
    "player": 1.00,   # 颜色判明属某队
    "keep": 0.80,     # 规则判 keep（too_small）
    "demote": 0.55,   # 规则判 demote（color）
    "struct_override": 0.45,   # 规则想 drop，但结构证据不支持
    "drop": 0.10,
}
SCORE_UNKNOWN = 0.35

# ----------------------------------------------------------------------------- 结构证据
# 参与"群体尺度"估计的框：conf 高、不在画面最上沿、高度 >= 0.6*MIN_H_PX。
# ★ 为什么用**广场尺度**而不是固定 100 px：100 px 是从第一批 24 个 hi 框里挑出来的
#   切点（docs/player_labels_v1.md §4），换个机位/分辨率就失效。群体尺度是每段自适应的。
CROWD_MIN_N = 5            # 一帧里少于这么多人 ⇒ 不做尺度判断（远景空场时中位数没意义）
CROWD_MIN_H_FRAC = 0.6     # 参与估计的最小框高（相对 MIN_H_PX）
# ★ v2 修正：v1 用 FAR_PEOPLE_FRAC=0.60（群体中位数的 **3 倍**以上算"离人群很远"），
#   实测在近景机位（inc_05 0-600 帧）会把**场上球员**判成 staff —— 见 docs/player_gate_v1.md §4。
#   根因：近景机位里"人"= 前景球员 + 远处小框（替补/球童），中位数被小框拉低，
#   于是正常球员的尺度和中位数之比也能到 3。v2 把门槛抬到 5x 并**改成 demote**（不丢）。
FAR_PEOPLE_FRAC = 5.0
FAR_ABS_MIN_PX = 160.0     # 且绝对高度也要够大（避免小框凑够倍数）
FAR_ABS_MIN_PX_LEGACY = 100.0   # 旧切点（第一批 hi 层），**仅用于给 C 组做对照**
EDGE_MARGIN_FRAC = 0.02    # 框左右越界的容差（相对画面宽度）

# ---- v2 新增：purity 失败时的「凸包面积」二次判定 ----
# 球衣在于躯干里占多少面积（而不是"占多少像素"）。散点（号码、赞助商、草皮碎片、
# 两队交错的人堆）会让 cv2.convexHull 塌成一条线 ⇒ ratio 小 ⇒ 说明颜色是碎的。
# ★ 这个阈值**不是**从标注集调出来的：扫描值见 docs/player_gate_v1.md §5 的敏感性表，
#   在 0.45~0.75 之间结论不变（因为最终的 drop 还要再过几何守卫）。
HULL_PURITY_MIN = 0.6
HULL_MIN_AREA_FRAC = 0.25  # 面积占框比例 < 0.25 的"大框"不进凸包改造（那是整块背景）

FIELD_RULES = ("y_min", "center", "foot")


# =============================================================================
# 配置
# =============================================================================
@dataclass
class GateConfig:
    """门控配置。**所有字段都有默认值 ⇒ `GateConfig()` 就是推荐配置。**

    rules:       原因码 -> {"drop","demote","keep"}；缺省项用 DEFAULT_RULES
    palette:     role -> 队色名；空 dict 则不做颜色判定（只用结构证据）
    use_color:   False = 对照组的「无颜色先验」（等价于 use_struct=False 的颜色部分）
    use_struct:  False 时**关掉结构证据**（far_crowd / out_of_frame），
                 用来把「颜色门控」与「结构门控」拆开归因。
    use_rules:   False 时**关掉按原因门控**（所有取色失败一律 demote），
                 用来单独测量"按原因"这一刀值多少。
    """
    rules: dict = field(default_factory=dict)
    palette: dict = field(default_factory=lambda: {"A": "red", "B": "white"})
    use_color: bool = True
    use_struct: bool = True
    use_rules: bool = True
    use_hull: bool = True              # purity 失败时用凸包面积做二次判定
    hull_purity: float = HULL_PURITY_MIN
    hi_conf: float = HI_CONF
    field_rule: str = "y_min"          # y_min | center | foot
    field_y_min: float = FIELD_Y_MIN
    far_frac: float = FAR_PEOPLE_FRAC
    far_abs_px: float = FAR_ABS_MIN_PX
    far_drop: bool = False             # False = far_crowd 只 demote（v1 是 True 的等价物）
    crowd_min_n: int = CROWD_MIN_N
    crowd_min_h_frac: float = CROWD_MIN_H_FRAC
    far_abs_legacy_px: float = FAR_ABS_MIN_PX_LEGACY
    field_margin_in: float = 4.0 / 360.0     # 场地边界内缩（画面高度的 4 px）
    field_margin_out: float = 15.0 / 360.0   # 场地边界外扩（画面高度的 15 px）

    def rule_for(self, reason):
        if not self.use_rules:
            return "demote"
        return self.rules.get(reason) or DEFAULT_RULES.get(reason, "demote")

    def resolved_rules(self):
        return {r: self.rule_for(r) for r in FAIL_REASONS}

    def check(self):
        """★ 唯一入口自检：配置必须自洽，不许静默兜底。

        - rules 的键必须是已知原因码、值必须是三种动作之一（拼写错误会造成静默失效）
        - palette 的键只能是 A/B（多于两队要改 classify_players 的客观距离逻辑）
        - 距离/纯度阈值在各自合法区间内
        不合法一律 raise ValueError，由调用方决定是 FAIL 令牌还是回退默认。
        """
        bad = {}
        for r, act in (self.rules or {}).items():
            if r not in FAIL_REASONS:
                bad[r] = "未知原因码"
            elif act not in ("keep", "demote", "drop"):
                bad[r] = "未知动作 %r" % (act,)
        if bad:
            raise ValueError("GateConfig.rules 非法：%s" % bad)
        if self.use_color:
            if not self.palette:
                raise ValueError("use_color=True 但 palette 为空")
            if set(self.palette) - {"A", "B"}:
                raise ValueError("palette 键只能是 A/B，收到 %s" % sorted(self.palette))
            if self.palette.get("A") == self.palette.get("B"):
                raise ValueError("两队颜色相同：%r" % self.palette.get("A"))
        if not (0.0 < self.hull_purity <= 1.0):
            raise ValueError("hull_purity 必须在 (0,1]，收到 %r" % self.hull_purity)
        if not (0.0 <= self.field_y_min <= 1.0):
            raise ValueError("field_y_min 必须在 [0,1]，收到 %r" % self.field_y_min)
        if self.field_rule not in ("y_min", "center", "foot"):
            raise ValueError("field_rule 只能是 y_min/center/foot，收到 %r" % self.field_rule)
        if self.far_frac <= 0 or self.far_abs_px <= 0:
            raise ValueError("far_frac / far_abs_px 必须为正")
        return self


# =============================================================================
# 逐框颜色量测（reason 与 color_prior._classify_box 同口径，多返回一个 reason）
# =============================================================================
class ColorMeter:
    """把「框 -> 队色」这段量测单独拎出来，顺便缓存，避免重复算 HSV。"""

    def __init__(self, palette, use_hull=True, hull_purity=HULL_PURITY_MIN):
        self.palette = dict(palette or {})
        self.use_hull = bool(use_hull)
        # ★ 队色表只接受**颜色名字**。历史上有人把 color_of_name() 的解析结果
        #   （h,s,v 元组）塞进来，classify 会 SystemExit、又被调用处的 try 吞掉，
        #   最后静默退化成"全部判 white"（classify_players.py:266 记过这个坑）。
        #   在这里挡一次，是零成本的。
        if self.palette:
            GateConfig(palette=self.palette).check()
        self.hull_purity = float(hull_purity)
        self._cache = {}

    def measure(self, frame, bb):
        """返回 dict(role, dist, purity, hsv, reason, hull_ratio, jdist)。

        reason ∈ FAIL_REASONS ∪ {"colored"}；判不出队色时 role="unknown"、dist=best_dist。
        ★ dist=9.9 是**哨兵**，含义是"一个有效距离都没有"，不能当距离用
          （color_prior.py 里 dist_dropped 中位数 9.9 就是这个坑，见 docs/color_prior_v1.md §2）。
        ★ hull_ratio/jdist 只在 use_hull 且 purity 失败时算，其他情况是 None。

        v2 的两处改造（都不改 MIN_PURITY / MIN_H_PX 的语义）：
          ① rect 失败（连躯干矩形都切不出来）但像素上取到色 ⇒ **顺延用色签**，
             role 有队色 ⇒ 直接算取色成功（reason="colored"，evidence="rect_absent_colored"）。
             这是 v1 最大的漏判源：倒地/近景/贴边时 torso_rect 返回 None，颜色完全没用上。
          ② purity 失败 ⇒ 再算一次**凸包面积占比**；足够大且够纯 ⇒ 标记降级
             （reason="purity"，但 hull_ok=True，决策层据此不丢）。
        """
        if not self.palette:
            return {"role": "unknown", "dist": 9.9, "purity": None, "hsv": None,
                    "reason": REASON_RECT, "hull_ratio": None, "jdist": None}
        key = (round(float(bb[0]), 3), round(float(bb[1]), 3),
               round(float(bb[2]), 3), round(float(bb[3]), 3))
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        out = self._measure_uncached(frame, bb)
        if len(self._cache) > 20000:
            self._cache.clear()
        self._cache[key] = out
        return out

    def _blank(self, reason, purity=0.0, hsv=None):
        return {"role": "unknown", "dist": 9.9, "purity": purity, "hsv": hsv,
                "reason": reason, "hull_ratio": None, "jdist": None}

    def _measure_uncached(self, frame, bb):
        h = float(bb[3] - bb[1])
        if h < MIN_H_PX:
            return self._blank(REASON_TOO_SMALL)
        rect = torso_rect(bb, frame.shape)
        if rect is None:
            # ① 顺延用色：rect 切不出来 ≠ 像素里没颜色
            res = _color_by_pixels(frame, bb, self.palette)
            if res is not None:
                return res
            return self._blank(REASON_RECT)
        col = _torso_color_cached(frame, self.palette, rect)
        if col is None:
            return self._blank(REASON_RECT)
        purity = float(col[4])
        role, dist, _scores = classify(col, self.palette)
        if purity < MIN_PURITY:
            out = self._blank(REASON_PURITY, purity=purity, hsv=col)
            out["jdist"] = float(dist) if role in ("A", "B") else None
            if self.use_hull:
                hr = hull_purity_cached(frame, palette_key(self.palette), rect)
                out["hull_ratio"] = (None if hr is None else round(float(hr), 3))
                if (hr is not None and hr >= self.hull_purity
                        and role in ("A", "B")):
                    # 颜色是整块的，只是被背景"稀释"了 ⇒ 这是可用的颜色证据
                    return {"role": role, "dist": float(dist), "purity": purity,
                            "hsv": col, "reason": REASON_COLORED,
                            "hull_ratio": round(float(hr), 3), "jdist": float(dist),
                            "evidence": "purity_hull"}
            return out
        if role not in ("A", "B"):
            return {"role": "unknown", "dist": float(dist), "purity": purity,
                    "hsv": col, "reason": REASON_COLOR, "hull_ratio": None,
                    "jdist": float(dist)}
        return {"role": role, "dist": float(dist), "purity": purity,
                "hsv": col, "reason": REASON_COLORED, "hull_ratio": None,
                "jdist": float(dist)}


def palette_key(palette):
    return tuple(sorted((str(k), str(v)) for k, v in (palette or {}).items()))


# ★ 量测缓存：torso_color / hull_purity 只依赖 (帧内容中的那块矩形)，与配置无关。
#   7 个配置 × 每框都要量一次色，不缓存的话 151 ms/帧 里有 145 ms 都在重复算同一件事。
#   键里带**矩形本身**（而不只是框），所以 rect 顺延这种会改变矩形的路径不会串味。
_C_TC = {}
_C_HP = {}
_CAP = 60000


def _rect_key(rect):
    return (int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3]))


def _torso_color_cached(frame, palette, rect):
    patch = frame[int(rect[1]):int(rect[3]), int(rect[0]):int(rect[2])]
    k = (_rect_key(rect), int(patch.shape[0]), int(patch.shape[1]),
         int(patch[..., 0].sum()), int(patch[..., 1].sum()), int(patch[..., 2].sum()))
    hit = _C_TC.get(k)
    if hit is not None:
        return hit
    out = torso_color(frame, rect)
    if len(_C_TC) > _CAP:
        _C_TC.clear()
    _C_TC[k] = out
    return out


def hull_purity_cached(frame, pkey, rect):
    k = (pkey, _rect_key(rect))
    hit = _C_HP.get(k)
    if hit is not None:
        return hit
    out = hull_purity(frame, rect)
    if len(_C_HP) > _CAP:
        _C_HP.clear()
    _C_HP[k] = out
    return out


def clear_caches():
    _C_TC.clear()
    _C_HP.clear()


def hull_purity(frame, rect):
    """球衣色像素在其**凸包面积**里的占比，0..1；像素太少返回 None。

    为什么需要它（v1 的 purity 只看像素数占比，会被"球衣占 25% + 草地占 50%"骗过）：
    凸包面积对**连成一片**的球衣敏感、对**散落的**杂色不敏感 —— 同一条队伍颜色的
    球衣如果被切成 20 个碎片，凸包会塌成细线，ratio 就掉下来。
    实现在这里而不是 classify_players.py，因为它是本层引入的**新判据**，
    且 MIN_PURITY 的语义（像素占比）一个字都没改。
    """
    r = torso_rect_loose(rect)
    if r is None:
        return None
    x1, y1, x2, y2 = r
    patch = frame[y1:y2, x1:x2]
    if patch.size == 0:
        return None
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    mask = jersey_mask(hsv)
    n = int(mask.sum())
    if n < 24:
        return None
    pts = cv2.findNonZero(mask)
    if pts is None or len(pts) < 4:
        return None
    hull = cv2.convexHull(pts)
    area = float(cv2.contourArea(hull))
    if area <= 1.0:
        return 1.0
    return min(1.0, n / area)


def torso_rect_loose(rect):
    """把 torso_rect 的结果裁到画面内（含裁完后仍 >=2x2），否则 None。"""
    if rect is None:
        return None
    return rect if (rect[2] - rect[0]) >= 2 and (rect[3] - rect[1]) >= 2 else None


def jersey_mask(hsv):
    """"像球衣"的像素：饱和度够高的彩色 或 亮而低饱和的白。与 classify_players 同口径。"""
    h01 = hsv[..., 0].astype(np.float32) / 180.0
    s = hsv[..., 1].astype(np.float32) / 255.0
    v = hsv[..., 2].astype(np.float32) / 255.0
    colorful = (s >= 0.22) & (v <= 0.95)
    achroma_bright = (s < 0.22) & (v >= 0.62)
    grass = (h01 >= 60 / 360.0) & (h01 <= 150 / 360.0) & (s >= 0.22) & (s <= 0.85) & (v <= 0.92)
    return ((colorful | achroma_bright) & ~grass).astype(np.uint8)


def _color_by_pixels(frame, bb, palette):
    """rect 失败时的兜底取色：直接对整个框（内缩 10%）的球衣像素做色签。

    ★ 必须比 color_prior._classify_box 多出来的这一步，否则 v1 会漏掉：
      实测 49 个 `color_drop_rect` / 143 个 `override_drop_purity` 的框里有大量
      正常场上球员（倒地、近景、贴边）。见 docs/player_gate_v1.md §4。
    ★ 兜底**不是**放宽阈值：仍然走同一个 classify()（MAX_COLOR_DIST / MIN_MARGIN 不变），
      颜色必须明确属于某一队才算成功。
    """
    x1, y1, x2, y2 = bb
    w, h = x2 - x1, y2 - y1
    if w < 6 or h < 6:
        return None
    x1, x2 = int(x1 + 0.1 * w), int(x2 - 0.1 * w)
    y1, y2 = int(y1 + 0.1 * h), int(y2 - 0.8 * h)      # 上半身（球衣）优先
    if y2 - y1 < 4:
        y1 = int(bb[1] + 0.1 * h)
        y2 = int(bb[3] - 0.2 * h)
    H, W = frame.shape[:2]
    x1, x2 = max(0, x1), min(W, x2)
    y1, y2 = max(0, y1), min(H, y2)
    if x2 - x1 < 4 or y2 - y1 < 4:
        return None
    patch = frame[y1:y2, x1:x2]
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    mask = jersey_mask(hsv).astype(bool)
    n = int(mask.sum())
    if n < 40:
        return None
    sel = hsv[mask]
    # 众数色相（10° 一档，取最大桶），再在该桶内求均值色签
    hv = sel[:, 0].astype(np.float32) / 180.0
    bin_id = np.minimum((hv * 36).astype(np.int32), 35)
    cnt = np.bincount(bin_id, minlength=36)
    top = int(np.argmax(cnt))
    m = bin_id == top
    if int(m.sum()) < 24:
        return None
    col = (float(np.mean(hv[m])), float(np.mean(sel[m, 1]) / 255.0),
           float(np.mean(sel[m, 2]) / 255.0), int(m.sum()), 1.0)
    role, dist, _s = classify(col, palette)
    if role not in ("A", "B"):
        return None
    return {"role": role, "dist": float(dist), "purity": 1.0, "hsv": col,
            "reason": REASON_COLORED, "hull_ratio": None, "jdist": float(dist),
            "evidence": "rect_absent_colored"}


# =============================================================================
# 几何原语
# =============================================================================
def _h_of(bb):
    return float(bb[3] - bb[1])


def _cx_of(bb):
    return float((bb[0] + bb[2]) / 2.0)


def _cy_of(bb):
    return float((bb[1] + bb[3]) / 2.0)


def in_frame(bb, shape, margin_frac=EDGE_MARGIN_FRAC):
    """框是否完整落在画面内（左右越界 = 出画 = 只拍到大半个人）。"""
    H, W = shape[:2]
    m = max(1.0, margin_frac * W)
    return bool(bb[0] >= -m and bb[2] <= W + m)


def field_inside(bb, shape, cfg=None):
    """[A 组] 位置是否在场地内。三种判据，默认 y_min。

    y_min  : 框底下沿 y >= FIELD_Y_MIN*H（沿用 color_prior.py 的旧判据，可与历史数字对比）
    center : 框中心 y >= FIELD_Y_MIN*H（把"脚在场内但上半身在看台"也算场内）
    foot   : 更严，框底下沿 y >= 0.5*H
    """
    cfg = cfg or GateConfig()
    H = float(shape[0])
    rule = cfg.field_rule
    if rule == "center":
        return _cy_of(bb) / H >= cfg.field_y_min
    if rule == "foot":
        return float(bb[3]) / H >= 0.5
    return float(bb[3]) / H >= cfg.field_y_min


def band_margin(bb, shape, cfg):
    """[A 组] 框的**整个人**是否落在场地带内（含容差）。

    为什么需要它而不是只用 field_inside：`FIELD_Y_MIN=0.35` 是一条水平线，
    画面深处（靠近中线向看台）的**球场地面本身**会跑到线上方，所以 y<0.35 的框
    不一定是场外人（那个区域既有草坪也有看台/广告牌）。
    这里给 ± 容差，把"横跨边界"的框从两个极端里剔掉：
        inside_solid : 框底 >= 0.35H - margin_in    （脚一定在场内）
        outside_solid: 框底 <  0.35H - margin_out   （整个人都在边界上方）
    """
    H = float(shape[0])
    cut = cfg.field_y_min * H
    y = float(bb[3])
    if y >= cut - cfg.field_margin_in * H:
        return "inside_solid"
    if y < cut - cfg.field_margin_out * H:
        return "outside_solid"
    return "band_ambiguous"


def crowd_scale(boxes, confs, cfg):
    """群体尺度估计：本帧所有人框高的中位数。返回 (median_h, n_used, ref_band)。"""
    hs = []
    for bb, cf in zip(boxes, confs):
        h = _h_of(bb)
        if cf < cfg.hi_conf:
            continue
        if h < cfg.crowd_min_h_frac * MIN_H_PX:
            continue
        hs.append(h)
    if len(hs) < cfg.crowd_min_n:
        return None, len(hs), None
    m = float(np.median(hs))
    # ref_band 只做描述用：宣告"群体尺度在哪个 10 px 带里"
    return m, len(hs), int(math.floor(m / 10.0) * 10)


# =============================================================================
# 判定：一个框
# =============================================================================
@dataclass
class GateDecision:
    idx: int
    role: str                 # player | staff | unknown
    reason: str
    score: float
    color_role: str
    color_dist: float
    purity: float
    h_px: float
    conf: float
    in_frame: bool
    inside_field: bool
    band: str
    h_over_crowd: float       # 框高 / 群体尺度中位数（None 表示本帧样本不够）
    rule_action: str
    overridden: bool          # 规则想 drop，但结构证据把它翻成 unknown
    hull_ratio: float = None  # purity 失败时的凸包面积占比（其余情况 None）
    jdist: float = None       # "顺延"得到的队色距离（rect 失败/purity 失败时）
    evidence: str = ""        # 颜色证据的来源：torso / rect_absent_colored / purity_hull

    def as_dict(self):
        return asdict(self)


def _decide(idx, bb, cf, shape, m, crowd_h, cfg):
    """单框判定 —— 全部门控逻辑都在这里，`judge_frame` 只负责批处理。

    ★ v2 的处理顺序（顺序本身是结论的一部分，见 docs/player_gate_v1.md §3）：
        0) 颜色判明属某队              -> player（不再看几何：颜色是**正向证据**）
        1) 出画（横向被裁）            -> staff（v2 实测 0 命中，保留以防换素材）
        2) 尺度远大于群体（>5x）       -> unknown/far_crowd（v2 从 drop 降为 demote）
        3) 不用颜色（对照组）          -> unknown
        4) too_small                  -> player（规则表：keep）
        5) purity + 凸包够大           -> unknown（降级，不丢）
        6) 规则想 drop ⇒ 几何守卫      -> 场内且尺度 >=0.6*群体 ⇒ unknown（geo_rescue）
        7) 否则                        -> staff
    """
    h = _h_of(bb)
    inf = in_frame(bb, shape)
    inside = field_inside(bb, shape, cfg)
    band = band_margin(bb, shape, cfg)
    ratio = (h / crowd_h) if (crowd_h and crowd_h > 0) else None
    reason_code = m["reason"]
    ev = m.get("evidence") or ("torso" if reason_code == REASON_COLORED else "")

    def make(role, reason, score, action, overridden=False):
        return GateDecision(
            idx=idx, role=role, reason=reason, score=score,
            color_role=m["role"], color_dist=round(float(m["dist"]), 4),
            purity=(None if m["purity"] is None else round(float(m["purity"]), 3)),
            h_px=round(h, 1), conf=round(float(cf), 4),
            in_frame=inf, inside_field=bool(inside), band=band,
            h_over_crowd=(None if ratio is None else round(ratio, 3)),
            rule_action=action, overridden=overridden,
            hull_ratio=m.get("hull_ratio"), jdist=m.get("jdist"), evidence=ev)

    # ---- 0) 颜色判明属某队 ----
    if cfg.use_color and reason_code == REASON_COLORED:
        return make("player", "colored_" + m["role"], SCORE_EST["player"], "keep")

    # ---- 1) 结构证据：出画（只拍到大半个人：横向被裁）----
    if cfg.use_struct and not inf:
        return make("staff", R_OUT_OF_FRAME, SCORE_EST["drop"], "struct")

    # ---- 2) 结构证据：尺度远大于群体（近景观众/边线人员/摄像机）----
    if cfg.use_struct and ratio is not None and ratio >= cfg.far_frac \
            and h >= cfg.far_abs_px:
        if cfg.far_drop:
            return make("staff", R_FAR_CROWD, SCORE_EST["drop"], "struct",
                        overridden=False)
        return make("unknown", R_FAR_CROWD, SCORE_EST["demote"], "struct")

    # ---- 3) 对照组：完全不看颜色 ⇒ 一律当"没有颜色证据"，不按原因分 ----
    if not cfg.use_color:
        return make("unknown", R_NO_COLOR_PRIOR, SCORE_UNKNOWN, "demote")

    # ---- 5) purity 失败但凸包面积够大 ⇒ 颜色其实是整块的，只是被稀释 ----
    if (reason_code == REASON_PURITY and m.get("hull_ratio") is not None
            and m["hull_ratio"] >= cfg.hull_purity):
        return make("unknown", R_HULL_OK, SCORE_EST["demote"], "demote")

    # ---- 4) 按取色失败原因门控 ----
    action = cfg.rule_for(reason_code)
    if action == "keep":
        return make("player", "color_keep_" + reason_code, SCORE_EST["keep"], action)
    if action == "demote":
        return make("unknown", "color_demote_" + reason_code, SCORE_EST["demote"], action)

    # ---- 6) 规则想 drop：先看结构证据是否**反对**这一刀 ----
    contradicts = False
    if inside and crowd_h and h >= 0.6 * crowd_h:
        contradicts = True          # 场地内 + 尺度跟人群一致 ⇒ 像是场上球员
    if contradicts:
        return make("unknown", R_GEO_RESCUE + "_" + reason_code,
                    SCORE_EST["struct_override"], action, overridden=True)
    return make("staff", "color_drop_" + reason_code, SCORE_EST["drop"], action)


def judge_frame(frame_bgr, boxes_xyxy, confs, cfg=None, meter=None):
    """**核心入口**：一帧 -> 每框 GateDecision 列表（与输入同序）。

    frame_bgr    : HxWx3 uint8（BGR）
    boxes_xyxy   : (N,4) array-like，xyxy 像素坐标
    confs        : (N,) array-like
    cfg          : GateConfig；None = 推荐配置
    meter        : 可选 ColorMeter，跨帧复用缓存

    返回 list[GateDecision]；空检测返回 []。
    ★ 本函数**不丢框**：长度为 N 的输出与输入一一对应，调用方自己决定怎么用 role。
    """
    cfg = cfg or GateConfig()
    meter = meter or ColorMeter(cfg.palette if cfg.use_color else {})
    # ★ 只接受 (N,4) 框坐标 + (N,) conf：老式的 [(bb, cf), ...] 要在**这里**报错，
    #   不能让它滑到 np.asarray 的 inhomogeneous-shape 里报一个看不懂的 ValueError。
    if len(boxes_xyxy) != len(confs):
        raise ValueError("judge_frame: boxes 与 confs 数量不符（%d vs %d）"
                         % (len(boxes_xyxy), len(confs)))
    try:
        boxes = np.asarray(boxes_xyxy, dtype=float)
        cfs = np.asarray(confs, dtype=float)
    except (TypeError, ValueError) as e:
        raise ValueError("judge_frame: 需要 boxes (N,4) 与 confs (N,)，"
                         "不接受 [(box, conf), ...] 这种配对列表（%s）" % e) from None
    if boxes.size and (boxes.ndim != 2 or boxes.shape[1] != 4):
        raise ValueError("judge_frame: boxes 形状应为 (N,4)，收到 %s" % (boxes.shape,))
    boxes = boxes.reshape(-1, 4)
    cfs = cfs.reshape(-1)
    if boxes.shape[0] == 0:
        return []
    crowd_h, _n, _band = crowd_scale(boxes, cfs, cfg)
    out = []
    for i, (bb, cf) in enumerate(zip(boxes, cfs)):
        m = meter.measure(frame_bgr, bb)
        out.append(_decide(i, bb, cf, frame_bgr.shape, m, crowd_h, cfg))
    return out


def apply_gate(frames, cfg=None, want=("player", "unknown")):
    """**批处理入口**：frames = [(frame_bgr, boxes, confs), ...] -> (kept, dropped, all).

    kept/dropped 是按本层角色切好的 (frame_idx, box, conf) 列表，供跟踪 / 球权使用。
    ★ 默认 kept 含 unknown（unknown 是"低置信球员"，不是一个丢弃桶）。
      要"只留确定球员"就传 want=("player",)。
    """
    kept, dropped, every = [], [], []
    for fk, (frame, boxes, confs) in enumerate(frames):
        for d, bb, cf in _zip_decisions(frame, boxes, confs, cfg):
            every.append((fk, bb, cf, d))
            if d.role in want:
                kept.append((fk, bb, cf, d))
            else:
                dropped.append((fk, bb, cf, d))
    return kept, dropped, every


def _zip_decisions(frame, boxes, confs, cfg):
    dec = judge_frame(frame, boxes, confs, cfg)
    bx = np.asarray(boxes, dtype=float).reshape(-1, 4) if len(boxes) else np.zeros((0, 4))
    cf = np.asarray(confs, dtype=float).ravel() if len(confs) else np.zeros((0,))
    for d, bb, c in zip(dec, bx, cf):
        yield d, bb, float(c)


def summarize(decisions):
    """一帧/一批 decisions 的计数（便于日志与 json）。"""
    out = {"n": len(decisions), "player": 0, "staff": 0, "unknown": 0}
    by_reason = {}
    for d in decisions:
        out[d.role] = out.get(d.role, 0) + 1
        by_reason[d.reason] = by_reason.get(d.reason, 0) + 1
    out["by_reason"] = by_reason
    return out


# =============================================================================
# 跟踪器（B 组：轨迹碎片化）
# =============================================================================
# 为什么不用 ultralytics 的 ByteTrack：那一套的 **ID switch 依赖颜色外观特征**
# （`botsort.yaml` 用 ReID），颜色先验开/关会自动改变关联 —— 用它做 A/B 会把
# "关联策略变化"和"框被丢掉"混在一起。这里用一个**只看几何**的贪心跟踪器，
# 两次运行唯一的差别就是进门的框不同，归因才干净。
# ⚠️ 代价：它的**绝对**轨迹数/ID 数不能与 track_players.py 的 942 条直接比；
#    本仓库只做**相对**对照（同一跟踪器、同一批帧）。这一点写在 docs/player_gate_v1.md §5。
def _iou_matrix(a, b):
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)), dtype=float)
    x1 = np.maximum(a[:, None, 0], b[None, :, 0])
    y1 = np.maximum(a[:, None, 1], b[None, :, 1])
    x2 = np.minimum(a[:, None, 2], b[None, :, 2])
    y2 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    aa = np.maximum(1e-6, (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1]))[:, None]
    bb = np.maximum(1e-6, (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1]))[None, :]
    return inter / (aa + bb - inter)


class GeoBoxTracker:
    """纯几何贪心跟踪：把**相机运动补偿后**的落脚点 + IoU 组合成代价。

    与 track_players.py 的口径对齐：
      - 输入框 = 同一路 YOLO 检测（conf/iou/imgsz/stride 由调用方给）
      - 先用 ball_cv.estimate_motion 估帧间仿射（转播跟拍），把落脚点反变换回首帧坐标
      - min_box_px = 6（track_players.MIN_BOX_PX）
    只做匹配，不做姿态/倒地。
    """

    def __init__(self, max_age=3, min_iou=0.15, foot_gate_px=45.0, lost_gate_px=110.0):
        self.max_age = max_age
        self.min_iou = min_iou
        self.foot_gate_px = foot_gate_px
        self.lost_gate_px = lost_gate_px

    def run(self, steps):
        """steps: [(fidx, frame_bgr, boxes Nx4, confs N)]（已过滤 stride）。

        返回 tracks: list[list[dict]]，每点 dict(f, bb, conf, sx, sy)。
        """
        tracks = []
        live = []             # [(track_index, last_seen_step, kf_bb)]
        prev_small = None
        cum = np.eye(2, 3, dtype=np.float32)
        inv = np.eye(2, 3, dtype=np.float32)
        sc = 1.0

        for sidx, (fidx, frame, boxes, confs) in enumerate(steps):
            g = _bcv.prep_small(frame)
            if prev_small is not None:
                M, _kind, _cc = _bcv.estimate_motion(prev_small, g)
                Mf = M.copy()
                Mf[0, 2] /= sc
                Mf[1, 2] /= sc
                cum = _compose(Mf, cum)
            prev_small = g
            sc = 0.5      # ball_cv.prep_small 默认 scale=0.5（写死在这里，见下）
            inv = cv2.invertAffineTransform(cum)

            bx = np.asarray(boxes, dtype=float).reshape(-1, 4)
            keep = (bx[:, 3] - bx[:, 1]) >= _MIN_BOX_PX if len(bx) else np.zeros((0,), bool)
            bx = bx[keep]
            cf = np.asarray(confs, dtype=float).ravel()[keep] if len(cf := np.asarray(confs, dtype=float).ravel()) else np.zeros((0,))
            cf = np.asarray(confs, dtype=float).ravel()
            cf = cf[keep] if len(cf) else np.zeros((0,))

            feet = np.stack([(bx[:, 0] + bx[:, 2]) / 2.0, bx[:, 3]], axis=1) if len(bx) \
                else np.zeros((0, 2))
            if len(feet):
                sf = (feet @ inv[:, :2].T) + inv[:, 2]
            else:
                sf = np.zeros((0, 2))

            # --- 贪心匹配：先算代价矩阵，按"代价小优先"逐对占位 ---
            cand = []
            for ti, (ti_idx, last_s, kf_bb) in enumerate(live):
                gap = sidx - last_s
                if gap > self.max_age:
                    continue
                if len(bx) == 0:
                    continue
                kf_feet = np.array([[(kf_bb[0] + kf_bb[2]) / 2.0, kf_bb[3]]], dtype=float)
                kf_sf = (kf_feet @ inv[:, :2].T) + inv[:, 2]
                d = np.hypot(sf[:, 0] - kf_sf[0, 0], sf[:, 1] - kf_sf[0, 1])
                iou = _iou_matrix(bx, kf_bb[None, :])
                gate = self.foot_gate_px if gap == 1 else self.lost_gate_px
                # _iou_matrix(bx, kf_bb[None,:]) 的形状是 (len(bx), 1)，不是 (1, len(bx))
                ok = (d <= gate) | (iou[:, 0] >= self.min_iou)
                for j in np.nonzero(ok)[0]:
                    cost = (d[j] / max(1.0, gate)) - float(iou[j, 0]) + 0.05 * gap
                    cand.append((cost, ti, int(j)))
            cand.sort(key=lambda t: t[0])
            used_t, used_j = set(), set()
            for cost, ti, j in cand:
                if ti in used_t or j in used_j:
                    continue
                used_t.add(ti)
                used_j.add(j)
                ti_idx, _last, _kb = live[ti]
                tracks[ti_idx].append({"f": fidx, "bb": bx[j].copy(), "conf": float(cf[j]),
                                       "sx": float(sf[j, 0]), "sy": float(sf[j, 1])})
                live[ti] = (ti_idx, sidx, bx[j].copy())

            # --- 未匹配的检测开新轨迹 ---
            for j in range(len(bx)):
                if j in used_j:
                    continue
                tracks.append([{"f": fidx, "bb": bx[j].copy(), "conf": float(cf[j]),
                                "sx": float(sf[j, 0]), "sy": float(sf[j, 1])}])
                live.append((len(tracks) - 1, sidx, bx[j].copy()))

            live = [t for t in live if sidx - t[1] <= self.max_age]
        return tracks


_MIN_BOX_PX = 6      # 与 tools/track_players.py 的 MIN_BOX_PX 一致


def _compose(A, B):
    """compose(A, B) = A ∘ B（2x3 仿射，先 B 后 A）。与 track_players._compose 同式。"""
    A3 = np.vstack([A, [0, 0, 1]])
    B3 = np.vstack([B, [0, 0, 1]])
    return (A3 @ B3)[:2, :].astype(np.float32)


def track_metrics(tracks, n_steps, fps=25.0, stride=1):
    """把 tracks 折成 B 组指标。

    指标定义（**口径必须写死，否则两次运行不可比**）：
      n_tracks       轨迹条数（一条轨迹 = 一个 track_id）
      len_median     轨迹长度中位数（帧点数，单位是"被处理的帧"，不是原始帧）
      frag_rate      断档率 = 存在 >=1 次"缺帧"的轨迹占所有 >=3 点轨迹的比例
                     （缺帧 = 相邻两点 frame 差 > stride，即该人这帧被丢/漏检）
      id_switch      同一位置上的 ID 更替次数：对每条轨迹的**起点**，
                     若距它 1..max_gap 步内某条**另一条**轨迹的终点落在 60 px 内 ⇒ 记一次。
                     开环/闭环两种可能都有，这里只数"新 ID 诞生后紧接着一个旧 ID 消失"的情况，
                     并且只数**每段一次**（按 clip 汇总）。
    """
    lens = [len(t) for t in tracks]
    multi = [t for t in tracks if len(t) >= 3]
    n_gap = 0
    for t in multi:
        fs = [p["f"] for p in t]
        steps = np.diff(np.array(sorted(set(fs)), dtype=float)) if len(fs) > 1 else np.array([])
        if steps.size and np.any(steps > stride * 1.5):
            n_gap += 1
    frag_rate = (n_gap / len(multi)) if multi else None

    # ---- ID switch ----
    step_of = {}
    ends = []
    for t in tracks:
        if not t:
            continue
        for p in t:
            step_of.setdefault(p["f"], []).append((t is not None, p))
        ends.append((t[0]["f"], float(t[0]["sx"]), float(t[0]["sy"]), id(t)))
    seen = set()
    # 重建 step 索引：需要 fidx -> 步序号
    frames = sorted({p["f"] for t in tracks for p in t})
    fstep = {f: i for i, f in enumerate(frames)}
    starts = [(fstep[t[0]["f"]], float(t[0]["sx"]), float(t[0]["sy"]), id(t)) for t in tracks if t]
    fin = {}
    for t in tracks:
        if t:
            fin.setdefault(fstep[t[-1]["f"]], []).append((float(t[-1]["sx"]), float(t[-1]["sy"])))
    n_switch = 0
    for (s0, x, y, tid) in starts:
        hit = False
        for d in range(0, 4):
            for (ex, ey) in fin.get(s0 - d, []):
                if abs(ex - x) <= 60.0 and abs(ey - y) <= 60.0:
                    hit = True
                    break
            if hit:
                break
        if hit and (round(x), round(y)) not in seen:
            seen.add((round(x), round(y)))
            n_switch += 1
    return {
        "n_tracks": len(tracks),
        "n_short_lt3": sum(1 for v in lens if v < 3),
        "len_median": float(np.median(lens)) if lens else 0.0,
        "len_mean": float(np.mean(lens)) if lens else 0.0,
        "len_p90": float(np.percentile(lens, 90)) if lens else 0.0,
        "n_ge_25": sum(1 for v in lens if v >= 25),
        "frag_rate": (round(frag_rate, 4) if frag_rate is not None else None),
        "id_switch": int(n_switch),
        "n_tracks_per_100_steps": round(100.0 * len(tracks) / max(1, n_steps), 2),
    }


# =============================================================================
# 对照实验（A / B / C）
# =============================================================================
def _wilson(k, n, z=1.96):
    if not n:
        return None, None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(100 * max(0.0, c - h), 1), round(100 * min(1.0, c + h), 1)


def _pct(k, n):
    return (100.0 * k / n) if n else None


def _binom_p_less(k, n, p0):
    """单侧二项检验 P(X <= k | n, p0)，用来判「丢弃框的场内率是否显著更低」。

    ★ 用它而不是给 p 值编个说法：n 大时 p 值会小到没有意义，所以文档里同时给差值
      和 Wilson 区间，p 值只作**同向性**的第二票。
    """
    if not n:
        return None
    # 精确二项 CDF（n 最大约 3000，直接累加对数组合数，避免 scipy 依赖）
    lg = np.array([math.lgamma(i + 1) for i in range(n + 1)])
    ks = np.arange(0, min(k, n) + 1)
    logp = (lg[n] - lg[ks] - lg[n - ks]) + ks * math.log(max(p0, 1e-12)) \
        + (n - ks) * math.log(max(1 - p0, 1e-12))
    return float(np.exp(log10_to_ln(logp)).sum())


def log10_to_ln(x):
    return x


def _stat(vals):
    v = np.asarray([x for x in vals if x is not None], dtype=float)
    if v.size == 0:
        return {"n": 0}
    return {
        "n": int(v.size),
        "median": round(float(np.median(v)), 3),
        "mean": round(float(v.mean()), 3),
        "p10": round(float(np.percentile(v, 10)), 3),
        "p90": round(float(np.percentile(v, 90)), 3),
    }


def _fmt_med(blk):
    """打印用：空集（某些规则表下丢弃集合可以真的是 0 个框）也必须能打印，
    不能 KeyError —— 「没有任何框被丢」本身就是一个合法结果。"""
    m = (blk or {}).get("median")
    return "n/a" if m is None else "%.3f" % m


def mann_whitney(a, b):
    """Mann-Whitney U 的**正态近似**双尾 p（不打平手校正以外的花活）。

    ★ 为什么自己实现：docs/repo_spec.md §6 要求"禁止隐式第三方依赖"，
      scipy 虽然装了，但让 tools/ 里的脚本依赖它等于悄悄加了一条环境要求。
      这里只要 z 量级，用平均秩 + 连续性校正足够。
    """
    a = np.asarray([x for x in a if x is not None], dtype=float)
    b = np.asarray([x for x in b if x is not None], dtype=float)
    n1, n2 = a.size, b.size
    if n1 < 5 or n2 < 5:
        return None, None
    allv = np.concatenate([a, b])
    order = np.argsort(allv, kind="mergesort")
    ranks = np.empty(allv.size, dtype=float)
    ranks[order] = np.arange(1, allv.size + 1, dtype=float)
    # 平均秩（处理并列）
    sv = allv[order]
    i = 0
    while i < sv.size:
        j = i
        while j + 1 < sv.size and sv[j + 1] == sv[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + j + 2) / 2.0
        i = j + 1
    r1 = ranks[:n1].sum()
    u1 = r1 - n1 * (n1 + 1) / 2.0
    u = min(u1, n1 * n2 - u1)
    mu = n1 * n2 / 2.0
    sd = math.sqrt(n1 * n2 * (n1 + n2 + 1) / 12.0)
    if sd <= 0:
        return float(u), None
    z = (u - mu + 0.5) / sd
    p = 2.0 * 0.5 * math.erfc(abs(z) / math.sqrt(2.0))
    return float(u), float(min(1.0, p))


@dataclass
class BoxRecord:
    """一个 hi 层框的全部量（A/C 组的原始数据）。"""
    clip: str
    frame: int
    idx: int
    conf: float
    h_px: float
    cx: float
    cy: float
    ybot: float
    H: float
    W: float
    inf: bool
    band: str
    inside_y: bool
    used_y: bool
    crowd_h: float
    ratio: float
    reason: str
    color_role: str
    color_dist: float
    purity: float
    # 四种配置下的角色（after / legacy / noprior / nostruct）
    role_after: str = ""
    role_legacy: str = ""
    role_noprior: str = ""
    role_nostruct: str = ""
    role_norules: str = ""
    role_nohull: str = ""
    role_v1struct: str = ""
    role_minimal: str = ""
    reason_after: str = ""
    evidence: str = ""
    hull_ratio: float = -1.0
    # v1 时代的记录（写进 json 只为说明"v1 的设计被实测推翻"这件事，不参与任何结论）
    v1_note: str = ""


class _V1Meter(ColorMeter):
    """**冻结的 v1 量测**：只用于给文档提供"v1 会怎么做"的对照数字。

    v1 = 没有 rect 顺延、没有凸包判据的色层；far_crowd 直接丢。
    新代码不许调用它做决策，只许在对照里读它的数字。
    """

    def __init__(self, palette):
        ColorMeter.__init__(self, palette, use_hull=False)

    def _measure_uncached(self, frame, bb):
        h = float(bb[3] - bb[1])
        if h < MIN_H_PX:
            return self._blank(REASON_TOO_SMALL)
        rect = torso_rect(bb, frame.shape)
        if rect is None:
            return self._blank(REASON_RECT)
        col = _torso_color_cached(frame, self.palette, rect)
        if col is None:
            return self._blank(REASON_RECT)
        purity = float(col[4])
        if purity < MIN_PURITY:
            return self._blank(REASON_PURITY, purity=purity, hsv=col)
        role, dist, _scores = classify(col, self.palette)
        if role not in ("A", "B"):
            return {"role": "unknown", "dist": float(dist), "purity": purity,
                    "hsv": col, "reason": REASON_COLOR, "hull_ratio": None,
                    "jdist": None}
        return {"role": role, "dist": float(dist), "purity": purity,
                "hsv": col, "reason": REASON_COLORED, "hull_ratio": None,
                "jdist": None}


def v1_role(bb, cf, shape, m, crowd_h):
    """复算 v1 的判定（用于对照，不参与决策）。规则同 git 上一版 DEFAULT_RULES。"""
    h = _h_of(bb)
    inside = field_inside(bb, shape)
    if m["reason"] == REASON_COLORED:
        return "player"
    if not in_frame(bb, shape):
        return "staff"
    if crowd_h and (h / crowd_h) >= 0.60 and h >= FAR_ABS_MIN_PX_LEGACY:
        return "staff"
    act = LEGACY_RULES.get(m["reason"], "demote")
    if act == "keep":
        return "player"
    if act == "demote":
        return "unknown"
    if inside and crowd_h and h >= 0.6 * crowd_h:
        return "unknown"
    return "staff"


# ★ 公开配置表（PRESETS）：色层（tools/color_prior.py --gate）与对照实验共用**同一份**定义。
#   加新配置只改这里，消除"脚本里一套、实验里另一套"的第二真相源。
RULES_MINIMAL = {REASON_PURITY: "demote", REASON_RECT: "demote",
                 REASON_COLOR: "demote", REASON_TOO_SMALL: "keep"}

PRESETS = {
    # 推荐配置：按原因门控 + 结构证据
    "after": {},
    # 对照组（旧行为）：取不到颜色就丢，too_small 也算丢弃理由
    "legacy": {"rules": LEGACY_RULES},
    # 消融 1：完全没有颜色先验（只靠结构）
    "noprior": {"use_color": False},
    # 消融 2：只有颜色门控，没有结构证据
    "nostruct": {"use_struct": False},
    # 消融 3：颜色失败了就不分原因，一律 demote（"按原因"这一刀值多少）
    "norules": {"use_rules": False},
    # 消融 4：关掉凸包顺延（只用像素占比 purity，即 v1 的色层）
    "nohull": {"use_hull": False},
    # 消融 5：结构证据按 v1（far_crowd 直接丢），色层按 v2
    "v1struct": {"far_drop": True},
    # 消融 6：只保留"颜色正向证据 + too_small 不丢"（最保守的一刀）
    "minimal": {"rules": RULES_MINIMAL},
    # 旧口径别名（tools/color_prior.py 历史上叫这个名字）：= 只换规则表的 v1 结构证据
    # ★ 这一项**不是** v1 复现，只是规则表换回 LEGACY_RULES；真正的 v1 复算见 _V1Meter。
    "presets": {"rules": LEGACY_RULES},
}


def preset_config(name, palette=None, rules=None):
    """按名字造 GateConfig；未知名字抛 KeyError（带可选清单，别静默兜底）。

    palette : 覆盖队色表（色层与门控必须用同一个 palette）
    rules   : 覆盖规则表（reason -> keep/demote/drop 的字符串字典）
    """
    if name not in PRESETS:
        raise KeyError("%r 不是已知配置；可选：%s" % (name, ", ".join(sorted(PRESETS))))
    cfg = GateConfig(**{**asdict_cfg(GateConfig()), **PRESETS[name]})
    if palette:
        cfg.palette = dict(palette)
    if rules:
        cfg.rules = dict(rules)
    cfg.check()
    return cfg


def _configs(cfg_base):
    """八个配置 = 一个"推荐" + 七个消融。改门控必须能逐个拆开归因，不许只报一个总账。
    定义全部来自 PRESETS；cfg_base 只提供 palette / 阈值等**底盘**。"""
    return {k: GateConfig(**{**asdict_cfg(cfg_base), **PRESETS[k]}) for k in CONFIG_KEYS}


CONFIG_KEYS = ("after", "legacy", "noprior", "nostruct", "norules", "nohull",
               "v1struct", "minimal")


def asdict_cfg(cfg):
    d = asdict(cfg)
    d.pop("rules", None)
    return d


def collect(clips, model, cfg_base, stride, imgsz, hi_conf, do_track, max_clips=0,
            progress=True):
    """跑全量：逐帧检测 -> 五个配置各判一次 -> 收集 BoxRecord + 轨迹指标。"""
    cfgs = _configs(cfg_base)
    # ★ "after" 必须是**用户配置**(cfg_base)跑出来的，不能是 PRESETS["after"] 那个固定预设。
    #   否则 --gate-rules 会「在对照表上看着生效、在核心统计里完全不生效」—— 静默假绿。
    #   当 cfg_base == 默认推荐配置时两者等价，所以默认行为不变。
    cfgs["after"] = cfg_base
    meters = {k: ColorMeter(c.palette if c.use_color else {}, use_hull=c.use_hull,
                            hull_purity=c.hull_purity)
              for k, c in cfgs.items()}
    meter_v1 = _V1Meter(GateConfig().palette)      # 冻结的 v1 量测（只做对照）
    tracker_after = GeoBoxTracker()
    tracker_legacy = GeoBoxTracker()
    tracker_buffer = GeoBoxTracker()               # 缓冲保持：B 组第三口径
    records = []
    per_clip = []
    steps_after = {}
    steps_legacy = {}
    n_steps = 0

    def _sel(bx, cf, decs):
        k = [i for i in range(len(bx)) if r_after_ok(decs[i].role)]
        return np.array([bx[i] for i in k], dtype=float).reshape(-1, 4), \
            np.array([cf[i] for i in k], dtype=float).reshape(-1,)

    for ci, clip in enumerate(clips, 1):
        cap = cv2.VideoCapture(str(clip))
        if not cap.isOpened():
            raise RuntimeError("cannot open %s" % clip)
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        frame_no = -1
        st_a, st_l, st_b = [], [], []
        n_here = 0
        last_a = np.zeros((0, 4))
        last_l = np.zeros((0, 4))
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frame_no += 1
            if frame_no % stride:
                continue
            res = model.predict(frame, classes=[0], conf=hi_conf, iou=0.45,
                                imgsz=imgsz, verbose=False)
            boxes = res[0].boxes if res and res[0].boxes is not None else None
            if boxes is None or len(boxes) == 0:
                if do_track:      # 空帧也要推进（否则相机运动补偿会跨空帧乱跳）
                    st_a.append((frame_no, frame, last_a, np.ones(len(last_a))))
                    st_l.append((frame_no, frame, last_l, np.ones(len(last_l))))
                    st_b.append((frame_no, frame, last_a, np.ones(len(last_a))))
                continue
            bx = boxes.xyxy.cpu().numpy()
            cf = boxes.conf.cpu().numpy()
            n_steps += 1
            crowd_h, _n, _band = crowd_scale(bx, cf, cfg_base)
            H, W = frame.shape[:2]
            dec = {}
            for key in CONFIG_KEYS:
                dec[key] = judge_frame(frame, bx, cf, cfgs[key], meters[key])
            for i in range(len(bx)):
                bb = bx[i]
                h = float(bb[3] - bb[1])
                ybot = float(bb[3])
                cx, cy = float((bb[0] + bb[2]) / 2.0), float((bb[1] + bb[3]) / 2.0)
                m = meters["after"].measure(frame, bb)
                mv1 = meter_v1.measure(frame, bb)
                inside = field_inside(bb, frame.shape, cfg_base)
                band = band_margin(bb, frame.shape, cfg_base)
                records.append(BoxRecord(
                    clip=clip.name, frame=frame_no, idx=i, conf=float(cf[i]), h_px=h,
                    cx=cx, cy=cy, ybot=ybot, H=float(H), W=float(W),
                    inf=in_frame(bb, frame.shape), band=band, inside_y=bool(inside),
                    used_y=bool(inside and in_frame(bb, frame.shape)),
                    crowd_h=(crowd_h if crowd_h else -1.0),
                    ratio=((h / crowd_h) if crowd_h else -1.0),
                    reason=m["reason"], color_role=m["role"],
                    color_dist=float(m["dist"]), purity=float(m["purity"] or 0.0),
                    role_after=dec["after"][i].role,
                    role_legacy=dec["legacy"][i].role,
                    role_noprior=dec["noprior"][i].role,
                    role_nostruct=dec["nostruct"][i].role,
                    role_norules=dec["norules"][i].role,
                    role_nohull=dec["nohull"][i].role,
                    role_v1struct=dec["v1struct"][i].role,
                    role_minimal=dec["minimal"][i].role,
                    reason_after=dec["after"][i].reason,
                    evidence=dec["after"][i].evidence,
                    hull_ratio=(dec["after"][i].hull_ratio
                                if dec["after"][i].hull_ratio is not None else -1.0),
                    v1_note=v1_role(bb, cf[i], frame.shape, mv1, crowd_h)))
                n_here += 1
            if do_track:
                ka, ca = _sel(bx, cf, dec["after"])
                kl, cl = _sel(bx, cf, dec["legacy"])
                st_a.append((frame_no, frame, ka, ca))
                st_l.append((frame_no, frame, kl, cl))
                # 第三口径：缓冲保持 —— 本帧被门控判 staff 的框，如果前一帧还留在
                # 跟踪输入里，就把**上一帧的位置**补进来（不给新位置，只让轨迹续命）。
                # 测的是"丢弃到底是永久丢失观测，还是只缺一帧"。
                # ★ 必须是"被丢的"，不是"没被匹配上的"：后者会连移出画面的球员
                #   也在原地续命，把并不存在的连续性凭空造出来。
                kb = ka
                if len(ka) and len(dec["after"]):
                    dropped = [tuple(bx[i]) for i in range(len(bx))
                               if dec["after"][i].role == "staff"]
                    if dropped:
                        dm = np.asarray(dropped, dtype=float).reshape(-1, 4)
                        lost = [b for b in last_a
                                if _match_any(b, dm) or not _match_any(b, ka)]
                        if lost:
                            kb = np.vstack([ka, np.array(lost, dtype=float)])
                st_b.append((frame_no, frame, kb, np.ones(len(kb))))
                last_a = ka.copy()
                last_l = kl.copy()
        cap.release()
        if do_track:
            ta = tracker_after.run(st_a)
            tl = tracker_legacy.run(st_l)
            tb = tracker_buffer.run(st_b)
            ma = track_metrics(ta, len(st_a), fps=fps, stride=stride)
            ml = track_metrics(tl, len(st_l), fps=fps, stride=stride)
            mb = track_metrics(tb, len(st_b), fps=fps, stride=stride)
        else:
            ma = ml = mb = {}
        per_clip.append({"clip": clip.name, "n_hi_boxes": n_here,
                         "n_steps": len(st_a) if do_track else 0,
                         "track_after": ma, "track_legacy": ml, "track_buffer": mb})
        if progress:
            print("  [%d/%d] %-22s hi=%5d %s"
                  % (ci, len(clips), clip.name, n_here,
                     ("tracks a/l/b=%d/%d/%d len_med=%s/%s/%s"
                      % (ma.get("n_tracks"), ml.get("n_tracks"), mb.get("n_tracks"),
                         ma.get("len_median"), ml.get("len_median"),
                         mb.get("len_median"))) if do_track else ""), flush=True)
    return records, per_clip, n_steps


def _match_any(b, others, iou_thr=0.30):
    if not len(others):
        return False
    return bool(_iou_matrix(np.asarray([b], float), others)[0].max() >= iou_thr)


def r_after_ok(role):
    """进入跟踪器的框：player 与 unknown 都进（unknown 是低置信球员，不是丢弃桶）。"""
    return role in ("player", "unknown")


# ---------------------------------------------------------------- 统计装配
def _rate_block(sel, n_all):
    n = len(sel)
    k = sum(1 for r in sel if r.inside_y and r.inf)
    lo, hi = _wilson(k, n)
    return {"n": n, "n_inside": k, "rate_pct": _pct(k, n), "ci95": [lo, hi],
            "share_of_hi_pct": _pct(n, n_all)}


# ------------------------------------------------------------------ 场景（机位）分类
# ★ 为什么要分近景/远景再比：场内判据和轨迹指标在**近景机位**上完全不同（人少、人巨大、
#   画面里可能根本没有"场地边界"）。如果只在全量上给一个平均数，机位构成一变数字就翻。
SCENE_NEAR_MED_H_PX = 90.0     # 逐帧人框高中位数 >= 90 px ⇒ 近景机位
SCENE_MIN_N = 3


def scene_of(records_by_frame):
    """records_by_frame: { (clip, frame): [BoxRecord, ...] } -> {key: "near"|"far"}"""
    out = {}
    for key, rs in records_by_frame.items():
        hs = [r.h_px for r in rs if r.conf >= HI_CONF]
        if len(hs) < SCENE_MIN_N:
            out[key] = "sparse"
        else:
            out[key] = "near" if float(np.median(hs)) >= SCENE_NEAR_MED_H_PX else "far"
    return out


def _frames(records):
    d = {}
    for r in records:
        d.setdefault((r.clip, r.frame), []).append(r)
    return d


def _field_rates(sel, rule="y_min"):
    def ins(r):
        if rule == "center":
            return r.cy / r.H >= FIELD_Y_MIN
        if rule == "foot":
            return r.ybot / r.H >= 0.5
        return r.ybot / r.H >= FIELD_Y_MIN
    sel = [r for r in sel if r.inf]
    k = sum(1 for r in sel if ins(r))
    lo, hi = _wilson(k, len(sel))
    return {"n": len(sel), "n_inside": k, "rate_pct": _pct(k, len(sel)), "ci95": [lo, hi]}


def compare_A(records):
    """对照 A：hi 层「丢弃」的框 vs「保留」的框，场内/场外分布是否不同。

    ★ 定义全部写死（否则两边可以各自挑判据）：
        pool    = 全部 hi 层框（conf >= hi_conf）
        kept    = role ∈ {player} 或 {player, unknown}（两口径都给）
        dropped = role == staff
        inside  = field_inside(bb, field_rule) AND in_frame(bb)（出画的框不算"场内"）
      三种判据 y_min(0.35) / center(0.35) / foot(0.5) 并列给出，**异向要照写**。

    ★★ 解释力上限（写进 JSON，别让人误读）：
       这是"计数 ÷ 分母"，分母会随配置变 ⇒ 两个桶**不是同一批框**，
       场内占比差既可能来自"被丢的框更靠场外"，也可能来自"保留集本身漂移"。
       判定集合越小，这个差越不可信。样本量 < 200 的条目视为无判别力。
       样本量 < 20 的条目视为无判别力。
    """
    n_all = len(records)
    out = {}
    for tag, kept_sel in (("kept_pl_only", lambda r: r.role_after == "player"),
                          ("kept_pl_unk", lambda r: r.role_after in ("player", "unknown"))):
        kept = [r for r in records if kept_sel(r)]
        drop = [r for r in records if not kept_sel(r)]
        a, b = _rate_block(kept, n_all), _rate_block(drop, n_all)
        p0 = (a["rate_pct"] or 0) / 100.0
        p_lt = _binom_p_less(b["n_inside"], b["n"], p0) if b["n"] and p0 else None
        out[tag] = {
            "kept": a, "dropped": b,
            "diff_pt": (round(b["rate_pct"] - a["rate_pct"], 1)
                        if (a["rate_pct"] is not None and b["rate_pct"] is not None) else None),
            "binom_p_dropped_lower": p_lt,
        }

    # legacy（旧行为，取不到色就丢）同一口径：给出 before/after
    for tag, ok_role in (("legacy_kept_pl_unk", lambda r: r.role_legacy),
                         ("v1_kept_pl_unk", lambda r: r.v1_note)):
        kept = [r for r in records if ok_role(r) in ("player", "unknown")]
        drop = [r for r in records if ok_role(r) not in ("player", "unknown")]
        out[tag] = {"kept": _rate_block(kept, n_all),
                    "dropped": _rate_block(drop, n_all),
                    "diff_pt": (round(_rate_block(drop, n_all)["rate_pct"]
                                      - _rate_block(kept, n_all)["rate_pct"], 1)
                                if drop and kept else None)}

    # ★ 三种场内判据的同向性检查（防止结论只对某一条切点成立）
    kept = [r for r in records if r.role_after in ("player", "unknown")]
    drop = [r for r in records if r.role_after == "staff"]
    by_rule = {}
    for rule in FIELD_RULES:
        ka = _field_rates(kept, rule)
        kb = _field_rates(drop, rule)
        by_rule[rule] = {
            "kept": ka, "dropped": kb,
            "diff_pt": (round(kb["rate_pct"] - ka["rate_pct"], 1)
                        if (ka["rate_pct"] is not None and kb["rate_pct"] is not None)
                        else None),
        }
    out["by_field_rule"] = by_rule

    # 机位分层（近景 / 远景），避免机位构成改变结论
    sc = scene_of(_frames(records))
    by_scene = {}
    for name in ("near", "far", "sparse"):
        sub = [r for r in records if sc.get((r.clip, r.frame)) == name]
        if not sub:
            continue
        k = [r for r in sub if r.role_after in ("player", "unknown")]
        d = [r for r in sub if r.role_after == "staff"]
        kr = _field_rates(k)
        dr = _field_rates(d)
        by_scene[name] = {
            "n": len(sub), "kept": kr, "dropped": dr,
            "drop_rate_pct": _pct(len(d), len(sub)),
            "diff_pt": (round(dr["rate_pct"] - kr["rate_pct"], 1)
                        if (kr["rate_pct"] is not None and dr["rate_pct"] is not None)
                        else None),
        }
    out["by_scene"] = by_scene

    # 按原因拆（A 组最有信息量的一刀：哪一类"丢弃框"其实在场内）
    by_reason = {}
    for rs in FAIL_REASONS + (REASON_COLORED,):
        sub_kept = [r for r in kept if r.reason == rs]
        sub_drop = [r for r in drop if r.reason == rs]
        by_reason[rs] = {
            "n_total": len([r for r in records if r.reason == rs]),
            "kept": _rate_block(sub_kept, n_all),
            "dropped": _rate_block(sub_drop, n_all),
            "drop_rate_pct": _pct(len(sub_drop), len(sub_kept) + len(sub_drop)),
        }
    out["by_reason"] = by_reason

    # 按输出的 reason 码拆（v2 的归因表：谁被顺延救回来、谁被几何守卫救回来）
    by_out_reason = {}
    for r in records:
        k = r.reason_after
        b = by_out_reason.setdefault(k, {"n": 0, "player": 0, "unknown": 0, "staff": 0,
                                         "n_inside": 0})
        b["n"] += 1
        b[r.role_after] = b.get(r.role_after, 0) + 1
        if r.inside_y and r.inf:
            b["n_inside"] += 1
    for k, b in by_out_reason.items():
        b["inside_pct"] = _pct(b["n_inside"], b["n"])
    out["by_output_reason"] = by_out_reason

    # 带状分布（贴边界的"模糊带"里有多少被丢）—— 看判据是不是在边界上乱切
    bands = {}
    for bn in ("inside_solid", "band_ambiguous", "outside_solid"):
        sub = [r for r in records if r.band == bn]
        bands[bn] = {
            "n": len(sub),
            "n_dropped": sum(1 for r in sub if r.role_after == "staff"),
            "n_kept": sum(1 for r in sub if r.role_after in ("player", "unknown")),
            "drop_rate_pct": _pct(sum(1 for r in sub if r.role_after == "staff"), len(sub)),
        }
    out["by_band"] = bands

    # 与 v1 的直接对比（v1 的判定用冻结的 _V1Meter + LEGACY_RULES 复算）
    v1_drop = [r for r in records if r.v1_note == "staff"]
    v1_kept = [r for r in records if r.v1_note in ("player", "unknown")]
    out["v1_baseline"] = {
        "kept": _rate_block(v1_kept, n_all), "dropped": _rate_block(v1_drop, n_all),
        "n_kept": len(v1_kept), "n_dropped": len(v1_drop),
        "diff_pt": (round(_rate_block(v1_drop, n_all)["rate_pct"]
                          - _rate_block(v1_kept, n_all)["rate_pct"], 1)
                    if v1_drop and v1_kept else None),
    }
    return out


def compare_B(per_clip):
    """对照 B：丢弃后轨迹碎片化是否变差。

    三条口径，同一只**只看几何**的跟踪器、同一批帧、同一个 stride：
      after  : 用 v2 门控留下的框
      legacy : 用旧行为（取不到色就丢）留下的框
      buffer : after + "被丢的框在前一帧的位置补进来"（测丢弃是永久丢失还是缺一帧）
    """
    def agg(key):
        ks = [c["track_" + key] for c in per_clip if c.get("track_" + key)]
        if not ks:
            return {"n_clips": 0}
        lens = [k["len_median"] for k in ks]
        return {
            "n_clips": len(ks),
            "n_tracks": sum(k["n_tracks"] for k in ks),
            "n_short_lt3": sum(k["n_short_lt3"] for k in ks),
            "len_median_of_clips": round(float(np.median(lens)), 2),
            "len_mean_of_clips": round(float(np.mean([k["len_mean"] for k in ks])), 2),
            "n_ge_25": sum(k["n_ge_25"] for k in ks),
            "id_switch": sum(k["id_switch"] for k in ks),
            "frag_rate_mean": round(float(np.mean([k["frag_rate"] for k in ks
                                                   if k["frag_rate"] is not None])), 4)
            if any(k["frag_rate"] is not None for k in ks) else None,
            "tracks_per_100_steps": round(float(np.mean([k["n_tracks_per_100_steps"]
                                                         for k in ks])), 3),
        }
    a, l, b = agg("after"), agg("legacy"), agg("buffer")
    delta = {}
    for f in ("n_tracks", "id_switch", "n_ge_25", "n_short_lt3",
              "len_median_of_clips", "frag_rate_mean"):
        if f in a and f in l and a[f] is not None and l[f] is not None:
            delta[f] = round(a[f] - l[f], 3)
    # after 与 buffer 的差：如果为 0，说明"丢弃"根本没造成碎片化（缺点的框都被掩码吸收）
    delta_buf = {}
    for f in ("n_tracks", "id_switch", "n_ge_25", "len_median_of_clips"):
        if f in a and f in b and a[f] is not None and b[f] is not None:
            delta_buf[f] = round(a[f] - b[f], 3)
    return {"after": a, "legacy": l, "buffer": b,
            "delta_after_minus_legacy": delta,
            "delta_after_minus_buffer": delta_buf,
            "paired_per_clip": _paired_bf(per_clip),
            "per_clip": per_clip}


def _paired_bf(per_clip):
    """B 组的逐段**配对**检验：8 个指标在 12 个指标里方向完全一致，才算"没有变差"。

    ★ 为什么不能只看汇总 delta：汇总把 12 段的正负抵消掉了，而 843/787 这种
      总量差也可能是"消失的短轨迹"与"新断出来的短轨迹"互相抵消。逐段配对能看出
      方向是否一致；因为是配对设计（同一段、同一批帧、只换进门规则），用双侧
      符号检验（二项分布）给一个不依赖分布形状的 p 值。
    """
    from math import comb
    ks = [c for c in per_clip if c.get("track_after") and c.get("track_legacy")]
    if not ks:
        return {"n_clips": 0}
    fields = ("n_tracks", "n_short_lt3", "n_ge_25", "len_median", "len_mean",
              "frag_rate", "id_switch")
    out = {}
    for f in fields:
        d = []
        for c in ks:
            x, y = c["track_after"].get(f), c["track_legacy"].get(f)
            if x is None or y is None:
                continue
            d.append(float(x) - float(y))
        if not d:
            continue
        arr = np.asarray(d, dtype=float)
        nz = arr[np.abs(arr) > 1e-9]
        n_pos = int((nz > 0).sum())
        n_neg = int((nz < 0).sum())
        n = n_pos + n_neg
        if n == 0:
            p = 1.0
        else:
            k = min(n_pos, n_neg)
            p = min(1.0, 2.0 * sum(comb(n, i) for i in range(k + 1)) / (2.0 ** n))
        out[f] = {"n_clips": int(arr.size), "n_pos": n_pos, "n_neg": n_neg,
                  "median_delta": round(float(np.median(arr)), 4),
                  "sign_test_p_two_sided": round(float(p), 4)}
    n_worse = sum(1 for f, v in out.items()
                  if f in ("n_tracks", "n_short_lt3", "frag_rate", "id_switch")
                  and v["median_delta"] > 0)
    n_better = sum(1 for f, v in out.items()
                   if f in ("len_median", "len_mean", "n_ge_25")
                   and v["median_delta"] > 0)
    out["verdict"] = {
        "fields_where_more_fragmentation": n_worse,
        "fields_where_less_fragmentation": n_better,
        "note": "n_tracks/n_short_lt3/frag_rate/id_switch 变大 = 更碎；"
                "len_median/len_mean/n_ge_25 变大 = 更稳",
    }
    return out


def compare_C(records):
    """对照 C：被丢弃框的 conf / h_px 分布 vs 保留框；以及"丢弃是不是低阈值效应的重言"。

    ★ 关键一问：如果丢弃框的 conf 只是"更低"，门控就等价于把 conf 阈值从 0.25 提到 0.4，
      颜色没有独立贡献。所以给 conf 分箱内的**丢弃率**，并做事后分层检验。
    ★ 反事实（counterfactual）：丢掉这批框会让**多少帧**的球员数变化多少 ——
      这是唯一一个不依赖"场内"判据的、纯计数的对照。
    """
    kept = [r for r in records if r.role_after != "staff"]
    drop = [r for r in records if r.role_after == "staff"]
    out = {
        "kept": {"n": len(kept), "conf": _stat([r.conf for r in kept]),
                 "h_px": _stat([r.h_px for r in kept]),
                 "purity": _stat([r.purity for r in kept])},
        "dropped": {"n": len(drop), "conf": _stat([r.conf for r in drop]),
                    "h_px": _stat([r.h_px for r in drop]),
                    "purity": _stat([r.purity for r in drop])},
    }
    u, p = mann_whitney([r.conf for r in drop], [r.conf for r in kept])
    out["conf_dropped_vs_kept"] = {"mannwhitney_U": u, "p_two_sided": p,
                                  "direction": "dropped_higher" if
                                  (out["dropped"]["conf"].get("median") or 0)
                                  > (out["kept"]["conf"].get("median") or 0)
                                  else "dropped_lower"}
    u2, p2 = mann_whitney([r.h_px for r in drop], [r.h_px for r in kept])
    out["h_dropped_vs_kept"] = {"mannwhitney_U": u2, "p_two_sided": p2}

    bins = ((0.25, 0.35), (0.35, 0.45), (0.45, 0.55), (0.55, 0.70), (0.70, 0.85),
            (0.85, 1.01))
    by_conf = {}
    for lo, hi in bins:
        sub = [r for r in records if lo <= r.conf < hi]
        if not sub:
            continue
        by_conf["%.2f-%.2f" % (lo, min(hi, 1.0))] = {
            "n": len(sub),
            "drop_rate_after_pct": _pct(sum(1 for r in sub if r.role_after == "staff"), len(sub)),
            "drop_rate_legacy_pct": _pct(sum(1 for r in sub if r.role_legacy == "staff"), len(sub)),
            "drop_rate_noprior_pct": _pct(sum(1 for r in sub if r.role_noprior == "staff"), len(sub))}
    out["by_conf"] = by_conf

    hbins = ((0, 22), (22, 30), (30, 40), (40, 60), (60, 100), (100, 150), (150, 1e9))
    by_h = {}
    for lo, hi in hbins:
        sub = [r for r in records if lo <= r.h_px < hi]
        if not sub:
            continue
        nd = sum(1 for r in sub if r.role_after == "staff")
        by_h["%g-%g" % (lo, min(hi, 9999))] = {
            "n": len(sub), "n_dropped": nd, "drop_rate_pct": _pct(nd, len(sub)),
            "conf_median_kept": _stat([r.conf for r in sub if r.role_after != "staff"]).get("median"),
            "conf_median_dropped": _stat([r.conf for r in sub if r.role_after == "staff"]).get("median")}
    out["by_h_px"] = by_h

    sub = [r for r in records if r.conf >= 0.5 and r.h_px >= 30]
    out["high_conf_big"] = {
        "def": "conf>=0.50 且 h_px>=30",
        "n": len(sub),
        "n_dropped_after": sum(1 for r in sub if r.role_after == "staff"),
        "drop_rate_after_pct": _pct(sum(1 for r in sub if r.role_after == "staff"), len(sub)),
        "n_dropped_legacy": sum(1 for r in sub if r.role_legacy == "staff"),
        "drop_rate_legacy_pct": _pct(sum(1 for r in sub if r.role_legacy == "staff"), len(sub)),
        "n_dropped_noprior": sum(1 for r in sub if r.role_noprior == "staff"),
        "drop_rate_noprior_pct": _pct(sum(1 for r in sub if r.role_noprior == "staff"), len(sub)),
        "n_dropped_v1": sum(1 for r in sub if r.v1_note == "staff"),
        "drop_rate_v1_pct": _pct(sum(1 for r in sub if r.v1_note == "staff"), len(sub)),
    }

    big = [r for r in records if r.h_px >= 100.0]
    far = [r for r in records if r.ratio >= FAR_PEOPLE_FRAC and r.h_px >= FAR_ABS_MIN_PX]
    out["old_100px_vs_crowd"] = {
        "h_ge_100px": {"n": len(big), "share_pct": _pct(len(big), len(records)),
                       "n_dropped_after": sum(1 for r in big if r.role_after == "staff")},
        "far_crowd_rule": {"n": len(far), "share_pct": _pct(len(far), len(records)),
                           "n_dropped_after": sum(1 for r in far if r.role_after == "staff")},
    }

    # ---- 反事实：丢框对"每帧球员数"的影响（纯计数，不依赖场内判据）----
    frames = _frames(records)
    deltas = []
    for key, rs in frames.items():
        n_kept = sum(1 for r in rs if r.role_after != "staff")
        n_all = len(rs)
        if n_all:
            deltas.append(n_kept - n_all)
    dv = np.asarray(deltas, dtype=float) if deltas else np.zeros(1)
    out["counterfactual_frame_counts"] = {
        "n_frames": int(dv.size),
        "mean_loss_per_frame": round(float(-dv.mean()), 3),
        "pct_frames_losing_any": _pct(int((dv < 0).sum()), int(dv.size)),
        "max_loss_in_one_frame": int(-dv.min()) if dv.size else 0,
    }
    return out


def compare_ablation(records):
    """消融：每个配置丢多少、丢的框在不在场内 —— 回答"这条门控凭什么成立"。

    ★ `inside_gap_pt` 只是「丢弃桶里的场内占比 − 保留桶里的场内占比」，
      **不是精度**，也**不能**读作"这些框本来是球员"。原因见 §诚实边界 A：
      判定集合跟着配置变，场内占比的变化部分来自集合本身的漂移；
      场内还有裁判/教练/球童。这里之所以还留着它，是因为它在 5 个配置之间
      是**同口径可比的**，且与 by_band / by_field_rule 的方向一致时才采信。
    """
    out = {}
    for key in CONFIG_KEYS:
        rc = "role_" + key
        drop = [r for r in records if getattr(r, rc) == "staff"]
        kept = [r for r in records if getattr(r, rc) in ("player", "unknown")]
        unk = [r for r in records if getattr(r, rc) == "unknown"]
        ka = sum(1 for r in kept if r.inside_y and r.inf)
        kb = sum(1 for r in drop if r.inside_y and r.inf)
        out[key] = {
            "n_kept": len(kept), "n_player": len(kept) - len(unk), "n_unknown": len(unk),
            "n_dropped": len(drop),
            "drop_pct": _pct(len(drop), len(records)),
            "kept_inside_pct": _pct(ka, len(kept)),
            "dropped_inside_pct": _pct(kb, len(drop)),
            "n_kept_inside": ka, "n_dropped_inside": kb,
            "inside_gap_pt_NOT_precision": round(_pct(kb, len(drop)) - _pct(ka, len(kept)), 1)
            if (drop and kept) else None,
        }
    # 不同配置下"被丢的框"不是同一个集合，规模也差一个量级 ⇒ 并列时必须带 n
    n = {k: v["n_dropped"] for k, v in out.items()}
    ref = max(n, key=lambda k: n[k])
    for k, v in out.items():
        if k != ref:
            small, big = min(n[k], n[ref]) or 1, max(n[k], n[ref]) or 1
            v["n_scale_vs_%s" % ref] = round(big / small, 1)
    return out


def compare_v1_vs_v2(records):
    """v1（冻结复算）与 v2 在同一批框上的**归因**差异。

    这条对照回答任务书里那句"颜色在 hi 层完全没生效"：不是颜色没用，
    而是 v1 的量测层在 rect 失败时就先把颜色扔了。这里把"被 v2 救回来的框"单独数出来。
    """
    v1_drop = [r for r in records if r.v1_note == "staff"]
    rescued = [r for r in v1_drop if r.role_after != "staff"]
    still = [r for r in v1_drop if r.role_after == "staff"]
    new_drop = [r for r in records if r.v1_note != "staff" and r.role_after == "staff"]
    by_ev = {}
    for r in rescued:
        by_ev[r.reason_after] = by_ev.get(r.reason_after, 0) + 1
    return {
        "v1_dropped": len(v1_drop),
        "v2_dropped": sum(1 for r in records if r.role_after == "staff"),
        "v1_dropped_rescued_by_v2": len(rescued),
        "v1_dropped_still_dropped": len(still),
        "newly_dropped_by_v2": len(new_drop),
        "rescue_breakdown": by_ev,
        "rescued_inside_field_pct": _pct(sum(1 for r in rescued if r.inside_y and r.inf),
                                         len(rescued)),
        "rescued_h_median": _stat([r.h_px for r in rescued]).get("median"),
        "rescued_conf_median": _stat([r.conf for r in rescued]).get("median"),
    }


def label_free_sanity(records):
    """不依赖标注的**间接**校验：按 reason 分组看 conf / 框高 / 场内率。

    ★ 为什么需要它：对照 A 只能证明「丢掉的框在位置上更像非球员」，不能证明
      「每个 reason code 都名副其实」。没有真值的情况下，能做的只有一致性检查：
      某个 reason 如果真的是"非球员信号"，它的框应该同时表现出多项异常
      （conf 低 / 框离谱地大 / 出画），而不是只在一条上。
    ★ 这是**间接**证据，不是精度：所有数字都只能读作"同向/不同向"。
    """
    by = {}
    for r in records:
        by.setdefault(r.reason_after, []).append(r)
    out = {}
    for k, sel in sorted(by.items(), key=lambda kv: -len(kv[1])):
        roles = {}
        for r in sel:
            roles[r.role_after] = roles.get(r.role_after, 0) + 1
        out[k] = {
            "n": len(sel),
            "role": roles,
            "conf_median": round(float(np.median([r.conf for r in sel])), 3),
            "h_px_median": round(float(np.median([r.h_px for r in sel])), 1),
            "inside_pct": _pct(sum(1 for r in sel if r.inside_y and r.inf), len(sel)),
        }
    return out


def precision_proxy(records):
    """**加权到真实占比**的 precision 代理 —— 不是 precision。

    做法：标注是分层抽样（每层约 40 个），所以层内的 N率 可信、总量不可直接加。
    用 data/annot/scores2.json 的 stratum_true_share 把样本数折回"盘面上有多少框"，
    再按层加权得到「hi-unknown 里估计有多少非球员框」，然后与门控实际丢掉的框数对比。

    ★ 这里的三重不确定：① 标注本身 kappa=0.226；② stratum_true_share 来自另一批
      运行（data/results/hi_color_diag.json），两次运行不是同一批框；③ "drop 的框"
      是按本次运行的 reason_after 数出来的。所以先验层只能用 real_recall 的形式
      呈现 —— 即"门控丢掉了估计非球员量的百分之多少"，且必须标 n_est 是**估计值**。
    """
    ref = label_reference()
    strata = (ref or {}).get("by_stratum") or {}
    if not strata:
        return None
    diag = {}
    dp = REPO / "data" / "results" / "hi_color_diag.json"
    if dp.exists():
        diag = json.loads(dp.read_text(encoding="utf-8"))
    est_hi_unk = diag.get("n_unknown")
    n_hi = len(records)
    fmap = {"purity": "purity", "rect": "rect",
            "color": "color", "too_small": "toosmall"}
    out = {}
    share = {s: (strata.get(k) or {}).get("share_assumed_from_cp") for s, k in fmap.items()}
    for s, key in fmap.items():
        v = strata.get(key)
        if not v:
            continue
        out[s] = {"n_N": v.get("n_N") or 0,
                  "N_rate": v.get("N_rate"),
                  "n_labeled": v.get("n_labeled"),
                  "N_rate_ci95": v.get("ci95")}
    tot_N = sum(v["n_N"] for v in out.values())
    tot_lab = sum(v["n_labeled"] or 0 for v in out.values())
    # 本 run 里每个 reason 的丢弃数
    drop_by = {}
    for r in records:
        if r.role_after == "staff" and r.reason_after.startswith("color_drop_"):
            key = r.reason_after[len("color_drop_"):]
            drop_by[key] = drop_by.get(key, 0) + 1
    per = {}
    for reason, s in fmap.items():
        if s not in out:
            continue
        v = out[s]
        # 盘面估计：本 run 该层被门控判为 hi-unknown 的框数 / 抽样率（从 diag 拿）
        per[s] = {"n_dropped_by_gate": drop_by.get(reason, 0),
                  "reason": reason,
                  "n_labeled": v["n_labeled"],
                  "N_rate": v["N_rate"],
                  "n_N_in_sample": v["n_N"],
                  "share_assumed_from_cp": share.get(s),
                  "n_kept_in_stratum": None,
                  "n_N_est": None, "real_recall_est": None}
    n_drop = sum(v["n_dropped_by_gate"] for v in per.values())
    # 本 run 里每个 reason 的**保留**框数（含 player 与 unknown），用于「标注说该丢、
    # 门控却留着多少」这条对照；与 n_dropped_by_gate 同源，读者可自行核算。
    # ★ 按 **color_<动作>_<原因>** 的**后两段**归 key：丢的是 color_drop_purity（87），
    #   留的是 geo_rescue_purity（802）+ purity_hull_ok（42）—— 三者同属 purity 层。
    #   早先只用 r.reason_after 直接查，geo_rescue_*/purity_hull_ok 全查不到 ⇒ 保留数恒为 0，
    #   导致 §5.1(a) 的对照被读成「该层框全被丢了」。
    keep_by = {}
    for r in records:
        if r.role_after == "staff":
            continue
        parts = r.reason_after.rsplit("_", 1)
        if len(parts) == 2 and parts[1] in FAIL_REASONS:
            keep_by[parts[1]] = keep_by.get(parts[1], 0) + 1
    for s, v in per.items():
        v["n_kept_in_stratum"] = keep_by.get(v["reason"], 0)
    # ★ 覆盖率 >100% 是**不可能**的（门控不可能丢掉比"存在的非球员"更多的框）
    #   ⇒ 一旦超过，说明这次运行的 hi-unknown 盘面量与第二批标注那次不同。
    #   直接标为口径失效，不给数字，免得被当成结论引用。
    n_est_tot = sum(v["n_N_est"] or 0 for v in per.values())
    # ★ 前提检查：本 run 的 hi 层框数必须与 diag（分层比例的来源）一致
    over = diag.get("n_hi") is not None and n_hi != int(diag["n_hi"])
    why = None
    if not over and all(share.get(s) for s in per):
        # 盘面该层框数 = (本 run 的 hi 框数 × 该层假定占比)；
        # 盘面该层非球员数 = 盘面该层框数 × 层内 N率（= 样本 N 数 / 样本数）。
        # 化简后 = (样本 N 数 / 样本数) × 本 run 的 hi 框数 —— **不含**抽样率的倒数。
        # （旧实现写成「样本 N 数 / 抽样率」，抽样率≈0.084 时把估计量放大约 12 倍，
        #   于是覆盖率恒 >100% 而被判作废。这里按定义重算。）
        # ★ 「本 run 的 hi-unknown 框数」必须用**全量** hi-unknown（即取色失败的全部框），
        #   不是「丢掉的 + 留下的」。用 latter 会把丢弃数重复计入、把估计量撑到就把
        #   coverage 压成 100%（旧实现就是这么"通过"的）。全量 = 每个 reason 的 (丢 + 留)。
        n_hi_unk_run = sum(v["n_dropped_by_gate"] + (v["n_kept_in_stratum"] or 0)
                           for v in per.values())
        for s, v in per.items():
            n_lab = v["n_labeled"] or 0
            if not n_lab or v["N_rate"] is None:
                continue
            v["n_stratum_boxes_est"] = round(share[s] * n_hi_unk_run, 1)
            v["n_N_est"] = round(v["N_rate"] * v["n_stratum_boxes_est"], 1)
            # ★ 拓扑前提：同一层的「保留数」必须 ≤ 「该层盘面总框数」，否则说明
            #   假设的层占比与本 run 的 reason 命名口径不自洽（例：本 run 的 purity 层
            #   保留 844 框，而按 share 折算该层只有 785 框 ⇒ 内部矛盾）。
            #   矛盾时该层**不给区间**，只给这个不等式本身。
            v["topology_ok"] = bool(v["n_stratum_boxes_est"] is None or
                                    (v["n_kept_in_stratum"] or 0) <= v["n_stratum_boxes_est"])
            # ★ 比值可能 >1（估计量小于实际丢弃数）⇒ 一旦 clamp 成 1.0，读的人会以为
            #   "全覆盖了"。>1 时**不给数字**，只给数轴（n_est）与丢弃数，让读者自己判断。
            v["real_recall_est"] = (round(v["n_dropped_by_gate"] / v["n_N_est"], 3)
                                    if v["n_N_est"] else None)
            v["recall_est_clamped"] = bool(v["real_recall_est"] and v["real_recall_est"] > 1.0)
        n_est_tot = sum(v["n_N_est"] or 0 for v in per.values())
        # 只保留一种失效判据：**不可能性**（丢掉的框比盘面上的框还多）
        if n_hi_unk_run and n_drop > n_hi_unk_run:
            over, why = True, "门控丢的框(%d)比本 run 的 hi 层框(%d)还多" % (n_drop, n_hi_unk_run)
    if over:
        for v in per.values():
            v["n_N_est"] = None
            v["real_recall_est"] = None
    # ------------------------------------------------------------------
    # ★ cov_est 的去向（2026-10-20 改）：**不再输出任何精确百分比**。
    #   这个量的分子分母不同源（分母乘了另一次运行的层占比）、层内 N率 kappa=0.226、
    #   且 purity 层存在拓扑矛盾（保留 844 > 折算 785）。任何「8%」都会被下游当结论引用，
    #   而它既不是 precision 也不是 recall。⇒ 只给**保守界限**与不可靠原因。
    #   保守界限取对「门控覆盖得少」最不利（即让覆盖率看起来最大）的那个口径：
    #     分子 = 实际丢掉的框数 n_drop（本 run 实测，这是唯一硬数）
    #     分母 = max(全部折算估计量, 全部本 run 的 hi-unknown 框数)
    #   ⇒ cov_lo = n_drop / 分母上界；就算每 4 个取色失败框里只有 1 个是非球员，
    #     这个分母也是它的上界，所以 cov_lo 是「宁可高估覆盖率」的下界。
    # ------------------------------------------------------------------
    n_unk_whole = n_hi_unk_run if not over else None
    denom_lo = None
    if not over and n_est_tot:
        denom_lo = max(float(n_est_tot), float(n_unk_whole or 0))
    cov = {
        "cov_est_reliable": False,
        "why_unreliable": [
            "分母乘了一组来自另一次运行(hi_color_diag)的层占比，与本 run 不是同一批框；"
            "实测框数/折算框数在 rect 层相差 %.1f 倍" % (_agree(per.get("rect") or {}) or 1.0),
            "层内 N率来自 kappa=0.226 的标注，只可信层间相对高低",
            "purity 层存在拓扑矛盾：本 run 保留 %s 框 > 按层占比折算的 %s 框 ⇒ "
            "『同一层』的两种口径定义的集合不同（本 run 的 geo_rescue_purity/purity_hull_ok "
            "可能被 diag 那次归到了别层）"
            % ((per.get("purity") or {}).get("n_kept_in_stratum"),
               (per.get("purity") or {}).get("n_stratum_boxes_est")),
            "因此本字段**不给精确百分比**，只给一个对『覆盖率被低估』方向不敏感的界限",
        ],
        "bound_kind": ("rate_vs_hi_unknown_boxes（★上限意义上的下界，不是 precision，"
                       "也不是 recall）" if denom_lo else "n/a"),
        "bound_lo_from_measured_drops": (round(n_drop / denom_lo, 3) if denom_lo else None),
        "bound_lo_pct": (round(100.0 * n_drop / denom_lo, 1) if denom_lo else None),
        "bound_denominator": ({"n_drop_measured": n_drop,
                               "denom_upper_bound": round(denom_lo, 1),
                               "why_upper": "取 max(折算估计量, 本 run 的 hi-unknown 框数)，"
                                            "对『门控覆盖得少』最不利的口径"}
                              if denom_lo else None),
        "strength": "weak -> 只能读作『几分之一量级』，不得换算成任何百分比结论",
        "n_topology_violations": sum(1 for v in per.values() if v.get("topology_ok") is False),
    }
    return {
        "kind": "precision_proxy_weighted_by_stratum_share（不是 precision）",
        "valid": (not over),
        "invalid_reason": why or (("本次运行 hi 层共 %d 框，与 hi_color_diag.json 的 n_hi=%s 不符；"
                                   "第二批标注的分层比例是从 diag 那次抽样定的，口径不同 ⇒ 代理量作废"
                                   % (n_hi, diag.get("n_hi"))) if over else None),
        "cov_est_reliable": False,
        "cov": cov,
        "n_hi_this_run": n_hi,
        "n_hi_unknown_est_from_diag": est_hi_unk,
        "per_stratum": per,
        "n_N_in_labeled_sample": tot_N,
        "n_labeled_total": tot_lab,
        "sample_N_rate_weighted": round(tot_N / tot_lab, 3) if tot_lab else None,
        "n_drop_total": n_drop,
        "n_N_est_total": round(sum(v["n_N_est"] or 0 for v in per.values()), 1),
        # ★ 不再 clamp：>1 说明估计量比实际丢弃数还小 ⇒ 这个代理量**失效**，
        #   不是"覆盖得很好"。见 docs/player_gate_v1.md §5.1(b)。
        "real_recall_est_total_raw": (round(n_drop / n_est_tot, 3) if n_est_tot else None),
        "real_recall_est_total": None,   # 故意留空：这个数字只要出现就会被误读成覆盖率
        "coverage_status": ("n/a" if not n_est_tot else
                            ("INCONSISTENT_est_below_drops" if n_drop > n_est_tot else "ok")),
        "n_hi_unknown_run": n_hi_unk_run if not over else None,
        "biggest_gap": max(per.items(), key=lambda kv: (kv[1]["n_N_est"] or 0)
                           - kv[1]["n_dropped_by_gate"])[0] if per else None,
        "caveats": [
            "层内 N_rate 来自标注（kappa=0.226，只可信相对高低）",
            "stratum_true_share 来自另一次运行的分布，与本 run 不是同一批框",
            "n_N_est = 层内样本 N 数 / 该层在盘面上的真实占比，",
            "  隐含假设『第二批标注的那批帧与本次运行同样本分布』，",
            "  两次运行的 clip 集合/stride 不同 ⇒ 只能读作量级，不能读作计数",
            "因此只读作『门控覆盖了多少估计非球员量』，不能读作 precision",
        ],
    }


def label_reference():
    """把第二批标注的层间相对高低读进来（**只用于文字对照，不参与任何计算**）。"""
    p = REPO / "data" / "annot" / "scores2.json"
    if not p.exists():
        return {}
    d = json.loads(p.read_text(encoding="utf-8"))
    out = {"by_stratum": {}}
    n_total = 0
    shares = d.get("stratum_true_share") or {}
    for k, v in (d.get("by_stratum") or {}).items():
        key = k.replace("hi_unk_", "")
        out["by_stratum"][key] = {
            "n_labeled": v.get("n_labeled"), "n_N": v.get("n_N"), "n_sampled": v.get("n"),
            "N_rate": v.get("N_rate"),
            "N_rate_pct": (round(100 * v["N_rate"], 1) if v.get("N_rate") is not None else None),
            "ci95": v.get("N_rate_ci95")}
        n_total += v.get("n", 0)
        # 该层在**抽样那次运行**里有多少框 = 样本数 n / 抽样率
        share = shares.get(k)
        out["by_stratum"][key]["n_stratum_est_on_diag_run"] = (
            round(float(v["n"]) / float(share), 1) if share else None)
        # 该层占 hi 层的**假定占比**（= 抽样率）：按抽样率加权时必须乘它，不是乘 1
        out["by_stratum"][key]["share_assumed_from_cp"] = round(float(share), 6) if share else None
    out["_source"] = ("data/annot/scores2.json（第二批 %d 框人工标注；kappa=0.226 ⇒ 只用相对高低）"
                      % n_total)
    # 第一批（labels.csv 73 框）：hi 层与补检层的样本内精确率，以及"大框=非球员"的切片
    p1 = REPO / "data" / "annot" / "scores.json"
    if p1.exists():
        s1 = json.loads(p1.read_text(encoding="utf-8"))
        out["_batch1"] = {
            "source": "data/annot/scores.json（由 labels.csv 73 框算出）",
            "keep_color_prior": s1.get("keep_color_prior"),
            "keep_no_color": s1.get("keep_no_color"),
            "by_stratum": s1.get("by_stratum"),
            "hi_h_lt_100": s1.get("hi_h_lt_100"),
            "hi_h_ge_100": s1.get("hi_h_ge_100"),
            "caveats": s1.get("caveats"),
            # ★ 身份键说明：labels*.csv 的 box_idx 不是框的身份，
            #   本文件**从不**用 (clip,frame,box_idx) 与检测框配对；
            #   严格同框键 (clip,frame,conf,h_px) 只用于判定"两批有没有重叠"，不参与门控。
            "identity_key_note": {
                "rejected": "(clip, frame, box_idx)",
                "why": "box_idx = enumerate(items) 的该 stratum 内下标；items 的检测列表按 conf 降序、"
                       "长度随 conf 门限变化，不是框的身份",
                "used_for_gating": None,
            },
        }
    return out


def conflicts(A, C, v1v2, ref):
    """把「与标注结论冲突」的地方**机器化**地找出来，而不是靠人挑。

    三类：
      ① quasi_conflict：标注说该层多数非球员，但落到丢弃桶的框几乎全在场内。
         标注只看单个框、看不到"这个人下一帧还要被跟踪"，所以这不构成"标注错了"，
         但说明该层丢弃时**必须**有几何守卫，否则稳定性受损。
      ② label_vs_v1：标注说该层基本是球员，而旧行为一律丢 ⇒ 方向相反的 recall 风险。
      ③ counterintuitive：被丢框的 conf **更高**（见 C 组），与"低置信框是噪声"的直觉相反。
    """
    out = []
    for rs in (REASON_PURITY, REASON_RECT, REASON_COLOR, REASON_TOO_SMALL):
        blk = (A.get("by_reason") or {}).get(rs)
        lab = (ref.get(rs) or {}).get("N_rate_pct")
        if not blk or lab is None:
            continue
        drop_in = blk["dropped"]["rate_pct"]
        n_all = blk["n_total"]
        if lab >= 60 and drop_in is not None and drop_in >= 60:
            out.append({
                "kind": "quasi_conflict", "reason": rs, "n": n_all,
                "label_nonplayer_pct": lab, "dropped_inside_field_pct": drop_in,
                "text": ("标注说这一层 %.1f%% 非球员（n=%d），但落到丢弃桶的框有 %.1f%% 在场内 "
                         "⇒ 标注看不见的维度（轨迹连续性）上有代价；v2 用几何守卫把这些翻成 unknown"
                         % (lab, n_all, drop_in)),
            })
        if lab <= 25:
            out.append({
                "kind": "label_vs_v1", "reason": rs, "n": n_all,
                "label_nonplayer_pct": lab,
                "text": ("标注说这一层基本是球员（%.1f%% 非球员，n=%d），"
                         "而 v1/legacy 规则对它一律丢弃 ⇒ 与标注**方向相反**的 recall 风险。"
                         "v2 把该原因改为 keep" % (lab, n_all)),
            })
    if v1v2.get("v1_dropped_rescued_by_v2"):
        out.append({
            "kind": "fixed_v1", "reason": "v1_vs_v2", "n": v1v2["v1_dropped"],
            "text": ("v1 丢掉的 %d 个框里有 %d 个（%.1f%%）被 v2 救回非 staff，"
                     "其中场内率 %.1f%%、框高中位 %s px、conf 中位 %s"
                     % (v1v2["v1_dropped"], v1v2["v1_dropped_rescued_by_v2"],
                        100.0 * v1v2["v1_dropped_rescued_by_v2"] / max(1, v1v2["v1_dropped"]),
                        v1v2.get("rescued_inside_field_pct") or 0,
                        v1v2.get("rescued_h_median"), v1v2.get("rescued_conf_median"))),
        })
    cd = C.get("conf_dropped_vs_kept") or {}
    if cd.get("direction") == "dropped_higher":
        out.append({
            "kind": "counterintuitive", "reason": "conf",
            "n": C["dropped"]["n"],
            "text": ("被丢弃框的 conf 中位（%s）**高于**保留框（%s），p=%s "
                     "⇒ 门控不是在捡检测器的垃圾，而是在拒绝'检测器很有把握、但颜色不像任何队'"
                     "的框；也说明颜色证据是**独立于 conf** 的信息。"
                     % (C["dropped"]["conf"].get("median"), C["kept"]["conf"].get("median"),
                        cd.get("p_two_sided"))),
        })
    return out


def _agree(v):
    """实测该层框数 ÷ 按层占比折算的框数。差得越多，说明两把尺子越不同源。
    返回 None 表示缺少任一侧，调用方必须自己处理（不要用 1.0 冒充"一致"）。"""
    if not v:
        return None
    n_st = v.get("n_stratum_boxes_est")
    n_kept = v.get("n_kept_in_stratum")
    if n_st is None or n_kept is None or not n_st:
        return None
    n_actual = (v.get("n_dropped_by_gate") or 0) + n_kept
    return round(n_actual / n_st, 1)


def _agree_kind(v):
    """`_agree` 的容器包装（早先调用处传的是整个 per 字典，键名不匹配 ⇒ 恒返回 None）。"""
    if v and "n_stratum_boxes_est" not in v and "purity" in v:
        v = v.get("purity") or {}
    return _agree(v)


def gate_defects(ref, proxy):
    """标注驱动的**门控缺陷**清单：用分层 N率（相对高低可信）× 归一化后的框量。

    ★ 这是本文件与 data/annot 唯一的接触面。对齐方式：
       **零逐框对齐**。只读两样东西 ——
         (a) 第二批 159 框按 fail_reason 分层的 N率（层内比例，kappa=0.226 ⇒ 只信高低）
         (b) 第一批 73 框的 hi 层 / 补检层样本内精确率，以及「大框 vs 小框」切片
       **不用** (clip, frame, box_idx) 去和检测框相认 —— 那个键不是身份：
       两批各自 (clip,frame,box_idx) 只有 61 / 115 个唯一值（73 / 159 行），
       而 (clip,frame,conf,h_px) 在各自批内是 73 / 159 个唯一值，两批严格同框只有 5 个。
    ★ 每条缺陷都必须是「可证伪的预测」，不能是"再看看"。
    """
    out = []
    per = ((proxy or {}).get("per_stratum") or {})
    strata = {k: v for k, v in ((ref or {}).get("by_stratum") or {}).items()}

    # ① rect 层：标注 67.6% 非球员（n=210），但门控几乎不丢它 —— 覆盖面缺口
    r = strata.get("rect") or {}
    rp = per.get("rect") or {}
    if r.get("N_rate") is not None and r["N_rate"] >= 0.5:
        out.append({
            "kind": "coverage_gap", "reason": "rect",
            "label_nonplayer_pct": r.get("N_rate_pct"),
            "n_labeled": r.get("n_labeled"),
            "n_N_est": rp.get("n_N_est"),
            "n_stratum_boxes_est": rp.get("n_stratum_boxes_est"),
            "n_dropped_by_gate": rp.get("n_dropped_by_gate"),
            "n_kept_by_gate": rp.get("n_kept_in_stratum"),
            # ★ 自洽性：实测框数 vs 折算框数 是两把不同的尺，差 ≥5 倍就说明
            #   「本 run 的 hi-unknown 构成」与「第二批标注那次运行的层分布」已经不同源。
            "measure_source_agreement": _agree(rp),
            "text": ("本 run 落在 rect 失败桶的框 **%s 个（实测：丢 %s + 留 %s）**；"
                     "按第二批标注那次运行的层占比折算应约 %s 个（%.1f 倍差）。"
                     "标注层内 N 率 %.1f%%（n=%s）⇒ 若按标注，这桶里约 %s 个是非球员，"
                     "而门控只丢了 %s 个。两把尺子差这么多 ⇒ 缺口大小无法定论，"
                     "只能把「rect 层门控几乎不下手」当成**待测事实**（见文档 §5.1(a)）。"
                     % (rp.get("n_kept_in_stratum") is not None and
                        (rp["n_dropped_by_gate"] + rp["n_kept_in_stratum"]) or "?",
                        rp.get("n_dropped_by_gate"), rp.get("n_kept_in_stratum"),
                        rp.get("n_stratum_boxes_est"), _agree(rp) or 1.0,
                        r.get("N_rate_pct") or 0, r.get("n_labeled"),
                        rp.get("n_N_est") or 0, rp.get("n_dropped_by_gate"))),
            "falsifiable_prediction": ("在这些框里抽 K 个上看，门控判 player/unknown 的比例"
                                       "应接近 1 − 标注 N率；若实测显著更低，说明 rect 层的"
                                       "颜色兜底（整框取色 / hull 复检）确实救回了球员"),
        })

    # ② purity 层：标注 97.4% 非球员，门控丢弃桶里却几乎全在场内
    p = strata.get("purity") or {}
    pp = per.get("purity") or {}
    if p.get("N_rate") is not None and p["N_rate"] >= 0.9:
        out.append({
            "kind": "coverage_gap", "reason": "purity",
            "label_nonplayer_pct": p.get("N_rate_pct"),
            "n_labeled": p.get("n_labeled"),
            "n_N_est": pp.get("n_N_est"),
            "n_stratum_boxes_est": pp.get("n_stratum_boxes_est"),
            "n_dropped_by_gate": pp.get("n_dropped_by_gate"),
            "n_kept_by_gate": pp.get("n_kept_in_stratum"),
            "measure_source_agreement": _agree(pp),
            "text": ("purity 层是全案最大的分歧点。本 run 落在该桶的框 **%s 个"
                     "（实测：丢 %s + 留 %s）**，留下的 %s 个场内率 100%%、h 中位 37.8 px；"
                     "而标注说这层 97.4%% 非球员（n=%s，按那次运行的层占比只折算得出约 %s 个框，"
                     "与实测差 %s 倍）。两者无法同时成立，本轮**不裁决**（见文档 §5.1(a)）："
                     "场内率那一条不能单独反驳标注（场内有裁判/教练/工作人员）；"
                     "标注也不能单独推翻 800+ 个场内正常尺度的框。"
                     % (pp.get("n_kept_in_stratum") is not None and
                        (pp["n_dropped_by_gate"] + pp["n_kept_in_stratum"]) or "?",
                        pp.get("n_dropped_by_gate"), pp.get("n_kept_in_stratum"),
                        pp.get("n_kept_in_stratum"),
                        p.get("n_labeled"), pp.get("n_N_est") or 0, _agree(pp) or 1.0)),
            "falsifiable_prediction": ("若这一层真是非球员，它们应当**跨帧不稳定**："
                                       "同一人被检出的帧占比应低于球员层。抽 20 个测一下即可证伪"),
        })

    # ③ 第一批的"大框"切片：hi 层 h<100 px 时 10/10 是球员；h>=100 px 只有 57.1%
    b1 = (ref or {}).get("_batch1") or {}
    lo, hi = b1.get("hi_h_lt_100") or {}, b1.get("hi_h_ge_100") or {}
    if lo.get("precision") is not None and hi.get("precision") is not None:
        gap = (lo["precision"] - hi["precision"]) * 100
        out.append({
            "kind": "label_supported", "reason": "h_px",
            "n_lt_100": lo.get("n"), "n_ge_100": hi.get("n"),
            "text": ("第一批（labels.csv，hi 层）：h<100 px 精确率 %.1f%%（n=%d），"
                     "h>=100 px 只有 %.1f%%（n=%d），差 %.1f pt ⇒ 标注支持"
                     "「hi 层的大框才是非球员高风险区」。" %
                     (100 * lo["precision"], lo.get("n") or 0,
                      100 * hi["precision"], hi.get("n") or 0, gap)),
            "view_gap": ("第一批 n=%d / n=%d，95%% CI 宽约 ±20 pt；而且"
                         "「大框」在第二批里是**混**在四个 reason 里的，"
                         "不是独立的一层 ⇒ 这个切片只作旁证" % (lo.get("n") or 0, hi.get("n") or 0)),
        })

    # ④ 第一批的补检结论：有颜色先验（只留明确属于某队）样本内精确率 85.1%，
    #    无先验 71.0% —— 这是"颜色到底有没有用"这个原始问题的唯一正样本证据。
    kp, kn = b1.get("keep_color_prior") or {}, b1.get("keep_no_color") or {}
    if kp.get("precision_among_kept") is not None:
        out.append({
            "kind": "label_supported", "reason": "color_prior_value",
            "n_keep_color": kp.get("n"), "n_keep_nocolor": kn.get("n"),
            "text": ("第一批补检：有颜色先验保留框的样本内精确率 %.1f%%（n=%d），"
                     "无先验 %.1f%%（n=%d）⇒ 颜色先验在**低置信补检**这一环确实有效，"
                     "差 %.1f pt。" %
                     (100 * kp["precision_among_kept"], kp.get("n") or 0,
                      100 * (kn.get("precision_among_kept") or 0), kn.get("n") or 0,
                      100 * (kp["precision_among_kept"]
                             - (kn.get("precision_among_kept") or 0)))),
            "view_gap": ("样本是分层抽的、每层约 25 个、±9 pt；"
                         "而 hi 层（本文件主战场）里颜色先验的效应量在第一轮标注里**没有被测到**"),
        })
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="统一球员判定层（player gate）")
    ap.add_argument("--clips", default=str(CLIPS_DEFAULT))
    ap.add_argument("--max-clips", type=int, default=0)
    ap.add_argument("--stride", type=int, default=8,
                    help="隔几帧处理（默认 8，与 docs/hi_layer_color_gate.md §0 的口径一致）")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--hi-conf", type=float, default=HI_CONF)
    ap.add_argument("--model", default=str(MODEL_DEFAULT))
    ap.add_argument("--palette", default="red,white")
    ap.add_argument("--gate-rules", default="",
                    help="覆盖原因规则，例如 too_small=drop,purity=drop 或 presets:legacy")
    ap.add_argument("--ablate", action="store_true", help="跑 A/C 组对照（不跑跟踪）")
    ap.add_argument("--track", action="store_true", help="再跑 B 组轨迹碎片化对照（慢约 2x）")
    ap.add_argument("--write", action="store_true", help="写 data/results/player_gate.json")
    ap.add_argument("--out", default=str(OUT_JSON))
    args = ap.parse_args(argv)

    mp = Path(args.model)
    if not mp.exists():
        print("PLAYER_GATE_FAIL model_missing %s" % mp)
        return 2
    clips = sorted(Path(args.clips).glob("*.mp4")) if Path(args.clips).is_dir() \
        else [Path(args.clips)]
    if args.max_clips:
        clips = clips[: args.max_clips]
    if not clips:
        print("PLAYER_GATE_FAIL no_clips")
        return 2

    names = [s.strip().lower() for s in args.palette.split(",") if s.strip()]
    palette = {"A": names[0], "B": names[1]} if len(names) >= 2 else {}

    cfg_base = GateConfig(palette=palette)
    if args.gate_rules:
        if args.gate_rules.strip() == "presets:legacy":
            cfg_base.rules = dict(LEGACY_RULES)
        else:
            for kv in args.gate_rules.split(","):
                if not kv.strip():
                    continue
                k, _, v = kv.partition("=")
                k, v = k.strip(), v.strip()
                if k not in FAIL_REASONS or v not in ("drop", "demote", "keep"):
                    print("PLAYER_GATE_FAIL bad_rule %s" % kv)
                    return 2
                cfg_base.rules[k] = v

    from ultralytics import YOLO
    model = YOLO(str(mp))

    t0 = time.time()
    print("clips=%d stride=%d imgsz=%d hi_conf=%.2f rules=%s"
          % (len(clips), args.stride, args.imgsz, args.hi_conf,
             json.dumps(cfg_base.resolved_rules(), ensure_ascii=False)))
    records, per_clip, n_steps = collect(clips, model, cfg_base, args.stride, args.imgsz,
                                         args.hi_conf, args.track)
    elapsed = time.time() - t0

    A = compare_A(records)
    C = compare_C(records)
    ABL = compare_ablation(records)
    V1V2 = compare_v1_vs_v2(records)
    B = compare_B(per_clip) if args.track else {}
    ref = label_reference()
    PROXY = precision_proxy(records)
    defect = gate_defects(ref, PROXY)
    conf_list = conflicts(A, C, V1V2, ref)

    n_kept = ABL["after"]["n_kept"]
    n_drop = ABL["after"]["n_dropped"]

    print("\n== 门控规模（n_boxes=%d）==" % len(records))
    for k in CONFIG_KEYS:
        v = ABL[k]
        _di = v["dropped_inside_pct"]
        print("  %-9s kept=%6d (player %5d / unknown %5d) dropped=%5d (%.1f%%)"
              "  场内率 kept=%.1f%% dropped=%s"
              % (k, v["n_kept"], v["n_player"], v["n_unknown"], v["n_dropped"],
                 v["drop_pct"] or 0, v["kept_inside_pct"] or 0,
                 ("%.1f%%" % _di) if _di is not None else "n/a"))
    print("\n== v1（冻结复算）vs v2 ==")
    print("  v1 dropped=%d | v2 dropped=%d | v1 丢的框被 v2 救回=%d (%.1f%%) | 新丢=%d"
          % (V1V2["v1_dropped"], V1V2["v2_dropped"], V1V2["v1_dropped_rescued_by_v2"],
             _pct(V1V2["v1_dropped_rescued_by_v2"], V1V2["v1_dropped"]) or 0,
             V1V2["newly_dropped_by_v2"]))
    print("  救回明细：%s | 场内率 %.1f%% | h中位 %.1f | conf中位 %.3f"
          % (json.dumps(V1V2["rescue_breakdown"], ensure_ascii=False),
             V1V2["rescued_inside_field_pct"] or 0, V1V2["rescued_h_median"] or 0,
             V1V2["rescued_conf_median"] or 0))
    print("\n== 对照 A：场内/场外（判据 y_bottom >= %.2f*H 且未出画）==" % FIELD_Y_MIN)
    print("  ★ 两列不是同一批框，场内占比的变化部分来自集合漂移；这不是精度")
    for tag in ("kept_pl_only", "kept_pl_unk", "legacy_kept_pl_unk", "v1_kept_pl_unk"):
        if tag not in A:
            continue
        a, b = A[tag]["kept"], A[tag]["dropped"]
        print("  %-20s kept  %5d 场内 %5.1f%% | dropped %5d 场内 %5.1f%% | 差 %+.1f pt"
              % (tag, a["n"], a["rate_pct"] or 0, b["n"], b["rate_pct"] or 0,
                 A[tag]["diff_pt"] or 0))
    print("  三种场内判据的同向性 (diff_pt)：%s"
          % json.dumps({k: v["diff_pt"] for k, v in A["by_field_rule"].items()},
                       ensure_ascii=False))
    print("  by_band 丢弃率：%s"
          % json.dumps({k: (v["n"], v["drop_rate_pct"]) for k, v in A["by_band"].items()},
                       ensure_ascii=False))
    # 与标注分层的交叉检查：判定集合随配置变 ⇒ 必须报数量级
    _sizes = {k: ABL[k]["n_dropped"] for k in CONFIG_KEYS}
    print("  丢弃集合的规模（不同配置下的丢弃集合不是同一批框）：%s"
          % json.dumps(_sizes, ensure_ascii=False))
    print("\n== 对照 C：conf / h_px ==")
    print("  kept    n=%5d conf中位 %s h中位 %s"
          % (C["kept"]["n"], _fmt_med(C["kept"]["conf"]), _fmt_med(C["kept"]["h_px"])))
    print("  dropped n=%5d conf中位 %s h中位 %s | Mann-Whitney p=%s (%s)"
          % (C["dropped"]["n"], _fmt_med(C["dropped"]["conf"]), _fmt_med(C["dropped"]["h_px"]),
             C["conf_dropped_vs_kept"].get("p_two_sided"),
             C["conf_dropped_vs_kept"].get("direction") or "n/a(空集)"))
    hb = C["high_conf_big"]
    print("  高置信大框（%s, n=%d）丢弃率：after %.1f%% | noprior %.1f%% | legacy %.1f%% | v1 %.1f%%"
          % (hb["def"], hb["n"], hb["drop_rate_after_pct"] or 0,
             hb["drop_rate_noprior_pct"] or 0, hb["drop_rate_legacy_pct"] or 0,
             hb["drop_rate_v1_pct"] or 0))
    cf = C["counterfactual_frame_counts"]
    print("  反事实：%d 帧里平均每帧少 %s 个框，%.2f%% 的帧至少少一个，单帧最多少 %d"
          % (cf["n_frames"], cf["mean_loss_per_frame"], cf["pct_frames_losing_any"] or 0,
             cf["max_loss_in_one_frame"]))
    if B:
        print("\n== 对照 B：轨迹碎片化（同一只纯几何跟踪器，唯一差别是进门的框）==")
        print("  %-22s %-10s %-10s %-10s %s" % ("metric", "after", "legacy", "buffer",
                                                "Δ(after-legacy)"))
        for k in ("n_tracks", "n_short_lt3", "n_ge_25", "len_median_of_clips",
                  "len_mean_of_clips", "frag_rate_mean", "id_switch",
                  "tracks_per_100_steps"):
            print("  %-22s %-10s %-10s %-10s %s"
                  % (k, B["after"].get(k), B["legacy"].get(k), B["buffer"].get(k),
                     B["delta_after_minus_legacy"].get(k)))
        print("  Δ(after-buffer)：%s"
              % json.dumps(B["delta_after_minus_buffer"], ensure_ascii=False))
        pp = B.get("paired_per_clip") or {}
        if pp.get("n_clips") or pp:
            print("  逐段配对（同一段、同一批帧、只换进门规则；符号检验双侧 p）：")
            for f in ("n_tracks", "n_short_lt3", "n_ge_25", "len_median", "len_mean",
                      "frag_rate", "id_switch"):
                v = pp.get(f)
                if not v:
                    continue
                print("    %-14s 正/负=%2d/%2d  Δ中位 %+.3f  p=%.3f"
                      % (f, v["n_pos"], v["n_neg"], v["median_delta"],
                         v["sign_test_p_two_sided"]))
            print("    判定方向计数：%s" % json.dumps(pp.get("verdict"), ensure_ascii=False))
    if PROXY:
        print("\n== 精度代理（★不是 precision；按 层内N率 × 层假定占比 × 本run的hi-unknown框数 折到盘面量）==")
        print("  每个 reason 的框数（丢+留）：%s"
              % json.dumps({s: v["n_dropped_by_gate"] + (v.get("n_kept_in_stratum") or 0)
                            for s, v in (PROXY.get("per_stratum") or {}).items()},
                           ensure_ascii=False))
        for s, v in (PROXY.get("per_stratum") or {}).items():
            # ★ 别把局部变量叫 n_kept：令牌里的 n_kept 是 ABL["after"]["n_kept"]，
            #   同名覆盖会让故令牌打出最后一层的数（曾打出 1017 而不是 12487）。
            n_kept_st = v.get("n_kept_in_stratum")
            n_st = v.get("n_stratum_boxes_est")
            print("  %-10s 层内标注 非球员 %2d/%2d=%.1f%% | 本run该层 丢 %3d / 留 %3d | "
                  "按层占比折算盘面 %.1f 框 ⇒ 其中非球员估计 %.1f | 丢中覆盖 %s"
                  % (s, v["n_N_in_sample"], v["n_labeled"], 100 * (v["N_rate"] or 0),
                     v["n_dropped_by_gate"], -1 if n_kept_st is None else n_kept_st,
                     -1 if n_st is None else n_st, v["n_N_est"] or 0,
                     ("n/a" if v.get("topology_ok") is False
                      else "n/a（本字段不给精确百分比，见 cov 块）")))
        print("  ★ 口径警告：上面两列**分母不同源**。左列是本 run 实测（丢/留），"
              "右列是用另一次运行的层分布折算的估计量；两者不一致不构成任何一方的证伪。")
        print("  ★ 覆盖率**不再打印精确百分比**（cov_est_reliable=false）：分子分母不同源 "
              "+ purity 层拓扑矛盾（本 run 保留 844 框 > 按层占比折算 785 框）。只给保守界限：")
        _cv = PROXY.get("cov") or {}
        if _cv.get("bound_lo_pct") is not None:
            print("    门控丢 %d / 分母上界 %s ⇒ 覆盖率下界 %.1f%%"
                  "（★只是『上限意义的下界』，用来否定『门控覆盖了大部分』，"
                  "不能当 precision/recall）"
                  % (_cv["bound_denominator"]["n_drop_measured"],
                     _cv["bound_denominator"]["denom_upper_bound"], _cv["bound_lo_pct"]))
        else:
            print("    界限不给数：%s" % (_cv.get("strength") or "n/a"))
        for _w in (_cv.get("why_unreliable") or []):
            print("    · %s" % _w)
        if not PROXY.get("valid", True):
            print("  ⚠ 口径失效，本块数字**作废**：%s" % PROXY["invalid_reason"])
        elif PROXY.get("coverage_status") == "INCONSISTENT_est_below_drops":
            print("  合计：估计非球员量 %s 框 **小于** 实际丢弃数 %d ⇒ 代理量自相矛盾，"
                  "**不给覆盖率**（不是『覆盖得好』）"
                  % (PROXY["n_N_est_total"], PROXY["n_drop_total"]))
        elif PROXY.get("real_recall_est_total_raw") is not None:
            print("  合计：盘面估计非球员 %s 框（★该估计量与实测不符，见上『分母不同源』警告；"
                  "**不打印比值**——它会立刻被读成 precision）" % PROXY["n_N_est_total"])
        else:
            print("  合计：**不给数**（缺该层的抽样率/层占比）")
    if defect:
        print("\n== 门控缺陷 / 标注旁证（零逐框对齐，只用层内 N率 × 抽样率）==")
        for d in defect:
            print("  [%s] %s" % (d["kind"], d["text"]))
            if d.get("view_gap"):
                print("      可证伪的余地：%s" % d["view_gap"])
    if conf_list:
        print("\n⚠ 与标注结论的冲突（必须写进文档，不许挑指标）")
        for c in conf_list:
            print("  [%s] %s" % (c["kind"], c["text"]))

    payload = {
        "tool": "tools/player_gate.py",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "decision": "D013-a：用「取色失败原因」做球员判定门控（颜色是手段）",
        "version": "v2",
        "params": {"clips": len(clips), "stride": args.stride, "imgsz": args.imgsz,
                   "hi_conf": args.hi_conf, "palette": palette,
                   "rules": cfg_base.resolved_rules(),
                   "default_rules": dict(DEFAULT_RULES),
                   "legacy_rules": dict(LEGACY_RULES),
                   "min_h_px": MIN_H_PX, "min_purity": MIN_PURITY,
                   "max_color_dist": MAX_COLOR_DIST, "min_margin": MIN_MARGIN,
                   "field_y_min": FIELD_Y_MIN, "field_rule": cfg_base.field_rule,
                   "far_frac": cfg_base.far_frac, "far_abs_px": cfg_base.far_abs_px,
                   "far_drop": cfg_base.far_drop,
                   "hull_purity_min": cfg_base.hull_purity,
                   "crowd_min_n": cfg_base.crowd_min_n},
        "n_steps": n_steps,
        "n_boxes": len(records),
        "wall_time_s": round(elapsed, 1),
        "ablation": ABL,
        "v1_vs_v2": V1V2,
        "compare_A_position": A,
        "compare_B_fragmentation": B,
        "compare_C_conf_h": C,
        "label_reference": ref,
        "alignment_policy": {
            "how_labels_are_used": "分层 N率 × 抽样率；零逐框对齐",
            "key_rejected": "(clip, frame, box_idx)",
            "why_rejected": ("box_idx 是 make_player_labels.py 里 enumerate(items) 的"
                             "该 stratum 内下标，检测列表按 conf 降序且长度随 conf 门限变化，"
                             "不是一个框的身份。实测：labels.csv 73 行只有 61 个唯一"
                             "(clip,frame,box_idx)；labels2.csv 159 行只有 115 个；"
                             "而 (clip,frame,conf,h_px) 在各自批内是 73/159 个唯一值，"
                             "两批严格同框只有 5 个"),
            "n_boxes_aligned_one_to_one": 0,
            "consequence": "门控阈值没有一处在标注上拟合；标注只用于事后核对方向",
        },
        "precision_proxy": PROXY,
        "gate_defects": defect,
        "label_free_sanity": label_free_sanity(records),
        "conflicts_with_labels": conf_list,
        "limitations": [
            "所有指标都是**结构量**（位置/轨迹/conf/框高），不能当 precision/recall",
            "场内判据 FIELD_Y_MIN=0.35 是一条水平线；画面深处草坪与看台重叠，"
            "已用 by_band(±容差) 与三种判据做同向性检查",
            "B 组用自研纯几何跟踪器（不用 ByteTrack），绝对轨迹数不能与 "
            "data/results/player_tracks.json 的 942 条直接比，只做相对对照",
            "B 组的 stride=8，相邻处理帧间隔 0.32 s；max_age=3 ⇒ 单帧丢弃会被掩码吸收，"
            "B 组只能测到『连续多帧丢弃』的碎片化，测不到一次性丢帧；buffer 口径只补"
            "**被门控判 staff 的框**（不是『没匹配上的框』，后者会凭空造出连续性）",
            "素材仅 12 段、同一场比赛、单机位、红/白队服；白色队区分度天生弱",
            "第二批标注 kappa=0.226、P/N 真冲突 27% ⇒ 标注只用于『层间相对高低』，"
            "不与本文件的绝对数字做交叉验证",
            "hull_purity 用的是「球衣色像素在其凸包面积中的占比」，"
            "阈值 0.60 不是从标注集调的，敏感性见 docs/player_gate_v1.md §5",
            "precision_proxy 只是**层内 N 率 × 层假定占比**的折算（层假定占比来自另一次运行），"
            "且 purity 层存在**拓扑矛盾**（本 run 保留 844 框 > 按层占比折算的 785 框）⇒ "
            "cov_est_reliable=false，**不给精确百分比**，令牌里只打 cov_est=unreliable；"
            "见 docs/player_gate_v1.md §5.1(b)",
            "标注的 (clip,frame,box_idx) 不是框身份（box_idx 是层内 enumerate 下标，"
            "labels.csv 73 行仅 61 个唯一值、labels2.csv 159 行仅 115 个）⇒ 本文件零逐框对齐；"
            "alignment_policy.n_boxes_aligned_one_to_one = 0",
        ],
    }
    if args.write:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                  encoding="utf-8", newline="\n")
        print("\nRESULT_JSON %s" % args.out)

    metric = ("inside_gap=%+.1fpt" % (A["kept_pl_unk"]["diff_pt"] or 0))
    metric += " v1_drop=%d" % V1V2["v1_dropped"]
    if B:
        metric += " d_tracks=%+d d_idsw=%+d d_lenmed=%+.1f" % (
            B["delta_after_minus_legacy"].get("n_tracks", 0),
            B["delta_after_minus_legacy"].get("id_switch", 0),
            B["delta_after_minus_legacy"].get("len_median_of_clips", 0) or 0)
    # ★ 令牌里**不再打印 cov_est 的精确百分比**：会被下游当结论引用（见 §5.1(b)）。
    #   只给「不可靠」标签 + 分母上界意义下的下界，且下界也会被读成精度 ⇒ 只给标签。
    if PROXY is not None:
        metric += " cov_est=unreliable"
        if not PROXY.get("valid", True):
            metric += "(invalid)"
    if defect:
        metric += " DEFECT=%d" % len(defect)
    if conf_list:
        print("PLAYER_GATE_OK %d %d %s CONFLICT=%d"
              % (n_kept, n_drop, metric, len(conf_list)))
    else:
        print("PLAYER_GATE_OK %d %d %s" % (n_kept, n_drop, metric))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())