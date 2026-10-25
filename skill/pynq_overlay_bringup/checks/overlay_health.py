#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读体检：加载 PYNQ overlay，打印 IP/地址窗口，检查控制窗口是否成对。

用法（板上）::

    python3 overlay_health.py /path/to/xxx.bit

不写任何寄存器、不分配 DMA 缓冲；只回答“overlay 能不能加载、IP 在不在、
控制窗口是否缺 `_r`”。判据：末行 `OVERLAY_HEALTH_OK`（或 `OVERLAY_HEALTH_FAIL`）。
"""
import sys

sys.dont_write_bytecode = True


def main(argv):
    if len(argv) < 2:
        print("usage: python3 overlay_health.py <overlay.bit>")
        print("OVERLAY_HEALTH_FAIL")
        return 1
    bit = argv[1]
    try:
        from pynq import Overlay
    except Exception as exc:
        print(f"FAIL import pynq: {type(exc).__name__}: {exc}")
        print("OVERLAY_HEALTH_FAIL")
        return 1

    try:
        ovl = Overlay(bit)
    except Exception as exc:
        print(f"FAIL load overlay: {type(exc).__name__}: {exc}")
        print("OVERLAY_HEALTH_FAIL")
        return 1

    n_ip = 0
    for name, info in ovl.ip_dict.items():
        n_ip += 1
        print(f"IP {name}")
        print(f"   type   = {info.get('type')}")
        print(f"   mem_id = {info.get('mem_id')}")
        print(f"   phys   = {info.get('phys_addr')}")
        print(f"   addr   = {info.get('addr_range')}")

    # 控制窗口成对检查：HLS IP 常见 s_axi_control / s_axi_control_r
    names = {str(k) for k in ovl.ip_dict}
    has_ctl = any("s_axi_control" in n for n in names)
    has_ctl_r = any("s_axi_control_r" in n for n in names)
    print(f"control={has_ctl} control_r={has_ctl_r}")
    if has_ctl and not has_ctl_r:
        print("WARN 只有 s_axi_control、没有 s_axi_control_r：BD 可能漏接第二个控制窗口")

    print(f"OVERLAY_HEALTH_{'OK' if n_ip else 'FAIL'}")
    return 0 if n_ip else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
