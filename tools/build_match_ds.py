"""build_match_ds.py -- 从两场完整比赛构建检测数据集（YOLO 格式，按比赛切分）。

- 每场等间隔抽 --per-video 帧，保存 JPEG；
- YOLOv8n 在 --imgsz 下自动标注 person / sports_ball；
- train = 第一场，val = 第二场（跨场验证，避免同场泄漏）；
- 输出 data/train/det/{images,labels}/{train,val} + data.yaml。

用法：
    python tools/build_match_ds.py --per-video 1000 --imgsz 960
令牌：MATCH_DS_OK <n_train> <n_val> <path>
"""
import argparse
import time
from pathlib import Path

import cv2
from ultralytics import YOLO

REPO = Path(__file__).resolve().parents[1]
VIDEOS = [
    Path(r"D:\yt-dlp\downloads\migu_5106947698\migu_5106947698.mp4"),
    Path(r"D:\yt-dlp\downloads\migu_968460165\2026中超第28轮全场回放_大连英博海发3-1深圳新鹏城.mp4"),
]
OUT = REPO / "data" / "train" / "det"
CLS_MAP = {0: 0, 32: 1}  # COCO person -> 0, sports_ball -> 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-video", type=int, default=1000)
    ap.add_argument("--imgsz", type=int, default=960)
    ap.add_argument("--conf", type=float, default=0.30)
    args = ap.parse_args()

    model = YOLO(str(REPO / "data" / "models" / "yolov8n.pt"))
    for split in ("train", "val"):
        (OUT / "images" / split).mkdir(parents=True, exist_ok=True)
        (OUT / "labels" / split).mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    counts = {"train": 0, "val": 0}
    labels = {"train": 0, "val": 0}
    for vi, vp in enumerate(VIDEOS):
        split = "train" if vi == 0 else "val"
        cap = cv2.VideoCapture(str(vp))
        if not cap.isOpened():
            print("open fail", vp)
            continue
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        for k in range(args.per_video):
            idx = int(total * (0.03 + 0.94 * k / max(1, args.per_video)))
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, frame = cap.read()
            if not ok:
                continue
            res = model(frame, imgsz=args.imgsz, conf=args.conf, classes=[0, 32], verbose=False)[0]
            boxes = res.boxes.xyxy.cpu().numpy()
            clss = res.boxes.cls.cpu().numpy().astype(int)
            h, w = frame.shape[:2]
            lines = []
            for b, c in zip(boxes, clss):
                if c not in CLS_MAP:
                    continue
                x1, y1, x2, y2 = b
                bw, bh = x2 - x1, y2 - y1
                if bw < 4 or bh < 8:
                    continue
                cx, cy = (x1 + x2) / 2.0 / w, (y1 + y2) / 2.0 / h
                lines.append("%d %.6f %.6f %.6f %.6f" % (CLS_MAP[c], cx, cy, bw / w, bh / h))
            name = "%s_%06d" % (split, idx)
            cv2.imwrite(str(OUT / "images" / split / (name + ".jpg")), frame,
                        [cv2.IMWRITE_JPEG_QUALITY, 90])
            (OUT / "labels" / split / (name + ".txt")).write_text("\n".join(lines), encoding="utf-8")
            counts[split] += 1
            labels[split] += len(lines)
        cap.release()
        print("SPLIT %s frames=%d boxes=%d" % (split, counts[split], labels[split]), flush=True)

    yaml = OUT / "data.yaml"
    yaml.write_text(
        "path: %s\ntrain: images/train\nval: images/val\nnames:\n  0: person\n  1: sports_ball\n" %
        OUT.as_posix(), encoding="utf-8")
    print("MATCH_DS_OK %d %d %s (%.0fs)" % (counts["train"], counts["val"], yaml,
                                            time.perf_counter() - t0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())