"""player_color_filter.py —— 用球衣颜色把「球员」从 person 里筛出来（D012 / D013-a 的落地）。

为什么需要它
------------
`person` 这一类里混着球员、裁判、教练、观众。实测（2026-10-27，inc_12）：
同一帧 imgsz 640→1280、conf 0.25→0.12，人框数 18→23，但观众席与工作人员也被框进来。
调阈值换不来干净结果：调高漏球员，调低框观众。缺的是「这身衣服属于哪一队」这一层判断。

本模块只做三件事，都是可复算的几何/颜色运算，不训练、不猜：
    1) 取**躯干**区域（框上 25%~65%、中间 50%），避开短裤/袜子/草地
    2) 按调色板（classify_players.PALETTE_REF）判最接近的球衣色
    3) 颜色属于任一方 → 保留（低置信也保，这就是「补检」）；
       属于裁判色 / 都不像 / 判不出来 → 丢弃并记原因

用法
----
    # 单帧/多帧评估（不需要板子）
    py tools/player_color_filter.py --video data/samples/incidents/inc_12_t06867.9.mp4 \
        --frames 60,200,340 --team-a white --team-b red --ref yellow \
        --viz-dir D:/NousPortal/hermes/cache/scratch/colorfilt

    # 自检
    py tools/player_color_filter.py --selftest

判据令牌：COLOR_FILTER_OK kept=<n> dropped=<n> / COLOR_FILTER_FAIL <原因>
"""
import argparse
import json
import os
import sys
from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
from classify_players import PALETTE_REF  # noqa: E402
from ball_cv_probe import grass_mask  # noqa: E402

LOW_CONF = 0.12          # 补检下限：比主阈值低，靠颜色决定去留
MIN_H = 16               # 框高下限（素材里人多在 20~117 px；再小颜色不可信）
TORSO_TOP, TORSO_BOT = 0.25, 0.65
TORSO_HALF_W = 0.25      # 左右各取框宽的 25%


def torso_crop(bgr, box):
    h, w = bgr.shape[:2]
    x1, y1, x2, y2 = [int(v) for v in box]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    bw, bh = x2 - x1, y2 - y1
    if bw < 4 or bh < 8:
        return None
    ty1 = y1 + int(bh * TORSO_TOP)
    ty2 = y1 + int(bh * TORSO_BOT)
    tx1 = x1 + int(bw * TORSO_HALF_W)
    tx2 = x2 - int(bw * TORSO_HALF_W)
    if tx2 - tx1 < 3 or ty2 - ty1 < 4:
        return None
    return bgr[ty1:ty2, tx1:tx2]


def torso_hist(crop):
    """躯干像素 → HSV 三分量的稳健统计（去掉过暗像素，避免阴影拉偏）。"""
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0].astype(np.float32), hsv[..., 1].astype(np.float32) / 255.0, \
        hsv[..., 2].astype(np.float32) / 255.0
    m = v > 0.15
    if m.sum() < 8:
        return None
    return float(np.median(h[m])), float(np.median(s[m])), float(np.median(v[m]))


def _hue_dist(a, b):
    d = abs(a - b)
    return min(d, 180.0 - d)


def palette_score(h, s, v, name):
    """一个躯干统计量对某个调色板颜色的贴合度（0~1，越大越像）。"""
    ph, ps, pv = PALETTE_REF[name]
    if name == "white":                      # 低饱和高亮
        sc = (1.0 if s < 0.22 else max(0.0, 1.0 - (s - 0.22) / 0.25)) * \
             (1.0 if v > 0.55 else max(0.0, (v - 0.30) / 0.25))
        return float(max(0.0, min(1.0, sc)))
    if name == "black":                      # 深色球衣
        # 阈值放宽到 0.45：门将深色球衣在转播里实测中位亮度 0.38（inc_12 f300），
        # 卡 0.32 会把门将判不出来
        return float(max(0.0, min(1.0, (0.45 - v) / 0.22)))
    if s < 0.18 or v < 0.12:                 # 无彩/太暗：不可能是有彩色
        return 0.0
    # 色相是主判据；饱和度/亮度容忍度放宽 —— 球衣在阴影、逆光、脏污下 S/V 会大幅漂移，
    # 卡太紧会把整队判成 unknown（首版 ds/0.40 就把纯红判成 0.37 分丢掉）
    dh = _hue_dist(h, ph) / 35.0
    ds = abs(s - ps) / 0.60
    dv = abs(v - pv) / 0.70
    sc = 1.0 - max(dh, ds, dv)
    return float(max(0.0, min(1.0, sc)))


def label_torso(st, allow, thr=0.45):
    """返回 (颜色名 or None, 分数)。allow 是候选颜色名列表。"""
    best, best_sc = None, 0.0
    for name in allow:
        sc = palette_score(st[0], st[1], st[2], name)
        if sc > best_sc:
            best, best_sc = name, sc
    return (best, best_sc) if best_sc >= thr else (None, best_sc)


