"""同一段视频，左右对照：旧（压扁）vs 新（letterbox），同模型同尺寸。"""
import os, sys, time
import cv2, numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import board_detect

HERE = os.path.dirname(os.path.abspath(__file__))
SHIM = os.path.join(HERE, "_fake_infer.bat")
SRC = sys.argv[1] if len(sys.argv) > 1 else "data/samples/scenes/c05_t01730.0.mp4"
OUT = sys.argv[2] if len(sys.argv) > 2 else "build/compare_letterbox_c05.mp4"
EVERY = 5
CFG = [("OLD squash@320", False), ("NEW letterbox@320", True)]
PARAM = "build/models/person_v8n25.param"
BIN = "build/models/person_v8n25.bin"

cap = cv2.VideoCapture(SRC)
fps = cap.get(cv2.CAP_PROP_FPS) or 25
W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
vw = cv2.VideoWriter(OUT, cv2.VideoWriter_fourcc(*"mp4v"), fps, (2 * W, H))
tmp = os.path.join(HERE, "_cmp_frame.jpg")
last = {k: [] for k, _ in CFG}
i = 0; t0 = time.time(); counts = {k: 0 for k, _ in CFG}
while True:
    ok, frame = cap.read()
    if not ok:
        break
    if i % EVERY == 0:
        cv2.imwrite(tmp, frame)
        for name, lb in CFG:
            dets, _ = board_detect.detect(tmp, SHIM, PARAM, BIN, 0.25, 0.45, size=320, letterbox=lb)
            last[name] = dets
            counts[name] += len(dets)
    tiles = []
    for name, lb in CFG:
        im = frame.copy()
        for d in last[name]:
            x1, y1, x2, y2 = [int(round(v)) for v in d[:4]]
            cv2.rectangle(im, (x1, y1), (x2, y2), (0, 0, 255), 2)
        cv2.rectangle(im, (0, 0), (im.shape[1], 30), (0, 0, 0), -1)
        cv2.putText(im, "%s  f%d  boxes=%d" % (name, i, len(last[name])), (8, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        tiles.append(im)
    vw.write(np.hstack(tiles))
    i += 1
cap.release(); vw.release()
print("CMP_VIDEO %s frames=%d %dx%d fps=%.1f %.0fs" % (OUT, i, 2*W, H, fps, time.time()-t0))
print("BOX_TOTAL", counts)
