#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pc_dashboard.py —— 电脑端接收/回放板端结果，显示球场态势与事件。

两种数据源：
  1. `--listen 0.0.0.0:5005`：板端 `tools/result_stream.py` 把 JSONL 逐行发来；
  2. `--replay run_*.jsonl`：直接回放 SD 卡/提交包里的结果文件。

显示内容：
  * 105x68 球场俯视图：球员点（带 track_id）、球、事件文字；
  * 当前帧号、球员数、球坐标/有效性、机位状态、事件计数；
  * 事件日志与 CSV 导出。

无显示环境（SSH/CI）加 `--headless`：只解析并打印摘要，用于自检/管道。

自检（不需要显示器）：
    python tools/pc_dashboard.py --selftest
    # 末行 PC_DASHBOARD_OK
"""
import argparse
import csv
import json
import queue
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.dont_write_bytecode = True

PITCH_X, PITCH_Y = 105.0, 68.0


@dataclass
class FrameState:
    frame: int = -1
    timestamp: float = 0.0
    players: list = field(default_factory=list)
    ball: dict = field(default_factory=dict)
    camera_state: str = "-"
    events: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


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
    return st


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


class CsvWriter:
    FIELDS = ["frame", "timestamp", "players", "ball_x", "ball_y",
              "ball_valid", "camera_state", "event"]

    def __init__(self, path):
        self.f = open(path, "w", encoding="utf-8", newline="")
        self.w = csv.DictWriter(self.f, fieldnames=self.FIELDS)
        self.w.writeheader()

    def write(self, st: FrameState):
        b = st.ball or {}
        ev = st.events or {}
        event = ev.get("type") or ev.get("event") or (
            "goal" if ev.get("goal") else ("out" if ev.get("out_of_play") else ""))
        self.w.writerow({
            "frame": st.frame, "timestamp": st.timestamp,
            "players": len(st.players),
            "ball_x": b.get("x"), "ball_y": b.get("y"),
            "ball_valid": b.get("valid", False),
            "camera_state": st.camera_state, "event": event,
        })

    def close(self):
        self.f.close()


def draw_pitch(st: FrameState, w=840, h=560):
    """用 OpenCV 画一张 105x68 态势图；返回 BGR ndarray。"""
    import cv2
    import numpy as np

    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = (40, 120, 40)
    m = 30

    def X(x):
        return int(m + (x / PITCH_X) * (w - 2 * m))

    def Y(y):
        return int(m + (y / PITCH_Y) * (h - 2 * m))

    cv2.rectangle(img, (X(0), Y(0)), (X(PITCH_X), Y(PITCH_Y)), (230, 230, 230), 2)
    cv2.line(img, (X(PITCH_X / 2), Y(0)), (X(PITCH_X / 2), Y(PITCH_Y)), (230, 230, 230), 2)
    cv2.circle(img, (X(PITCH_X / 2), Y(PITCH_Y / 2)), int((9.15 / PITCH_X) * (w - 2 * m)),
               (230, 230, 230), 2)
    for x0, x1 in ((0, 16.5), (PITCH_X - 16.5, PITCH_X)):
        cv2.rectangle(img, (X(x0), Y(PITCH_Y / 2 - 20.16)),
                      (X(x1), Y(PITCH_Y / 2 + 20.16)), (230, 230, 230), 2)

    for p in st.players[:25]:
        if not (-5 <= p["x"] <= PITCH_X + 5 and -5 <= p["y"] <= PITCH_Y + 5):
            continue
        cv2.circle(img, (X(p["x"]), Y(p["y"])), 5, (60, 220, 255), -1)
        cv2.putText(img, str(p["id"]), (X(p["x"]) + 6, Y(p["y"]) - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)

    b = st.ball or {}
    if b.get("valid") and b.get("space", "field") == "field" and b.get("x") is not None:
        cv2.circle(img, (X(_num(b["x"])), Y(_num(b["y"]))), 8, (0, 0, 255), 2)
        cv2.putText(img, "BALL", (X(_num(b["x"])) + 9, Y(_num(b["y"])) + 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1, cv2.LINE_AA)

    cv2.putText(img, "frame %s  players %d  cam %s" % (st.frame, len(st.players), st.camera_state),
                (m, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
    return img


def _selftest():
    samples = [
        {"frame": 1, "timestamp": 1000, "camera_state": "VALID",
         "players": [{"track_id": 1, "x": 52.5, "y": 34.0, "confidence": 0.9}],
         "ball": {"x": 52.0, "y": 30.0, "valid": True, "space": "field"},
         "events": {"out_of_play": False}},
        {"frame": 2, "timestamp": 2000, "camera_state": "INVALID_VIEW",
         "tracks": [[7, 10, 10, 20, 40, 0.8, 60.0, 20.0]], "ball": {}},
    ]
    parsed = [normalize(x) for x in samples]
    ok = (parsed[0] is not None and parsed[0].players[0]["id"] == 1
          and len(parsed[1].players) == 1 and parsed[1].players[0]["id"] == 7
          and parsed[1].camera_state == "INVALID_VIEW")
    print("PC_DASHBOARD_SELFTEST", "OK" if ok else "FAIL")
    return 0 if ok else 1


def run_gui(q, stop, csv_path=None):
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("Zynq-7020 足球分析 · PC Dashboard")
    info = tk.StringVar(value="waiting...")
    evtext = tk.StringVar(value="")
    tk.Label(root, textvariable=info, anchor="w", font=("Consolas", 11)).pack(fill="x", padx=8, pady=4)
    canvas = tk.Canvas(root, width=840, height=560, bg="#1f6b2a")
    canvas.pack(padx=8, pady=4)
    tk.Label(root, textvariable=evtext, anchor="w", justify="left", font=("Consolas", 9),
             fg="#b00020").pack(fill="x", padx=8, pady=4)
    writer = CsvWriter(csv_path) if csv_path else None
    last = {"n": 0}

    def draw(st):
        w, h, m = 840, 560, 30

        def X(x):
            return int(m + (x / PITCH_X) * (w - 2 * m))

        def Y(y):
            return int(m + (y / PITCH_Y) * (h - 2 * m))

        canvas.delete("all")
        canvas.create_rectangle(X(0), Y(0), X(PITCH_X), Y(PITCH_Y), outline="white", width=2)
        canvas.create_line(X(PITCH_X / 2), Y(0), X(PITCH_X / 2), Y(PITCH_Y), fill="white", width=2)
        canvas.create_oval(X(PITCH_X / 2) - 55, Y(PITCH_Y / 2) - 55,
                           X(PITCH_X / 2) + 55, Y(PITCH_Y / 2) + 55, outline="white", width=2)
        for p in st.players[:25]:
            if not (-5 <= p["x"] <= PITCH_X + 5 and -5 <= p["y"] <= PITCH_Y + 5):
                continue
            x, y = X(p["x"]), Y(p["y"])
            canvas.create_oval(x - 5, y - 5, x + 5, y + 5, fill="#ffd166", outline="")
            canvas.create_text(x + 8, y - 8, text=str(p["id"]), fill="white", anchor="w",
                               font=("Consolas", 8))
        b = st.ball or {}
        if b.get("valid") and b.get("x") is not None:
            if b.get("space", "field") == "field":
                x, y = X(_num(b["x"])), Y(_num(b["y"]))
                canvas.create_oval(x - 8, y - 8, x + 8, y + 8, outline="#ff3b30", width=2)
                canvas.create_text(x + 10, y + 4, text="BALL", fill="#ff3b30", anchor="w",
                                   font=("Consolas", 9, "bold"))
        info.set("frame %s | players %d | ball %s | cam %s | %s" % (
            st.frame, len(st.players),
            ("%.1f,%.1f" % (_num(b.get("x")), _num(b.get("y")))) if b.get("valid") else "-",
            st.camera_state, time.strftime("%H:%M:%S")))
        ev = st.events or {}
        if ev:
            evtext.set("events: " + json.dumps(ev, ensure_ascii=False)[:300])
        if writer:
            writer.write(st)
        last["n"] += 1

    def poll():
        if stop.is_set():
            if writer:
                writer.close()
            root.destroy()
            return
        try:
            while True:
                kind, payload = q.get_nowait()
                if kind == "frame":
                    draw(payload)
                elif kind == "status":
                    info.set(str(payload))
        except queue.Empty:
            pass
        root.after(40, poll)

    root.after(40, poll)
    root.mainloop()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", default="", help="host:port，例如 0.0.0.0:5005")
    ap.add_argument("--replay", default="", help="JSONL 文件")
    ap.add_argument("--csv", default="", help="把收到的帧写成 CSV")
    ap.add_argument("--speed", type=float, default=1.0, help="回放倍速")
    ap.add_argument("--loop", action="store_true", help="回放循环")
    ap.add_argument("--headless", action="store_true", help="不打开 GUI，只打印摘要")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        raise SystemExit(_selftest())
    if not args.listen and not args.replay:
        ap.error("需要 --listen 或 --replay")
    if args.listen and args.replay:
        ap.error("--listen 与 --replay 只能二选一")

    q = queue.Queue()
    stop = threading.Event()

    if args.listen:
        host, port = args.listen.rsplit(":", 1)
        threading.Thread(target=recv_lines, args=(host, int(port), q, stop), daemon=True).start()

    if args.replay:
        def replay():
            while not stop.is_set():
                t0 = time.time()
                n = 0
                for st in read_jsonl(Path(args.replay)):
                    if stop.is_set():
                        return
                    q.put(("frame", st))
                    n += 1
                    if args.speed > 0:
                        dt = 1.0 / (10.0 * args.speed)
                        time.sleep(dt)
                q.put(("status", "REPLAY_DONE %d frames" % n))
                if not args.loop:
                    stop.set()
                    return
                time.sleep(0.5)
        threading.Thread(target=replay, daemon=True).start()

    if args.headless:
        n = 0
        writer = CsvWriter(args.csv) if args.csv else None
        try:
            while not (args.replay and stop.is_set()):
                try:
                    kind, payload = q.get(timeout=0.5)
                except queue.Empty:
                    continue
                if kind == "status":
                    print("STATUS", payload)
                    if payload.startswith("REPLAY_DONE") or payload == "DISCONNECTED":
                        break
                elif kind == "frame":
                    writer and writer.write(payload)
                    n += 1
                    if n % 10 == 0 or args.replay:
                        print("FRAME %s players=%d ball=%s cam=%s" % (
                            payload.frame, len(payload.players),
                            payload.ball.get("valid") if payload.ball else False,
                            payload.camera_state))
        finally:
            writer and writer.close()
        print("PC_DASHBOARD_OK frames=%d" % n)
        return 0

    run_gui(q, stop, args.csv or None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
