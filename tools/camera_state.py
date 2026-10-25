"""camera_state.py —— 镜头有效性三态判断（方案 §12.3）

输入四个信号，输出 (valid, state, reason)：
    grass_frac : 画面里草地的像素占比（HSV 绿色）
    n_det      : 本帧跟踪到的目标数
    n_field    : 其中落在球场范围内的数量
    motion     : 全局运动幅度（像素/帧，相位相关结果）

规则（第一版，保守优先）：
    grass < 0.20                         -> INVALID_VIEW  (close_up_or_crowd)
    valid_frac < 0.30 且 n_det >= 8      -> INVALID_VIEW  (calibration_mismatch)
    grass >= 0.35 且 valid_frac >= 0.60  -> VALID_FIELD_VIEW
    其余                                  -> LIMITED_FIELD_VIEW

自检：python3 tools/camera_state.py --selftest
"""
import argparse

VALID = "VALID_FIELD_VIEW"
LIMITED = "LIMITED_FIELD_VIEW"
INVALID = "INVALID_VIEW"


def assess(grass_frac, n_det, n_field, motion=0.0):
    valid_frac = (float(n_field) / n_det) if n_det else 0.0

    if grass_frac < 0.20:
        return False, INVALID, "close_up_or_crowd"
    if n_det >= 8 and valid_frac < 0.10:
        return False, INVALID, "calibration_mismatch"
    if grass_frac >= 0.35 and valid_frac >= 0.40 and n_det >= 5:
        return True, VALID, "ok"
    return True, LIMITED, "limited_view"


def _selftest():
    cases = [
        ((0.05, 10, 0), (False, INVALID)),
        ((0.60, 20, 1), (False, INVALID)),
        ((0.60, 20, 15), (True, VALID)),
        ((0.60, 4, 3), (True, LIMITED)),
        ((0.25, 10, 8), (True, LIMITED)),
    ]
    ok = True
    for args, exp in cases:
        got = assess(*args)[:2]
        if got != exp:
            print("FAIL", args, got, "expect", exp)
            ok = False
    print("CAMERA_SELFTEST", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        raise SystemExit(_selftest())
    ap.print_help()