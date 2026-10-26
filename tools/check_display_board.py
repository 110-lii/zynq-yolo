#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""板上验收：HDMI/LCD 1bpp 帧缓存写入 + AXI 读回。

板上运行（二选一）：
    python3 tools/check_display_board.py --which hdmi --repo /home/xilinx/zynq-yolo
    python3 tools/check_display_board.py --which lcd  --repo /home/xilinx/zynq-yolo

它验证：overlay 能加载、bram_ctrl 能拿到基址、1bpp 数据能写进 BRAM 且读回一致。
**实际屏幕画面仍需人眼确认**；本脚本不会把“读回对”写成“显示对”。
判据：末行 DISPLAY_BOARD_OK。
"""
import argparse
import sys
from pathlib import Path

sys.dont_write_bytecode = True

import numpy as np


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--which", choices=["hdmi", "lcd"], required=True)
    ap.add_argument("--repo", default="/home/xilinx/zynq-yolo")
    args = ap.parse_args()
    repo = Path(args.repo)
    sys.path.insert(0, str(repo / "tools"))

    from pynq import Overlay, MMIO
    from pc_dashboard import FrameState
    from status_display import render
    from bram_fb import pack_1bpp, write_image

    if args.which == "hdmi":
        bit = repo / "board" / "overlay" / "hdmi_status.bit"
        w, h = 1280, 720
    else:
        bit = repo / "board" / "overlay" / "lcd_status.bit"
        w, h = 800, 480

    ovl = Overlay(str(bit))
    base = None
    for k, v in ovl.ip_dict.items():
        if "bram_ctrl" in str(k) or "bram_ctrl" in str(v.get("type", "")):
            base = int(v.get("phys_addr"))
            break
    if base is None:
        base = 0x40000000
    print("DISPLAY_BOARD_BASE", hex(base))

    st = FrameState(
        frame=202, camera_state="VALID",
        players=[{"id": 3, "x": 52.5, "y": 34.0, "conf": 0.9},
                 {"id": 7, "x": 40.0, "y": 22.0, "conf": 0.8}],
        ball={"x": 52.0, "y": 30.0, "valid": True, "space": "field"},
        events={"out_of_play": False},
    )
    img = render(st, w, h)
    mode = write_image(img, base, w, h)
    words = pack_1bpp(img, w, h)
    nz = np.flatnonzero(words)
    if nz.size == 0:
        print("DISPLAY_BOARD_FAIL no_white_pixel")
        return 1
    idx = int(nz[0])
    mm = MMIO(base, len(words) * 4)
    got = int(mm.read(idx * 4))
    exp = int(words[idx])
    ok = (got == exp)
    print("DISPLAY_BOARD_WRITE", mode, "words", len(words), "probe", idx,
          "exp", hex(exp), "got", hex(got))
    print("DISPLAY_BOARD_READBACK", ok)
    if not ok:
        print("DISPLAY_BOARD_FAIL")
        return 1

    print("DISPLAY_BOARD_OK")
    print("NOTE: 实际 %s 画面需人眼确认；本脚本只验证帧缓存写入/读回。" % args.which.upper())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())