"""train_person_gate.py -- 用 YOLOv8n 给**运动块**打标签，蒸馏极小的球员闸门。

为什么不用 YOLO 的人框直接训练
------------------------------
第一版用 YOLO 的标准人框做正样本、随机背景做负样本，AUC 0.90；但接到板上推理时，
输入是 320x180 的**运动块**（常常只有半个身子、或多人粘连），分布完全不同，
结果 10 个候选只剩 1 个。这里改成：先跑与板上一致的运动检测，再用 YOLO 框给
每个运动块打 0/1 标签（IoU>=0.3 或中心落在人框内算正）。训练与推理同分布。

特征：24x48 灰度 + HSV 三通道 16-bin 直方图（1200 维），板上约 0.2-0.5 ms/ROI。
验证：按片段切分（8 训 4 测），报 AUC / 精确率 / 召回率。
产出：data/models/person_gate.npz + data/results/person_gate.json
"""
import json
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO

REPO = Path(__file__).resolve().parents[1]
CLIPS = sorted((REPO / "data" / "samples" / "incidents").glob("inc_*.mp4"))
OUT_MODEL = REPO / "data" / "models" / "person_gate.npz"
OUT_JSON = REPO / "data" / "results" / "person_gate.json"
rng = np.random.RandomState(20261021)


def motion_boxes(bgr, sw=320, prev=None):
    h = int(round(sw * bgr.shape[0] / bgr.shape[1] / 2.0)) * 2
    small = cv2.resize(bgr, (sw, h), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    if prev is None:
        return [], gray
    d = cv2.absdiff(gray, prev)
    _, m = cv2.threshold(d, 10, 255, cv2.THRESH_BINARY)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    m = cv2.dilate(m, np.ones((5, 5), np.uint8), iterations=2)
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    sx = bgr.shape[1] / float(sw)
    sy = bgr.shape[0] / float(h)
    out = []
    for c in cnts:
        if cv2.contourArea(c) < 40:
            continue
        x, y, w, hh = cv2.boundingRect(c)
        if hh < 8 or not (0.25 <= w / float(hh) <= 3.5):
            continue
        out.append([int(x * sx), int(y * sy), int(w * sx), int(hh * sy)])
    return out, gray


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


def features(crop):
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, (24, 48), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hist = []
    for ch in range(3):
        hh = cv2.calcHist([hsv], [ch], None, [16], [0, 256]).ravel()
        hist.append(hh / max(1.0, hh.sum()))
    return np.concatenate([g.ravel(), np.concatenate(hist)]).astype(np.float32)


def iou(a, b):
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix = max(0, min(ax2, bx2) - max(ax1, bx1))
    iy = max(0, min(ay2, by2) - max(ay1, by1))
    inter = ix * iy
    ua = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / ua if ua > 0 else 0.0


def main():
    model = YOLO(str(REPO / "data" / "models" / "yolov8n.pt"))
    per_clip = {}
    for clip in CLIPS:
        cap = cv2.VideoCapture(str(clip))
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        prev = None
        xs, ys = [], []
        idx = 0
        while idx < n:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, img = cap.read()
            if not ok:
                break
            boxes, gray = motion_boxes(img, prev=prev)
            prev = gray
            if not boxes:
                idx += 3
                continue
            res = model(img, imgsz=640, conf=0.40, classes=[0], verbose=False)[0]
            persons = [[float(v) for v in b] for b in res.boxes.xyxy.cpu().numpy()]
            for b in boxes:
                c = crop_window(img, b)
                if c is None:
                    continue
                cx, cy = b[0] + b[2] / 2.0, b[1] + b[3] / 2.0
                lab = 1 if any(iou(b, p) >= 0.3 for p in persons) else 0
                if lab == 0:
                    lab = 1 if any(p[0] <= cx <= p[2] and p[1] <= cy <= p[3] for p in persons) else 0
                xs.append(features(c))
                ys.append(lab)
            idx += 4
        cap.release()
        per_clip[clip.name] = (np.array(xs), np.array(ys))
        print("clip %s n=%d pos=%d neg=%d" % (clip.name, len(ys), int(np.sum(ys)),
                                              int(len(ys) - np.sum(ys))))

    keys = list(per_clip)
    rng.shuffle(keys)
    cut = max(1, int(len(keys) * 0.7))
    tr_keys, te_keys = keys[:cut], keys[cut:]

    def stack(ks):
        return (np.vstack([per_clip[k][0] for k in ks]),
                np.concatenate([per_clip[k][1] for k in ks]))

    Xtr, ytr = stack(tr_keys)
    Xte, yte = stack(te_keys)
    if len(np.unique(ytr)) < 2 or len(np.unique(yte)) < 2:
        print("PERSON_GATE_FAIL single_class")
        return 2

    mu, sd = Xtr.mean(0), Xtr.std(0) + 1e-6
    Z = (Xtr - mu) / sd
    w = np.zeros(Z.shape[1], np.float32)
    b = 0.0
    pos_w = (len(ytr) - ytr.sum()) / max(1, ytr.sum())
    sw = np.where(ytr == 1, pos_w, 1.0).astype(np.float32)
    for _ in range(1500):
        p = 1.0 / (1.0 + np.exp(-(Z @ w + b)))
        g = (p - ytr) * sw
        w -= 0.3 * (Z.T @ g / len(ytr) + 1e-3 * w)
        b -= 0.3 * g.mean()

    s = 1.0 / (1.0 + np.exp(-((Xte - mu) / sd @ w + b)))
    pos, neg = s[yte == 1], s[yte == 0]
    auc = float((pos[:, None] > neg[None, :]).mean() + 0.5 * (pos[:, None] == neg[None, :]).mean()) \
        if len(pos) and len(neg) else float("nan")
    pred = (s >= 0.5).astype(int)
    acc = float((pred == yte).mean())
    prec = float(((pred == 1) & (yte == 1)).sum() / max(1, (pred == 1).sum()))
    rec = float(((pred == 1) & (yte == 1)).sum() / max(1, (yte == 1).sum()))

    np.savez(OUT_MODEL, w=w, b=np.float32(b), mu=mu, sd=sd, threshold=np.float32(0.5))
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    json.dump(dict(tool="tools/train_person_gate.py", n_train=int(len(ytr)), n_test=int(len(yte)),
                   train_clips=tr_keys, test_clips=te_keys, auc=auc, acc=acc,
                   precision=prec, recall=rec, model=str(OUT_MODEL)),
              open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("PERSON_GATE_OK auc=%.3f acc=%.3f prec=%.3f rec=%.3f" % (auc, acc, prec, rec))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())