#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_lcd4_board.py —— 4bpp 读侧自证：写 tag4 → 读逐行抓取寄存器 → 对期望值。

为什么需要它：`lcd_probe.py` 能写帧缓存、能读回，但「PS 写什么、PL 就读什么」这件事
必须用**自带坐标的图案**去问读侧；PS 侧读写回环永远自洽（端口 A 两侧同向），抓不到
地址口径类的错（详见 `lcd_probe_core.v` 文件头与 `ai/sessions/021`）。

图案：帧缓存第 r 行第 k 字 = `0x11111111 * ((r+k) & 15)`，末尾 16 字是 16 色调色板。
判据：面板第 R 行的 `lb0/lbmid/st0/stmid` 必须等于第 R 行的期望值，且行 DE 拍数 = 800。
例外：R ∈ {0,1} 的 st/de 结构性取不到（装载拍在上一行；0 被 RTL 排除、1 的装载行是消隐行），
这两行只判 lb —— 必须写明例外，否则陈旧值会假通过。
末行 `READBACK_OK bad=0 checked=N` 或 `READBACK_FAIL ...`。

用法（主机侧，板子要在线）：
    py tools/check_lcd4_board.py
    py tools/check_lcd4_board.py --rows 0,1,15,240,479
"""
import argparse
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

BIT = "/home/xilinx/zynq-yolo/board/overlay/lcd_probe.bit"

CODE = r'''
import time
from pynq import Overlay, MMIO

Overlay("__BIT__")
print("OVERLAY_OK")

WPR, VD = 100, 480
mm = MMIO(0x40000000, 0x30000)      # 800x480 4bpp：192 KB（AXI 段开 256K）
cfg = MMIO(0x41220000, 0x1000)
d0 = MMIO(0x41230000, 0x1000)       # (lb0, lbmid)
d1 = MMIO(0x41240000, 0x1000)       # (st0, stmid)
d2 = MMIO(0x41250000, 0x1000)       # (fills, de_last)
tm = MMIO(0x41210000, 0x1000)       # pclk 自由计数器

cfg.write(0, 0x10)                   # 帧缓存 + 800x480 核心 + 背光亮

t0 = time.time()
for r in range(VD):
    base = r * WPR
    for k in range(WPR):
        mm.write(4 * (base + k), 0x11111111 * ((r + k) & 15))
pal = [0x000000, 0x0000AA, 0x00AA00, 0x00AAAA, 0xAA0000, 0xAA00AA, 0xAAAA00, 0xAAAAAA,
       0x555555, 0x5555FF, 0x55FF55, 0x55FFFF, 0xFF5555, 0xFF55FF, 0xFFFF55, 0xFFFFFF]
for i, p in enumerate(pal):
    mm.write(4 * (WPR * VD + i), p)
print("FB_WRITTEN words=%d pal=16 %.2f s" % (WPR * VD, time.time() - t0))

a = int(tm.read(8)); t1 = time.perf_counter(); time.sleep(0.25)
b = int(tm.read(8)); t2 = time.perf_counter()
print("PCLK %.3f MHz" % (((b - a) & 0xFFFFFFFF) / (t2 - t1) / 1e6))

mid = WPR // 2
rows = [int(x) for x in "__ROWS__".split(",") if x.strip()]
bad = 0
for R in rows:
    cfg.write(0, 0x10 | ((R & 0x3FF) << 6))
    time.sleep(0.12)
    lb0, lbmid = int(d0.read(0)), int(d0.read(8))
    st0, stmid = int(d1.read(0)), int(d1.read(8))
    cnt = int(d2.read(0))
    e0 = 0x11111111 * (R & 15)
    em = 0x11111111 * ((R + mid) & 15)
    de = cnt & 0xFFFF
    # st*/de 对 cap_row 0/1 是**结构性取不到**的，不是失败（2026-10-30 上板发现）：
    #   st/cnt 在「目标行的上一行」装载那一拍更新（hit_load），且 RTL 显式要求 cap_row != 0；
    #   于是 cap_row 0 永远不更新（陈旧值会假通过），cap_row 1 的装载行是场消隐行，
    #   de_last 取到消隐行 => 恒为 0。这两行只判 lb（内容）。
    # 另外 cnt 里的 de 实为「目标行上上方那一行」的 DE 拍数（见 lcd_probe_core.v 注释修正）。
    if R >= 2:
        ok = (lb0 == e0) and (lbmid == em) and (st0 == e0) and (stmid == em) and (de == 800)
    else:
        ok = (lb0 == e0) and (lbmid == em)
    bad += 0 if ok else 1
    print("ROW %3d %s%s lb=[%08X|%08X] exp=[%08X|%08X] st=[%08X|%08X] de=%d fills=%d"
          % (R, "OK " if ok else "BAD", "" if R >= 2 else "(仅lb)",
             lb0, lbmid, e0, em, st0, stmid, de, cnt >> 16))
cfg.write(0, 0x10)
print("READBACK_%s bad=%d checked=%d" % ("OK" if bad == 0 else "FAIL", bad, len(rows)))
'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bit", default=BIT)
    ap.add_argument("--rows", default="0,1,2,7,15,16,120,240,478,479")
    args = ap.parse_args()
    import board_jupyter
    code = CODE.replace("__BIT__", args.bit).replace("__ROWS__", args.rows)
    try:
        return board_jupyter.run(code)
    except SystemExit:
        raise
    except Exception as exc:                                  # noqa: BLE001
        print("LCD4_BOARD_FAIL %s: %s" % (type(exc).__name__, exc))
        print("（板子离线/掉线时就是这条；先上电重启，再确认 9090 端口通）")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
