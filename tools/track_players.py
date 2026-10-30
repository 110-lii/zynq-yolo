"""球员检测 / 跟踪 / 轨迹 / 倒地候选 —— D011 的第一块地基。

为什么先做这个
--------------
D011（2026-10-20）把重点定为「人 + 球」的目标识别，只做简单事件（球出界后的球权、
角球/球门球）与**人员倒地**，可选交付**球员轨迹**。这四件里：

    轨迹        <- 必须有跟踪（本文件）
    球权/角球   <- 需要「谁最后触球」= 球 + 人的跟踪 + 队别
    倒地        <- 需要每个人的框尺寸/长宽比时间序列（本文件）

所以先把「逐帧检测 -> 轨迹编号 -> 稳定后的落脚点」做出来，后面三件都建在它上面。

★ 相机运动必须补偿（这是转播画面，不是固定机位）
  转播机位会跟拍（平移 + 变焦）。不做补偿的话，同一名球员的「轨迹」会带着相机的整体
  位移，看起来人人都在横穿球场 —— 这种轨迹拿去算队形/球权都是错的。
  做法：对相邻帧的灰度缩略图估仿射（复用 tools/ball_cv.py 的 estimate_motion：
  相位相关给初值 + ECC 细化，且用「代进去 warp 谁的残差小就用谁」自检方向），
  把逐帧变换累乘成全局变换，再把落脚点反变换回**首帧坐标系**。

★ 倒地的判据（v1 是几何启发式，不是姿态估计）
  转播远景里球员只有 10~40 px 高，姿态关键点（yolov8n-pose）在这个尺度上不可靠，
  所以 v1 只用框的几何 + 时间连续性：
    1) 相对高度 rel_h = 框高 / 该帧所有人框高的中位数（消除变焦影响）
    2) 长宽比 ar = 框宽 / 框高
    3) 候选 = rel_h 掉到该轨迹自身基线的 0.7 倍以下，且 ar > 1.0，连续 >= 3 帧
  这是**候选**不是结论：必须用人工标注核（`data/samples/incidents/MANIFEST.csv` 的
  用户裁定里含「倒地」的事件可作粗标签）。没有标注就不许把候选数当准确率。

用法
----
    & D:\\python\\python.exe tools\\track_players.py --clips data/samples/incidents --stride 2
    ... --max-clips 2        # 只跑前 2 段（调试）
    ... --imgsz 640          # 检测输入尺寸（D010 实测：640 相对召回 89%，320 只有 60%）
    ... --video path.mp4 --t0 100 --dur 20    # 直接给一段原视频

产物
----
    data/results/player_tracks.json          汇总（可直接写进报告的量）
    data/results/tracks/<clip>.json          逐段明细（轨迹点、倒地候选）
    data/results/tracks/<clip>.csv           轨迹表（track_id,frame,x,y,bbox...），便于外部分析
    data/results/viz/tracks/<clip>_f<NNNN>.png  抽帧可视化（.gitignore 已排除 viz/）

判据令牌（repo_spec §5：令牌，不看退出码）
    TRACKS_DONE <n_clips> <n_tracks> <n_fall_strong> <n_fall_weak>
    TRACKS_FAIL <reason>
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True          # 禁止 __pycache__（check_repo.py check d 会扫盘）

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
TRACKS_DIR = RESULTS / "tracks"
VIZ_DIR = RESULTS / "viz" / "tracks"

MODEL_DEFAULT = REPO / "data" / "models" / "yolov8n.pt"

# ---- 跟踪/判定参数（全部可被命令行覆盖，写在这里便于报告引用） ----
CONF_DEFAULT = 0.25        # 与 D010 的标定口径一致（tools/conf_calibration.py）
IOU_DEFAULT = 0.45
MIN_BOX_PX = 6             # 小于此高度的框不参与（远景噪声）
FALL_MIN_LEN = 8           # 轨迹至少这么多帧才判倒地候选
FALL_REL_DROP = 0.70       # 相对高度掉到自身基线（p90）的 70% 以下
# 倒地判据分两级。第一版用「aspect>1.0 + 3 帧」在 12 段真值上跑出
# 召回 4/4、精度 4/8 —— 误报全部是「框被遮挡/被画幅裁切」或「极远小人」，
# 高度掉了但人并没倒。所以把「够横 / 够大 / 够久」三条同时作为**强候选**门槛，
# 未达标的仍作为**弱候选**如实上报，不假装没看见。
FALL_ASPECT_MIN = 1.35     # 强候选：宽/高 > 1.35（躺倒横向）
FALL_MIN_H_PX = 40.0       # 强候选：框高 >= 40 px（更小的目标几何不可靠）
FALL_MIN_FRAMES = 4        # 强候选：连续满足 >= 4 帧
                           # ★ 2026-10-20 用户人工复核后由 5 改为 4：真倒地 #22
                           # （inc_10 tid8179，4 帧）被 5 帧门槛漏掉；改为 4 帧后
                           # 同一批数据上只多出 1 个未复核候选、0 个已知误报。
FALL_WEAK_ASPECT_MIN = 1.0 # 弱候选门槛（只记录，不计入强判据）
FALL_WEAK_MIN_FRAMES = 3


# =============================================================================
# 相机运动补偿
# =============================================================================
def _small_gray(frame, width=320):
    h, w = frame.shape[:2]
    scale = width / float(w)
    small = cv2.resize(frame, (width, max(1, int(round(h * scale)))),
                       interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), scale


def _phase_shift(a, b):
    try:
        (dx, dy), resp = cv2.phaseCorrelate(
            np.float32(a), np.float32(b),
            cv2.createHanningWindow(a.shape[::-1], cv2.CV_32F))
    except cv2.error:
        return None, 0.0
    if not np.isfinite(dx) or not np.isfinite(dy):
        return None, 0.0
    return (float(dx), float(dy)), float(resp)


def _mae(a, b):
    return float(cv2.absdiff(a, b).mean())


def estimate_motion(prev_s, cur_s):
    """估 prev -> cur 的仿射（小图坐标）。返回 (M 2x3, kind, cc)。

    与 tools/ball_cv.py 同源做法：相位相关给初值，ECC 细化；**方向不猜**，
    两候选都代进去 warp，残差更小者胜（方向搞反时残差会变大，自检不会错）。
    """
    h, w = cur_s.shape
    best_e = _mae(prev_s, cur_s)
    M = np.eye(2, 3, dtype=np.float32)
    kind, cc = "identity", 0.0

    sh, resp = _phase_shift(prev_s, cur_s)
    if sh is not None and abs(sh[0]) < w / 4.0 and abs(sh[1]) < h / 4.0:
        for cand in (np.array([[1, 0, sh[0]], [0, 1, sh[1]]], np.float32),
                     np.array([[1, 0, -sh[0]], [0, 1, -sh[1]]], np.float32)):
            e = _mae(cv2.warpAffine(prev_s, cand, (w, h), flags=cv2.INTER_LINEAR,
                                    borderMode=cv2.BORDER_REPLICATE), cur_s)
            if e < best_e:
                best_e, M, kind, cc = e, cand, "trans", float(resp)

    try:
        crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 1e-4)
        cc2, Mw = cv2.findTransformECC(prev_s, cur_s, M.copy(), cv2.MOTION_AFFINE,
                                       crit, None, 5)
        if np.isfinite(cc2) and cc2 > 0.3:
            for cand, tag in ((Mw.astype(np.float32), "affine"),
                              (cv2.invertAffineTransform(Mw).astype(np.float32), "affine_inv")):
                e = _mae(cv2.warpAffine(prev_s, cand, (w, h), flags=cv2.INTER_LINEAR,
                                        borderMode=cv2.BORDER_REPLICATE), cur_s)
                if e < best_e:
                    best_e, M, kind, cc = e, cand, tag, float(cc2)
    except cv2.error:
        pass
    return M, kind, cc


def _compose(A, B):
    """compose(A, B) = A ∘ B（都按 2x3 仿射，先 B 后 A）。"""
    A3 = np.vstack([A, [0, 0, 1]])
    B3 = np.vstack([B, [0, 0, 1]])
    return (A3 @ B3)[:2, :].astype(np.float32)


# =============================================================================
# 跟踪
# =============================================================================
def _open_video(path):
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError("cannot open video: %s" % path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    return cap, float(fps), w, h


def apply_calib(tracks, Hm):
    """把每条轨迹的每个点映射到球场米制，写 X/Y（不覆盖 fx/fy）。

    为什么用 `sx/sy` 而不是 `fx/fy`：本文件里 fx/fy 是**图像像素**的落脚点
    （相机补偿后的 sx/sy 才在"首帧坐标系"里），而标定的约定就是「该机位首帧」。
    只写 X/Y、并单独给出 `n_field`，是为了让"没映射上"这件事可见，
    而不是把 0 混进一堆坐标里。

    返回：成功映射的点数（全片段累计）。
    """
    from field_coords import apply_h
    n_ok = 0
    for tr in tracks:
        nf = 0
        for pt in tr.get("points", []):
            f = apply_h(Hm, pt["sx"], pt["sy"])
            if f is None:
                pt["X"] = pt["Y"] = None
            else:
                pt["X"] = round(f[0], 2)
                pt["Y"] = round(f[1], 2)
                nf += 1
        tr["n_field"] = nf
        n_ok += nf
    return n_ok


def track_clip(model, clip_path, args):
    _pf = None
    if getattr(args, "color_filter", False):
        import player_color_filter as _pf_mod     # 只在开开关时导入，避免无谓依赖
        _pf = _pf_mod
    n_color_drop = 0
    """对一段片段做「检测 + ByteTrack 关联 + 相机补偿 + 倒地候选」。

    返回 dict（含轨迹明细与统计），**所有数字都可重算**。
    """
    cap, fps, W, H = _open_video(clip_path)
    stride = max(1, int(args.stride))
    # 可选：球场单应（field_coords.py 产出的 calib.json）。给了才出 X/Y（米）。
    Hm = None
    if getattr(args, "calib", ""):
        _c = json.load(open(args.calib, encoding="utf-8"))
        Hm = _c.get("H")
        if Hm is None:
            print("CALIB_NO_H %s（缺 H 字段，按未标定处理）" % args.calib)

    rows = []                 # (frame_idx, t_sec, track_id, x1,y1,x2,y2, conf)
    cam_rows = []             # (frame_idx, kind, cc, dx, dy, scale)
    cum_by_frame = {}         # frame_no -> 首帧到该帧的累计仿射（2x3，全图坐标）
    prev_small = None
    cum = np.eye(2, 3, dtype=np.float32)   # 首帧 -> 当前帧 的累计变换
    frame_no = -1
    used = 0
    t_start = time.time()

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frame_no += 1
        if frame_no % stride:
            continue

        small, scale = _small_gray(frame)
        if prev_small is not None:
            M, kind, cc = estimate_motion(prev_small, small)
            # 小图坐标 -> 全图坐标（平移项乘 1/scale；旋转/缩放项不变）
            Mf = M.copy()
            Mf[0, 2] /= scale
            Mf[1, 2] /= scale
            cum = _compose(Mf, cum)
            # 平移量按全图像素记录（scale 取 M 的线性部分近似）
            dx = float(Mf[0, 2])
            dy = float(Mf[1, 2])
            sc = float(math.sqrt(abs(Mf[0, 0] * Mf[1, 1] - Mf[0, 1] * Mf[1, 0])) or 1.0)
            cam_rows.append((frame_no, kind, round(cc, 4), round(dx, 3), round(dy, 3), round(sc, 5)))
        prev_small = small
        cum_by_frame[frame_no] = cum.copy()

        res = model.track(frame, persist=True, classes=[0], conf=args.conf,
                          iou=args.iou, imgsz=args.imgsz,
                          tracker="bytetrack.yaml", verbose=False)
        if res and res[0].boxes is not None and res[0].boxes.id is not None:
            boxes = res[0].boxes
            ids = boxes.id.int().cpu().numpy()
            xyxy = boxes.xyxy.cpu().numpy()
            confs = boxes.conf.cpu().numpy()
            items = []
            for tid, bb, cf in zip(ids, xyxy, confs):
                x1, y1, x2, y2 = (float(v) for v in bb)
                if (y2 - y1) < MIN_BOX_PX:
                    continue
                items.append({"id": int(tid), "box": [x1, y1, x2, y2],
                              "conf": float(cf)})
            if args.color_filter:
                # 球衣颜色筛人（D012/D013-a）：剔观众/裁判，低置信框按颜色补检。
                # 低置信下限取当前 conf，避免把检测器已经放行的框再按阈值砍掉。
                kept, _dropped, _st = _pf.filter_players(
                    frame, items, args.team_colors, args.ref_color,
                    adaptive=True, keeper_colors=args.keeper_colors,
                    low_conf=args.conf)
                n_color_drop += len(items) - len(kept)
                items = kept
            for it in items:
                x1, y1, x2, y2 = it["box"]
                rows.append((frame_no, round(frame_no / fps, 3), it["id"],
                             round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1),
                             round(float(it["conf"]), 4)))
            used += 1
        n_read = frame_no

    cap.release()
    elapsed = time.time() - t_start

    # ---- 归并轨迹 ----
    by_track: dict[int, list[tuple]] = {}
    for r in rows:
        by_track.setdefault(r[2], []).append(r)

    # 逐帧所有人框高的中位数（用于相对高度，消除变焦影响）
    heights_by_frame: dict[int, list[float]] = {}
    for r in rows:
        heights_by_frame.setdefault(r[0], []).append(r[6] - r[4])
    med_h = {f: float(np.median(v)) for f, v in heights_by_frame.items() if v}

    tracks = []
    inv_cache = {}

    def to_stab(frame_idx, cx, cy):
        """当前帧坐标 -> 首帧坐标系（用该帧累计变换的逆）。"""
        if frame_idx not in inv_cache:
            M = cum_by_frame.get(frame_idx)
            if M is None:
                M = np.eye(2, 3, dtype=np.float32)
            inv_cache[frame_idx] = cv2.invertAffineTransform(M)
        Mi = inv_cache[frame_idx]
        return (float(Mi[0, 0] * cx + Mi[0, 1] * cy + Mi[0, 2]),
                float(Mi[1, 0] * cx + Mi[1, 1] * cy + Mi[1, 2]))
    for tid in sorted(by_track):
        pts = sorted(by_track[tid], key=lambda r: r[0])
        if len(pts) < FALL_MIN_LEN:
            continue
        track = {
            "track_id": tid,
            "n_frames": len(pts),
            "f0": pts[0][0],
            "f1": pts[-1][0],
            "t0_s": pts[0][1],
            "t1_s": pts[-1][1],
            "mean_conf": round(float(np.mean([p[7] for p in pts])), 4),
            "points": [
                {"f": p[0], "t": p[1],
                 "cx": round((p[3] + p[5]) / 2.0, 1),
                 "cy": round((p[4] + p[6]) / 2.0, 1),
                 "fx": round((p[3] + p[5]) / 2.0, 1),      # 落脚点 x（框底中点）
                 "fy": round(p[6], 1),                     # 落脚点 y
                 "sx": round(to_stab(p[0], (p[3] + p[5]) / 2.0, p[6])[0], 1),
                 "sy": round(to_stab(p[0], (p[3] + p[5]) / 2.0, p[6])[1], 1),
                 "w": round(p[5] - p[3], 1),
                 "h": round(p[6] - p[4], 1),
                 "conf": p[7]}
                for p in pts
            ],
        }
        fcs = _fall_candidates(pts, med_h)
        track["fall_candidates"] = fcs
        track["n_fall_strong"] = sum(1 for f in fcs if f["tier"] == "strong")
        track["n_fall_weak"] = sum(1 for f in fcs if f["tier"] == "weak")
        tracks.append(track)

    n_field = apply_calib(tracks, Hm) if Hm is not None else 0

    return {
        "clip": Path(clip_path).name,
        "clip_path": str(Path(clip_path).resolve()),
        "fps": fps,
        "size": [W, H],
        "stride": stride,
        "frames_read": frame_no + 1,
        "frames_used": used,
        "wall_time_s": round(elapsed, 2),
        "camera_motion": _cam_stats(cam_rows),
        "n_tracks": len(tracks),
        "n_color_drop": n_color_drop,
        "calib": (args.calib or None),
        "field_points": n_field,
        "tracks": tracks,
    }


def _cam_stats(cam_rows):
    if not cam_rows:
        return {"n": 0}
    kinds = {}
    for r in cam_rows:
        kinds[r[1]] = kinds.get(r[1], 0) + 1
    dxs = np.array([r[3] for r in cam_rows])
    dys = np.array([r[4] for r in cam_rows])
    scs = np.array([r[5] for r in cam_rows])
    return {
        "n": len(cam_rows),
        "kind_hist": kinds,
        "mean_abs_dx": round(float(np.mean(np.abs(dxs))), 2),
        "mean_abs_dy": round(float(np.mean(np.abs(dys))), 2),
        "p95_abs_dx": round(float(np.percentile(np.abs(dxs), 95)), 2),
        "p95_abs_dy": round(float(np.percentile(np.abs(dys), 95)), 2),
        "scale_range": [round(float(scs.min()), 4), round(float(scs.max()), 4)],
        "identity_rate": round(kinds.get("identity", 0) / float(len(cam_rows)), 3),
    }


def _fall_candidates(pts, med_h):
    """框几何 + 时间连续性的倒地候选（v1 启发式，不是姿态估计）。

    分**强/弱**两级，原因见 FALL_ASPECT_MIN 上方注释（第一版精度只有 4/8）：
      强候选（strong）= 高度相对基线掉落 >= 30%  AND 宽/高 > 1.35
                        AND 该段框高 >= 40 px AND 连续 >= 5 帧
      弱候选（weak）  = 高度掉落 AND 宽/高 > 1.0 AND 连续 >= 3 帧（但未达强门槛）
    强候选用作判据；弱候选**如实上报**，避免"看起来干净"掩盖真实误报。
    """
    out = []
    if len(pts) < FALL_MIN_LEN:
        return out
    hs = np.array([p[6] - p[4] for p in pts], dtype=float)
    ws = np.array([p[5] - p[3] for p in pts], dtype=float)
    # 站立基线取框高的 p90 而不是「前 1/3 帧的中位数」：
    # 若轨迹本身从「已经倒地」的时刻才开始（常见），前 1/3 帧就是躺姿，
    # 基线会被拉低 ⇒ 永远判不出倒地。p90 取「这个人最高的样子」= 站立，
    # 对「先站后倒」和「一直躺」两种轨迹都稳。
    base = float(np.percentile(hs, 90))

    drop_flags, strong_flags = [], []
    for k, p in enumerate(pts):
        h = hs[k]
        w = ws[k]
        ar = w / h if h > 0 else 0.0
        mh = med_h.get(p[0], 0.0)
        rel = (h / mh) if mh > 0 else 1.0
        base_rel = (base / mh) if mh > 0 else 1.0
        dropped = (rel < base_rel * FALL_REL_DROP) if mh > 0 else (h < base * FALL_REL_DROP)
        drop_flags.append(bool(dropped))
        strong_flags.append(bool(dropped and ar > FALL_ASPECT_MIN and h >= FALL_MIN_H_PX))

    def _runs(flags, min_frames, tier):
        found = []
        run = None
        for i, f in enumerate(list(flags) + [False]):
            if f and run is None:
                run = i
            elif not f and run is not None:
                if i - run >= min_frames:
                    seg = pts[run:i]
                    found.append({
                        "tier": tier,
                        "f_start": seg[0][0], "f_end": seg[-1][0],
                        "n_frames": i - run,
                        "t_start_s": seg[0][1], "t_end_s": seg[-1][1],
                        "h_min": round(float(np.min(hs[run:i])), 1),
                        "h_base_p90": round(base, 1),
                        "aspect_max": round(float(np.max(ws[run:i] / np.maximum(hs[run:i], 1e-6))), 2),
                    })
                run = None
        return found

    out.extend(_runs(strong_flags, FALL_MIN_FRAMES, "strong"))
    # 弱候选只保留「没被强候选覆盖」的那些，避免同一件事报两次
    strong_span = {(fc["f_start"], fc["f_end"]) for fc in out}
    for fc in _runs(drop_flags, FALL_WEAK_MIN_FRAMES, "weak"):
        overlap = any(fc["f_start"] <= se and fc["f_end"] >= ss for ss, se in strong_span)
        if not overlap:
            out.append(fc)
    return out

# =============================================================================
# 输出
# =============================================================================
def write_outputs(results, args):
    TRACKS_DIR.mkdir(parents=True, exist_ok=True)
    VIZ_DIR.mkdir(parents=True, exist_ok=True)

    summary = {
        "tool": "tools/track_players.py",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "decision": "D011（人+球；简单事件；倒地；可选轨迹）",
        "params": {"conf": args.conf, "iou": args.iou, "imgsz": args.imgsz,
                   "stride": args.stride, "min_box_px": MIN_BOX_PX,
                   "calib": (args.calib or None),
                   "fall": {"min_len": FALL_MIN_LEN, "rel_drop": FALL_REL_DROP,
                            "aspect_min_strong": FALL_ASPECT_MIN,
                            "min_h_px_strong": FALL_MIN_H_PX,
                            "min_frames_strong": FALL_MIN_FRAMES,
                            "aspect_min_weak": FALL_WEAK_ASPECT_MIN,
                            "min_frames_weak": FALL_WEAK_MIN_FRAMES}},
        "tracker": "ultralytics ByteTrack（bytetrack.yaml）",
        "clips": [],
        "totals": {},
        "limitations": ([
            "轨迹坐标已做相机运动补偿（首帧坐标系）。**未给 --calib 时单位仍是像素**，不是米；"
            "给了 --calib 才在 points 里额外写 X/Y（米，输入为 sx/sy）",
            "倒地判据是框几何启发式，**未经人工标注验证**；候选数 != 准确率",
            "ByteTrack 在剧烈跟拍/遮挡下会 ID switch；本文件只报轨迹长度分布，无法自证 ID 一致性",
            "队别：本文件只有 `--color-filter/--team-colors` 的**颜色过滤**（不输出每人的队别）；"
            "每人队别见 tools/team_assign.py，球权见 tools/events.py",
        ] + ([
            "单应只对标定过的机位有效；本片段用 %s，标定残差与有效区域见该文件" % args.calib,
        ] if getattr(args, "calib", "") else [])),
    }

    n_tracks = 0
    n_fall = 0
    n_fall_weak = 0
    for r in results:
        clip = r["clip"]
        short = Path(clip).stem
        (TRACKS_DIR / ("%s.json" % short)).write_text(
            json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")

        # CSV：一行一个轨迹点，便于外部分析（Excel/pandas 可直接读）
        with open(TRACKS_DIR / ("%s.csv" % short), "w", encoding="utf-8",
                  newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["track_id", "frame", "t_s", "foot_x", "foot_y",
                        "cx", "cy", "w", "h", "conf"])
            for t in r["tracks"]:
                for p in t["points"]:
                    w.writerow([t["track_id"], p["f"], p["t"], p["fx"], p["fy"],
                                p["cx"], p["cy"], p["w"], p["h"], p["conf"]])

        lens = sorted((t["n_frames"] for t in r["tracks"]), reverse=True)
        falls = sum(t.get("n_fall_strong", 0) for t in r["tracks"])
        falls_weak = sum(t.get("n_fall_weak", 0) for t in r["tracks"])
        n_tracks += len(r["tracks"])
        n_fall += falls
        n_fall_weak += falls_weak
        summary["clips"].append({
            "clip": clip,
            "frames_used": r["frames_used"],
            "n_tracks": len(r["tracks"]),
            "track_len": {"max": lens[0] if lens else 0,
                          "median": float(np.median(lens)) if lens else 0,
                          "n_ge_25": sum(1 for v in lens if v >= 25),
                          "n_ge_75": sum(1 for v in lens if v >= 75),
                          "n_short_lt_12": sum(1 for v in lens if v < 12)},
            "n_fall_strong": falls,
            "n_fall_weak": falls_weak,
            "camera_motion": r["camera_motion"],
            "wall_time_s": r["wall_time_s"],
        })

    tot_frames = sum(c["frames_used"] for c in summary["clips"])
    summary["totals"] = {
        "n_clips": len(summary["clips"]),
        "frames_used": tot_frames,
        "n_tracks": n_tracks,
        "n_fall_strong": n_fall,
        "n_fall_weak": n_fall_weak,
        "tracks_per_frame": round(n_tracks / tot_frames, 3) if tot_frames else 0,
    }
    (RESULTS / "player_tracks.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    return summary


def _draw_clip(model, clip_path, args, r, every=200):
    """抽帧可视化：画框 + ID + 轨迹尾迹 + 倒地候选标记（只画少量帧，省时间）。"""
    cap, fps, W, H = _open_video(clip_path)
    stride = max(1, int(args.stride))
    want = {}
    for t in r["tracks"]:
        for p in t["points"]:
            if p["f"] % every == 0:
                want.setdefault(p["f"], []).append((t["track_id"], p))
    fall_marks = set()
    for t in r["tracks"]:
        for fc in t["fall_candidates"]:
            for f in range(fc["f_start"], fc["f_end"] + 1):
                fall_marks.add((t["track_id"], f))
    frame_no = -1
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frame_no += 1
        if frame_no % stride:
            continue
        if frame_no not in want:
            continue
        for tid, p in want[frame_no]:
            x1, y1 = p["cx"] - p["w"] / 2, p["cy"] - p["h"] / 2
            x2, y2 = p["cx"] + p["w"] / 2, p["cy"] + p["h"] / 2
            color = (0, 0, 255) if (tid, frame_no) in fall_marks else (0, 255, 0)
            cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), color, 2)
            cv2.putText(frame, "id%d" % tid, (int(x1), max(12, int(y1) - 4)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
            cv2.circle(frame, (int(p["fx"]), int(p["fy"])), 3, (255, 0, 0), -1)
        cv2.putText(frame, "%s f=%d" % (Path(clip_path).stem, frame_no), (8, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2, cv2.LINE_AA)
        out = VIZ_DIR / ("%s_f%04d.jpg" % (Path(clip_path).stem, frame_no))
        cv2.imwrite(str(out), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    cap.release()


# =============================================================================
# 主流程
# =============================================================================
def _collect_clips(args):
    clips = []
    if args.video:
        clips.append(Path(args.video))
        return clips
    d = Path(args.clips)
    if d.is_file():
        return [d]
    for p in sorted(d.glob("*.mp4")):
        clips.append(p)
    if args.max_clips:
        clips = clips[: args.max_clips]
    return clips


def main(argv=None):
    ap = argparse.ArgumentParser(description="球员检测/跟踪/轨迹/倒地候选（D011）")
    ap.add_argument("--clips", default=str(REPO / "data" / "samples" / "incidents"),
                    help="片段目录（*.mp4）或单个文件")
    ap.add_argument("--video", default="", help="直接给原视频（配合 --t0/--dur 时先切）")
    ap.add_argument("--max-clips", type=int, default=0)
    ap.add_argument("--calib", default="",
                    help="field_coords.py 产出的 calib.json；给了才在 points 里写 X/Y（米）")
    ap.add_argument("--stride", type=int, default=2, help="隔几帧处理（1=每帧）")
    ap.add_argument("--imgsz", type=int, default=640, help="YOLO 输入尺寸")
    ap.add_argument("--conf", type=float, default=CONF_DEFAULT)
    ap.add_argument("--iou", type=float, default=IOU_DEFAULT)
    ap.add_argument("--model", default=str(MODEL_DEFAULT))
    ap.add_argument("--viz-every", type=int, default=200, help="每 N 帧出一张可视化")
    ap.add_argument("--color-filter", action="store_true",
                    help="用球衣颜色剔观众/裁判并按颜色补检（D012/D013-a）")
    ap.add_argument("--team-colors", default="white,red",
                    help="两队球衣色，逗号分隔（作 2-means 种子；实际以本帧统计为准）")
    ap.add_argument("--ref-color", default=None, help="裁判球衣色（可空）")
    ap.add_argument("--keeper", dest="keeper_colors", action="append", default=[],
                    help="门将球衣色（可重复）")
    args = ap.parse_args(argv)
    if isinstance(args.team_colors, str):
        args.team_colors = [c.strip() for c in args.team_colors.split(",") if c.strip()]

    model_path = Path(args.model)
    if not model_path.exists():
        print("TRACKS_FAIL model_missing %s" % model_path)
        return 2

    clips = _collect_clips(args)
    if not clips:
        print("TRACKS_FAIL no_clips")
        return 2

    from ultralytics import YOLO
    model = YOLO(str(model_path))

    print("clips: %d  imgsz=%d stride=%d conf=%.2f" %
          (len(clips), args.imgsz, args.stride, args.conf))
    results = []
    for i, c in enumerate(clips, 1):
        print("[%d/%d] %s ..." % (i, len(clips), c.name), flush=True)
        r = track_clip(model, c, args)
        results.append(r)
        if args.viz_every > 0:
            try:
                _draw_clip(model, c, args, r, every=args.viz_every)
            except Exception as exc:            # 可视化失败不影响主结果
                print("  WARN viz failed: %r" % (exc,))
        _cf = ""
        if getattr(args, "color_filter", False):
            _cf = " color_dropped=%d" % r.get("n_color_drop", 0)
        print("  tracks=%d fall_strong=%d fall_weak=%d%s  %.1fs" % (
            r["n_tracks"],
            sum(t.get("n_fall_strong", 0) for t in r["tracks"]),
            sum(t.get("n_fall_weak", 0) for t in r["tracks"]),
            _cf,
            r["wall_time_s"]), flush=True)

    s = write_outputs(results, args)
    t = s["totals"]
    print("RESULT_JSON %s" % (RESULTS / "player_tracks.json"))
    print("TRACKS_DONE %d %d %d %d" % (t["n_clips"], t["n_tracks"],
                                       t["n_fall_strong"], t["n_fall_weak"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
