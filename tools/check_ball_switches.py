#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_ball_switches.py —— 球链路两个新开关的离线验证（不需要板子）

验证对象（2026-10-26 新增；默认行为必须与稳定版一致）：

  A. ``--ball-lost-sec``
     丢失窗口按秒计。``None`` -> ``BallTracker.max_miss=4``（稳定版原值）。
     ``step()`` 在管线里每视频帧调用一次，所以换算单位是视频帧，不是球帧；
     本脚本用合成轨迹验证「同窗口等价」「跨缺口重捕获」两件事。

  B. ``--ball-predict-past-boundary``
     预测点（``track.miss > 0``）允许越过球场范围 ±5 m。默认关。
     本脚本用真实标定 ``data/calib/field_calib.json`` 的逆单应生成图像轨迹：
     观测段在球场内，随后无候选，预测位置越过远端边线。

判据 / 令牌：
  末行 ``BALL_SWITCHES_OK`` 表示全部硬判据通过；否则
  ``BALL_SWITCHES_FAIL <原因>``。硬判据见下方 ``main()`` 中的注释。

只读输入：``data/calib/field_calib.json``、``data/results/ball_cv.json``
（后者仅取 clip.fps 作为离线换算参数；板上以实际 ``cap.get`` 为准）。
不读写板上文件，不依赖 pynq / ncnn / C 分类器。
"""
import json
import os
import sys

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import numpy as np  # noqa: E402

from ball_policy import (  # noqa: E402
    DEFAULT_MAX_MISS, in_field_box, place_ball, resolve_max_miss,
)
from ball_track import BallTracker  # noqa: E402
from field_coords import box_to_field  # noqa: E402
from result_publisher import build_frame  # noqa: E402

CALIB = os.path.join(ROOT, "data", "calib", "field_calib.json")
BALL_CV = os.path.join(ROOT, "data", "results", "ball_cv.json")

GAP_START = 20
GAP_LEN = 8
N_FRAMES = 50
BALL_VX = 5.0
BALL_X0, BALL_Y0 = 100.0, 200.0


def _fail(msg):
    print("BALL_SWITCHES_FAIL", msg)
    return 1


def _load_test_fps():
    """离线换算参数。优先用已有结果 JSON 里记录的真实 clip.fps。"""
    try:
        d = json.load(open(BALL_CV, encoding="utf-8"))
        fps = float(d["clip"]["fps"])
        print("FPS_SOURCE %s clip.fps=%.3f" % (os.path.relpath(BALL_CV, ROOT), fps))
        return fps
    except Exception as e:                       # 文件缺失/结构变化时不静默编造
        print("FPS_SOURCE fallback=25.000 (%s)" % e)
        return 25.0


def _legacy_place(f_xy, img_xy, confidence):
    """稳定版 board_pipeline.py 的球放置分支，逐字段复刻，用作回归基准。"""
    placed = False
    ball = None
    if f_xy is not None:
        if -5 <= f_xy[0] <= 110 and -5 <= f_xy[1] <= 73:
            ball = {"x": round(f_xy[0], 2), "y": round(f_xy[1], 2),
                    "confidence": confidence, "valid": True, "space": "field"}
            placed = True
    if not placed:
        ball = {"x": round(img_xy[0], 1), "y": round(img_xy[1], 1),
                "confidence": confidence, "valid": True, "space": "image"}
    return ball


def simulate_gap(max_miss):
    """合成匀速直线 + 8 帧无候选缺口；返回逐帧轨迹与内部存活 ID。

    每帧调用一次 step(dt=1)，与 board_pipeline 的实际调用方式一致。
    """
    tr = BallTracker(min_hits=2, max_miss=max_miss, min_speed=3.0)
    out, alive = [], []
    for i in range(N_FRAMES):
        cands = ([] if GAP_START <= i < GAP_START + GAP_LEN
                 else [{"x": BALL_X0 + BALL_VX * i, "y": BALL_Y0}])
        bt = tr.step(cands, dt=1.0)
        out.append([(t.tid, t.hits, t.miss, round(t.x, 3), round(t.y, 3))
                    for t in bt])
        alive.append(sorted(t.tid for t in tr.tracks))
    return out, alive


def _summarize_gap(trace, alive, max_miss, fps):
    tid0 = trace[GAP_START - 1][0][0] if trace[GAP_START - 1] else None
    gap_end = GAP_START + GAP_LEN
    in_gap_outputs = sum(1 for i in range(GAP_START, gap_end) if trace[i])
    first_same = next((i for i in range(gap_end, N_FRAMES)
                       if any(t[0] == tid0 for t in trace[i])), None)
    first_any = next((i for i in range(gap_end, N_FRAMES) if trace[i]), None)
    same_out = next((t for i in range(gap_end, N_FRAMES) for t in trace[i]
                     if t[0] == tid0), None)
    return {
        "max_miss": max_miss,
        "lost_sec": round(max_miss / fps, 3),
        "survived_gap": tid0 in alive[gap_end - 1],
        "gap_output_frames": in_gap_outputs,
        "first_any_frame": first_any,
        "first_same_id_frame": first_same,
        "same_id_reacquired": same_out is not None,
        "reacquire_conf": (round(min(1.0, same_out[1] / 5.0), 2)
                           if same_out else None),
    }


def test_lost_sec(fps):
    """A 组：默认值、同窗口等价、跨缺口重捕获。返回 (ok, 说明)。"""
    print("\n== A. --ball-lost-sec ==")

    # A1 默认路径必须原样返回 4，不因 fps 变化。
    for f in (fps, 12.5, 30.0):
        got = resolve_max_miss(None, f)
        print("A1 default fps=%.2f -> max_miss=%d" % (f, got))
        if got != DEFAULT_MAX_MISS or got != 4:
            return False, "A1 default_max_miss_changed"

    # A2 同窗口等价：lost_sec = 4/fps 必须得到 max_miss=4，轨迹逐帧一致。
    eq_sec = 4.0 / fps
    eq_miss = resolve_max_miss(eq_sec, fps)
    tr_a, al_a = simulate_gap(4)
    tr_b, al_b = simulate_gap(eq_miss)
    identical = (tr_a == tr_b and al_a == al_b)
    print("A2 equivalent lost_sec=%.3f fps=%.2f -> max_miss=%d identical=%s"
          % (eq_sec, fps, eq_miss, int(identical)))
    if eq_miss != 4 or not identical:
        return False, "A2_equivalent_window_mismatch"

    # A3 跨缺口：默认 4 步在 8 帧缺口后丢 ID；0.5 s 窗口 > 缺口，应保住同一 ID。
    lost_sec = 0.5
    lost_miss = resolve_max_miss(lost_sec, fps)
    tr_d, al_d = simulate_gap(4)
    tr_l, al_l = simulate_gap(lost_miss)
    d = _summarize_gap(tr_d, al_d, 4, fps)
    l = _summarize_gap(tr_l, al_l, lost_miss, fps)
    for tag, s in (("default", d), ("lost_sec", l)):
        print("A3 %-8s max_miss=%-3d survived=%-5s same_id=%-5s "
              "first_same=%s reacq_conf=%s gap_outputs=%d"
              % (tag, s["max_miss"], s["survived_gap"], s["same_id_reacquired"],
                 s["first_same_id_frame"], s["reacquire_conf"],
                 s["gap_output_frames"]))
    if d["survived_gap"] or d["same_id_reacquired"]:
        return False, "A3_default_unexpectedly_bridged_gap"
    if not l["survived_gap"] or not l["same_id_reacquired"]:
        return False, "A3_lost_sec_did_not_bridge_gap"
    if l["first_same_id_frame"] is None or d["first_any_frame"] is None:
        return False, "A3_missing_reacquire_frame"
    if l["first_same_id_frame"] >= d["first_any_frame"]:
        return False, "A3_lost_sec_not_faster"

    # A4 如实报告：缺口内的预测输出受 min_speed/0.6 衰减限制，不能把
    # 「窗口变长」误读成「缺口内帧数一定变多」。
    print("A4 note: gap_outputs default=%d lost_sec=%d "
          "(受 min_speed=3.0 与 vx*=0.6 衰减约束，二者可相同)"
          % (d["gap_output_frames"], l["gap_output_frames"]))
    return True, "A_ok"


def simulate_pipeline_gap(max_miss):
    """粗略复刻 board_pipeline 的粘滞 ball 输出（无标定时 space=image）。

    目的：说明当前代码里 ``if bt: ball=...`` 没有 else，球一旦出现就不会因
    bt 为空而清零；因此 lost-sec 改的是「重捕获/更新」，不是粘滞覆盖率。
    """
    tr = BallTracker(min_hits=2, max_miss=max_miss, min_speed=3.0)
    ball, rows = None, []
    for i in range(N_FRAMES):
        cands = ([] if GAP_START <= i < GAP_START + GAP_LEN
                 else [{"x": BALL_X0 + BALL_VX * i, "y": BALL_Y0}])
        bt = tr.step(cands, dt=1.0)
        updated = False
        if bt:
            b = max(bt, key=lambda t: t.hits)
            conf = round(min(1.0, b.hits / 5.0), 2)
            ball = place_ball(None, (b.x, b.y), conf, is_predicted=(b.miss > 0),
                              allow_predict_past_boundary=False)
            updated = True
        rows.append({"has_ball": ball is not None, "updated": updated,
                     "space": None if ball is None else ball["space"]})
    return rows


def test_pipeline_sticky(fps):
    """诊断项：lost-sec 不改变粘滞覆盖率，但应缩短「位置更新缺口」。"""
    print("\n== A5. pipeline sticky-ball diagnostic ==")
    d = simulate_pipeline_gap(4)
    l = simulate_pipeline_gap(resolve_max_miss(0.5, fps))
    for tag, rows in (("default", d), ("lost_sec", l)):
        with_ball = sum(1 for r in rows if r["has_ball"])
        updates = sum(1 for r in rows if r["updated"])
        last = None
        max_gap = 0
        for i, r in enumerate(rows):
            if r["updated"]:
                if last is not None:
                    max_gap = max(max_gap, i - last)
                last = i
        print("A5 %-8s frames_with_ball=%d update_frames=%d max_update_gap=%d"
              % (tag, with_ball, updates, max_gap))
    with_d = sum(1 for r in d if r["has_ball"])
    with_l = sum(1 for r in l if r["has_ball"])
    upd_d = sum(1 for r in d if r["updated"])
    upd_l = sum(1 for r in l if r["updated"])
    if with_d != with_l:
        return False, "A5_sticky_coverage_changed"
    if upd_l < upd_d:
        return False, "A5_lost_sec_reduced_updates"
    print("A5 note: 当前代码 bt 为空时不清空 ball（board_pipeline.py 的 if bt 无 else），"
          "所以总球帧不变，变化只在重捕获/更新。")
    return True, "A5_ok"


def test_boundary_policy():
    """B1/B2：默认逐字段等价 + 开关作用域。"""
    print("\n== B1/B2. --ball-predict-past-boundary policy ==")

    # B1 默认关：与旧实现逐字段一致（含边界值、None、越界观测/预测）。
    f_vals = [None, (0.0, 0.0), (-5.0, 0.0), (110.0, 73.0), (-5.001, 34.0),
              (110.001, 34.0), (52.5, 73.001), (50.0, 34.0), (200.0, -20.0),
              (float("nan"), 34.0)]
    bad = 0
    for f in f_vals:
        for pred in (False, True):
            got = place_ball(f, (640.0, 360.0), 0.8, pred, False)
            exp = _legacy_place(f, (640.0, 360.0), 0.8)
            if got != exp:
                bad += 1
                print("B1 MISMATCH f=%r pred=%s got=%r exp=%r"
                      % (f, pred, got, exp))
    print("B1 legacy_equivalence cases=%d mismatches=%d"
          % (len(f_vals) * 2, bad))
    if bad:
        return False, "B1_default_not_identical"

    # B2 开关作用域：只有「预测 + 越界 + 显式允许」才留在球场坐标系。
    cases = [
        ((50.0, 34.0), False, False, "field", False),
        ((50.0, 34.0), True, False, "field", False),
        ((50.0, 34.0), True, True, "field", False),
        ((120.0, 34.0), False, False, "image", False),
        ((120.0, 34.0), False, True, "image", False),   # 观测点：开关不生效
        ((120.0, 34.0), True, False, "image", False),   # 预测点：默认关
        ((120.0, 34.0), True, True, "field", True),     # 预测点：开
        (None, True, True, "image", False),
    ]
    for f, pred, flag, exp_space, exp_mark in cases:
        got = place_ball(f, (640.0, 360.0), 0.8, pred, flag)
        mark = bool(got.get("predicted"))
        print("B2 f=%-12r pred=%-5s flag=%-5s -> space=%-5s predicted=%s"
              % (f, pred, flag, got["space"], mark))
        if got["space"] != exp_space or mark != exp_mark:
            return False, "B2_scope_wrong"
    return True, "B_ok"


def _field_to_image(H_inv, x, y):
    p = H_inv @ np.array([x, y, 1.0])
    return float(p[0] / p[2]), float(p[1] / p[2])


def simulate_boundary(H_list, H_inv, allow_past, legacy=False):
    """真实标定下的图像轨迹：3 帧观测（场内） + 7 帧无候选。

    观测点选球场 y=40/50/60 m，速度指向远端；预测点在第 4 帧越过
    y=73 m（约 74.5 m，越界约 1.5 m），避免用极端外推证明开关。
    ``legacy=True`` 时走稳定版的放置分支，用于整条轨迹的回归对拍。
    """
    pts = [_field_to_image(H_inv, 52.5, y) for y in (40.0, 50.0, 60.0)]
    tr = BallTracker(min_hits=2, max_miss=4, min_speed=3.0)
    ball = None
    rows = []
    for i in range(10):
        cands = [{"x": pts[i][0], "y": pts[i][1]}] if i < 3 else []
        bt = tr.step(cands, dt=1.0)
        b = None
        if bt:
            b = max(bt, key=lambda t: t.hits)
            f_xy = box_to_field(H_list, [b.x - 1, b.y - 1, b.x + 1, b.y + 1])
            conf = round(min(1.0, b.hits / 5.0), 2)
            if legacy:
                ball = _legacy_place(f_xy, (b.x, b.y), conf)
            else:
                ball = place_ball(f_xy, (b.x, b.y), conf,
                                  is_predicted=(b.miss > 0),
                                  allow_predict_past_boundary=allow_past)
        frame = build_frame(i, [], ball=ball)
        ev = frame.get("events") or {}
        rows.append({
            "frame": i,
            "bt": bool(bt),
            "miss": None if b is None else b.miss,
            "space": None if ball is None else ball["space"],
            "predicted": bool(ball and ball.get("predicted")),
            "out_of_play": bool(ev.get("out_of_play") or ev.get("goal")),
            "xy": None if ball is None else (ball["x"], ball["y"]),
        })
    return rows


def test_boundary_trajectory():
    """B3/B4：真实标定轨迹上的开/关对比与代价报表。"""
    print("\n== B3/B4. real-calib trajectory ==")
    try:
        cal = json.load(open(CALIB, encoding="utf-8"))
        H_list = cal["H"]
        H_inv = np.linalg.inv(np.array(H_list, dtype=float))
    except Exception as e:
        return False, "B3_calib_load_failed(%s)" % e

    rows = {
        False: simulate_boundary(H_list, H_inv, allow_past=False),
        True: simulate_boundary(H_list, H_inv, allow_past=True),
        "legacy": simulate_boundary(H_list, H_inv, allow_past=False, legacy=True),
    }

    # B3a 默认关：整条轨迹（space/坐标/predicted/events）必须与旧实现一致。
    def _sig(r):
        return (r["space"], r["predicted"], r["out_of_play"], r["xy"])

    mismatch = [(i, _sig(a), _sig(b)) for i, (a, b)
                in enumerate(zip(rows[False], rows["legacy"])) if _sig(a) != _sig(b)]
    print("B3a legacy_trajectory frames=%d mismatches=%d"
          % (len(rows[False]), len(mismatch)))
    for m in mismatch[:3]:
        print("   B3a MISMATCH", m)
    if mismatch:
        return False, "B3a_default_trajectory_not_identical"

    # B3b 开关开：恢复的越界帧必须留在 field，且带 predicted 标记。
    off_field = sum(1 for r in rows[False] if r["space"] == "field")
    on_field = sum(1 for r in rows[True] if r["space"] == "field")
    off_image = sum(1 for r in rows[False] if r["space"] == "image")
    on_image = sum(1 for r in rows[True] if r["space"] == "image")
    off_pred = sum(1 for r in rows[False] if r["predicted"])
    on_pred = sum(1 for r in rows[True] if r["predicted"])
    off_updates = sum(1 for r in rows[False] if r["bt"])
    on_updates = sum(1 for r in rows[True] if r["bt"])
    off_ev = sum(1 for r in rows[False] if r["out_of_play"])
    on_ev = sum(1 for r in rows[True] if r["out_of_play"])
    on_pred_updates = sum(1 for r in rows[True]
                          if r["bt"] and (r["miss"] or 0) > 0
                          and r["space"] == "field" and not in_field_box(r["xy"]))
    print("B3 flag_off field=%d image=%d predicted=%d events=%d updates=%d"
          % (off_field, off_image, off_pred, off_ev, off_updates))
    print("B3 flag_on  field=%d image=%d predicted=%d events=%d updates=%d "
          "predicted_updates=%d"
          % (on_field, on_image, on_pred, on_ev, on_updates, on_pred_updates))
    for r in rows[True]:
        print("   on frame=%2d bt=%-5s miss=%-4s space=%-5s pred=%-5s "
              "out=%-5s xy=%s"
              % (r["frame"], r["bt"], r["miss"], r["space"], r["predicted"],
                 r["out_of_play"], r["xy"]))
    if off_pred != 0:
        return False, "B3_off_mode_has_predicted_marker"
    if on_field <= off_field:
        return False, "B3_on_mode_did_not_recover_field_frames"
    if on_pred_updates <= 0:
        return False, "B3_on_mode_never_used_prediction"
    for r in rows[True]:
        if r["bt"] and (r["miss"] or 0) > 0 and r["space"] == "field" \
                and not in_field_box(r["xy"]) and not r["predicted"]:
            return False, "B3_recovered_frame_not_marked_predicted"
    if on_ev < off_ev:
        return False, "B3_event_count_unexpectedly_dropped"

    # B4 代价必须如实打印：新增 out_of_play 不是收益，板上要用真值复核。
    on_xy = [r["xy"] for r in rows[True] if r["space"] == "field" and r["xy"]]
    max_over = max((max(0.0, x - 110.0, -5.0 - x, y - 73.0, -5.0 - y)
                    for x, y in on_xy), default=0.0)
    print("B4 tradeoff: field_frames +%d, events +%d, max_overshoot_m=%.2f "
          "（event 增加来自越界预测，不能当成真实出界）"
          % (on_field - off_field, on_ev - off_ev, max_over))
    print("B4 note: 板上应只把 overshoot 在可接受带内的预测帧算作恢复；"
          "稳定片段 144 帧的 out_of_play 全为 false，任何新增出界都要先当真值复核。")
    return True, "B3_ok"


def main():
    fps = _load_test_fps()
    ok, why = test_lost_sec(fps)
    if not ok:
        return _fail(why)
    ok, why = test_pipeline_sticky(fps)
    if not ok:
        return _fail(why)
    ok, why = test_boundary_policy()
    if not ok:
        return _fail(why)
    ok, why = test_boundary_trajectory()
    if not ok:
        return _fail(why)
    print("\nBALL_SWITCHES_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
