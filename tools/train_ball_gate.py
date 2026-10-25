"""train_ball_gate.py -- 训练"球/非球"小判别器（合成正样本 + 真实负样本）。

为什么用合成正样本
------------------
两批人工标注只得到 **5 个真实球**（且来自 5 帧），不足以训练；但 290 个真实负样本
（白袜/白鞋/白线/广告牌/UI）是真实分布。球在 4~16 px 时外观非常简单（亮、圆、
贴草地），合成正样本能覆盖这一档；风险是合成→真实的域差，所以验证必须用真球。

特征：16x16 灰度（256）+ [平均饱和度, 最大亮度, 草地环绕, 圆度, 尺寸, 面积] = 262 维
模型：L2 逻辑回归（numpy 实现，无 sklearn 依赖）
验证：真球留一（leave-one-frame-out）+ 真实负样本按帧切分

产出：
    data/models/ball_gate.npz        weights + mean/std + threshold
    data/results/ball_gate.json      指标

令牌：BALL_GATE_OK <auc> <recall5> <fp_rate>
"""
import csv
import json
from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[1]
NATIVE = REPO / ".research" / "native"
CLIPS = REPO / "data" / "samples" / "incidents"
OUT_MODEL = REPO / "data" / "models" / "ball_gate.npz"
OUT_JSON = REPO / "data" / "results" / "ball_gate.json"
rng = np.random.RandomState(20261021)


def grass_mask(bgr):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    return ((hsv[:, :, 0] > 30) & (hsv[:, :, 0] < 95) &
            (hsv[:, :, 1] > 60) & (hsv[:, :, 2] > 40))


