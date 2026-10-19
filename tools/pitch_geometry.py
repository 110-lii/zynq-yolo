"""球场几何（白线 / 中圈 / 罚球弧 / 球门）检测与**几何自洽性**验证。

为什么需要这个工具
------------------
判罚系统需要一个**绝对参考系**：出界要球门线与边线，越位要中线与最后一名防守球员，
禁区要禁区线。没有场地几何，「球在画面偏左」是没有意义的描述。

为什么主判据不是「检出多少条线」
--------------------------------
广告牌边缘、看台栏杆、球门网、白袜子都能让 Hough 报出长直线。更关键的是：
**四条直线在投影上永远可以被某个单应解释成一个矩形**（4 条线 = 8 个约束 = 单应
的 8 个自由度，恰好定解）。所以「这两条看起来像边线」这件事本身**没有判别力**。

真正的约束是：这个单应还必须同时是一个**刚体相机**的成像，
    H = K [r1 r2 t],  K = diag(f, f, 1) 且主点在像心
即 r1、r2 必须是 3D 正交单位向量。这一条不是自由度，是**额外的两个标量约束**，
它给出两个**互相独立**的 f 估计：

    e1: h1'^T w h2' = 0                    -> f_a
    e2: h1'^T w h1' = h2'^T w h2'          -> f_b

两者之差就是「边线与球门线在地面上是否真的近似 90 度」的**残差**，
而且它没有被写进拟合，所以不循环。再叠加：
  - 反解出的相机高度必须在 5~60 m（物理）；
  - **留出结构**：中线 / 禁区线 / 球门区线 / 中圈 / 罚球弧从未参与拟合，
    模型必须把它们也投影到白线掩膜上；
  - 中圈 + 罚球弧是地面上的圆，投影成圆锥曲线，广告牌不可能伪造。

负对照
------
  a) 只保留图像上半部分（看台/观众/广告）跑同一套检测；
  b) 水平翻转 + 噪声；
  c) 扫多组关键阈值（8 组 × 2，共 16 个变体）。

算力
----
分阶段计时。最终要落到 Zynq-7020 的 PL（53 200 LUT6 / 220 DSP / 4.9 Mb BRAM）。

用法
----
    D:\\python\\python.exe tools\\pitch_geometry.py            # 40 帧 + 负对照 + 扫描
    ... --split test                     # 只跑 10 帧留出集
    ... --no-controls --no-sweep         # 快速单跑
    ... --viz                            # 输出人眼复核图 data/results/viz/pitch/

末行 `PITCH_OK <n>` / `PITCH_FAIL <原因>`。
"""
import sys

sys.dont_write_bytecode = True

import argparse
import json
import math
import time
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

import cv2  # noqa: E402
import eval_calib_set as ecs  # noqa: E402

OUT_JSON = REPO / "data" / "results" / "pitch_geometry.json"
VIZ_DIR = REPO / "data" / "results" / "viz" / "pitch"
CALIB_DIR = REPO / "data" / "samples" / "calib_real" / "frames_calib"
TEST_DIR = REPO / "data" / "samples" / "calib_real" / "frames_test"

# ---------------------------------------------------------------- 渲染配色
# 沿用 tools/draw_detections.py 的语义：绿=好的、红=另一类、品红=第三类、
# 橙=MISS/提示、黄=次阈值/弱证据。
C_OK = (80, 220, 80)       # 绿：边线
C_GOAL = (60, 60, 245)     # 红：球门线
C_HALF = (230, 60, 230)    # 品红：中线
C_PEN = (0, 165, 255)      # 橙：禁区线
C_AREA = (0, 235, 255)     # 黄：球门区线
C_CIRC = (255, 200, 0)     # 青：中圈 / 罚球弧
C_SEG = (235, 235, 235)    # 白：未归类的原始线段
C_TXT = (255, 255, 255)

# ---------------------------------------------------------------- 球场真值（IFAB）
PITCH_L = 105.0   # 球门线到球门线，m
PITCH_W = 68.0    # 边线到边线，m
GOAL_W = 7.32
PEN_DEPTH = 16.5
PEN_HALF_W = 20.16
AREA_DEPTH = 5.5
AREA_HALF_W = 9.16
CIRCLE_R = 9.15
SPOT_D = 11.0

# 横向线（法向 = x 方向）的标准位置：反解出的位置必须落在这些值上
B_CANON = (0.0, 5.5, 16.5, 52.5, 88.5, 94.0, 105.0)

# 横向线中「一定在地面上、且不易被广告牌伪造」的几条 —— 用于**挑选假设**
SELECT_NAMES = ("halfway", "pen_L", "pen_R", "area_L", "area_R")
# 圆弧 —— 作为**纯留出**判决项，从不参与假设挑选
DECIDE_CLS = "circ"

CLS_COLOR = {"touch": C_OK, "goal": C_GOAL, "half": C_HALF,
             "pen": C_PEN, "area": C_AREA, "circ": C_CIRC}


@dataclass
class Params:
    """全部阈值集中在这里，`--sweep` 只改这里的字段（可追溯）。"""
    # --- 草掩膜
    hsv_lo: tuple = (30, 40, 25)
    hsv_hi: tuple = (100, 255, 255)
    grass_close: int = 15
    grass_dilate: int = 11
    # --- 白线掩膜：局部对比度 top-hat + 相对低饱和
    tophat_ksz: int = 13
    tophat_min: int = 10
    sat_max: int = 110
    sat_rel: float = 0.80
    v_min: int = 65
    # --- Hough
    hough_thresh: int = 24
    hough_min_len: int = 38
    hough_max_gap: int = 16
    seg_support: float = 0.55      # 线段上落在白线掩膜的比例
    # --- 边线对（两条长且近似平行、中间是草坪）
    touch_ang_max: float = 22.0
    touch_sep_min: float = 22.0
    touch_xspan_min: float = 0.45  # 这一对在画面横向上的合并跨度（占画面宽）
    grass_between_min: float = 0.45
    max_a: int = 8
    max_pairs: int = 4             # 送进拟合的边线对候选数（由留出直线裁决）
    # --- 横向线候选（与边线明显不平行、且真的穿过两条边线）
    trans_ang_min: float = 18.0
    trans_ang_max: float = 90.0
    max_b: int = 6
    # --- 几何自洽判据
    f_min: float = 400.0
    f_max: float = 3000.0
    self_rel_max: float = 0.15     # 两个独立 f 估计的相对差（自洽性残差）
    cam_h_min: float = 5.0
    cam_h_max: float = 60.0
    touch_cov_min: float = 0.55
    select_cov_min: float = 0.30   # 留出直线（中线/禁区/球门区）覆盖度
    arc_cov_min: float = 0.25      # 纯留出圆弧覆盖度
    rms_max: float = 3.0
    min_line_px: float = 14.0
    cheap_step: float = 9.0        # 假设挑选阶段的投影重采样步长（px）


# ---------------------------------------------------------------- 基础工具

def _put(img, text, org, color=C_TXT, scale=0.42, thick=1):
    cv2.putText(img, text, (org[0] + 1, org[1] + 1), cv2.FONT_HERSHEY_SIMPLEX,
                scale, (0, 0, 0), thick + 2, cv2.LINE_AA)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


def grass_mask(bgr, p):
    """草坪掩膜：HSV 相对阈值 + 最大连通域 + 填洞。

    为什么不用固定亮度：白天/阴天/夜场三类帧的草 V 中位数在 132~198 之间摆动
    （实测），固定亮度阈值必然在一类帧上崩掉。这里只用色相/饱和度这对
    对照明不敏感的量，再用最大连通域把看台、跑道、替补席切掉。
    """
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)
    g = ((h >= p.hsv_lo[0]) & (h <= p.hsv_hi[0]) &
         (s >= p.hsv_lo[1]) & (v >= p.hsv_lo[2])).astype(np.uint8)
    g = cv2.morphologyEx(g, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(g, 8)
    if n <= 1:
        return np.zeros(g.shape, np.uint8)
    k = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    m = (lab == k).astype(np.uint8) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (p.grass_close, p.grass_close)))
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled = np.zeros_like(m)
    if cnts:
        cv2.drawContours(filled, cnts, -1, 255, -1)
    return filled


