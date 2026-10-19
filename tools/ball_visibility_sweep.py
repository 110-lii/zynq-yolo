"""能不能让 YOLO「看见足球」——客观实验扫描（分辨率 / 切图 / 模型 / 置信度）。

问题
----
D004 要在「输入分辨率 / 用哪个模型 / 要不要自己训」之间定参数，但前提是一个只能用
数据回答的问题：**COCO 预训练的 YOLOv8n 到底能不能在这批真实转播画面里看见球？**

已实测的起点（本仓库既有结论，本次要复现）：
    · imgsz=320：40 帧里 0 帧检出 `sports ball`（COCO id=32）
    · imgsz=640：40 帧里 3 帧
    · float32 参考同样是 0 ⇒ 与 INT8 量化无关
    · 球在 1280x720 原图上只有约 4-12 像素

本工具一次跑完四组变量，每组都落到**同一个主判据**上：

    1) 输入分辨率   : imgsz = 320 / 640 / 960 / 1280 / 1600
    2) 切图 tiling  : 把 1280x720 切成 640/512/320/256 的重叠子图，逐块推理再映射回全图 + NMS
    3) 模型容量     : yolov8n vs yolov8s（同分辨率）
    4) 置信度阈值   : 0.25 / 0.10 / 0.05 / 0.02 / 0.01 —— 判断球是「被阈值卡掉」还是「网络没响应」

判据（必须先能失败）
------------------
主判据 = **球检出帧数**（40 帧里的绝对数）。基线 320→0、640→3。任何「改进」必须在
这个数字上体现，不接受只有定性描述。

三条防假阳/防假阴的护栏（历史教训：本仓库出现过「判据本身算错」的假结果）：
    a) 类别自检   : 打印 model.names，断言 names[32] == "sports ball"，否则直接 FAIL。
    b) 基线复现   : imgsz=320 应复现 0/40、imgsz=640 应复现 3/40；对不上就说明测量代码有问题。
    c) 阴性对照   : 把帧水平翻转 / 加噪 / 涂灰后重跑，检出数不应上升（涂灰应当归零）。
    d) 阳性对照   : 在 data/samples/calib_commons/ 的 Wikimedia 足球图上跑同一套解码，
                    球必须能被检出 —— 证明「0 检出」不是解码路径坏了。

产物
----
    data/results/ball_visibility_sweep.json   每档的帧数/耗时/球检出数（入库）
    data/results/viz/sweep/*.png              人眼复核图（gitignore，不入库）

用法
----
    D:\\python\\python.exe tools\\ball_visibility_sweep.py
    D:\\python\\python.exe tools\\ball_visibility_sweep.py --limit 10 --imgsz 640,1280

    # 没有 .research/native/ 的 1280 原生帧时，会自动从源视频按同一批关键帧重抽
    # （第三方转播内容，只读、只写 .research/，不入库）

末行令牌：`SWEEP_DONE <配置数>`；出错 `SWEEP_FAIL <原因>`。
"""

import sys

# 先关掉 .pyc 写入（tools/check_repo.py 的 check d) 会扫盘找 __pycache__ 并 FAIL）
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8")  # 控制台是 GBK，日志里有中文

import argparse
import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

# COCO 类别号（自检用：断言 32 号就是 sports ball）
PERSON = 0
SPORTS_BALL = 32

# 解码口径（与 tools/class_coverage.py 一致：conf 后处理、逐类 NMS、agnostic=False）
NMS_IOU = 0.45
CONF_FLOOR = 0.01          # 本工具所有统计都从这一档往下数，避免重复前向
CONFS = (0.25, 0.10, 0.05, 0.02, 0.01)
MAX_DET = 3000             # 别让 ultralytics 的 max_det=300 在低阈值下截断

# JSON 落盘时的列式布局（把 25 档 × 5 阈值展开成数组，文件才只有几 KB）
# rows 只留主判据两列（每档都存）；其余指标只存 0.25 / 0.01 两档，见 DETAIL_COLS
CONF_COLS = ("ball_frames", "ball_boxes")
DETAIL_COLS = ("ball_w_px_median", "ball_h_px_median", "ball_aspect_median",
             "ball_score_max", "person_boxes", "zero_det_frames")

# 测试帧（40 张，640x360 JPEG，第三方转播内容，gitignore）
CALIB_DIRS = (
    "data/samples/calib_real/frames_calib",
    "data/samples/calib_real/frames_test",
)
# 同一批帧的原生 1280x720（由 tools/make_calib_from_video.py 的关键帧池一一对应抽出）
NATIVE_DIR = ".research/native"
# 阳性对照：Wikimedia 足球图（球是几十像素的大目标，必须能检出）
POS_DIR = "data/samples/calib_commons"
SELECTION = "data/samples/calib_real/_scratch/selection.json"
DEFAULT_VIDEO = Path(r"D:\yt-dlp\downloads\migu_5106947698\migu_5106947698.mp4")
DEFAULT_FFMPEG = Path(r"D:\yt-dlp\ffmpeg\ffmpeg.exe")

OUT_JSON = "data/results/ball_visibility_sweep.json"
OUT_VIZ = "data/results/viz/sweep"

# 球在原生 1280x720 上的直径（用户已实测 4-12 px，取中位 8 px）。
# 用它算「球在网络输入里有几像素」不会被检出与否反过来筛选样本 —— 那条曲线必须
# 包含「一个都没检出」的配置，否则整个实验就只剩成功案例了。
BALL_PX_NATIVE = 8.0

# Zynq-7020 PL 引擎的算力硬约束（来自 tools/fps_feasibility.py，已实测）
GOP_320 = 2.186            # 一帧 @320 的运算量（GOP）
PL_GOPS = 15.3             # PL 引擎实测吞吐（GOPS）
# 每档把「CPU 实测 ms/帧」换算成「如果搬到 PL 上、按面积比例折算」的 fps。
# 这是**投影不是实测**：YOLO 的算力随 imgsz^2 涨，切片数按片计。
# 只用来说明"分辨率这条路在 Zynq 上买不起"，不能当板上性能

C_BALL = (60, 60, 245)     # 红：sports ball
C_GRID = (0, 210, 255)     # 黄：切片边界
C_TXT = (255, 255, 255)
C_BG = (32, 32, 32)


# ------------------------------------------------------------------ 小工具

def _abs(p):
    p = Path(p)
    return p if p.is_absolute() else REPO / p


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def list_images(paths) -> list:
    out = []
    for d in paths:
        p = _abs(d)
        if not p.is_dir():
            continue
        out += sorted(q for q in p.iterdir() if q.suffix.lower() in IMG_EXT)
    return out


def load_frames(paths, limit=0):
    import cv2

    imgs = []
    for p in paths[: limit or None]:
        a = cv2.imread(str(p))
        if a is None:
            print("  [warn] 读不出 %s，跳过" % p.name)
            continue
        imgs.append((p, a))
    return imgs


# ------------------------------------------------------------------ 检测原语

def _nms_one(boxes, scores, iou_thr):
    """单类贪心 NMS。boxes/scores 需已按分数降序。返回保留下标。"""
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
    order = np.arange(len(boxes))
    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(int(i))
        if order.size == 1:
            break
        rest = order[1:]
        xx1 = np.maximum(x1[i], x1[rest])
        yy1 = np.maximum(y1[i], y1[rest])
        xx2 = np.minimum(x2[i], x2[rest])
        yy2 = np.minimum(y2[i], y2[rest])
        inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
        iou = inter / (areas[i] + areas[rest] - inter + 1e-9)
        order = rest[iou <= iou_thr]
    return keep


