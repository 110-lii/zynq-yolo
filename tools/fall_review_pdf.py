"""把倒地候选复核图打包成一份可打印/可滚动查看的 PDF（附候选清单表）。

为什么单独写这个
----------------
`tools/fall_review.py` 已经为每个候选出了「前/峰/后」三帧联系表（26 张 jpg），
但逐张点开看图效率低，且**看图的人需要在图上直接写下裁定**。本脚本把：

    第 1 页      候选总表（编号 / 片段 / 帧段 / 强度 / 几何量 / 待勾选栏）
    第 2 页起    一页一个候选，图在上、裁定勾选栏在下

排成一册。裁定栏对应 `data/results/fall_candidates.csv` 的 `judgement` 列
（pos / neg / unsure），看完后把 CSV 填好即可 `fall_review.py --decide` 写回。

用法
----
    & D:\\python\\python.exe tools/fall_review_pdf.py
    ... --out report/fall_review_20261020.pdf
    ... --tier strong        # 只打包强候选

判据令牌
    FALL_PDF_OK <pages> <out>
    FALL_PDF_FAIL <reason>
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import csv
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parents[1]
RESULTS = REPO / "data" / "results"
CAND_CSV = RESULTS / "fall_candidates.csv"

# A4 横向 @ 150 dpi（够清晰又能直接打印）
PAGE_W, PAGE_H = 1754, 1240
MARGIN = 70


def _font(size, bold=False):
    # Windows 自带字体；取不到就退回默认位图字体（不影响内容）
    names = ["msyhbd.ttc", "msyh.ttc"] if bold else ["msyh.ttc", "simhei.ttf"]
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _text(d, xy, s, size=26, bold=False, fill=(0, 0, 0)):
    d.text(xy, s, font=_font(size, bold), fill=fill)


def _wrap(s, width_chars):
    out, cur = [], ""
    for ch in s:
        cur += ch
        if len(cur) >= width_chars:
            out.append(cur)
            cur = ""
    if cur:
        out.append(cur)
    return out


def build_index_page(rows, title):
    img = Image.new("RGB", (PAGE_W, PAGE_H), "white")
    d = ImageDraw.Draw(img)
    y = MARGIN
    _text(d, (MARGIN, y), title, 40, bold=True)
    y += 62
    _text(d, (MARGIN, y),
          "每一条都是**候选**，不是结论。请在“裁定”栏写 pos（真倒地）/ neg（误报）/ unsure（看不清）。",
          24, fill=(60, 60, 60))
    y += 40
    _text(d, (MARGIN, y),
          "看完后把这些裁定填进 data/results/fall_candidates.csv 的 judgement 列，再跑 fall_review.py --decide。",
          24, fill=(60, 60, 60))
    y += 48

    cols = [("cand_id", 560), ("clip", 250), ("tier", 90), ("frames", 120),
            ("h_min/base", 150), ("ar_max", 100), ("裁定", 200)]
    x = MARGIN
    _text(d, (x, y), "候选编号", 24, bold=True); x += cols[0][1]
    _text(d, (x, y), "片段", 24, bold=True); x += cols[1][1]
    _text(d, (x, y), "强度", 24, bold=True); x += cols[2][1]
    _text(d, (x, y), "帧数", 24, bold=True); x += cols[3][1]
    _text(d, (x, y), "高 min/p90", 24, bold=True); x += cols[4][1]
    _text(d, (x, y), "宽高比", 24, bold=True); x += cols[5][1]
    _text(d, (x, y), "裁定", 24, bold=True)
    y += 34
    d.line([(MARGIN, y), (PAGE_W - MARGIN, y)], fill=(150, 150, 150), width=2)
    y += 10

    for i, r in enumerate(rows, 1):
        if y > PAGE_H - MARGIN - 40:
            break
        x = MARGIN
        short = r["cand_id"]
        if len(short) > 46:
            short = short[:44] + ".."
        _text(d, (x, y), "%2d. %s" % (i, short), 20); x += cols[0][1]
        _text(d, (x, y), r["clip"].split("_t")[0], 20); x += cols[1][1]
        _text(d, (x, y), r["tier"], 20,
              fill=(200, 0, 0) if r["tier"] == "strong" else (180, 120, 0)); x += cols[2][1]
        _text(d, (x, y), "%s f" % r["n_frames"], 20); x += cols[3][1]
        _text(d, (x, y), "%s/%s" % (r["h_min"], r["h_base_p90"]), 20); x += cols[4][1]
        _text(d, (x, y), str(r["aspect_max"]), 20); x += cols[5][1]
        d.rectangle([(x, y - 2), (x + 170, y + 26)], outline=(120, 120, 120), width=1)
        y += 34
    return img


def build_detail_page(r, idx, total):
    img = Image.new("RGB", (PAGE_W, PAGE_H), "white")
    d = ImageDraw.Draw(img)
    y = MARGIN
    tier_cn = "强候选" if r["tier"] == "strong" else "弱候选"
    _text(d, (MARGIN, y), "%d / %d   %s   [%s]" % (idx, total, r["cand_id"], tier_cn),
          30, bold=True, fill=(200, 0, 0) if r["tier"] == "strong" else (150, 100, 0))
    y += 46
    _text(d, (MARGIN, y),
          "片段 %s   track %s   帧 %s-%s（%s 帧 ≈ %.1f s）   时刻 %.2f-%.2f s" % (
              r["clip"], r["track_id"], r["f_start"], r["f_end"], r["n_frames"],
              int(r["n_frames"]) / 25.0, float(r["t_start_s"]), float(r["t_end_s"])),
          24, fill=(50, 50, 50))
    y += 38
    _text(d, (MARGIN, y),
          "几何：框高最低 %s px（该轨迹站立基线 p90 = %s px，掉高 %.0f%%），最大宽高比 %s，轨迹总长 %s 帧" % (
              r["h_min"], r["h_base_p90"],
              100.0 * (1 - float(r["h_min"]) / max(1e-6, float(r["h_base_p90"]))),
              r["aspect_max"], r["track_len"]),
          24, fill=(50, 50, 50))
    y += 48

    p = REPO / r["viz"]
    if p.exists():
        im = Image.open(p).convert("RGB")
        max_w = PAGE_W - 2 * MARGIN
        max_h = PAGE_H - y - 300
        sc = min(1.0, max_w / im.width, max_h / im.height)
        if sc < 1.0:
            im = im.resize((int(im.width * sc), int(im.height * sc)), Image.LANCZOS)
        img.paste(im, (MARGIN, y))
        y += im.height + 30
    else:
        _text(d, (MARGIN, y), "(缺图：%s)" % r["viz"], 24, fill=(200, 0, 0))
        y += 40

    # 裁定栏
    dy = PAGE_H - 240
    d.line([(MARGIN, dy), (PAGE_W - MARGIN, dy)], fill=(150, 150, 150), width=2)
    _text(d, (MARGIN, dy + 18), "裁定（勾一个）：", 28, bold=True)
    boxes = [("pos  真倒地", 420), ("neg  误报", 700), ("unsure  看不清", 940)]
    for label, bx in boxes:
        d.rectangle([(bx, dy + 16), (bx + 40, dy + 56)], outline=(0, 0, 0), width=2)
        _text(d, (bx + 52, dy + 22), label, 26)
    _text(d, (MARGIN, dy + 78), "备注：", 26, bold=True)
    d.line([(MARGIN + 90, dy + 112), (PAGE_W - MARGIN, dy + 112)], fill=(120, 120, 120), width=1)
    _text(d, (MARGIN, dy + 130),
          "常见误报形态（供参考）：merged box（两人并成一个框）、画幅边缘切框、远处小人、"
          "演播室字幕/广告牌被误检", 20, fill=(90, 90, 90))
    return img


def main(argv=None):
    ap = argparse.ArgumentParser(description="倒地候选复核手册 PDF")
    ap.add_argument("--csv", default=str(CAND_CSV))
    ap.add_argument("--out", default=str(REPO / "report" / "fall_review_20261020.pdf"))
    ap.add_argument("--tier", default="", choices=["", "strong", "weak"])
    args = ap.parse_args(argv)

    p = Path(args.csv)
    if not p.exists():
        print("FALL_PDF_FAIL csv_missing %s" % p)
        return 2
    with open(p, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if args.tier:
        rows = [r for r in rows if r["tier"] == args.tier]
    if not rows:
        print("FALL_PDF_FAIL no_rows")
        return 2

    # 强候选排前面（先看最像的）
    rows.sort(key=lambda r: (0 if r["tier"] == "strong" else 1,
                             r["clip"], int(r["f_start"])))

    pages = [build_index_page(
        rows, "倒地候选复核手册  ·  %d 个候选" % len(rows))]
    for i, r in enumerate(rows, 1):
        pages.append(build_detail_page(r, i, len(rows)))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    pages[0].save(out, "PDF", resolution=150.0, save_all=True,
                  append_images=pages[1:])
    print("PAGES %d" % len(pages))
    print("FALL_PDF_OK %d %s" % (len(pages), out.relative_to(REPO)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
