#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读体检：加载 PYNQ overlay，打印 IP/地址窗口，检查控制窗口是否成对、`.hwh` 是否同名同批。

用法（板上）::

    python3 overlay_health.py /path/to/xxx.bit
    python3 overlay_health.py --help

不写任何寄存器、不分配 DMA 缓冲；只回答四件事：**.hwh 在不在**、**overlay 能不能加载**、
**IP 在不在**、**控制窗口是否缺 `_r`**。

判据（末行令牌）::

    OVERLAY_HEALTH_OK            全部通过
    OVERLAY_HEALTH_WARN_CONTROL  能加载，但只有 s_axi_control、缺 s_axi_control_r（退出码 2）
    OVERLAY_HEALTH_FAIL          加载失败 / 没有 IP / 找不到 .hwh 之外的结构（退出码 1）

退出码非 0 即「不算通过」：把这个脚本接进 CI 或提交前检查时，只看退出码就够了。
"""
import argparse
import sys
from pathlib import Path

sys.dont_write_bytecode = True


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="PYNQ overlay 只读体检：.hwh 同名检查 + 加载 + IP/控制窗口成对检查")
    ap.add_argument("bit", nargs="?", help="overlay 比特流路径（如 board/overlay/flow_system.bit）")
    ap.add_argument("--strict", action="store_true",
                    help="把「缺 s_axi_control_r」也判为 FAIL（默认只 WARN，退出码 2）")
    ap.add_argument("--no-hwh-check", action="store_true",
                    help="跳过同名 .hwh 检查（有些 overlay 用外部 hwh）")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if not args.bit:
        parse_args(["--help"])
        return 1
    bit = Path(args.bit)

    if not bit.is_file():
        print(f"FAIL bit not found: {bit}")
        print("OVERLAY_HEALTH_FAIL")
        return 1

    if not args.no_hwh_check:
        hwh = bit.with_suffix(".hwh")
        if not hwh.is_file():
            print(f"FAIL .hwh 不在同一目录：期望 {hwh}")
            print("   PYNQ 0.2.x 需要同名 .hwh 才能知道地址表；缺了会加载出「空 overlay」")
            print("OVERLAY_HEALTH_FAIL")
            return 1
        print(f"HWH {hwh} ({hwh.stat().st_size} bytes)")

    try:
        from pynq import Overlay
    except Exception as exc:
        print(f"FAIL import pynq: {type(exc).__name__}: {exc}")
        print("   （本脚本必须在板上跑：主机端没有 pynq 包是正常的）")
        print("OVERLAY_HEALTH_FAIL")
        return 1

    try:
        ovl = Overlay(str(bit))
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

    names = {str(k) for k in ovl.ip_dict}
    has_ctl = any("s_axi_control" in n for n in names)
    has_ctl_r = any("s_axi_control_r" in n for n in names)
    print(f"control={has_ctl} control_r={has_ctl_r}")

    if not n_ip:
        print("FAIL overlay 加载成功但 IP 表为空：多半是 .hwh 缺失或与 .bit 不同批")
        print("OVERLAY_HEALTH_FAIL")
        return 1

    if has_ctl and not has_ctl_r:
        print("WARN 只有 s_axi_control、没有 s_axi_control_r：BD 可能漏接第二个控制窗口")
        print("   （用 --strict 可把这条判成 FAIL；本队踩过：读侧窗口漏接时寄存器读回全 0）")
        print("OVERLAY_HEALTH_WARN_CONTROL")
        return 1 if args.strict else 2

    print("OVERLAY_HEALTH_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
