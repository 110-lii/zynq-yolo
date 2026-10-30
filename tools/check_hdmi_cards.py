#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_hdmi_cards.py —— HDMI 画面的**人眼判据**工具：一张一张放色卡。

为什么需要它
------------
2026-10-30 上板踩到一次很典型的事故：`frames` 60 帧/s 稳定增长、`under=0`、
帧缓存地址和 `pack` 解出来全对 —— **机器判据全部通过，屏上却是纯白**。
那一课写进了记录：`BRAM_OK` 不能替代 `VISUAL_OK`。

但「人眼看」也要有章法，否则「好像有画面」和「根本是白的」分不清。这个工具把
判定压缩成**读屏上的字**：红/绿/蓝/白/黑五张卡，每张正中一行大字 `STEPn-NAME`。
人只要报出「看到了哪几个 STEP」，就能一次区分三种情况：

    STEP1..5 依次出现   ⇒ 通路接通（仍有内容问题另说）
    一直是白的          ⇒ PL 扫出来的仍是常数（多半是 cache 没刷 / 帧缓存没写进去）
    一直是黑的 / 无信号 ⇒ 时序或 TMDS 没起来，另查

用法（板端）
------------
    python3 tools/check_hdmi_cards.py                      # 五张卡各 12 s，循环 5 分钟
    python3 tools/check_hdmi_cards.py --seconds 20 --rounds 10
    python3 tools/check_hdmi_cards.py --bit .../hdmi_video.bit

判据令牌
--------
    HDMI_CARDS_READY bit=<path> bufs=<phys,...> pack=0x..
    HDMI_CARDS_PHASE <n> <NAME> frames=<f> under=<u>
    HDMI_CARDS_DONE rounds=<n> frames=<f> under=<u>
    HDMI_CARDS_FAIL <reason>
