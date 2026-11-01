"""_fake_infer.py —— 在 PC 上冒充 ncnn_infer，用于本地验证 board_detect.detect 全程。

参数顺序与板上 C++ 一致：
    _fake_infer.py model.param model.bin input.rgb [conf] [iou] [planar] [size]
planar=1 不走（PC 无 PL）。输出同样是 "DET x1 y1 x2 y2 score" + "INFER_MS"，
坐标是**网络输入像素坐标**，和 C++ 版一致 —— 逆变换留给 board_detect 做。
"""
import sys
import time

import cv2
import numpy as np

try:
    import ncnn
except ImportError:
    print("EXTRACT_FAIL no-ncnn", file=sys.stderr)
    raise SystemExit(1)


def iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    w, h = max(0.0, x2 - x1), max(0.0, y2 - y1)
    inter = w * h
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def main(argv):
    param, binp, rawp = argv[1], argv[2], argv[3]
    conf = float(argv[4]) if len(argv) > 4 else 0.25
    iou_t = float(argv[5]) if len(argv) > 5 else 0.45
    if len(argv) > 6 and argv[6] == "1":
        print("INPUT_FAIL planar unsupported", file=sys.stderr)
        return 1
    S = int(argv[7]) if len(argv) > 7 else 320

    buf = np.fromfile(rawp, np.uint8)
    if buf.size != S * S * 3:
        print("INPUT_FAIL size=%d want=%d" % (buf.size, S * S * 3))
        return 1
    rgb = buf.reshape(S, S, 3).astype(np.float32) / 255.0
    mat = ncnn.Mat(rgb.transpose(2, 0, 1)[None], batch_index=0).clone()

    net = ncnn.Net()
    net.opt.num_threads = 2
    if net.load_param(param) != 0 or net.load_model(binp) != 0:
        print("LOAD_FAIL")
        return 1

    t0 = time.perf_counter()
    ok = -1
    for in_name in ("images", "in0"):
        ex = net.create_extractor()
        if ex.input(in_name, mat) != 0:
            continue
        for out_name in ("output0", "out0"):
            ok, out = ex.extract(out_name)
            if ok == 0:
                break
        if ok == 0:
            break
    t1 = time.perf_counter()
    if ok != 0:
        print("EXTRACT_FAIL %d" % ok)
        return 1

    a = np.array(out)
    if a.shape[0] != a.shape[1] and a.shape[0] > a.shape[1]:
        a = a.T                                     # (6, N)
    boxes = []
    for i in range(a.shape[1]):
        s = float(a[4, i])
        if s < conf:
            continue
        cx, cy, w, h = a[0, i], a[1, i], a[2, i], a[3, i]
        boxes.append([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2, s])
    boxes.sort(key=lambda z: -z[4])
    keep = []
    for b in boxes:
        if all(iou(b, k) <= iou_t for k in keep):
            keep.append(b)
    for b in keep:
        print("DET %.1f %.1f %.1f %.1f %.3f" % tuple(b))
    print("INFER_MS %.1f" % ((t1 - t0) * 1e3))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
