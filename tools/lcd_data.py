"""lcd_data.py -- LCD 1bpp 帧缓存数据：独立打包器 + 诊断图生成。

与旧 bram_fb/status_display 无关，口径写死在这里：
  · 每行 WPR = W/32 个 32-bit 字，共 H 行；
  · 字内 bit0 = 最左像素，bit31 = 该字第 32 个像素；
  · 输出 .bin 为小端 uint32 原始流，板端按字直接写 BRAM。

用法：
    python tools/lcd_data.py --size 800x480 --pattern text --bin fb.bin --png fb.png
    python tools/lcd_data.py --selftest
"""
import argparse
import struct
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
FONT = ROOT / "data" / "fonts" / "simhei_subset.ttf"

SIZES = {"800x480": (800, 480), "480x272": (480, 272)}


def pack_1bpp(gray, w, h):
    """灰度图 -> uint32 数组。bit0=最左像素，行优先，无行填充（w 必须是 32 的倍数）。"""
    a = np.asarray(gray, dtype=np.uint8)
    if a.shape != (h, w):
        raise ValueError("shape %s != (%d,%d)" % (a.shape, h, w))
    if w % 32:
        raise ValueError("width %d not multiple of 32" % w)
    bits = (a > 127).astype(np.uint8).reshape(h, w)
    packed = np.packbits(bits, axis=1, bitorder="little")   # 每行 w/8 字节
    words = np.frombuffer(packed.tobytes(), dtype="<u4").copy()
    return words


def unpack_1bpp(words, w, h):
    raw = np.asarray(words, dtype="<u4").tobytes()
    bits = np.unpackbits(np.frombuffer(raw, dtype=np.uint8), bitorder="little")
    return bits[: h * w].reshape(h, w)


def _canvas(w, h):
    return np.zeros((h, w), dtype=np.uint8)


def _border(img, t=2):
    img[:t, :] = 255
    img[-t:, :] = 255
    img[:, :t] = 255
    img[:, -t:] = 255
    return img


def _text(img, xy, s, size=32):
    from PIL import Image, ImageDraw, ImageFont
    pil = Image.fromarray(img)
    d = ImageDraw.Draw(pil)
    try:
        font = ImageFont.truetype(str(FONT), size)
    except Exception:
        font = ImageFont.load_default()
    d.text(xy, s, fill=255, font=font)
    return np.asarray(pil).copy()



# ---------------------------------------------------------------------------
# 4bpp（16 色）自证图 —— LCD 现在的格式
#
# 自证方式与 1bpp 的 tag 图同理：字 (r,k) 的 8 个像素全写索引 (r+k)&15，
# 于是「面板第 R 行第 0 个字」应当正好是 8 个 (R&15) 拼起来：
#   0x00000000 / 0x11111111 / ... / 0xFFFFFFFF
# 读侧读错行、读错字、位序错、把 4bpp 当 1bpp，全都会立刻对不上。
# ---------------------------------------------------------------------------
PAL4 = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0),
        (0, 255, 255), (255, 0, 255), (255, 255, 255), (0, 0, 0),
        (128, 0, 0), (0, 128, 0), (0, 0, 128), (128, 128, 0),
        (0, 128, 128), (128, 0, 128), (192, 192, 192), (64, 64, 64)]


def pal4_words():
    return np.array([(r << 16) | (g << 8) | b for (r, g, b) in PAL4], dtype=np.uint32)


def tag4_words(w, h):
    """4bpp 自证图：字 (r,k) = 8 份索引 (r+k)&15。"""
    wpr = w // 8
    out = np.zeros(wpr * h, dtype=np.uint32)
    for r in range(h):
        base = r * wpr
        for k in range(wpr):
            out[base + k] = np.uint32(0x11111111) * np.uint32((r + k) & 15)
    return out


def tag4_png(w, h):
    """把 tag4 画成 PNG（预览用，颜色按 PAL4 索引）。"""
    wpr = w // 8
    img = np.zeros((h, w, 3), dtype=np.uint8)
    for r in range(h):
        for k in range(wpr):
            img[r, k * 8:(k + 1) * 8] = PAL4[(r + k) & 15]
    return img


