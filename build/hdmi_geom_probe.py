#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hdmi_geom_probe.py —— 用「八条横带 / 八条竖带」把画面几何一次问清楚。

背景（2026-10-30 23:4x）：
    彩条（不碰 DDR）人眼确认，含左侧按行灰阶 ⇒ TMDS、像素时钟、行推进都通。
    切回帧缓存后，本该是「左上 128x128 白块」的画面被描述成
    「左边白、其余黑、白里有黑横条、还在闪」。
    累加和已经证明 **DDR 里读回来的像素值是对的**，所以问题只可能在
    「哪一行/哪一列被送到屏幕的哪个位置」——也就是行缓存与行号的映射。

为什么用横带：把 720 行分成 8 条、每条 90 行、颜色各不相同。
    · 8 条等宽、从上到下顺序正确 ⇒ 行映射对，问题在别处；
    · 条数变少/重复/厚度不均 ⇒ 行号（fetch_line / disp_line）在打滑；
    · 整屏一个颜色 ⇒ 所有行都在读同一行缓存（老缺陷复发）。
竖带同理，用来单独看列。

同时做一次「静止画面的累加和稳定性」测量：同一幅图连读 6 次累加和，
正常应完全不变；变化量直接给出「有多少像素在帧间漂移」。

用法：py build/hdmi_geom_probe.py --wait 300 --hold 60
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import run_board_acceptance as r  # noqa: E402

LOG = ROOT / "build" / "hdmi_geom_probe.log"

BOARD_CODE = r'''
import sys, time
import numpy as np
from pynq import Overlay
ov = Overlay('/home/xilinx/zynq-yolo/board/overlay/flow_system.bit')
sys.path.insert(0, '/home/xilinx/zynq-yolo/tools')
from hdmi_video import HdmiVideoOut, pack_frame, W, H

out = HdmiVideoOut()
ST, CFG = out.st, out.cfg

def rd():
    v = int(ST.read(0)) & 0xFFFFFFFF
    return (v >> 16) & 0xFFFF, v & 0xFFFF, int(ST.read(8)) & 0xFFFFFFFF

BANDS = [(255,255,255),(0,255,255),(0,255,0),(255,255,0),
         (0,0,255),(255,0,0),(255,0,255),(0,0,0)]     # BGR
print('DIAG_BANDS_DESC 从上到下：白 青 绿 黄 蓝 红 品红 黑（BGR 顺序）')

img = np.zeros((H, W, 3), np.uint8)
step = H // 8
for i, c in enumerate(BANDS):
    img[i*step:(i+1)*step] = c
out.show(img)
time.sleep(0.5)
print('DIAG_HBANDS_ON 40s', rd(), flush=True)
time.sleep(40)

img = np.zeros((H, W, 3), np.uint8)
wstep = W // 8
for i, c in enumerate(BANDS):
    img[:, i*wstep:(i+1)*wstep] = c
print('DIAG_BANDS_DESC 从前往后：白 青 绿 黄 蓝 红 品红 黑')
out.show(img)
time.sleep(0.5)
print('DIAG_VBANDS_ON 40s', rd(), flush=True)
time.sleep(40)

# 静止画面的累加和稳定性：同一幅图连读 6 次
vals = []
for k in range(6):
    vals.append(rd()[2])
    time.sleep(0.25)
print('DIAG_STABLE', ' '.join('%08X' % v for v in vals),
      'distinct=%d' % len(set(vals)), flush=True)

# 画面上留一个人能同时核对的标记：白块在左上、红块在右上、蓝块在左下
img = np.zeros((H, W, 3), np.uint8)
img[0:180, 0:320] = (255, 255, 255)
img[0:180, W-320:W] = (0, 0, 255)
img[H-180:H, 0:320] = (255, 0, 0)
out.show(img)
print('DIAG_MARKERS_ON 90s：左上白、右上红、左下蓝，其余黑', flush=True)
t0 = time.time()
while time.time() - t0 < 90:
    time.sleep(10)
    print('DIAG_MARK_TICK', rd(), flush=True)
print('DIAG_DONE')
'''


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def main():
    want = float(sys.argv[sys.argv.index("--wait") + 1]) if "--wait" in sys.argv else 300
    t0 = time.time()
    while time.time() - t0 < want:
        if r.board_alive(timeout=25):
            break
        time.sleep(20)
    else:
        log("BOARD_NEVER_CAME_BACK")
        return 2
    rc, out = r.run_board(["python3", "-c", BOARD_CODE], timeout=600)
    for ln in out:
        log("  " + ln)
    log("rc=%d" % rc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