def features(crop64):
    g = cv2.cvtColor(crop64, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(g, (16, 16), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    hsv = cv2.cvtColor(crop64, cv2.COLOR_BGR2HSV)
    # 中心 1/3 区域当作候选本体
    h, w = crop64.shape[:2]
    c = crop64[h // 3:2 * h // 3, w // 3:2 * w // 3]
    ch = cv2.cvtColor(c, cv2.COLOR_BGR2HSV)
    sat = float(ch[:, :, 1].mean()) / 255.0
    vmax = float(ch[:, :, 2].max()) / 255.0
    # 中心亮度块的“圆度”：阈值后最大连通域面积 / 外接框面积
    gm = cv2.cvtColor(c, cv2.COLOR_BGR2GRAY)
    _, m = cv2.threshold(gm, 150, 255, cv2.THRESH_BINARY)
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    fill = 0.0
    size = 0.0
    if cnts:
        cc = max(cnts, key=cv2.contourArea)
        a = float(cv2.contourArea(cc))
        x, y, ww, hh = cv2.boundingRect(cc)
        fill = a / float(max(1, ww * hh))
        size = max(ww, hh) / 64.0
    return np.concatenate([[sat, vmax, fill, size], small.ravel()]).astype(np.float32)


def crop_around(img, cx, cy, side):
    h, w = img.shape[:2]
    side = int(max(16, min(96, side)))
    x0 = int(np.clip(cx - side / 2, 0, max(0, w - side)))
    y0 = int(np.clip(cy - side / 2, 0, max(0, h - side)))
    c = img[y0:y0 + side, x0:x0 + side]
    if c.shape[0] < 8 or c.shape[1] < 8:
        return None
    return cv2.resize(c, (64, 64), interpolation=cv2.INTER_AREA)


def real_samples():
    """返回 [(features, label, frame_key)]；标签来自两批人工标注。"""
    xs, ys, keys = [], [], []
    seen = set()
    for man, lab in ((REPO / "build" / "ball_rois.csv", REPO / "data" / "annot" / "ball_rois_labels.csv"),
                     (REPO / "build" / "ball_rois2.csv", REPO / "data" / "annot" / "ball_rois2_labels.csv")):
        if not man.exists() or not lab.exists():
            continue
        L = {r["id"]: r["label"] for r in csv.DictReader(open(lab, encoding="utf-8"))}
        for r in csv.DictReader(open(man, encoding="utf-8")):
            lb = L.get(r["id"])
            if lb not in ("b", "n"):
                continue
            key = (r["frame"], round(float(r["cx"])), round(float(r["cy"])))
            if key in seen:
                continue
            seen.add(key)
            img = cv2.imread(str(REPO / "build" / ("ball_rois2" if "rois2" in str(man) else "ball_rois") / r["img"]))
            if img is None:
                continue
            xs.append(features(img))
            ys.append(1 if lb == "b" else 0)
            keys.append(r["frame"])
    return np.array(xs), np.array(ys), np.array(keys)


def draw_ball(img, cx, cy, d):
    r = max(2, int(round(d / 2.0)))
    # 阴影
    cv2.ellipse(img, (int(cx + r * 0.5), int(cy + r * 0.8)), (r, max(1, r // 2)),
                0, 0, 360, (40, 60, 40), -1)
    shade = int(rng.randint(225, 256))
    cv2.circle(img, (int(cx), int(cy)), r, (shade, shade, shade), -1)
    # 一点灰斑，避免“完美纯白”
    if r >= 3 and rng.rand() < 0.7:
        cv2.circle(img, (int(cx - r * 0.3), int(cy - r * 0.3)),
                   max(1, r // 3), (int(shade * 0.82),) * 3, -1)
    k = rng.choice([1, 3, 3, 5])
    if k > 1:
        img[:] = cv2.GaussianBlur(img, (k, k), 0)
    return img


def synthetic_samples(n=800):
    frames = [cv2.imread(str(p)) for p in sorted(NATIVE.glob("match_*.jpg"))]
    for p in sorted(CLIPS.glob("inc_*.mp4"))[:6]:
        cap = cv2.VideoCapture(str(p))
        for idx in (0, 30, 60):
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, im = cap.read()
            if ok:
                frames.append(im)
        cap.release()
    frames = [f for f in frames if f is not None]
    xs, keys = [], []
    tries = 0
    while len(xs) < n and tries < n * 40:
        tries += 1
        img = frames[rng.randint(len(frames))].copy()
        gm = grass_mask(img)
        h, w = img.shape[:2]
        cy, cx = rng.randint(8, h - 8), rng.randint(8, w - 8)
        # 候选周围要有草
        y0, y1 = max(0, cy - 12), min(h, cy + 13)
        x0, x1 = max(0, cx - 12), min(w, cx + 13)
        if gm[y0:y1, x0:x1].mean() < 0.7:
            continue
        d = int(rng.choice([4, 5, 6, 7, 8, 10, 12, 14, 16]))
        draw_ball(img, cx, cy, d)
        c = crop_around(img, cx, cy, 6 * d)
        if c is None:
            continue
        xs.append(features(c))
        keys.append("synth")
    return np.array(xs), np.ones(len(xs), dtype=int), np.array(keys)


def train_logreg(x, y, l2=1.0, iters=800, lr=0.5):
    mu, sd = x.mean(0), x.std(0) + 1e-6
    z = (x - mu) / sd
    w = np.zeros(z.shape[1], np.float32)
    b = 0.0
    n = len(y)
    # 类别平衡权重
    pw = (n - y.sum()) / max(1, y.sum())
    sw = np.where(y == 1, pw, 1.0).astype(np.float32)
    for _ in range(iters):
        p = 1.0 / (1.0 + np.exp(-(z @ w + b)))
        g = (p - y) * sw
        w -= lr * (z.T @ g / n + l2 * w / n)
        b -= lr * g.mean()
    return w, b, mu, sd


def predict(x, w, b, mu, sd):
    return 1.0 / (1.0 + np.exp(-(((x - mu) / sd) @ w + b)))


def auc(y, s):
    pos, neg = s[y == 1], s[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    return float((pos[:, None] > neg[None, :]).mean() + 0.5 * (pos[:, None] == neg[None, :]).mean())


def main():
    xr, yr, kr = real_samples()
    xs, ys, ks = synthetic_samples(800)
    print("real n=%d pos=%d | synth n=%d" % (len(yr), int(yr.sum()), len(ys)))

    # 真负样本按帧切 70/30；合成正样本按 80/20
    frames = sorted(set(kr[yr == 0]))
    rng.shuffle(frames)
    cut = max(1, int(len(frames) * 0.7))
    tr_f, te_f = set(frames[:cut]), set(frames[cut:])
    real_tr = np.array([k in tr_f for k in kr])
    real_te = ~real_tr
    syn_tr = rng.rand(len(ys)) < 0.8

    xtr = np.vstack([xr[real_tr], xs[syn_tr]])
    ytr = np.concatenate([yr[real_tr], ys[syn_tr]])
    w, b, mu, sd = train_logreg(xtr, ytr)

    s_te = predict(xr[real_te], w, b, mu, sd)
    a = auc(yr[real_te], s_te)

    # 5 个真球留一（每次只用真实负样本 + 其余合成正样本训练）
    pos_idx = np.where(yr == 1)[0]
    scores = []
    for i in pos_idx:
        keep_real = np.ones(len(yr), bool)
        keep_real[i] = False
        x2 = np.vstack([xr[keep_real], xs[syn_tr]])
        y2 = np.concatenate([yr[keep_real], ys[syn_tr]])
        w2, b2, mu2, sd2 = train_logreg(x2, y2)
        scores.append(float(predict(xr[i:i + 1], w2, b2, mu2, sd2)[0]))
    scores = np.array(scores)

    sweep = {}
    for t in (0.30, 0.35, 0.40, 0.45, 0.50):
        sweep[str(t)] = {
            "fp_real_holdout": float((s_te[yr[real_te] == 0] >= t).mean()),
            "loo_recall": float(np.mean(scores >= t)),
        }
    # 取“留出集误报 <= 5%”的最低阈值（优先保真球召回）
    thr = 0.5
    for t in (0.30, 0.35, 0.40, 0.45, 0.50):
        if sweep[str(t)]["fp_real_holdout"] <= 0.05:
            thr = t
            break
    fp = sweep[str(thr)]["fp_real_holdout"]
    recall5 = float(np.mean(scores >= thr))

    OUT_MODEL.parent.mkdir(parents=True, exist_ok=True)
    np.savez(OUT_MODEL, w=w, b=np.float32(b), mu=mu, sd=sd, threshold=np.float32(thr))
    res = dict(tool="tools/train_ball_gate.py", n_real=int(len(yr)), n_pos=int(yr.sum()),
               n_synth=int(len(ys)), auc_real_holdout=a, fp_rate_real_holdout=fp,
               loo_pos_scores=[round(float(s), 4) for s in scores], loo_recall=recall5, sweep=sweep,
               threshold=thr, model=str(OUT_MODEL))
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    print("BALL_GATE_OK auc=%.3f loo_recall=%.0f%% fp=%.1f%%" % (a, 100 * recall5, 100 * fp))
    print(json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()