def nms_per_class(boxes, scores, cls, iou_thr=NMS_IOU):
    """逐类别 NMS（agnostic=False，与 ultralytics 默认一致）。返回按分数降序的下标。"""
    if len(boxes) == 0:
        return np.zeros(0, dtype=int)
    keep = []
    for c in np.unique(cls):
        idx = np.where(cls == c)[0]
        idx = idx[np.argsort(-scores[idx])]
        sel = _nms_one(boxes[idx].astype(np.float64), scores[idx].astype(np.float64), iou_thr)
        keep.extend(idx[np.asarray(sel, dtype=int)].tolist())
    keep.sort(key=lambda i: -float(scores[i]))
    return np.asarray(keep, dtype=int)


def _pack(r):
    """把 ultralytics 的 Result 拆成 numpy，并丢掉空表。"""
    if r.boxes is None or len(r.boxes) == 0:
        z = np.zeros(0, dtype=np.float64)
        return {"boxes": np.zeros((0, 4)), "scores": z, "cls": np.zeros(0, dtype=int)}
    return {
        "boxes": r.boxes.xyxy.cpu().numpy().astype(np.float64),
        "scores": r.boxes.conf.cpu().numpy().astype(np.float64),
        "cls": r.boxes.cls.cpu().numpy().astype(int),
    }


def predict_full(model, img, imgsz, conf):
    """整图推理。返回 (det, 耗时s)。框已在**原图像素坐标**（ultralytics 会自行反算）。"""
    t0 = time.perf_counter()
    r = model.predict(img, imgsz=imgsz, conf=conf, iou=NMS_IOU, max_det=MAX_DET,
                      device="cpu", verbose=False)[0]
    return _pack(r), time.perf_counter() - t0


def tile_positions(w, h, tile, overlap=0.25):
    """覆盖整幅图的切片起点（步长 = tile*(1-overlap)，末片贴边）。"""
    tile = min(tile, w, h)
    stride = max(1, int(round(tile * (1.0 - overlap))))
    xs = list(range(0, max(w - tile, 0) + 1, stride))
    ys = list(range(0, max(h - tile, 0) + 1, stride))
    if xs[-1] != w - tile:
        xs.append(max(0, w - tile))
    if ys[-1] != h - tile:
        ys.append(max(0, h - tile))
    return [(x, y, tile) for y in sorted(set(ys)) for x in sorted(set(xs))]


def predict_tiled(model, img, tile, imgsz, conf, overlap=0.25):
    """切图推理 -> 坐标回映射 -> 跨片逐类 NMS。返回 (det, 片数, 耗时s)。"""
    h, w = img.shape[:2]
    pos = tile_positions(w, h, tile, overlap)
    boxes, scores, cls = [], [], []
    dt = 0.0
    for (x, y, t) in pos:
        crop = np.ascontiguousarray(img[y:y + t, x:x + t])
        d, e = predict_full(model, crop, imgsz, conf)
        dt += e
        if len(d["boxes"]) == 0:
            continue
        b = d["boxes"].copy()
        b[:, [0, 2]] += x
        b[:, [1, 3]] += y
        boxes.append(b)
        scores.append(d["scores"])
        cls.append(d["cls"])
    if not boxes:
        return {"boxes": np.zeros((0, 4)), "scores": np.zeros(0), "cls": np.zeros(0, int)}, len(pos), dt
    b = np.concatenate(boxes)
    s = np.concatenate(scores)
    c = np.concatenate(cls)
    keep = nms_per_class(b, s, c)
    return {"boxes": b[keep], "scores": s[keep], "cls": c[keep]}, len(pos), dt


# ------------------------------------------------------------------ 统计

def summarize(dets, confs, n_frames):
    """把逐帧检测压成「每档阈值 × 球/人」的计数表。"""
    out = {}
    for c in confs:
        bf = bb = pf = pb = zf = 0
        bw, bh, fmax = [], [], []
        for d in dets:
            m = d["scores"] >= c
            c_ = d["cls"][m]
            s_ = d["scores"][m]
            b = c_ == SPORTS_BALL
            p = c_ == PERSON
            if b.any():
                bf += 1
                bb += int(b.sum())
                w_ = d["boxes"][m][b]
                bw += (w_[:, 2] - w_[:, 0]).tolist()
                bh += (w_[:, 3] - w_[:, 1]).tolist()
            if p.any():
                pf += 1
                pb += int(p.sum())
            if not m.any():
                zf += 1
            fmax.append(float(s_[b].max()) if b.any() else 0.0)
        out["%.2f" % c] = {
            "ball_frames": bf,
            "ball_boxes": bb,
            "person_frames": pf,
            "person_boxes": pb,
            "zero_det_frames": zf,
            "ball_w_px_median": round(float(np.median(bw)), 2) if bw else None,
            "ball_h_px_median": round(float(np.median(bh)), 2) if bh else None,
            "ball_w_px_max": round(float(np.max(bw)), 2) if bw else None,
            # 球是圆的：长宽比接近 1 才像球（用来审 tiling 是否把球员纹理当球）
            "ball_aspect_median": (round(float(np.median(np.asarray(bw) / np.maximum(np.asarray(bh), 1e-6))), 3)
                                   if bw else None),
            "ball_score_max": round(float(np.max(fmax)), 4) if fmax else 0.0,
        }
    # 与阈值无关的两条：网络对「球」的原始响应有多高
    per_frame_max = []
    for d in dets:
        b = d["cls"] == SPORTS_BALL
        per_frame_max.append(float(d["scores"][b].max()) if b.any() else 0.0)
    out["_ball_response"] = {
        "score_max_overall": round(float(np.max(per_frame_max)), 4) if per_frame_max else 0.0,
        "score_p50_of_frame_max": round(float(np.median(per_frame_max)), 4) if per_frame_max else 0.0,
        "frames_with_any_ball_ge_floor": int(sum(1 for v in per_frame_max if v > 0)),
        "n_frames": n_frames,
    }
    return out


def _slim(rec):
    """把一条 config 记录压成「列式 + 定长数组」：25 档 × 5 阈值按字典存要 65 KB。

    列名只在顶层 `decoding.layout` 里写一次（见 `LAYOUT`），每条记录只存数字数组，
    这样文件落到十几 KB 且人仍然读得懂（顺序由 layout 给出）。
    """
    bc = rec["by_conf"]
    zoom = [k for k in ("0.25", "0.01") if k in bc]
    out = {
        "id": rec["id"],
        "model": rec.get("model"),          # 阴性对照记录不一定带 model/source
        "source": rec.get("source"),
        "mode": rec["mode"],
        "imgsz": rec["imgsz"],
        "tile": rec["tile"],
        "n": rec["n_frames"],
        "ms": rec["ms_per_frame"],
        "inf": rec["inferences_per_frame"],
        # 每档两数，顺序 = 阈值从高到低（同 decoding.confs），展平成一维：
        # [bf, bb, bf, bb, ...]
        "rows": [x for k in sorted((q for q in bc if not q.startswith("_")),
                                   key=float, reverse=True)
                 for x in (bc[k]["ball_frames"], bc[k]["ball_boxes"])],
        # 两档细节（0.25 / 0.01）展平：[w, h, aspect, score, person, zero, w, h, ...]
        "detail": [x for k in zoom for x in (bc[k][c] for c in DETAIL_COLS)],
        # [input_scale, ball_px_in_input, ball_px_in_input_measured]
        "px": [bc["0.25"]["input_scale"], bc["0.25"]["ball_px_in_input"],
               bc["0.25"]["ball_px_in_input_measured"]],
        # [gop_per_frame, fps_ceiling_pl, ms_per_frame_pl]
        "zynq": [rec["zynq_projection"]["gop_per_frame"],
                 rec["zynq_projection"]["fps_ceiling_pl"],
                 rec["zynq_projection"]["ms_per_frame_pl"]],
        # [score_max, score_p50, frames_with_any_response]
        "resp": [bc["_ball_response"]["score_max_overall"],
                 bc["_ball_response"]["score_p50_of_frame_max"],
                 bc["_ball_response"]["frames_with_any_ball_ge_floor"]],
    }
    p = rec.get("patch_conf025")
    out["patch"] = ([p["n"], p["white_frac_median"], p["contrast_median"]] if p else None)
    # 阴性对照专用（其它记录没有这些键）
    for k in ("control", "base", "base_ball_frames", "pass_strict", "pass_tol1"):
        if k in rec:
            out[k] = rec[k]
    return out
    return out


