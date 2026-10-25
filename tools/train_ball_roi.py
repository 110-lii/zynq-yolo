"""train_ball_roi.py —— 足球 ROI 二分类小 CNN（32x32 输入）

数据：data/ball_roi_full/{crops,manifest.json,labels.json}
用法：python tools/train_ball_roi.py --epochs 60
输出：data/ball_roi_full/ball_roi.onnx + 验证指标
"""
import argparse
import json
import os

import cv2
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset

SZ = 32


class RoiNet(nn.Module):
    def __init__(self):
        super().__init__()
        self.f = nn.Sequential(
            nn.Conv2d(3, 16, 3, padding=1), nn.BatchNorm2d(16), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(16, 32, 3, padding=1), nn.BatchNorm2d(32), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(32, 64, 3, padding=1), nn.BatchNorm2d(64), nn.ReLU(),
            nn.AvgPool2d(8),   # 输入 8x8 -> 1x1，避免 ncnn 的 Reduction 转换问题
        )
        self.head = nn.Conv2d(64, 1, 1, bias=True)   # 全卷积头，ncnn 不需要 Reshape/InnerProduct

    def forward(self, x):
        return self.head(self.f(x))                  # (N,1,1,1)


class RoiDS(Dataset):
    def __init__(self, items, train):
        self.items = items
        self.train = train

    def __len__(self):
        return len(self.items)

    def __getitem__(self, i):
        path, y = self.items[i]
        im = cv2.imread(path, cv2.IMREAD_COLOR)
        im = cv2.resize(im, (SZ, SZ), interpolation=cv2.INTER_AREA)
        im = cv2.cvtColor(im, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        if self.train:
            if np.random.rand() < 0.5:
                im = im[:, ::-1]
            if np.random.rand() < 0.5:
                im = im[::-1]
            im = np.clip(im * np.random.uniform(0.8, 1.25) + np.random.uniform(-0.1, 0.1), 0, 1)
            if np.random.rand() < 0.5:
                s = np.random.uniform(0.85, 1.15)
                h, w = im.shape[:2]
                M = cv2.getRotationMatrix2D((w / 2, h / 2), 0, s)
                M[0, 2] += np.random.uniform(-3, 3)
                M[1, 2] += np.random.uniform(-3, 3)
                im = cv2.warpAffine(im, M, (w, h), borderMode=cv2.BORDER_REFLECT)
        im = np.transpose(im, (2, 0, 1))
        return torch.from_numpy(np.ascontiguousarray(im)), torch.tensor(y, dtype=torch.float32)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="data/ball_roi_full")
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--batch", type=int, default=16)
    args = ap.parse_args()

    lab = json.load(open(os.path.join(args.dir, "labels.json"), encoding="utf-8"))
    crops = os.path.join(args.dir, "crops")
    pos = set(lab["positives"])
    neg = set(lab["negatives"])
    items = []
    for p in sorted(pos | neg):
        items.append((os.path.join(crops, "%04d.png" % p), 1 if p in pos else 0))
    rng = np.random.RandomState(0)
    rng.shuffle(items)
    n_val = max(40, int(0.2 * len(items)))
    val, tr = items[:n_val], items[n_val:]
    print("train", len(tr), "val", len(val), "pos", sum(y for _, y in tr), "/", sum(y for _, y in val))

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    net = RoiNet().to(dev)
    opt = torch.optim.AdamW(net.parameters(), lr=1e-3, weight_decay=1e-4)
    npos = sum(y for _, y in tr)
    pw = torch.tensor(float(len(tr) - npos) / max(1, npos), device=dev)
    lossf = nn.BCEWithLogitsLoss(pos_weight=pw)
    dl = DataLoader(RoiDS(tr, True), batch_size=args.batch, shuffle=True, num_workers=0)
    vdl = DataLoader(RoiDS(val, False), batch_size=64, shuffle=False)

    for ep in range(args.epochs):
        net.train()
        tot = 0.0
        for x, y in dl:
            x, y = x.to(dev), y.to(dev)
            opt.zero_grad()
            loss = lossf(net(x).view(-1), y)
            loss.backward()
            opt.step()
            tot += float(loss)
        if (ep + 1) % 20 == 0:
            print("epoch", ep + 1, "loss %.4f" % (tot / max(1, len(dl))))

    net.eval()
    scores, ys = [], []
    with torch.no_grad():
        for x, y in vdl:
            scores += torch.sigmoid(net(x.to(dev)).view(-1)).cpu().numpy().tolist()
            ys += y.numpy().tolist()
    scores, ys = np.array(scores), np.array(ys)
    for th in (0.3, 0.5, 0.7):
        pred = scores >= th
        tp = int(((pred == 1) & (ys == 1)).sum())
        fp = int(((pred == 1) & (ys == 0)).sum())
        fn = int(((pred == 0) & (ys == 1)).sum())
        p = tp / max(1, tp + fp)
        r = tp / max(1, tp + fn)
        print("th=%.1f  P=%.3f R=%.3f  tp=%d fp=%d fn=%d" % (th, p, r, tp, fp, fn))

    os.makedirs(os.path.join(args.dir, "model"), exist_ok=True)
    # 纯 numpy 前向用的权重包（板上不需要 torch）
    sd = {k: v.detach().cpu().numpy() for k, v in net.state_dict().items()}
    np.savez(os.path.join(args.dir, "model", "ball_roi.npz"), **sd)
    print("NPZ_OK", os.path.join(args.dir, "model", "ball_roi.npz"))
    torch.onnx.export(net, torch.randn(1, 3, SZ, SZ).to(dev),
                      os.path.join(args.dir, "model", "ball_roi.onnx"),
                      input_names=["input"], output_names=["logit"], opset_version=12)
    print("ONNX_OK", os.path.join(args.dir, "model", "ball_roi.onnx"))


if __name__ == "__main__":
    main()