#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""track_preproc.py —— PYNQ 驱动：PL 预处理 + 白/运动掩码（单 overlay）。

硬件：`board/overlay/track_preproc.bit/.hwh`（`build/scripts/bd_track_preproc.tcl`）。
HLS 顶层：`src/hls/track/src/track_preproc.cpp`。

    rgb  = 3 x 320 x 320（channel-first，ncnn 布局）
    mask = 320 x 320，bit0=白（高亮低饱和），bit1=运动（|G-Gprev|>diff_thr）

板上用法：
    from track_preproc import TrackPreproc
    tp = TrackPreproc(repo)
    rgb, mask, ms = tp.run(cur_bgr, prev_bgr)
"""
import sys

sys.dont_write_bytecode = True

import os
import time
import xml.etree.ElementTree as ET

import numpy as np

OUT_W = 320
OUT_H = 320

_PTR_REGS = ("in", "prev", "rgb", "mask")
_SCALAR_REGS = ("W_IN", "H_IN", "white_thr", "sat_thr", "diff_thr")


class TrackPreproc:
    def __init__(self, repo, bitfile=None, hwhfile=None):
        from pynq import Overlay, allocate

        self.repo = os.path.abspath(repo)
        self.bit = bitfile or os.path.join(self.repo, "board", "overlay", "track_preproc.bit")
        self.hwh = hwhfile or (os.path.splitext(self.bit)[0] + ".hwh")
        if not os.path.exists(self.bit):
            raise RuntimeError("找不到 %s（先综合/出片）" % self.bit)
        if not os.path.exists(self.hwh):
            raise RuntimeError("找不到 %s" % self.hwh)

        self.overlay = Overlay(self.bit)
        self._regs = self._parse_hwh(self.hwh)
        self._ctl, self._ctl_r = self._find_windows()
        self._in = self._out = self._rgb = self._mask = None
        self._shape = None

    @staticmethod
    def _parse_hwh(path):
        regs = {}
        for ab in ET.parse(path).getroot().iter("ADDRESSBLOCK"):
            iface = ab.get("INTERFACE")
            for reg in ab.iter("REGISTER"):
                off = None
                for prop in reg.findall("PROPERTY"):
                    if prop.get("NAME") == "ADDRESS_OFFSET":
                        off = int(prop.get("VALUE"), 0)
                if reg.get("NAME") and off is not None:
                    regs[(iface, reg.get("NAME"))] = off
        return regs

    def _find_windows(self):
        wins = {}
        for key, info in self.overlay.ip_dict.items():
            typ = str(info.get("type", "")).lower()
            if "track_preproc" not in typ and "track_preproc" not in str(key).lower():
                continue
            mem = info.get("mem_id")
            if mem in ("s_axi_control", "s_axi_control_r"):
                wins[mem] = getattr(self.overlay, key).mmio
        if "s_axi_control" not in wins:
            raise RuntimeError("overlay 缺 s_axi_control；ip_dict=%s" % sorted(self.overlay.ip_dict))
        return wins["s_axi_control"], wins.get("s_axi_control_r")

    def _scalar(self, name):
        for iface in ("s_axi_control", "s_axi_control_r"):
            if (iface, name) in self._regs:
                return self._regs[(iface, name)]
        raise KeyError("寄存器 %s 不存在；已有=%s" % (name, sorted(self._regs)))

    def _ptr_regs(self, name):
        # 可能只有一个控制窗口（本次 IP），也可能有 s_axi_control_r（旧 preproc）。
        # HLS 2020.2 常见：name_1/name_2；关键字冲突会改名 name_r_1/name_r_2。
        for iface, mmio in (("s_axi_control", self._ctl), ("s_axi_control_r", self._ctl_r)):
            if mmio is None:
                continue
            for base in (name, name + "_r"):
                a, b = base + "_1", base + "_2"
                if (iface, a) in self._regs and (iface, b) in self._regs:
                    return mmio, self._regs[(iface, a)], self._regs[(iface, b)]
                if (iface, base) in self._regs:
                    o = self._regs[(iface, base)]
                    return mmio, o, o + 4
        raise KeyError("指针寄存器 %s 不存在；已有=%s" % (name, sorted(self._regs)))

    def _alloc(self, w, h):
        from pynq import allocate
        n = int(w) * int(h) * 3
        self._in = allocate(shape=(n,), dtype=np.uint8)
        self._out = allocate(shape=(n,), dtype=np.uint8)
        self._rgb = allocate(shape=(OUT_W * OUT_H * 3,), dtype=np.uint8)
        self._mask = allocate(shape=(OUT_W * OUT_H,), dtype=np.uint8)
        for name, buf in (("in", self._in), ("prev", self._out),
                          ("rgb", self._rgb), ("mask", self._mask)):
            mmio, lo, hi = self._ptr_regs(name)
            addr = int(buf.physical_address)
            mmio.write(lo, addr & 0xFFFFFFFF)
            mmio.write(hi, (addr >> 32) & 0xFFFFFFFF)
        self._ctl.write(self._scalar("W_IN"), int(w))
        self._ctl.write(self._scalar("H_IN"), int(h))
        self._shape = (int(w), int(h))

    def run(self, cur_bgr, prev_bgr=None, white_thr=150, sat_thr=40, diff_thr=12):
        cur = np.ascontiguousarray(cur_bgr, dtype=np.uint8)
        h, w = cur.shape[:2]
        if self._shape != (w, h):
            self._alloc(w, h)
        prev = cur if prev_bgr is None else np.ascontiguousarray(prev_bgr, dtype=np.uint8)
        if prev.shape != cur.shape:
            prev = cur
        np.copyto(self._in, cur.reshape(-1))
        np.copyto(self._out, prev.reshape(-1))
        for name, val in (("white_thr", white_thr), ("sat_thr", sat_thr), ("diff_thr", diff_thr)):
            self._ctl.write(self._scalar(name), int(val))
        self._in.flush()
        self._out.flush()
        t0 = time.perf_counter()
        self._ctl.write(0x00, 0x01)
        while (self._ctl.read(0x00) & 0x2) == 0:
            pass
        ms = (time.perf_counter() - t0) * 1e3
        self._rgb.invalidate()
        self._mask.invalidate()
        rgb = np.array(self._rgb, copy=True).reshape(3, OUT_H, OUT_W)
        mask = np.array(self._mask, copy=True).reshape(OUT_H, OUT_W)
        return rgb, mask, ms


def main():
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    args = ap.parse_args()
    tp = TrackPreproc(args.repo)
    print("TRACK_PREPROC_READY", tp.bit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