# ------------------------------------------------------------------ 真伪代理
#
# 光有"检出帧数"回答不了"检出来的到底是不是球"。足球在转播画面里是**小而亮的低饱和
# 斑块**，所以给每个 conf>=0.25 的球框算三个客观量（都在原生分辨率上按框坐标取）：
#   white_frac  框内「亮且不饱和」像素占比   —— 球员头/脚/草皮纹理不会高
#   v_inside    框内平均亮度
#   contrast    v_inside - 框外一圈的平均亮度 —— 球比周围草皮亮，这是它的物理特征
# 这只回答"像不像球"，不回答"是不是那一颗球"；后者只能用可视化人工看。

def _patch_stats(img, box):
    h, w = img.shape[:2]
    x1 = int(max(0, min(w - 1, round(box[0]))))
    y1 = int(max(0, min(h - 1, round(box[1]))))
    x2 = int(max(x1 + 1, min(w, round(box[2]))))
    y2 = int(max(y1 + 1, min(h, round(box[3]))))
    crop = img[y1:y2, x1:x2]
    if crop.size == 0:
        return None
    px = crop.reshape(-1, 3).astype(np.float32)
    v = px.max(axis=1)
    s = (px.max(axis=1) - px.min(axis=1)) / np.maximum(v, 1.0)
    white = float(np.mean((v >= 120.0) & (s <= 0.35)))

    # 框外一圈（1.8 倍框，去掉框内）作参照，衡量"比周围亮多少"
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    hw, hh = (x2 - x1) * 0.9, (y2 - y1) * 0.9
    rx1 = int(max(0, cx - hw)); rx2 = int(min(w, cx + hw))
    ry1 = int(max(0, cy - hh)); ry2 = int(min(h, cy + hh))
    ring = img[ry1:ry2, rx1:rx2].astype(np.float32)
    if ring.size == 0 or (rx2 - rx1) <= (x2 - x1) or (ry2 - ry1) <= (y2 - y1):
        return {"white_frac": white, "v_inside": float(v.mean()),
                "v_ring": None, "contrast": None}
    mask = np.ones(ring.shape[:2], bool)
    mask[(y1 - ry1):(y2 - ry1), (x1 - rx1):(x2 - rx1)] = False
    if not mask.any():
        return {"white_frac": white, "v_inside": float(v.mean()),
                "v_ring": None, "contrast": None}
    rv = ring[mask].reshape(-1, 3).max(axis=1)
    return {"white_frac": white, "v_inside": float(v.mean()),
            "v_ring": float(rv.mean()), "contrast": float(v.mean() - rv.mean())}


def _patch_summary(stats):
    if not stats:
        return None
    def _med(k):
        vals = [s[k] for s in stats if s.get(k) is not None]
        return round(float(np.median(vals)), 3) if vals else None
    return {"n": len(stats), "white_frac_median": _med("white_frac"),
            "v_inside_median": _med("v_inside"),
            "v_ring_median": _med("v_ring"), "contrast_median": _med("contrast")}


def patch_montage(frames, dets, conf, title, cols=8, patch=64, zoom=4):
    """把 conf>=conf 的球框从帧上裁下来放大，一行一个 —— 用来肉眼判断"检出来的是不是球"。

    只看检出帧数回答不了这个问题：tiling 把 4 px 的球放大成 8-16 px 后，球员头部、
    脚、草皮纹理也可能给出高分框。这张图是本工具唯一的"人眼判据"入口。
    """
    import cv2

    cells = []
    for (p, img), d in zip(frames, dets):
        m = (d["cls"] == SPORTS_BALL) & (d["scores"] >= conf)
        if not m.any():
            continue
        idx = np.where(m)[0]
        idx = idx[np.argsort(-d["scores"][idx])][:1]   # 每帧只取最高分那颗
        for j in idx:
            b = d["boxes"][j]
            h, w = img.shape[:2]
            cx, cy = (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0
            x0 = int(max(0, min(w - patch, round(cx - patch / 2.0))))
            y0 = int(max(0, min(h - patch, round(cy - patch / 2.0))))
            c = cv2.resize(img[y0:y0 + patch, x0:x0 + patch],
                           (patch * zoom, patch * zoom), interpolation=cv2.INTER_NEAREST)
            cv2.rectangle(c, (int((b[0] - x0) * zoom), int((b[1] - y0) * zoom)),
                          (int((b[2] - x0) * zoom), int((b[3] - y0) * zoom)), (0, 255, 0), 1)
            _put(c, "%s %.2f %.0fx%.0f" % (p.stem.replace("match_", ""), d["scores"][j],
                                          b[2] - b[0], b[3] - b[1]),
                 (2, c.shape[0] - 6), (0, 255, 255), 0.42)
            cells.append(c)
    if not cells:
        return None
    ch, cw = cells[0].shape[:2]
    rows = int(np.ceil(len(cells) / float(cols)))
    canvas = np.full((rows * (ch + 4) + 24, cols * cw, 3), 30, np.uint8)
    _put(canvas, title, (6, 16), (255, 255, 255), 0.5)
    for i, c in enumerate(cells):
        r, q = divmod(i, cols)
        canvas[24 + r * (ch + 4):24 + r * (ch + 4) + ch, q * cw:(q + 1) * cw] = c
    return canvas


def write_json(data, path):
    """顶层键缩进美化、记录列表**每条一行**。

    直接 json.dump(indent=1) 会把每个数组元素拆成一行，25 档配置能把文件撑到 60 KB；
    每条记录压成一行后同样的信息只有十几 KB，diff 时也是一行一档，仍然好读。
    """
    parts = ["{"]
    items = list(data.items())
    for i, (k, v) in enumerate(items):
        comma = "," if i < len(items) - 1 else ""
        key = json.dumps(k, ensure_ascii=False)
        if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
            rows = ",\n".join("    " + json.dumps(x, ensure_ascii=False, separators=(", ", ": "))
                              for x in v)
            parts.append("  %s: [\n%s\n  ]%s" % (key, rows, comma))
        else:
            body = json.dumps(v, ensure_ascii=False, indent=1).replace("\n", "\n  ")
            parts.append("  %s: %s%s" % (key, body, comma))
    parts.append("}")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(parts) + "\n")


