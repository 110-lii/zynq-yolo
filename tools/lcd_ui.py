#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lcd_ui.py —— 赛前设置界面（球衣颜色 / 裁判 / 半场），板端跑，屏上直接操作。

为什么用按键而不是触摸：这块板的 PL 只有两颗键（key[0]=L14、key[1]=K16，
见 `board_pinout_navigator_zynq_io_v3.7.xdc` 的 PL_KEY 段），LCD 没有触摸控制器。
约定：**key[0] = 下一项，key[1] = 换选项（在最后一项上 = 确认保存）**。

★ 与 D013 的「逻辑真源」同一份 schema（`zynq-yolo/match-config/2`）：
`tools/match_setup.py` 定义并校验它、网页前端是它的模拟，这里是把同一份东西
搬到真机屏上。颜色名一律取自 `classify_players.PALETTE_REF`（拼错会静默变
unknown，所以**不允许自由输入**，只能从这 10 个里翻）。

显示：800x480 4bpp（16 色）。调色板由本文件显式给出 —— 10 种球衣色必须**精确**
在板内，否则量化会把红/橙、蓝/紫这类相近色并掉。图像字后紧跟 16 个调色板字，
与 `bram_fb.write_image4` / RTL 的 PAL_BASE 口径一致。

用法（板端）：
    python3 tools/lcd_ui.py --once                      # 只画一次（看版面）
    python3 tools/lcd_ui.py                             # 按键交互（真板）
    python3 tools/lcd_ui.py --script 0,1,0,0,0,0,1      # 无按键时回放一串按键（自测）
    python3 tools/lcd_ui.py --keys                      # 只读一次按键电平，判极性

令牌：LCD_UI_READY / LCD_UI_DRAW / LCD_UI_SELECT / LCD_UI_COLOR / LCD_UI_SIDE /
      LCD_UI_SAVED / LCD_UI_KEYS / LCD_UI_FAIL
