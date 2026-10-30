#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""flow_overlay.py —— 全流程固化 overlay（flow_system.bit）的唯一入口。

为什么要它
----------
在这之前，「整条链路」是三个 bitstream 轮着下：`lcd_probe.bit` 看小屏、
`hdmi_video.bit` 看大屏、换功能还要重建 PS 侧句柄。赛场上没人愿意为了
「先看面板再看大屏」中断一次业务。`flow_system.bit` 把 LCD 4bpp 帧缓存、
HDMI 32bpp 扫描输出、两颗按键放进**同一个 PL 配置**，这里给出一条命令的
加载 + 自证。

它验什么、不验什么
------------------
验：寄存器地址对得上、BRAM 写得进读得出、LCD 时序在跑（逐行抓取）、
    HDMI 扫描输出不饿死（frames 增长、under=0）、按键读得到。
不验：**屏上颜色和版面对不对**。那是 VISUAL，要人眼看；
    BRAM_OK / HDMI_OK 只是机器判据，谁也不能替代谁（2026-10-30 踩过）。

用法（板端）
------------
    python3 tools/flow_overlay.py --selftest
    python3 tools/flow_overlay.py --selftest --hdmi-seconds 30
    python3 tools/flow_overlay.py --map            # 只打印地址表

判据令牌
--------
    FLOW_OVERLAY_MAP <ip> 0x<addr>           每个从设备一行
    FLOW_OVERLAY_LCD <OK|FAIL> <证据>
    FLOW_OVERLAY_HDMI <OK|FAIL> <证据>
    FLOW_OVERLAY_KEYS raw=0x<n>
    FLOW_OVERLAY_OK / FLOW_OVERLAY_FAIL <reason>