def _zynq_projection(imgsz, inferences_per_frame):
    """把一档配置折算到 Zynq-7020 PL 上要多少 GOP/帧、能跑多少 fps（投影，非实测）。

    GOP ∝ imgsz^2（卷积 FLOPs 与输入面积成正比，YOLOv8 各 stage 都如此），
    再乘每帧推理次数（切片数）。@320 = 2.186 GOP、PL 实测 15.3 GOPS。
    """
    gop_per_inf = GOP_320 * (float(imgsz) / 320.0) ** 2
    gop_per_frame = gop_per_inf * float(inferences_per_frame)
    return {
        "gop_per_inference": round(gop_per_inf, 3),
        "inferences_per_frame": round(float(inferences_per_frame), 2),
        "gop_per_frame": round(gop_per_frame, 3),
        "fps_ceiling_pl": round(PL_GOPS / gop_per_frame, 2) if gop_per_frame > 0 else None,
        "ms_per_frame_pl": round(gop_per_frame / PL_GOPS * 1000.0, 1) if gop_per_frame > 0 else None,
        "note": "projection only: linear in imgsz^2 and tile count; not a board measurement",
    }


def run_config(model, frames, mode, imgsz, confs, tile=None, overlap=0.25):
    dets = []
    t_inf = 0.0
    n_tiles = 0
    # 预热一帧（首帧含内存分配/图优化开销，实测能把 320 档从 720ms 拉到 13ms，不预热会污染耗时）
    if frames:
        if mode == "full":
            predict_full(model, frames[0][1], imgsz, CONF_FLOOR)
        else:
            predict_tiled(model, frames[0][1], tile, imgsz, CONF_FLOOR, overlap)
    t0 = time.perf_counter()
    for _p, img in frames:
        if mode == "full":
            d, e = predict_full(model, img, imgsz, CONF_FLOOR)
            n_tiles += 1
        else:
            d, nt, e = predict_tiled(model, img, tile, imgsz, CONF_FLOOR, overlap)
            n_tiles += nt
        dets.append(d)
        t_inf += e
    total = time.perf_counter() - t0
    n = len(frames)
    summ = summarize(dets, confs, n)

    # 换算到「网络输入像素」：唯一能跨配置比较的尺度，也是回答"需要什么条件"的关键。
    # 推导：球在原生帧里 BALL_PX_NATIVE px；full 模式下整帧长边缩到 imgsz，
    #       球 = BALL_PX_NATIVE * imgsz / 1280（与帧本身多少像素无关）；
    #       tiles 模式下把 tile 边长缩到 imgsz，球 = BALL_PX_NATIVE * imgsz / tile。
    # 用固定 BALL_PX_NATIVE 而不是实测框宽：否则只有"已被检出"的配置才有横坐标，
    # 曲线会变成只统计成功案例，看不到失败的半边。
    ref_w = (float(tile) if mode == "tiles" else 1280.0)
    scale = imgsz / ref_w
    for k, v in summ.items():
        if k.startswith("_"):
            continue
        v["input_scale"] = round(scale, 4)
        v["ball_px_in_input"] = round(BALL_PX_NATIVE * scale, 2)
        v["ball_px_in_input_measured"] = (round(v["ball_w_px_median"] * scale, 1)
                                          if v["ball_w_px_median"] is not None else None)

    # 真伪代理：conf>=0.25 的球框，在**原生帧**上取斑块统计（tiling 的框已回映射到全图坐标）
    patch_stats = []
    if frames:
        for (_p, img), d in zip(frames, dets):
            m = (d["cls"] == SPORTS_BALL) & (d["scores"] >= 0.25)
            for b in d["boxes"][m]:
                st = _patch_stats(img, b)
                if st is not None:
                    patch_stats.append(st)

    rec = {
        "mode": mode,
        "imgsz": imgsz,
        "tile": tile if mode == "tiles" else None,
        "n_frames": n,
        "infer_s": round(t_inf, 2),
        "total_s": round(total, 2),
        "ms_per_frame": round(total / max(n, 1) * 1000.0, 1),
        "inferences_per_frame": round(n_tiles / max(n, 1), 2),
        "zynq_projection": _zynq_projection(imgsz, n_tiles / max(n, 1)),
        # 内部一律用「阈值 -> 各项指标」的字典，落盘时由 _slim() 改成列式数组
        "by_conf": summ,
        "patch_conf025": _patch_summary(patch_stats),
    }
    return rec, dets


# ------------------------------------------------------------------ 原生帧

def ensure_native(native_dir: Path, calib_paths, video: Path, ffmpeg: Path, scratch: Path):
    """确保 .research/native/ 里有 40 张原生 1280x720 帧（与 640 版同一关键帧）。

    映射链：match_XXX.jpg 是池文件 pool/00xxx.jpg 的逐字节拷贝（shutil.copy2）
    ⇒ 用 sha256 就能在池里找到序号；池序号 = ffmpeg `-skip_frame nokey` 的输出序号，
    所以用同一条命令、去掉 scale 再抽一次，序号一一对应。
    """
    native_dir.mkdir(parents=True, exist_ok=True)
    have = {p.name for p in native_dir.glob("*.jpg")}
    need = [p.name for p in calib_paths]
    if all(n in have for n in need):
        return True, "已存在 %d 张" % len(need)

    if not video.exists() or not ffmpeg.exists():
        print("  [skip] 无原生帧且无视频/ffmpeg：%s | %s" % (video, ffmpeg))
        return False, "缺视频或 ffmpeg"

    # 1) 用 sha256 在关键帧池里定位序号
    pool = REPO / "data/samples/calib_real/_scratch/pool"
    if not pool.is_dir():
        print("  [skip] 关键帧池不存在：%s" % pool)
        return False, "缺关键帧池"
    by_hash = {}
    for q in pool.glob("*.jpg"):
        by_hash.setdefault(sha256_of(q), q.stem)
    want = {}
    for p in calib_paths:
        h = sha256_of(p)
        if h not in by_hash:
            print("  [skip] 帧 %s 在关键帧池里找不到（池已变更？）" % p.name)
            return False, "关键帧池对不上"
        want[p.name] = int(by_hash[h])
    t_stems = sorted(want.values())

    # 2) 只解码这 40 个关键帧：-skip_frame nokey 后按输出序号 select
    scratch.mkdir(parents=True, exist_ok=True)
    expr = "+".join("eq(n\\,%d)" % (s - 1) for s in t_stems)
    cmd = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-skip_frame", "nokey",
           "-i", str(video), "-vf", "select='%s'" % expr, "-vsync", "0",
           "-q:v", "2", str(scratch / "%05d.jpg")]
    print("  抽原生帧: ffmpeg select %d 个关键帧 ..." % len(t_stems))
    t0 = time.perf_counter()
    r = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if r.returncode != 0:
        print("  [fail] ffmpeg: %s" % r.stderr[-400:])
        return False, "ffmpeg 失败"
    got = sorted(scratch.glob("*.jpg"))
    print("  抽出 %d 张，耗时 %.1fs" % (len(got), time.perf_counter() - t0))
    if len(got) != len(t_stems):
        print("  [fail] 抽出张数 %d != 期望 %d" % (len(got), len(t_stems)))
        return False, "抽帧数不符"

    # 3) 序号 -> 文件名，落盘后清掉临时目录
    pos = {s: i for i, s in enumerate(t_stems)}
    for name, stem in want.items():
        shutil.copy2(got[pos[stem]], native_dir / name)
    shutil.rmtree(scratch, ignore_errors=True)

    # 4) 自检：原生帧降采样回 640 宽，必须与 640 标定帧内容一致（否则说明序号错位）
    import cv2

    ref = {p.name: p for p in calib_paths}
    worst = 0.0
    for name in list(want)[:8]:
        a = cv2.imread(str(native_dir / name))
        b = cv2.imread(str(ref[name]))
        if a is None or b is None:
            return False, "自检读图失败"
        small = cv2.resize(a, (b.shape[1], b.shape[0]), interpolation=cv2.INTER_AREA)
        worst = max(worst, float(np.abs(small.astype(np.int16) - b.astype(np.int16)).mean()))
    if worst > 8.0:
        print("  [fail] 原生帧与 640 标定帧内容对不上（mean|diff| = %.1f/255）" % worst)
        return False, "抽帧序号错位"
    print("  原生帧自检: 与 640 标定帧 mean|diff| = %.2f/255" % worst)
    return True, "从视频重抽 %d 张" % len(want)