def white_mask(bgr, grass, p):
    """白线掩膜：局部亮度 top-hat（相对）+ 相对低饱和 + 草坪邻域内。

    top-hat = V - open(V) 只保留**比邻域亮**的细结构，因此对夜场整幅偏暗免疫。
    饱和度用「相对邻域均值」而不是绝对值，因为夜场草坪的绝对 S 整体下滑。
    草坪边界向外 dilate 一点，否则正好压在边线上的白线会被自己人切掉。
    """
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    s = hsv[..., 1]
    v = hsv[..., 2]
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (p.tophat_ksz, p.tophat_ksz))
    top = cv2.morphologyEx(v, cv2.MORPH_TOPHAT, ker)
    smean = cv2.blur(s.astype(np.float32), (p.tophat_ksz, p.tophat_ksz))
    w = ((top >= p.tophat_min) & (s <= p.sat_max) &
         (s <= smean * p.sat_rel) & (v >= p.v_min)).astype(np.uint8) * 255
    roi = cv2.dilate(grass, cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (p.grass_dilate, p.grass_dilate)))
    w = cv2.bitwise_and(w, roi)
    return cv2.morphologyEx(w, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))


def hough_segments(mask, p):
    """HoughLinesP -> [(x1,y1,x2,y2,len,ang_deg,mid_x,mid_y)]，ang 归一到 [0,180)。"""
    lines = cv2.HoughLinesP(mask, 1, np.pi / 180.0, p.hough_thresh,
                            minLineLength=p.hough_min_len, maxLineGap=p.hough_max_gap)
    if lines is None:
        return []
    # opencv 5.0 返回 (N,4)，4.5 返回 (N,1,4) —— reshape 兜住两种
    arr = np.asarray(lines, dtype=np.float64).reshape(-1, 4)
    segs = []
    for x1, y1, x2, y2 in arr:
        dx, dy = x2 - x1, y2 - y1
        L = float(math.hypot(dx, dy))
        if L < p.hough_min_len:
            continue
        ang = math.degrees(math.atan2(dy, dx)) % 180.0
        segs.append([float(x1), float(y1), float(x2), float(y2), L, ang,
                     0.5 * (x1 + x2), 0.5 * (y1 + y2)])
    segs.sort(key=lambda s: -s[4])
    return segs


def seg_support(mask, s, n=48):
    """线段上落在掩膜内的像素比例 —— 用来踢掉「靠间隙连起来的」假长线。"""
    xs = np.linspace(s[0], s[2], n)
    ys = np.linspace(s[1], s[3], n)
    xi = np.clip(xs.astype(np.int32), 0, mask.shape[1] - 1)
    yi = np.clip(ys.astype(np.int32), 0, mask.shape[0] - 1)
    return float((mask[yi, xi] > 0).mean())


def keep_supported(segs, mask, p):
    out = [s for s in segs if seg_support(mask, s) >= p.seg_support]
    out.sort(key=lambda s: -s[4])
    return out


def merge_collinear(segs, p, ang_tol=3.0, off_tol=4.0):
    """共线且相邻的线段合并（Hough 会把一条边线拆成十几段），用 PCA 重拟合端点。

    不合并的话「最长线段」只是整条边线的 1/4，近端/远端的判断就会错。
    """
    n = len(segs)
    if n < 2:
        return segs
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        a, b = find(i), find(j)
        if a != b:
            parent[b] = a

    A = np.array([s[:6] for s in segs], dtype=np.float64)
    for i in range(n):
        d = np.abs(((A[i:, 5] - A[i, 5] + 90.0) % 180.0) - 90.0)
        for jj in np.nonzero(d <= ang_tol)[0]:
            j = int(jj) + i
            if j == i:
                continue
            th = math.radians(A[i, 5])
            dv = np.array([math.cos(th), math.sin(th)])
            nv = np.array([-dv[1], dv[0]])
            p0 = A[i, 0:2]
            q0, q1 = A[j, 0:2] - p0, A[j, 2:4] - p0
            if max(abs(float(q0 @ nv)), abs(float(q1 @ nv))) > off_tol:
                continue
            t0, t1 = float(q0 @ dv), float(q1 @ dv)
            lo, hi = min(t0, t1), max(t0, t1)
            if lo > A[i, 4] + 4 * off_tol or hi < -4 * off_tol:
                continue
            union(i, j)

    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    out = []
    for _k, idx in groups.items():
        if len(idx) == 1:
            out.append(segs[idx[0]])
            continue
        pts = np.concatenate([A[idx][:, 0:2], A[idx][:, 2:4]], 0)
        c = pts.mean(0)
        _u, _s, vv = np.linalg.svd(pts - c, full_matrices=False)
        d = vv[0]
        t = (pts - c) @ d
        e0, e1 = c + d * t.min(), c + d * t.max()
        L = float(np.linalg.norm(e1 - e0))
        ang = math.degrees(math.atan2(e1[1] - e0[1], e1[0] - e0[0])) % 180.0
        out.append([float(e0[0]), float(e0[1]), float(e1[0]), float(e1[1]), L, ang,
                    0.5 * (e0[0] + e1[0]), 0.5 * (e0[1] + e1[1])])
    out.sort(key=lambda s: -s[4])
    return out


# ---------------------------------------------------------------- 直线 -> 单应

def seg_line(s):
    """线段 -> 齐次直线 (a,b,c)，a^2+b^2=1。"""
    x1, y1, x2, y2 = s[0], s[1], s[2], s[3]
    a, b = y2 - y1, x1 - x2
    c = x2 * y1 - x1 * y2
    nn = math.hypot(a, b)
    if nn < 1e-9:
        return None
    return (a / nn, b / nn, c / nn)


def seg_ang_diff(a, b):
    """两条线段方向的锐角差（度）。"""
    return float(abs(((a[5] - b[5] + 90.0) % 180.0) - 90.0))


def _intersect(s1, s2):
    """两条无限长直线的交点；近平行返回 None。"""
    x1, y1, x2, y2 = s1[0], s1[1], s1[2], s1[3]
    x3, y3, x4, y4 = s2[0], s2[1], s2[2], s2[3]
    d = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(d) < 1e-6:
        return None
    a = x1 * y2 - y1 * x2
    b = x3 * y4 - y3 * x4
    px = (a * (x3 - x4) - (x1 - x2) * b) / d
    py = (a * (y3 - y4) - (y1 - y2) * b) / d
    if not (np.isfinite(px) and np.isfinite(py)):
        return None
    if abs(px) > 1e5 or abs(py) > 1e5:
        return None
    return (float(px), float(py))


