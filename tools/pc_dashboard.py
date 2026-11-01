#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pc_dashboard.py —— Zynq-7020 足球视频分析的上位机（PC）演示软件。

为什么需要它
------------
板端只负责“检测 → 事件 → JSONL”，评委/教练要看的是**一场球的可读态势**：
谁在场上、球在哪、刚刚发生了什么、判罚有没有证据帧。本文件把板端结果
（TCP 实时流或 SD 卡 JSONL 回放）渲染成一块固定布局的面板，并能导出 CSV
与 PNG 截图，是演示与存档的统一入口。

面板布局（1240x780 单幅位图；GUI 与 PNG 共用同一个渲染函数，所见即所得）
    ┌ 顶部状态条：连接状态 | 帧号 | 人数 | 球可见 | 机位状态 ─────────────┐
    │ 105x68 球场（球员/球/球权高亮）  │ 事件流：最近 10 条，新在上       │
    │ 底部：球权 · 事件计数 · CSV 路径 │ 证据帧缩略图（无则“无证据帧”）   │
    └────────────────────────────────────────────────────────────────────┘

用法
----
    # 1) 接板端实时流（tools/result_stream.py 每行一个 JSON）
    py tools/pc_dashboard.py --listen 0.0.0.0:5005 --csv session.csv

    # 2) 回放 SD 卡/提交包里的结果文件
    py tools/pc_dashboard.py --replay data/results/run_20261025.jsonl --speed 1

    # 3) 不开窗口，把回放最后一帧存成 PNG（出图/截图/无显示器机器）
    py tools/pc_dashboard.py --replay run.jsonl --dump-png out/dashboard.png

    # 4) 无显示环境只打摘要（管道/CI）
    py tools/pc_dashboard.py --listen 0.0.0.0:5005 --headless --csv s.csv

    # 5) 自检：自造 5 帧（含一条出界）走完整渲染链路，不需要板子和显示器
    py tools/pc_dashboard.py --selftest

    # 6) 一条命令走完「推流 → 板上处理 → 拉回结果 → 看带框画面」（需板端 zynq-stream 在听）
    py tools/pc_dashboard.py --push clips_5min/clip_04_015min.mp4 --push-start 60 --push-max-frames 45

判据令牌（脚本/文档只认这几个串，改动时不要换词）
--------------------------------------------------
    PC_DASHBOARD_SELFTEST OK   自检通过（解析→事件→渲染→CSV→证据帧→PNG 全链路）
    PC_DASHBOARD_OK frames=N   数据源正常结束，共处理 N 帧
    PC_DASHBOARD_PNG <abs>     已写出 PNG，附绝对路径
    PC_DASHBOARD_NO_DISPLAY    需要 GUI 但没有可用显示（此时请用 --headless/--dump-png）

数据约定
--------
每帧 JSON 的 ``events`` 字段与 ``tools/events.py`` 的 ``step()`` 对齐：
``out_of_play / out_side / goal / goal_side / possession / possession_dist``。
``evidence`` 为可选字段（证据帧 jpg 路径，字符串），有则显示缩略图。
不在 events 里的额外事件（如倒地候选 ``fall`` / ``fall_candidates``）只在板端
真的给出该字段时才显示，不凭空推断。

