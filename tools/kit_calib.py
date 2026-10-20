"""按场校准球衣色板：量出**这一场**两队球衣的真实色度，并与 PALETTE_REF 的预置值对照。

为什么需要它
------------
`classify_players.PALETTE_REF` 里的参考色是**按旧比赛素材校准过一次的**
（2026-10-20，257 个躯干样本；见该文件 `PALETTE_REF` 上方注释）。换一场球，
队服材质、照明、转播色调、曝光全都变，预置参考色就不再落在真实像素分布的中心：
`color_prior._classify_box` 的 `MAX_COLOR_DIST=0.16` 是一把固定的尺子，
参考色偏了 ⇒ 大量像素落在 0.16 之外 ⇒ 判 `unknown`。

2026-10-20 在 migu_968460165（大连英博 vs 深圳新鹏城）实测：`cross_match_eval`
的 unknown_rate 从旧场的 20.6% 涨到约 48%（判据 G4 报 +27 pt）。本工具回答一个
具体问题：**这 27 pt 里有多少是「参考色没按这場球校准」造成的？**

做法（只有三步，全部可复算）
----------------------------
1. 在比赛段内取若干帧（默认用 `cross_match_eval.scan_play_windows` 的比赛段，
   每段取 3 帧），跑 hi 层 person 检测，只保留 `h >= --min-h` 的框；
2. 每个框取躯干色 `torso_color`，按「到两队参考色的较近者」粗分两簇 ——
   ★ 这一簇**只用于量色度**，不是判定结论；判不了的一律留在簇外不参与；
3. 输出两组数：
   - **现状**：用 PALETTE_REF 原值 `classify()` 得到的 colored/unknown 比例；
   - **校准后**：把参考色换成实测中位数后，同一批像素的 colored/unknown 比例。
   两列之差 = 「参考色偏移」这一项单独贡献的取色失败率。

诚实边界
--------
- 本工具**没有真值**：它量的是「取色成功率」，不是「分队正确率」。校准后更容易
  取到色，也可能**更容易取错**（把深蓝判给黑、把浅灰判给白）。所以它只能用来
  解释 unknown_rate 的来源，不能单独当「校准方案已可用」的证据。
- 用**粗分簇的中位数**当参考色，等于用「离谁近就归谁」的初判去定参考色 ——
  初判的偏差会原样继承。真实可用的校准应该用**人工确认的几个像素样本**
  （D013 的 LCD 录入方案正是为此），本工具只是先把量级量清楚。
- 帧位置只取比赛段内的 3 帧/段，不是全场普查；`--starts` 可显式指定起点复现。

用法
----
    & D:\\python\\python.exe tools\\kit_calib.py `
        --video "D:\\yt-dlp\\downloads\\migu_968460165\\<全场 mp4>" `
        --palette white,blue --n-windows 12 --frames-per-window 3

产物
----
    data/results/kit_calib.json

判据令牌
--------
    KIT_CALIB_OK <n> <colored_before_pct> <colored_after_pct>   # 量到了且校准后更高
    KIT_CALIB_FAIL <reason>                                     # 样本不足 / 打不开 / 参数错
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import importlib.util
import json
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools"
RESULTS = REPO / "data" / "results"
OUT = RESULTS / "kit_calib.json"
MODEL_DEFAULT = REPO / "data" / "models" / "yolov8n.pt"

MIN_SAMPLES = 40          # 少于这个数的中位数不可信（会直接 FAIL）


def _load(path, name):
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cp = _load(TOOLS / "color_prior.py", "_kc_color_prior")
kl = _load(TOOLS / "classify_players.py", "_kc_classify")
cm = _load(TOOLS / "cross_match_eval.py", "_kc_cross_match")


def circular_median(h01):
    """色相的环形中位数（0..1 表示 0..360°）。避免 0/360 边界把中位数拉向 0.5。"""
    import numpy as np
    rad = np.deg2rad(np.asarray(h01, dtype=float) * 360.0)
    return float((np.degrees(np.arctan2(np.median(np.sin(rad)),
                                        np.median(np.cos(rad)))) % 360.0) / 360.0)


def sample_torso_colors(video, starts, frames_per_window, model_path, min_h, conf):
    """在给定起点附近取帧，检测人、取躯干色。返回 list[dict]。"""
    import cv2
    from ultralytics import YOLO

    model = YOLO(str(model_path))
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        return None
    samples = []
    try:
        for t0 in starts:
            for k in range(frames_per_window):
                t = t0 + k * 6.0                     # 窗内间隔 6 s，避免连续帧高度相关
                cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
                ok, frame = cap.read()
                if not ok:
                    continue
                res = model.predict(frame, classes=[0], conf=conf, iou=0.45,
                                    imgsz=640, verbose=False)[0]
                for i in range(len(res.boxes)):
                    x1, y1, x2, y2 = [float(v) for v in res.boxes.xyxy[i].tolist()]
                    if (y2 - y1) < min_h:
                        continue
                    rect = kl.torso_rect((x1, y1, x2, y2), frame.shape)
                    if rect is None:
                        continue
                    col = cp.torso_color(frame, rect)
                    if col is None:
                        continue
                    samples.append({"t": round(t, 1),
                                    "h01": float(col[0]), "s": float(col[1]),
                                    "v": float(col[2]), "purity": float(col[4]),
                                    "h_px": round(y2 - y1, 1)})
    finally:
        cap.release()
    return samples


def kit_refs(samples, palette):
    """按「到两队预置参考色的较近者」粗分两簇，返回每簇的实测参考色。"""
    refs = {name: kl.color_of_name(name) for name in palette.values()}
    groups = {name: [] for name in palette.values()}
    for s in samples:
        col = (s["h01"], s["s"], s["v"])
        d = {name: float(kl.distance_to_ref(col, refs[name])[0]) for name in refs}
        nearest = min(d, key=d.get)
        groups[nearest].append(s)

    out = {}
    for name, sub in groups.items():
        if not sub:
            out[name] = None
            continue
        hs = [s["h01"] for s in sub]
        ss = [s["s"] for s in sub]
        vs = [s["v"] for s in sub]
        if name in ("white", "black"):          # 无彩色的参考色不看色相
            out[name] = {"kind": "achromatic", "n": len(sub),
                         "s": round(float(sorted(ss)[len(ss) // 2]), 4),
                         "v": round(float(sorted(vs)[len(vs) // 2]), 4),
                         "s_p10": round(float(sorted(ss)[max(0, len(ss) // 10)]), 4),
                         "s_p90": round(float(sorted(ss)[min(len(ss) - 1, 9 * len(ss) // 10)]), 4),
                         "v_med": round(float(sorted(vs)[len(vs) // 2]), 4),
                         "h01_circular_median": round(circular_median(hs), 4)}
        else:
            out[name] = {"kind": "hue", "n": len(sub),
                         "h01": round(circular_median(hs), 4),
                         "s": round(float(sorted(ss)[len(ss) // 2]), 4),
                         "v": round(float(sorted(vs)[len(vs) // 2]), 4)}
    return out, groups


def calibrate(samples, palette, refs):
    """用实测参考色重算 colored / unknown。判据口径与 classify() 保持一致。"""
    n_col = 0
    for s in samples:
        c = (s["h01"], s["s"], s["v"])
        ds = {}
        for key in ("A", "B"):
            name = palette[key]
            ref = refs.get(name)
            if ref is None:
                ds[key] = 9.9
                continue
            if ref["kind"] == "achromatic":
                ds[key] = (0.5 * min(1.0, max(0.0, c[1] - 0.30) / 0.70)
                           + 0.5 * min(1.0, abs(c[2] - ref["v"]) / 0.25))
            else:
                dh = min(abs(c[0] - ref["h01"]) % 1.0, 1.0 - abs(c[0] - ref["h01"]) % 1.0) / 0.5
                ds[key] = 0.8 * dh + 0.2 * abs(c[1] - ref["s"])
        order = sorted(ds.items(), key=lambda kv: kv[1])
        best_k, best_d = order[0]
        if best_d < cp.MAX_COLOR_DIST and (order[1][1] - best_d) >= cp.MIN_MARGIN:
            n_col += 1
    return n_col / float(len(samples)) if samples else None


def main(argv=None):
    ap = argparse.ArgumentParser(description="按场校准球衣色板（量取色成功率的前后差）")
    ap.add_argument("--video", required=True, help="新比赛视频（整场 mp4）")
    ap.add_argument("--palette", default="red,white",
                    help="双方队服颜色名（人工录入，D013 LCD 方案）")
    ap.add_argument("--n-windows", type=int, default=12, help="比赛段内取几个窗口")
    ap.add_argument("--frames-per-window", type=int, default=3, help="每窗取几帧")
    ap.add_argument("--starts", default="",
                    help="显式给起点（逗号分隔秒），给了就跳过比赛段粗扫，便于复现")
    ap.add_argument("--min-h", type=float, default=40.0, help="框高下限 px（默认 40）")
    ap.add_argument("--conf", type=float, default=0.25, help="检测 conf（与基线一致）")
    ap.add_argument("--model", default=str(MODEL_DEFAULT))
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)

    t0 = time.time()
    video = Path(args.video)
    if not video.exists():
        print("KIT_CALIB_FAIL video_missing %s" % video)
        return 2
    palette, err = cm.parse_palette(args.palette)
    if err:
        print("KIT_CALIB_FAIL %s" % err)
        return 2

    if args.starts:
        starts = [float(x) for x in args.starts.split(",") if x.strip()]
        windows = None
        how = "explicit_starts"
    else:
        print("  比赛段粗扫（step=%.0fs grass>=%.2f）--" % (cm.PLAY_STEP_S, cm.PLAY_GRASS_MIN))
        windows = cm.scan_play_windows(video)
        if windows is None:
            print("KIT_CALIB_FAIL scan_failed %s" % video)
            return 2
        plan, err = cm.plan_clips_in_windows(windows, video, args.n_windows, 20.0)
        if err:
            print("KIT_CALIB_FAIL %s" % err)
            return 2
        starts = [p["start_s"] + 10.0 for p in plan]     # 窗口中间偏后，避开切换点
        how = "play_windows"
    print("  取样起点 %d 个（%s），每点 %d 帧" % (len(starts), how, args.frames_per_window))

    if not Path(args.model).exists():
        print("KIT_CALIB_FAIL model_missing %s" % args.model)
        return 2
    samples = sample_torso_colors(video, starts, args.frames_per_window, args.model,
                                  args.min_h, args.conf)
    if samples is None:
        print("KIT_CALIB_FAIL video_unreadable %s" % video)
        return 2
    n = len(samples)
    print("  躯干样本 n=%d（h>=%.0f px）" % (n, args.min_h))
    if n < MIN_SAMPLES:
        print("KIT_CALIB_FAIL not_enough_samples %d<%d" % (n, MIN_SAMPLES))
        return 2

    refs, groups = kit_refs(samples, palette)
    before = 0
    for s in samples:
        role, _d, _sc = cp.classify((s["h01"], s["s"], s["v"]), palette)
        if role in ("A", "B"):
            before += 1
    before = before / float(n)
    after = calibrate(samples, palette, refs)

    payload = {
        "tool": "tools/kit_calib.py",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "decision": "D013-a：球衣色板是**按场**校准的量，不是全仓库常数",
        "video": str(video),
        "palette_input": {"palette": palette, "given_by": "human",
                          "note": "队服颜色名由人输入（D013 LCD 方案）"},
        "params": {"n_windows": len(starts), "frames_per_window": args.frames_per_window,
                   "min_h_px": args.min_h, "conf": args.conf,
                   "model": Path(args.model).name, "sampling": how,
                   "starts": [round(x, 1) for x in starts]},
        "windows": (None if windows is None else {
            "n_intervals": len(windows["intervals"]),
            "total_play_s": windows["total_play_s"],
            "n_samples": windows["n_samples"], "n_play_samples": windows["n_play"]}),
        "n_torso_samples": n,
        "preset_refs": {name: ({"kind": "achromatic",
                                "s": round(kl.color_of_name(name)[1], 4),
                                "v": round(kl.color_of_name(name)[2], 4)}
                               if kl.color_of_name(name)[0] is None else
                               {"kind": "hue",
                                "h01": round(kl.color_of_name(name)[0], 4),
                                "s": round(kl.color_of_name(name)[1], 4),
                                "v": round(kl.color_of_name(name)[2], 4)})
                        for name in palette.values()},
        "measured_refs": refs,
        "colored_pct_preset": round(100.0 * before, 2),
        "colored_pct_calibrated": (round(100.0 * after, 2) if after is not None else None),
        "delta_pt": (round(100.0 * (after - before), 2) if after is not None else None),
        "thresholds": {"MAX_COLOR_DIST": cp.MAX_COLOR_DIST, "MIN_MARGIN": cp.MIN_MARGIN,
                       "min_samples": MIN_SAMPLES},
        "caveats": [
            "无真值：量的是取色成功率，不是分队正确率；校准后更易取到色也可能更易取错",
            "簇的中位数来自「离谁近就归谁」的初判，初判偏差会被继承",
            "真实可用校准应用人工确认的像素样本（D013 LCD 方案），本工具只先把量级量清楚",
            "取帧在比赛段内抽样，不是全场普查",
        ],
        "generated_in_s": round(time.time() - t0, 1),
        "output_path": str(Path(args.out)),
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                              encoding="utf-8", newline="\n")
    print("  预置参考色 colored=%.1f%%（unknown %.1f%%）"
          % (100 * before, 100 * (1 - before)))
    print("  校准参考色 colored=%.1f%%（unknown %.1f%%）  Δ=%+.1f pt"
          % (100 * after, 100 * (1 - after), 100 * (after - before)))
    print("RESULT_JSON %s" % args.out)
    print("KIT_CALIB_OK %d %.1f %.1f" % (n, 100 * before, 100 * after))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:                        # noqa: BLE001 - 兜底也要留令牌
        print("KIT_CALIB_FAIL uncaught %s: %s" % (type(exc).__name__, exc))
        raise SystemExit(2)