def make_image(pattern, w, h):
    if pattern == "white":
        return np.full((h, w), 255, dtype=np.uint8)
    if pattern == "black":
        return _canvas(w, h)

    if pattern == "split":
        img = _canvas(w, h)
        img[:, : w // 2] = 255
        img[:, w - 1] = 255          # 最右一列白：用来判断右边有没有被裁掉
        img[0, :] = 255
        img[-1, :] = 255
        return img

    if pattern == "checker":
        yy, xx = np.mgrid[0:h, 0:w]
        return np.where(((xx // 32) + (yy // 32)) % 2 == 0, 255, 0).astype(np.uint8)

    if pattern == "grid":
        img = _canvas(w, h)
        img[::32, :] = 255
        img[:, ::32] = 255
        return _border(img)

    if pattern == "bars":
        img = _canvas(w, h)
        bw = w // 8
        for i in range(8):
            if i % 2 == 0:
                img[:, i * bw:(i + 1) * bw] = 255
        return _border(img)

    if pattern == "map":
        # 标尺图：每 32px 一根 1px 竖齿（25 根，最左齿加粗到 3px 作左端标记），
        # 每 60 行一条通栏横线。上板拍一张照片就能量出：
        #   · 水平方向有没有整字重复/漏齿（正确的屏应看到 25 根等距齿）
        #   · 垂直方向有没有错行（8 条横线应等距，第一条贴顶）
        img = _canvas(w, h)
        img[:, ::32] = 255
        img[:, 0:3] = 255
        for y in range(0, h, 60):
            img[y, :] = 255
        return img

    if pattern == "tag":
        # 自证图：第 r 行第 w 个字 = (r<<16) | w。PS 读回 line_buf 的某个 32 位字，
        # 就能直接解出「面板这一行显示的是源图哪一行的第几个字」，不用靠照片反推
        # （读哪一行由 cfg[15:6] 选）。
        rows = np.repeat(np.arange(h, dtype=np.uint32), w // 32)
        widx = np.tile(np.arange(w // 32, dtype=np.uint32), h)
        words = (rows << 16) | widx
        raw = words.astype("<u4").view(np.uint8).reshape(-1, 4)
        bits = np.unpackbits(raw, axis=1, bitorder="little")
        return (bits.reshape(h, w) * 255).astype(np.uint8)

    if pattern == "diag":
        img = _canvas(w, h)
        _border(img, 2)
        img[h // 2, :] = 255
        img[:, w // 2] = 255
        for x in range(0, w, 64):        # 顶边刻度
            img[2:12, x:min(x + 2, w)] = 255
        for y in range(0, h, 64):        # 左边刻度
            img[y:min(y + 2, h), 2:12] = 255
        img = _text(img, (18, 16), "LCD %dx%d" % (w, h), 40)
        img = _text(img, (18, 70), "1bpp  bit0=left", 26)
        img = _text(img, (18, 104), "row=%d words=%d" % (h, w // 32), 26)
        # 四角让出 8x8 方块，判断画面是否整体偏移
        for yy in (0, h - 12):
            for xx in (0, w - 12):
                img[yy + 2:yy + 12, xx + 2:xx + 12] = 255
        return img

    raise ValueError("unknown pattern %s" % pattern)


def load_image(path, w, h):
    """任意图片 -> 目标尺寸的 1bpp（Floyd–Steinberg 抖动），用于「直接渲染图片」验收。"""
    from PIL import Image
    im = Image.open(path).convert("L").resize((w, h), Image.LANCZOS)
    bw = im.convert("1")                      # PIL 默认 FLOYDSTEINBERG
    return (np.asarray(bw).astype(np.uint8)) * 255


def make_testchart(w, h):
    """几何测试图：圆形看长宽比、1px 竖线看像素级分辨率、方格看是否重复/错位。"""
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, w - 1, h - 1], outline=255, width=2)
    d.ellipse([w // 2 - h // 3, h // 2 - h // 3, w // 2 + h // 3, h // 2 + h // 3],
              outline=255, width=3)
    # 左边：每 2px 一条 1px 竖线（出现「变宽/粘连」就说明水平方向被拉伸）
    for x in range(0, w // 4, 2):
        d.line([x, 40, x, h - 40], fill=255)
    # 右边：每 32px 一条 3px 竖线（字边界；若整体偏移一宇会错开）
    for x in range(w // 4, w, 32):
        d.line([x, 40, x, h - 40], fill=255, width=3)
    # 顶部：每 8px 一格、每 32px 加粗
    for x in range(0, w, 8):
        d.line([x, 8, x, 24], fill=255, width=3 if x % 32 == 0 else 1)
    try:
        f = ImageFont.truetype(str(FONT), 56)
    except Exception:
        f = ImageFont.load_default()
    d.text((20, h // 2 - 30), "800x480 1bpp", fill=255, font=f)
    d.text((w - 210, h - 90), "END", fill=255, font=f)
    return np.asarray(im).copy()


def save_bin(words, path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(np.asarray(words, dtype="<u4").tobytes())
    return len(words) * 4


def _selftest():
    ok = True

    # 手工构造：64x32，第一行最左像素亮 -> word0 bit0=1
    img = np.zeros((32, 64), dtype=np.uint8)
    img[0, 0] = 255
    w = pack_1bpp(img, 64, 32)
    ok &= w.shape == (64,) and int(w[0]) == 1
    img[0, 31] = 255
    img[0, 32] = 255
    w = pack_1bpp(img, 64, 32)
    ok &= int(w[0]) == 0x80000001 and int(w[1]) == 1

    # 整行白
    w = pack_1bpp(np.full((32, 64), 255, np.uint8), 64, 32)
    ok &= w.shape == (64,) and int(w.min()) == 0xFFFFFFFF
    # 第二行第一个字
    img = np.zeros((32, 64), np.uint8)
    img[1, 0] = 255
    w = pack_1bpp(img, 64, 32)
    ok &= int(w[2]) == 1 and int(w[1]) == 0

    for size, (ww, hh) in SIZES.items():
        words = pack_1bpp(make_image("diag", ww, hh), ww, hh)
        n = (ww // 32) * hh
        ok &= words.shape == (n,)
        back = unpack_1bpp(words, ww, hh)
        ok &= int((back > 0).sum()) == int((make_image("diag", ww, hh) > 127).sum())

    # 端点：800x480 最右一列必须落在最后一字的 bit31
    img = np.zeros((480, 800), np.uint8)
    img[0, 799] = 255
    w = pack_1bpp(img, 800, 480)
    ok &= int(w[24]) == 0x80000000 and int(w[25]) == 0

    # 4bpp 自证图：行 R 字 0 必须是 8 份 (R&15)
    t4 = tag4_words(800, 480)
    ok &= t4.shape == (48000,)
    ok &= (int(t4[0]) == 0 and int(t4[1]) == 0x11111111
           and int(t4[15]) == 0xFFFFFFFF and int(t4[16]) == 0)
    ok &= int(t4[479 * 100]) == int(0x11111111 * (479 & 15))

    print("LCD_DATA_SELFTEST", "OK" if ok else "FAIL",
          "800=%d 480=%d" % (25 * 480, 15 * 272))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--size", choices=list(SIZES), default="800x480")
    ap.add_argument("--pattern", default="diag",
                    choices=["diag", "bars", "checker", "grid", "split", "white", "black", "chart", "map", "tag"])
    ap.add_argument("--image", help="直接渲染一张图片（任意格式，抖动成 1bpp）")
    ap.add_argument("--bpp", type=int, choices=[1, 4], default=1,
                    help="帧缓存格式：1=黑白（旧），4=16 色（LCD 现行）")
    ap.add_argument("--bin")
    ap.add_argument("--png")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()

    w, h = SIZES[args.size]
    if args.image:
        img = load_image(args.image, w, h)
        pattern = "image:" + Path(args.image).name
    elif args.pattern == "chart":
        img = make_testchart(w, h)
        pattern = "chart"
    else:
        img = make_image(args.pattern, w, h)
        pattern = args.pattern
    if args.bpp == 4:
        import bram_fb
        if pattern == "tag":
            words = np.concatenate([tag4_words(w, h), pal4_words()])
            img = tag4_png(w, h)
        else:
            bgr = np.asarray(img)[:, :, ::-1].copy() if np.asarray(img).ndim == 3                 else np.stack([img] * 3, axis=2)[:, :, ::-1].copy()
            iw, pw = bram_fb.pack_4bpp(bgr, w, h)
            words = np.concatenate([iw, pw])
            pal = np.stack([((pw >> 16) & 0xFF), ((pw >> 8) & 0xFF), (pw & 0xFF)], axis=1)
            idx = np.zeros((h, w), np.uint8)
            for kk in range(8):
                idx[:, kk::8] = ((iw >> (4 * kk)) & 0xF).reshape(h, w // 8)
            img = pal[idx.astype(int)].astype(np.uint8).astype(np.uint8)
    else:
        words = pack_1bpp(img, w, h)
    print("LCD_DATA size=%s pattern=%s bpp=%d words=%d nonzero=%d"
          % (args.size, pattern, args.bpp, len(words), int((words != 0).sum())))
    if args.bin:
        n = save_bin(words, args.bin)
        print("LCD_DATA_BIN %s (%d bytes)" % (args.bin, n))
    if args.png:
        from PIL import Image
        Path(args.png).parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(img).save(args.png)
        print("LCD_DATA_PNG %s" % args.png)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
