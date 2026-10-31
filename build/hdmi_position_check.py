#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hdmi_position_check.py —— 把「屏第几列显示的是源第几列」变成机器判据（不再靠人眼照片）

为什么需要它
------------
整帧像素累加和是**与顺序无关**的量：任何整行循环移位在它眼里都是同一个数。
2026-10-31 这一天，机器判据（累加和 / under / frames）全绿、而屏上的图整整转了 1024 列
（用户照片 + 念数字 8 9 0 1 2 3 4 5 6 7 定死），根因就是「位置没有机器口径」。

★ 读回口：`0x41290000` 实测读不到（读到的是 gpio_hdmi_st），诊断量走 **`0x41210008`**
（st 的第二通道；`cfg_pack[31:29] == 0` 时那里仍是累加和）。用 `cfg_pack[31:29]` 选：

    2  本行第 0 个 data_req 拍上的 rd_idx
    5  帧首「写 row0 第 0 列」那一拍的数据        （取数流起点源列 = 值 − 1）
    6  屏幕第 511 列实际送出的像素                （该列源列 = 值 − 1）
    7  位置加权和 Σ(列号)×像素 —— 主机反解整行平移量 k

采样点是「本行第 N 个 data_req 拍」，比屏幕列号早一拍，故 6/7 对应第 511 / 1023 列
（见 `fb_scanout.v` 里 de_x 的注释）。

源图用 ramp：第 c 列写 32 位字 c+1（B=(c+1)&0xFF，G=(c+1)>>8），于是读回值减 1
就是源列号。判据：

    HDMI_POSITION_OK                     屏第 511/1023 列 = 源第 511/1023 列
    HDMI_POSITION_FAIL <实际映射>         报出其中一个或两个源列号

用法：
    py build/hdmi_position_check.py
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import run_board_acceptance as r  # noqa: E402

LOG = ROOT / "build" / "hdmi_position_check.log"

BOARD_CODE = r'''
import sys, time
import numpy as np
from pynq import Overlay, MMIO
ov = Overlay('/home/xilinx/zynq-yolo/board/overlay/flow_system.bit')
sys.path.insert(0, '/home/xilinx/zynq-yolo/tools')
from hdmi_video import HdmiVideoOut, W, H

out = HdmiVideoOut()
st  = MMIO(0x41210000, 0x1000)

c = np.arange(1, W + 1, dtype=np.uint32)
img = np.zeros((H, W, 3), np.uint8)
img[:, :, 0] = (c & 0xFF).astype(np.uint8)
img[:, :, 1] = ((c >> 8) & 0xFF).astype(np.uint8)
out.show(img)
time.sleep(1.0)

def rd(sel):
    out.cfg.write(0x8, (out.pack & 0x1FFFFFFF) | (int(sel) << 29))
    time.sleep(0.4)
    return int(st.read(8)) & 0xFFFFFFFF      # 诊断口 = st 的第二通道（0x41290000 读不到）

vals = {s: rd(s) for s in (2, 5, 6, 7)}
out.cfg.write(0x8, out.pack)                # 现场还原（sel=0 时这条读回的是累加和）
s_col = (vals[5] & 0xFFFFFF) - 1
c511  = (vals[6] & 0xFFFFFF) - 1

# 全程 int64 再取低 32 位（np.uint64 与 Python int 混算会提升成 float64，& 直接报错）
M32 = 0xFFFFFFFF
cc = np.arange(1, W + 1, dtype=np.int64)
def pos_expected(k):
    src = ((np.arange(W, dtype=np.int64) + k) % W) + 1
    return (int((cc * src).sum()) * H) & M32
cand = [k for k in range(W) if pos_expected(k) == vals[7]]
shift = cand[0] if len(cand) == 1 else None

print('POS_STREAM 取数流起点=源列%d' % s_col, flush=True)
print('POS_PX 屏511←源%d' % c511, flush=True)
print('POS_RDADDR rd_at_de0=%d (mod 2048 = %d)' % (vals[2] & 0x1FFF, (vals[2] & 0x1FFF) % 2048), flush=True)
print('POS_SHIFT 位置加权和反解 k=%s (0 = 一一对应)' % (shift if shift is not None else cand[:8]), flush=True)
ok = (s_col == 0) and (c511 == 511) and (shift == 0)
print('HDMI_POSITION_%s %s' % ('OK' if ok else 'FAIL',
      'stream=%d col511=%d k=%s' % (s_col, c511, shift)), flush=True)
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
    rc, out = r.run_board(["python3", "-c", BOARD_CODE], timeout=240)
    for ln in out:
        log("  " + ln)
    log("rc=%d" % rc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
