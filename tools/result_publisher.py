"""result_publisher.py —— 把跟踪结果整理成方案 §2.1 的 JSON 结构

输入：每帧的 tracks [[id, x1,y1,x2,y2, score, field_x, field_y], ...]
输出：{"timestamp", "camera_valid", "ball", "players"}

规则（方案 §2.1）：
  - 只输出落在球场范围内的球员（0<=x<=105, 0<=y<=68），带 2 m 余量；
  - confidence 取检测分数与跟踪命中数的融合（命中越多越可信）；
  - 球暂未接入时输出 valid=false。

自检：python3 tools/result_publisher.py --selftest
"""
import argparse

from events import step as event_step

PITCH_X = 105.0
PITCH_Y = 68.0
MARGIN = 2.0


def _in_field(x, y, margin=MARGIN):
    return (-margin <= x <= PITCH_X + margin) and (-margin <= y <= PITCH_Y + margin)


def build_frame(timestamp_ms, tracks, hits=None, camera_valid=True, ball=None, teams=None):
    """tracks: 每项 [id, x1,y1,x2,y2, score, field_x, field_y]；field 为空则跳过。

    teams: {track_id: "A"|"B"}，由 `tools/team_assign.py` 投票给出；缺省则不输出队别
    （保持旧行为逐字节不变）。
    """
    players = []
    for t in tracks:
        if len(t) < 8 or t[6] is None or t[7] is None:
            continue
        tid, score, fx, fy = int(t[0]), float(t[5]), float(t[6]), float(t[7])
        if not _in_field(fx, fy):
            continue
        conf = score
        if hits:
            conf = min(1.0, score * (0.6 + 0.4 * min(hits.get(tid, 1), 5) / 5.0))
        p = {"track_id": tid, "x": round(fx, 2), "y": round(fy, 2),
             "confidence": round(conf, 3)}
        if teams is not None:
            p["team"] = teams.get(tid)
        players.append(p)
    out_ball = ball or {"x": None, "y": None, "confidence": 0.0, "valid": False}
    ev = None
    if out_ball.get("valid") and out_ball.get("space", "field") == "field":
        ev = event_step((out_ball["x"], out_ball["y"]), players)
    out = {"timestamp": timestamp_ms, "camera_valid": bool(camera_valid),
           "ball": out_ball, "players": players}
    if ev is not None:
        out["events"] = ev
    return out


def _selftest():
    tracks = [
        [1, 100, 200, 140, 360, 0.9, 40.0, 30.0],     # 场内
        [2, 500, 200, 540, 360, 0.8, 120.0, 30.0],    # x 超界
        [3, 300, 200, 340, 360, 0.7, 60.0, 80.0],     # y 超界
        [4, 300, 200, 340, 360, 0.7, None, None],     # 无坐标
    ]
    out = build_frame(123456789, tracks, hits={1: 3})
    ok = (len(out["players"]) == 1 and out["players"][0]["track_id"] == 1
          and out["players"][0]["confidence"] < 0.9
          and out["camera_valid"] is True
          and out["ball"]["valid"] is False)
    # 队别链路：teams 给了才写 team；球在场内时 possession_team 跟持球者走
    ball = {"x": 40.5, "y": 30.0, "confidence": 1.0, "valid": True, "space": "field"}
    out2 = build_frame(1, tracks, hits={1: 3}, ball=ball, teams={1: "A"})
    ok &= (out2["players"][0].get("team") == "A")
    ok &= ((out2.get("events") or {}).get("possession") == 1)
    ok &= ((out2.get("events") or {}).get("possession_team") == "A")
    out3 = build_frame(1, tracks, hits={1: 3}, ball=ball)     # 不给 teams -> 旧行为
    ok &= ("team" not in out3["players"][0])
    ok &= ((out3.get("events") or {}).get("possession_team") is None)
    print("PUBLISH_SELFTEST", "OK" if ok else "FAIL")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        raise SystemExit(_selftest())
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())