#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hdmi_pixel_probe.py —— 一个像素一个像素地问：「源 (r,c) 这个点亮起来，落在屏上第几列？」

为什么换这套仪器：整帧累加和 / 位置加权和 / sel5 / sel6 几个读数互相打架
（列恒等映射读成 511 也读成 149），说明这些「整帧统计量」被别的东西污染了。
最干净的探针是**只点亮一个像素、且值 = 1**：
    sum    = 这个源像素在屏上出现了几次（0 = 丢了，1 = 正常，2 = 重复）
    possum = Σ(落到的屏列号 + 1)   ⇒ 直接读出它落在哪一列
屏上正确的映射下，源 (r,c) 应当落在第 c 列 ⇒ sum=1, possum=c+1。

令牌：PIX <r> <c> sum=<n> px=<屏列> OK|SHIFT|LOST|DUP
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import run_board_acceptance as r  # noqa: E402

LOG = ROOT / "build" / "hdmi_pixel_probe.log"

BOARD_CODE = r'''
import sys, time
import numpy as np
from pynq import Overlay, MMIO
ov = Overlay('/home/xilinx/zynq-yolo/board/overlay/flow_system.bit')
sys.path.insert(0, '/home/xilinx/zynq-yolo/tools')
from hdmi_video import HdmiVideoOut, W, H
out = HdmiVideoOut(); st = MMIO(0x41210000, 0x1000)
M32 = 0xFFFFFFFF

def rd(sel):
    out.cfg.write(0x8, (out.pack & 0x1FFFFFFF) | (int(sel) << 29))
    time.sleep(0.22)
    return int(st.read(8)) & M32

black = np.zeros((H, W, 3), np.uint8)
out.show(black); time.sleep(0.4)

def probe(r, c):
    img = np.zeros((H, W, 3), np.uint8)
    img[r, c, 0] = 1
    out.show(img); time.sleep(0.30)
    s = rd(0); p = rd(7)
    out.cfg.write(0x8, out.pack)
    n = s
    if n == 1:
        px = p - 1
        tag = 'OK' if px == c else 'SHIFT'
        print('PIX r=%3d c=%4d sum=1 px=%4d %s' % (r, c, px, tag), flush=True)
    elif n == 0:
        print('PIX r=%3d c=%4d sum=0 px=---- LOST' % (r, c), flush=True)
    else:
        print('PIX r=%3d c=%4d sum=%d possum=%08X DUP' % (r, c, n, p), flush=True)

for c in range(0, W, 64):
    probe(300, c)
probe(300, W - 1)
for r in (0, 1, 2, 3, 719, 718, 717):
    probe(r, 640)
print('PIX_DONE', flush=True)
'''


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def main():
    if not r.board_alive(timeout=25):
        log("BOARD_OFFLINE")
        return 2
    rc, out = r.run_board(["python3", "-c", BOARD_CODE], timeout=600)
    for ln in out:
        log("  " + ln)
    log("rc=%d" % rc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
