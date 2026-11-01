"""board_detect.py —— 图片 -> ncnn 检测 -> 原图坐标框

两种预处理：
    默认  cv2.resize（双线性/最近邻由调用方决定）
    --pl  PL preproc（overlay 在进程内只加载一次）

库用法（推荐，避免重复配置 PL）：
    from board_detect import init_pl, detect
    init_pl(repo)                       # 进程启动时一次
    dets, ms = detect(path, bin, param, model, 0.25, 0.45, use_pl=True, repo=repo)
"""
import argparse
import json
import os
import subprocess
import tempfile

import cv2
import numpy as np

_PL = {}


def init_pl(repo):
    """加载 preproc overlay（只做一次），返回 run_fn。"""
    if _PL:
        return _PL["run"]
    import xml.etree.ElementTree as ET

    from pynq import Overlay, allocate

    bit = os.path.join(repo, "board", "overlay", "preproc.bit")
    hwh = os.path.join(repo, "board", "overlay", "preproc.hwh")
    ovl = Overlay(bit)
    regs = {}
    for ab in ET.parse(hwh).getroot().iter("ADDRESSBLOCK"):
        iface = ab.get("INTERFACE")
        for reg in ab.iter("REGISTER"):
            off = None
            for pr in reg.findall("PROPERTY"):
                if pr.get("NAME") == "ADDRESS_OFFSET":
                    off = int(pr.get("VALUE"), 0)
            if reg.get("NAME") and off is not None:
                regs[(iface, reg.get("NAME"))] = off
    wins = {}
    for key, info in ovl.ip_dict.items():
        if "preproc" in str(info.get("type", "")).lower() and info.get("mem_id"):
            wins[info["mem_id"]] = getattr(ovl, key).mmio
    ctl, pr = wins["s_axi_control"], wins["s_axi_control_r"]

    def base(name):
        for cand in (name, name + "_r"):
            if ("s_axi_control_r", cand + "_1") in regs or ("s_axi_control_r", cand) in regs:
                return cand
        raise KeyError(name)

    in_buf = allocate(shape=(1280 * 720 * 3,), dtype=np.uint8)
    out_buf = allocate(shape=(320 * 320 * 3,), dtype=np.uint8)
    for name, buf in (("in", in_buf), ("out", out_buf)):
        nm = base(name)
        if ("s_axi_control_r", nm + "_1") in regs:
            o1 = regs[("s_axi_control_r", nm + "_1")]
            o2 = regs[("s_axi_control_r", nm + "_2")]
        else:
            o1 = regs[("s_axi_control_r", nm)]
            o2 = o1 + 4
        addr = int(buf.physical_address)
        pr.write(o1, addr & 0xFFFFFFFF)
        pr.write(o2, (addr >> 32) & 0xFFFFFFFF)

    def run(bgr):
        h, w = bgr.shape[:2]
        in_buf[: bgr.size] = bgr.reshape(-1)
        in_buf.flush()
        ctl.write(regs[("s_axi_control", "W_IN")], w)
        ctl.write(regs[("s_axi_control", "H_IN")], h)
        ctl.write(0x00, 0x01)
        while not (ctl.read(0x00) & 0x2):
            pass
        out_buf.invalidate()
        return np.array(out_buf)

    _PL.update(run=run, inbuf=in_buf, outbuf=out_buf)
    return run