CSV 列固定为：帧号, 时间, 人数, 球x, 球y, 球有效, 出界, 出界侧, 进球,
球权ID, 球权距离, 机位状态；每帧一行，表头在文件首行。
"""
import argparse
import csv
import json
import os
import queue
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.dont_write_bytecode = True

PITCH_X, PITCH_Y = 105.0, 68.0

# ---------------------------------------------------------------- 画布常量
CANVAS_W, CANVAS_H = 1240, 780
TOP_H = 64                      # 顶部状态条高度
LEFT_X0, LEFT_X1 = 12, 896      # 左栏（球场）
RIGHT_X0, RIGHT_X1 = 908, 1228  # 右栏（事件流 + 证据帧）
PANEL_Y0, PANEL_Y1 = TOP_H + 8, CANVAS_H - 12
PITCH_W, PITCH_H = 856, 554
PITCH_OX, PITCH_OY = LEFT_X0 + 14, PANEL_Y0 + 40
MAX_EVENTS = 10                 # 事件流显示的条数

COL_BG = (28, 32, 40)           # 以下全部为 BGR
COL_PANEL = (40, 46, 56)
COL_CHIP = (52, 60, 72)
COL_LINE = (70, 78, 92)
COL_TEXT = (238, 241, 246)
COL_DIM = (158, 168, 182)
COL_OK = (120, 225, 150)
COL_WARN = (70, 180, 250)
COL_BAD = (90, 90, 235)
COL_ACCENT = (200, 170, 90)

# 连接状态文案（顶部状态条与事件区共用）
CONN_IDLE = "未连接"
CONN_WAIT = "等待板端连接"
CONN_ON = "TCP 已连接"
CONN_OFF = "TCP 已断开"
CONN_REPLAY = "回放中"
CONN_DONE = "回放结束"
CONN_PUSH = "推流中"

_SIDE_CN = {"left": "左端线", "right": "右端线", "top": "上边线", "bottom": "下边线"}
_GOAL_CN = {"left": "左球门", "right": "右球门"}


# ---------------------------------------------------------------- 数据模型
@dataclass
class FrameState:
    frame: int = -1
    timestamp: float = 0.0
    players: list = field(default_factory=list)
    ball: dict = field(default_factory=dict)
    camera_state: str = "-"
    events: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)
    evidence: str = ""          # 证据帧路径（字符串），无则空


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def normalize(obj):
    """把板端两种 JSON（proposal / frames）统一成 FrameState。"""
    if not isinstance(obj, dict):
        return None
    if obj.get("type") in ("summary", "meta"):
        return None
    st = FrameState(raw=obj)
    fv = obj.get("frame")
    if fv is None:
        fv = obj.get("timestamp", -1)
    try:
        st.frame = int(fv)
    except (TypeError, ValueError):
        st.frame = -1
    st.timestamp = _num(obj.get("timestamp", 0.0))
    st.camera_state = str(obj.get("camera_state", obj.get("camera_valid", "-")))

    players = []
    if isinstance(obj.get("players"), list):
        for p in obj["players"]:
            if not isinstance(p, dict):
                continue
            x, y = p.get("x"), p.get("y")
            if x is None or y is None:
                continue
            players.append({
                "id": p.get("track_id", p.get("id", -1)),
                "x": _num(x), "y": _num(y),
                "conf": _num(p.get("confidence", 0.0)),
                "space": p.get("space", "field"),
            })
    elif isinstance(obj.get("tracks"), list):
        # tracks = [id, x1, y1, x2, y2, score, (fx, fy)]。
        # ★ 没有标定时 board_pipeline 只写到第 6 项（没有球场坐标）。以前这里要求
        #   len >= 8，于是转播机位下的每一行都被丢掉：视频里明明画着 20 个框，
        #   顶栏却写「人数 0」—— 这就是用户说的「面板和显示的不一致」。
        #   有 (fx,fy) 才算球场坐标；没有就用框中心当**画面坐标**（space=px）。
        for t in obj["tracks"]:
            if not isinstance(t, (list, tuple)) or len(t) < 5:
                continue
            if len(t) >= 8 and t[6] is not None and t[7] is not None:
                x, y, space = _num(t[6]), _num(t[7]), "field"
            else:
                x, y, space = (_num(t[1]) + _num(t[3])) * 0.5, _num(t[4]), "px"
            players.append({"id": t[0], "x": x, "y": y, "conf": _num(t[5]),
                            "space": space,
                            "box": (_num(t[1]), _num(t[2]), _num(t[3]), _num(t[4]))})
    st.players = players
    st.ball = obj.get("ball") or {}
    ev = obj.get("events")
    if isinstance(ev, dict):
        st.events = ev
    st.evidence = _evidence_ref(obj)
    return st


def _evidence_ref(obj):
    """从一帧 JSON 里取证据帧路径（几个可能的字段名，取第一个非空字符串）。"""
    for key in ("evidence", "evidence_path", "evidence_frame", "evidence_img"):
        v = obj.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    ev = obj.get("events")
    if isinstance(ev, dict):
        for key in ("evidence", "evidence_path"):
            v = ev.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


def read_jsonl(path, limit=0):
    n = 0
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
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
                n += 1
                if limit and n >= limit:
                    return


def recv_lines(host, port, out_q, stop):
    """TCP 服务端：等一块板子连上来，逐行解析 JSONL 推入队列。"""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, int(port)))
    srv.listen(1)
    srv.settimeout(1.0)
    out_q.put(("status", "LISTEN %s:%s" % (host, port)))
    while not stop.is_set():
        try:
            conn, addr = srv.accept()
        except socket.timeout:
            continue
        except OSError:
            break
        out_q.put(("status", "CONNECTED %s" % (addr,)))
        conn.settimeout(1.0)
        buf = b""
        while not stop.is_set():
            try:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        obj = json.loads(line.decode("utf-8", "replace"))
                    except json.JSONDecodeError:
                        continue
                    st = normalize(obj)
                    if st is not None:
                        out_q.put(("frame", st))
            except socket.timeout:
                continue
            except OSError:
                break
        conn.close()
        out_q.put(("status", "DISCONNECTED"))
    srv.close()


# ---------------------------------------------------------------- 事件流
@dataclass
class EventItem:
    frame: int
    timestamp: float
    kind: str       # out / goal / poss / fall
    text: str


KIND_COLOR = {
    "out": COL_WARN,
    "goal": COL_BAD,
    "poss": COL_OK,
    "fall": COL_ACCENT,
}


def fmt_ts(ts):
    """时间戳格式化。**板端一律给毫秒**（`result_publisher.build_frame(timestamp_ms, ...)`），
    所以这里不再按数值大小猜单位——早期版本把 <100 的值当秒，于是 40 ms 会显示成
    `00:40.00`（CSV 第 2 行就能看到，已改）。"""
    v = _num(ts, -1.0)
    if v < 0:
        return "--:--.--"
    m, s = divmod(v / 1000.0, 60)
    return "%02d:%05.2f" % (int(m), s)


def _fall_ref(ev):
    """取“倒地候选”信息：板端给了才返回，否则 None（不推断）。"""
    for key in ("fall_candidates", "fallen", "fall", "down"):
        v = ev.get(key)
        if isinstance(v, (list, tuple)) and v:
            return list(v)
        if v is True:
            return True
    return None


class EventLog:
    """从每帧 events 推导人类可读事件，只保留最近 N 条（新在上）。

    只在**状态变化**时记一条（出界持续 30 帧不应该刷 30 条），这样 10 行
    就足够讲清半分钟的比赛。
    """

    def __init__(self, max_items=MAX_EVENTS):
        self.max_items = max(1, int(max_items))
        self.items = []              # 旧 → 新
        self.counts = {"out": 0, "goal": 0, "poss": 0, "fall": 0}
        self._prev_out = False
        self._prev_goal = False
        self._prev_poss = None
        self._prev_fall = None

    def recent(self):
        """新 → 旧（面板从上往下就是这个顺序）。"""
        return list(reversed(self.items))[: self.max_items]

    def _push(self, st, kind, text):
        self.items.append(EventItem(st.frame, st.timestamp, kind, text))
        self.counts[kind] = self.counts.get(kind, 0) + 1
        if len(self.items) > self.max_items * 4:   # 只留少量历史，长回放不吃内存
            self.items = self.items[-self.max_items * 2:]

    def update(self, st: FrameState):
        ev = st.events or {}
        out = bool(ev.get("out_of_play"))
        goal = bool(ev.get("goal"))
        if goal and not self._prev_goal:
            side = _GOAL_CN.get(ev.get("goal_side"), "球门")
            self._push(st, "goal", "进球！球越过%s线（第 %d 帧）" % (side, st.frame))
        if out and not self._prev_out and not goal:
            side = _SIDE_CN.get(ev.get("out_side"), "边线")
            self._push(st, "out", "球出界（%s），比赛暂停" % side)
        pid = ev.get("possession")
        if pid is not None and pid != self._prev_poss:
            d = ev.get("possession_dist")
            dtxt = "--" if d is None else "%.2f m" % _num(d)
            self._push(st, "poss", "球权 → #%s，距球 %s" % (pid, dtxt))
        fall = _fall_ref(ev)
        if fall is not None:
            key = tuple(fall) if isinstance(fall, list) else True
            if key != self._prev_fall:
                if isinstance(fall, list):
                    txt = "倒地候选：%s" % "、".join("#%s" % f for f in fall)
                else:
                    txt = "倒地候选：有球员疑似倒地"
                self._push(st, "fall", txt)
        self._prev_out, self._prev_goal = out, goal
        if pid is not None:
            self._prev_poss = pid
        self._prev_fall = fall


# ---------------------------------------------------------------- CSV
class CsvWriter:
    """逐帧 CSV：表头固定，Excel 双击直接可读（utf-8-sig）。"""

    FIELDS = ["帧号", "时间", "人数", "球x", "球y", "球有效", "出界", "出界侧",
              "进球", "球权ID", "球权距离", "机位状态"]

    def __init__(self, path):
        p = Path(path)
        if p.parent and not p.parent.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
        self.path = str(p)
        self.f = open(p, "w", encoding="utf-8-sig", newline="")
        self.w = csv.DictWriter(self.f, fieldnames=self.FIELDS)
        self.w.writeheader()
        self.rows = 0

    def write(self, st: FrameState):
        b = st.ball or {}
        ev = st.events or {}
        bx, by = b.get("x"), b.get("y")
        self.w.writerow({
            "帧号": st.frame,
            "时间": fmt_ts(st.timestamp),
            "人数": len(st.players),
            "球x": "" if bx is None else round(_num(bx), 2),
            "球y": "" if by is None else round(_num(by), 2),
            "球有效": bool(b.get("valid")),
            "出界": bool(ev.get("out_of_play")),
            "出界侧": ev.get("out_side") or "",
            "进球": bool(ev.get("goal")),
            "球权ID": "" if ev.get("possession") is None else ev.get("possession"),
            "球权距离": "" if ev.get("possession_dist") is None else ev.get("possession_dist"),
            "机位状态": st.camera_state,
        })
        self.rows += 1

    def close(self):
        try:
            self.f.close()
        except Exception:
            pass


# ---------------------------------------------------------------- 文字层（PIL）
# cv2.putText 画不了中文，所有文字先攒起来，最后一次性过 PIL 落字。
_FONT_CACHE = {}

_FONT_NORMAL = ["C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
                "C:/Windows/Fonts/simsun.ttc",
                "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
                "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                "/System/Library/Fonts/PingFang.ttc"]
_FONT_BOLD = ["C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/simhei.ttf",
              "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
              "/System/Library/Fonts/PingFang.ttc"]


def _font(size, bold=False):
    key = (int(size), bool(bold))
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    from PIL import ImageFont
    f = None
    for cand in (_FONT_BOLD if bold else _FONT_NORMAL):
        try:
            if Path(cand).exists():
                f = ImageFont.truetype(cand, int(size))
                break
        except Exception:
            continue
    if f is None:
        try:
            f = ImageFont.load_default(size=int(size))
        except TypeError:
            f = ImageFont.load_default()
    _FONT_CACHE[key] = f
    return f


def text_width(s, size, bold=False):
    try:
        return int(_font(size, bold).getlength(str(s)))
    except Exception:
        return int(len(str(s)) * size * 0.6)


def ellipsize(s, size, max_w, bold=False):
    """超出列宽就截断加省略号，保证文字不糊到边线外。"""
    s = str(s)
    if max_w <= 0:
        return ""
    if text_width(s, size, bold) <= max_w:
        return s
    cut = s
    while cut and text_width(cut + "…", size, bold) > max_w:
        cut = cut[:-1]
    return (cut + "…") if cut else ""


class TextBatch:
    """把文字绘制延后到一次统一交给 PIL，避免每行文字都做一次整图转换。"""

    def __init__(self):
        self.items = []

    def add(self, xy, s, size=13, color=COL_TEXT, anchor="la", bold=False):
        if s is None:
            return
        self.items.append((int(xy[0]), int(xy[1]), str(s), int(size), color, anchor, bold))

    def flush(self, img):
        import numpy as np
        from PIL import Image, ImageDraw
        if not self.items:
            return img
        pil = Image.fromarray(np.ascontiguousarray(img[:, :, ::-1]))
        d = ImageDraw.Draw(pil)
        for x, y, s, size, color, anchor, bold in self.items:
            d.text((x, y), s, font=_font(size, bold),
                   fill=(color[2], color[1], color[0]), anchor=anchor)
        return np.ascontiguousarray(np.asarray(pil)[:, :, ::-1])


# ---------------------------------------------------------------- 证据帧
_THUMB_CACHE = {}


def resolve_path(path_str, base_dir=None):
    """把 JSON 里的证据帧路径解析成真实文件：先按原样，再按 JSONL 所在目录。"""
    if not path_str:
        return None
    p = Path(str(path_str))
    cands = [p]
    if base_dir:
        b = Path(base_dir)
        cands += [b / p, b / p.name]
    for c in cands:
        try:
            if c.is_file():
                return c
        except OSError:
            continue
    return None


def load_thumb(path, box_w, box_h, base_dir=None):
    """读证据帧并 letterbox 到 box 内；失败返回 None（面板显示“无证据帧”）。"""
    import cv2
    import numpy as np
    p = resolve_path(path, base_dir)
    if p is None:
        return None
    try:
        mtime = p.stat().st_mtime
    except OSError:
        mtime = 0.0
    key = (str(p), int(box_w), int(box_h), round(mtime, 3))
    if key in _THUMB_CACHE:
        return _THUMB_CACHE[key]
    img = cv2.imread(str(p))
    if img is None:
        return None
    h, w = img.shape[:2]
    if not h or not w:
        return None
    s = min(box_w / float(w), box_h / float(h))
    nw, nh = max(1, int(w * s)), max(1, int(h * s))
    small = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
    canvas = np.full((box_h, box_w, 3), COL_CHIP, dtype=np.uint8)
    ox, oy = (box_w - nw) // 2, (box_h - nh) // 2
    canvas[oy:oy + nh, ox:ox + nw] = small
    if len(_THUMB_CACHE) > 8:
        _THUMB_CACHE.pop(next(iter(_THUMB_CACHE)))
    _THUMB_CACHE[key] = canvas
    return canvas


# ---------------------------------------------------------------- 球场绘制
def draw_pitch(st: FrameState, w=PITCH_W, h=PITCH_H, margin=30):
    """用 OpenCV 画一张 105x68 球场态势图；返回 BGR ndarray。

    面板内嵌用它，`tools/make_report_figures.py` 也直接调它（保持旧签名可用）。
    """
    import cv2
    import numpy as np

    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = (52, 116, 52)
    m = margin
    # 草皮竖条，让场地有纵深
    stripes = 12
    for i in range(stripes):
        if i % 2:
            x0 = int(m + (float(i) / stripes) * (w - 2 * m))
            x1 = int(m + (float(i + 1) / stripes) * (w - 2 * m))
            cv2.rectangle(img, (x0, m), (x1, h - m), (46, 106, 46), -1)

    def X(x):
        return int(m + (x / PITCH_X) * (w - 2 * m))

    def Y(y):
        return int(m + (y / PITCH_Y) * (h - 2 * m))

    white = (240, 240, 240)
    cv2.rectangle(img, (X(0), Y(0)), (X(PITCH_X), Y(PITCH_Y)), white, 2)
    cv2.line(img, (X(PITCH_X / 2), Y(0)), (X(PITCH_X / 2), Y(PITCH_Y)), white, 2)
    cv2.circle(img, (X(PITCH_X / 2), Y(PITCH_Y / 2)),
               max(6, int((9.15 / PITCH_Y) * (h - 2 * m))), white, 2)
    cv2.circle(img, (X(PITCH_X / 2), Y(PITCH_Y / 2)), 3, white, -1)
    for box_x0, box_x1, sign in ((0.0, 16.5, 1), (PITCH_X - 16.5, PITCH_X, -1)):
        cv2.rectangle(img, (X(box_x0), Y(PITCH_Y / 2 - 20.16)),
                      (X(box_x1), Y(PITCH_Y / 2 + 20.16)), white, 2)
        small_x = box_x0 + 5.5 if sign > 0 else box_x1 - 5.5
        cv2.rectangle(img, (X(box_x1 if sign < 0 else box_x0), Y(PITCH_Y / 2 - 9.16)),
                      (X(box_x0 if sign < 0 else box_x1), Y(PITCH_Y / 2 + 9.16)), white, 2)
        cv2.circle(img, (X(small_x), Y(PITCH_Y / 2)), 3, white, -1)

    # 球门（画在端线外侧）
    for gx, sgn in ((0.0, -1), (PITCH_X, 1)):
        x_edge = X(gx)
        cv2.rectangle(img, (x_edge, Y(PITCH_Y / 2 - 3.66)),
                      (x_edge + sgn * 10, Y(PITCH_Y / 2 + 3.66)), (205, 205, 205), 2)

    ev = st.events or {}
    pid = ev.get("possession")
    for p in st.players[:25]:
        if p.get("space", "field") != "field":       # 画面坐标的球员画不到顶视图上
            continue
        if not (-5 <= p["x"] <= PITCH_X + 5 and -5 <= p["y"] <= PITCH_Y + 5):
            continue
        px, py = X(p["x"]), Y(p["y"])
        cv2.circle(img, (px, py), 6, (60, 220, 255), -1)
        if pid is not None and str(p["id"]) == str(pid):
            cv2.circle(img, (px, py), 11, COL_OK, 2)
        cv2.putText(img, str(p["id"]), (px + 7, py - 7),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1, cv2.LINE_AA)

    b = st.ball or {}
    if b.get("valid") and b.get("space", "field") == "field" and b.get("x") is not None:
        bx, by = X(_num(b["x"])), Y(_num(b["y"]))
        cv2.circle(img, (bx, by), 9, (0, 0, 255), 2)
        cv2.circle(img, (bx, by), 2, (0, 0, 255), -1)
        # 球在场内才标 BALL 文字；出线时只留红圈，避免压住端线附近的球员号
        if 0.0 <= _num(b["x"]) <= PITCH_X and 0.0 <= _num(b["y"]) <= PITCH_Y:
            cv2.putText(img, "BALL", (bx + 11, by - 9),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1, cv2.LINE_AA)

    cv2.putText(img, "frame %s  players %d  cam %s" % (st.frame, len(st.players), st.camera_state),
                (m, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
    return img


# ---------------------------------------------------------------- 面板渲染
def _chip(img, tb, x, y, w, h, label, value, color):
    import cv2
    cv2.rectangle(img, (x, y), (x + w, y + h), COL_CHIP, -1)
    cv2.rectangle(img, (x, y), (x + w, y + h), COL_LINE, 1)
    lw = text_width(label, 11)
    tb.add((x + 12, y + h // 2 + 1), label, 11, COL_DIM, "lm")
    tb.add((x + 12 + lw + 8, y + h // 2 + 1), ellipsize(value, 15, w - lw - 34, True),
           15, color, "lm", bold=True)


def _conn_color(conn):
    if conn == CONN_ON:
        return COL_OK
    if conn in (CONN_WAIT, CONN_REPLAY, CONN_PUSH):
        return COL_WARN
    if conn == CONN_OFF:
        return COL_BAD
    return COL_DIM


def _cam_color(cam):
    c = str(cam).upper()
    if c.startswith("VALID") and "IN" not in c:
        return COL_OK
    if c in ("-", ""):
        return COL_DIM
    return COL_WARN


def _draw_status_bar(img, tb, st, conn):
    import cv2
    cv2.rectangle(img, (0, 0), (CANVAS_W, TOP_H), COL_PANEL, -1)
    cv2.line(img, (0, TOP_H - 1), (CANVAS_W, TOP_H - 1), COL_LINE, 1)
    tb.add((16, TOP_H // 2 + 1), "ZYNQ-7020 足球视频分析 · PC Dashboard", 17, COL_TEXT,
           "lm", bold=True)

    st_frame = "-" if st is None else str(st.frame)
    st_players = "-" if st is None else str(len(st.players))
    b = (st.ball or {}) if st is not None else {}
    ball_txt = "可见" if b.get("valid") else ("不可见" if st is not None else "-")
    ball_col = COL_OK if b.get("valid") else COL_DIM
    cam = "-" if st is None else st.camera_state

    x, y, h = 372, 12, 40
    chips = [("连接", conn, _conn_color(conn), 206),
             ("帧号", st_frame, COL_TEXT, 100),
             ("人数", st_players, COL_TEXT, 100),
             ("球", ball_txt, ball_col, 126)]
    for label, value, color, w in chips:
        _chip(img, tb, x, y, w, h, label, value, color)
        x += w + 8
    _chip(img, tb, x, y, RIGHT_X1 - x, h, "机位", cam, _cam_color(cam))


class VideoSource:
    """按帧号取画面（顺读；跳帧才 seek）。转播素材没有球场标定时，用画面代替俯视图。"""

    def __init__(self, path, start=0):
        import cv2 as _cv
        self.cv = _cv
        self.path = str(path)
        self.cap = _cv.VideoCapture(self.path)
        self.ok = self.cap.isOpened()
        self.pos = -1
        # 帧号偏移：推流时若从第 N 秒开始送，板端的 frame=0 对应本视频的第 start 帧
        self.start = int(start)

    def get(self, idx):
        if not self.ok or idx is None or idx < 0:
            return None
        idx = int(idx) + self.start
        if idx != self.pos + 1:
            self.cap.set(self.cv.CAP_PROP_POS_FRAMES, int(idx))
        ok, f = self.cap.read()
        self.pos = int(idx) if ok else -1
        return f if ok else None


def _video_with_start(path, start_sec):
    """建画面源；给了 --video-start 就把秒换算成帧偏移（板端 frame=0 对应的那一帧）。"""
    vid = VideoSource(path)
    if vid.ok and start_sec and start_sec > 0:
        fps = vid.cap.get(vid.cv.CAP_PROP_FPS) or 25.0
        vid.start = int(round(float(start_sec) * float(fps)))
    return vid


_CALIB_HINV = None


def load_calib_hinv(path):
    """读标定文件里的 3×3 单应，存下它的逆矩阵（球场坐标 → 画面像素）。

    事件模式（`--proposal`）的 JSONL 里**只有球场坐标、没有像素框**，画面要画出
    球员位置与球，就得靠这张逆矩阵回投。失败不抛异常：返回 None 时画面只少几个圈。
    """
    global _CALIB_HINV
    _CALIB_HINV = None
    if not path:
        return None
    try:
        import numpy as np
        with open(path, "r", encoding="utf-8") as f:
            h = np.array(json.load(f)["H"], dtype=float)
        _CALIB_HINV = np.linalg.inv(h)
        print("CALIB_LOADED %s" % path)
    except Exception as e:                                     # noqa: BLE001
        print("CALIB_LOAD_FAIL %s %s" % (path, e))
    return _CALIB_HINV


def _field_to_px(x, y):
    """球场坐标 → 画面像素；没有标定或点在无穷远时返回 None。"""
    if _CALIB_HINV is None or x is None or y is None:
        return None
    try:
        import numpy as np
        v = _CALIB_HINV @ np.array([float(x), float(y), 1.0])
        if abs(v[2]) < 1e-9:
            return None
        return (v[0] / v[2], v[1] / v[2])
    except Exception:                                          # noqa: BLE001
        return None


def _draw_video_panel(img, tb, st, video):
    """比赛画面 + 检测框（像素坐标直接画在缩略图上）。

    两种 JSON 都要能画：
      - `tracks`（普通模式）：带 `[id,x1,y1,x2,y2,...]`，直接画矩形框；
      - `players`（**事件模式 `--proposal`**）：只有球场坐标、没有像素框，
        借 `--calib` 的单应逆矩阵回投到画面画圈——否则事件那一镜画面是**光板**的
        （2026-11-01 现场提问「为什么没有框」）。
    """
    import cv2
    cv2.rectangle(img, (LEFT_X0, PANEL_Y0), (LEFT_X1, PANEL_Y1), COL_PANEL, -1)
    _tracks = (st.raw or {}).get("tracks") if st is not None else None
    _title = "比赛画面 · 带框（转播机位，无需标定）"
    if st is not None and not _tracks and _CALIB_HINV is not None:
        _title = "比赛画面 · 事件模式（球场坐标回投，圈在脚下）"
    tb.add((LEFT_X0 + 14, PANEL_Y0 + 14), _title, 14, COL_TEXT, "la", bold=True)
    f = video.get(st.frame) if (st is not None and video is not None) else None
    if f is None:
        cv2.rectangle(img, (PITCH_OX, PITCH_OY), (PITCH_OX + PITCH_W, PITCH_OY + PITCH_H),
                      (46, 54, 64), -1)
        tb.add((PITCH_OX + PITCH_W // 2, PITCH_OY + PITCH_H // 2),
               "等待画面…" if video is not None else "未给 --video", 18, COL_DIM, "mm")
        return
    h, w = f.shape[:2]
    s = min(PITCH_W / float(w), PITCH_H / float(h))
    nw, nh = max(1, int(w * s)), max(1, int(h * s))
    small = cv2.resize(f, (nw, nh), interpolation=cv2.INTER_AREA)
    for t in ((st.raw or {}).get("tracks") or []):
        try:
            tid, x1, y1, x2, y2 = (int(float(t[0])), float(t[1]), float(t[2]),
                                   float(t[3]), float(t[4]))
        except (IndexError, TypeError, ValueError):
            continue
        p1 = (int(x1 * s), int(y1 * s))
        p2 = (int(x2 * s), int(y2 * s))
        cv2.rectangle(small, p1, p2, (80, 240, 80), 2)
        cv2.putText(small, "#%s" % tid, (p1[0], max(10, p1[1] - 3)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (80, 240, 80), 1)
    if not ((st.raw or {}).get("tracks") or []):
        for p in (st.players or []):
            if p.get("space") != "field":
                continue
            q = _field_to_px(p.get("x"), p.get("y"))
            if q is None:
                continue
            px, py = int(q[0] * s), int(q[1] * s)
            if not (0 <= px < nw and 0 <= py < nh):
                continue
            cv2.circle(small, (px, py), 14, (80, 240, 80), 2)
            cv2.putText(small, "#%s" % p.get("id"), (px + 15, max(12, py - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (80, 240, 80), 1)
    b = (st.ball or {}) if st is not None else {}
    bxy = None
    if b.get("valid") and b.get("x") is not None:
        if b.get("space", "field") in ("image", "px"):
            bxy = (_num(b["x"]), _num(b["y"]))
        elif b.get("space") == "field":
            bxy = _field_to_px(b.get("x"), b.get("y"))
    if bxy is not None:
        bx, by = int(bxy[0] * s), int(bxy[1] * s)
        if 0 <= bx < nw and 0 <= by < nh:
            cv2.circle(small, (bx, by), 12, (0, 0, 255), 2)
            cv2.circle(small, (bx, by), 2, (0, 0, 255), -1)
            cv2.putText(small, "BALL", (bx + 15, max(12, by - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
    ox = PITCH_OX + (PITCH_W - nw) // 2
    oy = PITCH_OY + (PITCH_H - nh) // 2
    img[oy:oy + nh, ox:ox + nw] = small


def _draw_pitch_panel(img, tb, st, video=None):
    import cv2
    if video is not None:
        _draw_video_panel(img, tb, st, video)
        _draw_pitch_footer(img, tb, st)
        return
    cv2.rectangle(img, (LEFT_X0, PANEL_Y0), (LEFT_X1, PANEL_Y1), COL_PANEL, -1)
    tb.add((LEFT_X0 + 14, PANEL_Y0 + 14), "球场态势 · 105 × 68 m（顶视图）", 14, COL_TEXT,
           "la", bold=True)
    if st is None:
        cv2.rectangle(img, (PITCH_OX, PITCH_OY), (PITCH_OX + PITCH_W, PITCH_OY + PITCH_H),
                      (46, 54, 64), -1)
        tb.add((PITCH_OX + PITCH_W // 2, PITCH_OY + PITCH_H // 2), "等待数据…",
               18, COL_DIM, "mm")
    else:
        img[PITCH_OY:PITCH_OY + PITCH_H, PITCH_OX:PITCH_OX + PITCH_W] = \
            draw_pitch(st, PITCH_W, PITCH_H)

    _draw_pitch_footer(img, tb, st)


def _draw_pitch_footer(img, tb, st):
    """球场/画面下方那一行：球权 · 球 · 出界/进球。两条视图共用。"""
    ev = (st.events or {}) if st is not None else {}
    b = (st.ball or {}) if st is not None else {}
    y = PITCH_OY + PITCH_H + 16
    pid = ev.get("possession")
    if pid is None:
        poss = "球权：--"
    else:
        d = ev.get("possession_dist")
        poss = "球权：#%s" % pid + ("" if d is None else " · 距球 %.2f m" % _num(d))
    tb.add((LEFT_X0 + 16, y), poss, 13, COL_TEXT, "la", bold=True)

    bx, by = b.get("x"), b.get("y")
    if b.get("valid") and bx is not None:
        if b.get("space", "field") in ("image", "px"):
            btxt = "球：画面 (%d, %d) px" % (int(_num(bx)), int(_num(by)))
        else:
            btxt = "球：%.1f, %.1f m" % (_num(bx), _num(by))
    elif b:
        btxt = "球：不可见"
    elif st is not None:
        btxt = "球：无数据（本帧未扫球）"
    else:
        btxt = "球：无数据"
    tb.add((LEFT_X0 + 16 + text_width(poss, 13, True) + 28, y), btxt, 13, COL_DIM, "la")

    if ev.get("goal"):
        out_txt, out_col = "进球", COL_BAD
    elif ev.get("out_of_play"):
        out_txt, out_col = "出界", COL_WARN
    else:
        out_txt, out_col = "场内", COL_DIM
    tb.add((LEFT_X1 - 16, y), out_txt, 13, out_col, "ra", bold=True)


def _draw_footer(img, tb, log, csv_path, source):
    y = PANEL_Y1 - 46
    tb.add((LEFT_X0 + 16, y),
           "事件统计：出界 %d · 进球 %d · 球权变化 %d · 倒地 %d" % (
               log.counts.get("out", 0), log.counts.get("goal", 0),
               log.counts.get("poss", 0), log.counts.get("fall", 0)),
           12, COL_DIM, "la")
    tb.add((LEFT_X0 + 16, y + 20), "数据源：%s" % (source or "-"), 11, COL_DIM, "la")
    tb.add((LEFT_X1 - 16, y + 20), "CSV：%s" % (csv_path if csv_path else "未开启"),
           11, COL_DIM, "ra")


def _draw_events(img, tb, log):
    import cv2
    x0, x1 = RIGHT_X0, RIGHT_X1
    cv2.rectangle(img, (x0, PANEL_Y0), (x1, PANEL_Y1), COL_PANEL, -1)
    tb.add((x0 + 16, PANEL_Y0 + 14), "事件流", 14, COL_TEXT, "la", bold=True)
    tb.add((x0 + 16 + text_width("事件流", 14, True) + 10, PANEL_Y0 + 16),
           "最近 %d 条 · 新在上" % MAX_EVENTS, 11, COL_DIM, "la")

    y0 = PANEL_Y0 + 52
    row_h = 42
    items = log.recent()
    for i in range(MAX_EVENTS):
        y = y0 + i * row_h
        if y + row_h > PANEL_Y0 + 470:
            break
        if i < len(items) and i % 2 == 0:
            cv2.rectangle(img, (x0 + 10, y), (x1 - 10, y + row_h - 4), (46, 52, 62), -1)
        if i >= len(items):
            continue
        it = items[i]
        col = KIND_COLOR.get(it.kind, COL_DIM)
        cv2.rectangle(img, (x0 + 10, y), (x0 + 14, y + row_h - 4), col, -1)
        cy = y + (row_h - 4) // 2 + 1
        tb.add((x0 + 24, cy), fmt_ts(it.timestamp), 11, COL_DIM, "lm")
        tb.add((x0 + 88, cy), ellipsize(it.text, 13, (x1 - 12) - (x0 + 88)), 13, COL_TEXT, "lm")
    if not items:
        tb.add(((x0 + x1) // 2, y0 + 60), "暂无事件", 14, COL_DIM, "ma")
        tb.add(((x0 + x1) // 2, y0 + 84), "（出界 / 进球 / 球权变化触发）", 11, COL_DIM, "ma")


def _draw_evidence(img, tb, st, base_dir):
    import cv2
    x0, x1 = RIGHT_X0 + 10, RIGHT_X1 - 10
    ty = PANEL_Y0 + 486
    tb.add((x0 + 6, ty), "证据帧", 14, COL_TEXT, "la", bold=True)
    box_w, box_h = x1 - x0 - 12, PANEL_Y1 - (ty + 30) - 8
    bx, by = x0 + 6, ty + 28
    cv2.rectangle(img, (bx, by), (bx + box_w, by + box_h), COL_CHIP, -1)
    cv2.rectangle(img, (bx, by), (bx + box_w, by + box_h), COL_LINE, 1)

    ref = st.evidence if st is not None else ""
    thumb = load_thumb(ref, box_w - 6, box_h - 6, base_dir) if ref else None
    if thumb is not None:
        img[by + 3:by + 3 + thumb.shape[0], bx + 3:bx + 3 + thumb.shape[1]] = thumb
        tb.add((bx + box_w - 8, by + box_h - 8), Path(str(ref)).name, 10, COL_TEXT, "rd")
    else:
        msg = "无证据帧" if not ref else "证据帧缺失"
        tb.add((bx + box_w // 2, by + box_h // 2 - 10), msg, 15, COL_DIM, "mm")
        sub = "" if not ref else ellipsize(Path(str(ref)).name, 11, box_w - 16)
        if sub:
            tb.add((bx + box_w // 2, by + box_h // 2 + 12), sub, 11, COL_DIM, "ma")


def render_dashboard(st=None, log=None, conn=CONN_IDLE, base_dir=None,
                     csv_path="", source="", video=None):
    """把状态 + 事件 + 证据帧渲染成一张 BGR 位图（GUI 与 PNG 共用同一份逻辑）。"""
    import numpy as np
    img = np.full((CANVAS_H, CANVAS_W, 3), COL_BG, dtype=np.uint8)
    log = log or EventLog()
    tb = TextBatch()
    _draw_status_bar(img, tb, st, conn)
    _draw_pitch_panel(img, tb, st, video)
    _draw_footer(img, tb, log, csv_path, source)
    _draw_events(img, tb, log)
    _draw_evidence(img, tb, st, base_dir)
    return tb.flush(img)


# ---------------------------------------------------------------- 面板控制器
class Dashboard:
    """把「数据源消息 → 状态 / 事件 / CSV → 一帧图像」串成一条链路。

    数据源线程只往队列塞 ("frame", FrameState) / ("status", str)，所有状态收在
    这里，GUI 与 --headless / --dump-png 复用同一套逻辑与同一个渲染函数。
    """

    def __init__(self, csv_path=None, base_dir=None, source="", video=None):
        self.state = None
        self.log = EventLog(MAX_EVENTS)
        self.conn = CONN_IDLE
        self.frames = 0
        self.base_dir = Path(base_dir) if base_dir else None
        self.csv_path = str(csv_path) if csv_path else ""
        self.source = source
        self.video = video
        self.writer = CsvWriter(csv_path) if csv_path else None

    def status(self, text):
        t = str(text)
        if t.startswith("LISTEN"):
            self.conn = CONN_WAIT
        elif t.startswith("CONNECTED"):
            self.conn = CONN_ON
        elif t == "DISCONNECTED":
            self.conn = CONN_OFF
        elif t.startswith("REPLAY_DONE"):
            self.conn = CONN_DONE
        elif t.startswith("REPLAY"):
            self.conn = CONN_REPLAY
        elif t.startswith("PUSH_FAIL"):
            self.conn = CONN_OFF
        elif t.startswith("PUSH"):
            # 推流阶段板端还没有结果，屏上只显示「推流中 12/300」这样的进度
            detail = t[5:].strip()
            self.conn = ("%s %s" % (CONN_PUSH, detail)) if detail else CONN_PUSH

    def frame(self, st: FrameState):
        self.state = st
        self.log.update(st)
        self.frames += 1
        if self.writer:
            self.writer.write(st)

    def render(self):
        return render_dashboard(self.state, self.log, self.conn, self.base_dir,
                                self.csv_path, self.source, self.video)

    def close(self):
        if self.writer:
            self.writer.close()
            self.writer = None


def _open_dump_video(path, fps):
    """开一个 MP4 写入器（画布同尺寸）。路径不存在就建目录；开不了直接抛。"""
    import cv2
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    if p.parent and not p.parent.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
    vw = cv2.VideoWriter(str(p), cv2.VideoWriter_fourcc(*"mp4v"), float(fps),
                         (CANVAS_W, CANVAS_H))
    if not vw.isOpened():
        raise RuntimeError("cv2.VideoWriter 打不开：%s（换个文件名/换 mp4v 编码器）" % p)
    return vw


def write_png(dash, path):
    """渲染当前帧并写成 PNG（走 imencode，中文路径也不会失败）。返回绝对路径。"""
    import cv2
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    if p.parent and not p.parent.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
    img = dash.render()
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise RuntimeError("cv2.imencode 失败：%s" % p)
    p.write_bytes(buf.tobytes())
    return str(p.resolve())


# ------------------------------------------------- 推流到板子 + 把结果拉回来
# 为什么把这一步搬进上位机：`video_send.py` 只负责「送」，送完板端 JSONL 还在板子上，
# 要 SSH/Jupyter 再捞一遍才能看。演示时不该让人切两个终端。这里把
# 「推 → 等落盘 → 拉回 → 回放」串成一条，屏上先显示推流进度，再显示带框画面。
#
# 板端的时间不可信（没对时，实测停在 10-30），所以**不比较时间戳**：推流前先记下
# 目录里每个文件的字节数，推完只认「比快照变长、且有内容」的那个文件。
_BOARD_LIST = r'''
import glob, os
d = %(dir)r
files = glob.glob(os.path.join(d, %(pat)r))
files.sort(key=os.path.getmtime)
print("N %%d" %% len(files))
for p in files:
    with open(p, "rb") as f:
        n = sum(1 for _ in f)
    print("F %%s %%d %%d" %% (p, n, os.path.getsize(p)))
'''

_BOARD_PULL = r'''
import base64, hashlib
p = %(remote)r
d = open(p, "rb").read()
print("INFO %%d %%s" %% (len(d), hashlib.sha256(d).hexdigest()))
b = base64.b64encode(d).decode("ascii")
for i in range(0, len(b), 65536):
    print("CHUNK " + b[i:i + 65536])
print("END")
'''

# 「事件模式」的板端管线：装机服务（zynq-stream）**不带** --proposal、也没有标定，
# 所以它的 JSONL 里永远没有 events。要现场看到出界/球权，得另起一条带 --proposal
# 的管线（另一个端口），推完就杀掉。命令行与装机服务分开，是为了不把演示素材
# 用的标定/参数拖慢主路径（--ball-every 2 比服务里的 5 慢一倍多）。
_BOARD_START = r'''
import os, signal, subprocess, time
def _state(p):
    # 僵尸（状态 Z）也算「已经死了」：Jupyter kernel 不 wait，管线跑完会以僵尸留在
    # /proc 里，光看 /proc/<pid> 存在会误判成还在跑（见 _BOARD_ALIVE 的注释）。
    try:
        st = open("/proc/%%d/stat" %% p).read()
    except OSError:
        return None
    tail = st.rsplit(")", 1)[-1].split()
    return tail[0] if tail else None
def _reap():
    me = os.getpid()
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            st = open("/proc/" + name + "/stat").read()
        except OSError:
            continue
        tail = st.rsplit(")", 1)[-1].split()
        if len(tail) > 1 and tail[0] == "Z" and int(tail[1]) == me:
            try:
                os.waitpid(int(name), os.WNOHANG)
            except OSError:
                pass
pidfile = %(pidfile)r
_reap()
old = None
try:
    old = int(open(pidfile).read().strip())
except Exception:
    pass
if old and _state(old) not in (None, "Z"):
    # 上一条没退干净（Ctrl-C、掉线、连着跑两遍）：它占着 5009 口和帧缓存，pidfile 还会
    # 被新进程覆盖，上位机的「等管线退出」就一直盯着别人的 pid 等到超时
    # （2026-11-01 实测：板端早已 PIPELINE_OK，上位机还在 WAIT pid=1720）。
    # 先把它收掉再起新的，别让两条管线同时写同一批结果文件。
    try:
        os.kill(old, signal.SIGTERM)
        print("BOARD_PIPELINE_BUSY pid=%%d stopped" %% old)
    except ProcessLookupError:
        pass
    for _ in range(60):
        if _state(old) in (None, "Z"):
            break
        time.sleep(0.5)
    _reap()
    try:
        os.remove(%(flag)r)
    except OSError:
        pass
argv = %(argv)r
log = open(%(log)r, "w")
p = subprocess.Popen(argv, cwd=%(cwd)r, stdout=log, stderr=subprocess.STDOUT)
open(pidfile, "w").write(str(p.pid))
time.sleep(2.0)
print("BOARD_PIPELINE_START pid=%%d alive=%%s" %% (p.pid, p.poll() is None))
txt = open(%(log)r, "r", errors="replace").read()[-600:]
if txt:
    print("BOARD_LOG " + txt.replace("\n", " | "))
'''

_BOARD_STOP = r'''
import os, signal, time, subprocess
pid = %(pid)r
if pid is None:
    try:
        pid = int(open(%(pidfile)r).read().strip())
    except Exception:
        pid = None
if pid is None:
    print("BOARD_PIPELINE_STOP no_pid")
else:
    try:
        os.kill(pid, signal.SIGTERM)
        print("BOARD_PIPELINE_STOP pid=%%d" %% pid)
    except ProcessLookupError:
        print("BOARD_PIPELINE_STOP pid=%%d already_gone" %% pid)
time.sleep(1.5)
# 被 SIGTERM 打断的管线走不到自己的收尾分支，/tmp/hdmi_preview.active 会留在盘上，
# 于是 zynq-idle 以为「有人占着屏」，白屏永远回不来。这里替它清掉。
try:
    os.remove(%(flag)r)
except OSError:
    pass
'''


def _board_mod(jupyter):
    """懒加载 board_jupyter（它依赖 websocket-client，纯本地回放时不该被拖进来）。"""
    import importlib.util
    here = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location("board_jupyter",
                                                  str(here / "board_jupyter.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if jupyter:
        mod.configure(jupyter, None)
    return mod


def board_snapshot(store_dir, pattern="net_*.jsonl", jupyter="192.168.2.99:9090"):
    """推流前拍一张「目录里每个文件多大」。返回 {远程路径: 字节数}。

    为什么不比较时间戳：板子不对时（2026-11-01 实测板端时钟停在 10-30），
    拿主机时间去和板端 mtime 比毫无意义；比「文件有没有变长」不需要同步时钟。
    为什么不只认「新建文件」：`zynq-stream` 是在**服务启动时**就把 `net_*.jsonl`
    建出来的，推流数据写进的是这个已有文件，所以新建判据会整段漏掉（同样实测踩到）。
    """
    bj = _board_mod(jupyter)
    rc, text = bj.run_capture(_BOARD_LIST % {"dir": store_dir, "pat": pattern},
                              timeout=120.0)
    if rc != 0:
        raise RuntimeError("板端列目录失败（rc=%s）" % rc)
    return {p: v[1] for p, v in _parse_list(text).items()}


def _parse_list(text):
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("F "):
            continue
        try:
            _tag, path, n, size = line.split(" ", 3)
            out[path] = (int(n), int(size))
        except ValueError:
            continue
    return out


def _pick_grown(items, before):
    """从一次目录快照里挑「比推流前变长、且有内容」的文件。

    items: {path: (lines, size)}；before: {path: size}（推流前的快照）。
    返回 (delta, path, lines, size) 或 None。抽成函数是为了能被 --selftest 直接覆盖：
    这套判据踩过两次坑（空文件、已有文件长数据），不该只靠上板才能验证。
    """
    grown = []
    for p, (n, size) in items.items():
        delta = size - before.get(p, 0)
        if delta > 0 and n > 0:
            grown.append((delta, p, n, size))
    if not grown:
        return None
    grown.sort()
    return grown[-1]


_BOARD_ALIVE = r'''
import os
pid = %(pid)r
def _state(p):
    # 只认 /proc/<pid> 存在是不够的：父进程（Jupyter kernel）从不 wait，管线跑完会变成
    # **僵尸**留在 /proc 里，判据就成了「永远活着」（2026-11-01 实测：板端 01:26:34 已
    # PIPELINE_OK，上位机又等了 10 分钟）。stat 第二列的状态位 Z 才是真死了。
    try:
        st = open("/proc/%%d/stat" %% p).read()
    except OSError:
        return None
    tail = st.rsplit(")", 1)[-1].split()
    return tail[0] if tail else None
state = _state(pid) if pid else None
alive = state not in (None, "Z")
try:
    cur = int(open(%(pidfile)r).read().strip())
except Exception:
    cur = None
print("BOARD_PIPELINE_ALIVE pid=%%s pidfile_pid=%%s state=%%s alive=%%s"
      %% (pid, cur, state, alive))
'''


def board_wait_pipeline_exit(pidfile, jupyter="192.168.2.99:9090",
                             timeout=1200.0, poll=3.0, log=None, pid=None):
    """等板上那条 Popen 起来的管线**自己跑完退出**。

    为什么要这一步：`ResultStore` 按 `--store-fsync 30` 攒着写盘，推流一结束就按
    「行数不再变」去拉，会把**最后不到 30 行**当成「写稳了」——2026-11-01 现场就是这样
    （推 80 帧只拉回 60 行，第 166 帧那条 1.09 m 的球权正好落在丢掉的那 20 帧里）。
    管线进程退出时会补一次 flush，所以判据改成「进程没了」再拉。

    超时从 300 s 放宽到 1200 s：**推完 ≠ 板端算完**。250 帧实测推流 6 min，
    收尾时内核还压着几十帧 JPEG 没解，板端按 ~1 fps 追，再要 3–5 min。
    2026-11-01 那次卡在 300 s 上，随后紧接着拉文件恰好撞上「还没写完」。

    `pid` 必须传自己起的那条：**不能只信 pidfile**。2026-11-01 实测连着跑两次时，
    第二次启动把 pidfile 覆盖成新 pid，第一次的等待就一直盯着别人的进程，
    于是「板端早就 PIPELINE_OK 了，上位机还在 WAIT」（日志里 pid=1720 alive=True
    而板上根本没有这个进程）。pidfile 只用于排查，判据用 pid。
    """
    bj = _board_mod(jupyter)
    if pid is None:
        rc, text = bj.run_capture(_BOARD_ALIVE % {"pidfile": pidfile, "pid": None},
                                  timeout=120.0)
        for line in text.splitlines():
            if line.startswith("BOARD_PIPELINE_ALIVE"):
                kv = dict(x.split("=", 1) for x in line.split()[1:] if "=" in x)
                pid = int(kv["pidfile_pid"]) if kv.get("pidfile_pid", "-") not in ("None", "-") else None
        if pid is None:
            if log:
                log("BOARD_PIPELINE_EXIT_NO_PID %s" % pidfile)
            return False
    t0 = time.time()
    deadline = t0 + timeout
    last_note = t0
    while time.time() < deadline:
        rc, text = bj.run_capture(_BOARD_ALIVE % {"pidfile": pidfile, "pid": pid},
                                  timeout=120.0)
        if rc == 0 and "alive=False" in text:
            if log:
                log("BOARD_PIPELINE_EXITED after=%ds" % int(time.time() - t0))
            return True
        if log and time.time() - last_note >= 60.0:
            last_note = time.time()
            log("BOARD_PIPELINE_WAIT %ds %s" % (int(time.time() - t0),
                                                text.strip().replace("\n", " ")[:120]))
        time.sleep(poll)
    if log:
        log("BOARD_PIPELINE_EXIT_TIMEOUT %ss" % int(timeout))
    return False


def board_wait_results(before, store_dir, pattern="net_*.jsonl",
                       jupyter="192.168.2.99:9090", timeout=180.0,
                       poll=2.0, log=None):
    """等板端把这次推流的结果写稳，返回 (remote_path, lines, size)。

    判据：选**相对快照增长最多**的那个文件，且它的「行数+字节数」在连续两次轮询里
    不变、且有内容。空文件不算数——板端先建文件后写内容，且 `ResultStore` 是攒着
    写的，开局那一刻文件确实就是 0 行（实测 pull 回来过空文件）。
    """
    bj = _board_mod(jupyter)
    deadline = time.time() + timeout
    last = None
    stable = 0
    while time.time() < deadline:
        rc, text = bj.run_capture(_BOARD_LIST % {"dir": store_dir, "pat": pattern},
                                  timeout=120.0)
        items = _parse_list(text) if rc == 0 else {}
        pick = _pick_grown(items, before)
        if pick is not None:
            _delta, path, n, size = pick
            cur = (path, n, size)
            if cur == last:
                stable += 1
                if stable >= 1:
                    if log:
                        log("BOARD_RESULT_READY %s lines=%d bytes=%d"
                            % (cur[0], cur[1], cur[2]))
                    return cur
            else:
                stable = 0
            last = cur
        time.sleep(poll)
    raise RuntimeError("板端结果没有落定（timeout=%ss, last=%s）" % (timeout, last))


def board_pull_jsonl(remote, local, jupyter="192.168.2.99:9090", timeout=300.0,
                     tries=3):
    """把板端一个 JSONL 拉回本地并校验 sha256。返回本地路径。

    为什么要重试：2026-11-01 一键演示那次，板端管线还在收尾（推完不等于算完），
    紧接着的这一次读回**返回了空输出**（`板端没有返回数据`）——同一文件手工重拉一次
    就 281 KB 全对。Jupyter 那侧偶发空回，重试一次比让人当场手忙脚乱强。
    """
    import base64
    import hashlib
    bj = _board_mod(jupyter)
    last = None
    for attempt in range(int(tries)):
        rc, text = bj.run_capture(_BOARD_PULL % {"remote": remote}, timeout=timeout)
        if rc != 0:
            last = RuntimeError("板端读文件失败（rc=%s）：%s" % (rc, remote))
        else:
            want_len = want_sha = None
            chunks = []
            for line in text.splitlines():
                if line.startswith("INFO "):
                    _tag, n, sha = line.split(" ", 2)
                    want_len, want_sha = int(n), sha.strip()
                elif line.startswith("CHUNK "):
                    chunks.append(line[6:].strip())
                elif line.strip() == "END":
                    break
            if want_len is None or not chunks:
                last = RuntimeError("板端没有返回内容：%s" % remote)
            else:
                raw = base64.b64decode("".join(chunks))
                got_sha = hashlib.sha256(raw).hexdigest()
                if len(raw) != want_len or got_sha != want_sha:
                    last = RuntimeError("回传校验失败 len=%d/%d sha=%s/%s"
                                        % (len(raw), want_len, got_sha[:12],
                                           (want_sha or "")[:12]))
                else:
                    p = Path(local)
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(raw)
                    return p, got_sha
        time.sleep(2.0)
    raise last


EVENT_CALIB_DEFAULT = "data/calib/field_calib_ball.json"
EVENT_PY = "/usr/local/share/pynq-venv/bin/python3"
EVENT_REPO = "/home/xilinx/zynq-yolo"
EVENT_PREVIEW_FLAG = "/tmp/hdmi_preview.active"

# ---- 一键演示（`--demo`）的固定脚本 -----------------------------------------
# 素材选 c05：`data/samples/scenes/MANIFEST.csv` 里 8 段争抢场景中得分最高的
# （score 0.9384，其余 0.85–0.89），也是**唯一**验出球权事件的一段
# （球 + 配对的中圈标定同时成立）。250 帧 = 10 s，事件在第 110/130/166 帧前后。
DEMO_VIDEO = "data/samples/scenes/c05_t01730.0.mp4"
DEMO_FRAMES = 250
DEMO_CSV = "build/demo_events.csv"

# 板子活着 = Jupyter 能连上 + 两个服务都在跑 + 没有人占着 HDMI
_BOARD_PREFLIGHT = r'''
import subprocess
r = subprocess.run("systemctl is-active zynq-idle zynq-stream", shell=True,
                   capture_output=True, text=True)
print("SERVICES", r.stdout.replace("\n", " ").strip())
import os
print("PREVIEW_FLAG", os.path.exists("/tmp/hdmi_preview.active"))
'''


def board_preflight(jupyter="192.168.2.99:9090", timeout=30.0):
    """演示前的三行体检。返回 (ok, 说明)。连不上就是板子没起 —— 先去上电。"""
    try:
        bj = _board_mod(jupyter)
        rc, text = bj.run_capture(_BOARD_PREFLIGHT, timeout=timeout)
    except Exception as e:                       # noqa: BLE001
        return False, "Jupyter 连不上（%s: %s）——板子没起或掉线，先上电重启" % (
            type(e).__name__, e)
    if rc != 0:
        return False, "板端体检执行失败：%s" % (text or ("rc=%s" % rc))[-200:]
    svc = " ".join(l.split(None, 1)[1] for l in text.splitlines()
                   if l.startswith("SERVICES"))
    ok = svc.count("active") >= 2 and "inactive" not in svc and "failed" not in svc
    return ok, text.strip().replace("\n", " | ")


def _event_argv(port=5009, calib=EVENT_CALIB_DEFAULT,
                store_dir="/home/xilinx/zynq-yolo/build/flow", prefix="evt",
                hdmi=True, py=EVENT_PY, every=25, ball_every=2, lcd=5):
    """事件模式板端管线（与装机服务分开起）：参数是 2026-10-31 板上验收那一组。

    `--every 25 --ball-every 2 --max-det 0` 三个都不能省，省了就采不到东西：
      · `--max-det 0`：默认 8 只做前 8 次检测，之后球员全靠跟踪器外推，越推越散；
      · `--ball-every 2`：不给就退回「只在检测帧扫球」，202 帧只剩十几次机会；
      · `--every 25`：与验收/报告里的数字同口径，别单独改。
    `--proposal` 是 events 的唯一开关；`--calib` 必须与素材配对（中圈片段配
    `field_calib_ball.json`，配错了球会退回画面坐标、events 恒空）。
    """
    argv = [py, "tools/board_pipeline.py",
            "--listen", str(int(port)),
            "--every", str(int(every)), "--ball-every", str(int(ball_every)),
            "--max-det", "0",
            "--proposal", "--calib", calib,
            "--no-pl", "--no-camera-gate",
            "--store-dir", store_dir, "--store-prefix", prefix]
    if hdmi:
        argv.append("--hdmi-preview")
    if lcd and int(lcd) > 0:
        argv += ["--lcd-preview", str(int(lcd))]
    return argv


def board_start_pipeline(argv, jupyter="192.168.2.99:9090",
                         log="/tmp/pc_event_run.log", pidfile="/tmp/pc_event.pid"):
    """在板上后台起一条管线，返回它的日志/pid 路径。失败抛 RuntimeError。

    若 pidfile 上那条还活着，板端模板会先把它 SIGTERM 掉（`BOARD_PIPELINE_BUSY`）——
    两条管线同时跑会把结果文件、pidfile、5009 口全搅在一起。
    """
    bj = _board_mod(jupyter)
    rc, text = bj.run_capture(_BOARD_START % {"argv": list(argv), "log": log,
                                             "pidfile": pidfile, "cwd": EVENT_REPO,
                                             "flag": EVENT_PREVIEW_FLAG},
                              timeout=120.0)
    if rc != 0 or "BOARD_PIPELINE_START" not in text:
        raise RuntimeError("板端起管线失败：%s" % (text or ("rc=%s" % rc))[-400:])
    pid = None
    for line in text.splitlines():
        if line.startswith("BOARD_PIPELINE_START pid="):
            try:
                pid = int(line.split("pid=", 1)[1].split()[0])
            except (IndexError, ValueError):
                pid = None
    return {"log": log, "pidfile": pidfile, "pid": pid, "text": text}


def board_stop_pipeline(jupyter="192.168.2.99:9090",
                        pidfile="/tmp/pc_event.pid", log=None, pid=None):
    """停掉板上那条管线，并把 HDMI 占屏标记清掉（否则白屏回不来）。

    `pid` 优先：只停自己起的那条，避免对方正在跑时被后一个会话的收尾误杀。
    """
    bj = _board_mod(jupyter)
    rc, text = bj.run_capture(_BOARD_STOP % {"pidfile": pidfile, "pid": pid,
                                            "flag": EVENT_PREVIEW_FLAG},
                              timeout=120.0)
    return rc, text


def push_worker(video, q, stop, host="192.168.2.99", port=5007,
                jupyter="192.168.2.99:9090",
                store_dir="/home/xilinx/zynq-yolo/build/flow",
                pattern="net_*.jsonl", out_dir="build/pull",
                fps=25.0, quality=95, max_frames=0, scale=1.0, speed=1.0,
                start_sec=0.0, video_src=None, log=print, event=None):
    """线程体：推流 → 等板端落盘 → 拉回 → 交给回放线程。

    一切进度都通过队列 (`("status", ...)` / `("frame", ...)`) 回报，
    与 `--replay` / `--listen` 走同一条渲染路径，GUI 与 headless 都不用改。
    video_src 传进来是为了把帧号偏移写回去：从第 N 秒开始推时，板端 frame=0
    对应原视频的某一帧，画面与检测框对不上就白看了。
    event 给定时（`--event`），推流前先在板上另起一条 `--proposal --calib` 管线，
    推完拉完再把它停掉；不给就走装机服务 `zynq-stream`（没有事件）。
    """
    try:
        _push_pipeline(video, q, stop, host, port, jupyter, store_dir, pattern,
                       out_dir, fps, quality, max_frames, scale, speed,
                       start_sec, video_src, log, event)
    except Exception as e:                                   # noqa: BLE001
        # 线程里抛出去的异常不会有人接：不在这里兜住，屏上就会永远停在「推流中」
        # （2026-11-01 实测：sendall 超时后界面静默十分钟）。宁可报错也要说出来。
        q.put(("status", "PUSH_FAIL 异常：%s: %s" % (type(e).__name__, e)))


def _push_pipeline(video, q, stop, host, port, jupyter, store_dir, pattern,
                   out_dir, fps, quality, max_frames, scale, speed,
                   start_sec, video_src, log, event=None):
    started = None
    if event:
        q.put(("status", "PUSH 起事件管线 :%d" % event["port"]))
        started = board_start_pipeline(event["argv"], jupyter,
                                       pidfile=event.get("pidfile", "/tmp/pc_event.pid"),
                                       log=event.get("log", "/tmp/pc_event_run.log"))
        if log:
            for line in str(started.get("text", "")).splitlines():
                if line.startswith("BOARD_"):
                    log(line)
    try:
        _push_body(video, q, stop, host, port, jupyter, store_dir, pattern,
                   out_dir, fps, quality, max_frames, scale, speed,
                   start_sec, video_src, log,
                   await_pidfile=(started or {}).get("pidfile"),
                   await_pid=(started or {}).get("pid"))
    finally:
        if started is not None:
            try:
                rc, text = board_stop_pipeline(jupyter,
                                               pidfile=started["pidfile"],
                                               pid=started.get("pid"))
                if log:
                    for line in text.splitlines():
                        if line.startswith("BOARD_"):
                            log(line)
            except Exception as e:                           # noqa: BLE001
                if log:
                    log("BOARD_PIPELINE_STOP_FAIL %s: %s" % (type(e).__name__, e))


def _push_body(video, q, stop, host, port, jupyter, store_dir, pattern,
               out_dir, fps, quality, max_frames, scale, speed,
               start_sec, video_src, log, await_pidfile=None, await_pid=None):
    q.put(("status", "PUSH 连接板子"))
    before = board_snapshot(store_dir, pattern, jupyter)

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from video_send import push_frames

    def _prog(d):
        q.put(("status", "PUSH %d 帧 %.1f MB %.1f fps"
               % (d["frames"], d["mb"], d["fps"])))

    q.put(("status", "PUSH 开始推送"))
    res = push_frames(video, host=host, port=port, fps=fps, quality=quality,
                      max_frames=max_frames, scale=scale, start_sec=start_sec,
                      on_progress=_prog, stop=stop)
    if not res["ok"]:
        q.put(("status", "PUSH_FAIL 推流失败：%s" % res["reason"]))
        return
    if video_src is not None:
        video_src.start = int(res.get("start_frame") or 0)
        if log:
            log("PUSH_FRAME_OFFSET start_frame=%d" % video_src.start)
    if log:
        log("PUSH_DONE frames=%d mb=%.1f fps=%.1f"
            % (res["frames"], res["bytes"] / 1e6, res["fps"]))

    if await_pidfile:
        # 事件管线是我们自己起的：等它跑完再拉，否则最后 <30 行（fsync 间隔）会丢
        q.put(("status", "PUSH 等板端算完"))
        board_wait_pipeline_exit(await_pidfile, jupyter, log=log, pid=await_pid)
    q.put(("status", "PUSH 等板端落盘"))
    remote, lines, _size = board_wait_results(before, store_dir, pattern,
                                              jupyter, log=log)

    q.put(("status", "PUSH 拉回结果 %d 行" % lines))
    out = Path(out_dir).expanduser()
    if not out.is_absolute():
        out = Path.cwd() / out
    local, sha = board_pull_jsonl(remote, out / Path(remote).name, jupyter)
    if log:
        log("PULL_OK %s lines=%d sha256=%s" % (local, lines, sha[:16]))

    # 到这里数据已经在本机：后面的回放与 `--replay` 完全一样
    replay_worker(local, q, stop, speed=speed, loop=False)


# ---------------------------------------------------------------- 回放线程
def replay_worker(path, q, stop, speed=1.0, loop=False):
    """按 10 fps × speed 的节奏把 JSONL 推进队列；speed=0 表示不限速。"""
    while not stop.is_set():
        q.put(("status", "REPLAY_START"))
        t0 = time.time()
        n = 0
        for st in read_jsonl(Path(path)):
            if stop.is_set():
                return
            q.put(("frame", st))
            n += 1
            if speed > 0:
                dt = 1.0 / (10.0 * speed)
                el = time.time() - t0
                if el < n * dt:
                    time.sleep(n * dt - el)
        q.put(("status", "REPLAY_DONE %d frames" % n))
        if not loop:
            return
        time.sleep(0.5)


# ---------------------------------------------------------------- GUI
def run_gui(dash, q, stop):
    """tkinter 窗口：整幅位图直接贴到 Label 上，与 PNG 输出完全一致。"""
    import tkinter as tk
    from PIL import Image, ImageTk

    root = tk.Tk()
    root.title("Zynq-7020 足球分析 · PC Dashboard")
    root.configure(bg="#1c2028")
    root.resizable(False, False)
    label = tk.Label(root, bd=0, highlightthickness=0)
    label.pack()
    state = {"dirty": True}

    def on_close():
        stop.set()
        try:
            root.destroy()
        except Exception:
            pass

    root.protocol("WM_DELETE_WINDOW", on_close)

    def poll():
        if stop.is_set():
            dash.close()
            try:
                root.destroy()
            except Exception:
                pass
            return
        try:
            while True:
                kind, payload = q.get_nowait()
                if kind == "frame":
                    dash.frame(payload)
                else:
                    dash.status(payload)
                state["dirty"] = True
        except queue.Empty:
            pass
        if state["dirty"]:
            img = dash.render()
            photo = ImageTk.PhotoImage(Image.fromarray(img[:, :, ::-1]))
            label.configure(image=photo)
            label.image = photo          # 防止被 GC 回收
            state["dirty"] = False
        root.after(40, poll)

    root.after(40, poll)
    root.mainloop()


def _has_display():
    if sys.platform in ("win32", "darwin"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


# ---------------------------------------------------------------- 自检
def _selftest():
    """自造 5 帧（含一条出界）走完整链路：解析→事件→渲染→CSV→证据帧→PNG。"""
    import cv2
    import numpy as np
    import tempfile

    ok = True

    def check(cond, msg):
        nonlocal ok
        if not cond:
            print("PC_DASHBOARD_SELFTEST FAIL:", msg)
            ok = False

    tmp = Path(tempfile.mkdtemp(prefix="pc_dash_selftest_"))
    ev_path = tmp / "evidence_f3.jpg"
    cv2.imwrite(str(ev_path), np.full((180, 320, 3), (95, 125, 95), np.uint8))

    samples = [
        {"type": "frame", "frame": 1, "timestamp": 1000, "camera_state": "VALID",
         "players": [{"track_id": 3, "x": 52.5, "y": 34.0, "confidence": 0.92},
                     {"track_id": 7, "x": 40.0, "y": 22.0, "confidence": 0.88}],
         "ball": {"x": 52.0, "y": 30.0, "valid": True, "space": "field"},
         "events": {"out_of_play": False, "out_side": None, "goal": False,
                    "goal_side": None, "possession": 3, "possession_dist": 1.2}},
        {"type": "frame", "frame": 2, "timestamp": 1200, "camera_state": "VALID",
         "players": [{"track_id": 3, "x": 63.0, "y": 30.0, "confidence": 0.90},
                     {"track_id": 7, "x": 66.0, "y": 28.0, "confidence": 0.85}],
         "ball": {"x": 65.0, "y": 29.0, "valid": True, "space": "field"},
         "events": {"out_of_play": False, "goal": False,
                    "possession": 7, "possession_dist": 1.0}},
        {"type": "frame", "frame": 3, "timestamp": 1400, "camera_state": "VALID",
         "players": [{"track_id": 3, "x": 88.0, "y": 26.0, "confidence": 0.87},
                     {"track_id": 7, "x": 92.0, "y": 24.0, "confidence": 0.80},
                     {"track_id": 11, "x": 70.0, "y": 44.0, "confidence": 0.75}],
         "ball": {"x": 101.0, "y": 24.0, "valid": True, "space": "field"},
         "events": {"out_of_play": False, "goal": False,
                    "possession": 7, "possession_dist": 9.1},
         "evidence": str(ev_path)},
        # 第 4 帧：球越过右端线 —— 事件流里必须出现且只出现一条“球出界”
        {"type": "frame", "frame": 4, "timestamp": 1600, "camera_state": "VALID",
         "players": [{"track_id": 3, "x": 100.0, "y": 25.0, "confidence": 0.86},
                     {"track_id": 7, "x": 103.0, "y": 26.0, "confidence": 0.79}],
         "ball": {"x": 105.4, "y": 26.0, "valid": True, "space": "field"},
         "events": {"out_of_play": True, "out_side": "right", "goal": False,
                    "goal_side": None, "possession": 3, "possession_dist": 1.1},
         "evidence": str(ev_path)},
        {"type": "frame", "frame": 5, "timestamp": 1800, "camera_state": "INVALID_VIEW",
         "tracks": [[7, 10, 10, 20, 40, 0.80, 60.0, 20.0],
                    [9, 12, 12, 24, 44, 0.72, 30.0, 18.0]],
         "ball": {}, "events": {}},
        # 第 6 帧：转播机位没有标定 —— tracks 只有 6 项（像素框），球是画面坐标。
        # 以前这种行整行被丢：视频上画着框、顶栏写「人数 0」。
        {"type": "frame", "frame": 6, "timestamp": 2000, "camera_state": "-",
         "tracks": [[12, 100, 200, 140, 320, 0.55]],
         "ball": {"x": 824.0, "y": 184.0, "valid": True, "space": "image"},
         "events": {}},
    ]

    # 1) 解析：players 与 tracks 两种格式都要认
    parsed = [normalize(s) for s in samples]
    check(all(p is not None for p in parsed), "normalize 返回 None")
    check(parsed[0].players[0]["id"] == 3, "players 格式解析错误")
    check(len(parsed[4].players) == 2 and parsed[4].players[0]["id"] == 7,
          "tracks 格式解析错误")
    check(parsed[4].camera_state == "INVALID_VIEW", "camera_state 解析错误")
    # 无标定的短 tracks（6 项）也要出人：框中心当画面坐标
    check(len(parsed[5].players) == 1 and parsed[5].players[0]["space"] == "px",
          "短 tracks（无球场坐标）解析错误")
    check(abs(parsed[5].players[0]["x"] - 120.0) < 1e-6, "短 tracks 框中心算错")
    check(parsed[2].evidence.endswith("evidence_f3.jpg"), "evidence 字段未取到")
    check(parsed[1].evidence == "", "无 evidence 时不该有值")

    # 2) 事件流：只在状态变化时记一条
    csv_path = tmp / "selftest.csv"
    dash = Dashboard(csv_path=csv_path, base_dir=tmp, source="selftest")
    dash.status("REPLAY_START")
    for st in parsed:
        dash.frame(st)
    kinds = [it.kind for it in dash.log.recent()]
    check(dash.conn == CONN_REPLAY, "连接状态未更新（%s）" % dash.conn)
    check(kinds.count("out") == 1, "出界事件应恰好 1 条，实际 %s" % kinds)
    check(kinds.count("poss") == 3, "球权变化应恰好 3 条（3→7→3），实际 %s" % kinds)
    check(dash.frames == 6, "帧计数应为 6，实际 %d" % dash.frames)

    # 3) 渲染：尺寸固定，中文与缩略图都不能崩
    img = dash.render()
    check(img is not None and img.shape == (CANVAS_H, CANVAS_W, 3),
          "渲染尺寸错误：%s" % (None if img is None else (img.shape,)))
    check(float(img.std()) > 3.0, "渲染结果近乎空白")
    check(load_thumb(str(ev_path), 120, 90) is not None, "证据帧缩略图加载失败")
    check(load_thumb("不存在的文件.jpg", 120, 90) is None, "缺失图片应返回 None")

    # 4) 空面板（还没收到帧）也必须能出图
    empty = render_dashboard(None, EventLog(), CONN_WAIT)
    check(empty.shape == (CANVAS_H, CANVAS_W, 3), "空面板渲染失败")

    # 5) CSV：表头固定 + 每帧一行
    dash.close()
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    check(rows[0] == CsvWriter.FIELDS, "CSV 表头不符：%s" % rows[0])
    check(len(rows) == 7, "CSV 应有 1 表头 + 6 行，实际 %d 行" % len(rows))
    if len(rows) >= 5:
        check(rows[4][6] == "True" and rows[4][7] == "right", "CSV 出界列不对：%s" % rows[4])

    # 6) PNG：最后一张图落到磁盘
    png = write_png(dash, tmp / "selftest_dashboard.png")
    check(Path(png).is_file() and Path(png).stat().st_size > 5000, "PNG 未写出或过小")

    # 7) 推流拉回判据（--push）：只认「相对快照变长、且有内容」的文件。
    #    这两条都是 2026-11-01 上板踩出来的：空文件被当成结果；数据写进的是
    #    服务启动时就建好的旧文件，所以「新建文件」这个判据整段漏。
    items = {"/flow/a.jsonl": (20, 1728), "/flow/b.jsonl": (0, 0),
             "/flow/c.jsonl": (5, 400)}
    pick = _pick_grown(items, {"/flow/c.jsonl": 400})
    check(pick is not None and pick[1] == "/flow/a.jsonl", "增长判据选错文件：%s" % (pick,))
    check(_pick_grown(items, {"/flow/a.jsonl": 1728, "/flow/c.jsonl": 400}) is None,
          "没有增长时不该给出结果")
    check(_parse_list("N 1\nF /flow/a.jsonl 20 1728\n") == {"/flow/a.jsonl": (20, 1728)},
          "_parse_list 解析错误")

    # 8) 板端代码模板：塞进内核前必须能编译。这条是补课的——`_BOARD_LIST` 里
    #    一个没转义的 `%d` 曾让整条推流报「not enough arguments for format string」。
    tmpls = [("list", _BOARD_LIST, {"dir": "/tmp", "pat": "*.jsonl"}),
             ("pull", _BOARD_PULL, {"remote": "/tmp/a.jsonl"}),
             ("start", _BOARD_START, {"argv": ["py", "x.py"], "log": "/tmp/l",
                                      "pidfile": "/tmp/p", "cwd": "/tmp",
                                      "flag": "/tmp/f"}),
             ("stop", _BOARD_STOP, {"pidfile": "/tmp/p", "pid": None,
                                    "flag": "/tmp/f"})]
    for name, tmpl, kw in tmpls:
        try:
            compile(tmpl % kw, "<board_%s>" % name, "exec")
        except Exception as e:                               # noqa: BLE001
            check(False, "板端模板 %s 编译失败：%s" % (name, e))

    # 9) 事件模式的板端命令：三个关键参数一个都不能丢（丢了就采不到事件）
    eargv = _event_argv(port=5009, calib="c.json", store_dir="/d", prefix="evt",
                        hdmi=False)
    for flag in ("--proposal", "--calib", "--max-det", "--ball-every", "--listen"):
        check(flag in eargv, "事件管线缺参数 %s：%s" % (flag, eargv))
    check(eargv[eargv.index("--max-det") + 1] == "0", "--max-det 必须是 0")
    check("--hdmi-preview" not in eargv, "hdmi=False 时不该带 --hdmi-preview")

    print("PC_DASHBOARD_SELFTEST", "OK" if ok else "FAIL")
    print("PC_DASHBOARD_SELFTEST_PNG", png)
    return 0 if ok else 1


# ---------------------------------------------------------------- 入口
def main():
    ap = argparse.ArgumentParser(description="Zynq-7020 足球分析上位机面板")
    ap.add_argument("--listen", default="", help="host:port，例如 0.0.0.0:5005")
    ap.add_argument("--replay", default="", help="JSONL 结果文件")
    ap.add_argument("--video", default="", help="配一段比赛视频：左侧改画「带框画面」而不是球场俯视图"
                    "（转播机位没有球场标定时用这个）")
    ap.add_argument("--video-start", type=float, default=0.0,
                    help="画面从第几秒开始对齐（配 --replay 用：推流时用 --push-start 跳过的片头，"
                         "回放同一份 JSONL 时要补回来，否则框和画面对不上）")
    ap.add_argument("--csv", default="", help="把每帧写成 CSV（列固定）")
    ap.add_argument("--speed", type=float, default=1.0, help="回放倍速，0=不限速")
    ap.add_argument("--loop", action="store_true", help="回放循环")
    ap.add_argument("--headless", action="store_true", help="不打开 GUI，只打印摘要")
    ap.add_argument("--dump-png", default="", help="把（最后）一帧渲染成 PNG 后退出")
    ap.add_argument("--dump-video", default="", metavar="MP4",
                    help="把整段回放（面板原样：左侧带框画面 + 球场俯视 + 事件流）"
                         "录成 MP4，不开窗口；存到电脑上慢慢看/剪演示视频时用")
    ap.add_argument("--dump-fps", type=float, default=0.0,
                    help="--dump-video 的帧率（0=按 10×--speed 推）")
    ap.add_argument("--dump-frame", type=int, default=-1,
                    help="配合 --dump-png：回放到该帧号就停（-1=放到最后）。"
                         "出「球刚到画面里」这类截图时用")
    ap.add_argument("--selftest", action="store_true", help="无显示器自检")
    ap.add_argument("--demo", action="store_true",
                    help="一键演示：%s 整段 %d 帧（10 s）+ 事件 + LCD 面板 + HDMI 回显，"
                         "推完自动拉回并在面板上回放。开跑前先做板端体检"
                         % (Path(DEMO_VIDEO).name, DEMO_FRAMES))
    ap.add_argument("--push", default="", metavar="VIDEO",
                    help="把这段视频推到板子上（板端 zynq-stream 在听 5007），"
                         "等板端落盘后自动把结果拉回来回放；会自动把它当作画面源")
    ap.add_argument("--board", default="192.168.2.99:5007", help="板端推流地址 host:port")
    ap.add_argument("--jupyter", default="192.168.2.99:9090",
                    help="板端 Jupyter host:port（只用来跑板端命令与拉结果）")
    ap.add_argument("--store-dir", default="/home/xilinx/zynq-yolo/build/flow",
                    help="板端结果目录（zynq-stream 的 --store-dir）")
    ap.add_argument("--store-prefix", default="net",
                    help="只认这个前缀的 JSONL（zynq-stream 的 --store-prefix）")
    ap.add_argument("--push-max-frames", type=int, default=0,
                    help="只推前 N 帧（演示/试跑用；0=整段）")
    ap.add_argument("--push-start", type=float, default=0.0,
                    help="从第几秒开始推（跳过片头/中场休息）；画面会自动对齐帧号")
    ap.add_argument("--push-fps", type=float, default=25.0,
                    help="推流速率上限；实际吞吐由板子反压决定（约 2–3 fps）")
    ap.add_argument("--push-quality", type=int, default=95,
                    help="JPEG 质量；默认 95 是量出来的（q80 会多出假球），别往下调")
    ap.add_argument("--pull-dir", default="build/pull", help="拉回的 JSONL 存在哪")
    ap.add_argument("--event", action="store_true",
                    help="事件模式：推流前在板上另起一条 `--proposal --calib` 管线，"
                         "推完自动停掉。装机服务 zynq-stream 没有 --proposal，"
                         "不加这个开关时 events 恒为空")
    ap.add_argument("--event-port", type=int, default=5009,
                    help="事件模式管线监听的端口（别和 zynq-stream 的 5007 撞）")
    ap.add_argument("--calib", default=EVENT_CALIB_DEFAULT,
                    help="事件模式用的标定文件，必须与素材配对"
                         "（中圈片段配 field_calib_ball.json）")
    ap.add_argument("--event-lcd", type=int, default=5, metavar="N",
                    help="每 N 帧把状态面板写到板载 LCD（默认 5，0=关）")
    ap.add_argument("--event-no-hdmi", action="store_true",
                    help="事件模式不回显到 HDMI（默认回显，屏上能看到框+球）")
    ap.add_argument("--event-prefix", default="evt",
                    help="事件模式落盘文件名前缀（与服务的 net_ 分开，便于区分）")
    ap.add_argument("--event-every", type=int, default=25,
                    help="事件模式的检测间隔（与验收口径一致；球员坐标在非检测帧"
                         "由跟踪器外推，调小=位置更新更勤、但更慢）")
    ap.add_argument("--event-ball-every", type=int, default=2,
                    help="事件模式的扫球间隔（202 帧片段实测 2 才采得到球）")
    args = ap.parse_args()

    if args.selftest:
        raise SystemExit(_selftest())
    if args.demo:
        # 一键演示 = 一条命令把参数都定死，现场不用记；显式给了的输入源优先，不许混用
        if args.push or args.replay or args.listen:
            ap.error("--demo 自带素材，不要与 --push / --replay / --listen 同用")
        if not Path(DEMO_VIDEO).is_file():
            ap.error("演示素材不存在：%s" % DEMO_VIDEO)
        args.push = DEMO_VIDEO
        args.push_max_frames = DEMO_FRAMES
        args.push_start = 0.0
        args.event = True
        args.event_every = 5          # 检测帧密度：25 是验收口径，演示用 5 才采得到球
        args.event_lcd = 5            # 板载 LCD 每 5 帧刷一次状态面板
        args.csv = args.csv or DEMO_CSV
        ok, why = board_preflight(args.jupyter)
        print(("BOARD_OK " if ok else "BOARD_DOWN ") + why)
        if not ok:
            print("演示先别开始：按 AGENTS.md，板子离线时不做硬件验证、也不声称跑通。"
                  "先给板子上电、等 60–90 s，再跑同一条命令。")
            return 3
    if args.push:
        if args.listen or args.replay:
            ap.error("--push 不能与 --listen / --replay 同用（推完会自动回放）")
        if not Path(args.push).is_file():
            ap.error("找不到要推的视频：%s" % args.push)
        if not args.video:
            args.video = args.push          # 画面与结果同源，帧号才对得上
    if not args.listen and not args.replay and not args.push:
        ap.error("需要 --listen / --replay / --push 之一")
    if args.listen and args.replay:
        ap.error("--listen 与 --replay 只能二选一")
    if args.event and not args.push:
        ap.error("--event 只作用于 --push（事件模式是「另起一条管线再推流」）")
    if args.event and args.board.rsplit(":", 1)[-1] == str(args.event_port):
        ap.error("--board 的端口不能和 --event-port 相同（%d）" % args.event_port)

    # 事件模式的 JSONL 只有球场坐标：加载单应逆矩阵，画面才能把球员/球回投成圈
    if args.calib and Path(args.calib).is_file():
        load_calib_hinv(args.calib)

    # ---- 批量路径：回放完直接出 PNG / 录成 MP4，不开窗口（出图 / CI / 演示素材）
    if args.replay and (args.dump_png or args.dump_video):
        rp = Path(args.replay)
        if not rp.is_file():
            ap.error("找不到回放文件：%s" % args.replay)
        _vid = _video_with_start(args.video, args.video_start) if args.video else None
        dash = Dashboard(csv_path=args.csv or None, base_dir=rp.parent, source=rp.name,
                         video=_vid)
        dash.status("REPLAY_START")
        fps = args.dump_fps or (10.0 * args.speed if args.speed > 0 else 10.0)
        vw = _open_dump_video(args.dump_video, fps) if args.dump_video else None
        n = 0
        try:
            for st in read_jsonl(rp):
                dash.frame(st)
                n += 1
                if vw is not None:
                    vw.write(dash.render())      # 录像不按回放节奏等，直接按帧写
                if args.dump_frame >= 0 and st.frame >= args.dump_frame:
                    break
                if vw is None and args.speed > 0:
                    time.sleep(1.0 / (10.0 * args.speed))
        finally:
            dash.close()
            if vw is not None:
                vw.release()
        dash.status("REPLAY_DONE %d frames" % n)
        png = write_png(dash, args.dump_png) if args.dump_png else ""
        print("PC_DASHBOARD_OK frames=%d" % n)
        if png:
            print("PC_DASHBOARD_PNG %s" % png)
        if args.dump_video:
            size = Path(args.dump_video).stat().st_size if Path(args.dump_video).is_file() else 0
            print("PC_DASHBOARD_MP4 %s frames=%d fps=%.1f bytes=%d"
                  % (Path(args.dump_video).resolve(), n, fps, size))
        return 0 if n else 1

    q = queue.Queue()
    stop = threading.Event()

    # 画面源要先建出来：push_worker 拿到帧号偏移后直接写回它
    _vid = _video_with_start(args.video, args.video_start) if args.video else None
    if _vid is not None and not _vid.ok:
        ap.error("打不开视频：%s" % args.video)

    if args.listen:
        host, port = args.listen.rsplit(":", 1)
        threading.Thread(target=recv_lines, args=(host, int(port), q, stop),
                         daemon=True).start()
        base_dir, source = None, "TCP %s" % args.listen
    elif args.push:
        bhost, bport = args.board.rsplit(":", 1)
        ev = None
        port, pattern = int(bport), "%s_*.jsonl" % args.store_prefix
        if args.event:
            port = args.event_port
            pattern = "%s_*.jsonl" % args.event_prefix
            ev = {"port": port,
                  "prefix": args.event_prefix,
                  "pidfile": "/tmp/pc_event.pid",
                  "log": "/tmp/pc_event_run.log",
                  "argv": _event_argv(port=port, calib=args.calib,
                                      store_dir=args.store_dir,
                                      prefix=args.event_prefix,
                                      hdmi=not args.event_no_hdmi,
                                      lcd=args.event_lcd,
                                      every=args.event_every,
                                      ball_every=args.event_ball_every)}
        threading.Thread(target=push_worker, args=(args.push, q, stop),
                         kwargs=dict(host=bhost, port=port,
                                     jupyter=args.jupyter,
                                     store_dir=args.store_dir,
                                     pattern=pattern,
                                     out_dir=args.pull_dir,
                                     fps=args.push_fps, quality=args.push_quality,
                                     max_frames=args.push_max_frames,
                                     start_sec=args.push_start,
                                     video_src=_vid,
                                     speed=args.speed, event=ev), daemon=True).start()
        base_dir, source = None, "推流 %s" % Path(args.push).name
    else:
        rp = Path(args.replay)
        if not rp.is_file():
            ap.error("找不到回放文件：%s" % args.replay)
        threading.Thread(target=replay_worker,
                         args=(rp, q, stop, args.speed, args.loop), daemon=True).start()
        base_dir, source = rp.parent, rp.name

    dash = Dashboard(csv_path=args.csv or None, base_dir=base_dir, source=source,
                     video=_vid)

    # ---- 无窗口路径：--headless 摘要，或 --listen 收流后出 PNG
    if args.headless or args.dump_png:
        n = 0
        try:
            while not stop.is_set():
                try:
                    kind, payload = q.get(timeout=0.5)
                except queue.Empty:
                    continue
                if kind == "status":
                    dash.status(payload)
                    print("STATUS", payload)
                    if (str(payload).startswith("REPLAY_DONE")
                            or str(payload).startswith("PUSH_FAIL")
                            or str(payload) == "DISCONNECTED"):
                        break
                elif kind == "frame":
                    dash.frame(payload)
                    n += 1
                    if args.headless and (n % 10 == 0 or args.replay):
                        print("FRAME %s players=%d ball=%s cam=%s events=%s" % (
                            payload.frame, len(payload.players),
                            payload.ball.get("valid") if payload.ball else False,
                            payload.camera_state,
                            json.dumps(payload.events, ensure_ascii=False)))
        finally:
            dash.close()
        print("PC_DASHBOARD_OK frames=%d" % n if n else "PC_DASHBOARD_FAIL frames=0")
        if args.dump_png:
            print("PC_DASHBOARD_PNG %s" % write_png(dash, args.dump_png))
        return 0 if n else 1

    # ---- GUI 路径
    if not _has_display():
        print("PC_DASHBOARD_NO_DISPLAY")
        print("提示：无显示环境请加 --headless 或 --dump-png")
        return 2
    try:
        run_gui(dash, q, stop)
    finally:
        dash.close()
    print("PC_DASHBOARD_OK frames=%d" % dash.frames)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
