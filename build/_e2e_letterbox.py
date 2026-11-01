"""端到端：board_detect.detect(letterbox/squash) + 640 模型，跑 val 前 100 张算召回。
判据：letterbox@640 应接近 _acc_pre.log 的 0.597（真值同一份）。"""
import glob, os, sys
import cv2, numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import board_detect  # noqa

SHIM = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_fake_infer.bat")
PARAM = "build/models/person_w05_640.param"
BIN = "build/models/person_w05_640.bin"


def iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1]); x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    w, h = max(0.0, x2 - x1), max(0.0, y2 - y1); inter = w * h
    ua = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - inter
    return inter/ua if ua > 0 else 0.0


files = sorted(glob.glob("data/train/det/images/val/*.jpg"))[:100]
for lb in (True, False):
    vals, ngt = [], 0
    for f in files:
        img = cv2.imread(f)
        if img is None:
            continue
        H, W = img.shape[:2]
        gts = []
        for ln in open(f.replace("images", "labels").rsplit(".", 1)[0] + ".txt"):
            t = ln.split()
            if len(t) < 5 or t[0] != "0":
                continue
            cx, cy, w, h = map(float, t[1:5])
            gts.append([(cx-w/2)*W, (cy-h/2)*H, (cx+w/2)*W, (cy+h/2)*H])
        if not gts:
            continue
        dets, ms = board_detect.detect(f, SHIM, PARAM, BIN, 0.25, 0.45,
                                       size=640, letterbox=lb)
        vals.append(sum(1 for g in gts if any(iou(g, d) >= 0.5 for d in dets)) / len(gts))
        ngt += len(gts)
    print("E2E %-9s 召回=%.3f 图像=%d 真值=%d" %
          ("letterbox" if lb else "squash", float(np.mean(vals)), len(vals), ngt))
