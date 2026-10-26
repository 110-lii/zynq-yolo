"""ball_policy.py —— 球轨迹的丢失窗口与坐标放置策略（纯函数，离线可测）

本模块只承载两个与板无关、可独立开关的判定，供 `board_pipeline.py` 与
`check_ball_switches.py` 共用同一份实现：

1. `resolve_max_miss()`：`--ball-lost-sec`（秒） -> `BallTracker.max_miss`。
   `None` 时必须原样返回稳定版的 `4`，保证默认路径零改动。
2. `place_ball()`：球坐标放置。默认与稳定版一致——球场坐标超出
   `[-5, 110] x [-5, 73]`（球场范围 ±5 m）就退回图像坐标；
   仅在显式允许且该帧是预测点（`is_predicted=True`）时才越过该门限。

注意（与 session 010 的变量拆分有关）：
* `BallTracker.step()` 在管线里**每视频帧调用一次**，与 `--ball-every` 无关，
  所以 `miss` 的计数单位是视频帧。`--ball-lost-sec` 按 `fps` 换算成 `max_miss`，
  不改变采样节奏，也不改变速度衰减 `vx *= 0.6` 或 `min_speed=3.0`。
* 预测点能否输出还受 `min_speed` 限制；本模块不修改这一耦合。
"""
from __future__ import annotations

DEFAULT_MAX_MISS = 4

# 稳定版的有效区门限：球场范围 ±5 m（docs/system_status.md「已知边界」第 1 条）
FIELD_MARGIN = 5.0
PITCH_X = 105.0
PITCH_Y = 68.0
FIELD_X_MIN = -FIELD_MARGIN
FIELD_X_MAX = PITCH_X + FIELD_MARGIN
FIELD_Y_MIN = -FIELD_MARGIN
FIELD_Y_MAX = PITCH_Y + FIELD_MARGIN


def resolve_max_miss(lost_sec, fps):
    """把丢失窗口（秒）换算成 BallTracker 的 max_miss（视频帧数）。

    ``lost_sec is None`` 时返回稳定版默认值 4，不做任何换算。
    窗口语义：最后一次命中后，允许 ``max_miss`` 次 step 未命中；
    因此 ``max_miss / fps`` ≈ 容忍的秒数。
    """
    if lost_sec is None:
        return DEFAULT_MAX_MISS
    if fps is None or fps <= 0:
        raise ValueError("fps must be > 0")
    if lost_sec < 0:
        raise ValueError("lost_sec must be >= 0")
    return max(0, int(round(float(lost_sec) * float(fps))))


def in_field_box(f_xy):
    """球场坐标是否落在稳定版的有效区内（含 ±5 m 余量）。"""
    if f_xy is None:
        return False
    x, y = f_xy
    return FIELD_X_MIN <= x <= FIELD_X_MAX and FIELD_Y_MIN <= y <= FIELD_Y_MAX


def place_ball(f_xy, img_xy, confidence, is_predicted, allow_predict_past_boundary):
    """复刻 board_pipeline 的球输出分支，返回 ``ball`` dict。

    f_xy   : 球场坐标（或 None，表示无标定/单应失败）
    img_xy : ``(b.x, b.y)`` 图像坐标
    其余参数见模块头。默认（``allow_predict_past_boundary=False``）与旧实现
    逐字段一致；只有「预测点 + 显式允许 + 越界」这一种组合会留在球场坐标系，
    并额外带 ``"predicted": true``，便于板上区分回填帧。
    """
    past = f_xy is not None and not in_field_box(f_xy)
    if f_xy is not None and (not past or (allow_predict_past_boundary and is_predicted)):
        ball = {"x": round(f_xy[0], 2), "y": round(f_xy[1], 2),
                "confidence": confidence, "valid": True, "space": "field"}
        if past:
            ball["predicted"] = True
        return ball
    return {"x": round(img_xy[0], 1), "y": round(img_xy[1], 1),
            "confidence": confidence, "valid": True, "space": "image"}
