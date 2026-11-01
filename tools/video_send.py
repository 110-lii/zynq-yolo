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
JPEG q95 实测约 235 KB/帧（q80 约 110 KB），25 fps 约 5.9 MB/s，仍在 100 Mbit 内；
板端解一帧 ~5 ms，相对 ncnn 的 ~980 ms 不是瓶颈。
（ncnn 检测本身 ~980 ms/帧，所以**实际吞吐由板子决定**——发送端会被 TCP
反压自然限速，实测 2.2–2.3 fps，`--fps` 只是上限，不是保证。）

**质量默认 95 是量出来的，不是拍脑袋**：q80 时同一段 c03 在板上比文件源多出 10 帧
假球（f45–54），人工核对是球员的白鞋；q95 下球帧与文件源**逐帧一致**。
仍要记住 JPEG 是有损的：它能对齐本片的判决，不等于「板上像素与文件逐位相同」。

用法
    PC：
        py tools/video_send.py --video data/samples/scenes/c03_t01290.0.mp4 \
            --host 192.168.2.99 --port 5007 --fps 25
        # 直接用电脑摄像头（演示「实时送视频」用这个）
        py tools/video_send.py --camera 0 --host 192.168.2.99 --port 5007 --fps 15
        # 跳过片头/中场，从第 60 秒开始送
        py tools/video_send.py --video clip_04.mp4 --start 60 --max-frames 45
    板上（先起，它会阻塞等 PC 连上来）：
        py tools/board_jupyter.py "import subprocess;subprocess.Popen([...])"
        或直接：python3 tools/board_pipeline.py --listen 5007 --every 15 ...

推流 + 看结果一条命令（上位机里直接调这里的 `push_frames`）：
    py tools/pc_dashboard.py --push clip_04.mp4 --push-start 60 --push-max-frames 45

判据令牌：VIDEO_SEND_OK <frames> <MB> <MB/s> <fps> start_frame=<n> / VIDEO_SEND_FAIL <reason>
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

    # 第二阶段：push_frames —— pc_dashboard --push 与命令行共用的就是这一份，
    # 它自己也得走一遍回环，否则重构后只有 CLI 被验证过。
    import os
    import tempfile
    tmp = tempfile.mkdtemp(prefix="vsend_")
    mp4 = os.path.join(tmp, "push_selftest.mp4")
    vw = cv2.VideoWriter(mp4, cv2.VideoWriter_fourcc(*"mp4v"), 25.0, (320, 180))
    for i in range(10):
        vw.write(np.full((180, 320, 3), 20 * (i + 1), np.uint8))
    vw.release()

    got2 = {}

    def _recv2():
        s2 = NetFrameSource(15008, fps=25.0, timeout=20.0)
        n2 = 0
        while True:
            ok, _f = s2.read()
            if not ok:
                break
            n2 += 1
        s2.release()
        got2["frames"] = n2

    th2 = threading.Thread(target=_recv2, daemon=True)
    th2.start()
    time.sleep(0.5)
    prog = []
    res = push_frames(mp4, host="127.0.0.1", port=15008, fps=0,
                      on_progress=prog.append)
    th2.join(timeout=20.0)
    os.remove(mp4)
    os.rmdir(tmp)

    if not res["ok"]:
        errs.append("push_frames failed: %s" % res["reason"])
    if res["frames"] != 10:
        errs.append("push_frames sent %d != 10" % res["frames"])
    if got2.get("frames") != 10:
        errs.append("push_frames peer got %s != 10" % got2.get("frames"))
    if not prog:
        errs.append("push_frames on_progress never called")

    if errs:
        print("VIDEO_SEND_SELFTEST_FAIL " + "; ".join(errs[:4]))
        return 1
    print("VIDEO_SEND_SELFTEST OK frames=%d bytes=%d push=%d"
          % (len(got["frames"]), got["bytes"], res["frames"]))
    return 0


