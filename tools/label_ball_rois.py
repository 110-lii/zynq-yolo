"""label_ball_rois.py —— 足球 ROI 人工标注（OpenCV 窗口）

用法：
    python tools/label_ball_rois.py --dir data/ball_roi_full

操作：
    y  是球
    n  不是球
    u  撤销并回到上一张（可连续按）
    q  保存并退出
输出：<dir>/labels.json  {"positives":[...], "negatives":[...]}
已标注的会跳过，可随时中断续标。
"""
import argparse
import glob
import json
import os

import cv2

SCALE = 4
WIN = "ball labeling   y=ball  n=no  u=undo  q=save"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="data/ball_roi_full")
    args = ap.parse_args()

    man = json.load(open(os.path.join(args.dir, "manifest.json"), encoding="utf-8"))
    by_idx = {c["idx"]: c for c in man["crops"]}
    files = sorted(glob.glob(os.path.join(args.dir, "crops", "*.png")))
    lab_path = os.path.join(args.dir, "labels.json")
    labels = {"positives": [], "negatives": []}
    if os.path.exists(lab_path):
        labels = json.load(open(lab_path, encoding="utf-8"))
    done = set(labels["positives"]) | set(labels["negatives"])

    todo = [f for f in files if int(os.path.splitext(os.path.basename(f))[0]) not in done]
    pos_of = {int(os.path.splitext(os.path.basename(f))[0]): i for i, f in enumerate(todo)}
    history = []          # [(key, idx), ...]
    pos = 0

    cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
    while 0 <= pos < len(todo):
        f = todo[pos]
        cur = int(os.path.splitext(os.path.basename(f))[0])
        im = cv2.imread(f, cv2.IMREAD_COLOR)
        if im is None:
            pos += 1
            continue
        big = cv2.resize(im, (im.shape[1] * SCALE, im.shape[0] * SCALE),
                         interpolation=cv2.INTER_NEAREST)
        meta = by_idx.get(cur, {})
        cv2.putText(big, "#%d  t=%ss  %sx%s" % (cur, meta.get("t_s", "?"),
                    meta.get("w", "?"), meta.get("h", "?")),
                    (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 2)
        cv2.putText(big, "%d / %d" % (pos + 1, len(todo)), (6, big.shape[0] - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
        cv2.imshow(WIN, big)

        k = cv2.waitKey(0) & 0xFF
        if k in (ord("y"), ord("n")):
            key = "positives" if k == ord("y") else "negatives"
            labels[key].append(cur)
            history.append((key, cur))
            print("%s #%d" % ("BALL" if key == "positives" else "not ", cur))
            pos += 1
        elif k == ord("u"):
            if not history:
                print("undo: 已经是第一张")
                continue
            kk, ii = history.pop()
            if ii in labels[kk]:
                labels[kk].remove(ii)
            pos = pos_of.get(ii, max(0, pos - 1))   # 回到被撤销的那张
            print("undo #%d" % ii)
        elif k == ord("q") or k == 27:
            break

    cv2.destroyAllWindows()
    json.dump(labels, open(lab_path, "w", encoding="utf-8"), indent=2)
    print("SAVED pos=%d neg=%d -> %s" % (len(labels["positives"]),
                                         len(labels["negatives"]), lab_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())