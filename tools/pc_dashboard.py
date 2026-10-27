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
            })
    elif isinstance(obj.get("tracks"), list):
        for t in obj["tracks"]:
            if not isinstance(t, (list, tuple)) or len(t) < 8:
                continue
            if t[6] is None or t[7] is None:
                continue
            players.append({"id": t[0], "x": _num(t[6]), "y": _num(t[7]),
                            "conf": _num(t[5])})
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
    """时间戳格式化：板端给的是毫秒（如 1000），小于 100 的值按秒解释。"""
    v = _num(ts, -1.0)
    if v < 0:
        return "--:--.--"
    sec = v / 1000.0 if v >= 100 else v
    m, s = divmod(sec, 60)
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
    if conn in (CONN_WAIT, CONN_REPLAY):
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


def _draw_pitch_panel(img, tb, st):
    import cv2
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
        btxt = "球：%.1f, %.1f m" % (_num(bx), _num(by))
    elif b:
        btxt = "球：不可见"
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
                     csv_path="", source=""):
    """把状态 + 事件 + 证据帧渲染成一张 BGR 位图（GUI 与 PNG 共用同一份逻辑）。"""
    import numpy as np
    img = np.full((CANVAS_H, CANVAS_W, 3), COL_BG, dtype=np.uint8)
    log = log or EventLog()
    tb = TextBatch()
    _draw_status_bar(img, tb, st, conn)
    _draw_pitch_panel(img, tb, st)
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

    def __init__(self, csv_path=None, base_dir=None, source=""):
        self.state = None
        self.log = EventLog(MAX_EVENTS)
        self.conn = CONN_IDLE
        self.frames = 0
        self.base_dir = Path(base_dir) if base_dir else None
        self.csv_path = str(csv_path) if csv_path else ""
        self.source = source
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

    def frame(self, st: FrameState):
        self.state = st
        self.log.update(st)
        self.frames += 1
        if self.writer:
            self.writer.write(st)

    def render(self):
        return render_dashboard(self.state, self.log, self.conn, self.base_dir,
                                self.csv_path, self.source)

    def close(self):
        if self.writer:
            self.writer.close()
            self.writer = None


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
    ]

    # 1) 解析：players 与 tracks 两种格式都要认
    parsed = [normalize(s) for s in samples]
    check(all(p is not None for p in parsed), "normalize 返回 None")
    check(parsed[0].players[0]["id"] == 3, "players 格式解析错误")
    check(len(parsed[4].players) == 2 and parsed[4].players[0]["id"] == 7,
          "tracks 格式解析错误")
    check(parsed[4].camera_state == "INVALID_VIEW", "camera_state 解析错误")
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
    check(dash.frames == 5, "帧计数应为 5，实际 %d" % dash.frames)

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
    check(len(rows) == 6, "CSV 应有 1 表头 + 5 行，实际 %d 行" % len(rows))
    if len(rows) >= 5:
        check(rows[4][6] == "True" and rows[4][7] == "right", "CSV 出界列不对：%s" % rows[4])

    # 6) PNG：最后一张图落到磁盘
    png = write_png(dash, tmp / "selftest_dashboard.png")
    check(Path(png).is_file() and Path(png).stat().st_size > 5000, "PNG 未写出或过小")

    print("PC_DASHBOARD_SELFTEST", "OK" if ok else "FAIL")
    print("PC_DASHBOARD_SELFTEST_PNG", png)
    return 0 if ok else 1


# ---------------------------------------------------------------- 入口
def main():
    ap = argparse.ArgumentParser(description="Zynq-7020 足球分析上位机面板")
    ap.add_argument("--listen", default="", help="host:port，例如 0.0.0.0:5005")
    ap.add_argument("--replay", default="", help="JSONL 结果文件")
    ap.add_argument("--csv", default="", help="把每帧写成 CSV（列固定）")
    ap.add_argument("--speed", type=float, default=1.0, help="回放倍速，0=不限速")
    ap.add_argument("--loop", action="store_true", help="回放循环")
    ap.add_argument("--headless", action="store_true", help="不打开 GUI，只打印摘要")
    ap.add_argument("--dump-png", default="", help="把（最后）一帧渲染成 PNG 后退出")
    ap.add_argument("--selftest", action="store_true", help="无显示器自检")
    args = ap.parse_args()

    if args.selftest:
        raise SystemExit(_selftest())
    if not args.listen and not args.replay:
        ap.error("需要 --listen 或 --replay")
    if args.listen and args.replay:
        ap.error("--listen 与 --replay 只能二选一")

    # ---- 批量路径：回放完直接出 PNG，不开窗口（出图 / CI / 演示截图）
    if args.replay and args.dump_png:
        rp = Path(args.replay)
        if not rp.is_file():
            ap.error("找不到回放文件：%s" % args.replay)
        dash = Dashboard(csv_path=args.csv or None, base_dir=rp.parent, source=rp.name)
        dash.status("REPLAY_START")
        n = 0
        try:
            for st in read_jsonl(rp):
                dash.frame(st)
                n += 1
                if args.speed > 0:
                    time.sleep(1.0 / (10.0 * args.speed))
        finally:
            dash.close()
        dash.status("REPLAY_DONE %d frames" % n)
        png = write_png(dash, args.dump_png)
        print("PC_DASHBOARD_OK frames=%d" % n)
        print("PC_DASHBOARD_PNG %s" % png)
        return 0 if n else 1

    q = queue.Queue()
    stop = threading.Event()

    if args.listen:
        host, port = args.listen.rsplit(":", 1)
        threading.Thread(target=recv_lines, args=(host, int(port), q, stop),
                         daemon=True).start()
        base_dir, source = None, "TCP %s" % args.listen
    else:
        rp = Path(args.replay)
        if not rp.is_file():
            ap.error("找不到回放文件：%s" % args.replay)
        threading.Thread(target=replay_worker,
                         args=(rp, q, stop, args.speed, args.loop), daemon=True).start()
        base_dir, source = rp.parent, rp.name

    dash = Dashboard(csv_path=args.csv or None, base_dir=base_dir, source=source)

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
                    if str(payload).startswith("REPLAY_DONE") or str(payload) == "DISCONNECTED":
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
        print("PC_DASHBOARD_OK frames=%d" % n)
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
