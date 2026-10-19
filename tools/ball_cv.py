"""传统 CV 单独检测足球 —— 可行性原型（运动残差 + 外观先验 + 时空关联）。

为什么做这个
------------
COCO 预训练 YOLOv8n 在这批真实转播画面里基本看不见球（320 输入 0/40 帧、640 输入
3/40 帧检到 sports ball；float32 参考同样 0，所以不是量化问题）。真实画面里球在
1280x720 上只有 4~12 像素——这是「小目标 + 模型」的根因。所以这里换一条路：
不靠学习模型，只用运动/外观先验把球挑出来。

三个手段（都不是我发明的，但组合方式决定了能不能用）
----------------------------------------------------
1. 运动残差：25 fps 下球滚动/飞行时帧差在球位置留小亮点。但转播机位会跟拍，
   帧差会全是草坪边缘 ⇒ 必须先把全局运动（平移+缩放+微旋转）估出来、把上一帧
   warp 到当前帧再作差。估计用相位相关给初值，再用 ECC 仿射细化；ECC 失败就退回
   纯平移（并记录退化次数，因为这会让残差变脏）。
2. 外观先验：球是高亮度、低饱和度、近似圆的小 blob ⇒ HSV 阈值 + 连通域 + 面积/
   长宽比/填充率/圆度筛选。白线、白球衣、广告牌、观众席亮点都是干扰项。
3. 时空关联：逐帧候选用「匀速预测 + 硬门限」做二阶（加速度）约束的动态规划串成
   轨迹，只输出轨迹、不输出单帧点。单帧点没有判别力（白线到处都是），轨迹才有。

判据（★ 判据必须先能失败，否则等于没测）
----------------------------------------
主判据 M1：某帧所在片段里存在一条候选轨迹，长度 >= 10 帧、覆盖该帧、相邻帧位移
不超过 3 倍球直径（球直径取 8 px ⇒ 24 px）。逐帧报 pass/fail。

配套对照（缺一个结论就无效）：
  null           —— 不产生任何候选。应当 0/10；如果它不是 0，说明计分逻辑有 bug。
  static_center  —— 每帧都报画面正中一个点。它**必然** 10/10：任何静止点都能满足
                    「长且平滑」。这个对照的作用是证明 M1 单独**没有判别力**，
                    所以真正的结论必须看图（viz/）+ 看轨迹的支撑强度。
  shuffle        —— 把片段帧序随机打乱后重跑。如果通过率不下降，说明「连续性」
                    判据是废的。
  synthetic      —— 在真实帧上合成一个 8 px 白球（平滑运动），跑同一套流程。
                    这是算法的**上限**。注意：它的通过**不是**看「跑出来的最优轨迹」，
                    而是对着注入球的真值量三个数（否则它只是把同一条静止轨迹再跑
                    一遍，永远 10/10、什么都没证明）：
                      cand_hit  —— 注入球是否进了候选集（中心距 <= 6 px）
                      chain_pass—— 通过该候选的轨迹是否满足 M1
                      auto_pick —— 自动选出的最优轨迹是否就是注入球
                    chain_pass 高而 auto_pick 低 ⇒ 特征与关联都够用，坏在「排序/
                    挑哪条」。三个都低 ⇒ 特征就不行。
  ablate_*       —— 去掉运动项 / 去掉外观项，看哪一路在贡献候选。

实测结论（2026-10-19，40 帧标定集里的 10 个 test 帧，详见 data/results/ball_cv.json）
-------------------------------------------------------------------------------
M1 10/10 通过，但**全部是假绿**：自动选出的最优轨迹 0/10 落在球上，10/10 落在
记分牌、广告牌文字、球员白鞋/手套上（看图确认）。原因分三层：
  1) 候选层：8 px 的球确实被外观通道抓住（合成 8 px 球进候选 10/10、中心距 0.16 px），
     但同帧还有 ~40~200 个同规格白块，球在外观排序里只排 19~28 名；
  2) 轨迹层：DP 最大化「累计得分」⇒ 存在 100 帧的静止白块（记分牌冒号、广告字）
     永远赢过只存在 20~40 帧的真球；0.4 px/帧 的「轨迹」被判为完美平滑；
  3) 判据层：M1 只约束长度与平滑，不约束「是球」，连「每帧报画面正中」都能 10/10。
另外球在这批画面里表观尺寸从 8 px 到 25 px（被球员抱起时）横跨 3 倍，固定尺寸窗口
不可能同时覆盖；白鞋/白袜/门将手套在 HSV 里与球的分布直接重叠。
⇒ 结论：**单独靠传统 CV 在这批 1280x720 转播画面上定位球，不可行**。要往前走，至少
需要其中一个外部约束：更高分辨率/更近机位（球 >= 20 px）、由球员/场地质检出的 ROI
（球永远在球员脚边或手上）、或训练小目标检测器。

用法
----
    D:\\python\\python.exe tools\\ball_cv.py                 # 全量（10 帧 × 7 个变体）
    ... --only 2                # 只跑第 2 个 test 帧（调试）
    ... --variants full,shuffle # 只跑某些变体
    ... --seed-xy 1:903,585     # 人工给第 1 帧球的坐标，单独测「跟踪机制能不能跟住球」
    ... --no-viz                # 不出图
    ... --help

产物
----
    data/results/ball_cv.json            结论（入库，几 KB）
    data/results/viz/ball_cv/*.png       人眼复核图（不入库，.gitignore 已排除）
    .research/ball_cv_clips/<name>_p*.jpg 片段缓存（不入库）

★ 源视频是第三方转播内容，只读不复制进仓库；片段缓存放在 .research/（不入 git）。

末行打印 `BALL_CV_OK <n>` 或 `BALL_CV_FAIL <原因>`。
"""
import argparse
import json
import math
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8")

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VIDEO = Path(r"D:\yt-dlp\downloads\migu_5106947698\migu_5106947698.mp4")
FFMPEG = Path(r"D:\yt-dlp\ffmpeg\ffmpeg.exe")
MANIFEST = ROOT / "data" / "samples" / "calib_real" / "MANIFEST.csv"
TEST_DIR = ROOT / "data" / "samples" / "calib_real" / "frames_test"
OUT_JSON = ROOT / "data" / "results" / "ball_cv.json"
VIZ_DIR = ROOT / "data" / "results" / "viz" / "ball_cv"
CACHE_DIR = ROOT / ".research" / "ball_cv_clips"

FPS = 25.0
SRC_W, SRC_H = 1280, 720

# ---- 判据（这些是「筛选条件」，不是「调参旋钮」）--------------------------------
BALL_D_PX = 8.0          # 实测球直径 4~12 px 的中位数估计（1280x720）
MAX_STEP = 3.0 * BALL_D_PX   # 24 px：相邻帧位移上限 = 3 倍球直径
MAX_ACCEL = 10.0         # px/frame^2：速度变化上限（超过就是「瞬移」而非运动）
TRACK_MIN_LEN = 10       # 轨迹长度判据
MAX_GAP = 1              # 允许的连续漏检帧数

# ---- 检测参数 ----------------------------------------------------------------
# 外观：球在草坪上是「高 V + 低 S」的小亮点。阈值不是我拍的，是从实测球像素定的：
# 球核 V≈248~252、S≈30~60，周围草坪 V≈110~160 ⇒ 用 V>=175 / S<=60 就能把球
# 从草坪里分出来，同时不会把天空/观众席的灰白也全端进来（V>=125 时白像素占 21%，
# 候选 380 个/帧，跟踪立刻失去意义；V>=175 后 90 个/帧）。
SAT_MAX = 60
VAL_MIN = 175
AREA_MIN = 5             # 8px 球的圆面积 ~50；核心亮块 40~90
AREA_MAX = 200           # 12px 球 ~113，留余量；再大就不是球（是衣/牌）
MAX_WH = 16              # 单边 > 16 px 的「白块」不是球（是线/衣/牌）
ASPECT_MAX = 2.0         # 长宽比
EXTENT_MIN = 0.50        # 面积/外接框面积：圆 ~0.785，细线远低
ROUND_MIN = 0.55         # 面积/(pi*(max(w,h)/2)^2)：圆 ~1.0
RING_MIN = 15.0          # 环对比度下限：球与外圈草坪的 V 差（实测 45~58，广告牌 90）
# 运动：warp 后的残差峰值。实测草坪纹理残差 p99≈18，球在 60~130。
MOT_THR = 25.0
TOP_K = 40               # 每帧只保留外观得分最高的 K 个候选（控制组合爆炸）
# 几何先验（--grass-prior）：候选中心 15px 邻域内必须有草。HSV 的 H 是 0~179。
GRASS_HUE = (28, 95)     # 草坪绿色在 OpenCV HSV 里大约落在这里
GRASS_SMIN = 40
GRASS_VMIN = 40
GRASS_ANN = (6, 11)      # 环半径 r0..r1（px）：球心到球缘是 4~6，所以环必须落在球外
GRASS_FRAC_MIN = 0.70    # 环内草占比下限。实测真球 0.84/1.00，假阳性 0.00~0.44

