"""在**留出集**上评价 INT8 整网，而不是只看一张图。

为什么需要它
------------
`pynq_net.py --check` 一次只跑一张图，它报的 `max` 是**单个最差样本**。用一张图
下结论有两个问题：

1. **n = 1 没有判别力。** 一张图的运气好或坏，会直接决定 PASS/FAIL。
2. **`max` 极其脆弱。** 16 个匹配框里只要有一个处在分数阈值附近，`max_score_err`
   就会被它一个人拉高，看起来像"整体崩了"，而实际上 15 个框都很好。

所以本工具做两件事：把评价推到 N 张留出图，并把「最大值」换成**分布**
（p50 / p90 / max）+ **判决一致性**（框数是否相等、有无漏检多检）。

标定集与测试集必须不重叠
------------------------
`--calib` 给标定帧，`--test` 给留出帧，两者的交集必须是空集 —— 否则就是
拿答案去考试。本工具会**显式检查并拒绝**重叠。

用法
----
    D:\\python\\python.exe tools\\eval_calib_set.py \\
        --calib data/samples/calib_real/frames_calib \\
        --test  data/samples/calib_real/frames_test \\
        --tag match

    # 对照组：不传 --calib，则退化为"每张图各自标定"（旧做法）
    ... --test data/samples/calib_real/frames_test --tag selfcal

判据
----
末行 `EVAL_SET_OK` / `EVAL_SET_FAIL`。判据是**逐帧**的（每帧都要 cosine>=0.999、
框数相等、框角<=3 px），另额外报告分数差的分布供判断，**不把 max 分数差当硬判据**
—— 那正是它不可信的地方。
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

import pynq_net as pn  # noqa: E402

OUT_DIR = REPO / "data" / "results"
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def list_images(d: Path):
    return sorted(p for p in d.iterdir() if p.suffix.lower() in IMG_EXT)


def match_pairs(ref, sim, iou_thr=0.5):
    """贪心 IoU 匹配，返回逐对明细（与 pynq_net.match_detections 同算法，
    但它只给 max，这里要分布）。"""
    rb, rs, rc = ref["boxes"], ref["scores"], ref["cls"]
    sb, ss, sc = sim["boxes"], sim["scores"], sim["cls"]
    used = np.zeros(len(sb), dtype=bool)
    rows = []
    for i in np.argsort(-rs):
        cand = np.where((~used) & (sc == rc[i]))[0]
        if cand.size == 0:
            continue
        ious = pn._iou_one(rb[i], sb[cand])
        j = cand[int(np.argmax(ious))]
        if ious.max() >= iou_thr:
            used[j] = True
            rows.append(
                dict(
                    cls=int(rc[i]),
                    ref_score=float(rs[i]),
                    sim_score=float(ss[j]),
                    score_err=float(abs(rs[i] - ss[j])),
                    corner_err_px=float(np.max(np.abs(rb[i] - sb[j]))),
                    iou=float(ious.max()),
                )
            )
    return rows, int(used.sum())


def pct(a, q):
    return float(np.percentile(a, q)) if len(a) else float("nan")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=str(pn.DEFAULT_MODEL.relative_to(REPO)))
    ap.add_argument("--calib", default=None,
                    help="标定帧目录；不给则每张测试图各自标定（对照组）")
    ap.add_argument("--test", required=True, help="留出帧目录")
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--iou", type=float, default=0.45)
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 张（调试）")
    ap.add_argument("--tag", default="set")
    args = ap.parse_args(argv)

    model = Path(args.model)
    if not model.is_absolute():
        model = REPO / model
    test_dir = Path(args.test)
    if not test_dir.is_absolute():
        test_dir = REPO / test_dir
    test_imgs = list_images(test_dir)
    if args.limit:
        test_imgs = test_imgs[: args.limit]
    if not test_imgs:
        print(f"EVAL_SET_FAIL 测试目录没有图片: {test_dir}")
        return 1

    calib_imgs = []
    if args.calib:
        cd = Path(args.calib)
        if not cd.is_absolute():
            cd = REPO / cd
        calib_imgs = list_images(cd)
        if not calib_imgs:
            print(f"EVAL_SET_FAIL 标定目录没有图片: {cd}")
            return 1
        # ★ 标定集与测试集不许重叠（按解析后的绝对路径与 sha256 双重检查）
        import hashlib

        def key(p):
            return hashlib.sha256(p.read_bytes()).hexdigest()

        ck = {key(p) for p in calib_imgs}
        overlap = [p.name for p in test_imgs if key(p) in ck]
        if overlap:
            print(f"EVAL_SET_FAIL 标定集与测试集重叠 {len(overlap)} 张: {overlap[:5]}")
            return 1

    graph = pn.load_graph(model)
    size = graph.input_shape[-1]
    out_name = graph.output_names[0]

    print("=" * 78)
    print("eval_calib_set : YOLOv8n@%d 整网 INT8，留出集评价" % size)
    print("  模型     : %s" % model.name)
    print("  标定集   : %s" % (f"{len(calib_imgs)} 张 @ {args.calib}" if calib_imgs
                              else "无（每张测试图各自标定 —— 对照组）"))
    print("  测试集   : %d 张 @ %s" % (len(test_imgs), args.test))
    print("=" * 78)

    # ---- 标定只做一次（多图 ORT 前向很贵）----
    shared_ranges = None
    if calib_imgs:
        t0 = time.perf_counter()
        shared_ranges = pn.ranges_from_ort_multi(model, graph, calib_imgs)
        print("多图标定完成：%d 张，用时 %.1f s" % (len(calib_imgs), time.perf_counter() - t0))

    rows = []
    n_ok = 0
    for k, img in enumerate(test_imgs, 1):
        x = pn.load_image_input(img, size)
        if shared_ranges is not None:
            ranges = shared_ranges
            src = "shared"
        else:
            ranges = pn.ranges_from_ort(model, graph, x)
            src = "self"
        plans = pn.build_plan(graph, ranges, x)
        tensors, _stats = pn.run_network(graph, x, pn.NumpyBackend(), plans)
        y = tensors[out_name]
        ref = pn.ort_reference(model, {graph.input_name: x}, [out_name])[out_name]
        c = pn.compare(ref, y)

        det_ref = pn.decode_detections(ref, args.conf)
        det_sim = pn.decode_detections(y, args.conf)
        kr = pn.nms(det_ref["boxes"], det_ref["scores"], det_ref["cls"], args.iou)
        ks = pn.nms(det_sim["boxes"], det_sim["scores"], det_sim["cls"], args.iou)
        nr = {"boxes": det_ref["boxes"][kr], "scores": det_ref["scores"][kr],
              "cls": det_ref["cls"][kr]}
        ns = {"boxes": det_sim["boxes"][ks], "scores": det_sim["scores"][ks],
              "cls": det_sim["cls"][ks]}

        pairs, n_used = match_pairs(nr, ns, 0.5)
        box_errs = [p["corner_err_px"] for p in pairs]
        score_errs = [p["score_err"] for p in pairs]

        # ---- 失配框诊断：它们是不是都贴着阈值？----
        # 「多检」= 被测有、参考附近没有；「漏检」= 参考有、被测附近没有。
        # 记下它们的**分数**：若多检框只比阈值高一点点、漏检框只比阈值低一点点，
        # 就说明失配是"分数被量化误差推过阈值"，而不是"凭空多出/漏掉一个目标"。
        used = np.zeros(len(ns["boxes"]), dtype=bool)
        matched_r, matched_s = set(), set()
        for i in np.argsort(-nr["scores"]):
            cand = np.where((~used) & (ns["cls"] == nr["cls"][i]))[0]
            if cand.size == 0:
                continue
            ious = pn._iou_one(nr["boxes"][i], ns["boxes"][cand])
            j = cand[int(np.argmax(ious))]
            if ious.max() >= 0.5:
                used[j] = True
                matched_r.add(int(i))
                matched_s.add(int(j))
        extra = [dict(score=float(ns["scores"][j]), cls=int(ns["cls"][j]))
                 for j in sorted(set(range(len(ns["boxes"]))) - matched_s)]
        missed = [dict(score=float(nr["scores"][i]), cls=int(nr["cls"][i]))
                  for i in sorted(set(range(len(nr["boxes"]))) - matched_r)]

        ok = True
        reasons = []
        if c["cosine"] < pn.THRESH_COSINE:
            ok = False
            reasons.append("cosine %.6f" % c["cosine"])
        if len(nr["boxes"]) != len(ns["boxes"]):
            ok = False
            reasons.append("框数 %d vs %d" % (len(nr["boxes"]), len(ns["boxes"])))
        if box_errs and max(box_errs) > pn.THRESH_BOX_PX:
            ok = False
            reasons.append("框角 max %.2f px" % max(box_errs))
        if ok:
            n_ok += 1

        n_hi_ref = int(np.sum(nr["scores"] >= 0.5))
        n_hi_sim = int(np.sum(ns["scores"] >= 0.5))

        rows.append(
            dict(
                file=img.name,
                calib_src=src,
                cosine=float(c["cosine"]),
                n_nms_ref=len(nr["boxes"]),
                n_nms_sim=len(ns["boxes"]),
                n_hi_ref=n_hi_ref,
                n_hi_sim=n_hi_sim,
                matched=len(pairs),
                unmatched_ref=len(nr["boxes"]) - len(pairs),
                unmatched_sim=len(ns["boxes"]) - len(pairs),
                extra_boxes=extra,
                missed_boxes=missed,
                worst_pairs=sorted(pairs, key=lambda p: -p["corner_err_px"])[:3],
                box_err_p50=pct(box_errs, 50),
                box_err_p90=pct(box_errs, 90),
                box_err_max=max(box_errs) if box_errs else None,
                score_err_p50=pct(score_errs, 50),
                score_err_p90=pct(score_errs, 90),
                score_err_max=max(score_errs) if score_errs else None,
                ok=ok,
                reasons=reasons,
            )
        )
        print(
            "  [%2d/%2d] %-16s cos=%.6f 框 %2d/%2d 配对 %2d  "
            "框角max=%6.2fpx 分数max=%.4f  %s"
            % (
                k, len(test_imgs), img.name, c["cosine"],
                len(nr["boxes"]), len(ns["boxes"]), len(pairs),
                max(box_errs) if box_errs else float("nan"),
                max(score_errs) if score_errs else float("nan"),
                "OK" if ok else "FAIL:" + ";".join(reasons),
            )
        )

    # ---- 汇总 ----
    all_box = [r["box_err_max"] for r in rows if r["box_err_max"] is not None]
    all_score = [r["score_err_max"] for r in rows if r["score_err_max"] is not None]
    cos = [r["cosine"] for r in rows]
    det_agree = sum(1 for r in rows if r["n_nms_ref"] == r["n_nms_sim"])
    no_miss = sum(1 for r in rows if r["unmatched_ref"] == 0 and r["unmatched_sim"] == 0)

    # 失配框的分数离阈值有多远？这是「失配 = 阈值抖动」还是「失配 = 真丢目标」的判据。
    ex = [b["score"] for r in rows for b in r["extra_boxes"]]
    ms = [b["score"] for r in rows for b in r["missed_boxes"]]
    hi_agree = sum(1 for r in rows if r["n_hi_ref"] == r["n_hi_sim"])

    summary = {
        "tool": "tools/eval_calib_set.py",
        "model": model.name,
        "calib": args.calib,
        "n_calib": len(calib_imgs),
        "test": args.test,
        "n_test": len(rows),
        "conf": args.conf,
        "iou": args.iou,
        "n_frame_pass": n_ok,
        "frame_pass_rate": n_ok / len(rows),
        "cosine_min": min(cos),
        "cosine_mean": float(np.mean(cos)),
        "det_count_agree_frames": det_agree,
        "no_miss_no_extra_frames": no_miss,
        "hi_conf_count_agree_frames": hi_agree,
        "hi_conf_thr": 0.5,
        "extra_boxes_scores": ex,
        "missed_boxes_scores": ms,
        "box_err_px": {"p50": pct(all_box, 50), "p90": pct(all_box, 90),
                       "max": max(all_box) if all_box else None},
        "score_err": {"p50": pct(all_score, 50), "p90": pct(all_score, 90),
                      "max": max(all_score) if all_score else None},
        "thresholds": {"cosine_min": pn.THRESH_COSINE, "box_px_max": pn.THRESH_BOX_PX,
                       "score_max_report_only": pn.THRESH_SCORE},
        "per_frame": rows,
    }

    print("")
    print("-" * 78)
    print("留出集汇总（%d 张）" % len(rows))
    print("  逐帧全过        : %d/%d = %.1f%%" % (n_ok, len(rows), 100.0 * n_ok / len(rows)))
    print("  cosine  min/mean: %.6f / %.6f" % (min(cos), float(np.mean(cos))))
    print("  框数一致帧      : %d/%d" % (det_agree, len(rows)))
    print("  无漏检无多检帧  : %d/%d" % (no_miss, len(rows)))
    print("  高分框(>=0.5)数一致帧: %d/%d" % (hi_agree, len(rows)))
    if ex:
        print("  多检框 %d 个，分数 %.3f–%.3f（越接近 %.2f 越说明是阈值抖动）"
              % (len(ex), min(ex), max(ex), args.conf))
    if ms:
        print("  漏检框 %d 个，分数 %.3f–%.3f" % (len(ms), min(ms), max(ms)))
    if all_box:
        print("  框角差 px p50/p90/max: %.2f / %.2f / %.2f"
              % (pct(all_box, 50), pct(all_box, 90), max(all_box)))
    if all_score:
        print("  分数差   p50/p90/max: %.4f / %.4f / %.4f  （仅供参考，不作硬判据）"
              % (pct(all_score, 50), pct(all_score, 90), max(all_score)))

    out = OUT_DIR / ("calib_eval_%s.json" % args.tag)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print("")
    print("明细 -> %s" % out.relative_to(REPO))
    print("EVAL_SET_OK" if n_ok == len(rows) else "EVAL_SET_FAIL")
    return 0 if n_ok == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