"""
import argparse
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import numpy as np

DEFAULT_BIT = "/home/xilinx/zynq-yolo/board/overlay/flow_system.bit"

#: flow_system 的地址契约（与 build/scripts/bd_flow_system.tcl 的文件头逐条对应）。
#: 改这张表 = 改硬件契约，必须同步改 tcl 并立新决策（D006）。
MAP = {
    "lcd_fb_800":   0x40000000,
    "lcd_fb_480":   0x40080000,
    "hdmi_cfg":     0x41200000,
    "hdmi_status":  0x41210000,
    "lcd_cfg":      0x41220000,
    "lcd_dbg0":     0x41230000,
    "lcd_dbg1":     0x41240000,
    "lcd_dbg2":     0x41250000,
    "keys":         0x41260000,
    "lcd_id":       0x41270000,
    "lcd_timing":   0x41280000,
}


def load(bit):
    from pynq import Overlay
    return Overlay(bit)


def check_map(ov):
    """把 PYNQ 从 hwh 解出来的物理地址和 MAP 对一遍 —— 这是最便宜的一致性检查。"""
    bad = []
    for name, addr in sorted(MAP.items(), key=lambda kv: kv[1]):
        ent = ov.ip_dict.get(name) or {}
        got = ent.get("phys_addr")
        ok = (got is None) or (int(got) == addr)
        print("FLOW_OVERLAY_MAP %-12s 0x%08X%s" % (name, addr, "" if ok else
                                                   "  ← hwh=%s" % ("0x%08X" % got if got else "-")))
        if not ok:
            bad.append(name)
    return bad


def lcd_selftest():
    """写一屏 4bpp 彩块，回读校验，再看逐行抓取寄存器是否在变（证明时序在跑）。"""
    import bram_fb
    from pynq import MMIO

    base = MAP["lcd_fb_800"]
    img = np.zeros((480, 800, 3), np.uint8)
    cols = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0),
            (0, 255, 255), (255, 0, 255), (255, 255, 255), (32, 32, 32)]
    for i, c in enumerate(cols):
        img[:, i * 100:(i + 1) * 100] = c
    mode, _pal = bram_fb.write_image4(img, base, 800, 480,
                                      palette=[tuple(int(v) for v in c) for c in cols])
    cfg = MMIO(MAP["lcd_cfg"], 0x10)
    cfg.write(0, 0x10)                       # [4]=帧缓存 + 800x480 + 背光亮

    mm = MMIO(base, 0x30000)
    ref = np.asarray(bram_fb.pack_4bpp(img, 800, 480,
                                       palette=[tuple(int(v) for v in c) for c in cols])[0],
                     dtype=np.uint32).reshape(-1)
    # 抽 16 个等距字核对（全量读 48000 字在 Jupyter 通道上太慢）
    idx = np.linspace(0, len(ref) - 1, 16).astype(np.int64)
    bad = 0
    for i in idx:
        if (mm.read(int(i) * 4) & 0xFFFFFFFF) != int(ref[i]):
            bad += 1

    # 逐行抓取：cfg[15:6] 选目标行；再读 pclk 自由计数器两次看是否在走（0 = 时序没跑）。
    # ★ dbg_tick 在 gpio_t 的**通道 2**（偏移 8），通道 1（偏移 0）是 dbg_timing 的静态快照，
    #   读错偏移会永远得到同一个值，看起来像「时序停了」。
    d2 = MMIO(MAP["lcd_dbg2"], 0x1000)
    tm = MMIO(MAP["lcd_timing"], 0x1000)
    for row in (10, 200, 470):
        cfg.write(0, 0x10 | (row << 6))
        time.sleep(0.05)
    cfg.write(0, 0x10)
    t1 = int(tm.read(8)) & 0xFFFFFFFF
    time.sleep(0.2)
    t2 = int(tm.read(8)) & 0xFFFFFFFF
    cnt = int(d2.read(0)) & 0xFFFFFFFF
    return {"mode": mode, "bad": bad, "checked": int(len(idx)),
            "tick_changed": t1 != t2, "tick1": t1, "tick2": t2, "cnt": cnt}


def hdmi_selftest(seconds, fps=8.0):
    from hdmi_video import HdmiVideoOut, W, H
    out = HdmiVideoOut(cfg_addr=MAP["hdmi_cfg"], st_addr=MAP["hdmi_status"])
    st0 = out.status()
    img = np.zeros((H, W, 3), np.uint8)
    t_end = time.time() + max(1.0, seconds)
    n = 0
    while time.time() < t_end:
        # 竖向滚动色带：任何一行读错都能看出来，且和 LCD 的彩块不混淆
        k = (n // 3) % 8
        for i in range(8):
            img[:, i * (W // 8):(i + 1) * (W // 8)] = \
                [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0),
                 (0, 255, 255), (255, 0, 255), (255, 255, 255), (0, 0, 0)][(i + k) % 8]
        out.show(img)
        n += 1
        time.sleep(1.0 / fps)
    st1 = out.status()
    return {"frames0": st0["frames"], "frames1": st1["frames"],
            "under": st1["under"], "shown": n,
            "bufs": [int(b.physical_address) for b in out.bufs]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bit", default=DEFAULT_BIT)
    ap.add_argument("--map", action="store_true", help="只打印地址表")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--hdmi-seconds", type=float, default=12.0)
    ap.add_argument("--skip-lcd", action="store_true")
    ap.add_argument("--skip-hdmi", action="store_true")
    args = ap.parse_args()

    if not Path(args.bit).is_file():
        print("FLOW_OVERLAY_FAIL no_bit %s" % args.bit)
        return 2
    try:
        ov = load(args.bit)
    except Exception as e:                                   # noqa: BLE001
        print("FLOW_OVERLAY_FAIL overlay_load %s: %s" % (type(e).__name__, e))
        return 2

    bad = check_map(ov)
    if args.map and not args.selftest:
        print("FLOW_OVERLAY_MAP_OK" if not bad else "FLOW_OVERLAY_FAIL map %s" % ",".join(bad))
        return 0 if not bad else 1
    if not args.selftest:
        print("FLOW_OVERLAY_FAIL nothing_to_do（加 --selftest）")
        return 2

    fail = []
    if not args.skip_lcd:
        try:
            r = lcd_selftest()
            ok = r["bad"] == 0 and r["tick_changed"]
            print("FLOW_OVERLAY_LCD %s mode=%s bad=%d/%d tick %d→%d cnt=%d"
                  % ("OK" if ok else "FAIL", r["mode"], r["bad"], r["checked"],
                     r["tick1"], r["tick2"], r["cnt"]))
            if not ok:
                fail.append("lcd")
        except Exception as e:                               # noqa: BLE001
            print("FLOW_OVERLAY_LCD FAIL exception %s: %s" % (type(e).__name__, e))
            fail.append("lcd")

    if not args.skip_hdmi:
        try:
            r = hdmi_selftest(args.hdmi_seconds)
            adv = (r["frames1"] - r["frames0"]) & 0xFFFF
            ok = adv > 0 and r["under"] == 0
            print("FLOW_OVERLAY_HDMI %s frames %d→%d (+%d) under=%d shown=%d bufs=%s"
                  % ("OK" if ok else "FAIL", r["frames0"], r["frames1"], adv,
                     r["under"], r["shown"],
                     ",".join("0x%08X" % b for b in r["bufs"])))
            if not ok:
                fail.append("hdmi")
        except Exception as e:                               # noqa: BLE001
            print("FLOW_OVERLAY_HDMI FAIL exception %s: %s" % (type(e).__name__, e))
            fail.append("hdmi")

    try:
        from pynq import MMIO
        raw = int(MMIO(MAP["keys"], 0x1000).read(0)) & 0x3
        print("FLOW_OVERLAY_KEYS raw=0x%X key0=%d key1=%d" % (raw, raw & 1, (raw >> 1) & 1))
    except Exception as e:                                   # noqa: BLE001
        print("FLOW_OVERLAY_KEYS FAIL %s: %s" % (type(e).__name__, e))

    if fail:
        print("FLOW_OVERLAY_FAIL %s" % ",".join(fail))
        return 1
    print("FLOW_OVERLAY_OK（机器判据；屏上颜色/版面仍要人眼看）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
