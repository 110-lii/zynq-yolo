#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""板上验收：1bpp 帧缓存写入 + AXI 逐字读回（单屏 / 双屏合并 overlay 都支持）。

板上运行：
    # 合并版（一次验两块屏；两套帧缓存基址独立）
    sudo -E python3 tools/check_display_board.py --which both \
        --bit board/overlay/display_both.bit --pattern chess

    # 单屏
    python3 tools/check_display_board.py --which hdmi                # 状态面板
    python3 tools/check_display_board.py --which lcd --pattern white

它验证：overlay 能加载、找得到 BRAM 控制器基址、1bpp 数据写得进且**逐字读回一致**。
**实际屏幕画面仍需人眼确认**；本脚本不会把"读回对"写成"显示对"。
判据令牌：末行 DISPLAY_BOARD_OK / DISPLAY_BOTH_BOARD_OK，否则 DISPLAY_BOARD_FAIL <原因>。

`--pattern` 三级阶梯（session 013/014 的教训，顺序别颠倒）：
    white  全 1 铺满      —— 第一级：证明 TMDS/LCD 信号通路（读回必须全 0xFFFFFFFF）
    chess  64x64 棋盘格   —— 第二级：证明寻址、分辨率与位序对
    black  全 0           —— 反色对照，确认不是"恒白"
    panel  状态面板渲染   —— 前三级都过后才做，人眼看信息是否齐全

