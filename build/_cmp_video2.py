"""左右对照：letterbox@320（默认） vs w05@480 letterbox（精度档）。"""
import os, sys, time
import cv2, numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import board_detect
HERE = os.path.dirname(os.path.abspath(__file__))
SHIM = os.path.join(HERE, "_fake_infer.bat")
SRC = sys.argv[1] if len(sys.argv) > 1 else "data/samples/scenes/c05_t01730.0.mp4"
OUT = sys.argv[2] if len(sys.argv) > 2 else "build/compare_480_c05.mp4"
EVERY = 5
CFG = [("v8n25@320 lb", "build/models/person_v8n25", 320),
       ("w05@480 lb", "build/models/person_w05_480", 480)]
cap = cv2.VideoCapture(SRC)
fps = cap.get(cv2.CAP_PROP_FPS) or 25
W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
vw = cv2.VideoWriter(OUT, cv2.VideoWriter_fourcc(*"mp4v"), fps, (2 * W, H))
tmp = os.path.join(HERE, "_cmp_frame2.jpg")
last = {n: [] for n, _, _ in CFG}; counts = {n: 0 for n, _, _ in CFG}
i = 0; t0 = time.time()
while True:
    ok, frame = cap.read()
    if not ok: break
    if i % EVERY == 0:
        cv2.imwrite(tmp, frame)
        for name, stem, S in CFG:
            dets, _ = board_detect.detect(tmp, SHIM, stem + ".param", stem + ".bin", 0.25, 0.45, size=S)
            last[name] = dets; counts[name] += len(dets)
    tiles = []
    for name, _, _ in CFG:
        im = frame.copy()
        for d in last[name]:
            x1, y1, x2, y2 = [int(round(v)) for v in d[:4]]
            cv2.rectangle(im, (x1, y1), (x2, y2), (0, 0, 255), 2)
        cv2.rectangle(im, (0, 0), (im.shape[1], 30), (0, 0, 0), -1)
        cv2.putText(im, "%s  f%d  boxes=%d" % (name, i, len(last[name])), (8, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        tiles.append(im)
    vw.write(np.hstack(tiles)); i += 1
cap.release(); vw.release()
print("CMP_VIDEO %s frames=%d %.0fs" % (OUT, i, time.time() - t0))
print("BOX_TOTAL", counts)