"""
import argparse
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cv2
import numpy as np

BIT_DEFAULT = "/home/xilinx/zynq-yolo/board/overlay/flow_system.bit"

#: (名字, BGR)。白/黑两张必须有：白卡能区分「真的画了白」和「扫出来的常数刚好是白」，
#: 黑卡能区分「画了黑」和「没信号」。缺一张，结论就会含糊。
CARDS = [
    ("STEP1-RED", (0, 0, 255)),
    ("STEP2-GREEN", (0, 255, 0)),
    ("STEP3-BLUE", (255, 0, 0)),
    ("STEP4-WHITE", (255, 255, 255)),
    ("STEP5-BLACK", (0, 0, 0)),
]

#: 收尾卡：五张测完必须停在**有内容**的一屏上。
#: 2026-10-30 20:30 踩到：脚本正常跑完（frames=4580 under=0），但最后一张是 STEP5-BLACK，
#: 屏上就那么黑着 —— 人过去看，报的是「HDMI 没有画面」。黑卡是用来区分「画了黑」和
#: 「没信号」的，但它不能是最后一眼。收尾卡给白底大字，扫不出来就真是没信号。
BANNER = "zynq-yolo HDMI 5/5 OK"


def card(name, bgr, w, h):
    img = np.zeros((h, w, 3), np.uint8)
    img[:, :] = bgr
    fg = (0, 0, 0) if sum(bgr) > 380 else (255, 255, 255)
    cv2.putText(img, name, (int(w * 0.09), int(h * 0.36)),
                cv2.FONT_HERSHEY_SIMPLEX, 3.0, fg, 8)
    cv2.putText(img, "zynq-yolo HDMI", (int(w * 0.09), int(h * 0.47)),
                cv2.FONT_HERSHEY_SIMPLEX, 1.4, fg, 4)
    return img


def banner(w, h):
    """收尾横幅：白底、黑字、够大，目的是「跑完之后屏上一直有东西」。"""
    img = np.full((h, w, 3), 255, np.uint8)
    for i in range(8):                        # 上下两条彩带，顺便证明不是常数填充
        img[0:24, i * (w // 8):(i + 1) * (w // 8)] = \
            [(0, 0, 255), (0, 255, 0), (255, 0, 0), (0, 255, 255),
             (255, 0, 255), (255, 255, 0), (0, 0, 0), (128, 128, 128)][i]
        img[h - 24:h, i * (w // 8):(i + 1) * (w // 8)] = \
            [(0, 0, 255), (0, 255, 0), (255, 0, 0), (0, 255, 255),
             (255, 0, 255), (255, 255, 0), (0, 0, 0), (128, 128, 128)][i]
    cv2.putText(img, BANNER, (int(w * 0.05), int(h * 0.45)),
                cv2.FONT_HERSHEY_SIMPLEX, 2.6, (0, 0, 0), 7)
    cv2.putText(img, "Steps 1-5 done; screen stays like this", (int(w * 0.05), int(h * 0.60)),
                cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 0), 3)
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bit", default=BIT_DEFAULT)
    ap.add_argument("--cfg", type=lambda s: int(s, 0), default=None,
                    help="HDMI cfg 基址（默认从 overlay 按 IP 名解）")
    ap.add_argument("--st", type=lambda s: int(s, 0), default=None)
    ap.add_argument("--seconds", type=float, default=12.0, help="每张卡放多久")
    ap.add_argument("--rounds", type=int, default=5, help="循环几轮")
    ap.add_argument("--hold", type=float, default=30.0,
                    help="五张卡播完后停在横幅上的秒数（0 会停在黑卡上，别设）")
    args = ap.parse_args()

    if not Path(args.bit).is_file():
        print("HDMI_CARDS_FAIL no_bit %s" % args.bit)
        return 2
    from pynq import Overlay
    from hdmi_video import HdmiVideoOut, W, H

    ov = Overlay(args.bit)

    def A(needle, dflt):
        for n, i in (getattr(ov, "ip_dict", None) or {}).items():
            if needle in str(n):
                a = i.get("phys_addr")
                if isinstance(a, int):
                    return a
        return dflt

    cfg = args.cfg if args.cfg is not None else A("gpio_hdmi_cfg", 0x41200000)
    st = args.st if args.st is not None else A("gpio_hdmi_st", 0x41210000)
    out = HdmiVideoOut(cfg_addr=cfg, st_addr=st)
    print("HDMI_CARDS_READY bit=%s bufs=%s pack=0x%08X cacheable=%s"
          % (args.bit, ",".join("0x%08X" % int(b.physical_address) for b in out.bufs),
             out.pack, [getattr(b, "cacheable", "?") for b in out.bufs]))

    # 先证「配置进得去」。这一步不过，后面 frames/under 再漂亮也没有意义：
    # 2026-10-30 就是 cfg_pack 读回 0（写的是 0x0D001400）而屏上一片黑。
    cfg_ok = out.check_cfg()
    print("HDMI_CARDS_CFG addr_ok=%d pack_ok=%d" % (int(cfg_ok["addr"]), int(cfg_ok["pack"])))
    if not (cfg_ok["addr"] and cfg_ok["pack"]):
        print("HDMI_CARDS_FAIL cfg_readback_mismatch（配置没写进 PL："
              "先查 GPIO 地址表，再谈画面）")

    imgs = [card(n, c, W, H) for n, c in CARDS]
    tail_img = banner(W, H)
    t_all = time.time()
    try:
        for r in range(max(1, args.rounds)):
            for i, (name, _c) in enumerate(CARDS):
                img = imgs[i]
                t_end = time.time() + max(0.5, args.seconds)
                n = 0
                while time.time() < t_end:
                    out.show(img)
                    n += 1
                    time.sleep(0.12)
                s = out.status()
                print("HDMI_CARDS_PHASE %d %-11s frames=%d under=%d shown=%d"
                      % (i + 1, name, s["frames"], s["under"], n))
        # 收尾：停在横幅上，别停在黑卡上（见 BANNER 的注释）
        t_end = time.time() + max(5.0, args.hold)
        n = 0
        while time.time() < t_end:
            out.show(tail_img)
            n += 1
            time.sleep(0.12)
        s = out.status()
        print("HDMI_CARDS_HOLD %-20s frames=%d under=%d shown=%d"
              % (BANNER, s["frames"], s["under"], n))
    except KeyboardInterrupt:
        print("HDMI_CARDS_NOTE interrupted")
    s = out.status()
    print("HDMI_CARDS_DONE rounds=%d frames=%d under=%d elapsed=%.0fs"
          % (args.rounds, s["frames"], s["under"], time.time() - t_all))
    print("HDMI_CARDS_NOTE 机器侧到此为止 —— 屏上看到哪几张卡，请人报一下；"
          "没看到 STEP1..5 就不算 VISUAL_OK。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
