#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""status_display.py —— 把板端结果渲染成 HDMI/LCD「完整状态面板」。

为什么要它
----------
板子在场上跑的时候没有显示器、也不能每帧盯串口。裁判/评委需要一眼看到
「现在到底在干什么」：跑到第几帧、场上有几个人、球看没看到、刚才出了什么
事件、镜头是不是被切走了、结果写到哪个文件、PL 加速有没有真的在跑、
整体帧率是多少。本脚本把 JSONL（`tools/result_store.py` 写的行）变成一张
8 行状态面板，落到屏上或 PNG。

面板 8 项（固定顺序）
---------------------
  1. 帧号 / 时间戳                 5. 机位状态（有效/无效 + 原因）
  2. 场上人数（含 track_id）       6. 存储状态（JSONL 文件、累计行数、fsync 失败）
  3. 球状态（可见 + 球场 x,y / 未见） 7. PL 状态（preproc / track 是否启用）
  4. 最新事件（出界/进球/球权 + 时刻） 8. 运行时长与帧率

**数据纪律**：JSONL 里没有对应字段一律显示 `—`，不猜、不补默认值。例如
`fsync 失败` 只有记录自带 `fsync_error`/`fsync_failed`/`store_error` 时才显示，
`track` 只认 `summary.json` 的 `pl_track`，都没有就 `—`。

两条落地路径（保持原有能力）
----------------------------
  1. **Linux framebuffer**：`--fb /dev/fb0`，按 `bits_per_pixel` 转 32bpp BGRA /
     16bpp RGB565；
  2. **PNG**：`--png-out out.png`（PC 预览、无 fbdev 的板子、提交材料截图）；
  另保留 PL BRAM 1bpp 路径（`--fb-bram` / `--also-lcd-base`）。面板配色刻意让
  标签与正文亮度都 > 128，1bpp 二值化后仍然可读（不会出现整块黑）。

尺寸与字体
----------
`--size 800x480`（LCD）或 `--size 1280x720`（HDMI），也可写
`--size 800x480,1280x720` 一次出两种（此时 `--png-out` 支持 `{size}` 占位）。
版面按 800x480 设计，再整体乘缩放系数，字号/行高/边距等比放大。

中文字：有 CJK 字体时（`--font`、`STATUS_DISPLAY_FONT` 或系统常见路径，含
Windows msyh/simhei、Linux wqy/Noto）用 PIL 画中文标签；找不到字体就自动退回
OpenCV 内置 Hershey 字体 + **英文标签**。两条路都不会出现方块乱码。

用法
----
    # 板上：LCD 面板直写 framebuffer
    python3 tools/status_display.py --replay /mnt/sd/zynq-yolo/run_*.jsonl --fb /dev/fb0

    # 板上：HDMI（PL BRAM overlay）
    python3 tools/status_display.py --replay /mnt/sd/run_*.jsonl --size 1280x720 \
        --fb-bram 0x40000000 --once

    # PC：一次出两种尺寸的 PNG（--once = 读一遍就退出）
    py tools/status_display.py --replay run_*.jsonl --size 800x480,1280x720 \
        --png-out D:/out/dash_{size}.png --once

    # 实时尾随（ResultStore 正在写）
    python3 tools/status_display.py --replay /mnt/sd/run_*.jsonl --follow --fb /dev/fb0

判据令牌
--------
    STATUS_DISPLAY_SELFTEST OK    自检通过（两种尺寸各出一张 PNG）
    STATUS_DISPLAY_OK frames=N    正常结束（附累计行数与尺寸）
    STATUS_DISPLAY_PNG <path>     已写出 PNG
    STATUS_DISPLAY_FB <dev>       已写 framebuffer
    STATUS_DISPLAY_BRAM <addr>    已写 PL BRAM
    STATUS_DISPLAY_NO_FB <dev>    framebuffer 不存在（退化为 PNG）
    STATUS_DISPLAY_NO_FILE <pat>  找不到 JSONL
    STATUS_DISPLAY_NO_SINK        既没有 fb/BRAM/PNG/window，面板无处可落

自检（不需要板子、不需要显示器）：
    py tools/status_display.py --selftest
    # 末行 STATUS_DISPLAY_SELFTEST OK + 两张 PNG 的绝对路径
