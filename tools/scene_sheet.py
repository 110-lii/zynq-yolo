#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scene_sheet.py —— 把分层测试集拼成一张**给人看**的对照图。

为什么需要它
------------
`contest` / `normal` 两个标签目前是**几何代理**算出来的（人员聚集度 + 人数），
MANIFEST 里 `human_ok` 一列留空就是等这一眼：切片切得对不对，只能人看。
但要人看，不能让人一段段拖视频 —— 把每段取中间帧、缩成小图、拼成一张，
一眼扫完 16 段，哪段贴错标签当场就能指出来。

产出
----
    data/samples/scenes/_sheet_scenes.png   上排 contest，下排 normal，每格标 id / 时间 / 聚集度

用法
----
    py tools/scene_sheet.py                 # 默认每段取中点帧
    py tools/scene_sheet.py --at 0.25 --cell 480
"""
import argparse
import csv
import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DIR_DEFAULT = ROOT / "data" / "samples" / "scenes"


def label(img, lines, scale=0.7):
    import cv2
    h = img.shape[0]
    s = max(0.35, scale * h / 240.0)
    lh = int(30 * s)
    for i, text in enumerate(lines):
        y = int(h * 0.055) + int(26 * s) + i * lh
        for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
            cv2.putText(img, text, (int(8 * s) + dx, y + dy), cv2.FONT_HERSHEY_SIMPLEX,
                        s, (0, 0, 0), max(2, int(3 * s)), cv2.LINE_AA)
        cv2.putText(img, text, (int(8 * s), y), cv2.FONT_HERSHEY_SIMPLEX,
                    s, (255, 255, 255), max(1, int(2 * s)), cv2.LINE_AA)
    return img


def frame_at(path, frac):
    import cv2
    cap = cv2.VideoCapture(str(path))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(n * frac)))
    ok, img = cap.read()
    cap.release()
    return img if ok else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(DIR_DEFAULT))
    ap.add_argument("--at", type=float, default=0.5, help="取每段第几成处的帧")
    ap.add_argument("--cell", type=int, default=400, help="每格宽（像素）")
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    import cv2
    import numpy as np

    d = Path(args.dir)
    man = d / "MANIFEST.csv"
    if not man.is_file():
        print("SCENE_SHEET_FAIL no_manifest %s" % man)
        return 2
    rows = [r for r in csv.DictReader(open(man, encoding="utf-8")) if r.get("clip_file")]

    tiles, miss = [], []
    for r in rows:
        img = frame_at(d / r["clip_file"], args.at)
        if img is None:
            miss.append(r["scene_id"])
            continue
        h = int(img.shape[0] * args.cell / img.shape[1])
        img = cv2.resize(img, (args.cell, h), interpolation=cv2.INTER_AREA)
        col = (0, 0, 255) if r["kind"] == "contest" else (0, 255, 0)
        cv2.rectangle(img, (0, 0), (img.shape[1] - 1, img.shape[0] - 1), col, 2)
        tiles.append((r, img))

    if not tiles:
        print("SCENE_SHEET_FAIL no_frames（片段文件不在？先 --extract 切出来）")
        return 2

    # 两类分开排：人在扫图时要的是「同类放一起对比」，不是时间顺序
    order = sorted(range(len(tiles)), key=lambda i: (tiles[i][0]["kind"] != "contest",
                                                     tiles[i][0]["scene_id"]))
    hcell = tiles[0][1].shape[0]
    per = min(args.cols, max(1, len(tiles)))
    sheets = []
    i = 0
    while i < len(order):
        idx = order[i:i + per]
        canv = np.zeros((hcell, per * args.cell, 3), np.uint8)
        for j, k in enumerate(idx):
            r, img = tiles[k]
            # 两行：id 单独一行，时间/聚集度一行。挤在一行会在 400px 格子里被截掉尾巴。
            txt = ["%s  %s" % (r["scene_id"], r["kind"]),
                   "%s-%ss  w=%s" % (r["t_start_s"].split(".")[0],
                                     r["t_end_s"].split(".")[0], r["bunch_median"])]
            canv[:, j * args.cell:(j + 1) * args.cell] = label(img, txt)
        sheets.append(canv)
        i += per
    sheet = np.vstack(sheets)

    out = Path(args.out) if args.out else d / "_sheet_scenes.png"
    cv2.imwrite(str(out), sheet)
    nc = sum(1 for r, _ in tiles if r["kind"] == "contest")
    print("SCENE_SHEET_OK %s cells=%d contest=%d normal=%d missing=%d size=%dx%d"
          % (out.name, len(tiles), nc, len(tiles) - nc, len(miss),
             sheet.shape[1], sheet.shape[0]))
    if miss:
        print("SCENE_SHEET_MISSING %s" % ",".join(miss))
    print("SCENE_SHEET_NOTE 上排应是围抢（人挤在一起），下排应是铺开的阵地/推进；"
          "对不上的段请在 MANIFEST.csv 的 human_ok 写 no 并在 notes 说明。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