def on_pitch(bgr, box, grass=None, pad=6):
    """脚底那一点是不是落在草皮上 —— 剔除看台观众/场边工作人员的强判据。

    色彩只能回答「衣服像不像某队」，回答不了「人在不在场上」：穿白 T 的观众和白队球员
    颜色一样。转播机位里看台在草皮上方，脚底落点不在草皮上的一律不是场上人员。
    """
    if grass is None:
        grass = grass_mask(bgr)
    x1, y1, x2, y2 = [int(v) for v in box]
    h, w = grass.shape[:2]
    cx = max(0, min(w - 1, (x1 + x2) // 2))
    cy = max(0, min(h - 1, y2 - 2))
    y0, y1c = max(0, cy - pad), min(h, cy + pad + 1)
    x0, x1c = max(0, cx - pad), min(w, cx + pad + 1)
    roi = grass[y0:y1c, x0:x1c]
    return bool(roi.size and roi.mean() > 60)


def _norm_dist(st, center):
    """HSV 归一化距离：色相按 35° 折算，S/V 各按 0.6/0.7 折算，取最大分量。

    ★ 无彩色（白/黑）不比色相：白球衣的 H 是噪声（实测 48~90 都有），
      若把「白色中心」的 H 取 0 再比色相，白队会被判成离中心很远而整队丢掉（实测 f60）。
    """
    ds = abs(st[1] - center[1]) / 0.60
    dv = abs(st[2] - center[2]) / 0.70
    if st[1] < 0.22 or center[1] < 0.22:
        return max(ds, dv)
    dh = _hue_dist(st[0], center[0]) / 35.0
    return max(dh, ds, dv)


def learn_centers(stats_list, seeds=None, iters=6):
    """从本帧全部躯干统计量里用 2-means 估两队颜色中心。

    为什么不在固定调色板上做比对：转播画面有暖光/阴天/球衣脏污，红队在暖光下色相能漂到
    30~37°，和黄色裁判只差十几度 —— 固定调色板 + argmax 会把半个红队判成裁判（实测 f60）。
    自适应中心只回答「这身衣服更像场上的哪一队」，裁判/观众自然会离两个中心都远。
    """
    pts = [s for s in stats_list if s is not None]
    if len(pts) < 6:
        return None
    # 初始中心：用种子色的调色板值，若没有就取 S 最小/最大的两个
    if seeds and len(seeds) >= 2:
        c0 = np.array(PALETTE_REF[seeds[0]], dtype=np.float32)
        c1 = np.array(PALETTE_REF[seeds[1]], dtype=np.float32)
        c0[0] = c0[0] if c0[0] >= 0 else 0.0
        c1[0] = c1[0] if c1[0] >= 0 else 0.0
    else:
        order = sorted(pts, key=lambda p: p[1])
        c0 = np.array(order[0], dtype=np.float32)
        c1 = np.array(order[-1], dtype=np.float32)
    arr = np.array(pts, dtype=np.float32)
    for _ in range(iters):
        d0 = np.array([_norm_dist(p, c0) for p in arr])
        d1 = np.array([_norm_dist(p, c1) for p in arr])
        g0, g1 = arr[d0 <= d1], arr[d1 < d0]
        if len(g0) == 0 or len(g1) == 0:
            break
        c0 = np.median(g0, axis=0)
        c1 = np.median(g1, axis=0)
    return (c0, c1)


def filter_players(bgr, dets, team_colors, ref_color=None,
                   low_conf=LOW_CONF, min_h=MIN_H, thr=0.45, adaptive=False, max_dist=0.75,
                   keeper_colors=None):
    """按球衣颜色筛人。

    dets: [{'box':[x1,y1,x2,y2], 'conf': float, 'id': 可选, ...}]
    team_colors: 两队球衣色名列表，如 ['white', 'red']
    返回 (kept, dropped, stats)
    """
    kept, dropped = [], []
    stats = {"n_in": len(dets), "kept": 0, "kept_low_conf": 0,
             "drop_ref": 0, "drop_unknown": 0, "drop_small": 0, "adaptive": bool(adaptive)}
    allow = list(team_colors) + ([ref_color] if ref_color else [])

    # 自适应模式：先给所有「够高置信」的框算躯干统计量，用它估两队颜色中心
    grass = grass_mask(bgr)
    keeper_colors = [c for c in (keeper_colors or []) if c]
    centers = None
    if adaptive:
        hist = []
        for d in dets:
            box = d.get("box")
            if not box or (box[3] - box[1]) < min_h:
                continue
            crop = torso_crop(bgr, box)
            st = torso_hist(crop) if crop is not None else None
            if st is not None and float(d.get("conf", 0)) >= 0.35:
                hist.append(st)
        centers = learn_centers(hist, team_colors)
    for d in dets:
        box = d.get("box")
        conf = float(d.get("conf", 0.0) or 0.0)
        if not box:
            dropped.append(dict(d, reason="no_box"))
            continue
        if conf < low_conf:
            dropped.append(dict(d, reason="conf<%.2f" % low_conf))
            continue
        if not on_pitch(bgr, box, grass):
            dropped.append(dict(d, reason="off_pitch"))
            stats["drop_off_pitch"] = stats.get("drop_off_pitch", 0) + 1
            continue
        crop = torso_crop(bgr, box)
        st = torso_hist(crop) if crop is not None else None
        if st is None or (box[3] - box[1]) < min_h:
            stats["drop_small"] += 1
            dropped.append(dict(d, reason="too_small"))
            continue
        # 门将优先：门将球衣与两队都不同，不单独放行就会被「离两队都远」剔掉
        if keeper_colors:
            # 门将用更松的门槛（0.30）：门将服常是深色/荧光色，与调色板参考值差得远，
            # 用 0.45 会连门将自己也判不出来（实测 inc_12 f300 的深色门将 sc=0.32）
            gk_name, gk_sc = label_torso(st, keeper_colors, thr=0.30)
            if gk_sc >= 0.30:
                item = dict(d, color="GK:%s" % gk_name, color_score=round(gk_sc, 3))
                kept.append(item)
                stats["kept"] += 1
                stats["kept_keeper"] = stats.get("kept_keeper", 0) + 1
                continue

        if centers is not None:
            # 自适应：离两个中心最近的那个；最近距离超过 max_dist 就丢（裁判/观众/杂物）
            d0 = _norm_dist(st, centers[0])
            d1 = _norm_dist(st, centers[1])
            if min(d0, d1) > max_dist:
                stats["drop_unknown"] += 1
                dropped.append(dict(d, reason="far_from_teams(d=%.2f)" % min(d0, d1)))
                continue
            item = dict(d, color="A" if d0 <= d1 else "B",
                        color_score=round(1.0 - min(d0, d1), 3))
            kept.append(item)
            stats["kept"] += 1
            if conf < 0.25:
                stats["kept_low_conf"] += 1
            continue

        # ★ 判队优先、判裁判要「明显更近」才算 —— 红队球衣在暖光下色相能漂到 30~37，
        #   与黄色裁判只差十几度；首版用 argmax 直接判，把半个红队当裁判剔了（实测 f60）。
        team_name, team_sc = label_torso(st, list(team_colors), thr=thr)
        ref_sc = palette_score(st[0], st[1], st[2], ref_color) if ref_color else 0.0
        if team_sc >= thr and not (ref_sc > team_sc + 0.15):
            name, sc = team_name, team_sc
        elif ref_color and ref_sc >= thr:
            stats["drop_ref"] += 1
            dropped.append(dict(d, reason="referee(sc=%.2f)" % ref_sc, color=ref_color))
            continue
        else:
            stats["drop_unknown"] += 1
            dropped.append(dict(d, reason="no_color(team=%.2f ref=%.2f)" % (team_sc, ref_sc),
                                color=None))
            continue
        item = dict(d, color=name, color_score=round(sc, 3))
        kept.append(item)
        stats["kept"] += 1
        if conf < 0.25:
            stats["kept_low_conf"] += 1
    return kept, dropped, stats


# ---------------------------------------------------------------- 评估入口
def run_video(video, frames_idx, team_colors, ref_color, imgsz, viz_dir, adaptive=False,
              keeper_colors=None):
    from ultralytics import YOLO
    model = YOLO(str(REPO / "weights" / "yolo26n.pt"))
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        print("COLOR_FILTER_FAIL open %s" % video)
        return 2
    if viz_dir:
        os.makedirs(viz_dir, exist_ok=True)
    out = []
    for idx in frames_idx:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, f = cap.read()
        if not ok:
            continue
        r = model.predict(f, imgsz=imgsz, conf=LOW_CONF, verbose=False)[0]
        dets = []
        for b in r.boxes:
            if int(b.cls) != 0:
                continue
            x1, y1, x2, y2 = [float(v) for v in b.xyxy[0]]
            dets.append({"box": [x1, y1, x2, y2], "conf": float(b.conf)})
        kept, dropped, stats = filter_players(f, dets, team_colors, ref_color,
                                              adaptive=adaptive,
                                              keeper_colors=keeper_colors)
        out.append({"frame": idx, "stats": stats})
        print("f%-4d in=%2d kept=%2d (low=%d gk=%d) drop_ref=%d unknown=%d small=%d"
              % (idx, stats["n_in"], stats["kept"], stats["kept_low_conf"],
                 stats.get("kept_keeper", 0), stats["drop_ref"],
                 stats["drop_unknown"], stats["drop_small"]))
        if viz_dir:
            vis = f.copy()
            for d in kept:
                x1, y1, x2, y2 = [int(v) for v in d["box"]]
                cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 255, 0), 2)
                cv2.putText(vis, "%s %.2f" % (d["color"], d["conf"]), (x1, max(10, y1 - 3)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 0), 1)
            for d in dropped:
                if d.get("reason") in ("referee", "no_color") and d.get("box"):
                    x1, y1, x2, y2 = [int(v) for v in d["box"]]
                    cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 0, 255), 1)
            cv2.putText(vis, "kept=%d dropped=%d" % (stats["kept"], stats["n_in"] - stats["kept"]),
                        (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
            cv2.imwrite(os.path.join(viz_dir, "f%04d.jpg" % idx), vis)
    cap.release()
    print("COLOR_FILTER_OK kept=%d dropped=%d"
          % (sum(o["stats"]["kept"] for o in out),
             sum(o["stats"]["n_in"] - o["stats"]["kept"] for o in out)))
    return 0


def _selftest():
    ok = True
    # 合成三块躯干：白、红、黄（裁判），各 40x80
    def patch(bgr_val):
        im = np.zeros((80, 40, 3), np.uint8)
        im[:, :] = bgr_val
        return im
    cases = [("white", (245, 245, 245), ["white", "red"], None),
             ("red", (40, 40, 200), ["white", "red"], None),
             ("ref", (40, 220, 230), ["white", "red"], "yellow")]
    for want, bgrv, teams, ref in cases:
        crop = patch(bgrv)
        st = torso_hist(crop)
        allow = teams + ([ref] if ref else [])
        name, sc = label_torso(st, allow, thr=0.45)
        got = "ref" if (ref and name == ref) else name
        print("selftest %-5s -> got=%-5s sc=%.2f" % (want, got, sc))
        if got != want:
            ok = False
    # ---- 端到端：合成一张「草皮 + 白队 + 红队 + 深色门将 + 看台观众」的图 ----
    frame = np.zeros((240, 320, 3), np.uint8)
    frame[:, :] = (60, 140, 60)                     # BGR 绿：会被 grass_mask 认成草皮
    cv2.rectangle(frame, (0, 0), (320, 40), (180, 120, 60), -1)   # 顶部看台（非草皮）
    def person(x, y, h, bgr):
        cv2.rectangle(frame, (x, y), (x + int(h * 0.45), y + h), bgr, -1)
        return [x, y, x + int(h * 0.45), y + h]
    boxes = [
        ("white", person(20, 120, 70, (245, 245, 245))),
        ("red", person(90, 120, 70, (40, 40, 200))),
        ("gk", person(160, 110, 80, (40, 40, 40))),
        ("stand", person(20, 5, 30, (245, 245, 245))),      # 看台观众：脚不在草皮上
    ]
    dets = [{"box": b, "conf": 0.6} for _, b in boxes]
    kept, dropped, st2 = filter_players(frame, dets, ["white", "red"], None,
                                        adaptive=True, keeper_colors=["black"])
    labels = sorted(k.get("color", "") for k in kept)
    print("e2e kept=%d labels=%s dropped=%s" % (len(kept), labels,
                                                [d.get("reason") for d in dropped]))
    if len(kept) != 3 or not any(l.startswith("GK") for l in labels):
        print("COLOR_FILTER_SELFTEST_FAIL e2e kept=%d labels=%s" % (len(kept), labels))
        ok = False
    if not any(d.get("reason") == "off_pitch" for d in dropped):
        print("COLOR_FILTER_SELFTEST_FAIL 看台观众没被位置判据剔掉")
        ok = False

    print("COLOR_FILTER_SELFTEST", "OK" if ok else "FAIL")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video")
    ap.add_argument("--frames", default="60,200,340")
    ap.add_argument("--team-a", default="white")
    ap.add_argument("--team-b", default="red")
    ap.add_argument("--ref", default="yellow")
    ap.add_argument("--imgsz", type=int, default=1280)
    ap.add_argument("--keeper", action="append", default=[],
                    help="门将球衣色（可重复，如 --keeper black --keeper green）")
    ap.add_argument("--adaptive", action="store_true",
                    help="用本帧高置信检测自适应估计两队颜色中心（推荐）")
    ap.add_argument("--viz-dir", default="")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    if not args.video:
        print("COLOR_FILTER_FAIL need --video")
        return 2
    idxs = [int(x) for x in args.frames.split(",") if x.strip()]
    return run_video(args.video, idxs, [args.team_a, args.team_b],
                     args.ref or None, args.imgsz, args.viz_dir, args.adaptive,
                     args.keeper)


if __name__ == "__main__":
    sys.exit(main())