"""
import argparse
import glob
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.dont_write_bytecode = True

from pc_dashboard import FrameState, normalize, read_jsonl  # noqa: E402

DESIGN_W, DESIGN_H = 800, 480

# 自检产物默认落盘目录（写不进去就退到临时目录，见 _selftest）
SCRATCH_DIR = "D:/NousPortal/hermes/cache/scratch"

# 面板配色（BGR）。刻意全部保持亮度 > 128：1bpp BRAM 路径二值化后仍可读。
BG = (34, 28, 24)
HEADER_BG = (64, 52, 45)
ROW_A = (43, 36, 32)
ROW_B = (36, 30, 26)
LABEL_FG = (160, 148, 140)
WHITE = (248, 240, 235)
C_FRAME = (225, 210, 200)
C_PLAYERS = (80, 200, 255)
C_BALL = (255, 140, 90)
C_EVENT = (255, 200, 60)
C_CAMERA = (160, 255, 140)
C_STORE = (255, 180, 190)
C_PL = (120, 170, 255)
C_RUNTIME = (195, 180, 170)

# 字体/语言（main() 里允许命令行覆盖）
FONT_OVERRIDE = ""
LANG = "auto"
_FONT_CACHE = []

ZERO = "—"          # 缺字段的统一占位符（PIL 中文字体有该字形）
ZERO_ASCII = "-"    # cv2 Hershey 没有 em dash 字形，会画成乱码，必须换 ASCII


def _placeholder(zh):
    """占位符随绘制后端走：中文后端用 —，英文 Hershey 后端用 -（否则出乱码）。"""
    return ZERO if zh else ZERO_ASCII


def _font_candidates():
    env = os.environ.get("STATUS_DISPLAY_FONT", "")
    here = Path(__file__).resolve()
    bundled = str(here.parent.parent / "data" / "fonts" / "simhei_subset.ttf")
    out = [FONT_OVERRIDE, env,
           # 仓库自带子集字体放**第一**：它是按本项目实际用到的字符裁出来的，
           # ASCII 与中文都完整；系统字体不可靠（板端 DroidSansFallbackFull
           # 实测没有拉丁字形，数字全变方框，而 PIL 的 notdef 检测又判不出来）。
           bundled]
    out += [
        # 板端（PYNQ/Debian）常见 CJK 字体
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
        # macOS
        "/System/Library/Fonts/PingFang.ttc",
        # Windows（PC 预览/出图）
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        # 最后才用仓库自带子集字体（100 KB）：板端没有系统 CJK 字体时靠它出中文，
        # 放在最后是为了不改变 PC 侧既有渲染（子集只含用到的字）。
        bundled,
    ]
    return [c for c in out if c]


def _font_covers(path):
    """字体是否同时能画出 ASCII 数字与中文。

    坑：PIL 对缺失字形会返回 **.notdef 方框**（宽度非 0），所以「getmask 非空」
    根本判不出来 —— 板端实测 DroidSansFallbackFull 就这么被选中，数字全变成方框。
    这里改成拿一个必定缺失的私用区字符当 notdef 样本，逐字比对掩码。
    """
    try:
        from PIL import ImageFont
        f = ImageFont.truetype(str(path), 24)
        nb = f.getbbox("\ue000")          # 必定缺失 → .notdef 方框的包围盒
        def ok(ch):
            try:
                b = f.getbbox(ch)
            except Exception:
                return False
            if not b or (b[2] - b[0]) <= 0:
                return False
            return b != nb
        return ok("0") and ok("A") and ok("帧")
    except Exception:
        return False


def _pick_font():
    """返回可用的 CJK 字体路径；没有则返回 ""（调用方退英文标签）。

    首选「ASCII 与中文都能画」的字体；都覆盖不了时退回第一个存在的，
    保证至少不比没有字体更糟。
    """
    if _FONT_CACHE:
        return _FONT_CACHE[0]
    try:
        import PIL  # noqa: F401
        existing = [c for c in _font_candidates() if Path(c).exists()]
        path = ""
        for cand in existing:
            if _font_covers(cand):
                path = str(Path(cand))
                break
        if not path and existing:
            path = str(Path(existing[0]))
    except Exception:
        path = ""
    _FONT_CACHE.append(path)
    return path


def _use_zh():
    """是否用中文标签：没有中文字体就一律英文（避免方块乱码）。"""
    if LANG == "en":
        return False
    return bool(_pick_font())


class _Painter:
    """文字绘制器：有 CJK 字体走 PIL（可画中文），否则走 cv2 Hershey（英文）。

    文字先入队，最后一次性 flush —— PIL 路径只做一次 RGB<->BGR 转换，
    避免 1280x720 上每行文字都整图转换一次。
    """

    def __init__(self, img):
        self.img = img
        self.ops = []
        self.font_path = _pick_font()
        self.backend = ("PIL:%s" % Path(self.font_path).name) if self.font_path else "cv2:hershey"
        self._fonts = {}

    def _font(self, px):
        from PIL import ImageFont
        f = self._fonts.get(px)
        if f is None:
            f = ImageFont.truetype(self.font_path, px)
            self._fonts[px] = f
        return f

    def measure(self, text, px):
        text = str(text)
        if self.font_path:
            try:
                b = self._font(px).getbbox(text or " ")
                return (b[2] - b[0], b[3] - b[1])
            except Exception:
                return (int(len(text) * px * 0.6), px)
        import cv2
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, px / 29.0, 1)
        return (tw, th)

    def text(self, x, y_top, s, px=18, color=WHITE, bold=False):
        self.ops.append((int(x), int(y_top), str(s), int(px),
                         tuple(int(c) for c in color), bool(bold)))

    def text_right(self, x_right, y_top, s, px=18, color=WHITE, bold=False):
        tw, _ = self.measure(s, px)
        self.text(x_right - tw, y_top, s, px, color, bold)

    def flush(self):
        if not self.ops:
            return self.img
        if self.font_path:
            try:
                import numpy as np
                from PIL import Image, ImageDraw
                pil = Image.fromarray(np.ascontiguousarray(self.img[:, :, ::-1]))
                d = ImageDraw.Draw(pil)
                for x, y, t, px, c, _b in self.ops:
                    d.text((x, y), t, font=self._font(px),
                           fill=(c[2], c[1], c[0]), anchor="lt")
                self.img = np.asarray(pil)[:, :, ::-1].copy()
                return self.img
            except Exception:
                self.font_path = ""
                self.backend = "cv2:hershey"
        import cv2
        for x, y, t, px, c, bold in self.ops:
            cv2.putText(self.img, t, (x, y + int(px * 0.76)),
                        cv2.FONT_HERSHEY_SIMPLEX, px / 29.0, c,
                        2 if bold else 1, cv2.LINE_AA)
        return self.img


def _fit(painter, text, max_w, px, min_px=11):
    """把文字压进 max_w：先缩字号，缩到底还超就截断加 `..`。"""
    text = str(text)
    while px > min_px and painter.measure(text, px)[0] > max_w:
        px -= 1
    while len(text) > 4 and painter.measure(text, px)[0] > max_w:
        text = text[:-1]
        if painter.measure(text + "..", px)[0] <= max_w:
            text += ".."
            break
    return text, px


@dataclass
class PanelCtx:
    """面板需要的「运行期上下文」——JSONL 里没有，只能由显示进程自己统计。"""
    src: str = ""             # 当前 JSONL 文件名
    lines: int = 0            # 累计读到的记录行数
    started: float = field(default_factory=time.time)
    frames: int = 0           # 已渲染帧数（用于算 fps）
    last_event: tuple = None  # (frame, timestamp, text)
    last_event_text: str = ""
    pl_track: object = None   # summary.json 的 pl_track；None = 未上报
    ts_first: float = None
    ts_last: float = None


def _hms(sec):
    sec = int(max(0.0, sec))
    return "%02d:%02d:%02d" % (sec // 3600, sec % 3600 // 60, sec % 60)


def _num_str(v):
    return ("%d" % v) if float(v).is_integer() else ("%.1f" % v)


def _event_text(ev):
    """按优先级把一帧的 events 字典压成一条短文本；无事件返回 ""。"""
    if not isinstance(ev, dict) or not ev:
        return ""
    if ev.get("type") or ev.get("event"):
        return str(ev.get("type") or ev.get("event"))
    if ev.get("goal"):
        return ("GOAL side=%s" % ev["goal_side"]) if ev.get("goal_side") else "GOAL"
    if ev.get("out_of_play"):
        return ("OUT_OF_PLAY side=%s" % ev["out_side"]) if ev.get("out_side") else "OUT_OF_PLAY"
    if ev.get("possession") is not None:
        d = ev.get("possession_dist")
        tail = (" dist=%.1fm" % d) if isinstance(d, (int, float)) else ""
        return "POSSESSION #%s%s" % (ev.get("possession"), tail)
    return ""


def update_ctx(ctx, st, src=""):
    """把一帧状态并进运行期上下文（行数/时长/最新事件/时间戳跨度/pl_track）。"""
    ctx.frames += 1
    ctx.lines += 1
    if src:
        ctx.src = Path(src).name
    raw = st.raw or {}
    ts = raw.get("timestamp")
    if isinstance(ts, (int, float)):
        if ctx.ts_first is None:
            ctx.ts_first = float(ts)
        ctx.ts_last = float(ts)
    txt = _event_text(st.events or {})
    # 只在事件**变化**时记时刻：持续同一事件保留它的起始时刻，不会每帧刷新
    if txt and txt != ctx.last_event_text:
        ctx.last_event = (st.frame, ts if isinstance(ts, (int, float)) else None, txt)
        ctx.last_event_text = txt
    if ctx.pl_track is None and isinstance(raw.get("pl_track"), bool):
        ctx.pl_track = raw["pl_track"]


def build_rows(st, ctx, zh=True):
    """把一帧 + 上下文拼成 8 行 (标签, 值, 颜色)。缺字段一律 ZERO/占位符。"""
    raw = st.raw or {}
    rows = []
    ph = _placeholder(zh)

    def t(zh_s, en_s):
        return zh_s if zh else en_s

    # ① 帧号 / 时间戳
    if st.frame is None or st.frame < 0:
        v1 = ph
    else:
        v1 = t("帧 %d" % st.frame, "frame %d" % st.frame)
        ts = raw.get("timestamp")
        if isinstance(ts, (int, float)):
            v1 += t("   时间戳 %s ms" % _num_str(ts), "   ts %s ms" % _num_str(ts))
    rows.append((t("帧号 / 时间戳", "FRAME / TS"), v1, C_FRAME))

    # ② 场上人数（raw 里没有 players/tracks 时，若 FrameState 自带球员仍按真实数据画：
    #    tools/check_display_board.py 就是直接构造 FrameState、不带 raw 的）
    has_players = (isinstance(raw.get("players"), list)
                   or isinstance(raw.get("tracks"), list)
                   or bool(st.players))
    if not has_players:
        v2 = ph
    elif not st.players:
        v2 = t("0（本帧无有效坐标）", "0 (no valid coords)")
    else:
        ids = " ".join("#%s" % p["id"] for p in st.players[:10])
        more = "" if len(st.players) <= 10 else " +%d" % (len(st.players) - 10)
        v2 = "%d   %s%s" % (len(st.players), ids, more)
    rows.append((t("场上人数", "PLAYERS"), v2, C_PLAYERS))

    # ③ 球状态（同上：raw 无 ball 但 FrameState 有球时照实画）
    if "ball" not in raw and not st.ball:
        v3 = ph
    else:
        b = st.ball or {}
        bx, by = b.get("x"), b.get("y")
        if b.get("valid") and bx is not None and by is not None:
            v3 = t("可见  x=%.1f  y=%.1f m" % (float(bx), float(by)),
                   "SEEN  x=%.1f  y=%.1f m" % (float(bx), float(by)))
            space = b.get("space")
            if space and space != "field":
                v3 += "  (space=%s)" % space
            if isinstance(b.get("confidence"), (int, float)):
                v3 += "  conf=%.2f" % b["confidence"]
        else:
            v3 = t("未见（valid=false）", "NOT SEEN (valid=false)")
    rows.append((t("球状态", "BALL"), v3, C_BALL))

    # ④ 最新事件（含时刻）
    if not ctx.last_event:
        v4 = ph
    else:
        ef, ets, etext = ctx.last_event
        v4 = etext + t("   @帧 %s" % ef, "   @frame %s" % ef)
        if isinstance(ets, (int, float)):
            v4 += t("   时间戳 %s ms" % _num_str(ets), "   ts %s ms" % _num_str(ets))
    rows.append((t("最新事件", "EVENT"), v4, C_EVENT))

    # ⑤ 机位状态（+ 原因）
    cam_raw = str(st.camera_state or "").strip()
    if ("camera_state" not in raw and "camera_valid" not in raw
            and cam_raw in ("", "-")):
        v5 = ph
    else:
        cam = str(st.camera_state or "")
        cam = {"True": "VALID", "False": "INVALID"}.get(cam, cam) or ph
        reason = raw.get("camera_reason")
        v5 = "%s   %s=%s" % (cam, t("原因", "reason"), reason if reason else ph)
    rows.append((t("机位状态", "CAMERA"), v5, C_CAMERA))

    # ⑥ 存储状态（当前文件 + 累计行数是显示进程自己看到的；错误字段只有 JSONL 有才显示）
    err_key = None
    err = None
    for k in ("fsync_error", "fsync_failed", "store_error"):
        if k in raw:
            err_key, err = k, raw[k]
            break
    v6 = t("%s   累计行数 %d" % (ctx.src or ph, ctx.lines),
           "%s   lines %d" % (ctx.src or ph, ctx.lines))
    if err_key is None:
        v6 += t("   fsync失败=%s（无该字段）" % ph, "   fsync_err=%s(no field)" % ph)
    else:
        v6 += t("   fsync失败=%s" % err, "   fsync_err=%s" % err)
    rows.append((t("存储状态", "STORAGE"), v6, C_STORE))

    # ⑦ PL 状态（preproc 看 pl_ms；track 只认 summary.json 的 pl_track）
    pl_ms = raw.get("pl_ms")
    if isinstance(pl_ms, (int, float)):
        pre = t("preproc 启用 %.1f ms" % pl_ms, "preproc ON %.1f ms" % pl_ms)
    else:
        pre = t("preproc %s" % ph, "preproc %s" % ph)
    tr = ctx.pl_track
    track = (t("track 启用", "track ON") if tr is True
             else (t("track 未启用", "track OFF") if tr is False
                   else t("track %s" % ph, "track %s" % ph)))
    rows.append((t("PL 加速", "PL ACCEL"), "%s   %s" % (pre, track), C_PL))

    # ⑧ 运行时长与帧率（不足 1 s 不给帧率：分母太小会算出假的高帧率）
    el = max(0.0, time.time() - float(ctx.started))
    fps = (ctx.frames / el) if (el >= 1.0 and ctx.frames) else None
    fps_txt = ("%.1f" % fps) if fps is not None else ph
    v8 = t("运行 %s    %s 帧/秒" % (_hms(el), fps_txt),
           "up %s   %s fps" % (_hms(el), fps_txt))
    if ctx.ts_first is not None and ctx.ts_last is not None and ctx.ts_last > ctx.ts_first:
        span = (ctx.ts_last - ctx.ts_first) / 1000.0   # JSONL 时间戳按 ms
        v8 += t("   时间戳跨度 %.2f s" % span, "   ts-span %.2f s" % span)
    rows.append((t("运行时长 / 帧率", "UPTIME / FPS"), v8, C_RUNTIME))
    return rows


def _mode_label(w, h):
    if (w, h) == (800, 480):
        return "LCD 800x480"
    if (w, h) == (1280, 720):
        return "HDMI 1280x720"
    return "PANEL %dx%d" % (w, h)


def _src_hint(raw):
    """帧记录里若自带源文件/视频路径就用它（没有则空，不算编造）。"""
    for k in ("src", "file", "video"):
        v = raw.get(k)
        if isinstance(v, str) and v:
            return Path(v).name
    return ""


def render(st, w=800, h=480, ctx=None):
    """渲染一帧完整状态面板；返回 BGR ndarray (h, w, 3)。"""
    import cv2
    import numpy as np

    ctx = ctx or PanelCtx()
    s = w / float(DESIGN_W)
    zh = _use_zh()

    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = BG
    p = _Painter(img)

    # ---- 顶栏 ----
    head_h = int(round(46 * s))
    cv2.rectangle(img, (0, 0), (w, head_h), HEADER_BG, -1)
    cv2.rectangle(img, (0, head_h - max(2, int(2 * s))), (w, head_h), (90, 150, 210), -1)
    title = "ZYNQ-7020 足球视频分析 · 状态面板" if zh else "ZYNQ-7020  MATCH STATUS PANEL"
    tpx = int(23 * s)
    title, tpx = _fit(p, title, int(w * 0.56), tpx, int(12 * s))
    p.text(int(18 * s), int((head_h - tpx * 0.75) / 2), title, tpx, WHITE, bold=True)
    rtxt = "%s   %s" % (_mode_label(w, h), time.strftime("%H:%M:%S"))
    rpx = int(16 * s)
    rtxt, rpx = _fit(p, rtxt, int(w * 0.36), rpx, int(11 * s))
    p.text_right(w - int(18 * s), int((head_h - rpx * 0.75) / 2), rtxt, rpx,
                 (205, 215, 230))

    # ---- 8 行状态表 ----
    rows = build_rows(st, ctx, zh)
    top = head_h + int(8 * s)
    bottom = h - int(26 * s)
    gap = max(2, int(4 * s))
    rh = (bottom - top - gap * (len(rows) - 1)) // len(rows)
    pad_x = int(14 * s)
    label_w = int(150 * s)
    val_x = pad_x + label_w
    val_max = w - pad_x - val_x - int(12 * s)
    for i, (label, value, color) in enumerate(rows):
        y0 = top + i * (rh + gap)
        cv2.rectangle(img, (pad_x, y0), (w - pad_x, y0 + rh),
                      ROW_A if i % 2 == 0 else ROW_B, -1)
        cv2.rectangle(img, (pad_x, y0), (pad_x + max(3, int(5 * s)), y0 + rh), color, -1)
        lpx = int(16 * s)
        ltxt, lpx = _fit(p, label, label_w - int(14 * s), lpx, int(10 * s))
        p.text(pad_x + int(12 * s), y0 + rh // 2 - int(lpx * 0.62), ltxt, lpx,
               LABEL_FG, bold=True)
        vpx = int(19 * s)
        vtxt, vpx = _fit(p, value, val_max, vpx, int(10 * s))
        p.text(val_x, y0 + rh // 2 - int(vpx * 0.62), vtxt, vpx, color)

    # ---- 底栏 ----
    src = ctx.src or _src_hint(st.raw or {})
    lpx = int(13 * s)
    ltxt = "%s  %s" % ("源文件" if zh else "src", src or _placeholder(zh))
    ltxt, lpx = _fit(p, ltxt, int(w * 0.56), lpx, int(10 * s))
    p.text(int(16 * s), h - int(21 * s), ltxt, lpx, (150, 158, 172))
    rpx = int(13 * s)
    rtxt = "rows 8   backend=%s" % p.backend
    rtxt, rpx = _fit(p, rtxt, int(w * 0.38), rpx, int(10 * s))
    p.text_right(w - int(16 * s), h - int(21 * s), rtxt, rpx, (150, 158, 172))
    return p.flush()


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
                pt = Path(pattern)
                if f is None:
                    if not pt.exists():
                        print("STATUS_DISPLAY_WAIT_FILE %s" % pt)
                        time.sleep(0.5)
                        continue
                    f = open(pt, "r", encoding="utf-8", errors="replace")
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
    w, h = str(size).lower().split("x", 1)
    return int(w), int(h)


def _parse_sizes(text):
    out = []
    for chunk in str(text).replace(" ", ",").split(","):
        chunk = chunk.strip()
        if chunk:
            out.append(_nice_size(chunk))
    return out or [(DESIGN_W, DESIGN_H)]


def _expand(path):
    """--replay 支持单文件或通配符；通配符按名字排序（轮转文件按序回放）。"""
    pattern = str(path)
    if any(ch in pattern for ch in "*?["):
        return sorted(glob.glob(pattern))
    return [pattern] if Path(pattern).exists() else []


def _png_path(pattern, w, h, n_sizes):
    if "{size}" in pattern:
        return pattern.replace("{size}", "%dx%d" % (w, h))
    if n_sizes == 1:
        return pattern
    base = Path(pattern)
    return str(base.with_name("%s_%dx%d%s" % (base.stem, w, h, base.suffix)))


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


# 自检示例状态：**自造数据**，只为把 8 行填满，不代表任何真实板端结果。
SELF_SAMPLE = {
    "type": "frame",
    "frame": 137,
    "timestamp": 5480,
    "camera_state": "VALID",
    "camera_reason": "homography_ok",
    "ball": {"x": 48.6, "y": 30.2, "valid": True, "space": "field", "confidence": 0.72},
    "players": [
        {"track_id": 3, "x": 46.0, "y": 31.0, "confidence": 0.88},
        {"track_id": 7, "x": 52.4, "y": 28.5, "confidence": 0.81},
        {"track_id": 11, "x": 61.2, "y": 40.1, "confidence": 0.77},
    ],
    "events": {"out_of_play": False, "goal": False,
               "possession": 3, "possession_dist": 2.1},
    "pl_ms": 7.4,
}


def _selftest():
    """自造一条状态 → 两种尺寸各渲染一张 PNG → 校验形状/行数/占位符规则。"""
    import cv2

    st = normalize(SELF_SAMPLE)
    # started/frames 都是自造示例值：模拟「2 帧/秒刷新了 12 s」，只为让第 8 行有数
    ctx = PanelCtx(src="selftest_sample.jsonl", started=time.time() - 12.3,
                   frames=23, pl_track=True)
    update_ctx(ctx, st, "selftest_sample.jsonl")

    zeros = normalize({"frame": 1})   # 只剩帧号：其余行必须显示 —
    ctx0 = PanelCtx()
    update_ctx(ctx0, zeros, "")

    outdir = Path(os.environ.get("STATUS_DISPLAY_SELFTEST_DIR", SCRATCH_DIR))
    try:
        outdir.mkdir(parents=True, exist_ok=True)
    except Exception:
        outdir = Path(tempfile.mkdtemp(prefix="status_display_"))
    if not os.access(str(outdir), os.W_OK):
        outdir = Path(tempfile.mkdtemp(prefix="status_display_"))

    paths, problems = [], []
    for (w, h) in ((800, 480), (1280, 720)):
        img = render(st, w, h, ctx)
        if img.shape != (h, w, 3):
            problems.append("shape %s for %dx%d" % (img.shape, w, h))
        path = outdir / ("status_display_selftest_%dx%d.png" % (w, h))
        cv2.imwrite(str(path), img)
        if not path.exists() or path.stat().st_size == 0:
            problems.append("png not written: %s" % path)
        paths.append(str(path.resolve()))

    rows = build_rows(st, ctx, zh=_use_zh())
    if len(rows) != 8:
        problems.append("rows=%d (expect 8)" % len(rows))
    if any(not str(v).strip() for _l, v, _c in rows):
        problems.append("empty value in rows")

    rows0 = build_rows(zeros, ctx0, zh=_use_zh())
    ph = _placeholder(_use_zh())
    for i in (2, 3, 4, 5, 6):     # 球/事件/机位/存储错误/PL:样例无字段 → 必须含占位符
        if ph not in str(rows0[i][1]):
            problems.append("row %d not %s when field missing: %r" % (i + 1, ph, rows0[i][1]))

    ok = not problems
    print("STATUS_DISPLAY_SELFTEST", "OK" if ok else "FAIL",
          "backend=%s" % ("PIL:%s" % Path(_pick_font()).name if _pick_font() else "cv2:hershey"))
    for path in paths:
        print(path)
    for msg in problems:
        print("STATUS_DISPLAY_SELFTEST_PROBLEM %s" % msg)
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="Zynq-7020 状态面板（8 项完整版）")
    ap.add_argument("--replay", default="", help="JSONL 文件或通配符 run_*.jsonl")
    ap.add_argument("--size", default="800x480",
                    help="800x480 (LCD) / 1280x720 (HDMI)，可逗号相连一次出两种")
    ap.add_argument("--png-out", default="", help="PNG 输出路径（多尺寸时支持 {size} 占位）")
    ap.add_argument("--png", default="", help="同 --png-out（旧名，保留兼容）")
    ap.add_argument("--once", action="store_true", help="只读一遍 JSONL 就退出")
    ap.add_argument("--fb", default="", help="/dev/fb0 或 /dev/fb1")
    ap.add_argument("--fb-bram", default="", help="HDMI 1bpp BRAM 基址，如 0x40000000")
    ap.add_argument("--also-lcd-base", default="", help="同时写 LCD 1bpp BRAM 基址（组合 overlay），如 0x40020000")
    ap.add_argument("--hdmi-bit", default="", help="加载 hdmi_status.bit 并自动取 BRAM 基址（兼容旧名）")
    ap.add_argument("--display-bit", default="", help="加载 hdmi_status/lcd_status.bit 并自动取 BRAM 基址")
    ap.add_argument("--window", action="store_true", help="开窗口预览（PC）")
    ap.add_argument("--fps", type=float, default=2.0, help="回放节流帧率")
    ap.add_argument("--loop", action="store_true", help="回放循环")
    ap.add_argument("--follow", action="store_true", help="尾随正在写的 JSONL（实时刷新）")
    ap.add_argument("--from-start", action="store_true", help="follow 时从文件头读")
    ap.add_argument("--max-frames", type=int, default=0)
    ap.add_argument("--font", default="", help="CJK 字体 .ttf/.ttc；不给就自动找，找不到用英文标签")
    ap.add_argument("--lang", default="auto", choices=("auto", "zh", "en"),
                    help="标签语言：auto=有中文字体就用中文，否则英文")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    global FONT_OVERRIDE, LANG
    if args.font:
        FONT_OVERRIDE = args.font
        _FONT_CACHE.clear()
    LANG = args.lang

    if args.selftest:
        raise SystemExit(_selftest())
    if not args.replay:
        ap.error("需要 --replay（或 --selftest）")

    sizes = _parse_sizes(args.size)
    for (w, h) in sizes:
        if (w, h) not in ((800, 480), (1280, 720)):
            print("STATUS_DISPLAY_NOTE 非常规尺寸 %dx%d（版面按 800x480 等比缩放）" % (w, h))
    png_out = args.png_out or args.png

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

    if args.fb and not Path(args.fb).exists():
        print("STATUS_DISPLAY_NO_FB %s" % args.fb)
        args.fb = ""
    if not (args.fb or args.fb_bram or args.also_lcd_base or png_out or args.window):
        print("STATUS_DISPLAY_NO_SINK 面板无处可落：加 --fb / --fb-bram / --png-out / --window")

    sources = _expand(args.replay)
    if not sources:
        print("STATUS_DISPLAY_NO_FILE %s" % args.replay)
        return 1

    # 板端自报的 pl_track 只有 summary.json 里有；没有就保持 None（面板显示 —）
    ctx = PanelCtx(started=time.time())
    sib = Path(sources[-1]).parent / "summary.json"
    if sib.exists():
        try:
            summary = json.loads(sib.read_text(encoding="utf-8"))
        except Exception:
            summary = {}
        if isinstance(summary.get("pl_track"), bool):
            ctx.pl_track = summary["pl_track"]

    dt = 1.0 / max(0.1, args.fps)
    n = 0
    printed = set()
    while True:
        if args.follow:
            iterable = ((st, sources[-1])
                        for st in follow_jsonl(args.replay, from_start=args.from_start))
        else:
            def _gen():
                for src_path in sources:
                    for st in read_jsonl(Path(src_path)):
                        yield st, src_path
            iterable = _gen()

        for st, src in iterable:
            update_ctx(ctx, st, src)
            # type=event 是同一帧的「事件补充行」（board_pipeline 单独 append），
            # 只并进事件栏，不当作新的一帧重画，否则会多出一张几乎全是 — 的面板
            if str((st.raw or {}).get("type", "")) == "event":
                continue
            imgs = {sz: render(st, sz[0], sz[1], ctx) for sz in sizes}

            if args.fb:
                bpp = write_fb(imgs[sizes[0]], args.fb)
                if "fb" not in printed:
                    printed.add("fb")
                    print("STATUS_DISPLAY_FB %s bpp=%d" % (args.fb, bpp))
            if args.fb_bram:
                from bram_fb import write_image
                img_hdmi = imgs.get((1280, 720))
                if img_hdmi is None:
                    img_hdmi = render(st, 1280, 720, ctx)
                mode = write_image(img_hdmi, int(args.fb_bram, 0), 1280, 720)
                if "bram" not in printed:
                    printed.add("bram")
                    print("STATUS_DISPLAY_BRAM %s via %s" % (args.fb_bram, mode))
            if args.also_lcd_base:
                from bram_fb import write_image
                img_lcd = imgs.get((800, 480))
                if img_lcd is None:
                    img_lcd = render(st, 800, 480, ctx)
                mode2 = write_image(img_lcd, int(args.also_lcd_base, 0), 800, 480)
                if "lcd" not in printed:
                    printed.add("lcd")
                    print("STATUS_DISPLAY_LCD_BRAM %s via %s" % (args.also_lcd_base, mode2))
            if png_out:
                import cv2
                for (w, h) in sizes:
                    path = _png_path(png_out, w, h, len(sizes))
                    cv2.imwrite(path, imgs[(w, h)])
                    if path not in printed:
                        printed.add(path)
                        print("STATUS_DISPLAY_PNG %s %dx%d"
                              % (str(Path(path).resolve()), w, h))
            if args.window:
                import cv2
                cv2.imshow("status", imgs[sizes[0]])
                if cv2.waitKey(1) & 0xFF == 27:
                    print("STATUS_DISPLAY_OK frames=%d lines=%d" % (n, ctx.lines))
                    return 0

            n += 1
            if args.max_frames and n >= args.max_frames:
                break
            if not args.follow:
                time.sleep(dt)

        if args.once or args.follow or not args.loop:
            break

    print("STATUS_DISPLAY_OK frames=%d lines=%d sizes=%s"
          % (n, ctx.lines, ",".join("%dx%d" % sz for sz in sizes)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
