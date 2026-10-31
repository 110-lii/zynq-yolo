#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""video_back.py —— 带框视频**从网口回传**（HDMI 环出之外的备用通道）

为什么要有它
------------
HDMI 环出这一路（`fb_scanout`）到现在仍不稳定：板端读回三次实测，
屏幕内容相对源图**同时在行、列两个方向错位**，且错多少每次上电都不一样
（列 425 / 399 / 423，行 128，见 `build/hdmi_pixel_probe.py` 与
`ai/sessions/022_hdmi_osd_full_flow.md`）。累加和/拍数/队列游标全都报正常，
说明是取数（DDR→行缓存）与扫描输出之间的相位在帧界处没锁死，不是软件能补的。
用户 2026-10-31 明确给了退路：「不行就从网口回传带框的视频」——就是这个工具。

协议（TCP，板端 listen / PC 端 connect）
    magic b"ZYVB"
    循环：[u32 len][JPEG 字节]   （len = 0 表示结束）
    JPEG 质量默认 75，1280x720 一帧约 60–110 KB；10 fps 约 1 MB/s，
    100 Mbit 链路（~11 MB/s）绰绰有余 —— 网口本来就是这条链路的输入端。

用法
    板上（盯住一条片子，框来自 board_pipeline 的 JSONL；没有 JSONL 就只发原片）：
        python3 tools/video_back.py --serve --video /home/xilinx/inc.mp4 \
            --tracks /home/xilinx/tracks.json --fps 10 --port 5006
    PC：
        py tools/video_back.py --recv --host 192.168.2.99 \
            --out build/annotated_back.mp4 --png-out build/annotated_back.png

