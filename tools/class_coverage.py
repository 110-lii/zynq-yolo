"""统计给定图片集上「每个 COCO 类别被检出多少」——用来回答一个 D004 的问题：

    YOLOv8n 在什么输入分辨率下，才真的看得见「球」？

背景
----
`data/samples/calib_real/` 的 40 帧真实转播画面（640 宽）里，YOLOv8n@320 检出的
**全部是 person，sports ball 一个都没有**。这不是 INT8 的问题（float32 参考同样 0），
是「模型 + 分辨率」的问题。对一个「判罚」系统来说，看不见球是致命的，所以必须
在定 D004 参数（输入分辨率 / 模型 / 是否要自己训练）之前把这条测出来。

判据
----
无 PASS/FAIL —— 这是个**测量**工具，输出每个类别在多少个帧里出现过。
末行 `CLASS_COVERAGE_DONE`（跑完即成功，不看结果好坏）。

用法
----
    D:\\python\\python.exe tools\\class_coverage.py --imgsz 320,640 \\
        --dirs data/samples/calib_real/frames_calib,data/samples/calib_real/frames_test
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8")

REPO = Path(__file__).resolve().parents[1]
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="data/models/yolov8n.pt")
    ap.add_argument("--dirs", required=True, help="逗号分隔的目录")
    ap.add_argument("--imgsz", default="320,640")
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)

    from ultralytics import YOLO

    model_path = Path(args.model)
    if not model_path.is_absolute():
        model_path = REPO / model_path
    model = YOLO(str(model_path))
    names = model.names  # {id: name}

    imgs = []
    for d in args.dirs.split(","):
        p = Path(d.strip())
        if not p.is_absolute():
            p = REPO / p
        imgs += sorted(q for q in p.iterdir() if q.suffix.lower() in IMG_EXT)
    if args.limit:
        imgs = imgs[: args.limit]
    if not imgs:
        print("CLASS_COVERAGE_FAIL 没有图片")
        return 1

    sizes = [int(s) for s in args.imgsz.split(",")]
    print("=" * 78)
    print("class_coverage: %d 张图，模型 %s，conf>=%.2f" % (len(imgs), model_path.name, args.conf))
    print("=" * 78)

    for sz in sizes:
        per_cls_frames = Counter()   # 有多少帧至少检出 1 个该类别
        per_cls_boxes = Counter()    # 该类别总框数
        n_no_det = 0
        for p in imgs:
            r = model.predict(str(p), imgsz=sz, conf=args.conf, device="cpu", verbose=False)[0]
            cls = r.boxes.cls.cpu().numpy().astype(int) if r.boxes is not None else []
            if len(cls) == 0:
                n_no_det += 1
            for c in set(cls.tolist()):
                per_cls_frames[int(c)] += 1
            for c in cls.tolist():
                per_cls_boxes[int(c)] += 1
        print("")
        print("--- imgsz=%d ---" % sz)
        print("  零检测帧 %d/%d" % (n_no_det, len(imgs)))
        if not per_cls_frames:
            print("  （完全没有检出任何目标）")
            continue
        print("  %-16s %-12s %-10s" % ("类别", "出现的帧数", "总框数"))
        for c, nf in per_cls_frames.most_common():
            print("  %-16s %-12s %-10s" % ("%d %s" % (c, names.get(c, "?")), "%d/%d" % (nf, len(imgs)),
                                           per_cls_boxes[c]))

    print("")
    print("CLASS_COVERAGE_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
