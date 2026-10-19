"""定点实现（INT8）的置信度阈值该怎么定 —— 量化把分数整体压低了，阈值不能照抄 float32。

为什么要这个工具
----------------
实测（10 帧留出集，imgsz=320，conf 降到 0.01）：

    分数最大值   float32 = 0.7729      INT8 = 0.5682

也就是说 int8 量化把检测头的峰值置信度压低了约 0.20。那么同一个 0.25 阈值
在两条实现上的"含义"并不相同 —— 在 PC 上用 float32 调好的阈值，
搬到板上跑 INT8 会**少检出一批人**。这个工具把这条偏移量成一条曲线。

它同时回答另一个问题：颜色/几何等二次判据要用在"疑似"框上，
那"疑似"的下界取多少？本工具给出「阈值 → 框数」的实测曲线作为依据，
而不是拍一个 0.15。

用法
----
    D:\\python\\python.exe tools\\conf_calibration.py \\
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

import pynq_net as pn  # noqa: E402
import eval_calib_set as ecs  # noqa: E402

# 曲线采样点。0.25 是 ultralytics 默认，也是本项目此前一直用的值。
THRESHES = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50]
REF_THRESH = 0.25


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="data/models/yolov8n_320.onnx")
    ap.add_argument("--calib", default=None)
    ap.add_argument("--test", default="data/samples/calib_real/frames_test")
    ap.add_argument("--iou", type=float, default=0.45)
    ap.add_argument("--tag", default="video30")
    args = ap.parse_args(argv)

    model = Path(args.model)
    if not model.is_absolute():
        model = REPO / model
    test_dir = Path(args.test)
    if not test_dir.is_absolute():
        test_dir = REPO / test_dir
    imgs = ecs.list_images(test_dir)
    if not imgs:
        print("CONF_CAL_FAIL 测试目录没有图片")
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

    # 每帧只跑一次网络，把原始 output0 存下来，之后所有阈值都在 numpy 里扫
    raw = []
    for p in imgs:
        x = pn.load_image_input(p, size)
        ranges = shared if shared is not None else pn.ranges_from_ort(model, graph, x)
        plans = pn.build_plan(graph, ranges, x)
        tens, _ = pn.run_network(graph, x, pn.NumpyBackend(), plans)
        ref = pn.ort_reference(model, {graph.input_name: x}, [out_name])[out_name]
        raw.append((p.name, np.asarray(ref), np.asarray(tens[out_name])))
    print("跑完 %d 帧（网络只前向一遍，阈值扫描在 numpy 里做）" % len(raw))

    def count(out, thr):
        d = pn.decode_detections(out, thr)
        if len(d["boxes"]) == 0:
            return 0
        k = pn.nms(d["boxes"], d["scores"], d["cls"], args.iou)
        return int(len(k))

    table = []
    for t in THRESHES:
        nr = sum(count(a, t) for _, a, _ in raw)
        ns = sum(count(b, t) for _, _, b in raw)
        table.append({"conf": t, "ref_boxes": nr, "int8_boxes": ns})
        print("  conf %.2f  ->  float32 %4d 框   INT8 %4d 框" % (t, nr, ns))

    ref_at_default = next(r["ref_boxes"] for r in table if abs(r["conf"] - REF_THRESH) < 1e-9)
    # 在 INT8 曲线上找"框数最接近 float32@0.25"的阈值 -> 量化后的等效阈值
    best = min(table, key=lambda r: abs(r["int8_boxes"] - ref_at_default))
    print("")
    print("float32 @ %.2f = %d 框" % (REF_THRESH, ref_at_default))
    print("INT8 要得到同样多框数，阈值需降到 %.2f（%d 框）" % (best["conf"], best["int8_boxes"]))

    # 逐对分数偏移：只统计能配上的框，看量化把分数压低了还是抬高了
    deltas = []
    for _, a, b in raw:
        dr = pn.decode_detections(a, 0.05)
        ds = pn.decode_detections(b, 0.05)
        if len(dr["boxes"]) == 0 or len(ds["boxes"]) == 0:
            continue
        kr = pn.nms(dr["boxes"], dr["scores"], dr["cls"], args.iou)
        ks = pn.nms(ds["boxes"], ds["scores"], ds["cls"], args.iou)
        rb = dr["boxes"][kr]; rs = dr["scores"][kr]; rc = dr["cls"][kr]
        sb = ds["boxes"][ks]; ss = ds["scores"][ks]; sc = ds["cls"][ks]
        used = np.zeros(len(sb), dtype=bool)
        for i in range(len(rb)):
            cand = np.where((~used) & (sc == rc[i]))[0]
            if cand.size == 0:
                continue
            ious = pn._iou_one(rb[i], sb[cand])
            j = cand[int(np.argmax(ious))]
            if ious.max() >= 0.5:
                used[j] = True
                deltas.append(float(ss[j] - rs[i]))
    d = np.array(deltas) if deltas else np.zeros(0)
    if d.size:
        print("")
        print("配对上框的分数偏移 (INT8 - float32)，n=%d：" % d.size)
        print("  均值 %+.4f   中位 %+.4f   p10 %+.4f   p90 %+.4f   最大负偏 %.4f"
              % (d.mean(), np.median(d), np.percentile(d, 10), np.percentile(d, 90), d.min()))

    out = {
        "tag": args.tag, "model": model.name, "iou": args.iou,
        "frames": len(raw), "ref_default_conf": REF_THRESH,
        "curve": table,
        "ref_at_default": ref_at_default,
        "int8_equivalent_conf": best["conf"],
        "int8_equivalent_boxes": best["int8_boxes"],
        "score_delta": {
            "n": int(d.size),
            "mean": float(d.mean()) if d.size else None,
            "median": float(np.median(d)) if d.size else None,
            "p10": float(np.percentile(d, 10)) if d.size else None,
            "p90": float(np.percentile(d, 90)) if d.size else None,
            "min": float(d.min()) if d.size else None,
        },
    }
    jp = REPO / "data" / "results" / ("conf_calibration_%s.json" % args.tag)
    with open(jp, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(out, indent=2))
    print("")
    print("结果 -> %s" % jp.relative_to(REPO))
    print("CONF_CAL_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
