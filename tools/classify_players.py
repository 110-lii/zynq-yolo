"""球员 / 非球员分类（D012 + D013）：人工设定色板，机器做判定。

为什么这样设计
--------------
D012 要求区分「球员 / 裁判·教练·观众」。自动聚类球衣颜色在转播画面上不可靠
（白球衣与广告牌混、裁判黑衣与观众混），所以 **D013** 改成：开赛前由人在
**触屏 LCD** 上点选双方与裁判的球衣颜色（预设色板），机器只负责按这份设定分类。
本文件是该设定的 **PC 侧验证器**：先证明「给了正确颜色之后，分类到底准不准」，
再谈搬到板上 —— 不先验算法就去写 PL，是本仓库反复吃过的教训。

半场自动判定（用户 2026-10-20 要求「半场自动转换」）
--------------------------------------------------
不要求人输入「谁守哪半场」。做法：
  1. 只统计**高置信、框足够大**的球员（其余当噪声）；
  2. 每帧算「红队落脚点 x 的中位数」与「白队中位数」，比较两者 -> 该帧的左右归属；
  3. 全片按帧投票，票数过半者为该片的左右归属；
  4. 跨片段：若检测到归属**翻转**（例如下半场交换场地），记录翻转点时刻。

★ 为什么用「落脚点 x 中位数」而不是平均：中位数对少量误检不敏感。
★ 为什么需要「框足够大」：远处 10 px 的小人颜色不可靠，会把投票带偏。

用法
----
    # 1) 用预设色板（红/白），自动判定半场
    & D:\\python\\python.exe tools\\classify_players.py --palette red,white

    # 2) 指定片段与检测参数
    ... --clips data/samples/incidents --imgsz 640 --stride 2

    # 3) 不给色板：从数据里学（对照用，证明"人工设定"确实比自动强）
    ... --auto-palette

产物
----
    data/results/player_class.json    汇总：每段红/白/裁判/未知 计数、半场归属、翻转点
    data/results/class/<clip>.csv     逐人逐帧：track_id, frame, team, conf, color_dist

判据令牌
    CLASSIFY_DONE <n_clips> <n_player> <n_staff>
    CLASSIFY_FAIL <reason>

⚠️ 诚实限制（写进报告时必须带上）
  * 颜色判定的准确率**没有**人工真值：本文件只能给出「分类结果 + 距离」，不能说"准确率 92%"。
    要真值必须人工标注一批球员/非球员框（下一步）。
  * 裁判颜色若与某队接近，会被判成该队（这是物理限制，不是 bug）。
  * 光照变化（阴影/强光）会让同一件球衣的 HSV 漂移；本工具用色相为主、饱和度为辅来缓解。
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import csv
import json
import math
import time
from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[1]
RESULTS = REPO / "data" / "results"
CLASS_DIR = RESULTS / "class"
MODEL_DEFAULT = REPO / "data" / "models" / "yolov8n.pt"

# 躯干取色范围（与 tools/jersey_color.py 同口径，便于互相印证）
T_TOP, T_BOT, T_INSET = 0.18, 0.58, 0.18

# 预设色板（D013 方案 A：LCD 上显示这些色块让人点选）
# 色相取 OpenCV 0-179 制；S/V 给典型范围。做成"名字 -> 参考色相"，判定用色相距离。
# ★ 三个分量统一量纲（2026-10-20 修正）：h = OpenCV H(0-179) 原值或 -1/-2 哨兵；
#   s/v **一律 0..1**。第一版把 s 写成 0-255（170）、v 写成 0-1（0.75）混在一行里，
#   导致两轮"全判 white"的假结果 —— 表里混量纲是最难发现的一类 bug，这里明确写死。
# ★ 三个分量统一量纲（2026-10-20 修正）：h = OpenCV H(0-179) 原值或 -1/-2 哨兵；
#   s/v **一律 0..1**。第一版把 s 写成 0-255（170）、v 写成 0-1（0.75）混在一行里，
#   导致两轮"全判 white"的假结果 —— 表里混量纲是最难发现的一类 bug，这里明确写死。
# ★★ s/v 参考值已按**本批素材实测**校准（2026-10-20，257 个躯干样本，见
#    data/results/color_calibration.json）：红队 H=12° S=0.49 V=0.79，
#    白队 S=0.05 V=1.00。原先凭常识写的 S=0.75 / V=0.85 与实测偏差很大，
#    是「红队判成 unknown」的直接原因之一。
PALETTE_REF = {
    #         h(0-179)  s(0..1)  v(0..1)
    "red":     (6,       0.55,    0.78),   # 实测 H=12°(0-360) -> 6(0-179)
    "orange":  (15,      0.70,    0.80),
    "yellow":  (28,      0.75,    0.85),
    "green":   (60,      0.60,    0.55),
    "cyan":    (90,      0.65,    0.70),
    "blue":    (115,     0.70,    0.70),
    "purple":  (140,     0.60,    0.55),
    "magenta": (160,     0.65,    0.70),
    "white":   (-1,      0.08,    0.95),   # 实测 S=0.05 V=1.00
    "black":   (-2,      0.25,    0.20),
}

# 判定参数
MIN_H_PX = 22.0          # 框高下限：更小的目标颜色不可靠
                          # ★ 实测教训：素材是 640x360（原视频 1280x720 的切片），
                          # 人只有 25~117 px 高、多数 30~45 px。最初写 40 px 把大部分
                          # 框滤掉，导致红队 0 次检出。按实测下调到 22 px，并"宁可判
                          # unknown 也不硬猜"（MAX_COLOR_DIST 收紧）。
MIN_TORSO_PX = 6         # 躯干区域最小边长
MIN_PURITY = 0.30        # 躯干区域里"非草坪"像素的最低占比；低于此值判 unknown
                         # 640x360 小目标必然切进背景，purity 是唯一能自证的指标
YELLOW_MIN_S = 0.35      # 黄色与「草坪绿」的分界依赖饱和度
MAX_COLOR_DIST = 0.16    # 距离超过此值 -> unknown
                          # ★ 距离量纲：0.8*(色相差/0.5)+0.2*饱和度差，1.0 = 相差 180°。
                          #   0.30 相当于"色相 67° 以内都算"，实测把 H=68°(黄绿) 判成了
                          #   红色(0.287)。收到 0.16 ≈ 色相 34° 以内，才与"球衣颜色"匹配。
MIN_MARGIN = 0.04        # 最佳与次佳的距离差小于此值 -> ambiguous，判 unknown
                          # 只有两个色板时，"最近的那个"永远存在；不设 margin 会把
                          # 中间色强行二分。这条是"宁可 unknown 也不硬猜"的兑现。
                          # 小目标色相噪声大：阈值放松会制造大量假确定性，
                          # 故用较严的值，让"判不了"如实显示为 unknown。


def torso_rect(box, shape):
    """躯干矩形（与 jersey_color.torso_rect 同口径）。"""
    x1, y1, x2, y2 = [float(v) for v in box]
    w, h = x2 - x1, y2 - y1
    if w < 3 or h < 4:
        return None
    H, W = shape[:2]
    rx1 = int(max(0, min(W - 1, x1 + T_INSET * w)))
    rx2 = int(max(0, min(W, x2 - T_INSET * w)))
    ry1 = int(max(0, min(H - 1, y1 + T_TOP * h)))
    ry2 = int(max(0, min(H, y1 + T_BOT * h)))
    if rx2 - rx1 < MIN_TORSO_PX or ry2 - ry1 < MIN_TORSO_PX:
        return None
    return rx1, ry1, rx2, ry2


# 草坪绿背景抑制：这些色相区间 + 中低饱和度 = 几乎一定是草，不是球衣。
# ★ 为什么要这个（2026-10-20 实测）：640x360 素材里人只有 25~43 px 高，
#   躯干框约 20x13 px，"按固定比例切躯干"必然把大片草坪切进来；实测有大批
#   H=108~138/S=0.08~0.25 的样本被当成躯干色 —— 那是草坪，不是任何队服。
# ★ 草坪绿的色相在 OpenCV 的 H=30..90（即 0-360 制的 60..180）。但**白色/灰色也常被
#   OpenCV 报成 H≈180**（低饱和度时色相无意义），所以判草必须**同时**要求：
#   色相落在绿色区间 AND 饱和度够高（真正的草 S 通常 >=0.25）AND 亮度不过曝。
#   第一版只写 `s < 0.55 && 75<=H<=160`，把大量白色球衣（H=180 不在区间内，勉强没中招）
#   和灰白色（H≈100, S≈0.08）当成草剔掉 -> purity 掉到 0.07~0.31 -> 白队被门限挡掉。
GRASS_HUE_LO, GRASS_HUE_HI = 60.0, 150.0    # 度（0-360 制）
GRASS_S_MIN, GRASS_S_MAX = 0.22, 0.85       # 草是真绿：饱和度中高
GRASS_V_MAX = 0.92                          # 草不会过曝成白


def _is_grass_like(h_deg, s, v):
    return (GRASS_HUE_LO <= h_deg <= GRASS_HUE_HI
            and GRASS_S_MIN <= s <= GRASS_S_MAX
            and v <= GRASS_V_MAX)


def torso_color(bgr, rect):
    """躯干区域的主色 (h01, s, v, n_px, purity)。

    与第一版的差别（都是为了 640x360 小目标）：
      1. **剔除草坪绿像素**再统计 —— 否则背景草会主导中位数；
      2. 返回 `purity` = 剔除后剩余像素占躯干区域的比例。purity 低说明这个框
         大部分是背景，颜色不可信 —— 由调用方判 unknown，而不是硬猜。
    返回 None 表示该区域无可信像素。
    """
    x1, y1, x2, y2 = rect
    sub = bgr[y1:y2, x1:x2]
    if sub.size == 0:
        return None
    hsv = cv2.cvtColor(sub, cv2.COLOR_BGR2HSV)
    h = hsv[..., 0].astype(np.float32).ravel() * 2.0            # 0..360 度
    s = hsv[..., 1].astype(np.float32).ravel() / 255.0
    v = hsv[..., 2].astype(np.float32).ravel() / 255.0
    total = float(h.size)
    keep = v > 0.12                                              # 近黑像素无法定性
    if keep.sum() < 6:
        return None
    h, s, v = h[keep], s[keep], v[keep]
    # 草坪抑制：把疑似草的像素剔掉（保留高饱和的黄绿，可能是球衣）
    not_grass = ~np.array([_is_grass_like(hh, ss, vv) for hh, ss, vv in zip(h, s, v)], dtype=bool)
    if not_grass.sum() < 6:
        return None
    h2, s2, v2 = h[not_grass], s[not_grass], v[not_grass]

    # ★ 广告牌 / 背景抑制（2026-10-20 增）：实测有框取到 h=188°、s=0.19、v=0.75
    #   的"蓝绿色" —— 那是场边广告牌，不是球衣。球衣的两个特征是：
    #     (a) 饱和度高（真队服 S 通常 > 0.25；广告牌多为低饱和印刷色）
    #     (b) 亮度不过曝（V < 0.95；白色广告牌/灯箱会接近 1.0）
    #   于是再加两道过滤，并用**过滤后的剩余比例**当 purity。
    #   注意白色的队服 S 很低（实测 0.05），所以 (a) 只对**彩色**候选生效：
    #   这里先把"高饱和"与"低饱和但高亮且非草"两类都留下，再交给判定器裁决。
    colorful = (s2 >= 0.22) & (v2 <= 0.95)
    achromatic_bright = (s2 < 0.22) & (v2 >= 0.62)      # 白色球衣候选
    jersy_like = colorful | achromatic_bright
    if jersy_like.sum() < 6:
        return None
    h3, s3, v3 = h2[jersy_like], s2[jersy_like], v2[jersy_like]
    purity = float(jersy_like.sum()) / total
    rad = np.deg2rad(h3)
    mx, my = np.median(np.cos(rad)), np.median(np.sin(rad))
    hm = (math.degrees(math.atan2(my, mx)) % 360.0)
    return hm / 360.0, float(np.median(s3)), float(np.median(v3)), int(jersy_like.sum()), purity


def color_of_name(name):
    """预设色板名 -> 参考 (h01, s01, v01)。**全部归一到 0..1**。

    ★ 已修的 bug（2026-10-20）：PALETTE_REF 里 S 写成 0-255 量纲（如 170），
      而 torso_color 返回的 s 是 0..1（S/255）。两者直接相减得到 ~34 的
      "常数距离"，使任何颜色对红色都判为 34.4 —— 于是又退化成"全判 white"。
      ⇒ 在**这一处**统一除以 255.0，其他所有函数只用 0..1。
    """
    if name not in PALETTE_REF:
        raise SystemExit("unknown palette colour: %s (choices: %s)"
                         % (name, ", ".join(sorted(PALETTE_REF))))
    href, sref, vref = PALETTE_REF[name]   # 已是 0..1 量纲
    if href < 0:
        # white / black：色相无意义，用哨兵值
        return (None, sref, vref)
    # ★ PALETTE_REF 里 h 是 OpenCV 0-179 制，而 torso_color 返回 h01=0..1（=度/360）
    #   换算：度 = h*2，h01 = 度/360 = h/180。
    #   第一版写成 href/360.0 ⇒ 6 -> 0.0167 但**语义错位**（把 0-179 当 0-359 除），
    #   实测表现是白色判成 0.80 距离、红队恒 0.89 —— 又一个量纲 bug。
    return (href / 180.0, sref, vref)


def hue_dist(h1, h2):
    """两个色相的环形距离（0..0.5）。"""
    d = abs(h1 - h2) % 1.0
    return min(d, 1.0 - d)


def distance_to_ref(col, ref):
    """躯干颜色到某个预设色的距离（0=完全一致）。返回 (dist, kind)。

    分三类处理：
      white : 低饱和 + 高亮度 -> 用 (1-S) 与 |V-Vref| 的加权
      black : 低亮度         -> 用 |V-Vref|
      彩色  : 以色相为主（权重 0.8），饱和度为辅（0.2）

    ★ 这是 **v1**（CLASSIFY_METRIC="v1"，默认）。v2 见 `distance_to_ref_v2`。
      两套口径并存的原因见 v2 的注释：v1 的 0.16 对色相只剩 0.16/0.8=0.2 的配额
      （色相差 ≤36°）且完全没有亮度项 —— 换一场球、队服深浅一变就整队判 unknown。
      默认仍走 v1 ⇒ 旧比赛（red/white）的结论逐字节不变（回归表见
      docs/cross_match_eval.md §12.4）。
    """
    if col is None:
        return 9.9, "none"
    h01, s, v = col[0], col[1], col[2]
    href, sref, vref = ref
    if href is None:
        if sref <= 0.35 and vref >= 0.8:                 # white
            # ★ 已修 bug（2026-10-20）：第一版写 0.7*(1 - min(1, s/0.45))，
            #   s>=0.45 时该式恒为 0，于是**任何饱和色（含纯红 S=0.79）到白色的距离
            #   只剩 0.3*|V-Vref| ≈ 0.03**，比它到红色的距离 0.088 还小 ⇒ 全判 white。
            #   白色必须**同时**要求低饱和与高亮度，不能只用截断后的饱和度。
            ds = max(0.0, s - 0.30) / 0.70               # s<=0.30 视为"够白"；越大越不像白
            dv = abs(v - vref) / 0.25                    # 亮度偏差
            return 0.5 * min(1.0, ds) + 0.5 * min(1.0, dv), "white"
        return min(1.0, abs(v - vref) / 0.30), "black"   # black：只看亮度
    # 彩色：色相距离 + 饱和度差
    dh = hue_dist(h01, href) / 0.5                        # 归一到 0..1
    ds = abs(s - sref)
    return 0.8 * dh + 0.2 * ds, "hue"


# ---------------------------------------------------------------- v2 口径
# 为什么要有 v2（2026-10-20，跨比赛评估暴露）
#   1. v1 彩色分支 `0.8*dh + 0.2*|Δs|` 的**最大距离约 1.0**（dh 项最大 0.8，
#      |Δs| 项最大 0.2），所以 MAX_COLOR_DIST=0.16 在色相上只给了
#      0.16/0.8 = 0.2 的相对配额 ⇒ 色相差 ≤ 36°（且饱和度还要几乎相等）。
#      实测新比赛深蓝球衣（S 中位 0.298 vs 预置 blue 0.70）单这一项就吃掉 0.08。
#   2. v1 彩色分支**完全没有亮度 V**：深蓝 S=0.30/V=0.43 与浅蓝 S=0.30/V=0.52
#      距离完全相同 —— 而真实球衣像素的亮度跨度就有 0.36~0.62。
#   3. 两队的**色相间隔**是 60° 量级，不是"一格预设色"，用固定 36° 窗口去卡
#      本质上是把"色板网格"当成了"队服色"。
# v2 的换法（**不是**把阈值调大）：
#   - 彩色分支改成「**角度配额**」： hue_rel = 色相差 / MAX_HUE_DEG，与亮度/饱和度
#     放在同一 0~1 刻度里；亮度 V 正式进入（v1 没有这一项）。
#   - 门限改成 `MAX_COLOR_DIST_V2 = 0.70`，但语义是「允许多大**相对色相偏离**」，
#     不是放宽绝对阈值 —— 由 tools/kit_calib.py 采的真实队服样本标定（见文档）。
#   - 新增**草坪/非球衣守卫** `grass_like()`：放宽色相窗口后，草坪绿到预置 blue
#     的 v1 距离只有 0.36（= 0.8*(155-115)/180/0.5），上限一旦 >0.36 整片草坪都会
#     被判成蓝队。所以守卫与放宽必须同时做，否则只是把 unknown 换成假蓝。
CLASSIFY_METRIC = "v1"      # "v1" | "v2"；v1 是默认（旧结论逐字节不变）
MAX_COLOR_DIST_V2 = 0.70    # v2 门限（相对刻度，= 允许 70% 的 MAX_HUE_DEG 偏离）
MAX_HUE_DEG_V2 = 50.0       # v2 彩色分支的色相窗口（度，单边）
SAT_DEFICIT_K_V2 = 0.08     # v2 彩色分支：饱和度不足的补偿项（只对深色队服生效）
# ★ 亮度下限（v2 新加，**理由见文档 §12.5 的误判表**）：白衣在草反射 / 阴影 / 深处
#   压缩下会出现 h01≈0.58-0.65、S 0.07-0.24、V 0.59-0.80 的样本 —— 色相容完全落进
#   深蓝队的窗口，与真深蓝**只靠亮度分得开**（真深蓝 V 0.36-0.62、白队 V 0.63-0.80）。
#   把「彩色分支要求 V >= 0.65 的像素必须有足够饱和度」写成一条显式门槛，而不是让
#   0.70/0.08 这组常数去隐式兜底（那样随角度/饱和度换一下就重新开始误判）。
BRIGHT_S_MIN_V2 = 0.35      # V>=0.65 时，S 低于此值的像素不许进彩色分支
BRIGHT_V_REF_V2 = 0.65
MIN_MARGIN_V2 = 0.04        # v2 的 two-best 最小间隔（沿用 v1 的量纲：0~1 刻度）
GRASS_H01 = 0.2333          # 草绿色相（84°，4 帧真帧实测圆中位 0.233~0.244）
GRASS_HUE_TOL_DEG = 20.0    # 草绿窗口半宽
GRASS_S_MIN_V2 = 0.25       # 饱和度下限：低于它的灰/白像素不算"草坪"
GRASS_V_MIN_V2 = 0.30       # 亮度下限：暗到看不清的绿也不算（避免吞掉深色队服）


def grass_like(col):
    """v2 的草坪守卫：这一段像素主体是不是草。**只对彩色 + 高饱和生效**。

    ★ 为什么必须有（而不是"顺手加一条"）：放宽带内配额到 50° 后，
      草绿(0.233 一圈，实测 S 0.53-0.60 / V 0.59-0.66)与深蓝球衣(0.598 附近，
      S 0.30 / V 0.44)在**色相**上差 ~130°，但体素被草反射污染成 0.55-0.60、
      S≈0.30、V≈0.70 的"病态白衣"会落在两者之间。判据实测：
        - 草坪真像素质心 → 必须 unknown（4/4 帧）
        - 病态白衣（草反射）→ 不能判成深蓝队（这是 v2 预置口径下的 5 个 W→B 误判）
      同时 torso_color 已有 `_is_grass_like`（像素级、S 0.22-0.85 / V<=0.92 的草绿剔除），
      但它是"剔像素"不是"判整框"，两者互补。
    """
    if col is None:
        return False
    h01, s, v = col[0], col[1], col[2]
    if s < GRASS_S_MIN_V2 or v < GRASS_V_MIN_V2:
        return False
    return hue_dist(h01, GRASS_H01) * 360.0 <= GRASS_HUE_TOL_DEG


def distance_to_ref_v2(col, ref):
    """v2 距离（0=完全一致）。返回 (dist, kind)。**不读阈值，只管刻度**。

    彩色分支：dist = hue_rel + SAT_DEFICIT_K_V2 * max(0, sref-s) * (vref<0.60)
              其中 hue_rel = 色相差(度) / MAX_HUE_DEG_V2
              色相超出窗口 ⇒ 直接 2.0（不可判，与"很远"等价，比任何门限都大）
      为什么加"深色补偿"项：深藏青/深蓝队的**饱和度天然低**（草反射 + 传感器噪声
      把暗部饱和度拉平，实测 S 中位 0.298 而预置 blue 写的是 0.70）。它不是"更不像蓝"，
      是"更暗的蓝"。所以只在参考色本身偏暗（vref<0.60）时，允许用"色相到位"抵掉一部分
      饱和度不足。反过来，浅色队服（白）**不**享受这个补偿 —— 否则灰色会被判成白。
    无彩色分支（白/黑）：**与 v1 逐字节相同**。
      ★ 这里踩过坑：第一版 v2 把 white 分支的 ds 分母从 0.70 改成 0.50、dv 分母从 0.25
        改成 0.30，结果口罩（s=0.60/v=0.80）从 0.286 变成 0.26，**增幅 0.08 的
        "深色补偿"抵不过降幅** ⇒ 口罩被判成白衣。白/黑的判据 v1 本来就是对的
        （旧场白队距离 0.030、误判率 0/4 帧草坪），没有理由动它。
    """
    if col is None:
        return 9.9, "none"
    h01, s, v = col[0], col[1], col[2]
    href, sref, vref = ref
    if href is None:
        if sref <= 0.35 and vref >= 0.8:                 # white —— 与 v1 逐字节相同
            ds = max(0.0, s - 0.30) / 0.70
            dv = abs(v - vref) / 0.25
            return 0.5 * min(1.0, ds) + 0.5 * min(1.0, dv), "white"
        return min(1.0, abs(v - vref) / 0.30), "black"   # black —— 与 v1 逐字节相同
    hd = hue_dist(h01, href) * 360.0
    if hd > MAX_HUE_DEG_V2:
        return 2.0, "hue"                                # 色相窗外：不可判
    if v >= BRIGHT_V_REF_V2 and s < BRIGHT_S_MIN_V2:
        # 亮而不饱和 ⇒ 是"被环境色染了的白/灰"，不是"深色队服"。推回不可判。
        return 2.0, "hue"
    rel = hd / MAX_HUE_DEG_V2
    if vref < 0.60:                                      # 深色队服：允许色相补偿饱和度
        rel += SAT_DEFICIT_K_V2 * max(0.0, sref - s)
    return rel, "hue"


def _dist_metric(metric=None):
    """当前生效的距离函数（不传就用模块级 CLASSIFY_METRIC）。"""
    m = metric if metric is not None else CLASSIFY_METRIC
    return distance_to_ref_v2 if m == "v2" else distance_to_ref


def _limit(metric=None):
    """当前生效的门限（与距离函数配套，不要混搭）。"""
    m = metric if metric is not None else CLASSIFY_METRIC
    return MAX_COLOR_DIST_V2 if m == "v2" else MAX_COLOR_DIST


def _margin(metric=None):
    m = metric if metric is not None else CLASSIFY_METRIC
    return MIN_MARGIN_V2 if m == "v2" else MIN_MARGIN


def ref_of(value):
    """色板条目 -> 解析后的 (h01|None, s, v)。**唯一解析入口**。

    `palette` 的值允许三种形态（向后兼容 + 支持按场校准）：
      1. 颜色名（字符串，如 "white"/"blue"）—— 走 `color_of_name()` 查 PALETTE_REF；
      2. 已解析的元组 `(h01|None, s, v)` —— 直接使用（h01 用 None 表示无彩色）；
      3. kit_calib 的实测结构 `{"kind": "achromatic"|"hue", "h01"/"s"/"v"}` ——
         由 `tools/kit_calib.py` 产出，用于「按场校准」。
    ★ 为什么要有这一层：PALETTE_REF 是**按旧比赛素材标定**的常数，跨场会偏；
      实测新比赛深蓝球衣 S=0.298/V=0.435 而预置 blue 是 S=0.70/V=0.70，
      直接比会让 30 pt 的像素落到 0.16 门外判 unknown（见 docs/cross_match_eval.md §12.3）。
      允许实测参考色进来，但**不改变任何阈值**，也不改全局 PALETTE_REF。
    """
    if isinstance(value, str):
        return color_of_name(value)
    if isinstance(value, dict):
        kind = value.get("kind")
        if kind == "achromatic" or value.get("h01") is None:
            return (None, float(value["s"]), float(value["v"]))
        return (float(value["h01"]), float(value["s"]), float(value["v"]))
    if isinstance(value, (tuple, list)):
        if len(value) == 2:                       # (s, v) 视为无彩色
            return (None, float(value[0]), float(value[1]))
        if len(value) == 3:                       # (h01|None, s, v)
            h = value[0]
            return (None if h is None else float(h), float(value[1]), float(value[2]))
    raise SystemExit("bad palette entry: %r（只接受颜色名 / (h01,s,v) / kit_calib 结构）"
                     % (value,))


def classify(col, palette, metric=None):
    """把躯干颜色分配到最接近的预设色；超过阈值 -> unknown。

    palette: dict  role -> 颜色名 / 已解析参考色（见 `ref_of`）
    metric:  None = 用模块级 `CLASSIFY_METRIC`（默认 "v1"）；"v1"/"v2" 可显式指定。
             距离函数与门限**成对切换**（`_dist_metric` + `_limit`），不允许混搭。
    返回 (role or "unknown", best_dist, scores)

    ★ 这里有个**已修的 bug**（2026-10-20）：第一版把 `color_of_name()` 的**解析结果**
      传给了 `distance_to_ref()`，但那个函数内部又调用一次 `color_of_name()`，于是对
      「已解析的元组」再次解析 -> `SystemExit("unknown palette colour: (0.0, 170, 0.75)")`。
      而调用处包在 try 里，异常被吞掉后**静默退化成"全部判 white"**（实测 3 段 6506 帧
      红色 0 次、全白）。这是本仓库又一次「判据假绿」：没有崩溃，但结果毫无意义。
      ⇒ 现在 `distance_to_ref` 只接受**解析后的参考色**，解析只在 `ref_of()` 做一次。

    ★ v2（2026-10-20）额外加两道门，**只在 metric="v2" 时生效**：
      1. 草坪守卫：`grass_like(col)` 为真直接 unknown（理由见 `grass_like`）；
      2. 门限/间隔换成 v2 的一套（`_limit` / `_margin`）。
      它们**不改** `_classify_box` 的签名与 REASON_* 语义 —— 那是对外接口。
    """
    # ★ 必须经过 ref_of() 解析：PALETTE_REF 里是 h(0-179) 原始量纲，
    #   只有 color_of_name() 会把它换算成 h01(0..1)，与 torso_color 对齐。
    #   直接 .get() 拿到原始元组会让白色/黑色走错分支（实测白色距离恒 0.80）。
    use_v2 = (metric if metric is not None else CLASSIFY_METRIC) == "v2"
    if use_v2 and grass_like(col):
        return "unknown", 9.9, {}
    dist_fn = _dist_metric(metric)
    refs = {role: ref_of(cn) for role, cn in palette.items()}
    scores = {}
    for role, ref in refs.items():
        d, _kind = dist_fn(col, ref)
        scores[role] = round(float(d), 4)
    if not scores:
        return "unknown", 9.9, scores
    order = sorted(scores, key=scores.get)
    best = order[0]
    if scores[best] > _limit(metric):
        return "unknown", scores[best], scores
    if len(order) > 1 and (scores[order[1]] - scores[best]) < _margin(metric):
        return "unknown", scores[best], scores      # 两色几乎一样近 -> 不判
    return best, scores[best], scores


def auto_palette_from(bgr, boxes):
    """不给人工色板时，从本帧高置信大框里取两个主色（仅作对照组）。

    做法：收集所有躯干中位色，按 HSV 的 S/V 先分「低饱和(白/黑)」与「彩色」，
    彩色里按色相直方图取两个峰。返回 dict 或 {}。
    """
    cols = []
    for bb in boxes:
        r = torso_rect(bb, bgr.shape)
        if r is None:
            continue
        c = torso_color(bgr, r)
        if c is not None:
            cols.append(c)
    if len(cols) < 6:
        return {}
    hv = np.array([c[0] for c in cols])
    sv = np.array([c[1] for c in cols])
    vv = np.array([c[2] for c in cols])
    # 低饱和高亮 -> white 占多数时给 white
    out = {}
    colorful = sv > 0.35
    if colorful.sum() >= 4:
        # 色相直方图（36 bin）取前两个峰，且要求两峰相距足够
        hist, edges = np.histogram(hv[colorful], bins=36, range=(0.0, 1.0))
        order = np.argsort(-hist)
        picked = []
        for b in order:
            if hist[b] == 0:
                continue
            center = float((edges[b] + edges[b + 1]) / 2.0)
            if all(hue_dist(center, p) > 0.12 for p in picked):
                picked.append(center)
            if len(picked) == 2:
                break
        names = ["A", "B"]
        for i, c in enumerate(picked):
            # 找最近的预设名（只为可读；判定仍用原始色相）
            best = min(PALETTE_REF.items(),
                       key=lambda kv: 9.9 if kv[1][0] < 0 else
                       hue_dist(c, kv[1][0] / 180.0))
            out[names[i]] = best[0]
        # 低饱和多数 -> 说明有一队是白的，替换掉较弱的那一队
        if (sv < 0.3).mean() > 0.3 and "B" in out:
            out["B"] = "white"
    elif (sv < 0.3).mean() > 0.5:
        out["A"] = "white"
    return out


def process_clip(model, clip_path, args):
    cap = cv2.VideoCapture(str(clip_path))
    if not cap.isOpened():
        raise RuntimeError("cannot open %s" % clip_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    rows = []
    palette = None
    frame_no = -1
    stride = max(1, int(args.stride))

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frame_no += 1
        if frame_no % stride:
            continue

        res = model.predict(frame, classes=[0], conf=args.conf, iou=0.45,
                            imgsz=args.imgsz, verbose=False)
        if not res or res[0].boxes is None or len(res[0].boxes) == 0:
            continue
        b = res[0].boxes
        xyxy = b.xyxy.cpu().numpy()
        confs = b.conf.cpu().numpy()

        # 首次读帧后确定色板（人工给定优先）
        if palette is None:
            if args.palette:
                palette = {r: c for r, c in
                           zip((("A", "B") + (("ref",) if len(args.palette) > 2 else ())),
                               args.palette)}
            else:
                palette = auto_palette_from(frame, xyxy)
            if not palette:
                continue
            print("  palette: %s" % json.dumps(palette, ensure_ascii=False), flush=True)

        for bb, cf in zip(xyxy, confs):
            h = float(bb[3] - bb[1])
            if h < MIN_H_PX:
                rows.append((frame_no, -1, "unknown", round(float(cf), 4), "", 9.9,
                             round(float((bb[0] + bb[2]) / 2), 1), round(float(bb[3]), 1)))
                continue
            r = torso_rect(bb, frame.shape)
            if r is None:
                continue
            col = torso_color(frame, r)
            if col is not None and col[4] < MIN_PURITY:
                col = None                      # 背景占比过高 -> 不取色，如实 unknown
            role, dist, scores = classify(col, palette)
            rows.append((frame_no, -1, role, round(float(cf), 4),
                         "" if col is None else "%.3f/%.2f/%.2f/p%.2f" % (col[0], col[1], col[2], col[4]),
                         round(float(dist), 4),
                         round(float((bb[0] + bb[2]) / 2), 1),
                         round(float(bb[3]), 1)))
    cap.release()
    return {"clip": Path(clip_path).name, "fps": fps, "palette": palette, "rows": rows}


def analyse(clip_res):
    """从逐帧分类结果里算：计数、半场归属、翻转点。"""
    rows = clip_res["rows"]
    if not rows:
        return {"clip": clip_res["clip"], "n": 0}

    cnt = {}
    for r in rows:
        cnt[r[2]] = cnt.get(r[2], 0) + 1

    # 半场：按帧比较 A/B 两队落脚点 x 的中位数
    by_frame = {}
    for r in rows:
        if r[2] in ("A", "B") and r[6] is not None:
            by_frame.setdefault(r[0], {"A": [], "B": []})[r[2]].append(r[6])
    votes_left = []          # (frame, 左边是哪一队)
    for f in sorted(by_frame):
        d = by_frame[f]
        if len(d["A"]) < 2 or len(d["B"]) < 2:
            continue                     # 每队至少 2 人投票，否则不投
        ma = float(np.median(d["A"]))
        mb = float(np.median(d["B"]))
        votes_left.append((f, "A" if ma < mb else "B"))

    left_team, left_frac = "unknown", 0.0
    flips = []
    if votes_left:
        nA = sum(1 for _, t in votes_left if t == "A")
        nB = len(votes_left) - nA
        left_team = "A" if nA >= nB else "B"
        left_frac = max(nA, nB) / float(len(votes_left))
        # 翻转点：把投票序列按 majority 平滑后找变化
        seq = [(f, 1 if t == "A" else 0) for f, t in votes_left]
        win = 15
        sm = []
        for i in range(len(seq)):
            lo, hi = max(0, i - win // 2), min(len(seq), i + win // 2 + 1)
            m = sum(v for _, v in seq[lo:hi]) / float(hi - lo)
            sm.append((seq[i][0], m))
        prev = None
        for f, m in sm:
            cur = 1 if m > 0.5 else 0
            if prev is not None and cur != prev:
                flips.append({"frame": f, "t_s": round(f / float(clip_res["fps"]), 2),
                              "left_before": "A" if prev == 1 else "B",
                              "left_after": "A" if cur == 1 else "B"})
            prev = cur

    return {
        "clip": clip_res["clip"],
        "n_rows": len(rows),
        "counts": cnt,
        "left_team": left_team,
        "left_vote_frac": round(left_frac, 3),
        "n_vote_frames": len(votes_left),
        "flips": flips,
        "palette": clip_res["palette"],
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="球员/非球员分类（D012/D013，PC 侧验证）")
    ap.add_argument("--clips", default=str(REPO / "data" / "samples" / "incidents"))
    ap.add_argument("--max-clips", type=int, default=0)
    ap.add_argument("--palette", default="",
                    help="逗号分隔的预设色名，如 red,white 或 red,white,black"
                         "（第 3 个为裁判色；不填则自动学，作对照）")
    ap.add_argument("--auto-palette", action="store_true", help="强制自动学色板")
    ap.add_argument("--stride", type=int, default=2)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--model", default=str(MODEL_DEFAULT))
    args = ap.parse_args(argv)
    if args.auto_palette:
        args.palette = ""
    if args.palette:
        args.palette = [s.strip().lower() for s in args.palette.split(",") if s.strip()]
        if len(args.palette) < 2:
            print("CLASSIFY_FAIL palette_needs_at_least_2")
            return 2

    mp = Path(args.model)
    if not mp.exists():
        print("CLASSIFY_FAIL model_missing %s" % mp)
        return 2

    clips = sorted(Path(args.clips).glob("*.mp4")) if Path(args.clips).is_dir() \
        else [Path(args.clips)]
    if args.max_clips:
        clips = clips[: args.max_clips]
    if not clips:
        print("CLASSIFY_FAIL no_clips")
        return 2

    from ultralytics import YOLO
    model = YOLO(str(mp))

    CLASS_DIR.mkdir(parents=True, exist_ok=True)
    out = {"tool": "tools/classify_players.py",
           "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "decision": "D012（球员/非球员）+ D013（触屏录入色板）",
           "mode": "manual_palette" if args.palette else "auto_palette",
           "params": {"palette_arg": args.palette, "stride": args.stride,
                      "imgsz": args.imgsz, "conf": args.conf,
                      "min_h_px": MIN_H_PX, "max_color_dist": MAX_COLOR_DIST,
                      "torso": {"top": T_TOP, "bot": T_BOT, "inset": T_INSET}},
           "clips": [],
           "limitations": [
               "颜色分类**没有人工真值**：本文件不给准确率，只给分类结果与颜色距离",
               "裁判色若与某队接近会被判成该队（物理限制）",
               "半场归属是**逐帧投票**结果，不是裁判记录；交换场地时刻需人工核对",
               "track_id 此处为 -1：本文件不跑跟踪，只做单帧分类（跟踪由 track_players.py 负责）",
           ]}
    n_player = n_staff = 0
    for i, c in enumerate(clips, 1):
        print("[%d/%d] %s ..." % (i, len(clips), c.name), flush=True)
        r = process_clip(model, c, args)
        a = analyse(r)
        out["clips"].append(a)
        n_player += a.get("counts", {}).get("A", 0) + a.get("counts", {}).get("B", 0)
        n_staff += a.get("counts", {}).get("ref", 0)
        # CSV
        cp = CLASS_DIR / ("%s.csv" % Path(c).stem)
        with open(cp, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["frame", "track_id", "role", "conf", "hsv_median", "color_dist",
                        "foot_x", "foot_y"])
            w.writerows(r["rows"])
        print("  counts=%s left=%s flips=%d"
              % (json.dumps(a.get("counts", {}), ensure_ascii=False),
                 a.get("left_team"), len(a.get("flips", []))), flush=True)

    out["totals"] = {"n_clips": len(clips), "n_player_rows": n_player,
                     "n_staff_rows": n_staff}
    (RESULTS / "player_class.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print("RESULT_JSON %s" % (RESULTS / "player_class.json"))
    print("CLASSIFY_DONE %d %d %d" % (len(clips), n_player, n_staff))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