def fit_h_ground(corrs):
    """由「球场地面直线 <-> 图像直线」对应求 H（9 个未知量，尺度自由，DLT + SVD）。

    corrs: [(image_line, [(X,Y), (X,Y)]), ...]，每条对应给 2 行。
    4 条线 = 8 行，H 有 8 个自由度（9 个元素除掉尺度），所以恰好定解；
    多于 4 条时是过定最小二乘（用于精修）。

    **不要用 h33 = 1 归一化**：横向摄像机的 h33 常常接近 0，强制成 1 会把
    误差放大到 H 的其它元素上（实测 match_004 的 h11 涨到 22.6）。
    这里让 h33 自由，用 ||h|| = 1 定尺度。

    注意：投影意义下任意 4 条直线都能被某个 H 映射成矩形，
    所以 H 本身没有判别力 —— 判别力全在 focal_from_h 与**留出**结构上。
    """
    rows = []
    for l, pts in corrs:
        if l is None:
            continue
        a, b, c = l
        for (X, Y) in pts:
            rows.append([a * X, a * Y, a, b * X, b * Y, b, c * X, c * Y, c])
    if len(rows) < 8:
        return None
    A = np.asarray(rows, dtype=np.float64)
    # 列归一化改善条件数（点坐标可达 105 m，直线系数 ~1e-2）
    nrm = np.linalg.norm(A, axis=0)
    nrm[nrm < 1e-12] = 1.0
    A = A / nrm
    if np.linalg.matrix_rank(A) < 8:
        return None
    _u, _s, vt = np.linalg.svd(A, full_matrices=True)
    h = vt[-1] / nrm          # 把列缩放还原回原始变量
    H = h.reshape(3, 3)
    nn = np.linalg.norm(H)
    if not np.isfinite(nn) or nn < 1e-12:
        return None
    return H / nn


def focal_from_h(H, cx, cy):
    """H = lam * K [r1 r2 t] 的正交 + 等长约束 -> f 与**正交残差**。

    r1、r2 是 H 的**列**（不是行）。把主点平移到原点：L = T H = lam * diag(f,f,1)[r1 r2 t]，
    于是 col0 = lam*(f r1x, f r1y, r1z)，col1 = lam*(f r2x, f r2y, r2z)。令 u = 1/f^2：

      e1（r1 ⊥ r2）: (c0x c1x + c0y c1y) u + c0z c1z = 0
      e2（|r1|=|r2|）: (c0x^2+c0y^2-c1x^2-c1y^2) u + (c0z^2-c1z^2) = 0

    两式都是 u 的**一次**式，所以叠起来 lstsq 一次解出 u；两个方程不可能同时被满足时，
    残差 ||A u + b|| / ||b|| 就是「这四条线在地面上是否真的构成直角矩形」的度量。
    拟合 H 时只用了 8 行点-线约束，**没有**用这两式，所以残差不是自证。

    用 lstsq 而不是分别解两个 sqrt：分别解时任一式的分母为 0（横向摄像机常见，
    此时世界 X 轴的消失点在无穷远）就会整个判定失败，而那种帧其实仍然可解。
    """
    T = np.array([[1.0, 0.0, -cx], [0.0, 1.0, -cy], [0.0, 0.0, 1.0]])
    L = T @ H
    nn = np.linalg.norm(L)
    if not np.isfinite(nn) or nn < 1e-12:
        return None, None
    L = L / nn
    c0 = L[:, 0]
    c1 = L[:, 1]
    a1 = c0[0] * c1[0] + c0[1] * c1[1]
    b1 = c0[2] * c1[2]
    a2 = c0[0] ** 2 + c0[1] ** 2 - c1[0] ** 2 - c1[1] ** 2
    b2 = c0[2] ** 2 - c1[2] ** 2
    A = np.array([[a1], [a2]], dtype=np.float64)
    b = np.array([b1, b2], dtype=np.float64)
    nb = float(np.linalg.norm(b))
    scale = max(nb, float(np.linalg.norm(A)))
    if scale < 1e-14:
        return None, None
    if np.linalg.norm(A) < 1e-14 * scale:      # 两式都退化 -> 无法定 f
        return None, None
    u, *_ = np.linalg.lstsq(A, -b, rcond=None)
    u = float(u[0])
    if not np.isfinite(u) or u <= 0.0:
        return None, None
    f = 1.0 / math.sqrt(u)
    resid = A[:, 0] * u + b
    rel = float(np.linalg.norm(resid) / nb) if nb > 1e-14 else float("inf")
    return f, rel


def cam_height_h(H, f, cx, cy):
    """H = lam * K [r1 r2 t] -> 相机中心高度（球场系 z），物理上必须 5~60 m。

    注意 K^-1 H = [r1 r2 t] 已经成立，**不要**再左乘主点平移矩阵 T
    （T 只在 focal_from_h 里用，因为那里要把 K 化成 diag(f,f,1)）。
    """
    K_inv = np.array([[1.0 / f, 0.0, -cx / f], [0.0, 1.0 / f, -cy / f], [0.0, 0.0, 1.0]])
    M = K_inv @ H                # = lam * [r1 r2 t]
    lam = np.linalg.norm(M[:, 0])
    if not np.isfinite(lam) or lam < 1e-12:
        return None
    r1 = M[:, 0] / lam
    r2 = M[:, 1] / lam
    t = M[:, 2] / lam
    r3 = np.cross(r1, r2)
    R = np.stack([r1, r2, r3], 1)
    if abs(np.linalg.det(R) - 1.0) > 0.5:      # 不是旋转矩阵 -> 假解
        return None
    C = -R.T @ t
    return float(C[2])


# ---------------------------------------------------------------- 边线对 / 横向线

def _lerp(seg, t):
    return (seg[0] + (seg[2] - seg[0]) * t, seg[1] + (seg[3] - seg[1]) * t)


def grass_between(grass, s1, s2, n=25, m=7):
    """两条线之间的竖直带上有多少比例是草坪 —— 球场内部是草，看台/广告牌不是。

    这是挑边线对时最便宜也最有力的一条先验：广告牌上下的「长直线」之间
    不会有 68 m 宽的草坪。
    """
    h, w = grass.shape[:2]
    tot = good = 0
    for i in range(n):
        t = i / (n - 1.0)
        x1, y1 = _lerp(s1, t)
        x2, y2 = _lerp(s2, t)
        for j in range(m):
            u = (j + 0.5) / m
            x = x1 + (x2 - x1) * u
            y = y1 + (y2 - y1) * u
            xi, yi = int(round(x)), int(round(y))
            if 0 <= xi < w and 0 <= yi < h:
                tot += 1
                good += 1 if grass[yi, xi] > 0 else 0
    return good / max(1, tot)


def pick_touchlines(segs, grass, p):
    """从线段里挑「近端 + 远端边线」这一对，返回 (near, far) 或 None。

    条件（全部是几何/物理的，不是「够长就行」）：
      - 方向近似平行（广角下两条边线的像面夹角仍可差十几度）；
      - 在像面上有足够的高度差（否则它们不是「近/远」两条）；
      - 两条线之间 68 m 宽的带子里主要是草坪；
      - 这一对合起来要**横扫画面大部分宽度**：边线是场上最长的两条直线，
        转播机位下它们必然横跨画面。广告牌/跑道那类短横条只占画面一侧，
        单靠 grass_between 挡不住（实测 match_020 一对 34 px 相距的广告牌
        边缘 gf=0.99 照样通过），必须再加这条尺度先验。
    """
    A = segs[:p.max_a]
    h, w = grass.shape[:2]
    best = None
    for i in range(len(A)):
        for j in range(i + 1, len(A)):
            if seg_ang_diff(A[i], A[j]) > p.touch_ang_max:
                continue
            if abs(A[i][7] - A[j][7]) < p.touch_sep_min:
                continue
            x0 = min(A[i][0], A[i][2], A[j][0], A[j][2])
            x1 = max(A[i][0], A[i][2], A[j][0], A[j][2])
            span = (min(x1, float(w)) - max(x0, 0.0)) / float(w)
            if span < p.touch_xspan_min:
                continue
            gf = grass_between(grass, A[i], A[j])
            if gf < p.grass_between_min:
                continue
            score = min(A[i][4], A[j][4]) * gf * span
            if best is None or score > best[0]:
                best = (score, A[i], A[j], gf)
    if best is None:
        return None, None, 0.0
    _s, a, b, gf = best
    near, far = (a, b) if a[7] > b[7] else (b, a)     # y 越大越近
    return near, far, gf