"""
import argparse
import colorsys
import json
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np

from status_display import _Painter as Painter  # noqa: E402  复用文字绘制与中文字体

W, H = 800, 480
KEYS_BASE = 0x41260000
SCHEMA = "zynq-yolo/match-config/2"

# ---------------------------------------------------------------- 颜色
#: 颜色名 → RGB。名字必须是 PALETTE_REF 的键；RGB 由它自己的 HSV 定义换算，
#: 这样「屏上看到的颜色」和「分类器判色的参考色」是同一个东西。
COLOR_ORDER = ["red", "blue", "green", "yellow", "orange", "purple",
               "cyan", "magenta", "white", "black"]
COLOR_ZH = {"red": "红", "blue": "蓝", "green": "绿", "yellow": "黄",
            "orange": "橙", "purple": "紫", "cyan": "青", "magenta": "品红",
            "white": "白", "black": "黑"}


def _color_rgb(name):
    try:
        import classify_players as kp
        h, s, v = kp.color_of_name(name)
    except Exception:                                   # noqa: BLE001
        h, s, v = None, 0.5, 0.8
    h = 0.0 if h is None else float(h)
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, float(s or 0.0), float(v or 0.0))
    return (int(round(r * 255)), int(round(g * 255)), int(round(b * 255)))


RGB = {n: _color_rgb(n) for n in COLOR_ORDER}

# 界面自身的 16 色调色板：6 个界面色 + 10 个球衣色（正好 16）
BG = (26, 30, 36)
HEADER_BG = (45, 52, 64)
ROW_A = (38, 43, 52)
ROW_B = (31, 35, 43)
LABEL_FG = (168, 176, 190)
HILITE = (90, 150, 210)
WHITE = RGB["white"]
PALETTE = [BG, HEADER_BG, ROW_A, ROW_B, LABEL_FG, HILITE] + [RGB[n] for n in COLOR_ORDER]

# 行定义：(标题, 状态字段, 类型)
ITEMS = [
    ("A 队球衣", "kit_a", "color"),
    ("B 队球衣", "kit_b", "color"),
    ("A 队守门员", "gk_a", "color"),
    ("B 队守门员", "gk_b", "color"),
    ("裁判衣服", "ref_color", "color"),
    ("A 队上半场", "a_side", "side"),
    ("开始比赛", None, "go"),
]
SIDES = ["left", "right"]
SIDE_ZH = {"left": "左半场", "right": "右半场"}

DEFAULT_STATE = {"sel": 0, "kit_a": "red", "kit_b": "blue", "gk_a": "green",
                 "gk_b": "yellow", "ref_color": "black", "a_side": "left"}


def _rect(img, x0, y0, x1, y1, color):
    img[max(0, y0):max(0, y1), max(0, x0):max(0, x1)] = color


def _frame(img, x0, y0, x1, y1, color, t=2):
    _rect(img, x0, y0, x1, y0 + t, color)
    _rect(img, x0, y1 - t, x1, y1, color)
    _rect(img, x0, y0, x0 + t, y1, color)
    _rect(img, x1 - t, y0, x1, y1, color)


def _lum(c):
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


def render(state):
    """返回 (BGR 图, RGB 图, backend)。"""
    img = np.zeros((H, W, 3), dtype=np.uint8)
    img[:, :] = BG

    head = 52
    _rect(img, 0, 0, W, head, HEADER_BG)
    _rect(img, 0, head - 2, W, head, HILITE)
    p = Painter(img)
    p.text(18, 14, "赛前设置 · 球衣与半场", 24, WHITE, bold=True)
    p.text_right(W - 18, 18, time.strftime("%H:%M:%S"), 15, LABEL_FG)

    top = head + 10
    bottom = H - 34
    gap = 5
    rh = (bottom - top - gap * (len(ITEMS) - 1)) // len(ITEMS)
    for i, (name, field, kind) in enumerate(ITEMS):
        y0 = top + i * (rh + gap)
        sel = (i == state["sel"])
        _rect(img, 14, y0, W - 14, y0 + rh, ROW_A if i % 2 == 0 else ROW_B)
        if sel:
            _frame(img, 14, y0, W - 14, y0 + rh, HILITE, t=3)
            _rect(img, 14, y0, 20, y0 + rh, HILITE)
        p.text(34, y0 + rh // 2 - 11, name, 20, WHITE if sel else LABEL_FG)

        if kind == "color":
            cname = state[field]
            rgb = RGB[cname]
            sw = 46
            sy0 = y0 + (rh - sw) // 2
            _rect(img, 240, sy0, 240 + sw, sy0 + sw, rgb)
            _frame(img, 240, sy0, 240 + sw, sy0 + sw, LABEL_FG, t=1)
            label = "%s %s" % (COLOR_ZH.get(cname, cname), cname.upper())
            p.text(304, y0 + rh // 2 - 12, label, 21,
                   rgb if _lum(rgb) >= 70 else LABEL_FG)
        elif kind == "side":
            side = state["a_side"]
            _rect(img, 240, y0 + 6, 330, y0 + rh - 6, HILITE if side == "left" else BG)
            _rect(img, 330, y0 + 6, 420, y0 + rh - 6, HILITE if side == "right" else BG)
            _frame(img, 240, y0 + 6, 420, y0 + rh - 6, LABEL_FG, t=1)
            p.text(252, y0 + rh // 2 - 10, "左", 18, WHITE)
            p.text(342, y0 + rh // 2 - 10, "右", 18, WHITE)
            p.text(444, y0 + rh // 2 - 11, SIDE_ZH[side], 20, WHITE)
        else:
            p.text(240, y0 + rh // 2 - 11, "按 KEY1 确认并保存", 20,
                   WHITE if sel else LABEL_FG)

    # 4bpp 没有二值化，小字不需要描边粗体（描了反而发糊）
    p.text(16, H - 26, "KEY0 = 下一项    KEY1 = 换选项 / 确认", 15, LABEL_FG)
    p.text_right(W - 16, H - 26, "LCD 800x480 4bpp", 15, LABEL_FG)
    rgb_img = p.flush()
    return rgb_img[:, :, ::-1].copy(), rgb_img, p.backend


def draw(state, base=0x40000000, preview=None):
    """写屏。base=None ⇒ 只渲染不写（主机侧预览）。返回 (mode, pal, backend)。"""
    bgr, rgb, backend = render(state)
    mode, pal = "preview-only", []
    if base is not None:
        import bram_fb
        mode, pal = bram_fb.write_image4(bgr, base, 800, 480, palette=PALETTE)
    if preview:
        from PIL import Image
        if base is None:
            # 主机侧预览存「面板上真正看到的」：按调色板量化再还原
            import bram_fb
            words, _pw = bram_fb.pack_4bpp(bgr, 800, 480, palette=PALETTE)
            rows = np.asarray(words, dtype=np.uint32).reshape(480, 100)
            idx = np.zeros((480, 800), dtype=np.uint8)
            for k in range(8):
                idx[:, k::8] = (rows >> np.uint32(4 * k)) & 0xF
            lut = np.array(PALETTE, dtype=np.uint8)
            Image.fromarray(lut[idx]).save(preview)
        else:
            Image.fromarray(rgb).save(preview)
    return mode, pal, backend


def build_config(state):
    """组装 v2 schema（与 match_setup.py 同一份结构）。"""
    return {
        "schema": SCHEMA,
        "source": "lcd_ui",
        "teams": {
            "A": {"name": "A 队", "kit_color": state["kit_a"],
                  "keeper_color": state["gk_a"]},
            "B": {"name": "B 队", "kit_color": state["kit_b"],
                  "keeper_color": state["gk_b"]},
        },
        "referee": {"kit_color": state["ref_color"]},
        "first_half": {"a_side": state["a_side"], "swap_at_halftime": True},
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def validate(payload):
    """尽量复用 match_setup 的校验；它不在就直接看两条硬约束。"""
    try:
        import match_setup
        probs, warns = match_setup._validate(payload)      # noqa: SLF001
        return probs, warns
    except Exception:                                      # noqa: BLE001
        probs, warns = [], []
        a = payload["teams"]["A"]["kit_color"]
        b = payload["teams"]["B"]["kit_color"]
        if a == b:
            probs.append("same_team_color %s" % a)
        if payload["first_half"]["a_side"] not in SIDES:
            probs.append("bad_a_side %r" % payload["first_half"]["a_side"])
        return probs, warns


def save_config(state, path):
    cfg = build_config(state)
    probs, warns = validate(cfg)
    if probs:
        return None, probs, warns
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg, probs, warns


class Keys:
    """按键读取：极性未知也能用 —— 以「第一次读到的电平」为空闲态。"""

    def __init__(self, base=KEYS_BASE):
        from pynq import MMIO
        self.mm = MMIO(base, 0x1000)
        self.idle = self.raw()
        self.stable = self.idle
        self.since = time.time()

    def raw(self):
        return int(self.mm.read(0)) & 0x3

    def poll(self, debounce=0.03):
        """返回自上次调用以来「按下的键」序号列表（0/1）。"""
        v = self.raw()
        now = time.time()
        if v != self.stable:
            self.stable = v
            self.since = now
            return []
        if now - self.since < debounce or v == self.idle:
            return []
        pressed = (~v) & self.idle & 0x3        # 与空闲态相反的位 = 被按下
        self.idle = v                            # 按下的电平成为新「空闲」，等松开再翻转
        return [b for b in (0, 1) if pressed & (1 << b)]


def apply_key(state, key):
    """key: 0 = 下一项，1 = 换选项/确认。返回 True 表示要保存退出。"""
    name, field, kind = ITEMS[state["sel"]]
    if key == 0:
        state["sel"] = (state["sel"] + 1) % len(ITEMS)
        print("LCD_UI_SELECT n=%d item=%s" % (state["sel"], ITEMS[state["sel"]][0]))
        return False
    if kind == "go":
        return True
    if kind == "side":
        state["a_side"] = SIDES[(SIDES.index(state["a_side"]) + 1) % len(SIDES)]
        print("LCD_UI_SIDE item=%s value=%s" % (name, state["a_side"]))
        return False
    cur = COLOR_ORDER.index(state[field])
    state[field] = COLOR_ORDER[(cur + 1) % len(COLOR_ORDER)]
    print("LCD_UI_COLOR item=%s value=%s" % (name, state[field]))
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fb-bram", default="0x40000000")
    ap.add_argument("--cfg", default="/home/xilinx/zynq-yolo/board/ui_config.json")
    ap.add_argument("--once", action="store_true", help="只画一次就退出")
    ap.add_argument("--script", help="无按键时回放按键序列，如 0,1,0,0,0,0,1")
    ap.add_argument("--keys", action="store_true", help="只读一次按键电平并退出")
    ap.add_argument("--preview", default="", help="同时存一张 PNG")
    ap.add_argument("--preview-only", action="store_true",
                    help="只渲染存 PNG，不碰 BRAM（主机侧用，没有 pynq 也能跑）")
    ap.add_argument("--timeout", type=float, default=0.0, help="交互模式的超时秒数（0=不限）")
    args = ap.parse_args()

    state = dict(DEFAULT_STATE)
    print("LCD_UI_READY items=%d colors=%d schema=%s"
          % (len(ITEMS), len(COLOR_ORDER), SCHEMA))

    if args.preview_only:
        if not args.preview:
            args.preview = str(Path(__file__).resolve().parent.parent / "board" / "fb" / "ui_preview.png")
        if not args.script:
            args.once = True
    base = None if args.preview_only else int(args.fb_bram, 0)

    if args.keys:
        from pynq import MMIO
        v = int(MMIO(KEYS_BASE, 0x1000).read(0)) & 0x3
        print("LCD_UI_KEYS raw=0x%X key0=%d key1=%d（松开时读到的电平即空闲态）" % (v, v & 1, (v >> 1) & 1))
        return 0

    if args.once:
        mode, pal, backend = draw(state, base, args.preview or None)
        print("LCD_UI_DRAW once mode=%s backend=%s preview=%s"
              % (mode, backend, args.preview or "-"))
        return 0

    mode, pal, backend = draw(state, base, args.preview or None)
    print("LCD_UI_DRAW mode=%s sel=0 backend=%s" % (mode, backend))

    def finish():
        cfg, probs, warns = save_config(state, args.cfg)
        if cfg is None:
            print("LCD_UI_FAIL invalid %s" % ",".join(probs))
            return 1
        for w in warns:
            print("LCD_UI_WARN %s" % w)
        t = cfg["teams"]
        print("LCD_UI_SAVED %s a=%s b=%s gk=%s/%s ref=%s side=%s"
              % (args.cfg, t["A"]["kit_color"], t["B"]["kit_color"],
                 t["A"]["keeper_color"], t["B"]["keeper_color"],
                 cfg["referee"]["kit_color"], cfg["first_half"]["a_side"]))
        return 0

    t0 = time.time()
    if args.script:
        for tok in args.script.split(","):
            tok = tok.strip()
            if not tok:
                continue
            if apply_key(state, int(tok)):
                return finish()
            mode, _, _ = draw(state, base, args.preview or None)
            print("LCD_UI_DRAW mode=%s sel=%d" % (mode, state["sel"]))
        print("LCD_UI_FAIL script_ended_without_confirm")
        return 1

    keys = Keys()
    print("LCD_UI_KEYS idle=0x%X" % keys.idle)
    while True:
        for k in keys.poll():
            if apply_key(state, k):
                return finish()
            mode, _, _ = draw(state, base, args.preview or None)
            print("LCD_UI_DRAW mode=%s sel=%d" % (mode, state["sel"]))
        if args.timeout and time.time() - t0 > args.timeout:
            print("LCD_UI_FAIL timeout")
            return 1
        time.sleep(0.02)


if __name__ == "__main__":
    raise SystemExit(main())
