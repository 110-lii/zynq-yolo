#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_axi_bw.py —— 板上测 PL 主口经 S_AXI_HP 读/写 DDR 的实际吞吐。

跑在哪：领航者 ZYNQ 7020（PYNQ 2.7），需要 root（/dev/mem）。
PC 上不能跑（没有 pynq），这是设计分工。

用法（板上）：
    sudo python3 src/ps/host/run_axi_bw.py --mb 8 --reps 4 --repeat 5

输出（全是 ASCII 令牌，末行是判据）：
    BW_ROW mode=<m> name=<..> bytes=<..> best_s=<..> best_MBps=<..> med_MBps=<..>
    AXI_BW_OK <modes> <best_read_MBps> <best_write_MBps>

读模式把异或结果写到 out32[0]，写模式覆盖 out32；模式 3 是读+写并发。
"""
import argparse
import statistics
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

sys.dont_write_bytecode = True

import numpy as np

try:
    from pynq import Overlay, allocate
except ImportError as exc:
    raise ImportError("pynq not available: run this ON THE BOARD") from exc

REPO = Path(__file__).resolve().parents[3]
BIT = REPO / "board" / "overlay" / "axi_bw.bit"
HWH = REPO / "board" / "overlay" / "axi_bw.hwh"

SCALARS = ("n32", "n64", "mode", "reps")


def parse_regs(hwh_path):
    """从 .hwh 解析 (接口名, 寄存器名) -> 字节偏移。"""
    regs = {}
    tree = ET.parse(str(hwh_path))
    for ab in tree.getroot().iter("ADDRESSBLOCK"):
        iface = ab.get("INTERFACE")
        for reg in ab.iter("REGISTER"):
            name = reg.get("NAME")
            off = None
            for prop in reg.findall("PROPERTY"):
                if prop.get("NAME") == "ADDRESS_OFFSET":
                    off = int(prop.get("VALUE"), 0)
            if name is not None and off is not None:
                regs[(iface, name)] = off
    return regs


def find_windows(ovl):
    """按 mem_id 找 axi_bw 的 MMIO 窗口。"""
    out = {}
    for key, info in ovl.ip_dict.items():
        if "axi_bw" not in str(info.get("type", "")).lower():
            continue
        mem_id = info.get("mem_id")
        if mem_id:
            out[mem_id] = getattr(ovl, key).mmio
    if not out:
        raise RuntimeError("axi_bw windows not found in ip_dict")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--mb", type=int, default=8, help="每个缓冲区的 MB 数")
    ap.add_argument("--reps", type=int, default=4)
    ap.add_argument("--repeat", type=int, default=5)
    args = ap.parse_args(argv)

    if not BIT.exists() or not HWH.exists():
        print("AXI_BW_FAIL missing %s / %s" % (BIT, HWH))
        return 2

    ovl = Overlay(str(BIT))
    wins = find_windows(ovl)
    regs = parse_regs(HWH)

    def mmio_for(iface):
        if iface not in wins:
            raise RuntimeError("no window for %s (have %r)" % (iface, list(wins)))
        return wins[iface]

    # 标量寄存器统一在 s_axi_control
    ctl = mmio_for("s_axi_control")
    off = {name: regs[("s_axi_control", name)] for name in SCALARS}

    n32 = args.mb * 1024 * 1024 // 4
    n64 = args.mb * 1024 * 1024 // 8
    in32 = allocate(shape=(n32,), dtype=np.uint32)
    in64 = allocate(shape=(n64,), dtype=np.uint64)
    out32 = allocate(shape=(n32,), dtype=np.uint32)
    in32[:] = np.arange(n32, dtype=np.uint32)
    in64[:] = np.arange(n64, dtype=np.uint64)
    out32[:] = 0
    for b in (in32, in64, out32):
        b.flush()

    # 指针寄存器在 s_axi_control_r。HLS 2020.2 导出的命名有两种：
    #   in32 / in32_2（低/高 32 位两个寄存器），或单个 64 位 in32（off 与 off+4）。
    pr = mmio_for("s_axi_control_r")
    for name, buf in (("in32", in32), ("in64", in64), ("out32", out32)):
        if ("s_axi_control_r", name + "_1") in regs:
            o1 = regs[("s_axi_control_r", name + "_1")]
            o2 = regs[("s_axi_control_r", name + "_2")]
        else:
            o1 = regs[("s_axi_control_r", name)]
            o2 = o1 + 4
        pa = int(buf.physical_address)
        pr.write(o1, pa & 0xFFFFFFFF)
        pr.write(o2, (pa >> 32) & 0xFFFFFFFF)

    def run_once(mode):
        ctl.write(off["n32"], n32)
        ctl.write(off["n64"], n64)
        ctl.write(off["mode"], mode)
        ctl.write(off["reps"], args.reps)
        t0 = time.perf_counter()
        ctl.write(0x00, 0x01)
        while True:
            v = ctl.read(0x00)
            if v & 0x2:
                break
            if time.perf_counter() - t0 > 30.0:
                raise RuntimeError("timeout mode=%d ctrl=0x%08x" % (mode, v))
        return time.perf_counter() - t0

    names = ["read32", "read64", "write32", "copy32"]
    best = {}
    for mode in range(4):
        for _ in range(2):          # 预热
            run_once(mode)
        samples = [run_once(mode) for _ in range(args.repeat)]
        if mode == 0:
            nbytes = n32 * 4 * args.reps
            moved = nbytes
        elif mode == 1:
            nbytes = n64 * 8 * args.reps
            moved = nbytes
        elif mode == 2:
            nbytes = n32 * 4 * args.reps
            moved = nbytes
        else:
            nbytes = n32 * 4 * args.reps
            moved = 2 * nbytes
        best_s = min(samples)
        print("BW_ROW mode=%d name=%s bytes=%d reps=%d best_s=%.6f best_MBps=%.2f med_MBps=%.2f"
              % (mode, names[mode], moved, args.reps, best_s,
                 moved / best_s / 1e6, moved / statistics.median(samples) / 1e6))
        best[names[mode]] = moved / best_s / 1e6

    print("AXI_BW_OK 4 read32=%.2f read64=%.2f write32=%.2f copy32=%.2f"
          % (best["read32"], best["read64"], best["write32"], best["copy32"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
