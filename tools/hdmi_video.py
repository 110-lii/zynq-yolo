"""hdmi_video.py —— PS 侧：把「带检测框的画面」写进 DDR 帧缓存，PL 扫描输出到 HDMI。

为什么需要它
------------
D018 当时明确「不做视频环出/OSD 叠框」；现在要求 HDMI 直接输出**带框的视频**。
硬件侧是 `hdmi_video.bit`（PL 的 fb_scanout 经 S_AXI_HP0 读 DDR → TMDS），
软件侧就是本工具：每帧把 BGR888 画成 32bpp 字写进帧缓存，再通过 AXI GPIO 告诉 PL
基址/跨距/宽/使能。

寄存器（AXI GPIO，地址见 build/scripts/bd_hdmi_video.tcl）
    cfg  0x41200000  ch1 = 帧缓存物理地址（字节）
                     ch2 = {en[27], width[26:16], stride[15:0]}
    st   0x41210000  = {frames[31:16], under[15:0]}   （PL 计数，只读）

三种用法
--------
1) 本机出图（不需要板子，验收/报告用）：
       py tools/hdmi_video.py --replay data/results/run_x.jsonl \
           --video clip.mp4 --png-out out.png --max-frames 30
2) 板上环出（PYNQ）：
       python3 tools/hdmi_video.py --replay /home/xilinx/run_x.jsonl \
           --video /home/xilinx/clip.mp4 --fps 25
3) 自检：
       py tools/hdmi_video.py --selftest

判据令牌：HDMI_VIDEO_OK <frames> <fps> / HDMI_VIDEO_FAIL <reason>
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np

W, H = 1280, 720          # 帧缓存与输出时序
BUF_ADDR_CFG = 0x41200000  # AXI GPIO cfg
BUF_ADDR_ST = 0x41210000   # AXI GPIO status

COL_BOX = (0, 255, 0)
COL_BALL = (0, 0, 255)
COL_EVENT = (0, 255, 255)


# ---------------------------------------------------------------- 数据读取
def read_jsonl(path, limit=0):
    rows = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if limit and len(rows) >= limit:
                break
    return rows


def norm_frame(obj):
    """把一行 JSON 归一成 {players:[{id,x,y,conf,box?}], ball:{...}, events:{...}}。"""
    fr = obj.get("frame") if isinstance(obj, dict) else None
    players = []
    for p in (obj.get("players") or []):
        if not isinstance(p, dict):
            continue
        players.append({
            "id": p.get("id", p.get("track_id", -1)),
            "x": float(p.get("x", p.get("cx", 0.0)) or 0.0),
            "y": float(p.get("y", p.get("cy", 0.0)) or 0.0),
            "conf": float(p.get("conf", 0.0) or 0.0),
            "box": p.get("box"),
        })
    # ★ board_pipeline 落盘的 JSONL 用的是 `tracks: [[id,x1,y1,x2,y2,score,...], ...]`
    #   （像素坐标），与上面 `players`（球场坐标）不是同一口径。以前只认 `players`，
    #   于是拿 JSONL 回放/回传时框**一个都没画**，表头还显示 players 0 —— 看着像「没检到人」。
    if not players:
        for t in (obj.get("tracks") or []):
            if not isinstance(t, (list, tuple)) or len(t) < 5:
                continue
            x1, y1, x2, y2 = (float(t[1]), float(t[2]), float(t[3]), float(t[4]))
            players.append({
                "id": t[0],
                "x": (x1 + x2) * 0.5,
                "y": y2,
                "conf": float(t[5]) if len(t) > 5 else 0.0,
                "box": (x1, y1, x2, y2),
            })
    b = obj.get("ball") or {}
    ball = None
    if isinstance(b, dict) and b.get("valid", b.get("x") is not None):
        if b.get("x") is not None:
            ball = {"x": float(b["x"]), "y": float(b["y"] or 0.0),
                    "space": b.get("space", "field")}
    ev = obj.get("events") or {}
    return {"frame": fr, "players": players, "ball": ball, "events": ev,
            "camera_state": obj.get("camera_state")}


# ---------------------------------------------------------------- 绘制
def _img_from_field(x, y, calib):
    """球场坐标 → 图像坐标（有标定时用单应；没有就把球场坐标当归一化画）。"""
    if not calib:
        px = int(x / 105.0 * (W - 1))
        py = int(y / 68.0 * (H - 1))
        return px, py
    Hm = np.array(calib["H"], dtype=np.float64)
    v = Hm @ np.array([x, y, 1.0])
    if abs(v[2]) < 1e-9:
        return -1, -1
    return int(v[0] / v[2]), int(v[1] / v[2])


def draw_frame(frame, st, calib=None):
    """在 BGR 帧上画球员框/球/事件条。框优先用检测原始 box，没有就用球场坐标投影。"""
    img = frame.copy()
    for p in st["players"]:
        box = p.get("box")
        if box and len(box) == 4:
            x1, y1, x2, y2 = [int(v) for v in box]
        else:
            cx, cy = _img_from_field(p["x"], p["y"], calib)
            if cx < 0:
                continue
            x1, y1, x2, y2 = cx - 12, cy - 30, cx + 12, cy + 6
        cv2.rectangle(img, (x1, y1), (x2, y2), COL_BOX, 2)
        cv2.putText(img, "#%s" % p["id"], (x1, max(12, y1 - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, COL_BOX, 1)
    if st["ball"]:
        # 没有标定时 board_pipeline 给的是**画面坐标**（space=image），
        # 以前一律当球场坐标投影 —— 球会被画到屏幕中间偏左上，跟真球没关系
        if st["ball"].get("space", "field") in ("image", "px"):
            bx, by = int(st["ball"]["x"]), int(st["ball"]["y"])
        else:
            bx, by = _img_from_field(st["ball"]["x"], st["ball"]["y"], calib)
        if bx >= 0:
            cv2.circle(img, (bx, by), 12, COL_BALL, 2)
            cv2.putText(img, "BALL", (bx + 14, max(14, by - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, COL_BALL, 1)
    ev = st["events"] or {}
    tags = []
    if ev.get("out_of_play"):
        tags.append("OUT(%s)" % ev.get("out_side"))
    if ev.get("goal"):
        tags.append("GOAL(%s)" % ev.get("goal_side"))
    if ev.get("possession") is not None:
        tags.append("POSS #%s %.2fm" % (ev.get("possession"), ev.get("possession_dist") or 0))
    cs = st.get("camera_state")
    if cs:
        tags.append("CAM:%s" % cs)
    txt = "  ".join(tags) if tags else "no events"
    cv2.rectangle(img, (0, 0), (W, 28), (0, 0, 0), -1)
    cv2.putText(img, "frame %s   players %d   %s" % (st["frame"], len(st["players"]), txt),
                (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, COL_EVENT, 2)
    return img


def pack_frame(img_bgr, width=W, stride_px=W):
    """BGR 图 → 32bpp 字数组（低 24 位 = R|G<<8|B<<16，bit31 填充 0）。

    ★ 2026-10-31 用户报「真视频显示得很慢」。同一块板上同一批帧背靠背对比
    （排除两次测量之间的主频/负载差异）：旧写法 169.0 ms/帧，新写法 28.2 ms/帧
    —— 整整 6 倍。瓶颈是旧写法在 CPU 上把 92 万像素整帧做三轮 astype + 三组移位。
    改成一次 cv2.cvtColor 补 Alpha 再按 uint32 看：内存里本就是 B,G,R,A，
    小端读成 32 位恰好是 B | G<<8 | R<<16 | A<<24，与逐通道移位**逐位相同**
    （本机整帧随机图实测 maxdiff=0），再把高 8 位抹掉保持 bit31=0。
    """
    small = cv2.resize(img_bgr, (width, img_bgr.shape[0]), interpolation=cv2.INTER_AREA) \
        if img_bgr.shape[1] != width else img_bgr
    bgra = cv2.cvtColor(small, cv2.COLOR_BGR2BGRA)
    words = bgra.view(np.uint32).reshape(small.shape[0], width)
    np.bitwise_and(words, np.uint32(0x00FFFFFF), out=words)   # 原地清高 8 位（A=255 不许漏出去）
    if stride_px != width:
        pad = np.zeros((words.shape[0], stride_px - width), dtype=np.uint32)
        words = np.concatenate([words, pad], axis=1)
    return words


# ---------------------------------------------------------------- 板上通道
class HdmiVideoOut(object):
    """PYNQ 侧：分配 DDR 帧缓存 + 配置 PL 寄存器。"""

    def __init__(self, cfg_addr=BUF_ADDR_CFG, st_addr=BUF_ADDR_ST, width=W, height=H):
        from pynq import MMIO, allocate          # 只在板上可用
        self.width, self.height = width, height
        self.stride_px = width
        self.bufs = [allocate(shape=(height, width), dtype=np.uint32) for _ in range(2)]
        self.cfg = MMIO(cfg_addr, 0x10000)
        self.st = MMIO(st_addr, 0x10000)
        self.cur = 0
        pack = self.stride_px * 4 | (width << 16) | (1 << 27)
        self.pack = pack
        self.cfg.write(0x8, pack)
        self.cfg.write(0x0, self.bufs[0].physical_address)

    def check_cfg(self):
        """写-读闭环：配置寄存器到底有没有写进去？

        2026-10-30 21:0x 的教训：`frames` 60/s、`under=0` 全部正常，屏上却什么都没有，
        最后查出 `cfg_pack` 读回是 **0**（写的是 `0x0D001400`）、`cfg_addr` 读回的是
        `{frames, under}` —— 也就是说**配置根本没进 PL**，`cfg_en=0`，扫描输出一直关着。
        原有判据一个都没报警，因为它们量的是「扫描在不在跑」，不是「配置有没有生效」。

        这里用两个互不相同的魔数写 0x0 / 0x8 再立刻读回，最后恢复现场。
        调用方把返回值打进日志；不一致就必须当 FAIL，而不是继续往下测。
        """
        magic_a, magic_p = 0xA5A5_5A5A, 0x5A5A_A5A5
        ok_a = ok_p = None
        try:
            self.cfg.write(0x0, magic_a)
            self.cfg.write(0x8, magic_p)
            ok_a = (int(self.cfg.read(0x0)) & 0xFFFFFFFF) == magic_a
            ok_p = (int(self.cfg.read(0x8)) & 0xFFFFFFFF) == magic_p
        finally:                                   # 无论成败都把现场写回去
            self.cfg.write(0x8, self.pack)
            self.cfg.write(0x0, self.bufs[self.cur].physical_address)
        return {"addr": bool(ok_a), "pack": bool(ok_p)}

    def show(self, img_bgr):
        """写一帧并切到该缓冲（PL 在 vs 沿锁存基址 ⇒ 双缓冲无撕裂）。"""
        src = img_bgr
        if img_bgr.shape[1] != self.width or img_bgr.shape[0] != self.height:
            src = cv2.resize(img_bgr, (self.width, self.height), interpolation=cv2.INTER_AREA)
        # ★ 2026-10-31 提速第二步：**直接写进帧缓存**。
        #   旧路径 = pack_frame 另分配一帧（board 上 169 ms）→ np.copyto 再搬一次（7 ms）。
        #   这里把 PYNQ 的 (H,W) uint32 缓冲看成 (H,W,4) uint8，cvtColor 一步 BGR→BGRA
        #   就地填进 DDR，省掉一次整帧分配 + 一次整帧拷贝。
        #   代价：高 8 位是 255（A）而不是 0 —— PL 只用 bit[23:0]（`pixel_data = rd_q[23:0]`），
        #   `st_sum` 也只累加 24 位，所有判据都掩 0xFFFFFF，**观测不到**这个差别；
        #   pack_frame() 仍然是契约（逐位、bit31=0），单测和期望值都以它为准。
        dst = self.bufs[self.cur].view(np.uint8).reshape(self.height, self.width, 4)
        if self.stride_px == self.width:
            cv2.cvtColor(src, cv2.COLOR_BGR2BGRA, dst=dst)
        else:                                    # 带行填充的缓冲：仍走契约路径
            np.copyto(self.bufs[self.cur], pack_frame(src, self.width, self.stride_px))
        # PYNQ 的 allocate() 默认是可缓存（cacheable）的：CPU 写完只在 cache 里，
        # PL 走 HP0 直接读 DDR ⇒ 读到的是旧内容（上板现象：整屏纯白/garbage）。
        # 必须在切缓冲之前把这一帧刷到 DRAM。
        flush = getattr(self.bufs[self.cur], "flush", None)
        if callable(flush):
            flush()
        self.cfg.write(0x0, self.bufs[self.cur].physical_address)
        self.cur ^= 1

    def status(self):
        v = self.st.read(0x0)
        return {"frames": (v >> 16) & 0xFFFF, "under": v & 0xFFFF}


# ---------------------------------------------------------------- 自检
def _selftest():
    ok = True
    canvas = np.zeros((H, W, 3), np.uint8)
    canvas[::40, :] = (40, 40, 40)
    st = {"frame": 42,
          "players": [{"id": 3, "x": 30.0, "y": 20.0, "conf": 0.9},
                      {"id": 7, "x": 70.0, "y": 50.0, "conf": 0.8}],
          "ball": {"x": 40.0, "y": 30.0},
          "events": {"out_of_play": True, "out_side": "right", "possession": 7,
                     "possession_dist": 1.87},
          "camera_state": "valid"}
    img = draw_frame(canvas, st)
    words = pack_frame(img)
    # 打包往返：整帧解包后必须与原图逐像素一致（单点抽查会漏，首版就漏过一次）
    back = np.stack([(words & 0xFF).astype(np.uint8),
                     ((words >> 8) & 0xFF).astype(np.uint8),
                     ((words >> 16) & 0xFF).astype(np.uint8)], axis=2)
    dmax = int(np.abs(back.astype(np.int16) - img.astype(np.int16)).max())
    if dmax != 0:
        print("HDMI_VIDEO_SELFTEST_FAIL pack roundtrip dmax=%d" % dmax)
        ok = False
    out = Path(os.environ.get("TMPDIR", ".")) / "hdmi_video_selftest.png"
    cv2.imwrite(str(out), img)
    # 画框命中检查：球员区域应出现绿色像素
    g = ((img[..., 1] > 200) & (img[..., 0] < 80) & (img[..., 2] < 80)).sum()
    if g < 100:
        print("HDMI_VIDEO_SELFTEST_FAIL no green boxes (%d px)" % g)
        ok = False
    print("HDMI_VIDEO_SELFTEST", "OK" if ok else "FAIL", "png=%s" % out)
    print("pack_roundtrip_dmax=%d  green_px=%d  words=%dx%d" % (dmax, int(g), words.shape[1], words.shape[0]))
    return 0 if ok else 1


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", default="", help="board_pipeline 输出的 JSONL")
    ap.add_argument("--video", default="", help="对应视频（取画面）；不给就用黑底画")
    ap.add_argument("--calib", default="", help="field_calib.json（球场坐标→图像）")
    ap.add_argument("--fps", type=float, default=25.0,
                    help="环出刷新率（片源多为 25 fps；PS 侧 show 一帧实测 9.2 ms，25 fps 有余量）")
    ap.add_argument("--max-frames", type=int, default=0)
    ap.add_argument("--png-out", default="", help="本机出图路径（不接板子）")
    ap.add_argument("--timeout", type=float, default=0.0, help="板上运行秒数，0=不限")
    ap.add_argument("--no-sync-frame", action="store_true",
                    help="不按 JSONL 的 frame 号定位画面（默认会定位）")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()

    rows = read_jsonl(args.replay) if args.replay else []
    calib = json.load(open(args.calib, encoding="utf-8")) if args.calib else None
    cap = cv2.VideoCapture(args.video) if args.video else None

    out = None
    if not args.png_out:
        try:
            out = HdmiVideoOut()
        except Exception as e:                      # 没 pynq / 不是板子
            print("HDMI_VIDEO_FAIL no board: %s" % e)
            return 2

    n = 0
    t0 = time.perf_counter()
    idx = 0
    # ★ 定时用**绝对时刻表**，不是「画完再睡 1/fps」。
    #   旧写法每帧实际耗时 = 画图 + 1/fps，--fps 12 时就是 22 ms + 83 ms ≈ 9.5 fps，
    #   屏上看到的就是「卡、慢」，而且用户设的 fps 并不是屏上的 fps。
    #   现在按目标周期对齐：画得快就多睡一点，画得慢就不补（防止越积越落后）。
    period = 1.0 / max(0.5, args.fps)
    next_t = time.perf_counter()
    while True:
        if args.max_frames and n >= args.max_frames:
            break
        if args.timeout and (time.perf_counter() - t0) > args.timeout:
            break
        st = norm_frame(rows[idx % len(rows)]) if rows else \
            {"frame": n, "players": [], "ball": None, "events": {}}
        idx += 1
        frame = np.zeros((H, W, 3), np.uint8)
        if cap is not None:
            # ★ 按 JSONL 里的帧号对位取画面：顺序读会在「回放指定帧」时把框叠到第 0 帧上
            if st.get("frame") is not None and not args.no_sync_frame:
                want = int(st["frame"])
                # 只有**真的跳了**才 seek：逐帧顺放时每次 seek 都要重解一段 GOP，
                # board 上一次 seek 就是几十毫秒 —— 这是「回放卡」的第二个来源
                if abs(cap.get(cv2.CAP_PROP_POS_FRAMES) - want) > 0.5:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, want)
            ok, f = cap.read()
            if not ok:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, f = cap.read()
            if ok:
                frame = cv2.resize(f, (W, H))
        img = draw_frame(frame, st, calib)
        if args.png_out:
            cv2.imwrite(args.png_out, img)
            print("HDMI_VIDEO_OK 1 0 png=%s" % args.png_out)
            print("camera_state=%s events=%s" % (st.get("camera_state"), st.get("events")))
            return 0
        out.show(img)
        n += 1
        next_t += period
        dt = next_t - time.perf_counter()
        if dt > 0:
            time.sleep(dt)
        else:
            next_t = time.perf_counter()        # 落后超过一整帧：放弃补帧，重新对齐
    if out is not None:
        el = time.perf_counter() - t0
        print("HDMI_VIDEO_OK %d %.1f status=%s achieved=%.1f"
              % (n, args.fps, out.status(), n / el if el > 0 else 0.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
