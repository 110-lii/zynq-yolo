#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""board_idle.py —— 上电后的「待机白屏」：加载 flow_system.bit，HDMI 显示纯白并保持。

为什么需要它
------------
用户 2026-11-01 要求：「现在 hdmi 让他的空闲时显示白色，把整个固化到板子上，
让我可以一上电就可以使用」。

为什么要**一直挂着**而不是画一次就退：HDMI 帧缓存是 PYNQ `allocate()` 出来的
DDR 缓冲，进程退出缓冲就被释放，扫描输出会读到失效页面（黑屏或花屏）。
所以这个进程必须活着——它就是 systemd 服务的常驻体。

为什么白色不是「凑合」：`fb_scanout` 已知的间歇缺陷是**整行循环旋转**
（恒定 +313 列）。纯白画面既没有内容也不承载信息，旋转看不出来，也不误导人；
空闲时屏幕上不该出现任何「像检测结果」的东西。

用法（板端）
------------
    python3 tools/board_idle.py                 # 白屏并常驻（systemd 用这个）
    python3 tools/board_idle.py --seconds 5     # 只挂 5 秒（手动验证用）
    python3 tools/board_idle.py --status        # 打印扫描是否在跑，不常驻

判据令牌
--------
    IDLE_OVERLAY_OK                     overlay 已加载
    IDLE_HDMI_WHITE_OK frames=<n> under=<n>   白屏已写上并开始扫描
    IDLE_HDMI_WHITE_FAIL <reason>
"""
import argparse
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

DEFAULT_BIT = "/home/xilinx/zynq-yolo/board/overlay/flow_system.bit"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--bit", default=DEFAULT_BIT)
    ap.add_argument("--always-white", action="store_true", default=True,
                    help="每 5 s 重写一次白屏（默认开；防止别的进程改过缓冲）")
    ap.add_argument("--no-refresh", dest="always_white", action="store_false")
    ap.add_argument("--seconds", type=float, default=0.0,
                    help="只挂这么多秒；0 = 常驻（systemd 的用法）")
    ap.add_argument("--status", action="store_true", help="只打印扫描状态，不常驻")
    args = ap.parse_args(argv)

    import numpy as np
    from pynq import Overlay

    from hdmi_video import HdmiVideoOut, W, H

    try:
        Overlay(args.bit)
        print("IDLE_OVERLAY_OK %s" % args.bit, flush=True)
    except Exception as e:                                   # noqa: BLE001
        print("IDLE_HDMI_WHITE_FAIL overlay: %s" % e, flush=True)
        return 2

    white = np.full((H, W, 3), 255, np.uint8)
    try:
        out = HdmiVideoOut()
        cfg = out.check_cfg()
        if not (cfg["addr"] and cfg["pack"]):
            # 配置没进 PL 时屏上不会报警，只会一直是一个颜色 —— 必须当 FAIL
            print("IDLE_HDMI_WHITE_FAIL cfg_not_effective %s" % cfg, flush=True)
            return 3
        out.show(white)
    except Exception as e:                                   # noqa: BLE001
        print("IDLE_HDMI_WHITE_FAIL hdmi: %s" % e, flush=True)
        return 3

    time.sleep(0.3)                                          # 让 vs 沿至少过一帧
    try:
        st = out.status()
    except Exception:                                        # noqa: BLE001
        st = {}
    print("IDLE_HDMI_WHITE_OK frames=%s under=%s"
          % (st.get("frames", "?"), st.get("under", "?")), flush=True)

    if args.status:
        return 0

    t0 = time.time()
    while True:
        time.sleep(5.0)
        if args.always_white:
            try:
                out.show(white)
            except Exception as e:                           # noqa: BLE001
                print("IDLE_HDMI_WHITE_FAIL refresh: %s" % e, flush=True)
                return 4
        if args.seconds and (time.time() - t0) >= args.seconds:
            return 0


if __name__ == "__main__":
    sys.exit(main())
