"""train_person_ds.py -- 用完整比赛数据集训练球员候选验证器（按比赛切分）。

数据：tools/build_person_ds.py 生成的 data/train/person_ds.npz
模型：L2 逻辑回归（1200 维），板端 <1 ms/ROI。
验证：**按视频切分**（一场训练、另一场测试），避免同场泄漏。
产出：data/models/person_gate_ds.npz + data/results/person_gate_ds.json
"""
import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
DS = REPO / "data" / "train" / "person_ds.npz"
OUT_MODEL = REPO / "data" / "models" / "person_gate_ds.npz"
OUT_JSON = REPO / "data" / "results" / "person_gate_ds.json"


def train_logreg(x, y, l2=1e-3, iters=2000, lr=0.3):
    mu, sd = x.mean(0), x.std(0) + 1e-6
    z = (x - mu) / sd
    w = np.zeros(z.shape[1], np.float32)
    b = 0.0
    pos_w = (len(y) - y.sum()) / max(1, y.sum())
    sw = np.where(y == 1, pos_w, 1.0).astype(np.float32)
    for _ in range(iters):
        p = 1.0 / (1.0 + np.exp(-(z @ w + b)))
        g = (p - y) * sw
        w -= lr * (z.T @ g / len(y) + l2 * w)
        b -= lr * g.mean()
    return w, b, mu, sd


def metrics(y, s, thr=0.5):
    pos, neg = s[y == 1], s[y == 0]
    auc = float((pos[:, None] > neg[None, :]).mean() + 0.5 * (pos[:, None] == neg[None, :]).mean()) \
        if len(pos) and len(neg) else float("nan")
    pred = (s >= thr).astype(int)
    acc = float((pred == y).mean())
    prec = float(((pred == 1) & (y == 1)).sum() / max(1, (pred == 1).sum()))
    rec = float(((pred == 1) & (y == 1)).sum() / max(1, y.sum()))
    return dict(auc=auc, acc=acc, precision=prec, recall=rec, n=len(y),
                n_pos=int(y.sum()))


def main():
    z = np.load(DS, allow_pickle=True)
    X, y, meta = z["X"], z["y"].astype(np.int32), z["meta"]
    vids = sorted(set(m[0] for m in meta))
    if len(vids) < 2:
        print("PERSON_DS_FAIL need_two_videos")
        return 2
    tr = np.array([m[0] == vids[0] for m in meta])
    te = ~tr
    if y[tr].sum() == 0 or y[te].sum() == 0:
        print("PERSON_DS_FAIL single_class")
        return 2
    w, b, mu, sd = train_logreg(X[tr], y[tr])
    s = 1.0 / (1.0 + np.exp(-(((X[te] - mu) / sd) @ w + b)))
    m = metrics(y[te], s)
    np.savez(OUT_MODEL, w=w, b=np.float32(b), mu=mu, sd=sd, threshold=np.float32(0.5))
    res = dict(tool="tools/train_person_ds.py", train_video=vids[0], test_video=vids[1],
               n_train=int(tr.sum()), **m, model=str(OUT_MODEL))
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    json.dump(res, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("PERSON_DS_TRAIN_OK auc=%.3f acc=%.3f prec=%.3f rec=%.3f n_test=%d" % (
        m["auc"], m["acc"], m["precision"], m["recall"], m["n"]))
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())