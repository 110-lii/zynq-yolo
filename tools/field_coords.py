"""field_coords.py —— 图像坐标 -> 球场平面坐标（单应变换）

方案 §12.1：第一版用人工标定。这里只做两件事：
  1. 用 4+ 个点对算 H（--from-pairs）；
  2. 把跟踪框的底部中心（脚部）换成球场 x/y（库函数 + 管线集成）。

点对文件格式（JSON）：
    {"image": [[u1,v1],[u2,v2],...], "field": [[x1,y1],[x2,y2],...]}
    单位：图像像素 / 球场米。

用法：
    python3 tools/field_coords.py --from-pairs calib_pairs.json --out calib.json
    python3 tools/field_coords.py --selftest
"""
import argparse
import json
import math


def _solve_h(img_pts, fld_pts):
    """DLT：8 个未知量，4 个点对即可（4 点恰好、5+ 最小二乘）。"""
    n = len(img_pts)
    if n < 4:
        raise ValueError("need >=4 points")
    # 归一化，避免数值病态
    def norm(pts):
        cx = sum(p[0] for p in pts) / n
        cy = sum(p[1] for p in pts) / n
        d = sum(math.hypot(p[0] - cx, p[1] - cy) for p in pts) / n
        s = math.sqrt(2) / d if d > 0 else 1.0
        return [(p[0] - cx) * s for p in pts], [(p[1] - cy) * s for p in pts], cx, cy, s

    ix, iy, icx, icy, isc = norm(img_pts)
    fx, fy, fcx, fcy, fsc = norm(fld_pts)

    # 线性方程 A h = 0（h33=1），用最小二乘解 8 未知
    import numpy as np

    A = np.zeros((2 * n, 8))
    b = np.zeros((2 * n, 1))
    for k in range(n):
        u, v = ix[k], iy[k]
        x, y = fx[k], fy[k]
        A[2 * k] = [u, v, 1, 0, 0, 0, -x * u, -x * v]
        b[2 * k] = [x]
        A[2 * k + 1] = [0, 0, 0, u, v, 1, -y * u, -y * v]
        b[2 * k + 1] = [y]
    h, *_ = np.linalg.lstsq(A, b, rcond=None)
    Hn = np.array([[h[0, 0], h[1, 0], h[2, 0]],
                   [h[3, 0], h[4, 0], h[5, 0]],
                   [h[6, 0], h[7, 0], 1.0]])

    # 反归一化：H = T_f^-1 * Hn * T_i
    Ti = np.array([[isc, 0, -isc * icx], [0, isc, -isc * icy], [0, 0, 1]])
    Tf_inv = np.array([[1 / fsc, 0, fcx], [0, 1 / fsc, fcy], [0, 0, 1]])
    H = Tf_inv @ Hn @ Ti
    return (H / H[2, 2]).tolist()


def apply_h(H, u, v):
    d = H[2][0] * u + H[2][1] * v + H[2][2]
    if abs(d) < 1e-12:
        return None
    x = (H[0][0] * u + H[0][1] * v + H[0][2]) / d
    y = (H[1][0] * u + H[1][1] * v + H[1][2]) / d
    return x, y


def inside_hull(pts, u, v):
    """射线法：判断 (u,v) 是否落在标定点围成的凸多边形内（外推区不可信）。"""
    n = len(pts)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if ((yi > v) != (yj > v)) and (u < (xj - xi) * (v - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def box_to_field(H, box):
    """box = [x1,y1,x2,y2,...]，用底部中心（脚部）作为地面点。"""
    u = (box[0] + box[2]) * 0.5
    v = box[3]
    return apply_h(H, u, v)


def _selftest():
    import numpy as np

    # 已知 H：图像 (0..1000, 0..600) 映射到球场 (0..105, 0..68) 的仿射
    pairs = {
        "image": [[0, 0], [1000, 0], [1000, 600], [0, 600], [500, 300]],
        "field": [[0, 0], [105, 0], [105, 68], [0, 68], [52.5, 34]],
    }
    H = _solve_h(pairs["image"], pairs["field"])
    bad = 0.0
    for (u, v), (x, y) in zip(pairs["image"], pairs["field"]):
        px, py = apply_h(H, u, v)
        bad = max(bad, abs(px - x), abs(py - y))
    print("FIELD_SELFTEST", "OK" if bad < 1e-6 else "FAIL", "max_err=%.2e" % bad)
    return 0 if bad < 1e-6 else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-pairs")
    ap.add_argument("--out", default="data/calib/field_calib.json")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        raise SystemExit(_selftest())
    if not args.from_pairs:
        ap.print_help()
        return 2
    d = json.load(open(args.from_pairs, encoding="utf-8"))
    H = _solve_h(d["image"], d["field"])
    json.dump({"H": H, "source": args.from_pairs}, open(args.out, "w", encoding="utf-8"), indent=2)
    print("FIELD_CALIB_OK", args.out, "n_points=%d" % len(d["image"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())