#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hdmi_visual_check.py —— 上板「人眼验收」用的最小画面集（2026-10-31）

为什么单独一个脚本
------------------
`hdmi_geom_probe.py` 是**机器判据**那一侧：它把行/列映射、取数欠载、累加和
全部量化出来。但项目验收标准是「人眼看屏、上板跑通」，而人眼要看的只有三件事：

  1. 横带是不是**连续**的 8 条 —— 13 条黑横条 = 取数跟不上（2026-10-31 上板原话）；
  2. 竖带是不是**连续**的 8 条 —— 列映射；
  3. 画面是不是**静止**的 —— 静止图案在屏上「跳动」= 行缓存被回头重写。

第 4 屏（移动方块）是刻意加的运动测试：静止图案跳动有时看不准，
让一个白块按行往下走，走得不匀/跳格/撕裂就是取数在抢时间。

每屏都同时打印机器口径（帧数、under、累加和是否等于期望），
**人眼结论与机器结论分开记**，不要用其中一个代替另一个。

用法：
    py build/hdmi_visual_check.py --hold 60
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import run_board_acceptance as r  # noqa: E402

LOG = ROOT / "build" / "hdmi_visual_check.log"

BOARD_CODE = r'''
import sys, time
import numpy as np
from pynq import Overlay
ov = Overlay('/home/xilinx/zynq-yolo/board/overlay/flow_system.bit')
sys.path.insert(0, '/home/xilinx/zynq-yolo/tools')
from hdmi_video import HdmiVideoOut, pack_frame, W, H

out = HdmiVideoOut()
HOLD = __HOLD__

# 累加口径 = 屏幕口径（显示列 0/1/2 都取源列 0，源列 1278/1279 不显示）
COLS = list(range(W))

def expect(words):
    w = np.asarray(words, dtype=np.uint64) & np.uint64(0xFFFFFF)
    return int(w[:, COLS].sum() & np.uint64(0xFFFFFFFF))

def rd():
    v = int(out.st.read(0)) & 0xFFFFFFFF
    return (v >> 16) & 0xFFFF, v & 0xFFFF, int(out.st.read(8)) & 0xFFFFFFFF

def show(tag, img, note=''):
    e = expect(pack_frame(img))
    out.show(img)
    time.sleep(0.8)                       # 换缓冲后先让至少一帧过去
    fr, un, got = rd()
    print('VIS %-8s expect=%08X got=%08X %-8s frames=%d under=%d  %s'
          % (tag, e, got, 'MATCH' if got == e else 'MISMATCH', fr, un, note), flush=True)

BANDS = [(255,255,255), (0,255,255), (0,255,0), (255,255,0),
         (0,0,255), (255,0,0), (255,0,255), (0,0,0)]      # BGR

# ---- 0. 贴边细条：**水平偏移的直接读数** -----------------------------------
# 为什么单独加这一屏：2026-10-31 上板人眼报「白色/蓝色块不贴左边缘、右边缘的颜色
# 跑到左边」，而机器判据（累加和）看不出左右移位 —— 累加和是**与顺序无关**的，
# 一列跑到哪儿它都只知道"这列被显示了几次"。位置只能用眼睛量。
# 这里把源列 0..3 涂白、源列 1276..1279 涂红，整幅高度：
#   · 左边缘应是**纯白**、右边缘应是**纯红**（一一对应）；
#   · 左边出现红 ⇒ 内容右移并回卷；左边出现黑 ⇒ 右移未回卷（少了 2 列）；
#   · 左边白但右边缘不是红 ⇒ 图被拉伸/裁掉。
img = np.zeros((H, W, 3), np.uint8)
img[:, 0:4] = (255, 255, 255)              # 白（BGR 全 255）
img[:, W-4:W] = (0, 0, 255)                # 红
img[H//2-40:H//2+40, W//2-40:W//2+40] = (0, 255, 0)   # 正中一个绿方块（居中参照）
show('EDGES', img, '左边缘应纯白、右边缘应纯红、绿方块应在正中')
print('VIS_EDGES_HOLD %ds' % HOLD, flush=True)
time.sleep(HOLD)

# ---- 1. 八条横带：条数/顺序/厚度 -------------------------------------------
img = np.zeros((H, W, 3), np.uint8)
step = H // 8
for i, c in enumerate(BANDS):
    img[i*step:(i+1)*step] = c
show('HBANDS', img, '应为从上到下 白 青 绿 黄 蓝 红 品红 黑，8 条等宽、无黑横条')
print('VIS_HBANDS_HOLD %ds' % HOLD, flush=True)
time.sleep(HOLD)

# ---- 2. 八条竖带：列映射 ---------------------------------------------------
img = np.zeros((H, W, 3), np.uint8)
wstep = W // 8
for i, c in enumerate(BANDS):
    img[:, i*wstep:(i+1)*wstep] = c
show('VBANDS', img, '应为从左到右 白 青 绿 黄 蓝 红 品红 黑，8 条等宽')
print('VIS_VBANDS_HOLD %ds' % HOLD, flush=True)
time.sleep(HOLD)

# ---- 3. 细密格子：任何一行/一列丢了都会立刻看出来 --------------------------
# 注意 BGR：(255,0,0) 是**蓝**、(0,255,0) 是绿。第一版注释写成"红线"，上板
# 用户当场指出"竖线是蓝色的" —— 注释错了，颜色本身没错。
img = np.zeros((H, W, 3), np.uint8)
img[::16, :] = (0, 255, 0)                 # 每 16 行一条绿线
img[:, ::16] = (255, 0, 0)                 # 每 16 列一条蓝线（交叉处取蓝）
show('GRID', img, '横绿线应等距 45 条、竖蓝线 80 条，线要直、间距要匀')
print('VIS_GRID_HOLD %ds' % HOLD, flush=True)
time.sleep(HOLD)

# ---- 4. 运动的方块：静止图看不出的撕裂/跳格 --------------------------------
print('VIS_MOTION_BEGIN 40s：白色方块自上而下匀速移动', flush=True)
t0 = time.time()
k = 0
while time.time() - t0 < 40:
    k = (k + 1) % (H - 120)
    im = np.zeros((H, W, 3), np.uint8)
    im[k:k+120, 580:700] = (255, 255, 255)
    out.show(im)
    time.sleep(0.05)
fr, un, got = rd()
print('VIS_MOTION_END frames=%d under=%d sum=%08X' % (fr, un, got), flush=True)

# ---- 5. 三个角标：几何锚点 -------------------------------------------------
img = np.zeros((H, W, 3), np.uint8)
img[0:180, 0:320] = (255, 255, 255)        # 左上白
img[0:180, W-320:W] = (0, 0, 255)          # 右上红
img[H-180:H, 0:320] = (255, 0, 0)          # 左下蓝
show('MARKERS', img, '左上白、右上红、左下蓝，三块贴边、不外溢')
print('VIS_MARKERS_HOLD %ds' % HOLD, flush=True)
time.sleep(HOLD)
print('VIS_DONE')
'''


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def main():
    hold = 60
    if "--hold" in sys.argv:
        hold = int(sys.argv[sys.argv.index("--hold") + 1])
    if not r.board_alive(timeout=25):
        log("BOARD_OFFLINE")
        return 2
    code = BOARD_CODE.replace("__HOLD__", str(hold))
    rc, out = r.run_board(["python3", "-c", code], timeout=hold * 4 + 240)
    for ln in out:
        log("  " + ln)
    log("rc=%d" % rc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