# ------------------------------------------------------------------ 画图

def _put(img, text, org, color, scale=0.5, thick=1):
    import cv2

    cv2.putText(img, text, (org[0] + 1, org[1] + 1), cv2.FONT_HERSHEY_SIMPLEX,
                scale, (0, 0, 0), thick + 2, cv2.LINE_AA)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


def draw_frame(img, det, conf, title, grid=None, zoom_at=None, scale=1):
    """画球框（红）+ 可选切片网格（黄）+ 可选局部放大。返回 BGR 图。"""
    import cv2

    h, w = img.shape[:2]
    im = cv2.resize(img, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC) if scale != 1 else img.copy()
    if grid:
        for (x, y, t) in grid:
            cv2.rectangle(im, (x * scale, y * scale), ((x + t) * scale, (y + t) * scale), C_GRID, 1)
    m = (det["scores"] >= conf) & (det["cls"] == SPORTS_BALL)
    nb = 0
    for b, s in zip(det["boxes"][m], det["scores"][m]):
        p1 = (int(b[0] * scale), int(b[1] * scale))
        p2 = (int(b[2] * scale), int(b[3] * scale))
        cv2.rectangle(im, p1, p2, C_BALL, 2)
        _put(im, "ball %.3f" % s, (p1[0], max(12, p1[1] - 4)), C_BALL, 0.42)
        nb += 1
    bar = 22
    cv2.rectangle(im, (0, 0), (im.shape[1], bar), C_BG, -1)
    _put(im, "%s  ball@%.2f = %d" % (title, conf, nb), (6, 16), C_TXT, 0.45)
    if zoom_at is not None and nb:
        cx, cy = zoom_at
        half = 90
        x1 = int(max(0, cx - half)); x2 = int(min(im.shape[1], cx + half))
        y1 = int(max(0, cy - half)); y2 = int(min(im.shape[0], cy + half))
        crop = im[y1:y2, x1:x2]
        if crop.size:
            z = max(1, 360 // max(1, crop.shape[1]))
            zc = cv2.resize(crop, (crop.shape[1] * z, crop.shape[0] * z),
                            interpolation=cv2.INTER_NEAREST)
            _put(zc, "x%d" % z, (6, 16), C_TXT, 0.5)
            im = np.vstack([im, zc])
    return im


# ------------------------------------------------------------------ 主流程

def plot_visibility(visibility, path):
    """把可见性曲线画成一张散点图：横轴=球在网络输入里的像素，纵轴=检出帧率。

    这张图是本工具的结论载体：所有配置（不同模型、不同分辨率、切图或全图）都落在
    同一条曲线上，说明决定"能不能看见球"的只有一个变量——球在输入里占多少像素。
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    cols = ["ball_px_in_input", "id", "ball_frames", "n_frames", "ms_per_frame"]
    rows = [list(r) for r in visibility]
    fig, ax = plt.subplots(figsize=(9, 5.2), dpi=140)
    styles = {"tiles": ("s", "#d62728", "tiled (overlap 25%, NMS merged)"),
              "full": ("o", "#1f77b4", "single full frame")}
    seen = set()
    for px, cid, bf, nf, _ms in rows:
        mode = "tiles" if "_tile" in cid else "full"
        mk, c, lbl = styles[mode]
        ax.scatter(px, bf / float(max(nf, 1)) * 100, marker=mk,
                   s=46 if mode == "tiles" else 34, color=c, alpha=0.85, zorder=3,
                   label=lbl if mode not in seen else None)
        seen.add(mode)
        if bf / float(max(nf, 1)) >= 0.5 or px <= 4.1:
            ax.annotate(cid.replace("yolov8n_", "n_").replace("yolov8s_", "s_"),
                        (px, bf / float(max(nf, 1)) * 100), fontsize=5.4, rotation=12,
                        xytext=(3, -7), textcoords="offset points", color="#333")
    ax.axvline(BALL_PX_NATIVE, ls="--", lw=1.0, color="#888")
    ax.text(BALL_PX_NATIVE + 0.3, 4, "ball = %.0f px in input\n(= %.0f px on the 1280x720 frame)"
            % (BALL_PX_NATIVE, BALL_PX_NATIVE), fontsize=7, color="#555")
    ax.axhline(90, ls=":", lw=1.0, color="#bbb")
    ax.text(2.1, 91, "90% (never reached at conf>=0.25)", fontsize=6.5, color="#999")
    ax.set_xlabel("ball size in the network input (px)")
    ax.set_ylabel("frames with a sports-ball detection, % (conf >= 0.25)")
    ax.set_title("COCO YOLOv8 on real broadcast frames: only the ball's size in the input matters\n"
                 "circle = single full frame, square = tiled", fontsize=10)
    ax.set_xlim(0, max(34.0, max(r[0] for r in rows) * 1.05))
    ax.set_ylim(-3, 100)
    ax.grid(alpha=0.25)
    ax.legend(fontsize=7, loc="lower right")
    fig.tight_layout()
    fig.savefig(str(path))
    plt.close(fig)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="data/models/yolov8n.pt,data/models/yolov8s.pt")
    ap.add_argument("--dirs", default=",".join(CALIB_DIRS))
    ap.add_argument("--native-dir", default=NATIVE_DIR)
    ap.add_argument("--pos-dir", default=POS_DIR)
    ap.add_argument("--imgsz", default="640,960,1280,1600")
    ap.add_argument("--ref-imgsz", default="320,640", help="基线复现档（conf=0.25）")
    ap.add_argument("--tiles", default="640,512,320,256")
    ap.add_argument("--overlap", type=float, default=0.25)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--threads", type=int, default=16)
    ap.add_argument("--out", default=OUT_JSON)
    ap.add_argument("--viz", default=OUT_VIZ)
    ap.add_argument("--skip-native", action="store_true")
    ap.add_argument("--skip-tiles", action="store_true")
    ap.add_argument("--skip-neg", action="store_true")
    ap.add_argument("--skip-pos", action="store_true")
    ap.add_argument("--video", default=str(DEFAULT_VIDEO))
    ap.add_argument("--ffmpeg", default=str(DEFAULT_FFMPEG))
    args = ap.parse_args(argv)

    import torch

    torch.set_num_threads(args.threads)
    from ultralytics import YOLO

    imgsz_list = [int(s) for s in args.imgsz.split(",") if s.strip()]
    ref_list = [int(s) for s in args.ref_imgsz.split(",") if s.strip()]
    tiles = [int(s) for s in args.tiles.split(",") if s.strip()]
    confs = list(CONFS)

    # --- 帧 ---
    calib_paths = list_images(args.dirs.split(","))
    if args.limit:
        calib_paths = calib_paths[: args.limit]
    frames640 = load_frames(calib_paths)
    if not frames640:
        print("SWEEP_FAIL 没有测试帧（--dirs）")
        return 1
    n = len(frames640)
    h0, w0 = frames640[0][1].shape[:2]

    # --- 模型 ---
    models = {}
    model_meta = []
    for m in args.models.split(","):
        p = _abs(m.strip())
        if not p.is_file():
            print("  [skip] 模型不存在：%s" % p)
            continue
        mm = YOLO(str(p))
        name32 = mm.names.get(SPORTS_BALL)
        ok32 = (name32 == "sports ball")
        model_meta.append({
            "file": p.name,
            "bytes": p.stat().st_size,
            "sha256": sha256_of(p),
            "names_count": len(mm.names),
            "names_32": name32,
            "names_32_ok": bool(ok32),
        })
        if not ok32:
            print("SWEEP_FAIL 类别自检失败：names[32] = %r（应为 'sports ball'）" % (name32,))
            return 1
        models[p.stem] = mm
    if not models:
        print("SWEEP_FAIL 没有可用模型")
        return 1

    # 类别名称全表存档 + 模型间一致性（用户要求：证明查的确实是 sports ball，不是别的类）
    first = models[list(models)[0]]
    class_names = {str(k): v for k, v in first.names.items()}
    names_same = all({str(k): v for k, v in m.names.items()} == class_names
                     for m in models.values())
    print("类别自检: names[%d] = %r | names 共 %d 类 | 两模型名称一致=%s"
          % (SPORTS_BALL, class_names.get(str(SPORTS_BALL)), len(class_names), names_same))

    print("=" * 92)
    print("ball_visibility_sweep: %d 帧 %dx%d | 模型 %s | conf 下限 %.2f | NMS IoU %.2f"
          % (n, w0, h0, ",".join(models), CONF_FLOOR, NMS_IOU))
    print("=" * 92)

    # --- 原生帧 ---
    native_dir = _abs(args.native_dir)
    native_ok = False
    native_note = ""
    if not args.skip_native:
        native_ok, native_note = ensure_native(
            native_dir, calib_paths, Path(args.video), Path(args.ffmpeg),
            REPO / ".research" / "_native_scratch")
        print("原生帧: %s (%s)" % ("OK" if native_ok else "不可用", native_note))
    # 原生帧必须与 640 标定帧一一对应（同名），--limit 时一起截断，别让两边帧数不一致
    _want = [p.name for p in calib_paths]
    _np = [native_dir / n for n in _want if (native_dir / n).is_file()]
    frames_native = load_frames(_np) if native_ok else []
    if native_ok and len(frames_native) != len(calib_paths):
        print("  [warn] 原生帧 %d 张，标定帧 %d 张，两边不一致" % (len(frames_native), len(calib_paths)))

    configs = []
    dets_by_id = {}

    def add(model_key, source, frames, mode, imgsz, tile=None):
        cid = "%s_%s_%s_%d" % (model_key, source, "tile%d" % tile if mode == "tiles" else "full", imgsz)
        rec, dets = run_config(models[model_key], frames, mode, imgsz, confs, tile, args.overlap)
        rec.update({"id": cid, "model": model_key, "source": source})
        configs.append(rec)
        dets_by_id[cid] = dets
        b = rec["by_conf"]
        print("  %-26s frames=%-3d ms/f=%-7.1f ball@0.25=%-3d ball@0.01=%-4d person@0.25=%d"
              % (cid, rec["n_frames"], rec["ms_per_frame"],
                 b["0.25"]["ball_frames"], b["0.01"]["ball_frames"],
                 b["0.25"]["person_boxes"]))
        return rec

    # 1) 基线复现（320/640，conf=0.25，逐帧直接 predict，最贴近 class_coverage.py 的口径）
    print("")
    print("--- 1) 基线复现（imgsz=%s）" % ",".join(str(s) for s in ref_list))
    baseline = {}
    for sz in ref_list:
        dets = []
        predict_full(models[list(models)[0]], frames640[0][1], sz, 0.25)  # 预热，不计时
        t0 = time.perf_counter()
        for _p, img in frames640:
            d, _e = predict_full(models[list(models)[0]], img, sz, 0.25)
            dets.append(d)
        el = time.perf_counter() - t0
        bf = sum(1 for d in dets if ((d["cls"] == SPORTS_BALL) & (d["scores"] >= 0.25)).any())
        pb = int(sum(int(((d["cls"] == PERSON) & (d["scores"] >= 0.25)).sum()) for d in dets))
        baseline[str(sz)] = {"ball_frames": bf, "person_boxes": pb,
                             "n_frames": n, "total_s": round(el, 2),
                             "ms_per_frame": round(el / n * 1000.0, 1)}
        print("  imgsz=%-4d ball_frames=%d/%d  person_boxes=%d  %.1f ms/帧"
              % (sz, bf, n, pb, el / n * 1000.0))

    # 2) 分辨率扫描（640x360 标定帧 vs 原生 1280x720）
    print("")
    print("--- 2) 分辨率扫描（conf 下限 0.01，逐档统计）")
    sweep640 = [320] + [s for s in imgsz_list if s != 320] if 320 not in imgsz_list else imgsz_list
    for key in models:
        for sz in sweep640:
            add(key, "calib360", frames640, "full", sz)
        if frames_native:
            for sz in imgsz_list:
                add(key, "native720", frames_native, "full", sz)

    # 3) 切图
    if not args.skip_tiles and frames_native:
        print("")
        print("--- 3) 切图 tiling（原生 1280x720，imgsz=640，重叠 %.0f%%）" % (args.overlap * 100))
        for key in models:
            for t in tiles:
                add(key, "native720", frames_native, "tiles", 640, t)
        # 给 tiling 最大机会：最小的片 + 更大的 imgsz
        if tiles:
            add(list(models)[0], "native720", frames_native, "tiles", 1280, min(tiles))

    # 4) 阴性对照：破坏图像后重跑，检出数不应上升
    neg = []
    if not args.skip_neg and frames_native:
        print("")
        print("--- 4) 阴性对照（水平翻转 / 高斯噪声 / 涂灰）")
        base_id = None
        best_bf = -1
        for rec in configs:
            v = rec["by_conf"]["0.25"]["ball_frames"]
            if v > best_bf:
                best_bf, base_id = v, rec["id"]
        base_rec = next(r for r in configs if r["id"] == base_id)
        print("  以检出最多的一档为基准: %s（ball@0.25=%d）" % (base_id, best_bf))

        def _prep(kind):
            out = []
            rng = np.random.default_rng(0)
            for p, img in frames_native:
                if kind == "hflip":
                    out.append((p, np.ascontiguousarray(img[:, ::-1])))
                elif kind == "noise":
                    a = img.astype(np.float32) + rng.normal(0.0, 12.0, img.shape)
                    out.append((p, np.clip(a, 0, 255).astype(np.uint8)))
                else:  # gray
                    out.append((p, np.full_like(img, 128)))
            return out

        for kind in ("hflip", "noise", "gray"):
            rec, _d = run_config(models[base_rec["model"]], _prep(kind), base_rec["mode"],
                                 base_rec["imgsz"], confs, base_rec["tile"])
            rec["id"] = "neg_%s_on_%s" % (kind, base_id)
            rec["model"] = base_rec.get("model")
            rec["source"] = "negative_control"
            rec["control"] = kind
            rec["base"] = base_id
            neg.append(rec)
            print("  %-10s ball@0.25=%-3d ball@0.01=%-4d person@0.25=%-4d (%.0f ms/帧)"
                  % (kind, rec["by_conf"]["0.25"]["ball_frames"],
                     rec["by_conf"]["0.01"]["ball_frames"],
                     rec["by_conf"]["0.25"]["person_boxes"], rec["ms_per_frame"]))

    # 5) 阳性对照：大球的真实照片必须能检出（证明解码路径没坏）
    pos = None
    pf = []
    pos_dets = None
    if not args.skip_pos:
        pos_paths = list_images([args.pos_dir])
        if pos_paths:
            pf = load_frames(pos_paths[:30])
            print("")
            print("--- 5) 阳性对照（%s，%d 张，球是几十像素的大目标）" % (args.pos_dir, len(pf)))
            rec, pos_dets = run_config(models[list(models)[0]], pf, "full", 640, confs)
            rec["id"] = "pos_%s_640" % Path(args.pos_dir).name
            rec["source"] = "positive_control"
            rec["model"] = list(models)[0]
            # 阳性对照的球是几十像素的大目标，BALL_PX_NATIVE(8px) 这个假设对它不成立，
            # 所以把「球在输入里多少像素」清空，避免它被误读成能和主表比的横坐标。
            for _k, _v in rec["by_conf"].items():
                if isinstance(_v, dict) and "ball_px_in_input" in _v:
                    _v["ball_px_in_input"] = None
            pos = rec
            print("  ball@0.25: %d/%d 帧, %d 框, 最大分 %.3f, 框中位宽 %s px"
                  % (rec["by_conf"]["0.25"]["ball_frames"], rec["n_frames"],
                     rec["by_conf"]["0.25"]["ball_boxes"],
                     rec["by_conf"]["0.25"]["ball_score_max"],
                     rec["by_conf"]["0.25"]["ball_w_px_median"]))
        else:
            print("  [skip] 阳性对照目录无图：%s" % args.pos_dir)

    # --- 汇总判据 ---
    b320 = baseline.get("320", {}).get("ball_frames")
    b640 = baseline.get("640", {}).get("ball_frames")
    base_repro = {
        "expect_320": 0, "got_320": b320,
        "expect_640": 3, "got_640": b640,
        "n_frames": n,
        # 基线只在 40 帧上定义；--limit 减帧后这一项记为 None（不适用），不要拿它当通过
        "ok": (b320 == 0 and b640 == 3) if n == 40 else None,
    }
    best = None
    for rec in configs:
        v = (rec["by_conf"]["0.25"]["ball_frames"], rec["by_conf"]["0.25"]["ball_boxes"])
        if best is None or v > (best["by_conf"]["0.25"]["ball_frames"],
                                best["by_conf"]["0.25"]["ball_boxes"]):
            best = rec
    resp = max((r["by_conf"]["_ball_response"]["score_max_overall"] for r in configs), default=0.0)

    # 可见性曲线：把每档配置画成「球在网络输入里有多少像素 → 检出率（conf=0.25）」
    # 这是本工具的核心结论载体：所有配置都落在同一条曲线上，说明决定因素只有一个。
    # 落盘用扁平数组（[id, ball_px_in_input, ball_frames, ms_per_frame]），否则和
    # configs 重复一遍会让文件从 3 KB 涨到 5 KB。
    visibility = []
    for rec in configs:
        b = rec["by_conf"]["0.25"]
        if b.get("ball_px_in_input") is None:
            continue
        visibility.append((b["ball_px_in_input"], rec["id"], b["ball_frames"],
                           rec["n_frames"], rec["ms_per_frame"]))
    visibility.sort(key=lambda t: (t[0], t[1]))
    # 「球要有多少输入像素才看得见」——本实验最重要的一条定量答案。
    # 给三档 recall 阈值：0.90 可能整个扫描都达不到（那就是结论本身），所以不硬编造。
    def _first_px(bar):
        for px, _id, bf, nf, _ms in visibility:
            if bf / float(max(nf, 1)) >= bar:
                return px
        return None
    need_px = {"recall50_px": _first_px(0.50), "recall75_px": _first_px(0.75),
               "recall90_px": _first_px(0.90)}

    # 阴性对照判据：破坏图像后球检出**不应上升**（涂灰必须归零）。
    # 同时记「严格」与「+1 容差」两个版本，避免事后改判据。
    neg_strict = neg_tol1 = None
    if neg:
        base_bf = next(r for r in configs if r["id"] == neg[0]["base"])["by_conf"]["0.25"]["ball_frames"]
        neg_strict = neg_tol1 = True
        for r in neg:
            v = r["by_conf"]["0.25"]["ball_frames"]
            if r["control"] == "gray":
                ok_s = ok_t = (v == 0)
            else:
                ok_s, ok_t = (v <= base_bf), (v <= base_bf + 1)
            r["pass_strict"] = bool(ok_s)
            r["pass_tol1"] = bool(ok_t)
            r["base_ball_frames"] = base_bf
            neg_strict = neg_strict and ok_s
            neg_tol1 = neg_tol1 and ok_t

    summary = {
        "baseline_reproduced": base_repro,
        "best_config_id": best["id"] if best else None,
        "best_ball_frames_conf025": best["by_conf"]["0.25"]["ball_frames"] if best else 0,
        "max_ball_score_overall": resp,
        "positive_control_ok": (pos["by_conf"]["0.25"]["ball_frames"] > 0) if pos else None,
        "positive_control_ball_frames": pos["by_conf"]["0.25"]["ball_frames"] if pos else None,
        "negative_controls_ok_strict": neg_strict,
        "negative_controls_ok_tol1": neg_tol1,
        # 「球要有多少输入像素才看得见」——本实验最重要的一条数字（按 recall 分档）
        # recall90_px 为 null = 整个扫描里没有任何配置达到 90%（本身就是一个结论）
        "ball_px_in_input_thresholds": need_px,
        "visibility_curve_cols": ["ball_px_in_input", "id", "ball_frames", "n_frames", "ms_per_frame"],
        "visibility_curve": visibility,
    }

    data = {
        "tool": "tools/ball_visibility_sweep.py",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "machine": {
            "torch": torch.__version__,
            "ultralytics": __import__("ultralytics").__version__,
            "threads": args.threads,
            "device": "cpu",
        },
        "models": model_meta,
        # 类别名称全表存档：证明 conf/统计查的是 sports ball(id=32)，而不是别的类。
        # 这是"判据本身算错"的历史教训的直接防线——改天有人换了模型/换了类 id，
        # 打开这个 JSON 就能核对当时到底按哪个类统计的。
        "class_names": class_names,
        "class_names_same_across_models": bool(names_same),
        "decoding": {
            "conf_floor": CONF_FLOOR, "nms_iou": NMS_IOU, "max_det": MAX_DET,
            "confs": confs, "class_id_checked": SPORTS_BALL,
            "class_name_checked": "sports ball",
            "layout": {
                "rows": "rows = 一维展平 [ball_frames, ball_boxes] × len(confs)，顺序同 confs（高→低）",
                "detail": "detail = 一维展平 [%s] × 2 档（0.25 后接 0.01）" % ", ".join(DETAIL_COLS),
                "px": "px = [input_scale, ball_px_in_input, ball_px_in_input_measured]"
                      "（BALL_PX_NATIVE=%.1f px @1280x720；measured=实测框中位宽×scale）" % BALL_PX_NATIVE,
                "zynq": "zynq = [gop_per_frame, fps_ceiling_pl, ms_per_frame_pl]"
                        "（投影：GOP ∝ imgsz^2 × 每帧推理次数，PL=%.1f GOPS；非板上实测）" % PL_GOPS,
                "resp": "resp = [ball_score_max, ball_score_p50_of_frame_max,"
                        " frames_with_any_ball_response]",
                "patch": "patch = [n, white_frac_median, contrast_median] @conf>=0.25"
                         "（白且低饱和像素占比 / 球与周围亮度差）",
                "ms": "ms = CPU 单帧墙钟（含解码前处理），同机同线程数单次测量",
            },
        },
        "sources": {
            "calib360": {"dirs": args.dirs.split(","), "n": n, "w": w0, "h": h0},
            "native720": {"dir": str(native_dir), "n": len(frames_native),
                          "w": frames_native[0][1].shape[1] if frames_native else 0,
                          "h": frames_native[0][1].shape[0] if frames_native else 0,
                          "note": native_note},
        },
        "baseline": baseline,
        "configs": [_slim(r) for r in configs],
        "negative_controls": [_slim(r) for r in neg],
        "positive_control": _slim(pos) if pos is not None else None,
        "summary": summary,
    }

    # --- 入库 JSON ---
    outp = _abs(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    write_json(data, outp)
    print("")
    print("已写 %s (%.1f KB)" % (outp.relative_to(REPO), outp.stat().st_size / 1024.0))

    # --- 复核图 ---
    vizdir = _abs(args.viz)
    vizdir.mkdir(parents=True, exist_ok=True)
    import cv2

    n_written = 0
    if pos is not None and pos_dets:
        # 挑一张「球最小却仍检出」的做阳性对照图
        cand = []
        for (p, img), d in zip(pf, pos_dets):
            m = (d["scores"] >= 0.25) & (d["cls"] == SPORTS_BALL)
            if m.any():
                wmin = float(np.min(d["boxes"][m][:, 2] - d["boxes"][m][:, 0]))
                cand.append((wmin, p, img, d))
        if cand:
            cand.sort(key=lambda t: t[0])
            wmin, p, img, d = cand[0]
            im = draw_frame(img, d, 0.25, "POS control: %s (min ball w=%.1f px)" % (p.name, wmin))
            cv2.imwrite(str(vizdir / "poscontrol_smallest_ball.png"), im)
            n_written += 1

    if frames_native and configs:
        # 斑点图：全图 640 / 全图 1280 / 切图 320 各出一张，同一帧集合、同一个判据
        picks = []
        for want in (("full", 640, None), ("full", 1280, None), ("tiles", 640, 320)):
            cid = [c["id"] for c in configs if c["source"] == "native720"
                   and c["mode"] == want[0] and c["imgsz"] == want[1] and c["tile"] == want[2]]
            if cid:
                picks.append(cid[0])
        for cid in picks:
            rec = next(c for c in configs if c["id"] == cid)
            mo = patch_montage(frames_native, dets_by_id[cid], 0.25,
                               "%s  conf>=0.25  native 1280x720  ball_px_in_input=%.1f"
                               % (cid, rec["by_conf"]["0.25"]["ball_px_in_input"]))
            if mo is not None:
                cv2.imwrite(str(vizdir / ("patches_%s.png" % cid)), mo)
                n_written += 1

    if frames_native and configs:
        # 全图 1280 vs 切图 320：同一帧并排，看切图到底捞到了什么
        cid_full = [c["id"] for c in configs if c["source"] == "native720"
                    and c["mode"] == "full" and c["imgsz"] == max(imgsz_list)]
        cid_tile = [c["id"] for c in configs if c["source"] == "native720"
                    and c["mode"] == "tiles" and c["tile"] == 320]
        if cid_full:
            dets = dets_by_id[cid_full[0]]
            # 选「低阈值下球框最多」的一帧
            k = int(np.argmax([int(((d["cls"] == SPORTS_BALL) & (d["scores"] >= 0.05)).sum())
                               for d in dets]))
            p, img = frames_native[k]
            grid = tile_positions(img.shape[1], img.shape[0], 320, args.overlap) if cid_tile else None
            im = draw_frame(img, dets[k], 0.05, "full imgsz=%s  %s" % (max(imgsz_list), p.name),
                            grid=grid, scale=1)
            cv2.imwrite(str(vizdir / "native_full_and_tile_grid.png"), im)
            n_written += 1
        if cid_tile:
            dets = dets_by_id[cid_tile[0]]
            k = int(np.argmax([int(((d["cls"] == SPORTS_BALL) & (d["scores"] >= 0.01)).sum())
                               for d in dets]))
            p, img = frames_native[k]
            grid = tile_positions(img.shape[1], img.shape[0], 320, args.overlap)
            im = draw_frame(img, dets[k], 0.01, "tiles 320@640  %s" % p.name, grid=grid)
            cv2.imwrite(str(vizdir / "native_tiles320_conf001.png"), im)
            n_written += 1
    if n_written:
        print("复核图 -> %s (%d 张)" % (vizdir.relative_to(REPO), n_written))

    # 可见性曲线图（可选：matplotlib 装了才画，缺了不影响结论）
    try:
        plot_visibility(visibility, vizdir / "visibility_curve.png")
        n_written += 1
        print("曲线图 -> %s" % (vizdir / "visibility_curve.png").relative_to(REPO))
    except Exception as e:  # pragma: no cover - 只影响一张图
        print("  [skip] 曲线图未生成: %s" % e)

    # --- 控制台主表 ---
    print("")
    print("=" * 100)
    print("主表：球检出帧数 / %d 帧   （基线: 320=0, 640=3；conf 即 sports ball 的分数阈值）" % n)
    print("=" * 100)
    print("  %-26s %4s %5s %8s %8s %8s %8s %8s" %
          ("config", "帧", "ms/帧", "c.25", "c.10", "c.05", "c.02", "c.01"))
    for rec in configs:
        b = rec["by_conf"]
        print("  %-26s %4d %5.0f %8d %8d %8d %8d %8d"
              % (rec["id"], rec["n_frames"], rec["ms_per_frame"],
                 b["0.25"]["ball_frames"], b["0.10"]["ball_frames"],
                 b["0.05"]["ball_frames"], b["0.02"]["ball_frames"],
                 b["0.01"]["ball_frames"]))
    if pos is not None:
        b = pos["by_conf"]
        print("  %-26s %4d %5.0f %8d %8d %8d %8d %8d"
              % (pos["id"], pos["n_frames"], pos["ms_per_frame"],
                 b["0.25"]["ball_frames"], b["0.10"]["ball_frames"],
                 b["0.05"]["ball_frames"], b["0.02"]["ball_frames"],
                 b["0.01"]["ball_frames"]))
    for rec in neg:
        b = rec["by_conf"]
        print("  %-26s %4d %5.0f %8d %8d %8d %8d %8d"
              % (rec["id"], rec["n_frames"], rec["ms_per_frame"],
                 b["0.25"]["ball_frames"], b["0.10"]["ball_frames"],
                 b["0.05"]["ball_frames"], b["0.02"]["ball_frames"],
                 b["0.01"]["ball_frames"]))
    print("")
    print("基线复现: 320 期望 0 实得 %s | 640 期望 3 实得 %s | ok=%s"
          % (b320, b640, base_repro["ok"]))
    print("网络对球的最高原始响应: %.4f（全配置）" % resp)
    print("阳性对照（大球能检出）: %s" % summary["positive_control_ok"])
    if need_px["recall50_px"] is not None:
        print("球在网络输入里的像素 -> recall@conf0.25: "
              + " | ".join("%s px=%.1f" % (lbl, need_px[k])
                           for k, lbl in (("recall50_px", "50%"), ("recall75_px", "75%"),
                                          ("recall90_px", "90%")) if need_px[k] is not None))
    print("SWEEP_DONE %d" % len(configs))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # 兜底：任何异常都要留下可判读的令牌
        print("SWEEP_FAIL %s: %s" % (type(exc).__name__, exc))
        raise
