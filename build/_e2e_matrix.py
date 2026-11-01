"""部署同路径的 2x2x2 对照：{基线 person_v8n25, 640 微调} x {320,640} x {letterbox,squash}"""
import glob, os, sys
import cv2, numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import board_detect

HERE = os.path.dirname(os.path.abspath(__file__))
SHIM = os.path.join(HERE, "_fake_infer.bat")
MODELS = [("微调480", "build/models/person_w05_480.param", "build/models/person_w05_480.bin"),
          ("微调320", "build/models/person_w05_320.param", "build/models/person_w05_320.bin"),
          ("基线v8n25", "build/backup_20261101_2330/person_v8n25.param",
           "build/backup_20261101_2330/person_v8n25.bin"),
          ("640微调", "build/models/person_w05_640.param", "build/models/person_w05_640.bin")]

def iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1]); x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    w, h = max(0.0, x2-x1), max(0.0, y2-y1); inter = w*h
    ua = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - inter
    return inter/ua if ua > 0 else 0.0

files = sorted(glob.glob("data/train/det/images/val/*.jpg"))[:100]
GT = {}
for f in files:
    img = cv2.imread(f); H, W = img.shape[:2]
    g = []
    for ln in open(f.replace("images", "labels").rsplit(".", 1)[0] + ".txt"):
        t = ln.split()
        if len(t) < 5 or t[0] != "0": continue
        cx, cy, w, h = map(float, t[1:5])
        g.append([(cx-w/2)*W, (cy-h/2)*H, (cx+w/2)*W, (cy+h/2)*H])
    if g: GT[f] = g

for mname, param, binp in MODELS:
    for size in (320, 640):
        for lb in (True, False):
            vals, ngt = [], 0
            for f, gts in GT.items():
                dets, _ = board_detect.detect(f, SHIM, param, binp, 0.25, 0.45,
                                              size=size, letterbox=lb)
                vals.append(sum(1 for g in gts if any(iou(g, d) >= 0.5 for d in dets)) / len(gts))
                ngt += len(gts)
            print("M %-10s size=%-3d %-9s 召回=%.3f (真值 %d)" %
                  (mname, size, "letterbox" if lb else "squash", float(np.mean(vals)), ngt), flush=True)
