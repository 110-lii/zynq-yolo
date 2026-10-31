#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""video_send.py —— PC 端：把视频/摄像头逐帧送进板子（D015 输入侧）

为什么要有它
------------
D015（2026-10-20 用户裁定「现在不需要摄像头一切的视频由网口从电脑上输送」）
一直只落地了**输出**半条（`video_back.py`：板 → PC 的带框画面）。输入这半条
主线从来只吃板本地文件。本工具补上输入侧，配 `board_pipeline.py --listen`。

协议（小端，与 `board_pipeline.NetFrameSource` 对齐）
    magic b"ZYVI"
    循环：[u32 len][JPEG 字节]      （len = 0 表示结束）
为什么是 JPEG：1280×720 裸 RGB 一帧 2.7 MB，100 Mbit 链路只够 4 fps；
JPEG q80 约 100 KB，25 fps 才 2.5 MB/s，板端解一帧 ~5 ms，不是瓶颈。
（ncnn 检测本身 ~980 ms/帧，所以**实际吞吐由板子决定**——发送端会被 TCP
反压自然限速，`--fps` 只是上限，不是保证。）

用法
    PC：
        py tools/video_send.py --video data/samples/scenes/c03_t01290.0.mp4 \
            --host 192.168.2.99 --port 5007 --fps 25
        # 直接用电脑摄像头（演示「实时送视频」用这个）
        py tools/video_send.py --camera 0 --host 192.168.2.99 --port 5007 --fps 15
    板上（先起，它会阻塞等 PC 连上来）：
        py tools/board_jupyter.py "import subprocess;subprocess.Popen([...])"
        或直接：python3 tools/board_pipeline.py --listen 5007 --every 15 ...