def pick_touchline_pairs(segs, grass, p, k=None):
    """返回**排好序的**边线对候选（不是只要一个）。

    为什么要多个：单帧里正确的边线对未必排第一（实测 match_020 广告牌
    边缘一度排第一）。与其靠调阈值赌第一名，不如把前 k 对都送去拟合，
    让留出直线的覆盖度来裁决 —— 判据本身有判别力（见单测），
    多试几对不会让错误假设通过，只会提高正确假设被考虑到的机会。
    """
    if k is None:
        k = p.max_pairs
    A = segs[:p.max_a]
    h, w = grass.shape[:2]
    cand = []
    for i in range(len(A)):
        for j in range(i + 1, len(A)):
            if seg_ang_diff(A[i], A[j]) > p.touch_ang_max:
                continue
            if abs(A[i][7] - A[j][7]) < p.touch_sep_min:
                continue
            x0 = min(A[i][0], A[i][2], A[j][0], A[j][2])
            x1 = max(A[i][0], A[i][2], A[j][0], A[j][2])
            span = (min(x1, float(w)) - max(x0, 0.0)) / float(w)
            if span < p.touch_xspan_min:
                continue
            gf = grass_between(grass, A[i], A[j])
            if gf < p.grass_between_min:
                continue
            cand.append((min(A[i][4], A[j][4]) * gf * span, A[i], A[j], gf))
    cand.sort(key=lambda q: -q[0])
    out = []
    for _s, a, b, gf in cand[:k]:
        near, far = (a, b) if a[7] > b[7] else (b, a)
        out.append((near, far, gf))
    return out


def pick_transverse(segs, near, far, p):
    """横向线候选：与边线明显不平行，且（延长后）真的穿过两条边线。

    注意这里**不要求**交点在画面里：近端边线常常在画面下缘被切掉，横向线
    与它的交点自然落在画面外。只要求「两条边线都被穿过」—— 这才是
    「这条线横跨球场」的几何含义。早期版本把交点限制在边线端点附近
    ±40 px 内，实测在 match_032 上把真正的球门线（ang=163.8，
    交点 (-477,301) 在画面外）判掉了。
    """
    mean_ang = 0.5 * (near[5] + far[5])
    h, w = p._wh if hasattr(p, "_wh") else (360, 640)
    x_lo, x_hi = -2.0 * w, 3.0 * w
    out = []
    for s in segs:
        if s is near or s is far:
            continue
        d = float(abs(((s[5] - mean_ang + 90.0) % 180.0) - 90.0))
        if not (p.trans_ang_min <= d <= p.trans_ang_max):
            continue
        if s[4] < 25.0:
            continue
        hit = 0
        for t in (near, far):
            q = _intersect(s, t)
            if q is not None and x_lo <= q[0] <= x_hi:
                hit += 1
        if hit < 2:
            continue
        out.append(s)
    out.sort(key=lambda s: -s[4])
    return out[:p.max_b]


# ---------------------------------------------------------------- 球场模型

def pitch_model_lines():
    """[(name, cls, [(x,y),...])]，单位 m。x 沿长度 0..105，y 沿宽度 0..68。"""
    L, W = PITCH_L, PITCH_W
    cy = W / 2.0
    out = [
        ("touch_a", "touch", [(0.0, 0.0), (L, 0.0)]),
        ("touch_b", "touch", [(0.0, W), (L, W)]),
        ("goal_a", "goal", [(0.0, 0.0), (0.0, W)]),
        ("goal_b", "goal", [(L, 0.0), (L, W)]),
        ("halfway", "half", [(L / 2, 0.0), (L / 2, W)]),
    ]
    for sx, s in ((0.0, 1.0), (L, -1.0)):
        tag = "L" if s > 0 else "R"
        xp = sx + s * PEN_DEPTH
        out += [
            ("pen_%s" % tag, "pen", [(xp, cy - PEN_HALF_W), (xp, cy + PEN_HALF_W)]),
            ("pen_n_%s" % tag, "pen", [(sx, cy - PEN_HALF_W), (xp, cy - PEN_HALF_W)]),
            ("pen_f_%s" % tag, "pen", [(sx, cy + PEN_HALF_W), (xp, cy + PEN_HALF_W)]),
        ]
        xa = sx + s * AREA_DEPTH
        out += [
            ("area_%s" % tag, "area", [(xa, cy - AREA_HALF_W), (xa, cy + AREA_HALF_W)]),
            ("area_n_%s" % tag, "area", [(sx, cy - AREA_HALF_W), (xa, cy - AREA_HALF_W)]),
            ("area_f_%s" % tag, "area", [(sx, cy + AREA_HALF_W), (xa, cy + AREA_HALF_W)]),
        ]
    return out


def pitch_model_arcs(step_m=0.7):
    """中圈 + 两个罚球弧（罚球弧只保留落在禁区线之外的部分）。"""
    out = []
    n = max(24, int(2 * math.pi * CIRCLE_R / step_m))
    a = np.linspace(0, 2 * math.pi, n, endpoint=False)
    out.append(("circle", "circ",
                [(PITCH_L / 2 + CIRCLE_R * math.cos(t), PITCH_W / 2 + CIRCLE_R * math.sin(t))
                 for t in a]))
    for sx, s in ((0.0, 1.0), (PITCH_L, -1.0)):
        tag = "L" if s > 0 else "R"
        cxx = sx + s * SPOT_D
        pts = [(cxx + CIRCLE_R * math.cos(t), PITCH_W / 2 + CIRCLE_R * math.sin(t))
               for t in a]
        pts = [(x, y) for (x, y) in pts if s * (x - (sx + s * PEN_DEPTH)) > 0]
        if len(pts) > 3:
            out.append(("arc_%s" % tag, "circ", pts))
    return out


MODEL_LINES = pitch_model_lines()
MODEL_ARCS = pitch_model_arcs()
FIT_NAMES = ("touch_a", "touch_b")
FIT_MODEL = [m for m in MODEL_LINES if m[0] in FIT_NAMES]
SELECT_MODEL = [m for m in MODEL_LINES if m[0] in SELECT_NAMES]


def project(H, pts):
    """球场坐标（m）-> 像素。H 把地面 (X,Y,1) 映到 (u,v,1)。"""
    P = np.asarray(pts, dtype=np.float64)
    X = np.stack([P[:, 0], P[:, 1], np.ones(len(P))], 1)
    x = (H @ X.T).T
    z = x[:, 2:3]
    z = np.where(np.abs(z) < 1e-12, 1e-12, z)
    return x[:, :2] / z


def _resample_px(uv, step_px=5.0, nmin=4, nmax=120):
    """把投影后的折线按**像素弧长**重采样 —— 否则远端线会被挤成几个点而虚高覆盖度。"""
    uv = np.asarray(uv, dtype=np.float64)
    if not np.isfinite(uv).all():
        return None
    if len(uv) == 2:
        L = float(np.linalg.norm(uv[1] - uv[0]))
        if L < 1e-6:
            return None
        n = int(np.clip(L / step_px, nmin, nmax))
        t = np.linspace(0.0, 1.0, n)
        return uv[0][None, :] + (uv[1] - uv[0])[None, :] * t[:, None], L
    d = np.linalg.norm(np.diff(uv, axis=0), axis=1)
    cum = np.concatenate([[0.0], np.cumsum(d)])
    L = float(cum[-1])
    if L < 1e-6:
        return None
    n = int(np.clip(L / step_px, nmin, nmax))
    t = np.linspace(0.0, L, n)
    xs = np.interp(t, cum, uv[:, 0])
    ys = np.interp(t, cum, uv[:, 1])
    return np.stack([xs, ys], 1), L


