#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hdmi_geom_probe.py —— 用「八条横带 / 八条竖带」把画面几何一次问清楚。

背景（2026-10-30 23:4x 第一次上板，2026-10-31 补充）
---------------------------------------------------
彩条（不碰 DDR）人眼确认，含左侧按行灰阶 ⇒ TMDS、像素时钟、行推进都通。
切回帧缓存后，本该是「左上 128x128 白块」的画面被描述成
「左边白、其余黑、白里有黑横条、还在闪」。

把 BLOCK 那次的累加和反解（`sum = M·word mod 2^32`，word 只有 0 和 0xFFFFFF）
得到 **M = 92,978 个白像素**，而正确的 BLOCK 只有 16,384 个白像素。
92,978 / 720 行 ≈ 129 列/行 ⇒ 屏幕上**每一行**的左边 128 列都是白的 ——
和「左边白、其余黑」逐字对上，所以「哪一行送到屏幕的哪一行」确实错了，
而且不是「少数几行错」，是**每一行都读到了靠前的行**。

本次上板要一次问清三件事
------------------------
1. 行映射：8 条横带，每条 90 行、颜色各不相同。8 条等宽顺序正确 ⇒ 行映射对；
   条数变少/重复/厚度不均 ⇒ 行号在打滑；整屏一个颜色 ⇒ 所有行读同一条行缓存。
2. 列映射：8 条竖带同理。
3. 取数是否落后：读 `0x41290000`（HDMI dbg，vs 沿锁存）
       [10:0]  under_lines   本帧「行首没数据」的行数（正确 0）
       [21:11] behind_lines  本帧「取数落后于显示」的行数（正确 0）
       [31:22] last_disp     本帧最后显示到的行号（正确 719）
   两者都 0 而行号 719 ⇒ 显示与取数都在正常推进，错只能在地址（stride/base）
   或行缓存下标上；behind>0 ⇒ 取数被显示甩开，屏幕上是旧行缓存。

累加和的期望值一律按**屏幕口径**算（显示列 0/1/2 都是源列 0，源列 1278/1279
永远不显示），这也是 `st_sum` 现在的累加口径（2026-10-31 改成 video_de 对齐）。

用法：py build/hdmi_geom_probe.py --wait 300
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
from pynq import Overlay, MMIO
ov = Overlay('/home/xilinx/zynq-yolo/board/overlay/flow_system.bit')
sys.path.insert(0, '/home/xilinx/zynq-yolo/tools')
from hdmi_video import HdmiVideoOut, pack_frame, W, H

out = HdmiVideoOut()
ST, CFG = out.st, out.cfg
DBG = MMIO(0x41290000, 0x10000)

def rd():
    v = int(ST.read(0)) & 0xFFFFFFFF
    return (v >> 16) & 0xFFFF, v & 0xFFFF, int(ST.read(8)) & 0xFFFFFFFF

def dbg():
    v = int(DBG.read(0)) & 0xFFFFFFFF
    return dict(under=v & 0x7FF, behind=(v >> 11) & 0x7FF, last_disp=(v >> 22) & 0x3FF)

# 累加口径 = 屏幕口径：显示列 0/1/2 都取源列 0，源列 1278/1279 不显示
COLS = [0, 0, 0] + list(range(1, W - 2))
def expect(words):
    w = np.asarray(words, dtype=np.uint64) & np.uint64(0xFFFFFF)
    return int(w[:, COLS].sum() & np.uint64(0xFFFFFFFF))

def solid(bgr):
    img = np.zeros((H, W, 3), np.uint8); img[:, :] = bgr; return img

def report(tag, img, note=''):
    words = pack_frame(img)
    e = expect(words)
    out.show(img)
    time.sleep(0.45)
    fr, un, got = rd()
    d = dbg()
    print('DIAG_SUM %-7s expect=%08X got=%08X %-8s frames=%d under=%d dbg(under=%d behind=%d last=%d) %s'
          % (tag, e, got, 'MATCH' if got == e else 'MISMATCH', fr, un,
             d['under'], d['behind'], d['last_disp'], note), flush=True)

print('DIAG_CFG_READBACK addr=%08X pack=%08X' %
      (int(CFG.read(0)) & 0xFFFFFFFF, int(CFG.read(8)) & 0xFFFFFFFF), flush=True)

# ---------------- 1. 两个「已知会说话」的图案 ----------------
img = np.zeros((H, W, 3), np.uint8)
img[0:128, 0:128] = (255, 255, 255)
report('BLOCK', img, '正确显示时白像素应恰好 128*(128+2)=16640')

img = np.zeros((H, W, 3), np.uint8)
yy = np.arange(H, dtype=np.uint32) & 0xFF
img[:, :, 2] = yy[:, None].astype(np.uint8)          # r = y&0xFF，整行同色
report('VRAMP', img, '按行渐变；整屏同色 ⇒ 所有行读同一行')

# ---------------- 2. 八条横带（行映射） ----------------
BANDS = [(255,255,255), (0,255,255), (0,255,0), (255,255,0),
         (0,0,255), (255,0,0), (255,0,255), (0,0,0)]      # BGR
img = np.zeros((H, W, 3), np.uint8)
step = H // 8
for i, c in enumerate(BANDS):
    img[i*step:(i+1)*step] = c
print('DIAG_HBANDS 从上到下应为：白 青 绿 黄 蓝 红 品红 黑（8 条等宽）', flush=True)
report('HBANDS', img, '人眼核：条数=8、顺序对不对、厚度是否均匀')
print('DIAG_HBANDS_HOLD 40s', flush=True)
time.sleep(40)
print('DIAG_HBANDS_DBG', dbg(), flush=True)

# ---------------- 3. 八条竖带（列映射） ----------------
img = np.zeros((H, W, 3), np.uint8)
wstep = W // 8
for i, c in enumerate(BANDS):
    img[:, i*wstep:(i+1)*wstep] = c
print('DIAG_VBANDS 从左到右应为：白 青 绿 黄 蓝 红 品红 黑（8 条等宽）', flush=True)
report('VBANDS', img, '人眼核：条数=8、顺序对不对、第 1 条是否比其余宽 2 像素')
print('DIAG_VBANDS_HOLD 40s', flush=True)
time.sleep(40)
print('DIAG_VBANDS_DBG', dbg(), flush=True)

# ---------------- 4. 静止画面累加和的稳定性 ----------------
vals, ds = [], []
for k in range(6):
    vals.append(rd()[2]); ds.append(dbg())
    time.sleep(0.25)
print('DIAG_STABLE', ' '.join('%08X' % v for v in vals),
      'distinct=%d' % len(set(vals)), flush=True)
print('DIAG_STABLE_DBG', ds, flush=True)

# ---------------- 5. 三个角标（人眼几何锚点） ----------------
img = np.zeros((H, W, 3), np.uint8)
img[0:180, 0:320] = (255, 255, 255)
img[0:180, W-320:W] = (0, 0, 255)
img[H-180:H, 0:320] = (255, 0, 0)
out.show(img)
print('DIAG_MARKERS_ON 90s：左上白、右上红、左下蓝，其余黑', flush=True)
t0 = time.time()
while time.time() - t0 < 90:
    time.sleep(10)
    print('DIAG_MARK_TICK', rd(), dbg(), flush=True)
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
    rc, out = r.run_board(["python3", "-c", BOARD_CODE], timeout=900)
    for ln in out:
        log("  " + ln)
    log("rc=%d" % rc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
