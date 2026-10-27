"""check_hdmi_video_board.py —— HDMI 视频环出的上板验收（机器判据 + 供人眼看的图案）。

分两步，机器只判第一步：
    1) 机器判据：装 overlay → 写彩条到 DDR 帧缓存 → 读 status，
       要求 `frames` 在涨（PL 正在扫描）且 `under` 不随帧数线性增长。
    2) 人眼判据：显示器上应看到 8 色彩条 + 顶部白条 + 移动的白块。
       **机器通过 ≠ 画面正确**（`DISPLAY_BOARD_OK` 只证明数字通路，见 docs/hdmi_video_osd.md §4）。

用法（板端）：
    sudo -E python3 tools/check_hdmi_video_board.py --bit /home/xilinx/hdmi_video.bit
    ... --seconds 3 --keep        # 跑完不清屏，留着给人看

判据令牌：HDMI_VIDEO_BOARD_OK frames=<n> under=<m> / HDMI_VIDEO_BOARD_FAIL <reason>
"""
import argparse
import os
import sys
import time

import numpy as np


def color_bars(w=1280, h=720):
    """8 色竖条 + 顶部白条 + 底部黑条：一眼能看出通道顺序/翻转/撕裂。"""
    img = np.zeros((h, w, 3), np.uint8)
    cols = [(255, 255, 255), (0, 255, 255), (255, 255, 0), (0, 255, 0),
            (255, 0, 255), (0, 0, 255), (255, 0, 0), (0, 0, 0)]
    bw = w // len(cols)
    for i, c in enumerate(cols):
        img[:, i * bw:(i + 1) * bw] = c
    img[:, len(cols) * bw:] = cols[0]
    img[0:60, :] = (255, 255, 255)
    img[h - 40:, :] = (0, 0, 0)
    return img


def with_marker(img, k):
    """叠一个随帧移动的白色方块：能看出画面在刷新，而不是卡住的一帧。"""
    h, w = img.shape[:2]
    out = img.copy()
    x = (k * 37) % (w - 120)
    out[h // 2 - 60:h // 2 + 60, x:x + 120] = (255, 255, 255)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bit", default="/home/xilinx/hdmi_video.bit")
    ap.add_argument("--seconds", type=float, default=3.0)
    ap.add_argument("--fps", type=float, default=8.0)
    ap.add_argument("--keep", action="store_true", help="结束时不清屏")
    args = ap.parse_args()

    try:
        from pynq import Overlay
    except Exception as e:
        print("HDMI_VIDEO_BOARD_FAIL no pynq: %s" % e)
        return 2
    # hdmi_video.py 与 check_display_board.py 一样是 `python3 tools/xxx.py` 直接跑的，
    # 所以按脚本目录找它（板端目录通常就是 /home/xilinx/zynq-yolo/tools）
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        from hdmi_video import HdmiVideoOut
    except Exception as e:
        print("HDMI_VIDEO_BOARD_FAIL import hdmi_video: %s" % e)
        return 2

    ov = Overlay(args.bit)
    out = HdmiVideoOut()
    st0 = out.status()
    t_end = time.perf_counter() + args.seconds
    k = 0
    while time.perf_counter() < t_end:
        out.show(with_marker(color_bars(), k))
        k += 1
        time.sleep(max(0.0, 1.0 / max(0.5, args.fps)))
    st1 = out.status()
    df = st1["frames"] - st0["frames"]
    du = st1["under"] - st0["under"]
    # 判据：30 fps 输出下 3 秒应有 ~90 帧；给足容差（PS 写缓存会拖慢，但扫描不能停）
    ok = df > 10 and du <= max(2, df // 10)
    if not args.keep:
        pass          # 不清屏：彩条留着看
    if ok:
        print("HDMI_VIDEO_BOARD_OK frames=%d under=%d (delta frames=%d under=%d)"
              % (st1["frames"], st1["under"], df, du))
        print("人眼判据：显示器应看到 8 色彩条 + 顶部白条 + 中部移动白块")
        return 0
    print("HDMI_VIDEO_BOARD_FAIL frames=%d under=%d (delta frames=%d under=%d)"
          % (st1["frames"], st1["under"], df, du))
    return 1


if __name__ == "__main__":
    sys.exit(main())