def score_model(H, dist, model, w, h, p, min_len_px=None, step=None):
    """模型投影 -> 到最近白线像素的距离。返回覆盖度 / RMS / 逐条覆盖度。

    覆盖度按**像素**统计（每条线在像面上的采样点数正比于它的像素长度），
    因此一条占满画面的大边线不会因为「米数少」而被远处的短线段稀释。

    同时返回 `coverage_all`：把 `None`（该线被甩出画面 / 太短）记作 0 分。
    `coverage` 只对**存活**线条取平均，所以一个把 3 条留出线全甩出画面的
    单应仍能拿到 coverage=1.0 —— 这正是之前选择器被骗的原因（实测：
    match_012 sel=1.0 而 touch=0.0）。判据必须用 `coverage_all`。
    """
    if min_len_px is None:
        min_len_px = p.min_line_px
    if step is None:
        step = 5.0
    per = {}
    hits = []
    for name, _cls, pts in model:
        rs = _resample_px(project(H, pts), step_px=step)
        if rs is None:
            per[name] = None
            continue
        uv, L = rs
        if L < min_len_px:
            per[name] = None
            continue
        ok = (uv[:, 0] >= 0) & (uv[:, 0] < w) & (uv[:, 1] >= 0) & (uv[:, 1] < h)
        if ok.sum() < 4:
            per[name] = None
            continue
        ui = np.clip(uv[ok, 0].astype(np.int32), 0, w - 1)
        vi = np.clip(uv[ok, 1].astype(np.int32), 0, h - 1)
        d = dist[vi, ui]
        per[name] = float((d <= 2.5).mean())
        hits.append(d)
    if not per:
        return dict(coverage=0.0, coverage_all=0.0, rms=99.0, per=per, n=0)
    cov_all = float(np.mean([0.0 if v is None else v for v in per.values()]))
    if not hits:
        return dict(coverage=0.0, coverage_all=cov_all, rms=99.0, per=per, n=0)
    d = np.concatenate(hits)
    return dict(coverage=float((d <= 2.5).mean()), coverage_all=cov_all,
                rms=float(np.sqrt((d ** 2).mean())), per=per, n=int(d.size))


def joint_key(fit, sel, p):
    """假设排序键：**合取**（拟合线自己也必须被解释），不是只看留出线。

    返回 (score, touch_min, sel_all)。score 用 min 而不是乘积：乘积会让
    「0.9 * 0.05」和「0.3 * 0.15」难以区分，而判据本来就是「两条都得成立」。
    """
    tv = [fit["per"].get(n) for n in FIT_NAMES]
    touch = min([0.0 if v is None else v for v in tv]) if tv else 0.0
    return float(min(touch, sel["coverage_all"])), float(touch), float(sel["coverage_all"])


def _joint(H, dist, w, h, p, step=None):
    """一次挑一个假设：返回 (合取键, 拟合线覆盖, 留出线覆盖_all)。"""
    if step is None:
        step = p.cheap_step
    fit = score_model(H, dist, FIT_MODEL, w, h, p, step=step)
    sel = score_model(H, dist, SELECT_MODEL, w, h, p, step=step)
    return joint_key(fit, sel, p)


# ---------------------------------------------------------------- 单帧主流程

def _hypotheses(near, far, bsegs, p):
    """枚举「哪条线段是哪条球场线」的假设。

    未知的只有两件事：哪条是近端边线（Y=0）哪条是远端（Y=68），
    以及每条横向线对应哪个标准 x。这里枚举「两条横向线」的组合，
    再由适配度（留出直线的覆盖度）挑一个 —— 挑错的分片构型解释不了别的线。
    """
    if len(bsegs) < 2:
        return
    lnear, lfar = seg_line(near), seg_line(far)
    if lnear is None or lfar is None:
        return
    for swap in (False, True):
        t_a, t_b = (lfar, lnear) if swap else (lnear, lfar)
        for i in range(len(bsegs)):
            for j in range(len(bsegs)):
                if i == j:
                    continue
                si, sj = bsegs[i], bsegs[j]
                if si[6] > sj[6]:
                    continue          # 图像上从左到右必须与球场 x 同序
                li, lj = seg_line(si), seg_line(sj)
                if li is None or lj is None:
                    continue
                for x1 in B_CANON:
                    for x2 in B_CANON:
                        if not (x2 > x1 + 1.0):
                            continue
                        yield dict(
                            swap=swap,
                            x=(x1, x2),
                            corrs=[(t_a, [(0.0, 0.0), (PITCH_L, 0.0)]),
                                   (t_b, [(0.0, PITCH_W), (PITCH_L, PITCH_W)]),
                                   (li, [(x1, 0.0), (x1, PITCH_W)]),
                                   (lj, [(x2, 0.0), (x2, PITCH_W)])])


