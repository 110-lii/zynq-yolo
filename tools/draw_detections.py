"""把「INT8 整网」与「float32 参考」的检测框画在同一张图上，人眼复核。

为什么要有这个工具
------------------
数字（cosine 0.9996 / 框角差 1.5 px / 框数 114 vs 116）能说明"接近"，
但**看不出错在哪**。真要看懂一次漏检，必须把两套框叠在同一张图上：
是整块区域偏了？是某个球员被两个人框抢了？还是阈值附近多出来一个？

画法（颜色即结论）
------------------
    绿实线 = float32 参考（onnxruntime）
    红实线 = INT8 被测（本仓库的 numpy 定点实现）
    品红粗线 + EXTRA = 只有被测有（多检）
    橙粗线 + MISS    = 只有参考有（漏检）
配对成功的框上标 `IoU / 框角差px`，分数写成 `参考->被测`。

用法
----
    D:\\python\\python.exe tools\\draw_detections.py \\
        --calib data/samples/calib_real/frames_calib \\
        --test  data/samples/calib_real/frames_test \\
        --tag video30
"""
import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

import cv2  # noqa: E402
import pynq_net as pn  # noqa: E402
import eval_calib_set as ecs  # noqa: E402

OUT_DIR = REPO / "data" / "results" / "viz"

C_REF = (80, 220, 80)      # 绿：float32 参考
C_SIM = (60, 60, 245)      # 红：INT8 被测
C_EXTRA = (230, 60, 230)   # 品红：多检
C_MISS = (0, 165, 255)     # 橙：漏检
C_LOW = (0, 235, 255)      # 黄：次阈值框（分数 < conf，正常不画）
C_TXT = (255, 255, 255)
C_BG = (32, 32, 32)


def _put(img, text, org, color, scale=0.42, thick=1):
    cv2.putText(img, text, (org[0] + 1, org[1] + 1), cv2.FONT_HERSHEY_SIMPLEX,
                scale, (0, 0, 0), thick + 2, cv2.LINE_AA)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


def _box(img, b, color, thick=1):
    p1 = (int(round(b[0])), int(round(b[1])))
    p2 = (int(round(b[2])), int(round(b[3])))
    cv2.rectangle(img, p1, p2, color, thick, cv2.LINE_AA)


def _low_only(out, conf_low, conf, iou_thr):
    """取出分数落在 [conf_low, conf) 的框，并剔掉与「主框（>=conf）」重叠的重复项。

    为什么要剔：一条已经被 ≥conf 的框覆盖的检测，不是因为阈值被丢掉的，
    画出来只会让图更乱、并且会把「真被截掉的人」淹没。
    """
    d = pn.decode_detections(out, conf_low)
    if len(d["boxes"]) == 0:
        return None
    k = pn.nms(d["boxes"], d["scores"], d["cls"], iou_thr)
    b, s, c = d["boxes"][k], d["scores"][k], d["cls"][k]
    sel = s < conf
    hi = pn.decode_detections(out, conf)
    if len(hi["boxes"]):
        kh = pn.nms(hi["boxes"], hi["scores"], hi["cls"], iou_thr)
        hb, hc = hi["boxes"][kh], hi["cls"][kh]
        for i in range(len(b)):
            if not sel[i]:
                continue
            same = np.where(hc == c[i])[0]
            if same.size and pn._iou_one(b[i], hb[same]).max() >= 0.5:
                sel[i] = False
    if not sel.any():
        return None
    return {"boxes": b[sel], "scores": s[sel], "cls": c[sel]}


