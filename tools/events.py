"""events.py —— 由球/球员坐标推导比赛事件（方案 §11）

判定口径（可调，默认按 FIFA "整球过线"）：
    出界：球心越线距离 > line_margin（默认 0.11 m，即球半径）
    进球：球心进入球门区（两侧 |y-34| <= 3.66），且越线 > 线距
    球权：离球最近的球员，且距离 <= poss_r（默认 3 m）

自检：python tools/events.py --selftest
"""
import argparse

PITCH_X, PITCH_Y = 105.0, 68.0
GOAL_HALF = 3.66
LINE_MARGIN = 0.11


def out_of_play(ball, margin=LINE_MARGIN):
    """返回 (is_out, side)。side ∈ {left,right,top,bottom}。"""
    if ball is None:
        return False, None
    x, y = ball
    if x < -margin:
        return True, "left"
    if x > PITCH_X + margin:
        return True, "right"
    if y < -margin:
        return True, "bottom"
    if y > PITCH_Y + margin:
        return True, "top"
    return False, None


def is_goal(ball, margin=LINE_MARGIN):
    if ball is None:
        return False, None
    x, y = ball
    if abs(y - PITCH_Y / 2) > GOAL_HALF:
        return False, None
    if x < -margin:
        return True, "left"
    if x > PITCH_X + margin:
        return True, "right"
    return False, None


def possession(ball, players, max_r=3.0):
    """players: [{'track_id':int,'x':float,'y':float}, ...] -> (track_id, dist)"""
    if ball is None or not players:
        return None, None
    bx, by = ball
    best = (None, 1e9)
    for p in players:
        d = ((p["x"] - bx) ** 2 + (p["y"] - by) ** 2) ** 0.5
        if d < best[1]:
            best = (p["track_id"], d)
    if best[1] > max_r:
        return None, best[1]
    return best


def step(ball, players):
    """一次性给出本帧事件集合（供管线直接塞进 JSON）。"""
    out, side = out_of_play(ball)
    goal, gside = is_goal(ball)
    pid, pdist = possession(ball, players)
    return {
        "out_of_play": out,
        "out_side": side,
        "goal": goal,
        "goal_side": gside,
        "possession": pid,
        "possession_dist": None if pdist is None else round(pdist, 2),
    }


def _selftest():
    ok = True
    cases = [
        ((50, 30), (False, None)),
        ((-0.2, 30), (True, "left")),
        ((105.2, 30), (True, "right")),
        ((50, 68.3), (True, "top")),
        ((50, -0.3), (True, "bottom")),
    ]
    for ball, exp in cases:
        got = out_of_play(ball)
        if got != exp:
            print("OUT FAIL", ball, got, exp)
            ok = False
    if not (is_goal((-0.5, 34))[0] and not is_goal((-0.5, 40))[0]):
        print("GOAL FAIL")
        ok = False
    pid, d = possession((50, 34), [{"track_id": 7, "x": 51, "y": 34},
                                   {"track_id": 9, "x": 60, "y": 34}])
    if pid != 7:
        print("POSS FAIL", pid)
        ok = False
    print("EVENT_SELFTEST", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        raise SystemExit(_selftest())
    ap.print_help()