本机自检（不需要板子）：python tools/check_display_board.py --selftest → DISPLAY_SELFTEST_OK
"""
import argparse
import sys
from pathlib import Path

sys.dont_write_bytecode = True

import numpy as np

SCREENS = {
    "hdmi": {"size": (1280, 720), "bit": "hdmi_status.bit", "key": "hdmi"},
    "lcd": {"size": (800, 480), "bit": "lcd_status.bit", "key": "lcd"},
}


def chessboard(w, h, blk=64):
    img = np.zeros((h, w, 3), dtype=np.uint8)
    for y in range(0, h, blk):
        for x in range(0, w, blk):
            if ((x // blk) + (y // blk)) % 2 == 0:
                img[y:y + blk, x:x + blk] = 255
    return img


def solid(w, h, value):
    return np.full((h, w, 3), value, dtype=np.uint8)


def panel_image(w, h):
    from pc_dashboard import FrameState
    from status_display import render
    st = FrameState(
        frame=202, camera_state="VALID",
        players=[{"id": 3, "x": 52.5, "y": 34.0, "conf": 0.9},
                 {"id": 7, "x": 40.0, "y": 22.0, "conf": 0.8}],
        ball={"x": 52.0, "y": 30.0, "valid": True, "space": "field"},
        events={"out_of_play": False},
    )
    return render(st, w, h)


def make_image(pattern, w, h):
    if pattern == "chess":
        return chessboard(w, h)
    if pattern == "white":
        return solid(w, h, 255)
    if pattern == "black":
        return solid(w, h, 0)
    if pattern == "panel":
        return panel_image(w, h)
    raise ValueError("unknown pattern: %s" % pattern)


def _bram_cells(ovl):
    """列出所有可寻址 IP 的 (实例名, 基址)。

    PYNQ 2.x 在这里有个坑：`ovl.ip_dict` **只登记了 PS**（实测 display_both 的
    ip_dict 只有 processing_system7_0），BRAM 控制器的地址在 `ovl.mem_dict` 里，
    key 是实例名（hdmi_ctrl / lcd_ctrl）。只读 ip_dict 会得出"找不到控制器"，
    于是退回 0x40000000 写死基址 —— 双屏时 LCD 就被写到 HDMI 上。
    """
    out = []
    for name, info in (getattr(ovl, "mem_dict", None) or {}).items():
        addr = info.get("phys_addr")
        if addr is None:
            continue
        out.append((str(name), int(addr)))
    for name, info in ovl.ip_dict.items():
        addr = info.get("phys_addr")
        if addr is None or any(str(name) == n for n, _ in out):
            continue
        out.append((str(name), int(addr)))
    return out


def find_base(ovl, key):
    """按屏名找 BRAM 控制器基址。

    合并版的两个控制器叫 hdmi_ctrl / lcd_ctrl，单屏版叫 bram_ctrl —— 先按屏名筛，
    筛不到再退回"只有一个控制器"的情形，避免把 LCD 的基址写到 HDMI 上。
    """
    cells = _bram_cells(ovl)
    hit = [c for c in cells if key and key in c[0].lower()]
    if len(hit) == 1:
        return hit[0]
    if len(cells) == 1 and not hit:
        return cells[0]
    if hit:
        return hit[0]
    return None, None


def check_screen(ovl, which, pattern):
    from pynq import MMIO
    from bram_fb import pack_1bpp, write_image

    cfg = SCREENS[which]
    w, h = cfg["size"]
    name, base = find_base(ovl, cfg["key"])
    if base is None:
        print("DISPLAY_BOARD_FAIL no_bram_ctrl_for_%s" % which)
        return False
    print("DISPLAY_BOARD_BASE %s %s %s" % (which, name, hex(base)))

    img = make_image(pattern, w, h)
    words = pack_1bpp(img, w, h)
    if pattern == "white" and int(words.min()) != 0xFFFFFFFF:
        print("DISPLAY_BOARD_FAIL %s pack_not_all_ones" % which)
        return False
    if pattern == "black" and int(words.max()) != 0:
        print("DISPLAY_BOARD_FAIL %s pack_not_all_zeros" % which)
        return False

    mode = write_image(img, base, w, h)

    mm = MMIO(base, len(words) * 4)
    bad = 0
    first_bad = None
    for i in range(len(words)):
        got = int(mm.read(i * 4))
        if got != int(words[i]):
            bad += 1
            if first_bad is None:
                first_bad = (i, int(words[i]), got)
    print("DISPLAY_BOARD_WRITE %s mode=%s pattern=%s words=%d nonzero=%d"
          % (which, mode, pattern, len(words), int(np.count_nonzero(words))))
    if bad:
        i, e, g = first_bad
        print("DISPLAY_BOARD_READBACK %s FAIL mismatches=%d first=(%d exp=%s got=%s)"
              % (which, bad, i, hex(e), hex(g)))
        return False
    print("DISPLAY_BOARD_READBACK %s OK" % which)
    return True


def _selftest():
    from bram_fb import pack_1bpp
    ok = True
    words = pack_1bpp(chessboard(1280, 720), 1280, 720)
    ok &= words.shape == (28800,)
    ok &= int(words[0]) == 0xFFFFFFFF            # 左上块白
    ok &= int(words[64 * 40]) == 0x00000000      # 第 64 行最左块黑
    ok &= int(words[64 * 40 + 2]) == 0xFFFFFFFF  # x=64..95 为白
    white = pack_1bpp(solid(800, 480, 255), 800, 480)
    ok &= white.shape == (12000,) and int(white.min()) == 0xFFFFFFFF
    ok &= int(pack_1bpp(solid(800, 480, 0), 800, 480).max()) == 0
    print("DISPLAY_SELFTEST", "OK" if ok else "FAIL",
          "chess0=%s row64=%s" % (hex(int(words[0])), hex(int(words[64 * 40]))))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--which", choices=["hdmi", "lcd", "both"], default="both")
    ap.add_argument("--bit", help="overlay 的 .bit 路径（默认按 --which 取单屏 bit）")
    ap.add_argument("--repo", default="/home/xilinx/zynq-yolo")
    ap.add_argument("--pattern", choices=["chess", "white", "black", "panel"],
                    default="chess")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()

    repo = Path(args.repo)
    sys.path.insert(0, str(repo / "tools"))
    from pynq import Overlay

    if args.which == "both" and not args.bit:
        print("DISPLAY_BOARD_FAIL need_bit_for_both "
              "(--bit board/overlay/display_both.bit)")
        return 1

    if args.bit:
        bit = Path(args.bit)
        if not bit.is_absolute():
            bit = repo / bit
    else:
        bit = repo / "board" / "overlay" / SCREENS[args.which]["bit"]
    if not bit.exists():
        print("DISPLAY_BOARD_FAIL no_bit %s" % bit)
        return 1

    ovl = Overlay(str(bit))
    print("DISPLAY_BOARD_BIT", bit.name)
    targets = ["hdmi", "lcd"] if args.which == "both" else [args.which]
    ok = all([check_screen(ovl, t, args.pattern) for t in targets])
    if not ok:
        print("DISPLAY_BOARD_FAIL screen_readback")
        return 1
    token = "DISPLAY_BOTH_BOARD_OK" if args.which == "both" else "DISPLAY_BOARD_OK"
    print("%s bit=%s pattern=%s screens=%s"
          % (token, bit.name, args.pattern, ",".join(targets)))
    print("NOTE: 实际画面需人眼确认；本脚本只验证帧缓存写入/读回。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