GATE_PX = MAX_STEP + 2.0 # 关联门限（略放宽 2 px，因为预测点本身有误差）
MIN_SCORE = 0.0

VARIANTS = ["full", "shuffle", "static_center", "null", "synthetic",
            "ablate_no_motion", "ablate_no_appearance"]


def _j(o):
    """json 兜底：numpy 标量不能直接 dump。"""
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    raise TypeError(f"not JSON serializable: {type(o)}")


def log(msg):
    print(msg, flush=True)


# =============================================================================
# 视频片段
# =============================================================================
def probe_video(path):
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return None
    info = dict(width=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                height=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                fps=float(cap.get(cv2.CAP_PROP_FPS)),
                frames=int(cap.get(cv2.CAP_PROP_FRAME_COUNT)))
    cap.release()
    return info


def extract_clip(video, t_center, half_s, cache, name, force=False):
    """抽 [t-half, t+half] 的帧到 cache，返回按时间排序的 jpg 列表。

    为什么多抽：MANIFEST 的 t_approx_s 是「关键帧序号比例 × 总时长」估的，
    实测偏差可达 ~2 s（见 report 里 t_manifest vs t_matched）。所以窗口放宽到
    ±3 s，再用画面匹配把真正的测试帧定位出来——否则「±2 s 片段」可能根本不含该帧。
    """
    if cache.exists() and not force:
        files = sorted(cache.glob("*.jpg"))
        if len(files) >= int(2 * half_s * FPS) - 2:
            return files
    if cache.exists():
        shutil.rmtree(cache)
    cache.mkdir(parents=True, exist_ok=True)
    cmd = [str(FFMPEG), "-hide_banner", "-loglevel", "error",
           "-ss", f"{max(t_center - half_s, 0.0):.3f}", "-i", str(video),
           "-t", f"{2 * half_s:.3f}", "-vsync", "0",
           "-vf", f"scale={SRC_W}:{SRC_H}",
           "-q:v", "2", str(cache / "%04d.jpg")]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + r.stderr[-600:])
    return sorted(cache.glob("*.jpg"))


def locate_center(files, ref_jpg):
    """在片段里找与标定帧最像的一帧（640x360 灰度 MAE）。返回 (idx, mae, all_mae)。"""
    ref = cv2.imread(str(ref_jpg), cv2.IMREAD_COLOR)
    refg = cv2.cvtColor(cv2.resize(ref, (640, 360), interpolation=cv2.INTER_AREA),
                        cv2.COLOR_BGR2GRAY).astype(np.float32)
    maes = []
    for f in files:
        a = cv2.imread(str(f), cv2.IMREAD_COLOR)
        g = cv2.cvtColor(cv2.resize(a, (640, 360), interpolation=cv2.INTER_AREA),
                         cv2.COLOR_BGR2GRAY).astype(np.float32)
        maes.append(float(np.abs(g - refg).mean()))
    best = int(np.argmin(maes))
    return best, maes[best], maes


# =============================================================================
# 全局运动
# =============================================================================
def prep_small(bgr, scale=0.5):
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    if scale != 1.0:
        g = cv2.resize(g, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return cv2.GaussianBlur(g, (5, 5), 1.2)


def phase_shift(a, b):
    """b 相对 a 的平移（亚像素）。返回 (dx,dy) 或 None。"""
    try:
        (dx, dy), resp = cv2.phaseCorrelate(np.float32(a), np.float32(b),
                                            cv2.createHanningWindow(a.shape[::-1], cv2.CV_32F))
    except cv2.error:
        return None, 0.0
    if not np.isfinite(dx) or not np.isfinite(dy):
        return None, 0.0
    return (float(dx), float(dy)), float(resp)


def _mae(a, b, mask=None):
    d = cv2.absdiff(a, b)
    return float(d.mean() if mask is None else cv2.mean(d, mask)[0])


def estimate_motion(prev_s, cur_s, allow_affine=True):
    """估 prev -> cur 的变换，返回 (M2x3(小图坐标), kind, cc)。

    ★ 坑：ECC 与 phaseCorrelate 的「warp 方向」在不同 OpenCV 版本/调用次序上语义
      容易搞反，网上示例两种写法都有。这里不猜方向，把候选变换逐一代进去 warp，
      谁把 prev 对齐得更好就用谁（代价只是多几次 warpAffine）。方向搞反时残差会
      **变大**而不是变小，所以这个自检不会把错的解当成对的。
    """
    h, w = cur_s.shape
    best_e = _mae(prev_s, cur_s)          # identity 的残差就是基准
    M = np.eye(2, 3, dtype=np.float32)
    kind, cc = "identity", 0.0

    sh, resp = phase_shift(prev_s, cur_s)
    if sh is not None and abs(sh[0]) < w / 4.0 and abs(sh[1]) < h / 4.0:
        for cand in (np.array([[1, 0, sh[0]], [0, 1, sh[1]]], np.float32),
                     np.array([[1, 0, -sh[0]], [0, 1, -sh[1]]], np.float32)):
            e = _mae(cv2.warpAffine(prev_s, cand, (w, h), flags=cv2.INTER_LINEAR,
                                    borderMode=cv2.BORDER_REPLICATE), cur_s)
            if e < best_e:
                best_e, M, kind, cc = e, cand, "trans", float(resp)

    if allow_affine:
        # ECC 仿射细化（带缩放/微旋转）。失败或没变好就保留平移解。
        try:
            crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 40, 1e-4)
            cc2, Mw = cv2.findTransformECC(prev_s, cur_s, M.copy(), cv2.MOTION_AFFINE,
                                           crit, None, 5)
            if np.isfinite(cc2) and cc2 > 0.3:
                for cand, tag in ((Mw.astype(np.float32), "affine"),
                                  (cv2.invertAffineTransform(Mw).astype(np.float32),
                                   "affine_inv")):
                    e = _mae(cv2.warpAffine(prev_s, cand, (w, h), flags=cv2.INTER_LINEAR,
                                            borderMode=cv2.BORDER_REPLICATE), cur_s)
                    if e < best_e:
                        best_e, M, kind, cc = e, cand, tag, float(cc2)
        except cv2.error:
            pass
    return M, kind, cc


