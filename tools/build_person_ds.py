"""build_person_ds.py -- 用两场完整比赛生成“球员候选”数据集（YOLOv8n 当弱标注）。

流程：
  1) 在两场比赛里等间隔取帧；
  2) 跑与板上一致的运动候选（320x180 帧差 + 形态学 + 连通域）；
  3) YOLOv8n(person, conf>=0.35) 给每个候选打标签（IoU>=0.3 或中心落入人框）；
  4) 裁 32x64 窗口，提 24x48 灰度 + HSV 三通道 16-bin 直方图（1200 维）；
  5) 存 data/train/person_ds.npz（X, y, video, t, box）。

用法：
    python tools/build_person_ds.py --frames 200
令牌：PERSON_DS_OK <n> <pos> <path>
"""
import argparse
import time
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO

REPO = Path(__file__).resolve().parents[1]
VIDEOS = [
    Path(r"D:\yt-dlp\downloads\migu_5106947698\migu_5106947698.mp4"),
    Path(r"D:\yt-dlp\downloads\migu_968460165\2026中超第28轮全场回放_大连英博海发3-1深圳新鹏城.mp4"),
]
OUT = REPO / "data" / "train" / "person_ds.npz"


def motion_props(bgr, sw=320):
    h = int(round(sw * bgr.shape[0] / bgr.shape[1] / 2.0)) * 2
    small = cv2.resize(bgr, (sw, h), interpolation=cv2.INTER_AREA)
    return small


def features(crop):
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, (24, 48), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hist = []
    for ch in range(3):
        hh = cv2.calcHist([hsv], [ch], None, [16], [0, 256]).ravel()
        hist.append(hh / max(1.0, hh.sum()))
    return np.concatenate([g.ravel(), np.concatenate(hist)]).astype(np.float32)


def crop_window(img, box):
    x, y, w, h = box
    cx, cy = x + w / 2.0, y + h / 2.0
    H = max(24.0, 1.3 * h)
    W = H / 2.0
    x0 = int(np.clip(cx - W / 2, 0, max(0, img.shape[1] - W)))
    y0 = int(np.clip(cy - H / 2, 0, max(0, img.shape[0] - H)))
    c = img[y0:y0 + int(H), x0:x0 + int(W)]
    if c.shape[0] < 8 or c.shape[1] < 4:
        return None
    return cv2.resize(c, (32, 64), interpolation=cv2.INTER_AREA)


def iou(a, b):
    ax1, ay1, ax2, ay2 = a[0], a[1], a[0] + a[2], a[1] + a[3]
    bx1, by1, bx2, by2 = b[0], b[1], b[0] + b[2], b[1] + b[3]
    ix = max(0, min(ax2, bx2) - max(ax1, bx1))
    iy = max(0, min(ay2, by2) - max(ay1, by1))
    inter = ix * iy
    ua = a[2] * a[3] + b[2] * b[3] - inter
    return inter / ua if ua > 0 else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=200)
    args = ap.parse_args()
    model = YOLO(str(REPO / "data" / "models" / "yolov8n.pt"))

    X, Y, META = [], [], []
    t0 = time.perf_counter()
    per_v = max(1, args.frames // len(VIDEOS))
    for vp in VIDEOS:
        cap = cv2.VideoCapture(str(vp))
        if not cap.isOpened():
            continue
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        prev = None
        for k in range(per_v):
            idx = int(total * (0.05 + 0.9 * k / max(1, per_v)))
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, frame = cap.read()
            if not ok:
                continue
            small = motion_props(frame)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            if prev is None:
                prev = gray
                continue
            d = cv2.absdiff(gray, prev)
            prev = gray
            _, m = cv2.threshold(d, 10, 255, cv2.THRESH_BINARY)
            m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
            m = cv2.dilate(m, np.ones((5, 5), np.uint8), iterations=2)
            cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            sx = frame.shape[1] / float(small.shape[1])
            sy = frame.shape[0] / float(small.shape[0])
            props = []
            for c in cnts:
                if cv2.contourArea(c) < 40:
                    continue
                x, y, w, h = cv2.boundingRect(c)
                if h < 8 or not (0.25 <= w / float(h) <= 3.5):
                    continue
                props.append([int(x * sx), int(y * sy), int(w * sx), int(h * sy)])
            if not props:
                continue
            res = model(frame, imgsz=640, conf=0.35, classes=[0], verbose=False)[0]
            persons = [[float(v) for v in b] for b in res.boxes.xyxy.cpu().numpy()]
            persons = [b for b in persons if b[3] - b[1] >= 20]
            for b in props[:12]:
                c = crop_window(frame, b)
                if c is None:
                    continue
                cx, cy = b[0] + b[2] / 2.0, b[1] + b[3] / 2.0
                lab = 1 if any(iou(b, p) >= 0.3 for p in persons) else 0
                if lab == 0:
                    lab = 1 if any(p[0] <= cx <= p[2] and p[1] <= cy <= p[3] for p in persons) else 0
                X.append(features(c))
                Y.append(lab)
                META.append((vp.name, idx, b[0], b[1], b[2], b[3]))
        cap.release()

    if not X:
        print("PERSON_DS_FAIL no_samples")
        return 2
    OUT.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(OUT, X=np.array(X), y=np.array(Y, np.int8),
                        meta=np.array(META, dtype=object))
    print("PERSON_DS_OK %d %d %s (%.1fs)" % (len(Y), int(np.sum(Y)), OUT,
                                             time.perf_counter() - t0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())