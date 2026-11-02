"""给板端 ncnn_infer 生成三档 letterbox 输入（raw RGB, SxSx3）。"""
import cv2, numpy as np, os, sys
src = sys.argv[1] if len(sys.argv) > 1 else "data/samples/scenes/c05_t01730.0.mp4"
cap = cv2.VideoCapture(src); cap.set(cv2.CAP_PROP_POS_FRAMES, 120)
ok, im = cap.read(); cap.release()
assert ok, "读帧失败"
h0, w0 = im.shape[:2]
for S in (320, 480, 640):
    s = min(S / w0, S / h0)
    nw, nh = int(round(w0 * s)), int(round(h0 * s))
    px, py = (S - nw) // 2, (S - nh) // 2
    c = np.full((S, S, 3), 114, np.uint8)
    c[py:py+nh, px:px+nw] = cv2.resize(im, (nw, nh))
    rgb = cv2.cvtColor(c, cv2.COLOR_BGR2RGB)
    p = "build/_test_%d.rgb" % S
    rgb.tofile(p)
    print("MK", p, rgb.shape, os.path.getsize(p))