def analyse_frame(bgr, p):
    h, w = bgr.shape[:2]
    tim = {}

    t0 = time.perf_counter()
    gm = grass_mask(bgr, p)
    tim["grass_ms"] = (time.perf_counter() - t0) * 1e3

    t0 = time.perf_counter()
    wm = white_mask(bgr, gm, p)
    tim["white_ms"] = (time.perf_counter() - t0) * 1e3

    t0 = time.perf_counter()
    raw = hough_segments(wm, p)
    segs = merge_collinear(keep_supported(raw, wm, p), p)
    segs = keep_supported(segs, wm, p)
    tim["hough_ms"] = (time.perf_counter() - t0) * 1e3

    res = dict(frame="", size=[w, h], grass_ratio=round(float((gm > 0).mean()), 3),
               mask_ratio=round(float((wm > 0).mean()), 4),
               n_hough=len(raw), n_seg=len(segs), n_b=0, n_hyp=0, asg=None,
               f=None, self_rel=None, cam_h=None,
               touch_cov=None, select_cov=None, arc_cov=None, circle_cov=None,
               goal_cov=None, rms=None, passed=False, passed_arc=False, reason="",
               tim_ms=tim, segs=[], goal=None)
    if len(segs) < 4:
        res["reason"] = "too_few_segments(%d)" % len(segs)
        res["tim_ms"]["total_ms"] = sum(tim.values())
        return res

    # --- 挑边线对（多对候选，由留出直线裁决）
    t0 = time.perf_counter()
    pairs = pick_touchline_pairs(segs, gm, p)
    if not pairs:
        res["reason"] = "no_touchline_pair"
        res["tim_ms"]["total_ms"] = sum(tim.values())
        return res
    res["grass_between"] = round(pairs[0][2], 3)
    res["n_pairs"] = len(pairs)
    dist = cv2.distanceTransform((wm == 0).astype(np.uint8), cv2.DIST_L2, 5)
    tim["pair_ms"] = (time.perf_counter() - t0) * 1e3

    # --- 假设枚举 + 用留出直线挑一个
    t0 = time.perf_counter()
    best = None
    nhyp = 0
    nb_max = 0
    best_key = -1.0
    for near, far, _gbf in pairs:
        bsegs = pick_transverse(segs, near, far, p)
        nb_max = max(nb_max, len(bsegs))
        if len(bsegs) < 2:
            continue
        for hyp in _hypotheses(near, far, bsegs, p):
            nhyp += 1
            H = fit_h_ground(hyp["corrs"])
            if H is None:
                continue
            key, touch, sel_all = _joint(H, dist, w, h, p)
            if key <= best_key:
                continue
            best_key = key
            best = dict(key=key, H=H, hyp=hyp, near=near, far=far, bsegs=bsegs,
                        touch=touch, sel_all=sel_all)
    res["n_b"] = nb_max
    res["n_hyp"] = nhyp
    tim["fit_ms"] = (time.perf_counter() - t0) * 1e3
    if best is None:
        res["reason"] = "no_transverse_lines(%d)" % nb_max
        res["tim_ms"]["total_ms"] = sum(tim.values())
        return res
    bsegs = best["bsegs"]

    # --- 精修：把**所有**横向候选线按最近的 B_CANON 位置并入最小二乘
    t0 = time.perf_counter()
    H = best["H"]
    hyp = best["hyp"]
    x1, x2 = hyp["x"]
    corrs = list(hyp["corrs"])
    for s in bsegs:
        li = seg_line(s)
        if li is None:
            continue
        # 用当前 H 反推这条线在地面上的 x：投 (0,34) 与 (105,34) 得直线，
        # 与线段求交后再反投影到地面 —— 这里只需要一个近似的 x 排序，
        # 直接取候选 x 中使模型线离该线段最近的那个。
        bestx, bd = None, 1e9
        for xc in B_CANON:
            mu = project(H, np.array([[xc, 0.0], [xc, PITCH_W]]))
            d = float(np.min(np.linalg.norm(mu - np.array([s[6], s[7]]), axis=1)))
            if d < bd:
                bd, bestx = d, xc
        if bd < 40.0 and bestx not in (x1, x2):
            corrs.append((li, [(bestx, 0.0), (bestx, PITCH_W)]))
    H2 = fit_h_ground(corrs)
    if H2 is not None:
        k2, t2, s2 = _joint(H2, dist, w, h, p)
        if k2 >= best["key"]:
            H = H2
    tim["refine_ms"] = (time.perf_counter() - t0) * 1e3

    # --- 判据（全部与拟合无关的量）
    t0 = time.perf_counter()
    f, rel = focal_from_h(H, w / 2.0, h / 2.0)
    res["f"] = round(f, 1) if f else None
    res["self_rel"] = round(rel, 4) if rel is not None else None
    ch = cam_height_h(H, f, w / 2.0, h / 2.0) if f else None
    res["cam_h"] = round(ch, 2) if ch is not None else None

    full = score_model(H, dist, MODEL_LINES, w, h, p)
    arcs = score_model(H, dist, MODEL_ARCS, w, h, p)
    sel = score_model(H, dist, SELECT_MODEL, w, h, p)
    tim["score_ms"] = (time.perf_counter() - t0) * 1e3

    per = full["per"]
    tv = [per.get(n) for n in FIT_NAMES if per.get(n) is not None]
    res["touch_cov"] = round(float(np.min(tv)), 3) if tv else None
    res["select_cov"] = round(float(sel["coverage"]), 3)
    av = [v for v in arcs["per"].values() if v is not None]
    res["arc_cov"] = round(float(np.mean(av)), 3) if av else None
    res["circle_cov"] = (round(arcs["per"]["circle"], 3)
                         if arcs["per"].get("circle") is not None else None)
    gv = [per.get("goal_a"), per.get("goal_b")]
    gv = [v for v in gv if v is not None]
    res["goal_cov"] = round(float(max(gv)), 3) if gv else None
    res["rms"] = round(full["rms"], 2)
    res["asg"] = "%g/%g" % (x1, x2) if not hyp["swap"] else "%g/%g(sw)" % (x1, x2)
    res["per_line"] = {k: (round(v, 3) if v is not None else None) for k, v in per.items()}
    res["per_arc"] = {k: (round(v, 3) if v is not None else None)
                      for k, v in arcs["per"].items()}

    # --- 线段归类（仅用于着色与人工复核，不参与判定）
    labels = []
    for s in segs[:10]:
        mid = np.array([s[6], s[7]])
        bk = None
        for nm, cl, pl in MODEL_LINES:
            mu = project(H, np.asarray(pl, dtype=np.float64))
            dd = float(np.min(np.linalg.norm(mu - mid, axis=1)))
            if bk is None or dd < bk[0]:
                bk = (dd, nm, cl)
        labels.append(dict(nm=bk[1], cls=bk[2], d=round(bk[0], 1)))
    res["seg_class"] = labels

    # --- T1（主判据）：边线 + 单应必须是刚体相机 + 留出直线被解释
    ok_touch = res["touch_cov"] is not None and res["touch_cov"] >= p.touch_cov_min
    ok_self = (rel is not None and rel <= p.self_rel_max)
    ok_f = (f is not None and p.f_min <= f <= p.f_max)
    ok_h = (res["cam_h"] is not None and p.cam_h_min <= res["cam_h"] <= p.cam_h_max)
    ok_sel = res["select_cov"] >= p.select_cov_min
    ok_rms = res["rms"] <= p.rms_max
    res["passed"] = bool(ok_touch and ok_self and ok_f and ok_h and ok_sel and ok_rms)
    res["passed_arc"] = bool(res["passed"] and res["arc_cov"] is not None
                             and res["arc_cov"] >= p.arc_cov_min)
    if res["passed"]:
        res["reason"] = "ok"
    elif not ok_touch:
        res["reason"] = "touchline_unsupported(%s)" % res["touch_cov"]
    elif not ok_self:
        res["reason"] = "not_orthogonal(rel=%s)" % res["self_rel"]
    elif not ok_f:
        res["reason"] = "f_implausible(%s)" % res["f"]
    elif not ok_h:
        res["reason"] = "camera_height_implausible(%s)" % res["cam_h"]
    elif not ok_sel:
        res["reason"] = "holdout_lines_unsupported(%s)" % res["select_cov"]
    else:
        res["reason"] = "residual(rms=%s)" % res["rms"]
    res["H"] = [[round(float(v), 6) for v in row] for row in H]
    res["tim_ms"]["total_ms"] = sum(tim.values())
    return res


# ---------------------------------------------------------------- 球门

def goal_candidates(bgr):
    """盲检门框：两根**竖直**白柱 + 柱顶之间的横向白带（横梁）。

    只认「两柱 + 横梁」三者同时成立。单根白杆可能是角旗杆、广告牌边框或
    球门网拉杆 —— 不做横梁校验就会把它们当成门柱。
    """
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    s = hsv[..., 1]
    v = hsv[..., 2]
    white = ((v >= 145) & (s <= 75)).astype(np.uint8) * 255
    vert = cv2.morphologyEx(white, cv2.MORPH_OPEN,
                            cv2.getStructuringElement(cv2.MORPH_RECT, (1, 13)))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(vert, 8)
    posts = []
    for i in range(1, n):
        x, y = int(stats[i, cv2.CC_STAT_LEFT]), int(stats[i, cv2.CC_STAT_TOP])
        ww, hh = int(stats[i, cv2.CC_STAT_WIDTH]), int(stats[i, cv2.CC_STAT_HEIGHT])
        if hh < 20 or ww > 9 or hh / max(1, ww) < 2.5:
            continue
        posts.append((x + ww / 2.0, float(y), float(y + hh), float(ww), float(hh)))
    posts.sort(key=lambda q: -q[4])
    posts = posts[:8]
    best = None
    for i in range(len(posts)):
        for j in range(i + 1, len(posts)):
            x1, y1, _b1, _w1, h1 = posts[i]
            x2, y2, _b2, _w2, h2 = posts[j]
            dx = abs(x2 - x1)
            if not (16 <= dx <= 300):
                continue
            if abs(y1 - y2) > 0.4 * max(h1, h2):
                continue
            hh = max(h1, h2)
            if not (0.35 < hh / dx < 8.0):
                continue
            ybar = int(round(min(y1, y2) + 2))
            ya, yb = max(0, ybar - 3), min(white.shape[0], ybar + 4)
            xa, xb_ = int(min(x1, x2)) + 3, int(max(x1, x2)) - 3
            if xb_ - xa < 6 or ya >= yb:
                continue
            cover = float((white[ya:yb, xa:xb_] > 0).mean())
            if cover < 0.28:
                continue
            cand = dict(x1=round(x1, 1), x2=round(x2, 1), y=round(min(y1, y2), 1),
                        h=round(hh, 1), span=round(dx, 1), bar=round(cover, 2))
            if best is None or cand["h"] * cand["bar"] > best["h"] * best["bar"]:
                best = cand
    return best, len(posts)


