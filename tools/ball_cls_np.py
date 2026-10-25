"""ball_cls_np.py —— 足球 ROI 分类器的纯 numpy 前向（板上不依赖 ncnn/torch）

模型：Conv3x3(3→16)+BN+ReLU+Pool2 → Conv3x3(16→32)+BN+ReLU+Pool2
      → Conv3x3(32→64)+BN+ReLU+AvgPool8 → Conv1x1(64→1) → sigmoid

用法（库）：
    clf = BallClassifier(".../ball_roi.npz")
    p = clf.prob(crop_bgr)          # crop 任意尺寸，内部 resize 到 32x32
自检：
    python tools/ball_cls_np.py --selftest
"""
import argparse
import os

import cv2
import numpy as np

SZ = 32


def _im2col(x, k=3, pad=1, stride=1):
    n, c, h, w = x.shape
    xp = np.pad(x, ((0, 0), (0, 0), (pad, pad), (pad, pad)))
    ho = (h + 2 * pad - k) // stride + 1
    wo = (w + 2 * pad - k) // stride + 1
    cols = np.empty((n, c, k, k, ho, wo), dtype=np.float32)
    for i in range(k):
        for j in range(k):
            cols[:, :, i, j] = xp[:, :, i:i + ho * stride:stride, j:j + wo * stride:stride]
    return cols.reshape(n, c * k * k, ho * wo), ho, wo


def _conv(x, w, b, pad=1):
    n, c, h, wd = x.shape
    cols, ho, wo = _im2col(x, 3, pad, 1)
    wm = w.reshape(w.shape[0], -1).T          # (c*k*k, out)
    out = np.tensordot(cols.transpose(0, 2, 1), wm, axes=([2], [0]))  # (n, ho*wo, out)
    out = out.transpose(0, 2, 1).reshape(n, w.shape[0], ho, wo)
    return out + b.reshape(1, -1, 1, 1)


def _bn(x, g, be, m, v, eps=1e-5):
    s = g / np.sqrt(v + eps)
    return x * s.reshape(1, -1, 1, 1) + (be - m * s).reshape(1, -1, 1, 1)


def _maxpool2(x):
    n, c, h, w = x.shape
    return x.reshape(n, c, h // 2, 2, w // 2, 2).max(axis=(3, 5))


class BallClassifier:
    def __init__(self, npz_path):
        d = np.load(npz_path)
        self.p = {k: d[k].astype(np.float32) for k in d.files}

    def prob(self, crop_bgr):
        im = cv2.resize(crop_bgr, (SZ, SZ), interpolation=cv2.INTER_AREA)
        x = cv2.cvtColor(im, cv2.COLOR_BGR2RGB).astype(np.float32).transpose(2, 0, 1)[None] / 255.0
        p = self.p
        x = _conv(x, p["f.0.weight"], p["f.0.bias"])
        x = _bn(x, p["f.1.weight"], p["f.1.bias"], p["f.1.running_mean"], p["f.1.running_var"])
        x = np.maximum(x, 0)
        x = _maxpool2(x)
        x = _conv(x, p["f.4.weight"], p["f.4.bias"])
        x = _bn(x, p["f.5.weight"], p["f.5.bias"], p["f.5.running_mean"], p["f.5.running_var"])
        x = np.maximum(x, 0)
        x = _maxpool2(x)
        x = _conv(x, p["f.8.weight"], p["f.8.bias"])
        x = _bn(x, p["f.9.weight"], p["f.9.bias"], p["f.9.running_mean"], p["f.9.running_var"])
        x = np.maximum(x, 0)
        x = x.mean(axis=(2, 3))                    # AvgPool8 -> 1x1
        v = float((x * p["head.weight"].reshape(1, -1)).sum() + p["head.bias"][0])
        return 1.0 / (1.0 + np.exp(-v))


def _selftest():
    import json
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "ball_roi_full2")
    clf = BallClassifier(os.path.join(root, "model", "ball_roi.npz"))
    lab = json.load(open(os.path.join(root, "labels.json"), encoding="utf-8"))
    pos, neg = lab["positives"][:12], lab["negatives"][:12]
    sp = [clf.prob(cv2.imread(os.path.join(root, "crops", "%04d.png" % i))) for i in pos]
    sn = [clf.prob(cv2.imread(os.path.join(root, "crops", "%04d.png" % i))) for i in neg]
    print("POS mean %.3f  min %.3f  max %.3f" % (np.mean(sp), np.min(sp), np.max(sp)))
    print("NEG mean %.3f  min %.3f  max %.3f" % (np.mean(sn), np.min(sn), np.max(sn)))
    ok = np.mean(sp) > np.mean(sn) + 0.2
    print("NP_CLS_SELFTEST", "OK" if ok else "WEAK")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        raise SystemExit(_selftest())
    ap.print_help()