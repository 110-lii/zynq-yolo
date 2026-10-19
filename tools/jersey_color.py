"""用「球衣颜色」给低置信度球员框做二次判据 —— 可行性验证工具。

要回答的问题
-----------
YOLOv8n 在这批转播画面里，远处球员只有 0.06-0.24 分，被 conf=0.25 截掉。
假设：赛场上只有「红队 / 白队 / 裁判黄」三类稳定球衣色，所以一个低分框
如果躯干颜色等于某个队色，它更可能是真球员；如果是草坪绿/广告牌色，更像误检。

本工具不假设结论，只做**可失败**的测量：

  1. 色板**自动**从 score>=0.5 的框聚类得到（不手填"红队白队"）；
  2. 低分框按同一色板上色，给出颜色匹配度与重排后的候选表；
  3. 主判据 = 时间一致性（短轨迹里标签是否稳定），并给出随机基线；
  4. 负对照 = 同一分类器跑「框外 30% 环带」，若背景环的标签分布与躯干相同，
     说明这套特征没有判别力；
  5. 尺寸偏置自查 = 按框高分层重算一致性 + 纯度与框高的秩相关。

设计要点（为什么这么取）
-----------------------
* **躯干区域** 取 y∈[0.18,0.58]h、x 内缩 18%：
  头部/脖子在上方 18% 以内，短裤以下在 58% 之后，双臂垂在两侧 —— 三者都是污染源。
  只用这一条窄带，框再小也能凑出几十个像素。
* **HSV 用「色度直角坐标 + 明度」三维特征** `[S·cos h, S·sin h, V]`：
  色相是环形的，直接对 H 求均值会在 0/180 处撕裂；转成 (a,b) 后红与白
  分居"离原点远/近"，可以直接欧氏聚类，白/灰/黑靠 V 单独分开 ——
  低饱和度不会被当成"无颜色"丢掉，这是白队能被识别的前提。
* **草坪绿 vs 绿背心**：不靠手写色相区间，而是靠**负对照**自动判定：
  在背景环里占优、在躯干里不占优的聚类 = 场地/广告色（context），
  其余 = 球衣色（jersey）。所以"绿"这个词本身不决定结论，位置证据决定。
* **相机运动**：转播机位会跟着球摇，逐帧最近邻关联会被整体平移骗到，
  所以先用相位相关估全局位移，再关联（符号取残差小的那个）。

用法
----
    # 主实验：从源视频抽 3 段连续帧（1 段 20 s @3fps），检测在 640、取色在 720p
    D:\\python\\python.exe tools\\jersey_color.py clips \\
        --clips 1471,1939,6000 --clip-len 20 --fps 3 --imgsz 640 --tag hd

    # 部署分辨率复核：40 张标定帧 @640x360，检测 imgsz=320（与板上输入一致）
    D:\\python\\python.exe tools\\jersey_color.py frames \\
        --dir-calib data/samples/calib_real/frames_calib \\
        --dir-test  data/samples/calib_real/frames_test --imgsz 320 --tag deploy

判据
----
末行 `JERSEY_OK <n>` / `JERSEY_FAIL <原因>`。**颜色判据被判"没有判别力"也算 OK**
—— 本工具的价值在于把否定结论量化，而不是把数字做好看。
`--human-notes` 给一个 json（放 .research/，不入库），里面是人工逐框复核对
的结果，工具会把它原样并入 `data/results/jersey_color.json` 的 `human_check`。
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import json
import math
import subprocess
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

import cv2  # noqa: E402

import draw_detections as dd  # noqa: E402  只借它的字体/描边/底色，保证和人眼复核图一致
import eval_calib_set as ecs  # noqa: E402

FFMPEG = Path(r"D:\yt-dlp\ffmpeg\ffmpeg.exe")
VIDEO = Path(r"D:\yt-dlp\downloads\migu_5106947698\migu_5106947698.mp4")
RESEARCH = REPO / ".research" / "jersey"
OUT_JSON = REPO / "data" / "results" / "jersey_color.json"
OUT_VIZ = REPO / "data" / "results" / "viz" / "jersey"

# ---- 检测阈值（与仓库既有口径一致）----
CONF_LO = 0.05     # 低于此不取
CONF = 0.25        # 部署阈值
CONF_HI = 0.50     # 用来学习色板的"干净"框

# ---- 躯干几何 ----
T_TOP, T_BOT, T_INSET = 0.18, 0.58, 0.18
RING_MARGIN = 0.30  # 负对照：框外扩 30% 的环带
MIN_TORSO_PX = 24   # 少于这么多像素就不强行分类（记 TOO_SMALL）

# ---- 聚类 ----
K_MAX = 6
MERGE_D = 0.10
MIN_W = 0.02
SEED = 0

# ---- 上色（BGR，与 draw_detections 的观感对齐）----
NAME_BGR = {
    "RED": (40, 40, 235), "ORANGE": (0, 140, 255), "YELLOW": (0, 215, 255),
    "GREEN": (60, 200, 60), "CYAN": (215, 215, 0), "BLUE": (235, 120, 40),
    "PURPLE": (200, 60, 160), "MAGENTA": (230, 60, 230),
    "WHITE": (245, 245, 245), "GRAY": (150, 150, 150), "DARK": (90, 90, 90),
}
SHORT = {"RED": "RED", "ORANGE": "ORG", "YELLOW": "YEL", "GREEN": "GRN",
         "CYAN": "CYN", "BLUE": "BLU", "PURPLE": "PUR", "MAGENTA": "MAG",
         "WHITE": "WHT", "GRAY": "GRY", "DARK": "DRK"}


# =============================================================================
# 图像 / 区域
# =============================================================================
def _clampi(v, lo, hi):
    return int(max(lo, min(hi, v)))


def torso_rect(box, shape):
    """躯干矩形 (x1,y1,x2,y2)，像素坐标；太小返回 None。"""
    x1, y1, x2, y2 = [float(v) for v in box]
    w, h = x2 - x1, y2 - y1
    if w < 3 or h < 4:
        return None
    rx1 = x1 + T_INSET * w
    rx2 = x2 - T_INSET * w
    ry1 = y1 + T_TOP * h
    ry2 = y1 + T_BOT * h
    H, W = shape[:2]
    r = (_clampi(rx1, 0, W - 1), _clampi(ry1, 0, H - 1),
         _clampi(rx2, 0, W), _clampi(ry2, 0, H))
    if r[2] - r[0] < 2 or r[3] - r[1] < 2:
        return None
    return r


def ring_mask(box, shape, others):
    """负对照区域：框外扩 30% 的环带，但**只取躯干那几行**。

    为什么限制行范围：否则环带会包含头顶上方和脚下的草地，既扩大了面积又
    换了内容，比较就变成"大区域 vs 小区域"而不是"人 vs 背景"。这里体积/位置
    都对齐，唯一不同的是"在框内还是框外"。
    同时把**其它检测框**的像素抠掉：否则环形区域会盖到隔壁球员腿上，
    让对照组凭空带上球衣色，负对照就失去意义。
    """
    H, W = shape[:2]
    x1, y1, x2, y2 = [float(v) for v in box]
    w, h = x2 - x1, y2 - y1
    ex1 = _clampi(x1 - RING_MARGIN * w, 0, W - 1)
    ex2 = _clampi(x2 + RING_MARGIN * w, 0, W)
    ey1 = _clampi(y1 + T_TOP * h, 0, H - 1)
    ey2 = _clampi(y1 + T_BOT * h, 0, H)
    sub = np.zeros((ey2 - ey1, ex2 - ex1), dtype=bool)
    sub[:, :] = True
    # 挖掉本体框（与其列范围重叠的部分）
    ix1 = _clampi(x1, ex1, ex2)
    ix2 = _clampi(x2, ex1, ex2)
    if ix2 > ix1:
        sub[:, ix1 - ex1:ix2 - ex1] = False
    # 挖掉其它框
    for ob in others:
        ox1 = _clampi(ob[0] - 2, ex1, ex2)
        ox2 = _clampi(ob[2] + 2, ex1, ex2)
        oy1 = _clampi(ob[1] - 2, ey1, ey2)
        oy2 = _clampi(ob[3] + 2, ey1, ey2)
        if ox2 > ox1 and oy2 > oy1:
            sub[oy1 - ey1:oy2 - ey1, ox1 - ex1:ox2 - ex1] = False
    return (ex1, ey1, ex2, ey2), sub


def feats_from(bgr, mask):
    """BGR 切片 + 布尔掩码 -> 像素特征 (N,3) = [S·cosθ, S·sinθ, V]（S,V 归一到 0-1）。"""
    if bgr is None or mask is None or bgr.size == 0:
        return None, np.zeros(0, dtype=bool)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    th = hsv[..., 0].astype(np.float32) * 2.0          # OpenCV H:0-179 -> 0-358 度
    s = hsv[..., 1].astype(np.float32) / 255.0
    v = hsv[..., 2].astype(np.float32) / 255.0
    rad = np.deg2rad(th)
    f = np.stack([s * np.cos(rad), s * np.sin(rad), v], axis=-1)
    m = mask & (hsv[..., 2] > 25)   # 近黑像素既分不出色也分不出亮度，丢掉
    return f[m], m


# =============================================================================
# 色板：自动聚类 + 自动命名 + 靠负对照区分"球衣 vs 场地"
# =============================================================================
def _proto_name(cx, cy, v):
    c = math.hypot(cx, cy)
    if c < 0.13:
        if v >= 0.55:
            return "WHITE"
        if v >= 0.28:
            return "GRAY"
        return "DARK"
    deg = math.degrees(math.atan2(cy, cx)) % 360.0
    for a, b, nm in ((340, 360, "RED"), (0, 15, "RED"), (15, 42, "ORANGE"),
                     (42, 70, "YELLOW"), (70, 160, "GREEN"), (160, 200, "CYAN"),
                     (200, 258, "BLUE"), (258, 300, "PURPLE"), (300, 340, "MAGENTA")):
        if a <= deg < b:
            return nm
    return "MAGENTA"


def learn_palette(pool, k=K_MAX):
    """对高置信框的躯干像素做 k-means，再合并过近的簇、丢掉过小的簇。

    返回 list[dict]：按权重从大到小排序，含 centroid / weight / name。
    """
    from scipy.cluster.vq import kmeans2

    if pool is None or len(pool) < 200:
        return []
    if len(pool) > 40000:                      # 控制耗时，固定种子保证可复现
        rs = np.random.RandomState(SEED)
        pool = pool[rs.choice(len(pool), 40000, replace=False)]
    try:
        cen, lab = kmeans2(pool, k, minit="++", seed=SEED, iter=40)
    except Exception:
        return []
    w = np.bincount(lab, minlength=k).astype(np.float64) / len(lab)

    order = list(np.argsort(-w))
    kept = []                                  # [(cen, weight, members...)]
    for i in order:
        c = cen[i]
        hit = -1
        for j, (c2, _, _) in enumerate(kept):
            if np.linalg.norm(c - c2) < MERGE_D:
                hit = j
                break
        if hit >= 0:
            c2, w2, _ = kept[hit]
            kept[hit] = ((c2 * w2 + c * w[i]) / (w2 + w[i]), w2 + w[i], None)
        else:
            kept.append((c.copy(), w[i], None))
    protos = []
    for c, wi, _ in kept:
        if wi < MIN_W:
            continue
        protos.append({"c": [float(x) for x in c], "w": float(wi),
                       "name": _proto_name(c[0], c[1], c[2])})
    for i, p in enumerate(protos):
        p["id"] = "P%d" % i
    return protos


def assign(feats, protos):
    """每个像素分配最近原型（欧氏），返回 (idx, dist)。"""
    if feats is None or len(feats) == 0 or not protos:
        return None, None
    P = np.array([p["c"] for p in protos], dtype=np.float32)
    d = ((feats[:, None, :] - P[None, :, :]) ** 2).sum(-1)
    return np.argmin(d, axis=1), np.sqrt(np.min(d, axis=1))


def label_box(feats, protos):
    """一个区域 -> (winning proto idx, purity, per-proto share)。"""
    idx, _ = assign(feats, protos)
    if idx is None or len(idx) == 0:
        return None, 0.0, []
    cnt = np.bincount(idx, minlength=len(protos)).astype(np.float64)
    share = cnt / cnt.sum()
    j = int(np.argmax(share))
    return j, float(share[j]), [float(x) for x in share]


def familyize(protos, torso_counts, ring_counts, box_counts):
    """把簇并成**颜色族**（按名字分族），再用负对照决定每族的角色。

    为什么要在族一级决策：k-means 会把同一件球衣拆成 2-3 个簇（受光面/暗面/
    逆光），若按簇选"队色"，红队就只剩被选中的那一簇，另一半球员被算成
    "非队色"。族一级聚合把这个坑填掉。反过来，"绿"族是不是球衣，仍由
    环带/躯干的占比决定 —— 位置证据决定，词面不决定。

    判据：族级 环带占比 >= 1.5x 躯干占比 且 >=0.10 -> context，否则 jersey。
    """
    n_t = max(1.0, float(sum(torso_counts)))
    n_r = max(1.0, float(sum(ring_counts)))
    for i, p in enumerate(protos):
        p["share_torso"] = float(torso_counts[i] / n_t)
        p["share_ring"] = float(ring_counts[i] / n_r)
    fam = {}
    for i, p in enumerate(protos):
        f = fam.setdefault(p["name"], {"name": p["name"], "torso": 0.0, "ring": 0.0,
                                       "ids": [], "boxes": 0})
        f["torso"] += p["share_torso"]
        f["ring"] += p["share_ring"]
        f["ids"].append(p["id"])
        f["boxes"] += int(box_counts.get(p["id"], 0))
    for f in fam.values():
        f["role"] = ("context" if (f["ring"] >= 1.5 * f["torso"] and f["ring"] >= 0.10)
                     else "jersey")
        for p in protos:
            if p["name"] == f["name"]:
                p["role"] = f["role"]
    return fam


def pick_teams(fam, top=2):
    """队色族 = jersey 族里被 score>=0.5 的框用得最多的前 top 个。"""
    jer = [(f["boxes"], f["name"]) for f in fam.values() if f["role"] == "jersey"]
    jer.sort(key=lambda t: (-t[0], t[1]))
    return [nm for _, nm in jer[:top]]


def annotate(rows, protos, team_fams):
    """给每个框补 族名 / 是否队色 / 颜色匹配度 p_team。

    p_team = 躯干像素里落在**队色族**上的比例（不是"胜出簇的纯度"）。
    这样"红队球员长袖白边"不会被算成 0，而"一半草一半红"会诚实地只给 0.5。
    """
    team_fams = set(team_fams)
    for r in rows:
        lab = r.get("label")
        if not lab or lab == "TOO_SMALL":
            r["fam"] = None
            r["fam_role"] = None
            r["team"] = False
            r["p_team"] = 0.0
        else:
            p = protos[int(lab[1:])]
            r["fam"] = p["name"]
            r["fam_role"] = p["role"]
            sh = r.get("share") or []
            r["p_team"] = float(sum(x for i, x in enumerate(sh)
                                    if i < len(protos) and protos[i]["name"] in team_fams))
            r["team"] = r["fam"] in team_fams
        rl = r.get("ring_label")
        if not rl or rl == "TOO_SMALL":
            r["ring_fam"] = None
            r["ring_role"] = None
            r["ring_team"] = False
        else:
            p = protos[int(rl[1:])]
            r["ring_fam"] = p["name"]
            r["ring_role"] = p["role"]
            r["ring_team"] = r["ring_fam"] in team_fams
    return rows


# =============================================================================
# 跟踪：全局位移补偿 + 匈牙利关联
# =============================================================================
def estimate_shift(prev_gray, cur_gray):
    """相位相关估全局平移（摇镜）。返回 (dx,dy) 或 None。"""
    try:
        a = cv2.resize(prev_gray, (640, 360), interpolation=cv2.INTER_AREA)
        b = cv2.resize(cur_gray, (640, 360), interpolation=cv2.INTER_AREA)
        a = a.astype(np.float32)
        b = b.astype(np.float32)
        win = cv2.createHanningWindow((a.shape[1], a.shape[0]), cv2.CV_32F)
        (dx, dy), resp = cv2.phaseCorrelate(a, b, win)
        if not np.isfinite(dx) or not np.isfinite(dy) or resp < 0.05:
            return None, resp
        return (dx * 2.0, dy * 2.0), resp     # 640->1280 还原
    except Exception:
        return None, 0.0


def _assign_hungarian(prev, cur, shift):
    """prev/cur: list of dict(box, ...)。返回 [(i_prev, j_cur)]。"""
    if not prev or not cur:
        return []
    from scipy.optimize import linear_sum_assignment

    pc = np.array([[(b["box"][0] + b["box"][2]) / 2.0, (b["box"][1] + b["box"][3]) / 2.0]
                   for b in prev]) + np.array(shift)
    cc = np.array([[(b["box"][0] + b["box"][2]) / 2.0, (b["box"][1] + b["box"][3]) / 2.0]
                   for b in cur])
    ph = np.array([b["box"][3] - b["box"][1] for b in prev])
    ch = np.array([b["box"][3] - b["box"][1] for b in cur])
    D = np.linalg.norm(pc[:, None, :] - cc[None, :, :], axis=-1)
    gate = 0.9 * np.maximum(ph[:, None], ch[None, :])
    R = np.maximum(ph[:, None] / np.maximum(ch[None, :], 1),
                   ch[None, :] / np.maximum(ph[:, None], 1))
    cost = D / np.maximum(gate, 1.0) + 0.5 * np.log(np.maximum(R, 1.0))
    cost = np.where((D > gate) | (R > 1.8), 1e6, cost)
    ri, ci = linear_sum_assignment(cost)
    out = []
    for i, j in zip(ri, ci):
        if cost[i, j] < 1e5:
            out.append((int(i), int(j)))
    return out


# =============================================================================
# 统计
# =============================================================================
def _maj(tr, key="fam"):
    """轨迹的主标签（众数）。"""
    vals = [d.get(key) for d in tr]
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    cnt = {}
    for v in vals:
        cnt[v] = cnt.get(v, 0) + 1
    return max(cnt.items(), key=lambda kv: kv[1])[0]


def _stab(tracks, key="fam", min_len=5, pred=None):
    """轨迹内标签一致性。

    consec = 相邻帧标签相同的比例（所有轨迹的相邻对合在一起算）；
    strict = 整条轨迹标签全同的比例。长轨迹的 strict 天然趋 0，
    所以另给 strict_short（长度<=10 的轨迹）—— 那个数才和"随机猜"可比。
    """
    pair_eq = 0
    pair_n = 0
    strict = 0
    strict_s = 0
    n_s = 0
    n = 0
    lens = []
    for tr in tracks:
        if pred is not None and not pred(tr):
            continue
        labs = [d.get(key) for d in tr]
        if any(x is None for x in labs) or len(labs) < min_len:
            continue
        n += 1
        lens.append(len(labs))
        eq = [1 if labs[i] == labs[i + 1] else 0 for i in range(len(labs) - 1)]
        pair_eq += sum(eq)
        pair_n += len(eq)
        if all(eq):
            strict += 1
            if len(labs) <= 10:
                strict_s += 1
        if len(labs) <= 10:
            n_s += 1
    return {"n_tracks": n, "n_pairs": int(pair_n),
            "strict": (strict / n) if n else None,
            "strict_short": (strict_s / n_s) if n_s else None,
            "n_tracks_short": n_s,
            "consec": (pair_eq / pair_n) if pair_n else None,
            "len_median": float(np.median(lens)) if lens else None,
            "len_max": int(max(lens)) if lens else None}


def _stab_shuf(tracks, key="fam", min_len=5, pred=None, reps=200, rs=None):
    """随机基线：**每帧内**把标签打乱后重算一致性。

    为什么不用"随机猜 K 类"：K 类等概率会给 1/3，但这批轨迹的标签边际分布
    并不均匀（场上白/红占多数）。逐帧打乱保留了每一类的出现频率，
    得到的基线严格是"标签与图像内容无关时的期望一致率"，比 1/K 更难超越。
    """
    rs = rs or np.random.RandomState(SEED)
    frames = {}
    for tr in tracks:
        for d in tr:
            frames.setdefault(d["fi"], []).append(d)
    keys = sorted(frames)
    vals = [[d.get(key) for d in frames[f]] for f in keys]
    acc = []
    acc_short = []
    for _ in range(reps):
        for f, v in zip(keys, vals):
            p = rs.permutation(len(v))
            for pos, d in zip(p, frames[f]):
                d["_shuf"] = v[pos]
        s = _stab(tracks, key="_shuf", min_len=min_len, pred=pred)
        if s["consec"] is not None:
            acc.append(s["consec"])
        if s["strict_short"] is not None:
            acc_short.append(s["strict_short"])
    for tr in tracks:
        for d in tr:
            d.pop("_shuf", None)
    return (float(np.mean(acc)) if acc else None,
            float(np.mean(acc_short)) if acc_short else None)


def temporal_report(tracks, team_fams, reps=200,
                    strata=((0, 32), (32, 56), (56, 96), (96, 400))):
    """主判据的完整报表。

    除了总体，还切三个子集，因为总体数字会被两类"白送"的稳定性抬高：
      * claims_team    —— 轨迹主标签是队色（它自认为抓到了球员）。这一组才是
                          真正要回答"同一个球员的队色标签稳不稳"。
      * claims_context —— 主标签是草坪/广告色（它自认为抓到了背景）。
      * 尺寸分层       —— **每层各自算随机基线**，这一条直接回答"颜色判据
                          是不是框尺寸的代理变量"：若某层里 consec 掉回基线，
                          就说明那一层没有判别力。
    """
    rs = np.random.RandomState(SEED)

    def block(pred=None, name=""):
        s_f = _stab(tracks, key="fam", pred=pred)
        b_f, bs_f = _stab_shuf(tracks, key="fam", pred=pred, reps=reps, rs=rs)
        s_p = _stab(tracks, key="label", pred=pred)
        b_p, _ = _stab_shuf(tracks, key="label", pred=pred, reps=reps, rs=rs)
        return {"subset": name,
                "family": {**s_f, "shuffle_consec": b_f, "shuffle_strict_short": bs_f},
                "proto": {**s_p, "shuffle_consec": b_p}}

    out = {"all": block(None, "all")}
    out["claims_team"] = block(
        lambda tr: _maj(tr, "fam") in team_fams, "majority label is a team colour")
    out["claims_context"] = block(
        lambda tr: _maj(tr, "fam") not in team_fams, "majority label is not a team colour")
    # 负对照的时间一致性：**同一批框**、同样的位置关联，只把特征换成框外环带。
    # 如果环带标签也一样稳，那"稳定"就只是"这个球员一直站在草地上/球场那一块"，
    # 不能拿来当"颜色抓到了球员"的证据。这一条决定了主判据能不能当真。
    rc = _stab(tracks, key="ring_fam")
    rcb, rcs = _stab_shuf(tracks, key="ring_fam", reps=reps, rs=rs)
    out["ring_control"] = {"subset": "background ring (same boxes, offset region)",
                           "family": {**rc, "shuffle_consec": rcb,
                                      "shuffle_strict_short": rcs}}
    out["by_size"] = []
    for lo, hi in strata:
        sub = [tr for tr in tracks if lo <= np.median([d["h"] for d in tr]) < hi]
        if not sub:
            out["by_size"].append({"h_lo": lo, "h_hi": hi, "n_tracks": 0})
            continue
        s = _stab(sub, key="fam")
        b, bs = _stab_shuf(sub, key="fam", reps=reps, rs=rs)
        out["by_size"].append({"h_lo": lo, "h_hi": hi, "n_tracks": s["n_tracks"],
                               "n_pairs": s["n_pairs"], "consec": s["consec"],
                               "shuffle_consec": b, "strict": s["strict"],
                               "strict_short": s["strict_short"]})
    return out


def spearman(x, y):
    if len(x) < 4:
        return None, None
    from scipy.stats import spearmanr
    r, p = spearmanr(x, y)
    return (float(r), float(p)) if np.isfinite(r) else (None, None)


# =============================================================================
# 检测 / 单帧测量
# =============================================================================
def detect(model, bgr, imgsz):
    r = model.predict(bgr, imgsz=imgsz, conf=CONF_LO, iou=0.5, classes=[0],
                      verbose=False)[0]
    b = r.boxes.xyxy.cpu().numpy().astype(np.float64)
    s = r.boxes.conf.cpu().numpy().astype(np.float64)
    o = np.argsort(-s)
    return b[o], s[o]


def measure(bgr, boxes, scores, protos):
    """对一帧的所有框算躯干色 + 背景环色。protos 为空时只收集躯干像素。"""
    H, W = bgr.shape[:2]
    rows = []
    pool = []
    for i, (b, s) in enumerate(zip(boxes, scores)):
        tr = torso_rect(b, bgr.shape)
        others = [boxes[k] for k in range(len(boxes)) if k != i]
        rec = {"box": [round(float(v), 1) for v in b], "score": round(float(s), 3),
               "h": round(float(b[3] - b[1]), 1), "label": None, "purity": None,
               "ring_label": None, "ring_purity": None, "n_torso": 0, "n_ring": 0}
        if tr is None:
            rec["label"] = "TOO_SMALL"
            rec["ring_label"] = "TOO_SMALL"
            rows.append(rec)
            continue
        sub = bgr[tr[1]:tr[3], tr[0]:tr[2]]
        m = np.ones(sub.shape[:2], dtype=bool)
        f, mm = feats_from(sub, m)
        rec["n_torso"] = int(len(f)) if f is not None else 0
        if f is not None and len(f) and s >= CONF_HI and len(f) >= MIN_TORSO_PX:
            pool.append(f)
        if f is None or len(f) < MIN_TORSO_PX:
            rec["label"] = "TOO_SMALL"
        elif protos:
            j, pur, share = label_box(f, protos)
            rec["label"] = protos[j]["id"]
            rec["purity"] = round(pur, 3)
            rec["share"] = [round(x, 3) for x in share]
        # 负对照环带
        rr, rmask = ring_mask(b, bgr.shape, others)
        if rmask is not None and rmask.sum() >= MIN_TORSO_PX:
            rsub = bgr[rr[1]:rr[3], rr[0]:rr[2]]
            rf, rmm = feats_from(rsub, rmask)
            rec["n_ring"] = int(len(rf)) if rf is not None else 0
            if rf is not None and len(rf) >= MIN_TORSO_PX and protos:
                j, pur, _ = label_box(rf, protos)
                rec["ring_label"] = protos[j]["id"]
                rec["ring_purity"] = round(pur, 3)
        rows.append(rec)
    return rows, pool


# =============================================================================
# 抽帧
# =============================================================================
def extract_clip(t0, dur, fps, outdir):
    outdir.mkdir(parents=True, exist_ok=True)
    for p in outdir.glob("*.jpg"):
        p.unlink()
    pat = str(outdir / "f%03d.jpg")
    cmd = [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-ss", "%.2f" % t0,
           "-i", str(VIDEO), "-t", "%.2f" % dur, "-vf", "fps=%d" % fps,
           "-q:v", "2", "-y", pat]
    subprocess.run(cmd, check=True, timeout=300)
    return sorted(outdir.glob("f*.jpg"))


# =============================================================================
# 画图
# =============================================================================
def color_of(rec, protos):
    """框的颜色 = 它被判到的球衣颜色族；灰=无可判颜色。"""
    nm = rec.get("fam")
    if nm is None:
        return (110, 110, 110), "SMALL"
    if rec.get("fam_role") == "context" and nm == "GREEN":
        return (30, 105, 30), "GRASS"
    return NAME_BGR.get(nm, (255, 255, 255)), SHORT.get(nm, nm[:3])


def render_frame(bgr, rows, protos, team_fams, head):
    im = bgr.copy()
    bar = 26
    cv2.rectangle(im, (0, 0), (im.shape[1], bar), dd.C_BG, -1)
    for rec in rows:
        b = rec["box"]
        col, short = color_of(rec, protos)
        s = rec["score"]
        low = s < CONF
        thick = 1 if low else 2
        if rec.get("team"):
            thick += 1
        dd._box(im, b, col, thick)
        star = "*" if rec.get("team") else " "
        pt = rec.get("p_team")
        txt = "%s%.2f %s%s %s" % ("~" if low else "", s, short, star,
                                  "%.2f" % pt if pt is not None else "-")
        org = (int(b[0]), max(bar + 10, int(b[1]) - 4))
        # 深色底块 + 细描边：不加的话标签在草地上基本看不清
        (tw, th), base = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, 0.34, 1)
        cv2.rectangle(im, (org[0] - 2, org[1] - th - 3),
                      (org[0] + tw + 3, org[1] + base - 1), (25, 25, 25), -1)
        cv2.rectangle(im, (org[0] - 2, org[1] - th - 3),
                      (org[0] + tw + 3, org[1] + base - 1), col, 1)
        dd._put(im, txt, org, col, 0.34)
        # 躯干取样框（细白线），方便人眼核对"取的是哪块"
        tr = torso_rect(np.array(b), im.shape)
        if tr is not None:
            cv2.rectangle(im, (tr[0], tr[1]), (tr[2], tr[3]), (255, 255, 255), 1)
    dd._put(im, head, (8, 18), dd.C_TXT, 0.46)
    return im


def contact_sheet(imgs, cols, width, title):
    tiles = []
    for im in imgs:
        h, w = im.shape[:2]
        nh = int(round(h * width / w))
        tiles.append(cv2.resize(im, (width, nh), interpolation=cv2.INTER_AREA))
    if not tiles:
        return None
    rows = (len(tiles) + cols - 1) // cols
    th, tw = tiles[0].shape[:2]
    sheet = np.full((rows * th + 24, cols * tw, 3), 24, dtype=np.uint8)
    for i, t in enumerate(tiles):
        r, c = divmod(i, cols)
        sheet[24 + r * th:24 + (r + 1) * th, c * tw:(c + 1) * tw] = t
    dd._put(sheet, title, (8, 16), dd.C_TXT, 0.50)
    return sheet


def inspect_montage(picks, protos, path, cell=(140, 140), title=None):
    """人眼抽查图：上下文 / 躯干 / 同尺寸背景环，各自上标签。

    躯干 vs 环带并排是这套实验最关键的对照：两边看起来一样，特征就没有判别力。
    左边再加一张"框外扩 80% 的上下文"，否则光看躯干切片无法判断那到底是
    球员、广告牌还是球网。
    """
    cw, chh = cell
    pad = 22
    rows_n = len(picks)
    X0 = 3 * (cw + 6) + 8
    W = X0 + 300
    H = rows_n * (chh + pad) + 30
    canvas = np.full((H, W, 3), 26, dtype=np.uint8)
    dd._put(canvas, title or ("context (left) | torso | background ring   -- same classifier, "
                             "same size, same rows"), (8, 16), dd.C_TXT, 0.44)
    for i, pk in enumerate(picks):
        rec, timg, rimg, ctx, _nm = pk
        y = 30 + i * (chh + pad)
        x = 6
        panes = ((ctx, "CTX", None, None),
                 (timg, "TORSO", rec.get("fam"), rec.get("p_team")),
                 (rimg, "RING", rec.get("ring_fam"), None))
        for img, tag, lab, val in panes:
            if img is None or img.size == 0:
                cv2.rectangle(canvas, (x, y), (x + cw, y + chh), (60, 60, 60), 1)
            else:
                h, w = img.shape[:2]
                z = min(cw / max(1, w), chh / max(1, h))
                if z >= 1:
                    big = cv2.resize(img, (int(w * int(z)), int(h * int(z))),
                                     interpolation=cv2.INTER_NEAREST)
                else:
                    big = cv2.resize(img, (max(1, int(w * z)), max(1, int(h * z))),
                                     interpolation=cv2.INTER_AREA)
                canvas[y:y + big.shape[0], x:x + big.shape[1]] = big
                cv2.rectangle(canvas, (x, y), (x + big.shape[1], y + big.shape[0]),
                              (90, 90, 90), 1)
                txt = "%s" % tag
                if lab:
                    txt += " %s %s" % (lab, "%.2f" % val if val is not None else "-")
                dd._put(canvas, txt, (x, y + big.shape[0] + 10), dd.C_TXT, 0.33)
            x += cw + 6
        b = rec["box"]
        sc = rec["score"]
        dd._put(canvas, "#%02d s=%.3f h=%.0fpx %s%s/ring:%s"
                % (i + 1, sc, rec["h"], rec.get("fam"), "*" if rec.get("team") else "",
                   rec.get("ring_fam")), (X0, y + 14), dd.C_TXT, 0.38)
        dd._put(canvas, "box=(%.0f,%.0f,%.0f,%.0f) n=%s/%s"
                % (b[0], b[1], b[2], b[3], rec.get("n_torso"), rec.get("n_ring")),
                (X0, y + 34), (200, 200, 200), 0.34)
        dd._put(canvas, "%s" % _nm, (X0, y + 54), (170, 210, 170), 0.34)
    cv2.imwrite(str(path), canvas)
    return path


# =============================================================================
# 一次实验
# =============================================================================
_last_shift = (0.0, 0.0)
prev_dets = []


def run_clip(model, name, files, imgsz, tag, viz_every, args, ckpt):
    """处理一段连续帧：检测 -> 学色板（第一遍）-> 分类（第二遍）-> 跟踪 -> 统计。"""
    global _last_shift
    t0 = time.perf_counter()
    imgs = [cv2.imread(str(p)) for p in files]
    imgs = [im for im in imgs if im is not None]
    if not imgs:
        return None
    H, W = imgs[0].shape[:2]

    # pass 1: 检测 + 收集高置信框的躯干像素
    per = []
    pool = []
    for k, im in enumerate(imgs):
        b, s = detect(model, im, imgsz)
        rows, p = measure(im, b, s, [])
        per.append({"boxes": b, "scores": s, "rows": rows})
        pool.extend(p)
    pool = np.concatenate(pool, axis=0) if pool else None
    protos = learn_palette(pool)
    if not protos:
        return None

    # pass 2: 用学到的色板重新分类（池化像素不参与，避免自我确认）
    torso_cnt = [0.0] * len(protos)
    ring_cnt = [0.0] * len(protos)
    frames = []
    for k, im in enumerate(imgs):
        d = per[k]
        rows, _ = measure(im, d["boxes"], d["scores"], protos)
        for r in rows:
            if r["label"] and r["label"] != "TOO_SMALL":
                torso_cnt[int(r["label"][1:])] += r["n_torso"]
            if r["ring_label"] and r["ring_label"] != "TOO_SMALL":
                ring_cnt[int(r["ring_label"][1:])] += r["n_ring"]
        d["rows"] = rows
        frames.append(rows)

    # 族级角色 + 队色
    hi_cnt = {}
    for rows in frames:
        for r in rows:
            if r["score"] >= CONF_HI and r["label"] and r["label"] != "TOO_SMALL":
                hi_cnt[r["label"]] = hi_cnt.get(r["label"], 0) + 1
    fam = familyize(protos, torso_cnt, ring_cnt, hi_cnt)
    team_fams = pick_teams(fam, top=2)
    for rows in frames:
        annotate(rows, protos, team_fams)

    # 跟踪（需要 fi 与全局位移）
    global prev_dets
    for fi, rows in enumerate(frames):
        for d in rows:
            d["fi"] = fi
    prev_dets = []
    _last_shift = (0.0, 0.0)
    tracks, shifts = _track_frames(imgs, frames)

    temp = temporal_report(tracks, team_fams, reps=args.shuffles)

    # 低分框判决 + 重排
    low = [r for rows in frames for r in rows if CONF_LO <= r["score"] < CONF]
    ctx_names = {f["name"] for f in fam.values() if f["role"] == "context"}
    nj = sum(1 for r in low if r["team"])
    jab = sum(1 for r in low if r["ring_team"])
    c_t = sum(1 for r in low if r["fam"] in ctx_names)
    c_r = sum(1 for r in low if r["ring_fam"] in ctx_names)
    # 第三类主体（裁判黄/橙）不属于任何队色：只看"是否队色"必然把裁判算成非球员。
    # 所以另给一个更宽的口径 —— 躯干落在任一 jersey 族（任何非场地/广告色）上。
    jr_t = sum(1 for r in low if r["fam_role"] == "jersey")
    jr_r = sum(1 for r in low if r["ring_role"] == "jersey")
    # 低分框按尺寸分层：直接看"颜色判据是不是框大小的代理"
    low_by_size = []
    for lo, hi2 in ((0, 32), (32, 56), (56, 96), (96, 400)):
        sub = [r for r in low if lo <= r["h"] < hi2]
        if not sub:
            low_by_size.append({"h_lo": lo, "h_hi": hi2, "n": 0})
            continue
        low_by_size.append({"h_lo": lo, "h_hi": hi2, "n": len(sub),
                            "torso_team_rate": round(sum(1 for r in sub if r["team"]) / len(sub), 3),
                            "ring_team_rate": round(sum(1 for r in sub if r["ring_team"]) / len(sub), 3),
                            "ring_context_rate": round(sum(1 for r in sub if r["ring_fam"] in ctx_names) / len(sub), 3),
                            "torso_context_rate": round(sum(1 for r in sub if r["fam"] in ctx_names) / len(sub), 3)})

    ranked = sorted(low, key=lambda r: -r["p_team"])
    rerank = [{"score": r["score"], "p_team": round(r["p_team"], 3),
               "fam": r["fam"], "team": r["team"], "ring_fam": r["ring_fam"],
               "h": r["h"], "box": r["box"], "fi": r["fi"]} for r in ranked[:25]]
    viz_dir = OUT_VIZ / tag
    viz_dir.mkdir(parents=True, exist_ok=True)
    # 重排候选的肉眼复核图：低分框按颜色匹配度降序取前 10
    if ranked:
        rp = _crop_pairs([(r, imgs[r["fi"]], None, name) for r in ranked[:10]])
        inspect_montage(rp, protos, viz_dir / ("%s_rerank.png" % name), (140, 140),
                        "%s rerank top10: low-score boxes sorted by colour-match p_team"
                        % name)

    # 可视化
    viz_dir = OUT_VIZ / tag
    viz_dir.mkdir(parents=True, exist_ok=True)
    heads = []
    for k, im in enumerate(imgs):
        if k % viz_every:
            continue
        head = ("%s %s f%02d | imgsz=%d | box colour = assigned jersey colour | "
                "*=team palette | ~=score<%.2f | thin white = torso sample"
                % (tag, name, k, imgsz, CONF))
        v = render_frame(im, frames[k], protos, set(team_fams), head)
        cv2.imwrite(str(viz_dir / ("%s_f%03d.png" % (name, k))), v)
        heads.append(v)
    if heads:
        sh = contact_sheet(heads, 4, 420, "%s %s : box colour = jersey colour, *=team, ~=low score"
                           % (tag, name))
        if sh is not None:
            cv2.imwrite(str(viz_dir / ("_sheet_%s_%s.png" % (tag, name))), sh)

    ta = temp["all"]["family"]
    dt = time.perf_counter() - t0
    print("  %-16s frames=%3d dets=%4d low=%3d tracks=%3d consec=%s (base %s) "
          "team %s  %.1fs"
          % (name, len(imgs), sum(len(r) for r in frames), len(low),
             ta["n_tracks"] or 0,
             _pretty(ta["consec"]), _pretty(ta["shuffle_consec"]),
             "/".join(team_fams), dt))

    return {
        "clip": name, "t0_s": ckpt, "n_frames": len(imgs), "imgsz": imgsz,
        "frame_wh": [W, H],
        "n_det": int(sum(len(r) for r in frames)),
        "n_conf_ge_025": int(sum(1 for rows in frames for r in rows if r["score"] >= CONF)),
        "n_low_005_025": len(low),
        "palette": [{"id": p["id"], "name": p["name"], "role": p["role"],
                     "share_torso": round(p["share_torso"], 3),
                     "share_ring": round(p["share_ring"], 3),
                     "centroid": [round(x, 3) for x in p["c"]]} for p in protos],
        "families": {k: {"role": f["role"], "torso": round(f["torso"], 3),
                         "ring": round(f["ring"], 3), "boxes_hi": f["boxes"]}
                     for k, f in fam.items()},
        "team_families": team_fams,
        "temporal": temp,
        "low": {"n": len(low), "torso_team": nj, "ring_team": jab,
                "torso_context": c_t, "ring_context": c_r,
                "torso_jersey": jr_t, "ring_jersey": jr_r,
                "torso_team_rate": round(nj / len(low), 3) if low else None,
                "ring_team_rate": round(jab / len(low), 3) if low else None,
                "torso_context_rate": round(c_t / len(low), 3) if low else None,
                "ring_context_rate": round(c_r / len(low), 3) if low else None,
                "torso_jersey_rate": round(jr_t / len(low), 3) if low else None,
                "ring_jersey_rate": round(jr_r / len(low), 3) if low else None,
                "mean_p_team": round(float(np.mean([r["p_team"] for r in low])), 3) if low else None,
                "by_size": low_by_size},
        "rerank_top": rerank,
        "_frames_rows": frames,
        "_protos": protos,
        "_imgs": imgs,
    }


def _track_frames(imgs, frames):
    """帧间关联（全局位移补偿）。返回 tracks 与每帧位移。"""
    global _last_shift
    tracks = []
    shifts = []
    prev = []
    p_gray = None
    for fi, rows in enumerate(frames):
        if fi > 0:
            c_gray = cv2.cvtColor(imgs[fi], cv2.COLOR_BGR2GRAY)
            d, resp = estimate_shift(p_gray, c_gray)
            _last_shift = d if d is not None else (0.0, 0.0)
            # 符号消歧：两种方向都算，取配对残差中位数小的
            best = None
            for sgn in (1.0, -1.0):
                sh = (sgn * _last_shift[0], sgn * _last_shift[1])
                pairs = _assign_hungarian(prev, rows, sh)
                if not pairs:
                    cost = 1e9
                else:
                    res = []
                    for i, j in pairs:
                        pb, cb = prev[i]["box"], rows[j]["box"]
                        res.append(math.hypot((pb[0] + pb[2]) / 2 - (cb[0] + cb[2]) / 2,
                                              (pb[1] + pb[3]) / 2 - (cb[1] + cb[3]) / 2))
                    cost = float(np.median(res)) if res else 1e9
                if best is None or cost < best[0]:
                    best = (cost, sh, pairs, sgn)
            cost, sh, pairs, sgn = best
            shifts.append({"fi": fi, "dx": round(sh[0], 2), "dy": round(sh[1], 2),
                           "sign": sgn, "resp": round(float(resp), 3)})
            used = set()
            for i, j in pairs:
                rows[j]["tid"] = prev[i]["tid"]
                tracks[rows[j]["tid"]].append(rows[j])
                used.add(j)
            for j, d2 in enumerate(rows):
                if j in used:
                    continue
                d2["tid"] = len(tracks)
                tracks.append([d2])
        else:
            for j, d2 in enumerate(rows):
                d2["tid"] = j
                tracks.append([d2])
        prev = rows
        p_gray = cv2.cvtColor(imgs[fi], cv2.COLOR_BGR2GRAY)
    return tracks, shifts


def run_frames(model, files, imgsz, tag, args):
    """部署分辨率复核：40 张标定帧（不连续，无时间一致性）。"""
    imgs = [(p, cv2.imread(str(p))) for p in files]
    imgs = [(p, im) for p, im in imgs if im is not None]
    if not imgs:
        return None
    # 全局色板：所有帧的高置信框一起聚类（这批帧跨 2 小时，光照不同）
    per = []
    pool = []
    for p, im in imgs:
        b, s = detect(model, im, imgsz)
        per.append({"path": p, "boxes": b, "scores": s, "rows": []})
        _, pl = measure(im, b, s, [])
        pool.extend(pl)
    pool = np.concatenate(pool, axis=0) if pool else None
    protos = learn_palette(pool)
    if not protos:
        return None
    torso_cnt = [0.0] * len(protos)
    ring_cnt = [0.0] * len(protos)
    for k, (p, im) in enumerate(imgs):
        rows, _ = measure(im, per[k]["boxes"], per[k]["scores"], protos)
        for r in rows:
            r["fi"] = k
            if r["label"] and r["label"] != "TOO_SMALL":
                torso_cnt[int(r["label"][1:])] += r["n_torso"]
            if r["ring_label"] and r["ring_label"] != "TOO_SMALL":
                ring_cnt[int(r["ring_label"][1:])] += r["n_ring"]
        per[k]["rows"] = rows
    hi_cnt = {}
    for d in per:
        for r in d["rows"]:
            if r["score"] >= CONF_HI and r["label"] and r["label"] != "TOO_SMALL":
                hi_cnt[r["label"]] = hi_cnt.get(r["label"], 0) + 1
    fam = familyize(protos, torso_cnt, ring_cnt, hi_cnt)
    team_fams = pick_teams(fam, top=2)
    for d in per:
        annotate(d["rows"], protos, team_fams)
    low = [r for d in per for r in d["rows"] if CONF_LO <= r["score"] < CONF]
    ctx_names = {f["name"] for f in fam.values() if f["role"] == "context"}
    nj = sum(1 for r in low if r["team"])
    jab = sum(1 for r in low if r["ring_team"])
    c_t = sum(1 for r in low if r["fam"] in ctx_names)
    c_r = sum(1 for r in low if r["ring_fam"] in ctx_names)
    # 尺寸分层：颜色判据是不是框大小的代理？
    strata = [(0, 24), (24, 40), (40, 64), (64, 400)]
    by_size = []
    for lo, hi2 in strata:
        sub = [r for r in low if lo <= r["h"] < hi2]
        if not sub:
            by_size.append({"h_lo": lo, "h_hi": hi2, "n": 0})
            continue
        by_size.append({"h_lo": lo, "h_hi": hi2, "n": len(sub),
                        "torso_team_rate": round(sum(1 for r in sub if r["team"]) / len(sub), 3),
                        "ring_team_rate": round(sum(1 for r in sub if r["ring_team"]) / len(sub), 3),
                        "ring_context_rate": round(sum(1 for r in sub if r["ring_fam"] in ctx_names) / len(sub), 3)})
    # 颜色匹配度（p_team）与框高的秩相关：回答"是不是尺寸代理"
    allr = [r for d in per for r in d["rows"] if r["fam"] is not None]
    rho, pval = spearman([r["h"] for r in allr], [r["p_team"] for r in allr])
    # 高置信框的"队色命中率"——判据能不自洽的下限：真人框里有多少被判成队色
    hi_all = [r for d in per for r in d["rows"] if r["score"] >= CONF]
    hi_team = sum(1 for r in hi_all if r["team"])
    jr_t = sum(1 for r in low if r["fam_role"] == "jersey")
    jr_r = sum(1 for r in low if r["ring_role"] == "jersey")

    # 可视化
    viz_dir = OUT_VIZ / tag
    viz_dir.mkdir(parents=True, exist_ok=True)
    heads = []
    for k, (p, im) in enumerate(imgs):
        head = ("%s %s | imgsz=%d | box colour = assigned jersey colour | *=team | ~=low"
                % (tag, p.name, imgsz))
        v = render_frame(im, per[k]["rows"], protos, set(team_fams), head)
        cv2.imwrite(str(viz_dir / (p.stem + ".png")), v)
        heads.append(v)
    sh = contact_sheet(heads, 5, 380, "%s : %d calib frames @640x360, imgsz=%d"
                       % (tag, len(heads), imgsz))
    if sh is not None:
        cv2.imwrite(str(viz_dir / ("_sheet_%s.png" % tag)), sh)

    ranked = sorted(low, key=lambda r: -r["p_team"])
    rerank = [{"frame": imgs[r["fi"]][0].name,
               "score": r["score"], "p_team": round(r["p_team"], 3),
               "fam": r["fam"], "team": r["team"], "ring_fam": r["ring_fam"],
               "h": r["h"], "box": r["box"]}
              for r in ranked[:30]]
    if ranked:
        rp = _crop_pairs([(r, imgs[r["fi"]][1], None, tag) for r in ranked[:10]])
        inspect_montage(rp, protos, viz_dir / ("_rerank_%s.png" % tag), (140, 140),
                        "%s rerank top10 @640x360 imgsz=%d: low boxes by p_team" % (tag, imgsz))
    print("  %-10s frames=%3d dets=%4d low=%3d hi_team=%d/%d torso_team=%d "
          "ring_team=%d ring_ctx=%d rho=%.3f"
          % (tag, len(imgs), sum(len(d["rows"]) for d in per), len(low),
             hi_team, len(hi_all), nj, jab, c_r,
             rho if rho is not None else float("nan")))
    return {
        "imgsz": imgsz, "n_frames": len(imgs), "frame_wh": list(imgs[0][1].shape[1::-1]),
        "n_det": int(sum(len(d["rows"]) for d in per)),
        "n_conf_ge_025": len(hi_all),
        "n_low_005_025": len(low),
        "palette": [{"id": p["id"], "name": p["name"], "role": p["role"],
                     "share_torso": round(p["share_torso"], 3),
                     "share_ring": round(p["share_ring"], 3),
                     "centroid": [round(x, 3) for x in p["c"]]} for p in protos],
        "families": {k: {"role": f["role"], "torso": round(f["torso"], 3),
                         "ring": round(f["ring"], 3), "boxes_hi": f["boxes"]}
                     for k, f in fam.items()},
        "team_families": team_fams,
        "high_conf": {"n": len(hi_all), "team": hi_team,
                      "team_rate": round(hi_team / len(hi_all), 3) if hi_all else None},
        "low": {"n": len(low), "torso_team": nj, "ring_team": jab,
                "torso_context": c_t, "ring_context": c_r,
                "torso_jersey": jr_t, "ring_jersey": jr_r,
                "torso_team_rate": round(nj / len(low), 3) if low else None,
                "ring_team_rate": round(jab / len(low), 3) if low else None,
                "torso_context_rate": round(c_t / len(low), 3) if low else None,
                "ring_context_rate": round(c_r / len(low), 3) if low else None,
                "torso_jersey_rate": round(jr_t / len(low), 3) if low else None,
                "ring_jersey_rate": round(jr_r / len(low), 3) if low else None,
                "mean_p_team": round(float(np.mean([r["p_team"] for r in low])), 3) if low else None,
                "by_size": by_size},
        "p_team_vs_height": {"spearman": rho, "p": pval, "n": len(allr)},
        "rerank_top": rerank,
        "_per": per, "_protos": protos, "_imgs": imgs,
    }


# =============================================================================
# 主流程
# =============================================================================
def _pretty(v, nd=3):
    return "-" if v is None else ("%." + str(nd) + "f") % v


def _wavg(pairs):
    """[(value, weight), ...] -> 加权平均；忽略 None / 0 权重。"""
    num = 0.0
    den = 0.0
    for v, w in pairs:
        if v is None or not w:
            continue
        num += v * w
        den += w
    return (num / den) if den else None


def _combine(blocks):
    """把多段 _stab 结果按权重合并；键名不变。"""
    out = {}
    for key in ("family", "proto"):
        subs = [b[key] for b in blocks if b and b.get(key)]
        if not subs:
            out[key] = {}
            continue
        m = {}
        for f in ("n_tracks", "n_pairs", "n_tracks_short", "len_max"):
            m[f] = int(sum(s.get(f) or 0 for s in subs))
        m["strict"] = _wavg([(s["strict"], s["n_tracks"]) for s in subs])
        m["strict_short"] = _wavg([(s["strict_short"], s["n_tracks_short"])
                                   for s in subs])
        m["consec"] = _wavg([(s["consec"], s["n_pairs"]) for s in subs])
        m["shuffle_consec"] = _wavg([(s.get("shuffle_consec"), s["n_pairs"])
                                     for s in subs])
        m["shuffle_strict_short"] = _wavg([(s.get("shuffle_strict_short"),
                                            s["n_tracks_short"]) for s in subs])
        m["len_median"] = None
        out[key] = m
    return out


def _pool(reports):
    """多片段的时间一致性报表合并（按 pairs / tracks 加权）。"""
    res = {}
    for sub in ("all", "claims_team", "claims_context", "ring_control"):
        blocks = [r.get(sub) for r in reports]
        blocks = [b for b in blocks if b]
        c = _combine(blocks)
        c["subset"] = sub
        res[sub] = c
    strata = {}
    for r in reports:
        for s in r.get("by_size", []):
            k = (s["h_lo"], s["h_hi"])
            strata.setdefault(k, []).append(s)
    res["by_size"] = []
    for k in sorted(strata):
        ss = strata[k]
        n = int(sum(s.get("n_tracks") or 0 for s in ss))
        if not n:
            res["by_size"].append({"h_lo": k[0], "h_hi": k[1], "n_tracks": 0})
            continue
        res["by_size"].append({
            "h_lo": k[0], "h_hi": k[1], "n_tracks": n,
            "n_pairs": int(sum(s.get("n_pairs") or 0 for s in ss)),
            "consec": _wavg([(s["consec"], s.get("n_pairs") or 0) for s in ss]),
            "shuffle_consec": _wavg([(s["shuffle_consec"], s.get("n_pairs") or 0)
                                     for s in ss]),
            "strict": _wavg([(s["strict"], s.get("n_tracks") or 0) for s in ss]),
            "strict_short": _wavg([(s["strict_short"], s.get("n_tracks") or 0)
                                   for s in ss])})
    return res


def _pool_low(clips, field_map=None):
    """低分框统计按片段合并。"""
    lows = [c["low"] for c in clips]
    n = int(sum(x["n"] for x in lows))
    out = {"n": n}
    for k in ("torso_team", "ring_team", "torso_context", "ring_context",
              "torso_jersey", "ring_jersey"):
        out[k] = int(sum(x.get(k) or 0 for x in lows))
    for k in ("torso_team_rate", "ring_team_rate", "torso_context_rate",
              "ring_context_rate", "torso_jersey_rate", "ring_jersey_rate",
              "mean_p_team"):
        out[k] = _wavg([(x.get(k), x["n"]) for x in lows])
    out["by_size"] = []
    for i in range(4):
        ss = [x["by_size"][i] for x in lows if i < len(x["by_size"])]
        n2 = int(sum(s.get("n") or 0 for s in ss))
        if not n2:
            out["by_size"].append({"h_lo": ss[0]["h_lo"], "h_hi": ss[0]["h_hi"], "n": 0})
            continue
        out["by_size"].append({
            "h_lo": ss[0]["h_lo"], "h_hi": ss[0]["h_hi"], "n": n2,
            "torso_team_rate": _wavg([(s["torso_team_rate"], s.get("n") or 0) for s in ss]),
            "ring_team_rate": _wavg([(s["ring_team_rate"], s.get("n") or 0) for s in ss]),
            "torso_context_rate": _wavg([(s.get("torso_context_rate"), s.get("n") or 0)
                                         for s in ss]),
            "ring_context_rate": _wavg([(s.get("ring_context_rate"), s.get("n") or 0)
                                        for s in ss])})
    return out


def _np_default(o):
    """JSON 兜底：numpy 标量/数组 -> 原生类型（否则 json.dumps 直接报错）。"""
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, np.generic):
        return o.item()
    raise TypeError("Object of type %s is not JSON serializable" % type(o).__name__)


def _bad_paths(obj, path="", out=None):
    """找出结果里所有非原生 JSON 类型的位置（写盘前自检，别让 ndarray 混进 JSON）。"""
    out = [] if out is None else out
    if len(out) > 8:
        return out
    if isinstance(obj, np.ndarray):
        out.append("%s: ndarray%s" % (path or "$", obj.shape))
    elif isinstance(obj, np.generic):
        out.append("%s: %s" % (path or "$", type(obj).__name__))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _bad_paths(v, "%s.%s" % (path, k), out)
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _bad_paths(v, "%s[%d]" % (path, i), out)
    return out


def _round(obj, nd=3):
    """把浮点数压到 nd 位小数：报告用，省掉 0.7629574468085106 这种噪声。"""
    if isinstance(obj, float):
        return round(obj, nd)
    if isinstance(obj, dict):
        return {k: _round(v, nd) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_round(v, nd) for v in obj]
    return obj


def main(argv=None):
    ap = argparse.ArgumentParser(description="球衣颜色二次判据可行性验证")
    ap.add_argument("mode", choices=["clips", "frames", "all"])
    ap.add_argument("--clips", default="1471,1939,6000", help="源视频起始秒，逗号分隔")
    ap.add_argument("--clip-len", type=float, default=20.0)
    ap.add_argument("--fps", type=int, default=3)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--imgsz-frames", type=int, default=320,
                    help="calib 帧模式用的推理尺寸（默认 320，与板上输入一致）")
    ap.add_argument("--dir-calib", default="data/samples/calib_real/frames_calib")
    ap.add_argument("--dir-test", default="data/samples/calib_real/frames_test")
    ap.add_argument("--model", default="data/models/yolov8n.pt")
    ap.add_argument("--tag", default="hd")
    ap.add_argument("--viz-every", type=int, default=2)
    ap.add_argument("--shuffles", type=int, default=200)
    ap.add_argument("--inspect", type=int, default=10, help="人眼抽查图里放几个低分框")
    ap.add_argument("--human-notes", default=None)
    ap.add_argument("--out", default=str(OUT_JSON.relative_to(REPO)))
    args = ap.parse_args(argv)

    sys.stdout.reconfigure(encoding="utf-8")
    t_all = time.perf_counter()
    RESEARCH.mkdir(parents=True, exist_ok=True)

    weights = Path(args.model)
    if not weights.is_absolute():
        weights = REPO / weights
    if not weights.exists():
        print("JERSEY_FAIL 模型不存在 %s" % weights)
        return 1
    from ultralytics import YOLO
    model = YOLO(str(weights))

    result = {"tool": "tools/jersey_color.py", "model": weights.name,
              "conf_lo": CONF_LO, "conf": CONF, "conf_hi": CONF_HI,
              "torso": {"top": T_TOP, "bot": T_BOT, "inset": T_INSET},
              "ring_margin": RING_MARGIN, "min_torso_px": MIN_TORSO_PX,
              "clustering": {"k": K_MAX, "merge": MERGE_D, "min_w": MIN_W, "seed": SEED}}

    if args.mode in ("clips", "all"):
        if not VIDEO.exists():
            print("JERSEY_FAIL 源视频不存在")
            return 1
        clips = []
        for cs in args.clips.split(","):
            t0 = float(cs)
            d = RESEARCH / ("clip_%d" % int(t0))
            files = extract_clip(t0, args.clip_len, args.fps, d)
            if len(files) < 5:
                print("JERSEY_FAIL 抽帧不足 %s: %d" % (cs, len(files)))
                return 1
            r = run_clip(model, "t%d" % int(t0), files, args.imgsz, args.tag,
                         args.viz_every, args, t0)
            if r is None:
                print("JERSEY_FAIL 色板学习失败 %s" % cs)
                return 1
            clips.append(r)
        result["clips"] = [{k: v for k, v in c.items()
                            if not k.startswith("_")} for c in clips]
        result["pooled"] = _pool([c["temporal"] for c in clips])
        result["low_pooled"] = _pool_low(clips)
        # 抽查图
        picks = _pick_inspect(clips, args.inspect)
        path = OUT_VIZ / args.tag / ("_inspect_%s.png" % args.tag)
        inspect_montage(picks, [], path)
        result["inspect_image"] = str(path.relative_to(REPO))
        result["inspect_boxes"] = [{"#": i + 1, "clip": p[4], "score": p[0]["score"],
                                    "h": p[0]["h"], "fam": p[0]["fam"],
                                    "p_team": round(p[0]["p_team"], 3),
                                    "ring_fam": p[0]["ring_fam"],
                                    "ring_team": p[0]["ring_team"],
                                    "box": p[0]["box"]} for i, p in enumerate(picks)]
        print("  抽查图 -> %s" % path.relative_to(REPO))
    if args.mode in ("frames", "all"):
        tag2 = args.tag if args.mode == "frames" else "deploy"
        isz2 = args.imgsz if args.mode == "frames" else args.imgsz_frames
        files = ecs.list_images(REPO / args.dir_calib) + ecs.list_images(REPO / args.dir_test)
        if not files:
            print("JERSEY_FAIL 标定帧目录为空")
            return 1
        r = run_frames(model, files, isz2, tag2, args)
        if r is None:
            print("JERSEY_FAIL 色板学习失败")
            return 1
        result["deploy"] = {k: v for k, v in r.items() if not k.startswith("_")}
        # 抽查图：从低分框里挑（上下文/躯干/环带并排）
        low = [x for d in r["_per"] for x in d["rows"] if CONF_LO <= x["score"] < CONF]
        fmap = {}
        for (p, im), d in zip(r["_imgs"], r["_per"]):
            for x in d["rows"]:
                fmap[id(x)] = im
        picks = _pick_inspect_from(low, fmap, args.inspect)
        path = OUT_VIZ / tag2 / ("_inspect_%s.png" % tag2)
        inspect_montage(picks, [], path)
        result["deploy"]["inspect_image"] = str(path.relative_to(REPO))
        result["deploy"]["inspect_boxes"] = [
            {"#": i + 1, "score": p[0]["score"], "h": p[0]["h"],
             "fam": p[0]["fam"], "p_team": round(p[0]["p_team"], 3),
             "ring_fam": p[0]["ring_fam"], "ring_team": p[0]["ring_team"],
             "box": p[0]["box"]} for i, p in enumerate(picks)]
        print("  抽查图 -> %s" % path.relative_to(REPO))
        if args.mode == "frames":
            result["inspect_image"] = result["deploy"]["inspect_image"]
            result["inspect_boxes"] = result["deploy"]["inspect_boxes"]

    # 人工复核并入
    if args.human_notes:
        hp = Path(args.human_notes)
        if not hp.is_absolute():
            hp = REPO / hp
        if hp.exists():
            with open(hp, encoding="utf-8") as f:
                result["human_check"] = json.load(f)

    out = Path(args.out)
    if not out.is_absolute():
        out = REPO / out
    bad = _bad_paths(result)
    if bad:
        print("  非原生 JSON 类型（已自动转换）：%s" % "; ".join(bad))
    result = _round(result, 3)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(result, ensure_ascii=False, indent=1, default=_np_default))

    print("")
    print("=" * 78)
    p = result.get("pooled")
    if p:
        a = p["all"]["family"]
        c = p["claims_team"]["family"]
        print("主判据 时间一致性(族级, tracks>=5)：n=%d consec=%s 基线=%s | strict=%s"
              % (a["n_tracks"], _pretty(a["consec"]), _pretty(a["shuffle_consec"]),
                 _pretty(a["strict"])))
        print("  仅'自认为抓到队色'的轨迹：n=%d consec=%s 基线=%s | strict_short=%s"
              % (c["n_tracks"], _pretty(c["consec"]), _pretty(c["shuffle_consec"]),
                 _pretty(c["strict_short"])))
        rc = p.get("ring_control", {}).get("family", {})
        if rc:
            print("  负对照-环带标签的时间一致性：n=%d consec=%s 基线=%s（若与躯干同级，"
                  "则'稳定'只说明球员一直站在同一块场地上）"
                  % (rc.get("n_tracks") or 0, _pretty(rc.get("consec")),
                     _pretty(rc.get("shuffle_consec"))))
        for s in p["by_size"]:
            print("  按框高分档 h∈[%d,%d)：n=%3d consec=%s 基线=%s"
                  % (s["h_lo"], s["h_hi"], s["n_tracks"], _pretty(s.get("consec")),
                     _pretty(s.get("shuffle_consec"))))
    lp = result.get("low_pooled")
    if lp:
        print("低分框 [%.2f,%.2f)：n=%d 躯干判为队色 %d (%.3f) vs 环带 %.3f | "
              "躯干判为场地色 %.3f vs 环带 %.3f | 任一球衣色 %.3f vs %.3f"
              % (CONF_LO, CONF, lp["n"], lp["torso_team"],
                 lp["torso_team_rate"] or 0, lp["ring_team_rate"] or 0,
                 lp["torso_context_rate"] or 0, lp["ring_context_rate"] or 0,
                 lp["torso_jersey_rate"] or 0, lp["ring_jersey_rate"] or 0))
    print("JSON -> %s (%.1f KB)  用时 %.1fs"
          % (out.relative_to(REPO), out.stat().st_size / 1024.0, time.perf_counter() - t_all))
    print("JERSEY_OK %d" % (len(result.get("clips", [])) or len(result.get("deploy", {}))))
    return 0


def _pick_inspect(clips, n):
    """挑 n 个低分框做抽查：一半"颜色说是球员"、一半"颜色说是背景"，各按分数排序。

    刻意两边都取：只抽"成功"的那一半等于自证。要能看出误判，必须两侧都看。
    """
    cands = []
    for c in clips:
        for rows in c["_frames_rows"]:
            for r in rows:
                if CONF_LO <= r["score"] < CONF:
                    cands.append((r, c["_imgs"][r["fi"]], c, c["clip"]))
    if not cands:
        return []
    pos = [x for x in cands if x[0].get("team")]
    neg = [x for x in cands if not x[0].get("team")]
    pos.sort(key=lambda x: -x[0]["score"])
    neg.sort(key=lambda x: x[0]["score"])
    out = []
    k = 0
    while len(out) < n and k < max(len(pos), len(neg)):
        if k < len(pos) and len(out) < n:
            out.append(pos[k])
        if k < len(neg) and len(out) < n:
            out.append(neg[k])
        k += 1
    return _crop_pairs(out)


def _pick_inspect_from(low, fmap, n):
    pos = [r for r in low if r.get("team")]
    neg = [r for r in low if not r.get("team")]
    pos.sort(key=lambda r: -r["score"])
    neg.sort(key=lambda r: r["score"])
    out = []
    k = 0
    while len(out) < n and k < max(len(pos), len(neg)):
        if k < len(pos) and len(out) < n:
            out.append((pos[k], fmap[id(pos[k])], None, "deploy"))
        if k < len(neg) and len(out) < n:
            out.append((neg[k], fmap[id(neg[k])], None, "deploy"))
        k += 1
    return _crop_pairs(out)


def _strip(sub, mask, x0, x1):
    """取 mask 在列区间 [x0,x1) 内的像素外接矩形切片（纯背景，不含人物）。"""
    part = mask[:, x0:x1]
    idx = np.where(part)
    if len(idx[0]) < 4:
        return None
    y0, y1 = int(idx[0].min()), int(idx[0].max()) + 1
    ax0, ax1 = int(idx[1].min()) + x0, int(idx[1].max()) + 1 + x0
    return sub[y0:y1, ax0:ax1].copy()


def _crop_pairs(items):
    """(rec, img, clip, name) -> (rec, torso_img, ring_img, ctx_img, name)。

    环带切成左/右两段分别取切片再横拼：**中间那个人的像素被彻底排除**，
    否则"背景图"里还带着球员，负对照就是假的。ctx = 框外扩 80% 的上下文，
    用来人眼判断这个框到底是球员 / 广告牌 / 球网。
    """
    out = []
    for rec, im, c, nm in items:
        b = np.array(rec["box"], dtype=np.float64)
        tr = torso_rect(b, im.shape)
        timg = im[tr[1]:tr[3], tr[0]:tr[2]].copy() if tr is not None else None
        rr, rmask = ring_mask(b, im.shape, [])
        rimg = None
        if rmask is not None and rmask.any():
            sub = im[rr[1]:rr[3], rr[0]:rr[2]]
            mid = int(max(0, min(rmask.shape[1], (b[0] + b[2]) / 2.0 - rr[0])))
            parts = [p for p in (_strip(sub, rmask, 0, mid),
                                 _strip(sub, rmask, mid, rmask.shape[1])) if p is not None]
            if parts:
                hh = max(p.shape[0] for p in parts)
                parts = [cv2.copyMakeBorder(p, 0, hh - p.shape[0], 0, 2,
                                            cv2.BORDER_CONSTANT, value=(26, 26, 26))
                         for p in parts]
                rimg = np.hstack(parts)
        # 上下文切片：框外扩 80%（各边 0.4*宽/高），越界裁掉
        H, Wd = im.shape[:2]
        bw, bh = b[2] - b[0], b[3] - b[1]
        cx0 = int(max(0, b[0] - 0.4 * bw))
        cy0 = int(max(0, b[1] - 0.4 * bh))
        cx1 = int(min(Wd, b[2] + 0.4 * bw))
        cy1 = int(min(H, b[3] + 0.4 * bh))
        cimg = im[cy0:cy1, cx0:cx1].copy() if cx1 > cx0 and cy1 > cy0 else None
        if cimg is not None:
            # 在原框位置画一圈亮绿，人眼才看得出"被判的是哪一块"
            cv2.rectangle(cimg, (int(b[0]) - cx0, int(b[1]) - cy0),
                          (int(b[2]) - cx0, int(b[3]) - cy0), (80, 255, 80), 1)
        out.append((rec, timg, rimg, cimg, nm))
    return out


if __name__ == "__main__":
    raise SystemExit(main())