def goal_from_model(H, w, h):
    """用拟合出的单应**预测**门柱位置（不是检测，用于和盲检对照）。"""
    out = []
    for gx in (0.0, PITCH_L):
        for yy in (PITCH_W / 2 - GOAL_W / 2, PITCH_W / 2 + GOAL_W / 2):
            uv = project(H, np.array([[gx, yy]]))[0]
            if 0 <= uv[0] < w and 0 <= uv[1] < h:
                out.append([round(float(uv[0]), 1), round(float(uv[1]), 1), round(gx)])
    return out


# ---------------------------------------------------------------- 负对照 / 参数扫描

def strip_upper(bgr, frac=0.45):
    """负对照 a)：只保留图像上半部分（看台/观众/广告），模拟「非球场区域」。"""
    h = bgr.shape[0]
    return bgr[0:int(h * frac)].copy()


def add_noise_flip(bgr, seed=0):
    """负对照 b)：水平翻转 + 高斯噪声。"""
    rng = np.random.default_rng(seed)
    im = cv2.flip(bgr, 1)
    noise = rng.normal(0, 6, im.shape)
    return np.clip(im.astype(np.float32) + noise, 0, 255).astype(np.uint8)


def run_set(paths, p, viz=None, tag="", quiet=False):
    out = []
    for k, path in enumerate(paths, 1):
        bgr = cv2.imread(str(path))
        if bgr is None:
            continue
        r = analyse_frame(bgr, p)
        r["frame"] = path.name
        g, npst = goal_candidates(bgr)
        r["goal"] = g
        r["n_posts"] = npst
        if r.get("H"):
            r["goal_pred"] = goal_from_model(np.array(r["H"]), bgr.shape[1], bgr.shape[0])
        out.append(r)
        if not quiet:
            print("  [%2d/%2d] %-14s grs=%.2f seg=%-2d B=%-2d hyp=%-4d asg=%-9s "
                  "f=%-6s r=%-6.3f h=%-6s tch=%-5s sel=%-5s arc=%-5s "
                  "rms=%-4s %s %s"
                  % (k, len(paths), path.name, r["grass_ratio"], r["n_seg"], r["n_b"],
                     r["n_hyp"], r["asg"], r["f"], r["self_rel"] or 0.0, r["cam_h"],
                     r["touch_cov"], r["select_cov"],
                     r["arc_cov"], r["rms"], "PASS" if r["passed"] else "FAIL",
                     r["reason"]))
        if viz is not None:
            viz(path, bgr, r, p, tag)
    return out


# ---------------------------------------------------------------- 渲染

def render(path, bgr, r, p, tag=""):
    h, w = bgr.shape[:2]
    sc = 2
    im = cv2.resize(bgr, (w * sc, h * sc), interpolation=cv2.INTER_CUBIC)
    canvas = np.full((im.shape[0] + 34 + 20, im.shape[1], 3), 24, np.uint8)
    canvas[34:34 + im.shape[0]] = im
    im = canvas
    dy = 34

    for k, s in enumerate(r.get("segs", [])):
        lab = r["seg_class"][k] if k < len(r.get("seg_class", [])) else None
        col = CLS_COLOR.get(lab["cls"], C_SEG) if lab else C_SEG
        cv2.line(im, (int(s["x1"] * sc), int(s["y1"] * sc) + dy),
                 (int(s["x2"] * sc), int(s["y2"] * sc) + dy), col, 2, cv2.LINE_AA)
        mx = int((s["x1"] + s["x2"]) * 0.5 * sc)
        my = int((s["y1"] + s["y2"]) * 0.5 * sc) + dy
        txt = "%s %.0f" % (lab["nm"] if lab else "-", s["len"])
        _put(im, txt, (mx - 26, my), col, 0.32)

    H = r.get("H")
    if H:
        Hm = np.array(H, dtype=np.float64)
        for nm, cls, pts in MODEL_LINES:
            uv = project(Hm, np.asarray(pts, dtype=np.float64))
            if not np.isfinite(uv).all():
                continue
            cv2.line(im, (int(uv[0, 0] * sc), int(uv[0, 1] * sc) + dy),
                     (int(uv[1, 0] * sc), int(uv[1, 1] * sc) + dy),
                     CLS_COLOR[cls], 1, cv2.LINE_AA)
        for nm, cls, pts in MODEL_ARCS:
            uv = project(Hm, np.asarray(pts, dtype=np.float64))
            for a, b in zip(uv[:-1], uv[1:]):
                if np.isfinite(a).all() and np.isfinite(b).all() and np.hypot(*(a - b)) < 120:
                    cv2.line(im, (int(a[0] * sc), int(a[1] * sc) + dy),
                             (int(b[0] * sc), int(b[1] * sc) + dy), C_CIRC, 1, cv2.LINE_AA)
        for gp in r.get("goal_pred", []):
            cv2.circle(im, (int(gp[0] * sc), int(gp[1] * sc) + dy), 6, C_CIRC, 2, cv2.LINE_AA)
            _put(im, "post", (int(gp[0] * sc) + 7, int(gp[1] * sc) + dy), C_CIRC, 0.32)

    g = r.get("goal")
    if g:
        cv2.rectangle(im, (int(g["x1"] * sc) - 4, int(g["y"] * sc) + dy - 4),
                      (int(g["x2"] * sc) + 4, int((g["y"] + g["h"]) * sc) + dy + 4),
                      C_HALF, 2)
        _put(im, "GOAL span=%.0f bar=%.2f" % (g["span"], g["bar"]),
             (int(g["x1"] * sc), int(g["y"] * sc) + dy - 8), C_HALF, 0.4)

    head = ("%s %s | asg=%s f=%s(fa=%s fb=%s rel=%s) h=%s tch=%s sel=%s arc=%s "
            "rms=%s | %s"
            % (r["frame"], "PASS" if r["passed"] else "FAIL", r["asg"], r["f"],
               r["f_a"], r["f_b"], r["self_rel"], r["cam_h"], r["touch_cov"],
               r["select_cov"], r["arc_cov"], r["rms"], r["reason"]))
    _put(im, head, (8, 22), C_OK if r["passed"] else C_PEN, 0.50)
    _put(im, "green=touchline red=goal line magenta=halfway orange=penalty box "
             "yellow=goal area cyan=circle/arc  white=unmatched segment  "
             "t=%.0fms" % r["tim_ms"].get("total_ms", 0),
         (8, h * sc + dy + 14), C_TXT, 0.40, 1)
    out = VIZ_DIR / ("%s%s.png" % (tag + "_" if tag else "", Path(r["frame"]).stem))
    cv2.imwrite(str(out), im)
    return out


def contact_sheet(paths, out, cols=4, width=620):
    tiles = []
    for p_ in paths:
        im = cv2.imread(str(p_))
        if im is None:
            continue
        hh = int(round(im.shape[0] * width / im.shape[1]))
        tiles.append(cv2.resize(im, (width, hh), interpolation=cv2.INTER_AREA))
    if not tiles:
        return None
    th, tw = tiles[0].shape[:2]
    rows = (len(tiles) + cols - 1) // cols
    sheet = np.full((rows * th, cols * tw, 3), 24, np.uint8)
    for i, t in enumerate(tiles):
        rr, cc = divmod(i, cols)
        sheet[rr * th:(rr + 1) * th, cc * tw:(cc + 1) * tw] = t
    cv2.imwrite(str(out), sheet)
    return sheet


# ---------------------------------------------------------------- 汇总

def summarise(rows):
    n = len(rows)
    npass = sum(1 for r in rows if r["passed"])
    npass_arc = sum(1 for r in rows if r.get("passed_arc"))
    reasons = {}
    for r in rows:
        key = r["reason"].split("(")[0]
        reasons[key] = reasons.get(key, 0) + 1
    fv = [r["f"] for r in rows if r.get("f")]
    return dict(n=n, n_pass=npass, rate=round(npass / max(1, n), 3),
                n_pass_arc=npass_arc, reasons=reasons,
                f_median=(round(float(np.median(fv)), 1) if fv else None))


