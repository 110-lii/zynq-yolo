#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bram_fb.py —— 把状态面板写成 1bpp 并写入 HDMI 帧缓存（AXI BRAM）。

帧缓存布局（`src/rtl/io/hdmi/fb_reader.v`）：
  1280x720，每行 40 个 32-bit 字，共 28,800 字；bit0 = 最左像素。

板上：
  from bram_fb import write_image
  write_image(img_bgr, base=0x40000000)

优先用 PYNQ `MMIO`；没有 PYNQ 时用 `/dev/mem`（需要 root）。
自检（不需要板子）：
  python tools/bram_fb.py --selftest
  # 末行 BRAM_FB_OK
"""
import argparse
import mmap
import os
import sys
import tempfile

sys.dont_write_bytecode = True

import numpy as np

W, H = 1280, 720
WORDS_PER_ROW = W // 32
TOTAL_WORDS = WORDS_PER_ROW * H


def pack_1bpp(img_bgr, w=W, h=H):
    """BGR/灰度图 -> ((w//32)*h,) uint32；亮度 > 128 记为白。"""
    gray = img_bgr
    if getattr(img_bgr, "ndim", 2) == 3:
        gray = (0.114 * img_bgr[:, :, 0] + 0.587 * img_bgr[:, :, 1]
                + 0.299 * img_bgr[:, :, 2])
    g = np.asarray(gray, dtype=np.float32)
    if g.shape[0] != h or g.shape[1] != w:
        raise ValueError("expect %dx%d, got %s" % (w, h, g.shape))
    bits = (g > 128).astype(np.uint8)
    packed = np.packbits(bits, axis=1, bitorder="little")
    return packed.view("<u4").reshape((w // 32) * h)


def _write_mmio(words, base):
    try:
        from pynq import MMIO
        mm = MMIO(int(base), len(words) * 4)
        for i, v in enumerate(words):
            mm.write(i * 4, int(v))
        return "PYNQ"
    except Exception:
        pass
    fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
    try:
        mm = mmap.mmap(fd, len(words) * 4, offset=int(base))
        mm.write(words.tobytes())
        mm.flush()
        mm.close()
        return "DEVMEM"
    finally:
        os.close(fd)


def write_image(img_bgr, base=0x40000000, w=W, h=H):
    words = pack_1bpp(img_bgr, w, h)
    return _write_mmio(words, base)


def gold_bits(w=800, h=480):
    """仿真 TB 里「单 bit 漂移」图案的位图（h x w, 0/1）。

    两边必须逐字一致（TB：`bmem[row * wpr + col] = 1 << ((row + col) % 32)`）：
    一个 bit 在字内随行逐字漂移，bit0 落在最左像素、bit31 落在最右像素，
    因此能同时校验字序与位序。
    """
    wpr = w // 32
    bits = np.zeros((h, w), dtype=np.uint8)
    for row in range(h):
        for k in range(wpr):
            word = 1 << ((row + k) % 32)
            for b in range(32):
                bits[row, k * 32 + b] = (word >> b) & 1
    return bits


def verify_frame(path, w=800, h=480):
    """把 RTL 仿真 dump 的帧（每像素 '0'/'1'）与 Python 侧 pack_1bpp 的结果对拍。

    这是「Python 打包」与「RTL 读数」之间唯一的位序接口，必须独立验一次。
    LCD（800x480，25 字/行）与 HDMI（1280x720，40 字/行）用同一个判据。
    令牌：BRAM_FB_FRAME_OK / BRAM_FB_FRAME_FAIL
    """
    wpr = w // 32
    rows = []
    with open(path, "r", encoding="ascii") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(np.frombuffer(line.encode("ascii"), dtype=np.uint8) - 48)
    if not rows or len(rows) != h or any(r.size != w for r in rows):
        print("BRAM_FB_FRAME_FAIL bad_shape rows=%d %dx%d" % (len(rows), w, h))
        return 1
    got = np.array(rows, dtype=np.uint8)

    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[gold_bits(w, h).astype(bool)] = 255
    words = pack_1bpp(img, w, h)
    exp = np.zeros((h, w), dtype=np.uint8)
    for row in range(h):
        for k in range(wpr):
            for b in range(32):
                exp[row, k * 32 + b] = (int(words[row * wpr + k]) >> b) & 1

    diff = int(np.count_nonzero(got != exp))
    if diff == 0:
        print("BRAM_FB_FRAME_OK %dx%d 0 mismatches" % (w, h))
        return 0
    print("BRAM_FB_FRAME_FAIL mismatches=%d (%dx%d)" % (diff, w, h))
    return 1



# ---------------------------------------------------------------------------
# 4bpp（16 色）+ 每屏自带调色板 —— LCD 显示通路的现行格式
#
# 为什么是这样：
#   * 800x480 的 1bpp 只有黑白，必须靠 1:1 像素把字画清楚；一上抗锯齿，二值化就把
#     细笔画整段吃掉（「字看起来糊」）。4bpp 之后颜色直接上屏，不再二值化。
#   * 16 色够不够取决于**每屏用什么 16 色**。所以调色板不写死，而是每屏由
#     `pack_4bpp` 按图像自己切出来（PIL 中位切分，无抖动），附带写进帧缓存末尾
#     的 16 个字；RTL 每帧场消隐期把它们读进寄存器 → 换配色不用重出片。
#   * 索引位序与 RTL 一致：每字 8 像素，**最低 4 位 = 最左像素**。
# ---------------------------------------------------------------------------
PAL_N = 16


def quantize_palette(img_bgr, n=PAL_N):
    """BGR 图 -> (索引图 HxW uint8, 调色板 [(r,g,b)] * n)。

    用 PIL 的中位切分、关抖动：图像本身 ≤ n 种颜色时**逐色精确**（我们自己的
    界面就是这种），否则取最优的 n 色近似。
    """
    from PIL import Image as _Image
    rgb = np.asarray(img_bgr, dtype=np.uint8)[:, :, ::-1]
    im = _Image.fromarray(rgb, "RGB")
    q = im.quantize(colors=n, method=_Image.MEDIANCUT, dither=_Image.Dither.NONE)
    idx = np.asarray(q, dtype=np.uint8)
    raw = list(q.getpalette() or [])
    raw += [0] * (3 * n - len(raw))        # 图里色数不足时尾部补 0（黑）
    pal = [(int(raw[3 * i]), int(raw[3 * i + 1]), int(raw[3 * i + 2])) for i in range(n)]
    return idx, pal


def _nearest_index(img_bgr, palette):
    """BGR 图 → 最近调色板索引（uint8, h×w）。

    用 32³ 的查找表：把 RGB 各取高 5 位当格号。表按调色板内容缓存，
    换色板才重算（32768×16 次平方距离，几毫秒）。
    """
    pal = np.asarray([[int(c) & 0xFF for c in p] for p in list(palette)[:PAL_N]],
                     dtype=np.int32)
    arr = np.asarray(img_bgr)
    r = arr[:, :, 2].astype(np.uint32)
    g = arr[:, :, 1].astype(np.uint32)
    b = arr[:, :, 0].astype(np.uint32)
    packed = (r << 16) | (g << 8) | b
    uniq, inv = np.unique(packed, return_inverse=True)
    ur = ((uniq >> 16) & 0xFF).astype(np.int32)
    ug = ((uniq >> 8) & 0xFF).astype(np.int32)
    ub = (uniq & 0xFF).astype(np.int32)
    best = np.zeros(uniq.size, dtype=np.uint8)
    bestd = np.full(uniq.size, 1 << 30, dtype=np.int32)
    for i in range(pal.shape[0]):                    # 16 次向量比较，逐色**精确**
        d = (ur - pal[i, 0]) ** 2 + (ug - pal[i, 1]) ** 2 + (ub - pal[i, 2]) ** 2
        m = d < bestd
        bestd = np.where(m, d, bestd)
        best = np.where(m, np.uint8(i), best)
    return best[inv.reshape(-1)].reshape(arr.shape[0], arr.shape[1])


def pack_4bpp(img_bgr, w, h, palette=None):
    """BGR 图 -> (图像字, 调色板字)。各 ((w//8)*h,) / (16,) uint32。

    字 = 8 个 4-bit 索引，idx0 在最低 4 位；调色板字 = 0x00RRGGBB。
    """
    if palette is None:
        idx, palette = quantize_palette(img_bgr, PAL_N)
    else:
        # 指定调色板时按最近色落位（界面自己给了色表的情况）
        #
        # ★ 2026-10-31 面板刷新慢到 3.6 s/帧，就是这一行：
        #   `rgb[:,:,None,:] - pal[None,None,:,:]` 会当场开一个
        #   h×w×16×3 的 int32 数组（480×800 时 73 MB），A9 上光分配+遍历就要几秒。
        #   改成**按 5-3-2 位量化的查找表**：32³ 个格子 × 16 色的距离只在
        #   调色板变化时算一次，之后每像素只是一次整数索引。
        #   量化误差只在相邻色的中点附近（调色板里 10 种球衣色彼此离得远），
        #   而界面本身是用**调色板里的原色**画的，原色落格仍然精确。
        idx = _nearest_index(img_bgr, palette)
    if idx.shape[0] != h or idx.shape[1] != w:
        raise ValueError("expect %dx%d, got %s" % (w, h, idx.shape))
    words = np.zeros((w // 8) * h, dtype=np.uint32)
    for k in range(8):
        words |= (idx[:, k::8].astype(np.uint32).reshape(-1) & 0xF) << (4 * k)
    pal_words = np.array([((r & 0xFF) << 16) | ((g & 0xFF) << 8) | (b & 0xFF)
                          for (r, g, b) in palette[:PAL_N]], dtype=np.uint32)
    return words, pal_words


def write_image4(img_bgr, base=0x40000000, w=800, h=480, palette=None):
    """4bpp 写屏：图像字在前，16 个调色板字紧跟其后（RTL 的 PAL_BASE）。

    返回 (mode, palette)，调用方可以把调色板一起打进日志。
    """
    words, pal = pack_4bpp(img_bgr, w, h, palette)
    mode = _write_mmio(np.concatenate([words, pal]), base)
    return mode, [(int((x >> 16) & 0xFF), int((x >> 8) & 0xFF), int(x & 0xFF))
                  for x in pal]


def _selftest():
    img = np.zeros((H, W, 3), dtype=np.uint8)
    img[100:120, 200:240] = 255
    words = pack_1bpp(img)
    lcd = pack_1bpp(np.zeros((480, 800, 3), dtype=np.uint8), 800, 480)
    ok = (words.shape == (TOTAL_WORDS,)
          and int(words[100 * WORDS_PER_ROW + 200 // 32]) != 0
          and int(words[0]) == 0
          and lcd.shape == (12000,))

    t = tempfile.mkdtemp(prefix="bram_fb_")
    frame_ok = True
    for (w, h) in ((800, 480), (1280, 720)):
        dump = os.path.join(t, "frame_%dx%d.txt" % (w, h))
        with open(dump, "w", encoding="ascii") as f:
            for row in gold_bits(w, h):
                f.write("".join("1" if v else "0" for v in row) + "\n")
        frame_ok = frame_ok and verify_frame(dump, w, h) == 0

    # 4bpp：索引位序、调色板字、以及「与我们自己的界面同色时逐色精确」
    img4 = np.zeros((480, 800, 3), dtype=np.uint8)
    img4[:, :, 0] = 200                       # B
    img4[0, 0:8] = (0, 0, 0)
    w4, p4 = pack_4bpp(img4, 800, 480)
    ok4 = (w4.shape == (48000,) and p4.shape == (PAL_N,)
           and int(w4[0]) != int(w4[1])         # 第 1 个字（px 8..15）全同一个索引、与首字不同
           and len(set(int(x) for x in w4[:500])) <= 2)

    print("BRAM_FB_SELFTEST", "OK" if (ok and frame_ok and ok4) else "FAIL",
          words.shape, lcd.shape, "frame_ok=%s" % frame_ok,
          hex(int(words[100 * WORDS_PER_ROW + 6])))
    return 0 if (ok and frame_ok) else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--verify-frame", default="",
                    help="比对 RTL 仿真 dump 的 '0'/'1' 帧（LCD 800x480 / HDMI 1280x720）")
    ap.add_argument("--size", default="800x480", help="--verify-frame 用的分辨率，如 1280x720")
    args = ap.parse_args()
    if args.selftest:
        raise SystemExit(_selftest())
    if args.verify_frame:
        w, h = (int(v) for v in str(args.size).lower().split("x", 1))
        raise SystemExit(verify_frame(args.verify_frame, w, h))
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())