def push_frames(src, host="192.168.2.99", port=5007, fps=25.0, quality=95,
                max_frames=0, scale=1.0, connect_timeout=60.0, start_sec=0.0,
                on_progress=None, on_log=None, stop=None):
    """把 src（视频路径，或摄像头编号 int）逐帧推给板端 `--listen`。

    抽成函数是为了让 `pc_dashboard.py --push` 与命令行走**同一份**协议实现：
    两处各写一遍，迟早有一天只在一边修 bug。
    不打印任何令牌——令牌由调用方按自己的场合决定，这里只回调。

    start_sec：从第几秒开始送（演示时跳过片头/中场休息）。返回的 start_frame
    是实际落点帧号，调用方要用它去对齐画面（板端帧号从 0 重新数）。
    on_progress(dict) 每 25 帧一次：{"frames","bytes","mb","mb_s","fps"}；
    stop 是 threading.Event，置位后发终止标记并返回 ok=True（属于正常收尾）。
    返回 {"ok","frames","bytes","secs","mb_s","fps","start_frame","reason"}。
    """
    import cv2

    def _res(ok, n=0, nbytes=0, el=0.0, reason="", start_frame=0):
        return {"ok": ok, "frames": n, "bytes": nbytes, "secs": el,
                "mb_s": nbytes / 1e6 / el if el else 0.0,
                "fps": n / el if el else 0.0,
                "start_frame": start_frame, "reason": reason}

    what = ("camera:%d" % src) if isinstance(src, int) and src >= 0 else str(src)
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        return _res(False, reason="open %s" % what)

    start_frame = 0
    if start_sec and start_sec > 0:
        cap.set(cv2.CAP_PROP_POS_MSEC, float(start_sec) * 1000.0)
        start_frame = int(cap.get(cv2.CAP_PROP_POS_FRAMES) or 0)

    # 板端可能在等我们，也可能还没起；给一个明确的重试窗口，别一失败就放弃。
    t_conn = time.time()
    sock = None
    while True:
        try:
            sock = socket.create_connection((host, int(port)), timeout=5.0)
            break
        except OSError as e:
            if time.time() - t_conn > connect_timeout:
                cap.release()
                return _res(False, start_frame=start_frame,
                            reason="connect %s:%s (%s)" % (host, port, e))
            if stop is not None and stop.is_set():
                cap.release()
                return _res(False, start_frame=start_frame, reason="stopped")
            time.sleep(0.5)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    # 连接时用的 5 s 超时会一直留在 socket 上：板端在跑 ncnn（~1 s/帧）且 SD 写慢时，
    # sendall 卡住 5 s 就抛 TimeoutError，整条推流被误判成失败（2026-11-01 实测踩到）。
    # 这里改成一个「久到只有真掉线才会触发」的写超时，真正的对端断开仍会抛出来。
    sock.settimeout(120.0)
    sock.sendall(MAGIC)
    if on_log:
        on_log("VIDEO_SEND_CONNECTED %s:%d src=%s" % (host, int(port), what))

    period = 1.0 / fps if fps > 0 else 0.0
    n, nbytes, t0 = 0, 0, time.time()
    next_t = time.time()
    reason = ""
    try:
        while True:
            if max_frames and n >= max_frames:
                break
            if stop is not None and stop.is_set():
                break
            ok, frame = cap.read()
            if not ok:
                break
            if scale and scale != 1.0:
                frame = cv2.resize(frame, None, fx=scale, fy=scale,
                                   interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".jpg", frame,
                                   [int(cv2.IMWRITE_JPEG_QUALITY), quality])
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
            if on_progress and n % 25 == 0:
                el = max(time.time() - t0, 1e-6)
                on_progress({"frames": n, "bytes": nbytes, "mb": nbytes / 1e6,
                             "mb_s": nbytes / 1e6 / el, "fps": n / el})
        sock.sendall(struct.pack("<I", 0))
    except (BrokenPipeError, ConnectionResetError) as e:
        reason = "peer_closed after %d frames (%s)" % (n, e)
    except OSError as e:                 # 含 socket.timeout：连接被掐/板端不再读
        reason = "socket %s after %d frames (%s)" % (type(e).__name__, n, e)
    finally:
        cap.release()
        try:
            sock.close()
        except OSError:
            pass

    el = max(time.time() - t0, 1e-6)
    # 收尾补一次进度：短片（<25 帧）否则一次回调都不会有，调用方会以为推流没动过
    if on_progress and n and n % 25:
        on_progress({"frames": n, "bytes": nbytes, "mb": nbytes / 1e6,
                     "mb_s": nbytes / 1e6 / el, "fps": n / el})
    return _res(not reason, n, nbytes, el, reason, start_frame)


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
    ap.add_argument("--quality", type=int, default=95,
                    help="JPEG 质量。**默认 95 不是随手写的**：q80 时同一段 c03 在板上"
                         "多出 10 帧假球（f45–54，人工核对是球员的白鞋，见 ai/sessions/024）；"
                         "q95 下球帧与文件源逐帧一致（100–114、140–144）")
    ap.add_argument("--max-frames", type=int, default=0)
    ap.add_argument("--scale", type=float, default=1.0,
                    help="送之前按比例缩小（长宽同时乘）；1.0 = 原样")
    ap.add_argument("--connect-timeout", type=float, default=60.0,
                    help="等板端 --listen 就绪的秒数")
    ap.add_argument("--start", type=float, default=0.0,
                    help="从第几秒开始送（跳过片头/中场）；板端帧号会从 0 重新数")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()
    if not args.video and args.camera < 0:
        ap.error("need --video <file> or --camera <n>")

    src = args.camera if args.camera >= 0 else args.video

    def _progress(d):
        print("VIDEO_SEND_PROGRESS %d %.1f MB %.2f MB/s %.1f fps"
              % (d["frames"], d["mb"], d["mb_s"], d["fps"]), flush=True)

    res = push_frames(src, host=args.host, port=args.port, fps=args.fps,
                      quality=args.quality, max_frames=args.max_frames,
                      scale=args.scale, connect_timeout=args.connect_timeout,
                      start_sec=args.start,
                      on_progress=_progress,
                      on_log=lambda s: print(s, flush=True))
    if not res["ok"]:
        print("VIDEO_SEND_FAIL %s" % res["reason"])
        return 2 if res["reason"].startswith("open ") else 3
    print("VIDEO_SEND_OK %d %.2f %.2f %.1f start_frame=%d"
          % (res["frames"], res["bytes"] / 1e6, res["mb_s"], res["fps"],
             res["start_frame"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
