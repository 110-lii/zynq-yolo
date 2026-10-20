"""把「球员/非球员」标注包排成 PDF（每页一个框，带编号与填写栏）。

为什么要 PDF
-----------
73 张 jpg 逐个点开标注效率极低，且容易错行。PDF 里每页一个框：
  · 图在上（已放大，绿框标出"要判的这个框"）
  · 页眉写明编号 / 分层 / conf / 框高 / 颜色判定结果
  · 页脚是 P / N / U 勾选栏 + 备注一行

标注口径（必须在看图前知道，否则标出来的数据没法用于评估）
    P  球员（含守门员）
    N  非球员（裁判、教练、替补、看台上的观众、球童、摄像师、安保）
    U  看不清 / 只有半个身子 / 太小无法判断

用法
----
    & D:\\python\\python.exe tools\\player_labels_pdf.py
    & D:\\python\\python.exe tools\\player_labels_pdf.py --out report/player_labels_20261020.pdf

判据令牌
    PLAYER_PDF_OK <pages> <out>
    PLAYER_PDF_FAIL <reason>
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import csv
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parents[1]
LABELS = REPO / "data" / "annot" / "labels.csv"

PAGE_W, PAGE_H = 1240, 1754      # A4 纵向 @150dpi
MARGIN = 60

STRATUM_CN = {
    "hi": "常规检测层（conf>=0.25）",
    "lo_kept": "低置信·颜色保留（0.08<=conf<0.25，颜色判明属某队）",
    "lo_drop": "低置信·颜色丢弃（颜色判不明）",
}


def _font(size, bold=False):
    for n in (["msyhbd.ttc", "msyh.ttc"] if bold else ["msyh.ttc", "simhei.ttf"]):
        try:
            return ImageFont.truetype(n, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _text(d, xy, s, size=24, bold=False, fill=(0, 0, 0)):
    d.text(xy, s, font=_font(size, bold), fill=fill)


def index_page(rows, title):
    img = Image.new("RGB", (PAGE_W, PAGE_H), "white")
    d = ImageDraw.Draw(img)
    y = MARGIN
    _text(d, (MARGIN, y), title, 38, bold=True)
    y += 56
    for line in [
        "这是一个**标注包**，不是结果。请对每一页回答一个问题：这个框里的人是球员吗？",
        "  P = 球员（含守门员）",
        "  N = 非球员（裁判 / 教练 / 替补 / 看台观众 / 球童 / 摄像师 / 安保）",
        "  U = 看不清 / 只有半个身子 / 太小",
        "",
        "标完后把这些字母填进 data/annot/labels.csv 的 label 列，再跑：",
        "  python tools/make_player_labels.py --score",
        "那时才会出现「颜色先验 vs 无颜色先验」的 precision 对比。",
    ]:
        _text(d, (MARGIN, y), line, 24, fill=(50, 50, 50))
        y += 34
    y += 16
    _text(d, (MARGIN, y), "分层统计（分层是为了按层加权算 precision）", 26, bold=True)
    y += 38
    for k, v in STRATUM_CN.items():
        n = sum(1 for r in rows if r["stratum"] == k)
        _text(d, (MARGIN, y), "  %-8s %3d 个   %s" % (k, n, v), 22)
        y += 30
    y += 10
    _text(d, (MARGIN, y), "共 %d 页（每页一个框）" % len(rows), 24, bold=True)
    return img


def detail_page(r, idx, total):
    img = Image.new("RGB", (PAGE_W, PAGE_H), "white")
    d = ImageDraw.Draw(img)
    y = MARGIN
    _text(d, (MARGIN, y), "第 %d / %d 个框   id=%s" % (idx, total, r["id"]), 32, bold=True)
    y += 46
    _text(d, (MARGIN, y), "分层：%s" % STRATUM_CN.get(r["stratum"], r["stratum"]),
          24, fill=(150, 60, 0) if r["stratum"] != "hi" else (0, 100, 0))
    y += 36
    _text(d, (MARGIN, y),
          "检测置信度 %s  框高 %s px  颜色判定 %s（距离 %s）" % (
              r["conf"], r["h_px"], r["color_role"], r["color_dist"]),
          22, fill=(70, 70, 70))
    y += 34
    _text(d, (MARGIN, y), "来源：%s  第 %s 帧" % (r["clip"], r["frame"]),
          22, fill=(70, 70, 70))
    y += 40

    p = REPO / r["img"]
    if p.exists():
        im = Image.open(p).convert("RGB")
        max_w = PAGE_W - 2 * MARGIN
        max_h = PAGE_H - y - 330
        sc = min(2.0, max_w / im.width, max_h / im.height)
        if abs(sc - 1.0) > 0.02:
            im = im.resize((int(im.width * sc), int(im.height * sc)), Image.LANCZOS)
        img.paste(im, (MARGIN, y))
        y += im.height + 24
    else:
        _text(d, (MARGIN, y), "(缺图 %s)" % r["img"], 24, fill=(200, 0, 0))

    dy = PAGE_H - 250
    d.line([(MARGIN, dy), (PAGE_W - MARGIN, dy)], fill=(150, 150, 150), width=2)
    _text(d, (MARGIN, dy + 20), "这个框里的人是球员吗？（勾一个）", 28, bold=True)
    for label, bx, desc in (("P", 380, "球员"), ("N", 700, "非球员"), ("U", 980, "看不清")):
        d.rectangle([(bx, dy + 66), (bx + 42, dy + 108)], outline=(0, 0, 0), width=2)
        _text(d, (bx + 54, dy + 74), "%s  %s" % (label, desc), 26)
    _text(d, (MARGIN, dy + 130), "备注（可选）：", 24, bold=True)
    d.line([(MARGIN + 180, dy + 165), (PAGE_W - MARGIN, dy + 165)],
           fill=(130, 130, 130), width=1)
    _text(d, (MARGIN, dy + 182),
          "id 请对照本页右上角；填 CSV 时改对应 id 行的 label 列即可", 20, fill=(110, 110, 110))
    return img


def main(argv=None):
    ap = argparse.ArgumentParser(description="球员/非球员标注包 PDF")
    ap.add_argument("--csv", default=str(LABELS))
    ap.add_argument("--out", default=str(REPO / "report" / "player_labels_20261020.pdf"))
    args = ap.parse_args(argv)

    p = Path(args.csv)
    if not p.exists():
        print("PLAYER_PDF_FAIL csv_missing %s" % p)
        return 2
    with open(p, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        print("PLAYER_PDF_FAIL no_rows")
        return 2

    pages = [index_page(rows, "球员 / 非球员 标注包  ·  %d 个框" % len(rows))]
    for i, r in enumerate(rows, 1):
        pages.append(detail_page(r, i, len(rows)))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    pages[0].save(out, "PDF", resolution=150.0, save_all=True,
                  append_images=pages[1:])
    print("PAGES %d" % len(pages))
    print("PLAYER_PDF_OK %d %s" % (len(pages), out.relative_to(REPO)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
