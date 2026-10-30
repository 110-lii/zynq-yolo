#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hdmi_ddr_diag.py —— 把「DDR 帧缓存这一段到底通不通」变成机器判据。

为什么还要再做一次诊断
----------------------
2026-10-30 夜里的三处缺陷（`ai/sessions/022` §7.13、§7.15）都修完、配置寄存器
写-读闭环通过、`frames` 60/s、`under=0`；屏上却是一个不随帧缓存内容变化的颜色。
剩下的可能性只有两类，**现有计数器一个都分不开**：

    A. PL 从 DDR 读回来的就不是我们写的像素（HP0 / 地址 / 缓存）；
    B. DDR 读对了，是 TMDS 那一段把画面送坏了。

`frames` 只证明 vs 在跑，`under=0` 只证明行缓存**就绪**（§7.12 第 5 条）。
所以这一版 RTL 加了两个正交探针，本脚本就是它们的用法：

  诊断 1（机器判）：`hdmi_video_top` 把本帧显示用到的像素逐个累加，vs 上升沿
      锁存，走 `gpio_hdmi_st` 的**通道 2**（0x41210008）。主机侧按同一公式算
      期望值逐位比对 ⇒ 判 A。
  诊断 2（人眼判）：`cfg_pack[28]=1` 让 TMDS 收**本模块生成的彩条**（不碰 DDR，
      左侧第一段是按行灰阶）。彩条出得来 ⇒ 链路好、问题在 A；出不来 ⇒ 反过来。

探针分三组，一组比一组能定位：
  1. `Σ = 1 / 2 / 0x10000`：先把「累加的像素个数」和「数值是否逐位相等」分开。
     整屏写 1 时，正确的和应当**正好等于** 1280×720 = 0x000E1000。
  2. 竖向斜坡（第 y 行写 y）：行推进一旦退化成「每行读第 0 行」，这个和必然塌。
  3. 左上角 128×128 白块：几何位置也能算准，不受列映射细节影响。

用法：
    py build/hdmi_ddr_diag.py --wait 600
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import run_board_acceptance as r  # noqa: E402

LOG = ROOT / "build" / "hdmi_ddr_diag.log"

BOARD_CODE = r'''
import sys, time
import numpy as np
from pynq import Overlay
ov = Overlay('/home/xilinx/zynq-yolo/board/overlay/flow_system.bit')
sys.path.insert(0, '/home/xilinx/zynq-yolo/tools')
from hdmi_video import HdmiVideoOut, pack_frame, W, H

out = HdmiVideoOut()
ST = out.st
CFG = out.cfg
PIX = W * H
base_pack = out.pack

def rd():
    v = int(ST.read(0)) & 0xFFFFFFFF
    s = int(ST.read(8)) & 0xFFFFFFFF      # 通道 2：上一整帧的像素累加和
    return (v >> 16) & 0xFFFF, v & 0xFFFF, s

def solid(bgr):
    img = np.zeros((H, W, 3), np.uint8); img[:, :] = bgr; return img

def show_then(img, secs=0.35):
    out.show(img)
    time.sleep(secs)                      # 20 帧左右，保证整帧扫完、累加和已锁存

def expect(words, cols=None):
    w = np.asarray(words, dtype=np.uint64) & np.uint64(0xFFFFFF)
    if cols is not None:
        w = w[:, cols]
    return int(w.sum() & np.uint64(0xFFFFFFFF))

# 列映射：xpos 0/1/2 都落到第 0 列，最右两列不显示 —— 两种口径都算，任一命中即 MATCH
COLS_DEDUP = [0, 0, 0] + list(range(1, W - 2))
def expect_both(words):
    return expect(words, COLS_DEDUP), expect(words, None)

def report(tag, img, note=''):
    words = pack_frame(img)
    e_dedup, e_plain = expect_both(words)
    show_then(img)
    fr, un, got = rd()
    hit = 'MATCH' if got in (e_dedup, e_plain) else 'MISMATCH'
    print('DIAG_SUM %-6s expect=%08X/%08X got=%08X %-8s frames=%d under=%d %s'
          % (tag, e_dedup, e_plain, got, hit, fr, un, note))
    return got, hit, fr, un

print('DIAG_OPEN', rd(), 'bufs', ['0x%08X' % int(b.physical_address) for b in out.bufs])

print('--- 组 1：整屏常数，先把「累加了多少个像素」定下来 ---')
report('C1', solid((1, 0, 0)), '整屏 word=0x000001，正确的和必须正好是 1280*720=0x000E1000')
report('C2', solid((2, 0, 0)), '整屏 word=0x000002，应为 C1 的两倍')
report('CR1', solid((0, 0, 1)), '整屏 word=0x010000，应为 0xE1000<<16')
for tag, bgr in [('RED', (0, 0, 255)), ('GREEN', (0, 255, 0)),
                 ('BLUE', (255, 0, 0)), ('WHITE', (255, 255, 255))]:
    report(tag, solid(bgr))

print('--- 组 2：竖向斜坡（第 y 行写 y） —— 专门盯行推进 ---')
img = np.zeros((H, W, 3), np.uint8)
yy = np.arange(H, dtype=np.uint32) & 0xFF
img[:, :, 2] = yy[:, None].astype(np.uint8)          # r = y&0xFF
report('VRAMP', img, '每行读第 0 行 ⇒ 和会塌成一个常数倍')

print('--- 组 3：左上 128x128 白块（几何） ---')
img = np.zeros((H, W, 3), np.uint8)
img[0:128, 0:128] = (255, 255, 255)
report('BLOCK', img)

# ---------------- 诊断 2：不碰 DDR 的内置彩条 ----------------
CFG.write(8, base_pack | (1 << 28))
print('DIAG_BARS_ON  120s：屏上应是 8 段竖彩条（最左一段是按行灰阶），'
      '与帧缓存内容无关', flush=True)
t0 = time.time()
while time.time() - t0 < 120:
    time.sleep(8)
    fr, un, s = rd()
    print('DIAG_BARS_TICK frames=%d under=%d sum=%08X' % (fr, un, s), flush=True)
CFG.write(8, base_pack)
print('DIAG_BARS_OFF 恢复帧缓存输出')
print('DIAG_DONE')
'''


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def main():
    want = float(sys.argv[sys.argv.index("--wait") + 1]) if "--wait" in sys.argv else 600
    t0 = time.time()
    while time.time() - t0 < want:
        if r.board_alive(timeout=25):
            break
        time.sleep(20)
    else:
        log("BOARD_NEVER_CAME_BACK; 什么都没做")
        return 2
    log("BOARD_ONLINE; 开始 HDMI/DDR 诊断")
    rc, out = r.run_board(["python3", "-c", BOARD_CODE], timeout=900)
    for ln in out:
        log("  " + ln)
    log("rc=%d" % rc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
