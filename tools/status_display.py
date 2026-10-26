#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""status_display.py —— 把板端结果渲染成 HDMI/LCD 状态面板。

支持两条落地路径：
  1. **Linux framebuffer**：`--fb /dev/fb0`（HDMI 或 LCD，取决于镜像是否把
     对应显示控制器注册成 fbdev）。自动按 `bits_per_pixel` 转 32bpp BGRA 或
     16bpp RGB565；
  2. **PNG/窗口**：`--png out.png` 或 `--window`，用于 PC 预览、无 fbdev 的板子、
     或提交材料截图。

注意：当前 PYNQ 镜像若只把 HDMI/LCD 接到 PL、未注册 fbdev，本脚本会明确打印
`STATUS_DISPLAY_NO_FB`；此时需要 `track_preproc` 之外的显示 overlay（VDMA+TMDS
或 RGB LCD 控制器），属于下一步 PL 出片工作（见 report/design_report.md §8）。

自检：
    python tools/status_display.py --selftest
    # 末行 STATUS_DISPLAY_OK
"""
import argparse
import glob
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.dont_write_bytecode = True

from pc_dashboard import FrameState, normalize, read_jsonl


def follow_jsonl(path, from_start=False):
    """尾随 JSONL（ResultStore 正在写），逐条 yield FrameState。

    支持通配符（如 `/mnt/sd/run_*.jsonl`）：文件轮转时自动切到最新的一个。
    """
    pattern = str(path)
    is_glob = any(ch in pattern for ch in "*?[")
    f = None
    current = None
    try:
        while True:
            if is_glob:
                files = sorted(glob.glob(pattern))
                if not files:
                    print("STATUS_DISPLAY_WAIT_FILE %s" % pattern)
                    time.sleep(0.5)
                    continue
                newest = files[-1]
                if newest != current:
                    if f is not None:
                        f.close()
                    current = newest
                    f = open(current, "r", encoding="utf-8", errors="replace")
                    if not from_start:
                        f.seek(0, 2)
            else:
                p = Path(pattern)
                if f is None:
                    if not p.exists():
                        print("STATUS_DISPLAY_WAIT_FILE %s" % p)
                        time.sleep(0.5)
                        continue
                    f = open(p, "r", encoding="utf-8", errors="replace")
                    if not from_start:
                        f.seek(0, 2)

            line = f.readline()
            if not line:
                time.sleep(0.2)
                continue
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            st = normalize(obj)
            if st is not None:
                yield st
    finally:
        if f is not None:
            f.close()


def _nice_size(size):
    w, h = size.lower().split("x", 1)
    return int(w), int(h)


def render(st: FrameState, w=800, h=480):
    import cv2
    import numpy as np

    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = (24, 28, 34)
    # header
    cv2.rectangle(img, (0, 0), (w, int(h * 0.12)), (45, 52, 64), -1)
    cv2.putText(img, "ZYNQ-7020  MATCH MONITOR", (24, int(h * 0.08)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(img, "frame %s" % st.frame, (w - 220, int(h * 0.08)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (180, 200, 220), 2, cv2.LINE_AA)

    b = st.ball or {}
    bx = b.get("x")
    by = b.get("y")
    ball_txt = "%.1f, %.1f" % (bx, by) if b.get("valid") and bx is not None else "--"
    cards = [
        ("PLAYERS", str(len(st.players)), (80, 200, 255)),
        ("BALL", ball_txt, (80, 120, 255)),
        ("CAMERA", st.camera_state, (120, 255, 160)),
    ]
    cw = int((w - 64) / 3)
    for i, (label, value, color) in enumerate(cards):
        x0 = 24 + i * (cw + 8)
        y0 = int(h * 0.18)
        cv2.rectangle(img, (x0, y0), (x0 + cw, y0 + int(h * 0.26)), (38, 44, 54), -1)
        cv2.rectangle(img, (x0, y0), (x0 + cw, y0 + int(h * 0.26)), (70, 80, 95), 1)
        cv2.putText(img, label, (x0 + 14, y0 + 28), cv2.FONT_HERSHEY_SIMPLEX,
                    0.55, (150, 160, 175), 1, cv2.LINE_AA)
        scale = 1.15 if len(value) < 8 else 0.8
        cv2.putText(img, value, (x0 + 14, y0 + int(h * 0.19)), cv2.FONT_HERSHEY_SIMPLEX,
                    scale, color, 2, cv2.LINE_AA)

    # player id list
    y0 = int(h * 0.50)
    cv2.putText(img, "TRACKS", (24, y0), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                (150, 160, 175), 1, cv2.LINE_AA)
    ids = ["#%s %.0f,%.0f" % (p["id"], p["x"], p["y"]) for p in st.players[:12]]
    for i, txt in enumerate(ids):
        col = i % 3
        row = i // 3
        cv2.putText(img, txt, (24 + col * int(w / 3), y0 + 32 + row * 28),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (220, 225, 235), 1, cv2.LINE_AA)

    ev = st.events or {}
    if ev:
        txt = "EVENT " + str(ev.get("type") or ev.get("event") or
                             ("goal" if ev.get("goal") else "out"))
        cv2.putText(img, txt, (24, h - 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                    (0, 180, 255), 2, cv2.LINE_AA)

    cv2.putText(img, time.strftime("%H:%M:%S"), (w - 150, h - 18),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (160, 170, 185), 1, cv2.LINE_AA)
    return img


def _fb_info(fb):
    base = Path("/sys/class/graphics") / Path(fb).name
    bpp = 32
    try:
        bpp = int((base / "bits_per_pixel").read_text().strip())
    except Exception:
        pass
    return bpp


def write_fb(img, fb):
    import cv2
    import numpy as np

    bpp = _fb_info(fb)
    if bpp == 16:
        b, g, r = cv2.split(img)
        v = ((r.astype(np.uint16) >> 3) << 11) | ((g.astype(np.uint16) >> 2) << 5) | (b.astype(np.uint16) >> 3)
        data = v.astype("<u2").tobytes()
    elif bpp == 32:
        rgba = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
        data = rgba.tobytes()
    else:
        raise RuntimeError("unsupported fb bpp=%d" % bpp)
    with open(fb, "wb", buffering=0) as f:
        f.write(data)
    return bpp


def _selftest():
    st = FrameState(frame=42, camera_state="VALID",
                    players=[{"id": 3, "x": 52.5, "y": 34.0, "conf": 0.9}],
                    ball={"x": 48.0, "y": 30.0, "valid": True, "space": "field"},
                    events={"out_of_play": False})
    img = render(st, 800, 480)
    tmp = Path(tempfile.mkdtemp(prefix="status_display_")) / "dash.png"
    import cv2
    cv2.imwrite(str(tmp), img)
    ok = tmp.exists() and img.shape == (480, 800, 3)
    print("STATUS_DISPLAY_SELFTEST", "OK" if ok else "FAIL", img.shape)
    shutil.rmtree(tmp.parent, ignore_errors=True)
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", default="")
    ap.add_argument("--size", default="800x480", help="800x480 (LCD) 或 1280x720 (HDMI)")
    ap.add_argument("--fb", default="", help="/dev/fb0 或 /dev/fb1")
    ap.add_argument("--fb-bram", default="", help="HDMI 1bpp BRAM 基址，如 0x40000000")
    ap.add_argument("--also-lcd-base", default="", help="同时写 LCD 1bpp BRAM 基址（组合 overlay），如 0x40020000")
    ap.add_argument("--hdmi-bit", default="", help="加载 hdmi_status.bit 并自动取 BRAM 基址（兼容旧名）")
    ap.add_argument("--display-bit", default="", help="加载 hdmi_status/lcd_status.bit 并自动取 BRAM 基址")
    ap.add_argument("--png", default="", help="把最后一帧写成 PNG")
    ap.add_argument("--window", action="store_true")
    ap.add_argument("--fps", type=float, default=2.0)
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--follow", action="store_true", help="尾随正在写的 JSONL（实时刷新）")
    ap.add_argument("--from-start", action="store_true", help="follow 时从文件头读")
    ap.add_argument("--max-frames", type=int, default=0)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        raise SystemExit(_selftest())
    if not args.replay:
        ap.error("需要 --replay")
    bit = args.display_bit or args.hdmi_bit
    if bit:
        from pynq import Overlay
        ovl = Overlay(bit)
        base = None
        for k, v in ovl.ip_dict.items():
            if "bram_ctrl" in str(k) or "bram_ctrl" in str(v.get("type", "")):
                base = v.get("phys_addr")
                break
        if not args.fb_bram:
            args.fb_bram = hex(int(base if base is not None else 0x40000000))
        print("STATUS_DISPLAY_BIT %s base=%s" % (bit, args.fb_bram))

    w, h = _nice_size(args.size)
    if args.fb and not Path(args.fb).exists():
        print("STATUS_DISPLAY_NO_FB %s" % args.fb)
        args.fb = ""
    dt = 1.0 / max(0.1, args.fps)
    n = 0
    while True:
        got = 0
        iterable = (follow_jsonl(Path(args.replay), from_start=args.from_start)
                    if args.follow else read_jsonl(Path(args.replay)))
        for st in iterable:
            img = render(st, w, h)
            if args.fb:
                bpp = write_fb(img, args.fb)
                if n == 0:
                    print("STATUS_DISPLAY_FB %s bpp=%d" % (args.fb, bpp))
            if args.fb_bram:
                from bram_fb import write_image
                mode = write_image(img, int(args.fb_bram, 0), w, h)
                if n == 0:
                    print("STATUS_DISPLAY_BRAM %s via %s" % (args.fb_bram, mode))
            if args.also_lcd_base:
                from bram_fb import write_image
                img2 = render(st, 800, 480)
                mode2 = write_image(img2, int(args.also_lcd_base, 0), 800, 480)
                if n == 0:
                    print("STATUS_DISPLAY_LCD_BRAM %s via %s" % (args.also_lcd_base, mode2))
            if args.png:
                import cv2
                cv2.imwrite(args.png, img)
            if args.window:
                import cv2
                cv2.imshow("status", img)
                if cv2.waitKey(1) & 0xFF == 27:
                    return 0
            n += 1
            got += 1
            if args.max_frames and n >= args.max_frames:
                print("STATUS_DISPLAY_OK frames=%d" % n)
                return 0
            time.sleep(dt)
        if args.follow or not args.loop or got == 0:
            break
    print("STATUS_DISPLAY_OK frames=%d" % n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
