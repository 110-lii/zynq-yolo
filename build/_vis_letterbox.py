"""出一张对照图：同一帧，squash@640 vs letterbox@640（微调权重），画框+计数。"""
import os, sys
import cv2, numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import board_detect

HERE = os.path.dirname(os.path.abspath(__file__))
SHIM = os.path.join(HERE, "_fake_infer.bat")
PARAM, BIN = "build/models/person_w05_640.param", "build/models/person_w05_640.bin"
SRC = sys.argv[1] if len(sys.argv) > 1 else "data/samples/scenes/c05_t01730.0.mp4"
OUT = sys.argv[2] if len(sys.argv) > 2 else "build/_letterbox_compare.png"

cap = cv2.VideoCapture(SRC)
cap.set(cv2.CAP_PROP_POS_FRAMES, 120)
ok, frame = cap.read()
cap.release()
if not ok:
    raise SystemExit("读不到帧")
tmp = os.path.join(HERE, "_vis_frame.jpg")
cv2.imwrite(tmp, frame)

tiles = []
for label, lb in (("squash@640", False), ("letterbox@640", True)):
    dets, _ = board_detect.detect(tmp, SHIM, PARAM, BIN, 0.25, 0.45, size=640, letterbox=lb)
    im = frame.copy()
    for d in dets:
        x1, y1, x2, y2 = [int(round(v)) for v in d[:4]]
        cv2.rectangle(im, (x1, y1), (x2, y2), (0, 0, 255), 2)
    cv2.rectangle(im, (0, 0), (im.shape[1], 34), (0, 0, 0), -1)
    cv2.putText(im, "%s  boxes=%d" % (label, len(dets)), (8, 25),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
    tiles.append(im)
    print("%s boxes=%d" % (label, len(dets)))

h = min(t.shape[0] for t in tiles)
grid = np.hstack([cv2.resize(t, (int(t.shape[1] * h / t.shape[0]), h)) for t in tiles])
cv2.imwrite(OUT, grid)
print("VIS", OUT, grid.shape)
