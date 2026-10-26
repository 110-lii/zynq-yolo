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


def _selftest():
    img = np.zeros((H, W, 3), dtype=np.uint8)
    img[100:120, 200:240] = 255
    words = pack_1bpp(img)
    lcd = pack_1bpp(np.zeros((480, 800, 3), dtype=np.uint8), 800, 480)
    ok = (words.shape == (TOTAL_WORDS,)
          and int(words[100 * WORDS_PER_ROW + 200 // 32]) != 0
          and int(words[0]) == 0
          and lcd.shape == (12000,))
    print("BRAM_FB_SELFTEST", "OK" if ok else "FAIL", words.shape, lcd.shape,
          hex(int(words[100 * WORDS_PER_ROW + 6])))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        raise SystemExit(_selftest())
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())