def warp_gray_full(prev_g, M_small, scale):
    """把小图变换放大到全分辨率并 warp 上一帧。"""
    A = M_small.copy().astype(np.float64)
    A[0, 2] /= scale
    A[1, 2] /= scale
    return cv2.warpAffine(prev_g, A.astype(np.float32), (prev_g.shape[1], prev_g.shape[0]),
                          flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


# =============================================================================
# 候选
# =============================================================================
def comps(mask, area_min, area_max, max_wh, aspect_max, extent_min):
    n, lab, st, cen = cv2.connectedComponentsWithStats(mask, 8, cv2.CV_32S)
    out = []
    for i in range(1, n):
        x, y, w, h, area = (int(st[i, 0]), int(st[i, 1]), int(st[i, 2]),
                            int(st[i, 3]), int(st[i, 4]))
        if area < area_min or area > area_max:
            continue
        if w > max_wh or h > max_wh or w < 2 or h < 2:
            continue
        ar = w / float(h)
        if ar > aspect_max or ar < 1.0 / aspect_max:
            continue
        extent = area / float(w * h)
        if extent < extent_min:
            continue
        out.append(dict(x=float(cen[i][0]), y=float(cen[i][1]), xi=x, yi=y, w=w, h=h,
                        area=area, extent=extent, label=i))
    return out, lab


def _patch(a, b):
    """取 b 的外接框内的 a 子图（clamp 到边界，返回 None 表示空）。"""
    x0, y0 = max(b["xi"], 0), max(b["yi"], 0)
    x1, y1 = min(b["xi"] + b["w"], a.shape[1]), min(b["yi"] + b["h"], a.shape[0])
    if x1 <= x0 or y1 <= y0:
        return None
    return a[y0:y1, x0:x1]


def _rect_mean(ii, x0, y0, x1, y1):
    """积分图上的矩形均值（x1/y1 为开区间）。ii 是 cv2.integral 的结果（float64）。"""
    s = ii[y1, x1] - ii[y0, x1] - ii[y1, x0] + ii[y0, x0]
    n = max(1, (x1 - x0) * (y1 - y0))
    return s / n


def _ring_contrast(ii, b, pad=None):
    """球心亮度 - 外圈（球外一圈草坪/背景）亮度。

    这是把「高亮小圆点」和「亮区域里的小白块」（广告牌、观众席白衣、球场白线
    的交叉口）分开的唯一便宜手段：球在草坪上，外圈一定暗。
    """
    h, w = ii.shape[0] - 1, ii.shape[1] - 1
    x0, y0, x1, y1 = b["xi"], b["yi"], b["xi"] + b["w"], b["yi"] + b["h"]
    if pad is None:
        pad = max(3, max(b["w"], b["h"]))
    X0, Y0 = max(0, x0 - pad), max(0, y0 - pad)
    X1, Y1 = min(w, x1 + pad), min(h, y1 + pad)
    if X1 <= X0 or Y1 <= Y0:
        return 0.0
    inner = _rect_mean(ii, x0, y0, x1, y1)
    outer = _rect_mean(ii, X0, Y0, X1, Y1)
    na = max(1, (x1 - x0) * (y1 - y0))
    no = max(1, (X1 - X0) * (Y1 - Y0))
    if no <= na:
        return 0.0
    ring = (outer * no - inner * na) / (no - na)
    return float(inner - ring)


def grass_mask(hsv):
    """草坪掩膜（0/1 float32）。用途：把「候选四周都是草」当成一个便宜的几何先验。

    为什么需要它：实测的假绿来源里有一半根本不长在场上 —— 记分牌冒号、左上角
    「独播」水印、观众席亮块、场边广告牌的字缝。这些白块在 HSV 外观上和球一模
    一样（都满足 S<=60、V>=175、近圆、小），唯一区别是它们周围不是草。
    球本身是白的（不绿），但球在场上，半径 6~11 px 的环里几乎全是草。
    """
    H, S, V = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    g = ((H >= GRASS_HUE[0]) & (H <= GRASS_HUE[1]) & (S >= GRASS_SMIN)
         & (V >= GRASS_VMIN))
    return g.astype(np.float32)


def grass_annulus(gra, shape):
    """每个像素「半径 GRASS_ANN 环内草的占比」。用两次 filter2D（方框外接圆盘核）。

    实测该特征在人眼确认的球上是 0.84 / 1.00，而在广告牌、白袜、红球衣白点上
    是 0.00~0.44（见 .research/bc_attach.py 的输出），所以它是有判别力的，不是
    拍脑袋加的旋钮。
    """
    r0, r1 = GRASS_ANN
    k1 = _disk(r1)
    k0 = _disk(max(1, r0 - 1))
    s1 = cv2.filter2D(gra, -1, k1, borderType=cv2.BORDER_CONSTANT)
    s0 = cv2.filter2D(gra, -1, k0, borderType=cv2.BORDER_CONSTANT)
    return (s1 - s0) / float(k1.sum() - k0.sum())


def _disk(r):
    d = 2 * r + 1
    k = np.zeros((d, d), np.float32)
    cv2.circle(k, (r, r), r, 1.0, -1)
    return k


def detect_candidates(bgr, dmot, mode, gra=None):
    """一帧的候选。mode: full / no_motion / no_appearance。

    返回 (cands, stats)。cand: x,y,w,h,app,mot_peak,ring,score

    设计上刻意**不把运动当硬门限**：球在死球（任意球摆球、界外球、门将持球）时
    是真的静止的，而判罚辅助恰恰最关心这些时刻。所以运动只做加分项，运动通道
    单独成路（no_appearance）用来量化「只靠帧差能走多远」。
    gra 非 None 时启用「四周都是草」的几何先验（--grass-prior），它是这一版唯一
    能把球和广告牌/白袜区分开的特征，所以默认开启。
    """
    stats = {}
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    S, V = hsv[:, :, 1], hsv[:, :, 2]
    white = ((S <= SAT_MAX) & (V >= VAL_MIN)).astype(np.uint8)
    white = cv2.morphologyEx(white, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    stats["white_px"] = int(white.sum())
    if gra is not None:
        stats["grass_frac"] = round(float(gra.mean()), 3)
    ii = cv2.integral(V.astype(np.float64))

    cands = []
    n_app_blobs = 0
    if mode in ("full", "no_motion"):
        blobs, lab = comps(white, AREA_MIN, AREA_MAX, MAX_WH, ASPECT_MAX, EXTENT_MIN)
        n_app_blobs = len(blobs)
        for b in blobs:
            roundness = b["area"] / (math.pi * (max(b["w"], b["h"]) / 2.0) ** 2)
            if roundness < ROUND_MIN:
                continue
            if gra is not None:
                cy = min(max(int(b["y"]), 0), gra.shape[0] - 1)
                cx = min(max(int(b["x"]), 0), gra.shape[1] - 1)
                if gra[cy, cx] < GRASS_FRAC_MIN:
                    continue
            ring = _ring_contrast(ii, b)
            if ring < RING_MIN:
                continue
            pm = _patch(dmot, b)
            pl = _patch(lab, b)
            if pm is None or pl is None:
                continue
            m = pl == b["label"]
            if not m.any():
                continue
            mot_peak = float(pm[m].max())
            vpatch = _patch(V, b)
            v_mean = float(vpatch[m].mean()) if vpatch is not None else 0.0
            bright = max(0.0, (v_mean - VAL_MIN) / 70.0)
            rnd = roundness / 0.85
            ext = b["extent"] / 0.78
            rngv = min(1.5, ring / 60.0)
            motv = min(1.5, mot_peak / 60.0)
            app = 0.55 * min(1.0, bright) + 0.20 * min(1.0, rnd) \
                + 0.10 * min(1.0, ext) + 0.15 * min(1.0, rngv)
            # 各分项都截到 1.5（不是 1.0）：完全 clip 到 1 会让一堆候选并列 1.000，
            # DP 只能靠链长破并列、评分等于失效；但完全不截也不行 —— 实测里
            # 「深色背景上的白字/水印」环对比度能到 150（rngv=2.5），把真正的球
            # （rngv≈0.8）压到第 9~11 名。截到 1.5 既保留排序又不让单项独裁。
            if mode == "full":
                score = 1.0 * rngv + 1.0 * motv + 0.6 * rnd + 0.4 * ext + 0.3 * bright
            else:  # no_motion: 只靠外观
                score = 1.0 * rngv + 0.6 * rnd + 0.4 * ext + 0.3 * bright
            cands.append(dict(x=b["x"], y=b["y"], w=b["w"], h=b["h"], app=round(app, 3),
                              mot_peak=round(mot_peak, 1), ring=round(ring, 1),
                              score=round(float(score), 3)))
    n_mot_blobs = 0
    if mode in ("full", "no_appearance"):
        m8 = (dmot >= MOT_THR).astype(np.uint8)
        m8 = cv2.dilate(m8, np.ones((2, 2), np.uint8))
        blobs, _ = comps(m8, 4, AREA_MAX, MAX_WH, 2.6, 0.30)
        n_mot_blobs = len(blobs)
        for b in blobs:
            pm = _patch(dmot, b)
            if pm is None:
                continue
            if gra is not None:
                cy = min(max(int(b["y"]), 0), gra.shape[0] - 1)
                cx = min(max(int(b["x"]), 0), gra.shape[1] - 1)
                if gra[cy, cx] < GRASS_FRAC_MIN:
                    continue
            peak = float(pm.max())
            cands.append(dict(x=b["x"], y=b["y"], w=b["w"], h=b["h"], app=0.0,
                              mot_peak=round(peak, 1), ring=0.0,
                              score=round(peak / 60.0, 3)))
    stats["n_app_blobs"] = n_app_blobs
    stats["n_mot_blobs"] = n_mot_blobs
    if len(cands) > TOP_K:
        cands.sort(key=lambda c: -c["score"])
        cands = cands[:TOP_K]
    stats["n_cand"] = len(cands)
    return cands, stats


# =============================================================================
# 关联
# =============================================================================
def dist(a, b):
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


def build_tracks(per_frame, n_frames, gate=GATE_PX, accel_max=MAX_ACCEL,
                 max_gap=MAX_GAP):
    """二阶（加速度）约束的 DP 关联。

    状态：每帧的每个候选节点，记录 (累计得分, 前驱节点, 链长, 上一帧速度)。
    转移硬门限：|v| <= gate 且 |v - v_prev| <= accel_max。跳跃（瞬移）直接被切断。
    """
    nodes = []   # nodes[t] = list of dict(cand..., prev=idx_into_nodes[t-1], len, val)
    for t in range(n_frames):
        cur = []
        for c in per_frame[t]:
            node = dict(c)
            node.update(prev=-1, prev2=-1, length=1, val=c["score"], vx=0.0, vy=0.0)
            cur.append(node)
        nodes.append(cur)
        if t == 0:
            continue
        prev = nodes[t - 1]
        for c in cur:
            best = None
            for j, p in enumerate(prev):
                d = dist(c, p)
                if d > gate:
                    continue
                if p["prev"] >= 0:
                    pp = nodes[t - 2][p["prev"]]
                    vpx, vpy = pp["x"] - p["x"], pp["y"] - p["y"]
                    vx, vy = p["x"] - c["x"], p["y"] - c["y"]
                    if math.hypot(vx - vpx, vy - vpy) > accel_max:
                        continue
                else:
                    vx, vy = p["x"] - c["x"], p["y"] - c["y"]
                val = p["val"] + c["score"] - 0.012 * d
                if best is None or val > best[0]:
                    best = (val, j, vx, vy)
            if best is not None:
                c["val"], c["prev"], c["vx"], c["vy"] = best[0], best[1], best[2], best[3]
                c["length"] = prev[best[1]]["length"] + 1
    return nodes


def chain(nodes, t, i):
    """从 nodes[t][i] 回溯出轨迹（按时间正序）。"""
    pts = []
    ct, ci = t, i
    while ct >= 0 and ci >= 0:
        p = nodes[ct][ci]
        pts.append(dict(t=ct, x=p["x"], y=p["y"], w=p.get("w", BALL_D_PX),
                        h=p.get("h", BALL_D_PX), score=p["score"],
                        app=p.get("app", 0.0), mot=p.get("mot_peak", 0.0)))
        ci = p["prev"]
        ct -= 1
    pts.reverse()
    return pts


def track_stats(pts):
    steps = [math.hypot(pts[i]["x"] - pts[i - 1]["x"], pts[i]["y"] - pts[i - 1]["y"])
             for i in range(1, len(pts))]
    accs = []
    for i in range(1, len(steps)):
        accs.append(abs(steps[i] - steps[i - 1]))
    # 方向变化也记一下（真球不会每帧掉头 180 度）
    angs = []
    for i in range(1, len(pts)):
        angs.append(math.atan2(pts[i]["y"] - pts[i - 1]["y"], pts[i]["x"] - pts[i - 1]["x"]))
    turns = [abs(math.degrees(math.atan2(math.sin(angs[i] - angs[i - 1]),
                                         math.cos(angs[i] - angs[i - 1]))))
             for i in range(1, len(angs))]
    return dict(
        length=len(pts),
        t0=pts[0]["t"], t1=pts[-1]["t"],
        gaps=sum(1 for i in range(1, len(pts)) if pts[i]["t"] - pts[i - 1]["t"] > MAX_GAP + 1),
        max_step=round(max(steps), 2) if steps else 0.0,
        mean_step=round(float(np.mean(steps)), 2) if steps else 0.0,
        path_len=round(float(sum(steps)), 1),
        max_accel=round(max(accs), 2) if accs else 0.0,
        median_turn_deg=round(float(np.median(turns)), 1) if turns else 0.0,
        mean_score=round(float(np.mean([p["score"] for p in pts])), 3),
        mean_app=round(float(np.mean([p["app"] for p in pts])), 3),
        mean_mot=round(float(np.mean([p["mot"] for p in pts])), 1),
    )


def judge(tk, center):
    """M1：长度 >= 10、覆盖中心帧、且无跳步。"""
    if tk is None:
        return False, "no track"
    if tk["length"] < TRACK_MIN_LEN:
        return False, f"len {tk['length']} < {TRACK_MIN_LEN}"
    if not (tk["t0"] <= center <= tk["t1"]):
        return False, "does not cover center frame"
    if tk["max_step"] > MAX_STEP:
        return False, f"jump {tk['max_step']:.1f}px > {MAX_STEP:.0f}px"
    if tk["max_accel"] > MAX_ACCEL:
        return False, f"accel {tk['max_accel']:.1f} > {MAX_ACCEL}"
    if tk["gaps"] > 0:
        return False, f"{tk['gaps']} gaps"
    return True, "ok"


# =============================================================================
# 单片段流程
# =============================================================================
def synth_path(n, idx_center, w, h, seed=7):
    """合成的平滑球轨迹：起点随机、速度约 10~18 px/frame、缓慢转向前进并回弹。"""
    rng = np.random.default_rng(seed)
    x, y = rng.uniform(0.2 * w, 0.8 * w), rng.uniform(0.25 * h, 0.75 * h)
    ang = rng.uniform(-math.pi, math.pi)
    spd = rng.uniform(10.0, 18.0)
    pts = {}
    for t in range(n):
        pts[t] = (x, y)
        ang += 0.06 * math.sin(t / 9.0 + seed)
        x += spd * math.cos(ang)
        y += spd * math.sin(ang)
        if not (0.1 * w < x < 0.9 * w):
            ang = math.pi - ang
        if not (0.1 * h < y < 0.9 * h):
            ang = -ang
    return pts


def draw_ball(img, x, y, d=BALL_D_PX):
    """在 4x 超采样小图上画抗锯齿白球再贴回，模拟 4~8 px 的球在压缩后的样子。"""
    r = max(2, int(round(d / 2.0)))
    pad = r + 3
    x0, y0 = int(round(x)) - pad, int(round(y)) - pad
    s = 4
    tile = img[max(y0, 0):y0 + 2 * pad, max(x0, 0):x0 + 2 * pad]
    if tile.size == 0:
        return
    big = cv2.resize(tile, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)
    cx, cy = (x - x0) * s, (y - y0) * s
    cv2.circle(big, (int(cx), int(cy)), int(r * s), (250, 250, 250), -1, cv2.LINE_AA)
    cv2.circle(big, (int(cx), int(cy)), int(r * s * 0.55), (215, 215, 215), -1, cv2.LINE_AA)
    small = cv2.resize(big, (tile.shape[1], tile.shape[0]), interpolation=cv2.INTER_AREA)
    img[max(y0, 0):y0 + 2 * pad, max(x0, 0):x0 + 2 * pad] = small


def run_clip(files, idx_center, cfg, variant, seed=0):
    """跑一条片段，返回 (result_dict, ctx)。

    variant 决定「候选从哪来 / 帧序是否打乱」，但判分逻辑完全一样。
    idx_center 是 files 里的**绝对**下标（由画面匹配定位出来的那一帧）。
    """
    n_total = len(files)
    half = int(cfg["half"] * FPS)
    lo = max(0, idx_center - half)
    hi = min(n_total, idx_center + half + 1)
    idxs = list(range(lo, hi))
    n = len(idxs)
    center = idx_center - lo
    if variant == "shuffle":
        rng = np.random.default_rng(seed)
        order = list(idxs)
        rng.shuffle(order)
        center = order.index(idx_center)
    else:
        order = idxs

    scale = 0.5
    per_frame = [[] for _ in range(n)]
    motion_log = []
    stats_log = []
    prev_s = None
    prev_g = None
    t_start = time.time()

    gr = {}  # 需要给 viz 用的中心帧
    sp = synth_path(n, center, SRC_W, SRC_H, seed=seed + 1) if variant == "synthetic" else None
    for k, fi in enumerate(order):
        bgr = cv2.imread(str(files[fi]), cv2.IMREAD_COLOR)
        if bgr is None:
            prev_s, prev_g = None, None
            continue
        if sp is not None:
            draw_ball(bgr, *sp[k])
        g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        gs = prep_small(bgr, scale)
        dmot = np.zeros_like(g, dtype=np.uint8)
        if prev_g is not None:
            M, kind, cc = estimate_motion(prev_s, gs, allow_affine=cfg["affine"])
            wa = warp_gray_full(prev_g, M, scale)
            dmot = cv2.absdiff(g, wa)
            motion_log.append(dict(k=k, dx=round(float(M[0, 2] / scale), 2),
                                   dy=round(float(M[1, 2] / scale), 2), kind=kind,
                                   cc=round(cc, 3)))
        # 消融必须真的换掉候选来源，否则三条曲线会一模一样（踩过：只改名字不改 mode，
        # 三个变体跑出完全相同的数字，看上去像"消融无影响"，实际是没生效）。
        mode = {"ablate_no_motion": "no_motion",
                "ablate_no_appearance": "no_appearance"}.get(variant, cfg["mode"])
        gra = None
        if cfg.get("grass"):
            hsv_f = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
            gra = grass_annulus(grass_mask(hsv_f), bgr.shape)
        cands, st = detect_candidates(bgr, dmot, mode, gra)
        per_frame[k] = cands
        stats_log.append(st)
        if k == center:
            gr["bgr"] = bgr.copy()
            gr["cands"] = cands
            gr["dmot"] = dmot.copy()
            gr["file"] = files[fi].name
        prev_s, prev_g = gs, g

    nodes = build_tracks(per_frame, n)
    # 覆盖中心帧的轨迹里挑「最优」。**怎么挑本身就是判据的一部分，必须先能失败**。
    # 踩过的坑：最初按「链最长」挑，于是永远挑中记分牌/广告牌上那个 101 帧都在的静止
    # 白块（链最长），而真球在片段里只存在 20~40 帧 ⇒ 自动选出的最优轨迹 0/10 落在
    # 球上，看上去像「特征不行」，其实是挑法自己把球排除了。DP 的目标函数是累计得分
    # (val)，就用 val 挑（这是 DP 的全局最优，可解释）；同时保留 mean_score / 链长
    # 两种挑法的结果做对照，免得把「挑法错」误读成「算法不行」。
    picks = []
    for i, node in enumerate(nodes[center]):
        pts = chain(nodes, center, i)
        tk = track_stats(pts)
        tk["val"] = round(float(node["val"]), 2)
        picks.append((float(node["val"]), tk, i, pts))
    best, best_i = None, -1
    if picks:
        # 只在「够长」的链里挑（长度是门槛，不是排序依据），再按 mean_score 排序。
        # 为什么不用 val（累计得分）：累计得分会奖励「活得久」，101 帧都在的水印/
        # 广告牌永远赢过只存在 31 帧的真球（实测 val 99.2 对 115.4 还能咬住，但把
        # 得分项截断后水印反超）。mean_score 回答的是「这条链上的东西像球吗」，
        # 这才是判据该问的问题。
        long_enough = [r for r in picks if r[1]["length"] >= TRACK_MIN_LEN] or picks
        bi = max(range(len(long_enough)), key=lambda r: (long_enough[r][1]["mean_score"],
                                                         long_enough[r][1]["length"]))
        best, best_i = long_enough[bi][1], long_enough[bi][2]
    # 对照挑法 1：链最长（修之前的行为，留作证据）
    best_longest = None
    if picks:
        bl = max(range(len(picks)), key=lambda r: (picks[r][1]["length"],
                                                   picks[r][1]["mean_score"]))
        best_longest = picks[bl][1]
    # 对照挑法 2：累计得分最高（DP 的全局最优）
    best_val = None
    if picks:
        bv = max(range(len(picks)), key=lambda r: picks[r][0])
        best_val = picks[bv][1]
    center_top = [(p[1]["length"], p[1]["mean_score"], p[2], p[3]) for p in picks]
    center_top.sort(key=lambda r: (-r[1], -r[0]))
    best_pts = chain(nodes, center, best_i) if best_i >= 0 else []

    longest = None
    for t in range(n):
        for i, node in enumerate(nodes[t]):
            if node["length"] >= TRACK_MIN_LEN:
                pts = chain(nodes, t, i)
                tk = track_stats(pts)
                if longest is None or tk["length"] > longest["length"]:
                    longest = tk
    passed, why = judge(best, center)
    # 可选：人工给定该帧球的真实位置，用来单独回答「跟踪机制本身能不能跟住球」。
    # 这是把「候选排序错」和「轨迹表达不出来」两类失败区分开的唯一便宜办法。
    seeded = None
    seed_xy = cfg.get("seed_xy")
    if seed_xy:
        if nodes[center]:
            si = min(range(len(nodes[center])),
                     key=lambda j: (nodes[center][j]["x"] - seed_xy[0]) ** 2
                     + (nodes[center][j]["y"] - seed_xy[1]) ** 2)
            spts = chain(nodes, center, si)
            st = track_stats(spts)
            st["seed_dist"] = round(math.hypot(spts[-1]["x"] - seed_xy[0],
                                               spts[-1]["y"] - seed_xy[1]), 2)
            st["pass"] = bool(st["length"] >= TRACK_MIN_LEN
                              and st["max_step"] <= MAX_STEP
                              and st["max_accel"] <= MAX_ACCEL)
            seeded = st
            best_pts = spts

    # 合成球对照：必须对着**注入球的真值**量，否则它只是把同一条静止轨迹再跑一遍，
    # 看起来 10/10 通过但其实根本没证明「球能被检到」。这里分两个数：
    #   cand_dist  注入球中心到最近候选的距离（<=6px 才算"进了候选集"）
    #   chain_pass 通过该候选的轨迹是否满足 M1（=跟踪机制的上限）
    #   auto_dist  自动选出的最优轨迹在中心帧距注入球多远（=排序有没有挑对）
    syn = None
    if sp is not None:
        gx, gy = sp[center]
        if nodes[center]:
            si = min(range(len(nodes[center])),
                     key=lambda j: (nodes[center][j]["x"] - gx) ** 2
                     + (nodes[center][j]["y"] - gy) ** 2)
            spts = chain(nodes, center, si)
            st = track_stats(spts)
            syn = dict(gt=[int(gx), int(gy)],
                       cand_dist=round(math.hypot(spts[-1]["x"] - gx,
                                                  spts[-1]["y"] - gy), 2),
                       chain_len=st["length"], chain_max_step=st["max_step"],
                       chain_mean_step=st["mean_step"], chain_mean_score=st["mean_score"],
                       chain_pass=bool(st["length"] >= TRACK_MIN_LEN
                                       and st["max_step"] <= MAX_STEP
                                       and st["max_accel"] <= MAX_ACCEL),
                       auto_dist=round(math.hypot(best_pts[-1]["x"] - gx,
                                                  best_pts[-1]["y"] - gy), 2)
                       if best_pts else -1.0)
            if syn["chain_pass"]:
                # 上限语义：只要注入的球能被串成合规轨迹，就说明流程本身没问题
                passed, why = True, "synthetic ball tracked"
    res = dict(
        pass_=bool(passed), reason=why,
        best=best, best_longest=best_longest, best_val=best_val, longest=longest,
        seeded=seeded, synthetic=syn,
        n_cand_median=int(np.median([s["n_cand"] for s in stats_log])) if stats_log else 0,
        n_cand_total=int(sum(s["n_cand"] for s in stats_log)),
        n_app_blobs_median=int(np.median([s["n_app_blobs"] for s in stats_log]))
        if stats_log else 0,
        n_mot_blobs_median=int(np.median([s["n_mot_blobs"] for s in stats_log]))
        if stats_log else 0,
        motion=dict(
            n=len(motion_log),
            mean_abs_dx=round(float(np.mean([abs(m["dx"]) for m in motion_log])), 2) if motion_log else 0.0,
            mean_abs_dy=round(float(np.mean([abs(m["dy"]) for m in motion_log])), 2) if motion_log else 0.0,
            max_abs_dx=round(float(max([abs(m["dx"]) for m in motion_log])), 2) if motion_log else 0.0,
            n_affine=sum(1 for m in motion_log if m["kind"] == "affine"),
            mean_cc=round(float(np.mean([m["cc"] for m in motion_log])), 3) if motion_log else 0.0,
        ),
        runtime_s=round(time.time() - t_start, 2),
        center=center, center_abs=idx_center, lo=lo, n_frames=n,
        top3=[dict(length=a, mean_score=round(b, 3),
                   x=round(c[0]["x"], 1), y=round(c[0]["y"], 1),
                   path=[[int(p["x"]), int(p["y"])] for p in c[::max(1, len(c) // 12)]])
              for a, b, i, c in center_top[:3]],
    )
    return res, dict(nodes=nodes, per_frame=per_frame, gr=gr, center=center, order=order,
                     files=files, best_pts=best_pts, n=n)


def run_baseline(n, center, kind, w=SRC_W, h=SRC_H):
    """无检测基线：null（无候选）/ static_center（固定点）/ static_random（固定随机点）。"""
    if kind == "null":
        per_frame = [[] for _ in range(n)]
    elif kind == "static_center":
        c = dict(x=w / 2.0, y=h / 2.0, w=int(BALL_D_PX), h=int(BALL_D_PX), app=1.0,
                 mot_peak=0.0, mot_mean=0.0, score=1.0)
        per_frame = [[dict(c)] for _ in range(n)]
    else:
        rng = np.random.default_rng(3)
        c = dict(x=float(rng.uniform(0.2 * w, 0.8 * w)), y=float(rng.uniform(0.2 * h, 0.8 * h)),
                 w=int(BALL_D_PX), h=int(BALL_D_PX), app=1.0, mot_peak=0.0, mot_mean=0.0,
                 score=1.0)
        per_frame = [[dict(c)] for _ in range(n)]
    nodes = build_tracks(per_frame, n)
    best = None
    for i, node in enumerate(nodes[center]):
        tk = track_stats(chain(nodes, center, i))
        if best is None or tk["length"] > best["length"]:
            best = tk
    passed, why = judge(best, center)
    return dict(pass_=bool(passed), reason=why, best=best)


# =============================================================================
# 可视化 —— 必须能人眼复核「球到底有没有被框住」
# =============================================================================
def up(img, k):
    return cv2.resize(img, None, fx=k, fy=k, interpolation=cv2.INTER_NEAREST)


def viz_frame(bgr, cands, dmot, best_pts, center, t_info, n_frames, path):
    """全帧标注 + 3 个候选的 6x 放大。"""
    img = bgr.copy()
    for c in cands:
        x0, y0 = int(c["x"] - c["w"] / 2), int(c["y"] - c["h"] / 2)
        cv2.rectangle(img, (x0 - 3, y0 - 3), (x0 + c["w"] + 3, y0 + c["h"] + 3),
                      (255, 120, 0), 1)
    # 轨迹点亮
    for p in best_pts:
        if p["t"] == center:
            continue
        cv2.circle(img, (int(p["x"]), int(p["y"])), 2, (0, 255, 255), -1)
    for p in best_pts:
        if p["t"] == center:
            cv2.rectangle(img, (int(p["x"] - 6), int(p["y"] - 6)),
                          (int(p["x"] + 6), int(p["y"] + 6)), (0, 0, 255), 2)
            cv2.line(img, (int(p["x"] - 14), int(p["y"])), (int(p["x"] + 14), int(p["y"])),
                     (0, 0, 255), 1)
            cv2.line(img, (int(p["x"]), int(p["y"] - 14)), (int(p["x"]), int(p["y"] + 14)),
                     (0, 0, 255), 1)
    head = "%s  t=%.2fs  frame %d/%d  cands=%d  PASS=%s" % (
        t_info["name"], t_info["t_matched"], center, n_frames, len(cands),
        "Y" if t_info["pass"] else "N")
    cv2.rectangle(img, (0, 0), (SRC_W, 26), (0, 0, 0), -1)
    cv2.putText(img, head, (8, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (0, 255, 120), 1,
                cv2.LINE_AA)

    # 3 个放大块：轨迹中心点 + 次优候选
    picks = []
    for p in best_pts:
        if p["t"] == center:
            picks.append((p["x"], p["y"], "TRACK"))
    for c in sorted(cands, key=lambda d: -d["score"])[:3]:
        picks.append((c["x"], c["y"], "c%.2f" % c["score"]))
    picks = picks[:3]
    k = 6
    half = 26
    cell = 2 * half * k
    band_h = cell + 40
    band = np.zeros((band_h, SRC_W, 3), np.uint8)
    for j, (x, y, lab) in enumerate(picks):
        x0 = int(max(0, min(bgr.shape[1] - 2 * half, x - half)))
        y0 = int(max(0, min(bgr.shape[0] - 2 * half, y - half)))
        crop = bgr[y0:y0 + 2 * half, x0:x0 + 2 * half]
        if crop.shape[0] != 2 * half or crop.shape[1] != 2 * half:
            continue
        z = up(crop, k)
        cv2.line(z, (z.shape[1] // 2, 0), (z.shape[1] // 2, z.shape[0]), (0, 0, 255), 1)
        cv2.line(z, (0, z.shape[0] // 2), (z.shape[1], z.shape[0] // 2), (0, 0, 255), 1)
        cv2.circle(z, (z.shape[1] // 2, z.shape[0] // 2), int(BALL_D_PX / 2 * k),
                   (0, 255, 255), 1)
        ox = 12 + j * (cell + 12)
        band[34:34 + z.shape[0], ox:ox + z.shape[1]] = z
        cv2.putText(band, "%s (%d,%d)" % (lab, int(x), int(y)), (ox + 2, 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 120), 1, cv2.LINE_AA)
    # 右侧放运动残差（同一帧），方便一眼对照「球的位置有没有残差」
    mx = 12 + 3 * (cell + 12)
    mw = SRC_W - mx - 8
    dm3 = cv2.cvtColor(dmot, cv2.COLOR_GRAY2BGR)
    dm3 = cv2.resize(dm3, (mw, int(mw * SRC_H / SRC_W)))
    band[34:34 + dm3.shape[0], mx:mx + mw] = dm3
    cv2.putText(band, "motion residual (|warped diff|)", (mx + 2, 24),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 120), 1, cv2.LINE_AA)
    out = np.vstack([img, band])
    cv2.imwrite(str(path), out)

def contact_sheet(rows, path):
    """10 张中心帧的「轨迹点 8x 放大」拼一张，方便一眼看球有没有被框住。"""
    k, half = 8, 22
    cell = 2 * half * k
    cols = 5
    rows_n = (len(rows) + cols - 1) // cols
    sheet = np.zeros((rows_n * (cell + 46), cols * (cell + 24), 3), np.uint8)
    for i, r in enumerate(rows):
        bgr = cv2.imread(r["frame_path"], cv2.IMREAD_COLOR)
        x, y = r["x"], r["y"]
        x0 = int(max(0, min(bgr.shape[1] - 2 * half, x - half)))
        y0 = int(max(0, min(bgr.shape[0] - 2 * half, y - half)))
        crop = bgr[y0:y0 + 2 * half, x0:x0 + 2 * half]
        if crop.shape[0] != 2 * half:
            continue
        z = up(crop, k)
        cv2.circle(z, (z.shape[1] // 2, z.shape[0] // 2), int(BALL_D_PX / 2 * k),
                   (0, 255, 255), 1)
        cx = (i % cols) * (cell + 24) + 8
        cy = (i // cols) * (cell + 46) + 40
        sheet[cy:cy + z.shape[0], cx:cx + z.shape[1]] = z
        cv2.putText(sheet, "%s %.0fs %s" % (r["name"][-8:-4], r["t_matched"],
                                            "PASS" if r["pass"] else "fail"),
                    (cx, cy - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (0, 255, 120) if r["pass"] else (120, 120, 120), 1, cv2.LINE_AA)
    cv2.imwrite(str(path), sheet)


# =============================================================================
# 主流程
# =============================================================================
def load_test_frames():
    rows = []
    with open(MANIFEST, encoding="utf-8") as f:
        head = f.readline().strip().split(",")
        for line in f:
            parts = line.rstrip("\n").split(",")
            rec = dict(zip(head, parts))
            if rec["split"] != "test":
                continue
            rows.append(dict(name=rec["file"], t=float(rec["t_approx_s"]),
                             n_person=int(rec["n_person"]), green=float(rec["green_ratio"])))
    rows.sort(key=lambda r: r["t"])
    return [r for r in rows if (TEST_DIR / r["name"]).exists()]


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="原型验证：用传统 CV（运动残差+外观先验+时空关联）单独检测足球")
    ap.add_argument("--video", default=str(DEFAULT_VIDEO))
    ap.add_argument("--half", type=float, default=2.0, help="轨迹窗口半宽（秒），默认 2.0")
    ap.add_argument("--extract-pad", type=float, default=3.0,
                    help="抽帧窗口半宽（秒），默认 3.0（比 --half 宽，用来定位测试帧）")
    ap.add_argument("--only", default="", help="只跑第几个 test 帧，如 1,3 或 4")
    ap.add_argument("--variants", default=",".join(VARIANTS))
    ap.add_argument("--mode", default="full", choices=["full", "no_motion", "no_appearance"],
                    help="候选来源。ablate_* 变体会自己覆盖它（消融必须真的换掉 mode，"
                         "否则三条曲线一模一样）；正常跑用 full")
    ap.add_argument("--no-affine", action="store_true", help="只用平移估全局运动")
    ap.add_argument("--grass-prior", action="store_true",
                    help="加几何先验：候选 15px 邻域内必须有草（否掉记分牌/水印/广告牌）")
    ap.add_argument("--no-grass-prior", dest="grass_prior", action="store_false",
                    help="显式关掉草先验（默认关，A/B 用）")
    ap.set_defaults(grass_prior=False)
    ap.add_argument("--no-viz", action="store_true")
    ap.add_argument("--out", default=str(OUT_JSON))
    ap.add_argument("--viz-dir", default=str(VIZ_DIR))
    ap.add_argument("--force-extract", action="store_true")
    ap.add_argument("--seed-xy", default="",
                    help="人工给定该帧球的像素坐标 x,y（配合 --only 用一帧）。"
                         "作用：把「候选排序挑错了」和「轨迹表达不出球」分开。"
                         "不传就完全不启用，正常跑不受影响。")
    args = ap.parse_args(argv)

    video = Path(args.video)
    if not video.exists():
        log(f"BALL_CV_FAIL video not found: {video}")
        return 1
    if not FFMPEG.exists():
        log(f"BALL_CV_FAIL ffmpeg not found: {FFMPEG}")
        return 1
    info = probe_video(video)
    if not info:
        log("BALL_CV_FAIL cv2 cannot open video")
        return 1
    if abs(info["fps"] - FPS) > 0.01:
        log(f"BALL_CV_FAIL fps {info['fps']} != expected {FPS}")

    rows = load_test_frames()
    if not rows:
        log("BALL_CV_FAIL no test frames in MANIFEST")
        return 1
    if args.only:
        keep = {int(x) - 1 for x in args.only.split(",") if x.strip()}
        rows = [r for i, r in enumerate(rows) if i in keep]
    variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    for v in variants:
        if v not in VARIANTS:
            log(f"BALL_CV_FAIL unknown variant {v}")
            return 1

    cfg = dict(half=args.half, affine=not args.no_affine, mode=args.mode,
               grass=args.grass_prior)
    seed_map = {}
    if args.seed_xy:
        s = args.seed_xy.strip()
        if ":" in s:  # "3:902,583" 只对第 3 个 test 帧（1 起）生效
            k, v = s.split(":", 1)
            sx, sy = [float(t) for t in v.split(",")]
            seed_map[int(k) - 1] = (sx, sy)
        else:
            sx, sy = [float(t) for t in s.split(",")]
            seed_map = {i: (sx, sy) for i in range(len(rows))}
        cfg["seed_map"] = seed_map
    log("=" * 78)
    log(f"ball_cv: {info['width']}x{info['height']} @ {info['fps']:.3f} fps, "
        f"{len(rows)} test frames, variants={','.join(variants)}")
    log(f"criteria: track>= {TRACK_MIN_LEN} frames, max step <= {MAX_STEP:.0f}px "
        f"(3x ball d={BALL_D_PX:.0f}px), accel <= {MAX_ACCEL}, gaps <= {MAX_GAP}")
    log("=" * 78)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    viz_dir = Path(args.viz_dir)
    if not args.no_viz:
        viz_dir.mkdir(parents=True, exist_ok=True)

    # ---- 1) 抽片段 + 定位测试帧（一次抽好，所有变体复用）----
    clips = []
    for i, r in enumerate(rows):
        name = r["name"].replace(".jpg", "")
        pad = args.extract_pad
        cache = CACHE_DIR / f"{name}_p{pad:.1f}"
        t0 = time.time()
        files = extract_clip(video, r["t"], pad, cache, name,
                             force=args.force_extract)
        idx, mae, maes = locate_center(files, TEST_DIR / r["name"])
        t_hit = r["t"] - pad + idx / FPS
        # 第二次抽帧：以实测到的帧为中心，保证 ±half 的分析窗口完整落在片段内。
        # 不这么做的话（MANIFEST 偏差可达 -1.84 s），窗口会被片段头尾截断，
        # 中心帧偏到一边，前后帧数不对称，判据的可比性直接没了。
        if idx / FPS < args.half + 0.2 or (len(files) - 1 - idx) / FPS < args.half + 0.2:
            pad2 = args.half + 0.6
            cache2 = CACHE_DIR / f"{name}_c{pad2:.1f}"
            files2 = extract_clip(video, t_hit, pad2, cache2, name, force=True)
            idx2, mae2, _ = locate_center(files2, TEST_DIR / r["name"])
            t_hit2 = t_hit - pad2 + idx2 / FPS
            files, idx, mae = files2, idx2, mae2
            t_hit = t_hit2
        r.update(t_matched=round(t_hit, 3),
                 t_offset=round(t_hit - r["t"], 3),
                 match_mae=round(mae, 3), n_extract=len(files), idx=idx)
        clips.append(dict(row=r, files=files, t_start=t_hit - idx / FPS))
        log(f"[{i + 1}/{len(rows)}] {r['name']} manifest t={r['t']:.1f}s "
            f"-> matched t={r['t_matched']:.2f}s (offset {r['t_offset']:+.2f}s, "
            f"mae={mae:.2f}) frames={len(files)} idx={idx} {time.time() - t0:.1f}s")

    # ---- 2) 逐变体跑 ----
    results = {}
    viz_rows = []
    for v in variants:
        log("")
        log(f"--- variant: {v} ---")
        per = []
        for i, cl in enumerate(clips):
            r = cl["row"]
            if v in ("null", "static_center", "static_random"):
                n = 2 * int(args.half * FPS) + 1
                center = n // 2
                res = run_baseline(n, center, v)
                res.update(name=r["name"], t_matched=r["t_matched"])
            else:
                c2 = dict(cfg, seed_xy=cfg.get("seed_map", {}).get(i))
                res, ctx = run_clip(cl["files"], cl["row"]["idx"], c2, v, seed=i)
                res.update(name=r["name"], t_matched=r["t_matched"],
                           t_center_real=round(cl["t_start"] + res["center_abs"] / FPS, 3))
                if v == "full" and not args.no_viz:
                    p = viz_dir / f"{r['name'].replace('.jpg', '')}.png"
                    try:
                        viz_frame(ctx["gr"]["bgr"], ctx["gr"]["cands"], ctx["gr"]["dmot"],
                                  ctx["best_pts"], ctx["center"],
                                  dict(name=r["name"], t_matched=res["t_center_real"],
                                       **{"pass": res["pass_"]}), ctx["n"], p)
                        c = [q for q in ctx["best_pts"] if q["t"] == ctx["center"]]
                        if c:
                            viz_rows.append(dict(
                                name=r["name"],
                                frame_path=str(cl["files"][res["center_abs"]]),
                                x=c[0]["x"], y=c[0]["y"],
                                t_matched=res["t_center_real"], **{"pass": res["pass_"]}))
                    except Exception as e:  # 图不能影响结论
                        log(f"  viz failed {r['name']}: {e}")
            per.append(res)
            log(f"  {r['name']} PASS={res['pass_']}  {res['reason']}"
                + ("" if v in ("null", "static_center", "static_random")
                   else f"  len={res['best']['length'] if res['best'] else 0}"
                        f" step={res['best']['max_step'] if res['best'] else 0}"
                        f" cands={res.get('n_cand_median')}"))
        npass = sum(1 for x in per if x["pass_"])
        results[v] = dict(n_pass=npass, n_total=len(per), pass_rate=round(npass / len(per), 3),
                          frames=per)
        log(f"  {v}: {npass}/{len(per)} pass")

    # ---- 3) 结论 ----
    real = results.get("full", {})
    syn = results.get("synthetic", {})
    shuf = results.get("shuffle", {})
    static = results.get("static_center", {})
    nullb = results.get("null", {})

    rf = [f for f in real.get("frames", []) if f.get("best")]
    diag = dict(
        n_cand_median_per_frame=int(np.median([f["n_cand_median"] for f in rf])) if rf else 0,
        n_app_blobs_median=int(np.median([f.get("n_app_blobs_median", 0) for f in rf]))
        if rf else 0,
        n_mot_blobs_median=int(np.median([f.get("n_mot_blobs_median", 0) for f in rf]))
        if rf else 0,
        winner_len_median=int(np.median([f["best"]["length"] for f in rf])) if rf else 0,
        # 自动选出的「最优轨迹」里，多少条其实是**静止**的高亮块（记分牌/广告/看台）。
        # mean_step < 1 px/帧 在 25fps 下人眼看就是一动不动。
        winner_static_frames=sum(1 for f in rf if f["best"]["mean_step"] < 1.0),
        winner_moving_frames=sum(1 for f in rf if f["best"]["mean_step"] >= 1.5),
        winner_static_names=[f["name"] for f in rf if f["best"]["mean_step"] < 1.0],
        # 对照挑法（链最长）会挑出多少条静止块 —— 用来证明「挑法」本身就是失败源
        pick_rule_len_static_frames=sum(
            1 for f in rf if f.get("best_longest") and f["best_longest"]["mean_step"] < 1.0),
        winner_mean_score_median=round(float(np.median([f["best"]["mean_score"] for f in rf])), 2)
        if rf else 0.0,
        winner_mot_median=round(float(np.median([f["best"]["mean_mot"] for f in rf])), 1)
        if rf else 0.0,
        motion_abs_dx_mean=round(float(np.mean([f["motion"]["mean_abs_dx"] for f in rf])), 2)
        if rf else 0.0,
        motion_abs_dx_max=round(float(max([f["motion"]["max_abs_dx"] for f in rf])), 2)
        if rf else 0.0,
        n_affine_total=int(sum(f["motion"]["n_affine"] for f in rf)),
    )
    case = next((f for f in rf if f.get("seeded")), None)
    if case:
        diag["seed_case"] = dict(name=case["name"], t_matched_s=case["t_matched"],
                                 seed_dist_px=case["seeded"]["seed_dist"],
                                 length=case["seeded"]["length"],
                                 max_step=case["seeded"]["max_step"],
                                 max_accel=case["seeded"]["max_accel"],
                                 mean_step=case["seeded"]["mean_step"],
                                 mean_score=case["seeded"]["mean_score"],
                                 pass_=case["seeded"]["pass"],
                                 auto_pick=dict(x=case["best"]["length"],
                                                mean_step=case["best"]["mean_step"]))
    synf = [f for f in syn.get("frames", []) if f.get("synthetic")]
    if synf:
        diag["synthetic"] = dict(
            n=len(synf),
            n_cand_hit=sum(1 for f in synf if f["synthetic"]["cand_dist"] <= 6.0),
            n_chain_pass=sum(1 for f in synf if f["synthetic"]["chain_pass"]),
            n_auto_pick=sum(1 for f in synf if f["synthetic"]["auto_dist"] <= 6.0),
            median_cand_dist=round(float(np.median([f["synthetic"]["cand_dist"]
                                                    for f in synf])), 2),
            median_auto_dist=round(float(np.median([f["synthetic"]["auto_dist"]
                                                    for f in synf])), 2),
        )
    verdict, vclass = build_verdict(real, syn, shuf, static, nullb, diag)

    out = dict(
        tool="tools/ball_cv.py",
        generated=time.strftime("%Y-%m-%dT%H:%M:%S"),
        video=dict(path=str(video), width=info["width"], height=info["height"],
                   fps=info["fps"], frames=info["frames"]),
        clip=dict(fps=FPS, half_window_s=args.half, extract_pad_s=args.extract_pad,
                  frames_per_clip=2 * int(args.half * FPS) + 1),
        thresholds=dict(ball_d_px=BALL_D_PX, max_step_px=MAX_STEP,
                        max_accel_px=MAX_ACCEL, track_min_len=TRACK_MIN_LEN,
                        max_gap=MAX_GAP, gate_px=GATE_PX, sat_max=SAT_MAX,
                        val_min=VAL_MIN, area_min=AREA_MIN, area_max=AREA_MAX,
                        max_wh=MAX_WH, aspect_max=ASPECT_MAX, extent_min=EXTENT_MIN,
                        round_min=ROUND_MIN, ring_min=RING_MIN, mot_thr=MOT_THR,
                        top_k=TOP_K),
        frames=[{**x, "name": r["name"], "t_manifest_s": r["t"],
                 "t_matched_s": r["t_matched"], "t_offset_s": r["t_offset"],
                 "match_mae": r["match_mae"], "n_person": r["n_person"],
                 "green": r["green"]}
                for r, x in zip(rows, results.get("full", {}).get("frames", [{}] * len(rows)))],
        controls={k: dict(n_pass=v["n_pass"], n_total=v["n_total"],
                          pass_rate=v["pass_rate"], frames=v["frames"],
                          note=CONTROL_NOTE.get(k, ""))
                  for k, v in results.items() if k != "full"},
        verdict=verdict, verdict_class=vclass,
        diagnostics=diag,
        human_review=dict(note=REVIEW_NOTE, summary=REVIEW_SUMMARY,
                          per_frame=HUMAN_REVIEW),
        notes=NOTES,
    )
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=_j)

    # 人眼复核拼图
    log(f"viz_rows={len(viz_rows)}")
    if not args.no_viz and viz_rows:
        try:
            contact_sheet(viz_rows, viz_dir / "_sheet_real.png")
        except Exception as e:
            log(f"sheet failed: {e}")

    log("")
    log("=" * 78)
    log(f"real     : {real.get('n_pass', 0)}/{real.get('n_total', 0)}")
    log(f"synthetic: {syn.get('n_pass', 0)}/{syn.get('n_total', 0)}   (算法上限=注入球被串成轨迹)")
    log(f"shuffle  : {shuf.get('n_pass', 0)}/{shuf.get('n_total', 0)}   (连续性判别力)")
    log(f"static   : {static.get('n_pass', 0)}/{static.get('n_total', 0)}   (判据本身可被平凡通过)")
    log(f"null     : {nullb.get('n_pass', 0)}/{nullb.get('n_total', 0)}")
    log(f"cands/frame(median) = {diag['n_cand_median_per_frame']}")
    log(f"auto winner static   = {diag['winner_static_frames']}/{len(rf)} "
        f"(mean_step<1px: {','.join(diag['winner_static_names']) or '-'})")
    if "synthetic" in diag:
        s = diag["synthetic"]
        log(f"synthetic probe      : cand_hit {s['n_cand_hit']}/{s['n']}  "
            f"chain_pass {s['n_chain_pass']}/{s['n']}  auto_picked {s['n_auto_pick']}/{s['n']}"
            f"  median dist cand={s['median_cand_dist']}px auto={s['median_auto_dist']}px")
    if "seed_case" in diag:
        c = diag["seed_case"]
        log(f"seed case            : {c['name']} len={c['length']} step={c['mean_step']}"
            f" max_step={c['max_step']} pass={c['pass_']} seed_dist={c['seed_dist_px']}px")
    log(f"VERDICT: {vclass} — {verdict}")
    log(f"WROTE {Path(args.out).relative_to(ROOT) if str(args.out).startswith(str(ROOT)) else args.out}")
    log(f"VIZ   {viz_dir}")
    if vclass == "work_machine":
        log(f"BALL_CV_OK {real.get('n_pass', 0)}")
    else:
        log(f"BALL_CV_FAIL {vclass}")
    return 0


REVIEW_NOTE = (
    "human_review 是作者把 data/results/viz/ball_cv/*.png 逐张看过之后的判定，"
    "不是自动判据。自动判据（pass）只看长度/步长/加速度，它分不清球和记分牌——"
    "这正是本次实验的主要发现，所以必须把「人眼看到框在哪」单独记下来。")

# 每帧：自动最优轨迹在中心帧框住了什么（作者 8x 看图后的判定）。
HUMAN_REVIEW = {
    "match_004.jpg": "记分牌上 05:45 的冒号。真球在 (902,584)、直径约 8px，确实是候选之一，"
                     "但自动选出的最优轨迹是静止记分牌。",
    "match_008.jpg": "远端红队球员脚踝处的白鞋/白袜。真球被 3 号白队球员双手抱在胸前"
                     "（约 (545,372)，直径约 25px），超出 MAX_WH=16/AREA_MAX=200 的尺寸"
                     "窗口，根本没进候选。",
    "match_012.jpg": "广告牌与挡板交界处的白块（轨迹沿广告牌水平拉了一条虚线）。",
    "match_016.jpg": "门将右侧的白块（手套/护腿），轨迹沿门将身上斜着上来。",
    "match_020.jpg": "广告牌中文字内部的白色空隙（8x 图里是「德」字的笔画间）。",
    "match_024.jpg": "红色广告牌上的白块（8x 图里是「河」字旁边）。",
    "match_028.jpg": "白色标牌文字之间的暗块。",
    "match_032.jpg": "广告牌 C1L 标志上的白块。",
    "match_036.jpg": "记分牌 14:47 的数字。",
    "match_040.jpg": "白色标牌文字间的白块。",
}

REVIEW_SUMMARY = (
    "10/10 的自动最优轨迹都落在场景上（记分牌 2 帧、广告牌 5 帧、球员白鞋/手套 3 帧），"
    "0/10 落在球上。判据全部 PASS，但一条都没框住球：M1 只约束了「存在平滑链」，"
    "没有约束「链上的东西是球」。")

NOTES = [
    "MANIFEST 的 t_approx_s 有系统性漂移，实测 offset 从 -1.84 s (t=1251) 线性走到 "
    "+0.84 s (t=7173)，斜率约 +0.45 ms/s，等价于整段视频真实帧率约 25.011 而不是 "
    "25.000。所以每帧都必须用画面匹配重定位，不能直接信 t_approx_s。",
    "重定位后用 6.4 位小数比对的 MAE 是 1.28~1.93 灰阶，说明匹配可靠（最坏帧 MAE>90）。",
    "片段第一次抽帧可能把 ±2 s 窗口切在片段边缘（因为漂移），所以定位完会再以实测帧"
    "为中心抽第二次，否则前后帧数不对称。",
]

CONTROL_NOTE = {
    "null": "不产生候选。应为 0/10；若不是 0 说明计分有 bug。",
    "static_center": "每帧报画面正中的一个点，完全不做检测。它必然 10/10 —— "
                     "这条对照的作用是证明 M1 单独没有判别力，必须看图。",
    "static_random": "每帧报同一个随机点，同样不做检测。",
    "shuffle": "打乱帧序后重跑。若通过率不下降，说明「轨迹连续性」判据是废的。",
    "synthetic": "在真实帧上合成 8px 白球（平滑运动）后跑同一流程，是算法上限。",
    "ablate_no_motion": "去掉运动残差项，只用外观（白+小+圆）。",
    "ablate_no_appearance": "去掉外观项，只用运动残差。",
}


def build_verdict(real, syn, shuf, static, nullb, diag):
    rp = real.get("pass_rate", 0.0)
    sp = syn.get("pass_rate", 0.0)
    hp = shuf.get("pass_rate", 0.0)
    n_static = diag.get("winner_static_frames", 0)
    n_tot = len([f for f in real.get("frames", []) if f.get("pass_")]) or 1
    syn_d = diag.get("synthetic", {})
    if not real:
        return "未跑 real 变体，无结论", "inconclusive"
    if syn and sp < 0.5:
        return ("注入的合成球只有 %.0f%% 能被串成合规轨迹，说明流程本身有 bug 或阈值太紧，"
                "real 的通过率不能用来下结论" % (sp * 100), "broken")
    if n_static * 2 >= n_tot:
        return ("自动选出的最优轨迹有 %d/%d 帧是**静止**高亮块（记分牌/广告牌/看台，"
                "mean_step<1px/帧），打乱帧序后仍有 %.0f%% 通过 ⇒ 判据只证明「存在一条"
                "平滑链」，不证明「链是球」。合成 8px 球 %d/%d 能进候选、%d/%d 能串成合规"
                "轨迹，但自动排序只挑中 %d/%d ⇒ 机制不坏，坏在「哪个候选是球」上："
                "这条路在现有画面下不足以单独定位球。"
                % (n_static, n_tot, hp * 100, syn_d.get("n_cand_hit", 0), syn_d.get("n", 0),
                   syn_d.get("n_chain_pass", 0), syn_d.get("n", 0),
                   syn_d.get("n_auto_pick", 0), syn_d.get("n", 0)), "fake_green")
    if rp >= 0.5 and rp - hp >= 0.3:
        return ("在 %d 帧里 %d 帧产生满足 M1 的轨迹，且打乱帧序后通过率降到 %.0f%%"
                "（判别力存在）。仍需人眼复核轨迹是否落在球上。"
                % (real["n_total"], real["n_pass"], hp * 100), "work_machine")
    if rp >= 0.5:
        return ("通过率 %.0f%% 但打乱帧序后仍有 %.0f%%，连续性判据判别力不足，"
                "高分是假绿，不算 work" % (rp * 100, hp * 100), "fake_green")
    return ("%d/%d 帧能串出 >=10 帧、无跳步的轨迹（合成球 %.0f%% 通过 ⇒ 不是代码坏了，"
            "是球在这批画面里不可分）。这条路在现有素材上不可行。"
            % (real["n_pass"], real["n_total"], sp * 100), "no_work")


if __name__ == "__main__":
    raise SystemExit(main())