判据令牌：VIDEO_BACK_OK <frames> <MB> <fps> / VIDEO_BACK_FAIL <reason>
"""
import argparse
import json
import socket
import struct
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hdmi_video import W, H, draw_frame, norm_frame   # noqa: E402

MAGIC = b"ZYVB"


# ---------------------------------------------------------------- 板端
def _load_tracks(path):
    """JSONL → {帧号或序号: 归一化状态}。帧号对不上就退回按行序配对。"""
    by_frame, seq = {}, []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") in ("summary", "evidence"):
                    continue
                st = norm_frame(obj)
                seq.append(st)
                if st.get("frame") is not None:
                    try:
                        by_frame[int(st["frame"])] = st
                    except (TypeError, ValueError):
                        pass
    except FileNotFoundError:
        return {}, []
    return by_frame, seq


def serve(args):
    by_frame, seq = _load_tracks(args.tracks) if args.tracks else ({}, [])
    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        print("VIDEO_BACK_FAIL cannot open video %s" % args.video)
        return 2
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", args.port))
    srv.listen(1)
    print("VIDEO_BACK_LISTEN port=%d tracks=%d" % (args.port, len(by_frame) or len(seq)), flush=True)
    conn, peer = srv.accept()
    print("VIDEO_BACK_CONNECTED %s" % (peer,), flush=True)
    conn.sendall(MAGIC)
    period = 1.0 / args.fps if args.fps > 0 else 0.0
    n, nbytes, t0 = 0, 0, time.time()
    blank = {"frame": 0, "players": [], "ball": None, "events": {}, "camera_state": None}
    last = None
    try:
        while True:
            if args.max_frames and n >= args.max_frames:
                break
            ok, frame = cap.read()
            if not ok:
                break
            frame = cv2.resize(frame, (W, H)) if frame.shape[1] != W else frame
            # ★ 2026-10-31：这里原先是
            #       st = by_frame.get(idx) or by_frame.get(n) or (seq[n] if n < len(seq) else blank)
            #   两个问题，都会让「框落在错误的帧上」：
            #   ① board_pipeline --every 15 的 JSONL 里，`frame` 字段是**源帧号**
            #      （0,15,30…），而 `seq[n]` 是**第 n 行**。两者只在 every==1 时相等，
            #      否则回退到 seq[n] 就把第 7 帧的框画到视频第 7 帧上 —— 差 15 倍。
            #   ② `idx = st["frame"] + 1` 由框内容反推播放位置，一旦错位就自我延续。
            #   现在用**视频自己的帧计数** `vid_idx` 去查：有 frame 号就只认 frame 号。
            vid_idx = n
            st = by_frame.get(vid_idx)
            if st is None and not by_frame:
                st = seq[vid_idx] if vid_idx < len(seq) else blank
            elif st is None:
                st = blank                      # 这一帧确实没检测（不是「查不到」）
            # 检测是「每 --every 帧一次」，所以绝大多数行是空的。原样回传 ⇒ 框只在
            # 1/15 帧闪一下，肉眼看就是「没有框」（今晚抓帧才发现的）。--hold（默认开）
            # 让空帧沿用**上一帧有内容的**结果；坐标不插值、不预测，宁可不平滑也不编造位置。
            # 要拍「逐帧真检测」的证据时用 --no-hold。
            if st is not blank and (st.get("players") or st.get("ball")):
                last = st
            elif args.hold and last is not None:
                st = dict(last)
                st["frame"] = vid_idx
            img = draw_frame(frame, st) if st is not blank else frame
            ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), args.quality])
            if not ok:
                continue
            conn.sendall(struct.pack("<I", len(buf)) + buf.tobytes())
            n += 1
            nbytes += len(buf)
            el = time.time() - t0
            if period:
                time.sleep(max(0.0, period * n - el))
        conn.sendall(struct.pack("<I", 0))
    finally:
        el = max(time.time() - t0, 1e-6)
        print("VIDEO_BACK_OK %d %.2f %.1f" % (n, nbytes / 1e6, n / el), flush=True)
        conn.close()
        srv.close()
    return 0


# ---------------------------------------------------------------- PC 端
def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise IOError("peer closed (%d/%d)" % (len(buf), n))
        buf += chunk
    return buf


def recv(args):
    sock = socket.create_connection((args.host, int(args.port)), timeout=10)
    sock.settimeout(30)
    if _recv_exact(sock, 4) != MAGIC:
        print("VIDEO_BACK_FAIL bad magic")
        return 2
    writer, n, nbytes, t0, save_png = None, 0, 0, time.time(), bool(args.png_out)
    try:
        while True:
            (ln,) = struct.unpack("<I", _recv_exact(sock, 4))
            if ln == 0:
                break
            buf = np.frombuffer(_recv_exact(sock, ln), dtype=np.uint8)
            img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            if img is None:
                continue
            if writer is None:
                fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                writer = cv2.VideoWriter(args.out, fourcc, max(args.fps, 1.0),
                                         (img.shape[1], img.shape[0]))
            writer.write(img)
            n += 1
            nbytes += ln
            if save_png and n == args.png_frame:
                Path(args.png_out).parent.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(args.png_out, img)
                save_png = False
            if args.show:
                cv2.imshow("board annotated", img)
                if cv2.waitKey(1) & 0xFF == 27:
                    break
    finally:
        if writer is not None:
            writer.release()
        if args.show:
            cv2.destroyAllWindows()
        sock.close()
    el = max(time.time() - t0, 1e-6)
    print("VIDEO_BACK_OK %d %.2f %.1f -> %s" % (n, nbytes / 1e6, n / el, args.out))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--serve", action="store_true", help="板端：发")
    ap.add_argument("--recv", action="store_true", help="PC 端：收")
    ap.add_argument("--video", default="", help="板上视频路径")
    ap.add_argument("--tracks", default="", help="board_pipeline 的 JSONL")
    ap.add_argument("--fps", type=float, default=10.0)
    ap.add_argument("--port", type=int, default=5006)
    ap.add_argument("--quality", type=int, default=75)
    ap.add_argument("--max-frames", type=int, default=0)
    ap.add_argument("--host", default="192.168.2.99")
    ap.add_argument("--out", default="build/annotated_back.mp4")
    ap.add_argument("--png-out", default="")
    ap.add_argument("--png-frame", type=int, default=30)
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--hold", dest="hold", action="store_true", default=True,
                    help="空检测帧沿用上一帧有内容的框（默认开）；--no-hold 关掉，逐帧真检测")
    ap.add_argument("--no-hold", dest="hold", action="store_false")
    args = ap.parse_args()
    if args.serve:
        return serve(args)
    if args.recv:
        return recv(args)
    ap.error("need --serve or --recv")


if __name__ == "__main__":
    raise SystemExit(main())
