#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fb_diag.py —— 板子一上线就跑的「画面到底卡在哪」诊断（机器可判优先）。

背景（2026-10-30 20:3x）：
    hdmi_testpattern.bit（纯 RTL 彩条，不碰 DDR）→ 人眼看到 8 条彩条；
    flow_system.bit（fb_scanout 读 DDR 帧缓存）→ 人眼什么都看不到。
    ⇒ TMDS/时序/引脚/显示器/线都是好的，故障在「DDR 帧缓存 → fb_scanout」。

`fb_scanout` 的计数器只排除了两件事（且**不能**证明数据对）：
    frames 每秒 +60（vs 在跑）、under=0（每行开始前该行缓存就绪）
    —— 读回全零时 under 同样是 0，因为它是按「行是否就绪」而不是「像素是否非零」计的。

所以本脚本的核心是**不经缓存的那一次回读**：
    写完一屏纯色 → `flush()` → 用 /dev/mem（`MMIO`，非缓存）把同一物理地址读回，
    逐字与期望值比对。对上 ⇒ DRAM 里确实是我们的像素，问题在 PL/HP0 侧；
    对不上 ⇒ `flush()` 没生效（或写到的根本是另一块内存），与 PL 无关。
这一段完全不需要人眼。

人眼部分保留：红 → 绿 → 蓝黄交替，各一屏，用来给「读对了但屏上没有」这种情况定性。

用法：py build/fb_diag.py --wait 5400
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import run_board_acceptance as r  # noqa: E402

LOG = ROOT / "build" / "fb_diag.log"

BOARD_CODE = r'''
import sys, time
import numpy as np
from pynq import Overlay, MMIO
ov = Overlay('/home/xilinx/zynq-yolo/board/overlay/flow_system.bit')
sys.path.insert(0, '/home/xilinx/zynq-yolo/tools')
from hdmi_video import HdmiVideoOut, pack_frame, W, H

print('DIAG memtotal', open('/proc/meminfo').readline().strip())
out = HdmiVideoOut()
CFG = MMIO(0x41200000, 0x1000)
ST  = MMIO(0x41210000, 0x1000)
phys = [int(b.physical_address) for b in out.bufs]
print('DIAG bufs', ['0x%08X' % p for p in phys],
      'cacheable', [getattr(b, 'cacheable', '?') for b in out.bufs],
      'flush', [callable(getattr(b, 'flush', None)) for b in out.bufs])

def solid(bgr):
    img = np.zeros((H, W, 3), np.uint8); img[:, :] = bgr; return img

def show_and_check(img, tag, secs=4.0):
    out.show(img)
    exp = int(pack_frame(img).reshape(-1)[0])
    tgt = out.bufs[out.cur ^ 1]          # show() 结束后 cur 已指向下一个，上一帧在 cur^1
    addr = int(tgt.physical_address)
    try:
        mm = MMIO(addr, H * W * 4)
        ok = 0
        for k in range(16):
            off = (k * (H * W // 16)) * 4
            if int(mm.read(off)) & 0xFFFFFFFF == exp:
                ok += 1
        print('DIAG_RB %s addr=0x%08X expect=0x%06X match=%d/16' % (tag, addr, exp, ok))
    except Exception as e:
        print('DIAG_RB %s addr=0x%08X FAIL %s: %s' % (tag, addr, type(e).__name__, e))
    t0 = time.time(); n = 0
    while time.time() - t0 < secs:
        out.show(img); n += 1; time.sleep(0.1)
    v = int(ST.read(0)) & 0xFFFFFFFF
    print('DIAG_SHOW %s shown=%d frames=%d under=%d addr_reg=0x%08X pack=0x%08X'
          % (tag, n, (v >> 16) & 0xFFFF, v & 0xFFFF,
             int(CFG.read(0)) & 0xFFFFFFFF, int(CFG.read(8)) & 0xFFFFFFFF))

print('DIAG STEP1 整屏红 6s')
show_and_check(solid((0, 0, 255)), 'RED', 6)
print('DIAG STEP2 整屏绿 6s')
show_and_check(solid((0, 255, 0)), 'GREEN', 6)
print('DIAG STEP3 蓝/黄交替 10s')
t0 = time.time(); n = 0
while time.time() - t0 < 10:
    out.show(solid((255, 0, 0)) if n % 2 == 0 else solid((0, 255, 255)))
    n += 1; time.sleep(1.0)
v = int(ST.read(0)) & 0xFFFFFFFF
print('DIAG_END frames=%d under=%d' % ((v >> 16) & 0xFFFF, v & 0xFFFF))
print('DIAG_DONE 屏上应依次：红 → 绿 → 蓝黄交替')
'''


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def main():
    want = float(sys.argv[sys.argv.index("--wait") + 1]) if "--wait" in sys.argv else 5400
    t0 = time.time()
    while time.time() - t0 < want:
        if r.board_alive(timeout=25):
            break
        time.sleep(20)
    else:
        log("BOARD_NEVER_CAME_BACK; 什么都没做")
        return 2
    log("BOARD_ONLINE; 开始 fb 诊断")
    rc, out = r.run_board(["python3", "-c", BOARD_CODE], timeout=900)
    for ln in out:
        log("  " + ln)
    log("rc=%d" % rc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