def slim(r):
    """JSON 里只留判据用的数（几 KB 量级），原始线段/单应靠 --viz 看图。"""
    return dict(frame=r["frame"], passed=r["passed"], passed_arc=r.get("passed_arc"),
                reason=r["reason"], asg=r.get("asg"), f=r.get("f"), f_a=r.get("f_a"),
                f_b=r.get("f_b"), self_rel=r.get("self_rel"), cam_h=r.get("cam_h"),
                touch=r.get("touch_cov"), sel=r.get("select_cov"), arc=r.get("arc_cov"),
                circle=r.get("circle_cov"), goal=r.get("goal_cov"), rms=r.get("rms"),
                n_seg=r.get("n_seg"), n_b=r.get("n_b"), n_hyp=r.get("n_hyp"),
                grass=r.get("grass_ratio"), goal_det=r.get("goal"),
                ms=round(r["tim_ms"].get("total_ms", 0), 1))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="all", choices=["all", "test", "calib"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--viz", action="store_true")
    ap.add_argument("--no-controls", action="store_true")
    ap.add_argument("--no-sweep", action="store_true")
    ap.add_argument("--out", default=str(OUT_JSON))
    args = ap.parse_args(argv)

    p = Params()
    paths = []
    if args.split in ("all", "test"):
        paths += ecs.list_images(TEST_DIR)
    if args.split in ("all", "calib"):
        paths += ecs.list_images(CALIB_DIR)
    if args.limit:
        paths = paths[:args.limit]
    if not paths:
        print("PITCH_FAIL no frames")
        return 1

    VIZ_DIR.mkdir(parents=True, exist_ok=True)
    viz = render if args.viz else None
    print("PITCH: %d frames  tophat_min=%d sat_max=%d self_rel=%.2f sel_min=%.2f"
          % (len(paths), p.tophat_min, p.sat_max, p.self_rel_max, p.select_cov_min))
    rows = run_set(paths, p, viz=viz)
    base = summarise(rows)
    result = dict(
        generated=time.strftime("%Y-%m-%dT%H:%M:%S"),
        params={k: (list(v) if isinstance(v, tuple) else v)
                for k, v in p.__dict__.items()},
        criterion=("T1 pass requires: (1) touchline pair supported; "
                   "(2) the 4-line homography decomposes as K[r1 r2 t] with a single "
                   "focal length -- |f_a-f_b|/f <= self_rel_max is the measured "
                   "touchline-vs-goal-line orthogonality residual, and it was NOT "
                   "used in the fit; (3) camera height 5-60 m; "
                   "(4) HOLDOUT lines (halfway / penalty box / goal area) supported; "
                   "(5) reprojection RMS <= rms_max. "
                   "T2 additionally requires the holdout ARCS (circle/penalty arc)."),
        frames=[slim(r) for r in rows], summary=base)

    if not args.no_controls:
        print("\n=== control a) upper strip (stands / ad boards only) ===")
        up = []
        for path in paths:
            bgr = cv2.imread(str(path))
            if bgr is None:
                continue
            r = analyse_frame(strip_upper(bgr), p)
            r["frame"] = path.name
            up.append(r)
            print("  %-14s seg=%-2d B=%-2d f=%-7s tch=%-5s sel=%-5s %s %s"
                  % (path.name, r["n_seg"], r["n_b"], r["f"], r["touch_cov"],
                     r["select_cov"], "PASS" if r["passed"] else "FAIL", r["reason"]))
        print("\n=== control b) hflip + noise ===")
        fl = []
        for path in paths:
            bgr = cv2.imread(str(path))
            if bgr is None:
                continue
            r = analyse_frame(add_noise_flip(bgr), p)
            r["frame"] = path.name
            fl.append(r)
            print("  %-14s seg=%-2d B=%-2d f=%-7s tch=%-5s sel=%-5s %s %s"
                  % (path.name, r["n_seg"], r["n_b"], r["f"], r["touch_cov"],
                     r["select_cov"], "PASS" if r["passed"] else "FAIL", r["reason"]))
        result["control_upper"] = summarise(up)
        result["control_flip"] = summarise(fl)
        result["control_upper_frames"] = [slim(r) for r in up]
        result["control_flip_frames"] = [slim(r) for r in fl]

    if not args.no_sweep:
        print("\n=== control c) parameter sensitivity ===")
        variants = [
            ("base", p),
            ("tophat_min=7", replace(p, tophat_min=7)),
            ("tophat_min=14", replace(p, tophat_min=14)),
            ("sat_max=90", replace(p, sat_max=90)),
            ("sat_max=130", replace(p, sat_max=130)),
            ("seg_support=0.45", replace(p, seg_support=0.45)),
            ("seg_support=0.70", replace(p, seg_support=0.70)),
            ("grass_between_min=0.30", replace(p, grass_between_min=0.30)),
            ("grass_between_min=0.60", replace(p, grass_between_min=0.60)),
            ("trans_ang_min=12", replace(p, trans_ang_min=12.0)),
            ("trans_ang_min=24", replace(p, trans_ang_min=24.0)),
            ("self_rel_max=0.10", replace(p, self_rel_max=0.10)),
            ("self_rel_max=0.22", replace(p, self_rel_max=0.22)),
            ("select_cov_min=0.20", replace(p, select_cov_min=0.20)),
            ("select_cov_min=0.40", replace(p, select_cov_min=0.40)),
        ]
        sweep = []
        for name, pv in variants:
            rs = []
            for path in paths:
                bgr = cv2.imread(str(path))
                if bgr is None:
                    continue
                r = analyse_frame(bgr, pv)
                r["frame"] = path.name
                rs.append(r)
            s = summarise(rs)
            s["variant"] = name
            sweep.append(s)
            print("  %-24s pass=%d/%d %s" % (name, s["n_pass"], s["n"], s["reasons"]))
        result["sensitivity"] = sweep

    test_names = {q.name for q in ecs.list_images(TEST_DIR)}
    tp = [r for r in rows if r["frame"] in test_names]
    ms = [r["tim_ms"].get("total_ms", 0) for r in rows]
    result["conclusion"] = dict(
        pass_all=base["n_pass"], n_all=base["n"], pass_rate=base["rate"],
        pass_arc_all=base["n_pass_arc"],
        pass_test=sum(1 for r in tp if r["passed"]), n_test=len(tp),
        pass_arc_test=sum(1 for r in tp if r.get("passed_arc")),
        test_frames=[slim(r) for r in tp],
        goal_detected=sum(1 for r in rows if r.get("goal")),
        goal_test=sum(1 for r in tp if r.get("goal")),
        circle_frames=sum(1 for r in rows if (r.get("arc_cov") or 0) >= 0.25),
        mean_ms=round(float(np.mean(ms)), 1),
        p95_ms=round(float(np.percentile(ms, 95)), 1),
        stage_ms={k: round(float(np.mean([r["tim_ms"].get(k, 0) for r in rows])), 2)
                  for k in ("grass_ms", "white_ms", "hough_ms", "pair_ms",
                            "fit_ms", "refine_ms", "score_ms")})
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    print("\nPITCH summary: %d/%d pass (%.1f%%)  arc %d/%d  test %d/%d  goal-det %d  "
          "mean %.0f ms" % (base["n_pass"], base["n"], 100.0 * base["rate"],
                            base["n_pass_arc"], base["n"],
                            result["conclusion"]["pass_test"],
                            result["conclusion"]["n_test"],
                            result["conclusion"]["goal_detected"],
                            result["conclusion"]["mean_ms"]))
    if args.viz:
        contact_sheet(sorted(VIZ_DIR.glob("match_*.png")), VIZ_DIR / "_sheet_pitch.png")
    print("wrote %s" % args.out)
    print("PITCH_OK %d" % base["n_pass"] if base["n_pass"] else "PITCH_FAIL none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
