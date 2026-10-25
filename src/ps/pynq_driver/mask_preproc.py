#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mask_preproc.py -- PYNQ 驱动：PL 掩码预处理器（白球 + 运动）。

硬件契约（build/ip_repo/mask_preproc/drivers/.../xmask_preproc_hw.h 实测）：
    s_axi_control 单口，寄存器：
      0x00 AP_CTRL      bit0 start / bit1 done / bit2 idle / bit3 ready
      0x10/0x14 cur     64 位指针（低/高）
      0x1c/0x20 prev    64 位指针
      0x28/0x2c out_r   64 位指针
      0x34 W   0x3c H   0x44 white_thr   0x4c sat_thr   0x54 diff_thr
    m_axi_gmem -> S_AXI_HP0（读 cur/prev，写 out）

输出：每像素 1 字节，bit0=白（max(BGR)>white_thr 且 max-min<sat_thr），
      bit1=运动（|G-Gprev|>diff_thr）。

用法（板上）：
    ovl = MaskPreprocOverlay("mask_preproc.bit")
    ovl.prepare(w=640, h=360)
    ovl.run(cur_bgr, prev_bgr)   # 返回 uint8 [h,w] 掩码
"""
import sys

sys.dont_write_bytecode = True

import time

import numpy as np
from pynq import Overlay, allocate

AP_CTRL = 0x00
CUR_LO, CUR_HI = 0x10, 0x14
PREV_LO, PREV_HI = 0x1C, 0x20
OUT_LO, OUT_HI = 0x28, 0x2C
R_W, R_H = 0x34, 0x3C
R_WHITE, R_SAT, R_DIFF = 0x44, 0x4C, 0x54


class MaskPreprocOverlay:
    def __init__(self, bitfile):
        self.overlay = Overlay(bitfile)
        keys = [k for k in self.overlay.ip_dict if "mask_preproc" in k]
        if not keys:
            raise RuntimeError("overlay 里没有 mask_preproc：%s" % sorted(self.overlay.ip_dict))
        self.name = keys[0]
        self.ip = getattr(self.overlay, self.name)
        self.cur = None
        self.prev = None
        self.out = None
        self.w = 0
        self.h = 0

    def prepare(self, w, h):
        self.w, self.h = int(w), int(h)
        self.cur = allocate(shape=(h, w, 3), dtype=np.uint8)
        self.prev = allocate(shape=(h, w, 3), dtype=np.uint8)
        self.out = allocate(shape=(h, w), dtype=np.uint8)
        for name, buf in (("cur", self.cur), ("prev", self.prev), ("out", self.out)):
            addr = buf.physical_address
            lo, hi = addr & 0xFFFFFFFF, (addr >> 32) & 0xFFFFFFFF
            base = {"cur": CUR_LO, "prev": PREV_LO, "out": OUT_LO}[name]
            self.ip.write(base, lo)
            self.ip.write(base + 4, hi)
        self.ip.write(R_W, self.w)
        self.ip.write(R_H, self.h)

    def run(self, cur_bgr, prev_bgr, white_thr=150, sat_thr=40, diff_thr=12):
        if self.cur is None:
            self.prepare(cur_bgr.shape[1], cur_bgr.shape[0])
        np.copyto(self.cur, cur_bgr)
        np.copyto(self.prev, prev_bgr)
        self.ip.write(R_WHITE, int(white_thr))
        self.ip.write(R_SAT, int(sat_thr))
        self.ip.write(R_DIFF, int(diff_thr))
        self.cur.flush()
        self.prev.flush()
        t0 = time.perf_counter()
        self.ip.write(AP_CTRL, 0x1)
        while (self.ip.read(AP_CTRL) & 0x2) == 0:
            pass
        dt = time.perf_counter() - t0
        self.out.invalidate()
        return np.array(self.out, copy=True), dt