判据令牌：VIDEO_SEND_OK <frames> <MB> <MB/s> <fps> / VIDEO_SEND_FAIL <reason>
"""
import argparse
import socket
import struct
import sys
import time

MAGIC = b"ZYVI"


def _selftest():
    """本机回环自检：不需要板子，验证「发送端 ↔ NetFrameSource」的协议与帧数。"""
    import threading
    import cv2
    import numpy as np
    sys.path.insert(0, "tools")
    from board_pipeline import NetFrameSource

    port = 15007
    got = {}

    def _recv():
        src = NetFrameSource(port, fps=25.0, timeout=20.0)
        frames = []
        while True:
            ok, f = src.read()
            if not ok:
                break
            frames.append((f.shape, int(f.mean())))
        src.release()
        got["frames"] = frames
        got["bytes"] = src.bytes

    th = threading.Thread(target=_recv, daemon=True)
    th.start()
    time.sleep(0.5)

    # 造 6 帧可辨识的图（不同均值），确认到达顺序与内容都不是同一张重复图
    frames = [np.full((180, 320, 3), 20 * i, np.uint8) for i in range(1, 7)]
    sock = socket.create_connection(("127.0.0.1", port), timeout=5.0)
    sock.sendall(MAGIC)
    for f in frames:
        ok, buf = cv2.imencode(".jpg", f, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        assert ok
        sock.sendall(struct.pack("<I", len(buf.tobytes())) + buf.tobytes())
    sock.sendall(struct.pack("<I", 0))
    sock.close()
    th.join(timeout=20.0)

    errs = []
    if th.is_alive():
        errs.append("receiver thread hung")
    if len(got.get("frames", [])) != len(frames):
        errs.append("frames %d != %d" % (len(got.get("frames", [])), len(frames)))
    else:
        for i, (shape, mean) in enumerate(got["frames"]):
            if shape != frames[i].shape:
                errs.append("frame %d shape %s" % (i, shape))
            # JPEG 有损：均值只要求单调、且与期望差 < 6
            if abs(mean - 20 * (i + 1)) > 6:
                errs.append("frame %d mean %d != ~%d" % (i, mean, 20 * (i + 1)))
    if got.get("bytes", 0) <= 0:
        errs.append("bytes not counted")
    if errs:
        print("VIDEO_SEND_SELFTEST_FAIL " + "; ".join(errs[:4]))
        return 1
    print("VIDEO_SEND_SELFTEST OK frames=%d bytes=%d" % (len(got["frames"]), got["bytes"]))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true", help="本机回环自检，不需要板子")
    src = ap.add_mutually_exclusive_group(required=False)
    src.add_argument("--video", default="", help="PC 本地视频文件")
    src.add_argument("--camera", type=int, default=-1, help="本机摄像头编号（0 = 默认）")
    ap.add_argument("--host", default="192.168.2.99")
    ap.add_argument("--port", type=int, default=5007)
    ap.add_argument("--fps", type=float, default=25.0,
                    help="发送速率上限；0 = 不限（会被板子反压限速）")
    ap.add_argument("--quality", type=int, default=80, help="JPEG 质量")
    ap.add_argument("--max-frames", type=int, default=0)
    ap.add_argument("--scale", type=float, default=1.0,
                    help="送之前按比例缩小（长宽同时乘）；1.0 = 原样")
    ap.add_argument("--connect-timeout", type=float, default=60.0,
                    help="等板端 --listen 就绪的秒数")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()
    if not args.video and args.camera < 0:
        ap.error("need --video <file> or --camera <n>")

    import cv2
    import numpy as np                                    # noqa: F401（cv2 返回 ndarray 时需要）

    if args.camera >= 0:
        cap = cv2.VideoCapture(args.camera)
        what = "camera:%d" % args.camera
    else:
        cap = cv2.VideoCapture(args.video)
        what = args.video
    if not cap.isOpened():
        print("VIDEO_SEND_FAIL open %s" % what)
        return 2

    # 板端可能在等我们，也可能还没起；给一个明确的重试窗口，别一失败就放弃。
    t_conn = time.time()
    sock = None
    while True:
        try:
            sock = socket.create_connection((args.host, int(args.port)), timeout=5.0)
            break
        except OSError as e:
            if time.time() - t_conn > args.connect_timeout:
                print("VIDEO_SEND_FAIL connect %s:%s (%s)" % (args.host, args.port, e))
                return 2
            time.sleep(0.5)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.sendall(MAGIC)
    print("VIDEO_SEND_CONNECTED %s:%d src=%s" % (args.host, args.port, what), flush=True)

    period = 1.0 / args.fps if args.fps > 0 else 0.0
    n, nbytes, t0 = 0, 0, time.time()
    next_t = time.time()
    try:
        while True:
            if args.max_frames and n >= args.max_frames:
                break
            ok, frame = cap.read()
            if not ok:
                break
            if args.scale and args.scale != 1.0:
                frame = cv2.resize(frame, None, fx=args.scale, fy=args.scale,
                                   interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".jpg", frame,
                                   [int(cv2.IMWRITE_JPEG_QUALITY), args.quality])
            if not ok:
                continue
            data = buf.tobytes()
            sock.sendall(struct.pack("<I", len(data)) + data)
            n += 1
            nbytes += len(data)
            if period:
                next_t += period
                dt = next_t - time.time()
                if dt > 0:
                    time.sleep(dt)
                else:
                    next_t = time.time()      # 被反压拖慢：放弃补帧，重新对齐
            if n % 25 == 0:
                el = max(time.time() - t0, 1e-6)
                print("VIDEO_SEND_PROGRESS %d %.1f MB %.2f MB/s %.1f fps"
                      % (n, nbytes / 1e6, nbytes / 1e6 / el, n / el), flush=True)
        sock.sendall(struct.pack("<I", 0))
    except (BrokenPipeError, ConnectionResetError) as e:
        print("VIDEO_SEND_FAIL peer_closed after %d frames (%s)" % (n, e))
        return 3
    finally:
        cap.release()
        try:
            sock.close()
        except OSError:
            pass

    el = max(time.time() - t0, 1e-6)
    print("VIDEO_SEND_OK %d %.2f %.2f %.1f"
          % (n, nbytes / 1e6, nbytes / 1e6 / el, n / el))
    return 0


if __name__ == "__main__":
    sys.exit(main())