def detect(image_path, bin_path, param, model, conf=0.25, iou=0.45,
           use_pl=False, repo=None, size=320, letterbox=True):
    """返回 (dets, infer_ms)。dets = [[x1,y1,x2,y2,score], ...]，原图坐标。

    size：送进网络的边长（默认 320）。换 640 模型时必须三处一起改 ——
    这里、`ncnn_infer` 的第 8 个参数、以及模型的导出尺寸；任一不一致，
    框会整体缩放错位（不报错，表现为"像没检出来"）。

    letterbox：**保持宽高比、补灰边到正方形**（默认开，2026-11-02 改）。
    以前是直接 `cv2.resize` 压成正方形（squash），854×480 的画面会被竖向拉伸 1.78×。
    实测（100 张 val 图、conf 0.25、IoU 0.5，真值来自教师模型）：
        基线模型 320：squash 召回 0.162 → letterbox **0.330**
        640 微调 640：squash 召回 0.264 → letterbox **0.597**
    也就是说同一份模型、同样的推理量，只改预处理就能翻倍 —— 因为训练时用的就是
    letterbox，压扁等于把目标尺度/形状都换掉了。补边值 114 与 ultralytics 一致。
    """
    im = cv2.imread(image_path)
    if im is None:
        return [], None
    h0, w0 = im.shape[:2]
    size = int(size)
    planar = 0
    pad_x = pad_y = 0
    scale = 1.0
    fd, raw = tempfile.mkstemp(suffix=".rgb")
    os.close(fd)
    try:
        if use_pl:
            run_pl = init_pl(repo or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            out = run_pl(im)
            with open(raw, "wb") as fh:
                fh.write(out.tobytes())
            planar = 1
        elif letterbox:
            scale = min(size / float(w0), size / float(h0))
            nw, nh = max(1, int(round(w0 * scale))), max(1, int(round(h0 * scale)))
            pad_x, pad_y = (size - nw) // 2, (size - nh) // 2
            canvas = np.full((size, size, 3), 114, np.uint8)
            canvas[pad_y:pad_y + nh, pad_x:pad_x + nw] = cv2.resize(im, (nw, nh))
            rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
            rgb.astype(np.uint8).tofile(raw)
        else:
            rgb = cv2.cvtColor(cv2.resize(im, (size, size)), cv2.COLOR_BGR2RGB)
            rgb.astype(np.uint8).tofile(raw)

        p = subprocess.run([bin_path, param, model, raw, str(conf), str(iou), str(planar),
                            str(size)],
                           capture_output=True, text=True, timeout=180)
    finally:
        try:
            os.unlink(raw)
        except OSError:
            pass
    if p.returncode != 0:
        return [], None

    if planar or not letterbox:
        sx, sy = w0 / float(size), h0 / float(size)
    else:
        sx = sy = 1.0 / scale          # 逆变换：先减补边，再除缩放

    dets, infer_ms = [], None
    for line in p.stdout.splitlines():
        t = line.split()
        if not t:
            continue
        if t[0] == "DET":
            x1, y1, x2, y2, s = map(float, t[1:6])
            if letterbox and not planar:
                x1, x2 = (x1 - pad_x) * sx, (x2 - pad_x) * sx
                y1, y2 = (y1 - pad_y) * sy, (y2 - pad_y) * sy
            else:
                x1, x2, y1, y2 = x1 * sx, x2 * sx, y1 * sy, y2 * sy
            dets.append([x1, y1, x2, y2, s])
        elif t[0] == "INFER_MS":
            infer_ms = float(t[1])
    return dets, infer_ms


def detect_planar(planar, w0, h0, bin_path, param, model, conf=0.25, iou=0.45):
    """直接喂 PL 已生成的 3x320x320 RGB 平面（channel-first），跳过 JPEG/resize。

    planar: np.ndarray/bytes，长度 3*320*320；w0/h0: 原图尺寸，用于把框缩回原图。
    返回 (dets, infer_ms)，格式与 detect() 一致。
    """
    raw_bytes = planar.tobytes() if hasattr(planar, "tobytes") else bytes(planar)
    if len(raw_bytes) != 3 * 320 * 320:
        return [], None
    fd, raw = tempfile.mkstemp(suffix=".rgb")
    os.close(fd)
    try:
        with open(raw, "wb") as fh:
            fh.write(raw_bytes)
        p = subprocess.run([bin_path, param, model, raw, str(conf), str(iou), "1"],
                           capture_output=True, text=True, timeout=180)
    finally:
        try:
            os.unlink(raw)
        except OSError:
            pass
    if p.returncode != 0:
        return [], None

    sx, sy = w0 / 320.0, h0 / 320.0
    dets, infer_ms = [], None
    for line in p.stdout.splitlines():
        t = line.split()
        if not t:
            continue
        if t[0] == "DET":
            x1, y1, x2, y2, s = map(float, t[1:6])
            dets.append([x1 * sx, y1 * sy, x2 * sx, y2 * sy, s])
        elif t[0] == "INFER_MS":
            infer_ms = float(t[1])
    return dets, infer_ms


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--bin", default="/home/xilinx/ncnn_infer")
    ap.add_argument("--param", default="/home/xilinx/person_v8n25.param")
    ap.add_argument("--model", default="/home/xilinx/person_v8n25.bin")
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--iou", type=float, default=0.45)
    ap.add_argument("--pl", action="store_true")
    args = ap.parse_args()

    dets, ms = detect(args.image, args.bin, args.param, args.model,
                      args.conf, args.iou, use_pl=args.pl)
    im = cv2.imread(args.image)
    size = [im.shape[1], im.shape[0]] if im is not None else [0, 0]
    print(json.dumps({"image": args.image, "size": size, "infer_ms": ms,
                      "n": len(dets),
                      "dets": [{"x1": d[0], "y1": d[1], "x2": d[2], "y2": d[3],
                                "score": round(d[4], 4)} for d in dets]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())