"""第三批：**严格重复标注** —— 同一批框、同一张图，隔一段时间再标一遍。

要回答的问题
------------
前两批里有一批框在两批中出现过（`docs/hi_layer_color_gate.md` §6.4 记作「37 个重叠」），
但两批的图来自不同帧、不同裁剪，所以那组「一致性」数字既混进了「小图本身有歧义」，
也混进了「两次看的是不同东西」。kappa 0.226 因此无法解释。

本批把这两件事拆开：**同一帧、同一个框、同一张裁剪图**，重新出一遍，让同一个人隔一段时间再标。
这样左右两边的唯一差别就只剩「人」和「时间」。

★ 为什么键是 (clip, frame, conf) 而不是 box_idx
-----------------------------------------------
`box_idx` 不是框的身份，是**框在该次检测列表里的位置**，而检测列表按 conf 降序、且列表长度随
`conf` 门限变化：

  · 第一批按 `conf>=0.08` 跑检测，第二批按 `conf>=0.25` 跑（`build_hi_unknown` 用 `HI_CONF`）；
  · 同一个人在高门限那次的列表里位置会前移 —— 于是两批的 `box_idx=0` 经常**不是同一个框**。

实测（本脚本 `--audit` 可复现）：
  · `labels.csv` 里 (clip,frame,box_idx) **73 行只有 61 个唯一键**，12 个键重复；
  · `labels2.csv` 里 159 行只有 115 个唯一键，44 个键重复；
  · 两批按唯一键相交 20 个，其中**只有 1 个真的是同一个框**（conf 与 h_px 都对得上）。
  · 文档 §6.4 记的「37 个」是把多值键做了笛卡尔对齐（73 行里有 29 行落在相交键上），
    并非同一批框的两次标注。⇒ **前两批不构成重复标注，kappa 0.226 不能用来衡量标注噪声。**

所以本批用 `(clip, frame, conf)` 定位框：在同一帧上用 `conf==0.08` 重跑检测，
按 conf 精确匹配到那一个框（conf 由模型给出，4 位小数在整段视频里可区分不同的人）。
`box_idx` 仍然写进 CSV（网页要显示、人也看得懂），但**只作参考**，不作为身份。

用法
----
    # 1) 生成批次 3（60 框，四层各 15）
    & D:\\python\\python.exe tools\\make_player_labels.py --repeat-3

    # 2) 先看清 / 后看图：把 ctx 列关掉再标（本批默认 ctx 列就留空）
    #    浏览器打开 data/annot/labels3.csv 对应的网页（tools/label_web.py 支持 ?batch=3）

    # 3) 标完后算一致性
    & D:\\python\\python.exe tools\\repeat_agreement.py

★ 本脚本**不产生任何 label**。labels3.csv 的 label 列恒为空 —— 标注只能由人做。

产物
----
    data/annot/frames3/b3_<clip>_f<NNNN>_sb<NN>.jpg   与批1/批2 同样式的放大图（中性文件名）
    data/annot/labels3.csv                             待填：stratum,...,source,label,note
    data/annot/MANIFEST3.json                          来源映射（哪一行来自哪一批哪一条）+ 自检结论

★ 网页不能泄露「这是重复框」：labels3.csv 里**没有任何**指向原批次的列（无 source_* / 无
  原 id），来源映射只写在 MANIFEST3.json 里，网页不读它。`stratum` 沿用原框的层名，
  因为第三批本身就是按层分层抽的，这是采样设计而不是「重复」线索。

判据令牌
    REPEAT3_PACK_OK <n> <hi> <lo_kept> <lo_drop> <hi_unk>
    REPEAT3_AUDIT <n_unique>b1 <n_unique>b2 <n_overlap_keys> <n_same_box>
    REPEAT3_FAIL <reason>
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import csv
import importlib.util
import json
import time
from pathlib import Path

import cv2

REPO = Path(__file__).resolve().parents[1]
ANNOT = REPO / "data" / "annot"
CLIPS = REPO / "data" / "samples" / "incidents"

BATCH3_FRAMES = ANNOT / "frames3"
BATCH3_CSV = ANNOT / "labels3.csv"
BATCH3_MANIFEST = ANNOT / "MANIFEST3.json"
BATCH3_SCORES = ANNOT / "scores3.json"

BATCH1_CSV = ANNOT / "labels.csv"
BATCH2_CSV = ANNOT / "labels2.csv"

# 批次3 的四层：前三层来自批1，第四层来自批2（hy 层颜色失败）
B3_STRATA = ("hi", "lo_kept", "lo_drop", "hi_unk")
B3_PER_STRATUM = 15
B3_TOTAL = B3_PER_STRATUM * len(B3_STRATA)

# 定位框用的检测门限：必须与出图那次一致，否则 conf 匹配不上
DET_CONF = 0.08
DET_IOU = 0.45
DET_IMGSZ = 640


def _rows(path):
    if not Path(path).exists():
        return []
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _read_frames(clip_path, want):
    """顺序读到 max(want)，返回 {frame_no: ndarray}。

    ★ 必须顺序读：mp4 重新打开后只读前几帧（不读到底）拿到的帧字节与顺序读不一致，
      而出一张和原批次像素级相同的图，正是「严格重复」的全部意义。
    """
    got = {}
    if not want:
        return got
    cap = cv2.VideoCapture(str(clip_path))
    fn = -1
    last = max(want)
    while fn < last:
        ok, frame = cap.read()
        if not ok:
            break
        fn += 1
        if fn in want:
            got[fn] = frame.copy()
    cap.release()
    return got


def _detect(model, frame, conf):
    r = model.predict(frame, classes=[0], conf=conf, iou=DET_IOU,
                      imgsz=DET_IMGSZ, verbose=False)
    boxes = r[0].boxes if r and r[0].boxes is not None else None
    if boxes is None or len(boxes) == 0:
        return []
    return [([float(v) for v in bb], float(c))
            for bb, c in zip(boxes.xyxy.cpu().numpy(), boxes.conf.cpu().numpy())]


def _match_box(dets, conf, h_px, tol=1e-3):
    """在同一帧的检测列表里找回那个框，返回 (idx, box) 或 (None, None)。

    为什么不能只用 conf：conf 只存 4 位小数，同一帧里 0.713 与 0.7123
    会撞成同一个值（实测 inc_03 f0 就有这一对，撞了会张冠李戴到另一个框）。
    为什么不能用 box_idx 消歧：`box_idx` 是 `_crop_with_context` 循环里
    `enumerate(items)` 的下标，而 `items` 是**该 stratum 在该帧内的框**，
    不是整帧检测列表的下标（`make_player_labels.py` 第 200-250 行）——
    inc_06 f0 的 lo_drop 框 conf=0.1870 在整帧列表里排在 16，box_idx 却是 0。
    所以：先按 conf 找候选，再用原批次记下的 h_px 定人。
    """
    cand = [(i, bb) for i, (bb, cf) in enumerate(dets) if abs(cf - conf) <= tol]
    if not cand:
        return None, None
    if len(cand) == 1:
        return cand[0]
    same_h = [(i, bb) for i, bb in cand
              if abs(float(bb[3]) - float(bb[1]) - h_px) <= 0.6]
    if len(same_h) == 1:
        return same_h[0]
    return None, None          # 有歧义就报出来，绝不猜


def _crop_with_context(frame, bb, pad, cell):
    """和批1/批2 完全相同的裁剪：外扩 pad、画绿框、缩放到宽 cell。"""
    H, W = frame.shape[:2]
    x1, y1, x2, y2 = [float(v) for v in bb]
    w, h = x2 - x1, y2 - y1
    px, py = w * pad, h * pad
    cx1 = int(max(0, x1 - px)); cx2 = int(min(W, x2 + px))
    cy1 = int(max(0, y1 - py)); cy2 = int(min(H, y2 + py))
    if cx2 - cx1 < 8 or cy2 - cy1 < 8:
        return None
    crop = frame[cy1:cy2, cx1:cx2].copy()
    bx1, by1 = int(x1 - cx1), int(y1 - cy1)
    bx2, by2 = int(x2 - cx1), int(y2 - cy1)
    cv2.rectangle(crop, (bx1, by1), (bx2, by2), (0, 255, 0), 1)
    sc = cell / float(crop.shape[1])
    return cv2.resize(crop, (cell, max(1, int(crop.shape[0] * sc))),
                      interpolation=cv2.INTER_CUBIC)


def audit_keys():
    """统计两批的键唯一性，并把「重叠」拆成「同一个框」与「只是同号」两种。

    这是 §6.4 那个 kappa 能不能用的前提。返回 dict，供 --audit 打印与 MANIFEST3 记录。
    """
    from collections import Counter

    b1 = _rows(BATCH1_CSV)
    b2 = _rows(BATCH2_CSV)
    k1 = [(r["clip"], r["frame"], r["box_idx"]) for r in b1]
    k2 = [(r["clip"], r["frame"], r["box_idx"]) for r in b2]
    c1, c2 = Counter(k1), Counter(k2)
    shared = set(c1) & set(c2)

    # 同一个框的判据：(clip, frame, conf, h_px) 全对得上
    def box_key(r):
        return (r["clip"], r["frame"], round(float(r["conf"]), 4),
                round(float(r["h_px"]), 1))
    sb1 = set(box_key(r) for r in b1)
    sb2 = set(box_key(r) for r in b2)
    same_box = sb1 & sb2

    # 只按 conf（不带 h_px）会误判几个？给出这个差，防止别人复算时口径不一致
    cf_key = lambda r: (r["clip"], r["frame"], round(float(r["conf"]), 4))
    conf_only = set(cf_key(r) for r in b1) & set(cf_key(r) for r in b2)

    # 行级笛卡尔对齐会放大成多少（复现 §6.4 的 37 的来历）
    cart = sum(c1[k] * c2[k] for k in shared)

    return {
        "b1_rows": len(b1), "b1_unique_keys": len(set(k1)),
        "b1_dup_keys": len(k1) - len(set(k1)),
        "b2_rows": len(b2), "b2_unique_keys": len(set(k2)),
        "b2_dup_keys": len(k2) - len(set(k2)),
        "n_shared_keys": len(shared),
        "n_cartesian_pairs": cart,
        "n_same_box": len(same_box),
        "n_same_box_conf_only": len(conf_only),
        "note": ("box_idx 不是框的身份：它是 make_player_labels.py 里 "
                 "`enumerate(items)` 的下标，items = 该 stratum 在该帧内的框；"
                 "而两批又用了不同的 conf 门限（批1=0.08，批2=0.25），"
                 "同一个 box_idx 常常指向不同的框（实测 inc_06 f0 的 lo_drop 框 "
                 "conf=0.1870 在整帧检测列表里排第 16，box_idx 却是 0）。"
                 "因此 §6.4 的「37 个重叠」是多值键的笛卡尔对齐再叠加错配，"
                 "前两批**不构成**重复标注，那个 kappa 不能当作标注噪声的下界。"
                 "按 (clip,frame,conf,h_px) 严格对齐，真正同一个框只有 "
                 "%d 个（n_same_box）。" % len(same_box)),
    }


def build_repeat3(args):
    """生成批次 3：同一批框、同一张图，重新出图。"""
    from ultralytics import YOLO
    # 与批1/批2 保持同一套裁剪参数，否则「同一张图」不成立
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location(
        "_mpl", str(REPO / "tools" / "make_player_labels.py"))
    mpl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mpl)
    pad, cell = mpl.PAD, mpl.CELL

    b1 = _rows(BATCH1_CSV)
    b2 = _rows(BATCH2_CSV)
    if not b1 or not b2:
        print("REPEAT3_FAIL missing_source_batches")
        return 2

    # ---- 候选池：全量，去重后按层 ----
    pools = {s: [] for s in B3_STRATA}
    seen = set()

    def add(row, stratum, origin):
        k = (row["clip"], row["frame"], round(float(row["conf"]), 4))
        if k in seen:
            return
        seen.add(k)
        pools[stratum].append({
            "clip": row["clip"], "frame": int(row["frame"]),
            "conf": round(float(row["conf"]), 4),
            "h_px": float(row["h_px"]),
            "stratum": stratum, "src_label": row["label"],
            "origin": origin, "origin_id": row["id"],
            "origin_img": row["img"],
            "origin_box_idx": int(row["box_idx"]),
            "origin_h_px": float(row["h_px"]),
        })

    for r in b1:
        if r["stratum"] in ("hi", "lo_kept", "lo_drop"):
            add(r, r["stratum"], "batch1")
    for r in b2:
        add(r, "hi_unk", "batch2")

    for s in B3_STRATA:
        print("  pool %-8s %d" % (s, len(pools[s])), flush=True)

    # ---- 分层抽样：固定种子，可复现；层内按 clip 轮转避免全来自同一段 ----
    rng = _rng(args.seed)
    picked = []
    for s in B3_STRATA:
        pool = list(pools[s])
        if not pool:
            print("REPEAT3_FAIL empty_pool %s" % s)
            return 2
        # 层内先按 (clip,frame,conf) 排序再洗牌，保证不同机器上同种子结果一致
        pool.sort(key=lambda x: (x["clip"], x["frame"], x["conf"]))
        _shuffle(pool, rng)
        by_clip = {}
        for it in pool:
            by_clip.setdefault(it["clip"], []).append(it)
        take = []
        order = sorted(by_clip)
        i = 0
        while len(take) < min(args.per_stratum, len(pool)) and any(by_clip.values()):
            c = order[i % len(order)]
            i += 1
            if by_clip.get(c):
                take.append(by_clip[c].pop(0))
        picked.extend(take)

    # ---- 出图：按 clip 分组、顺序读到目标帧，用 conf 精确定位框 ----
    model = YOLO(str(mpl.MODEL))
    BATCH3_FRAMES.mkdir(parents=True, exist_ok=True)
    ANNOT.mkdir(parents=True, exist_ok=True)

    by_clip = {}
    for it in picked:
        by_clip.setdefault(it["clip"], {}).setdefault(it["frame"], []).append(it)

    rows = []
    # ★ 来源按「身份键」记录，不能按下标：picked 里可能有被跳过的框，
    #   一旦按下标对齐，provenance 就会整体错位（踩过：h_px 全部串行错开）。
    prov = {}
    unresolved = []
    for clip in sorted(by_clip):
        want = set(by_clip[clip])
        frames = _read_frames(CLIPS / clip, want)
        dets_cache = {}
        for fn in sorted(by_clip[clip]):
            frame = frames.get(fn)
            if frame is None:
                for it in by_clip[clip][fn]:
                    unresolved.append((it, "frame_missing"))
                continue
            if fn not in dets_cache:
                dets_cache[fn] = _detect(model, frame, DET_CONF)
            dets = dets_cache[fn]
            # 同一帧里多个框：按 conf 降序给 box_idx（与生成时一致），便于人复核
            order = {round(cf, 4): i for i, (_bb, cf) in enumerate(dets)}
            for it in sorted(by_clip[clip][fn], key=lambda x: -x["conf"]):
                bi, bb = _match_box(dets, it["conf"], it["origin_h_px"])
                if bb is None:
                    # 检测没复现：宁可不给图，也不能拿「差不多的框」冒充同一个框
                    unresolved.append((it, "detection_not_reproduced"))
                    continue
                crop = _crop_with_context(frame, bb, pad, cell)
                if crop is None:
                    unresolved.append((it, "crop_too_small"))
                    continue
                # 框高必须对得上原批次记的 h_px，否则说明匹配到了别的框
                h_now = float(bb[3]) - float(bb[1])
                if abs(h_now - it["origin_h_px"]) > 0.6:
                    unresolved.append((it, "height_mismatch %.1f!=%.1f"
                                       % (h_now, it["origin_h_px"])))
                    continue
                rid = len(rows) + 1
                # 中性文件名：b3_<clip>_f<NNNN>_sb<NN>，不暴露来源批次/层
                stem = "b3_%s_f%04d_sb%02d" % (Path(clip).stem, fn, rid)
                p = BATCH3_FRAMES / (stem + ".jpg")
                cv2.imwrite(str(p), crop, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
                rows.append({
                    "id": rid,
                    "stratum": it["stratum"],
                    "clip": clip,
                    "frame": fn,
                    "box_idx": order.get(it["conf"], bi),
                    "conf": "%.4f" % it["conf"],
                    "h_px": "%.1f" % (float(bb[3]) - float(bb[1])),
                    "color_role": "",
                    "color_dist": "",
                    "img": str(p.relative_to(REPO)).replace("\\", "/"),
                    "label": "",          # ← 人来填；本脚本永不写
                    "note": "",
                })
                prov[rid] = it

    if unresolved:
        for it, why in unresolved:
            print("  UNRESOLVED %s %s f%d conf=%.4f -> %s"
                  % (it["stratum"], Path(it["clip"]).stem, it["frame"], it["conf"], why),
                  flush=True)
        if len(rows) < B3_TOTAL:
            print("REPEAT3_FAIL unresolved_boxes %d (要 %d 框，只出成 %d)"
                  % (len(unresolved), B3_TOTAL, len(rows)))
            return 2

    # ---- 写 CSV（label 全空）与 MANIFEST（来源映射只在这里）----
    fields = ["id", "stratum", "clip", "frame", "box_idx", "conf", "h_px",
              "color_role", "color_dist", "img", "label", "note"]
    with open(BATCH3_CSV, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    counts = {s: sum(1 for r in rows if r["stratum"] == s) for s in B3_STRATA}
    au = audit_keys()
    manifest = {
        "tool": "tools/make_player_labels.py --repeat-3",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "purpose": ("严格重复标注：同一批框、同一张裁剪图，隔一段时间再标一遍，"
                    "把「标注噪声」与「小图本身有歧义」分开"),
        "label_scheme": {"P": "球员（含守门员）",
                         "N": "非球员（裁判/教练/替补/观众/球童/摄像师）",
                         "U": "看不清"},
        "sampling": {"per_stratum": args.per_stratum, "seed": args.seed,
                     "det_conf": DET_CONF, "det_iou": DET_IOU,
                     "det_imgsz": DET_IMGSZ, "pad": pad, "cell": cell},
        "counts": counts,
        "n_boxes": len(rows),
        "n_labeled": 0,
        "identity_key": "(clip, frame, conf) —— box_idx 只是该次检测列表里的位置，"
                        "随 conf 门限变化，不作身份",
        "audit_of_previous_overlap": au,
        # ★ 来源映射：只在 MANIFEST 里。labels3.csv 不写任何指向原批次的列。
        "provenance": [
            {"b3_id": r["id"],
             "origin": prov[r["id"]]["origin"],
             "origin_id": prov[r["id"]]["origin_id"],
             "origin_img": prov[r["id"]]["origin_img"],
             "origin_label": prov[r["id"]]["src_label"],
             "stratum": prov[r["id"]]["stratum"],
             "clip": prov[r["id"]]["clip"], "frame": prov[r["id"]]["frame"],
             "conf": prov[r["id"]]["conf"]}
            for r in rows
        ],
        "notes": [
            "labels3.csv 的 label 列必须由人填；本脚本不会写任何 label。",
            "网页（tools/label_web.py?batch=3）不读 MANIFEST3.json，"
            "所以标注者看不到「这是重复框」。",
            "stratum 列沿用原框的层名，因为本批就是按层分层抽的；"
            "它是采样设计，不构成「重复」线索。",
            "出图与批1/批2 同参数（pad=0.25, cell=320, imgsz=640, conf=0.08），"
            "同一 (clip,frame,conf) 得到的是像素级同一张图。",
        ],
    }
    BATCH3_MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                               encoding="utf-8", newline="\n")

    print("REPEAT3_AUDIT %d %d %d %d" % (au["b1_unique_keys"], au["b2_unique_keys"],
                                          au["n_shared_keys"], au["n_same_box"]))
    # ---- 自检：出图必须与原图逐像素相同，否则「严格重复」不成立 ----
    ok_pix, bad_pix = verify_pixels(rows, prov)
    manifest["pixel_check"] = {"identical": ok_pix, "differ": len(bad_pix),
                               "detail": bad_pix[:10]}
    BATCH3_MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                               encoding="utf-8", newline="\n")
    print("REPEAT3_PIXELS %d/%d identical" % (ok_pix, ok_pix + len(bad_pix)))
    if bad_pix:
        for b in bad_pix[:10]:
            print("  PIXEL_DIFF %s" % (b,))
        print("REPEAT3_FAIL pixel_mismatch %d" % len(bad_pix))
        return 2
    print("LABELS_CSV %s" % BATCH3_CSV)
    print("FRAMES_DIR %s" % BATCH3_FRAMES)
    print("REPEAT3_PACK_OK %d %d %d %d %d"
          % (len(rows), counts["hi"], counts["lo_kept"],
             counts["lo_drop"], counts["hi_unk"]))
    return 0


def verify_pixels(rows, prov):
    """逐张比对：新图 vs 原批次的图。返回 (一致数, 不一致明细)。"""
    import numpy as np
    same, bad = 0, []
    for r in rows:
        it = prov.get(r["id"])
        if not it or not it.get("origin_img"):
            continue
        new = cv2.imread(str(REPO / r["img"]))
        old = cv2.imread(str(REPO / it["origin_img"]))
        if new is None or old is None:
            bad.append((r["id"], "unreadable")); continue
        if new.shape != old.shape:
            bad.append((r["id"], "shape", list(new.shape), list(old.shape))); continue
        if int(np.abs(new.astype("int16") - old.astype("int16")).max()) == 0:
            same += 1
        else:
            bad.append((r["id"], "pixels"))
    return same, bad


# --------------------------------------------------------------------------
# 最小可复现的伪随机（不依赖 random 的版本实现，保证跨 Python 版本同种子同结果）
# --------------------------------------------------------------------------

def _rng(seed):
    return {"s": (seed * 6364136223846793005 + 1442695040888963407) & ((1 << 64) - 1)}


def _next(st):
    st["s"] = (st["s"] * 6364136223846793005 + 1442695040888963407) & ((1 << 64) - 1)
    return (st["s"] >> 33) & 0x7FFFFFFF


def _shuffle(items, st):
    for i in range(len(items) - 1, 0, -1):
        j = _next(st) % (i + 1)
        items[i], items[j] = items[j], items[i]


def audit_only():
    au = audit_keys()
    print("REPEAT3_AUDIT %d %d %d %d"
          % (au["b1_unique_keys"], au["b2_unique_keys"],
             au["n_shared_keys"], au["n_same_box"]))
    print(json.dumps(au, ensure_ascii=False, indent=1))
    return 0


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="批次3：严格重复标注包")
    ap.add_argument("--per-stratum", type=int, default=B3_PER_STRATUM)
    ap.add_argument("--seed", type=int, default=20261020)
    ap.add_argument("--audit", action="store_true",
                    help="只审计前两批的键唯一性，不出图")
    args = ap.parse_args(argv)
    if args.audit:
        return audit_only()
    return build_repeat3(args)


if __name__ == "__main__":
    raise SystemExit(main())