def render(frame_path: Path, det_ref, det_sim, nms_r, nms_s, img_bgr, size,
           scale=2, zoom=True, low_r=None, low_s=None, conf=0.25):
    """把两套框画到原图（框在 320 坐标系里，需按轴独立缩放回原图）。

    scale: 1 = 原分辨率；2 = 放大一倍（默认，640x360 上的注释文字不放大根本看不清）。
    zoom : 额外输出「最差配对框」的 4 倍局部放大图，用来判断偏差是整体平移还是形状不对。
    low_*: 分数落在 [conf_low, conf) 的**次阈值**框 —— 画成黄色，用来回答
           「图里明明有球员却没被标」到底是漏检还是被阈值截掉。
    """
    h0, w0 = img_bgr.shape[:2]
    sx, sy = w0 / float(size), h0 / float(size)
    if scale != 1:
        img_bgr = cv2.resize(img_bgr, (w0 * scale, h0 * scale),
                             interpolation=cv2.INTER_CUBIC)
    im = img_bgr.copy()
    hz, wz = im.shape[:2]

    def scale_box(b):
        return np.array([b[0] * sx * scale, b[1] * sy * scale,
                         b[2] * sx * scale, b[3] * sy * scale], dtype=np.float64)

    used_r = set()
    used_s = set()
    worst = None  # (err, box_ref_scaled, box_sim_scaled)
    bar = int(24 * scale)
    cv2.rectangle(im, (0, 0), (wz, bar), C_BG, -1)   # 顶部信息条，先画，免得被框盖住

    # 次阈值框（黄）：画在最底层，避免盖住绿/红判据框
    n_low_r = n_low_s = 0
    if low_r is not None:
        for i in range(len(low_r["boxes"])):
            b = scale_box(low_r["boxes"][i])
            _box(im, b, C_LOW, 2 * scale - 1)
            _put(im, "%.2f" % low_r["scores"][i],
                 (int(b[0]), max(bar + int(10 * scale), int(b[1]) - 4)), C_LOW, 0.36 * scale)
            n_low_r += 1
    if low_s is not None:
        for j in range(len(low_s["boxes"])):
            b = scale_box(low_s["boxes"][j])
            _box(im, b, C_LOW, 2 * scale - 1)
            _put(im, "%.2f" % low_s["scores"][j],
                 (int(b[0]), max(bar + int(10 * scale), int(b[3]) + 2)), C_LOW, 0.36 * scale)
            n_low_s += 1

    # 与 eval_calib_set.py 完全一致的贪心匹配（保证图上标注与 json 逐条对应）
    rr = np.zeros(len(nms_s["boxes"]), dtype=bool)
    for i in np.argsort(-nms_r["scores"]):
        cand = np.where((~rr) & (nms_s["cls"] == nms_r["cls"][i]))[0]
        if cand.size == 0:
            continue
        ious = pn._iou_one(nms_r["boxes"][i], nms_s["boxes"][cand])
        j = cand[int(np.argmax(ious))]
        if ious.max() >= 0.5:
            rr[j] = True
            used_r.add(int(i))
            used_s.add(int(j))
            br, bs = scale_box(nms_r["boxes"][i]), scale_box(nms_s["boxes"][j])
            _box(im, br, C_REF, 2 * scale - 1)
            _box(im, bs, C_SIM, 2 * scale - 1)
            err = float(np.max(np.abs(nms_r["boxes"][i] - nms_s["boxes"][j])))
            _put(im, "IoU.%.2f d%.1fpx %.2f>%.2f"
                 % (ious.max(), err, nms_r["scores"][i], nms_s["scores"][j]),
                 (int(br[0]), max(bar + int(10 * scale), int(br[1]) - 4)),
                 C_REF if err <= 3.0 else (0, 215, 255), 0.38 * scale)
            if worst is None or err > worst[0]:
                worst = (err, br, bs, ious.max())

    # 多检（品红）
    for j in range(len(nms_s["boxes"])):
        if j in used_s:
            continue
        b = scale_box(nms_s["boxes"][j])
        _box(im, b, C_EXTRA, 3 * scale - 2)
        _put(im, "EXTRA %.3f" % nms_s["scores"][j],
             (int(b[0]), max(bar + int(10 * scale), int(b[1]) - 4)), C_EXTRA, 0.44 * scale)
    # 漏检（橙）
    for i in range(len(nms_r["boxes"])):
        if i in used_r:
            continue
        b = scale_box(nms_r["boxes"][i])
        _box(im, b, C_MISS, 3 * scale - 2)
        _put(im, "MISS %.3f" % nms_r["scores"][i],
             (int(b[0]), max(bar + int(10 * scale), int(b[1]) - 4)), C_MISS, 0.44 * scale)

    head = ("%s   ref %d | INT8 %d | pair %d | EXTRA %d | MISS %d | LOW<%.2f ref %d / INT8 %d"
            % (frame_path.name, len(nms_r["boxes"]), len(nms_s["boxes"]),
               len(used_r), len(nms_s["boxes"]) - len(used_s),
               len(nms_r["boxes"]) - len(used_r), conf, n_low_r, n_low_s))
    bar = int(24 * scale)
    _put(im, head, (8, int(17 * scale)), C_TXT, 0.50 * scale)

    zoom_img = None
    if zoom and worst is not None:
        err, br, bs, iou = worst
        cx = (br[0] + br[2]) / 2.0
        cy = (br[1] + br[3]) / 2.0
        half = max(br[2] - br[0], br[3] - br[1]) * 0.85 + 12 * scale
        x1 = int(max(0, cx - half)); x2 = int(min(wz, cx + half))
        y1 = int(max(0, cy - half)); y2 = int(min(hz, cy + half))
        crop = im[y1:y2, x1:x2].copy()
        if crop.size:
            z = max(1, int(320 / max(1, crop.shape[1])))
            crop = cv2.resize(crop, (crop.shape[1] * z, crop.shape[0] * z),
                              interpolation=cv2.INTER_NEAREST)
            _put(crop, "worst pair  IoU %.2f  d %.2f px  (zoom x%d)"
                 % (iou, err, z), (6, 16), C_TXT, 0.46)
            zoom_img = crop
    return im, zoom_img


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="data/models/yolov8n_320.onnx")
    ap.add_argument("--calib", default=None)
    ap.add_argument("--test", required=True)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--conf-low", type=float, default=0.0,
                    help=">0 时把 [conf_low, conf) 的次阈值框画成黄色并标分数——"
                         "用来回答「这个球员为什么没被标」")
    ap.add_argument("--iou", type=float, default=0.45)
    ap.add_argument("--tag", default="viz")
    ap.add_argument("--scale", type=int, default=2, help="1=原分辨率，2=放大一倍（默认）")
    ap.add_argument("--cols", type=int, default=5)
    ap.add_argument("--sheet-w", type=int, default=560)
    args = ap.parse_args(argv)

    model = Path(args.model)
    if not model.is_absolute():
        model = REPO / model
    test_dir = Path(args.test)
    if not test_dir.is_absolute():
        test_dir = REPO / test_dir
    imgs = ecs.list_images(test_dir)
    if not imgs:
        print("DRAW_FAIL 测试目录没有图片")
        return 1

    graph = pn.load_graph(model)
    size = graph.input_shape[-1]
    out_name = graph.output_names[0]

    shared = None
    if args.calib:
        cd = Path(args.calib)
        if not cd.is_absolute():
            cd = REPO / cd
        shared = pn.ranges_from_ort_multi(model, graph, ecs.list_images(cd))
        print("标定完成：%d 张" % len(ecs.list_images(cd)))

    outdir = OUT_DIR / args.tag
    outdir.mkdir(parents=True, exist_ok=True)
    tiles = []
    stats = []
    for k, p in enumerate(imgs, 1):
        x = pn.load_image_input(p, size)
        ranges = shared if shared is not None else pn.ranges_from_ort(model, graph, x)
        plans = pn.build_plan(graph, ranges, x)
        tens, _ = pn.run_network(graph, x, pn.NumpyBackend(), plans)
        y = tens[out_name]
        ref = pn.ort_reference(model, {graph.input_name: x}, [out_name])[out_name]

        d_r = pn.decode_detections(ref, args.conf)
        d_s = pn.decode_detections(y, args.conf)
        kr = pn.nms(d_r["boxes"], d_r["scores"], d_r["cls"], args.iou)
        ks = pn.nms(d_s["boxes"], d_s["scores"], d_s["cls"], args.iou)
        nr = {"boxes": d_r["boxes"][kr], "scores": d_r["scores"][kr], "cls": d_r["cls"][kr]}
        ns = {"boxes": d_s["boxes"][ks], "scores": d_s["scores"][ks], "cls": d_s["cls"][ks]}

        lr = ls = None
        if args.conf_low > 0:
            lr = _low_only(ref, args.conf_low, args.conf, args.iou)
            ls = _low_only(y, args.conf_low, args.conf, args.iou)
            stats.append({
                "frame": p.name,
                "ref_ge_conf": int(len(nr["boxes"])),
                "sim_ge_conf": int(len(ns["boxes"])),
                "ref_low": 0 if lr is None else int(len(lr["boxes"])),
                "sim_low": 0 if ls is None else int(len(ls["boxes"])),
                "ref_low_scores": [] if lr is None else [round(float(v), 4) for v in lr["scores"]],
                "sim_low_scores": [] if ls is None else [round(float(v), 4) for v in ls["scores"]],
            })

        img = cv2.imread(str(p))
        vis, zoom = render(p, d_r, d_s, nr, ns, img, size, scale=args.scale,
                           low_r=lr, low_s=ls, conf=args.conf)
        dst = outdir / (p.stem + ".png")
        cv2.imwrite(str(dst), vis)
        if zoom is not None:
            cv2.imwrite(str(outdir / (p.stem + "_zoom.png")), zoom)

        h, w = vis.shape[:2]
        nw = args.sheet_w
        nh = int(round(h * nw / w))
        tiles.append(cv2.resize(vis, (nw, nh), interpolation=cv2.INTER_AREA))
        print("  [%2d/%2d] %s -> %s" % (k, len(imgs), p.name, dst.name))

    # 拼图（2 行 × cols 列的接触表）
    cols = args.cols
    rows = (len(tiles) + cols - 1) // cols
    th, tw = tiles[0].shape[:2]
    sheet = np.full((rows * th + 24, cols * tw, 3), 24, dtype=np.uint8)
    for i, t in enumerate(tiles):
        r, c = divmod(i, cols)
        sheet[24 + r * th:24 + (r + 1) * th, c * tw:(c + 1) * tw] = t
    _put(sheet, "INT8 vs float32 on %d held-out broadcast frames  %s   "
                "green=float32 ref   red=INT8   magenta=EXTRA   orange=MISS"
         % (len(tiles), args.tag), (8, 16), C_TXT, 0.52)
    sheet_path = outdir / ("_sheet_%s.png" % args.tag)
    cv2.imwrite(str(sheet_path), sheet)
    print("")
    print("接触表 -> %s" % sheet_path.relative_to(REPO))

    if args.conf_low > 0 and stats:
        tot_r = sum(s["ref_ge_conf"] for s in stats)
        tot_s = sum(s["sim_ge_conf"] for s in stats)
        lo_r = sum(s["ref_low"] for s in stats)
        lo_s = sum(s["sim_low"] for s in stats)
        print("")
        print("次阈值审计（conf_low=%.2f, conf=%.2f）：" % (args.conf_low, args.conf))
        print("  %-14s %10s %10s %10s %10s" % ("frame", "ref>=conf", "sim>=conf", "ref_low", "sim_low"))
        for s in stats:
            print("  %-14s %10d %10d %10d %10d"
                  % (s["frame"], s["ref_ge_conf"], s["sim_ge_conf"], s["ref_low"], s["sim_low"]))
        print("  %-14s %10d %10d %10d %10d" % ("TOTAL", tot_r, tot_s, lo_r, lo_s))
        print("  >> 被 %.2f 阈值截掉的真框（不在任何主框里）：ref %d 个 / INT8 %d 个"
              % (args.conf, lo_r, lo_s))
        jp = REPO / "data" / "results" / ("draw_lowconf_%s.json" % args.tag)
        with open(jp, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({
                "tag": args.tag, "conf": args.conf, "conf_low": args.conf_low,
                "iou": args.iou, "model": str(model.name),
                "totals": {"ref_ge_conf": tot_r, "sim_ge_conf": tot_s,
                           "ref_low": lo_r, "sim_low": lo_s},
                "frames": stats,
            }, indent=2))
        print("  审计 JSON -> %s" % jp.relative_to(REPO))
    print("DRAW_DONE %d" % len(tiles))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
