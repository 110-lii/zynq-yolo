"""board_preproc_test.py —— 板上验证 preproc：BGR 帧 -> 320x320 RGB 平面

用法（板上）：
    sudo python3 src/ps/host/board_preproc_test.py --image /home/xilinx/f125.jpg
判据：末行 PREPROC_OK 或 PREPROC_FAIL，并打印与 cv2.INTER_NEAREST 的差异。
"""
import argparse
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

sys.dont_write_bytecode = True

import cv2
import numpy as np

from pynq import Overlay, allocate

REPO = Path(__file__).resolve().parents[3]
BIT = REPO / "board" / "overlay" / "preproc.bit"
HWH = REPO / "board" / "overlay" / "preproc.hwh"


def parse_regs(hwh):
    regs = {}
    for ab in ET.parse(str(hwh)).getroot().iter("ADDRESSBLOCK"):
        iface = ab.get("INTERFACE")
        for reg in ab.iter("REGISTER"):
            off = None
            for p in reg.findall("PROPERTY"):
                if p.get("NAME") == "ADDRESS_OFFSET":
                    off = int(p.get("VALUE"), 0)
            if reg.get("NAME") is not None and off is not None:
                regs[(iface, reg.get("NAME"))] = off
    return regs


def find_windows(ovl):
    out = {}
    for key, info in ovl.ip_dict.items():
        if "preproc" not in str(info.get("type", "")).lower():
            continue
        if info.get("mem_id"):
            out[info["mem_id"]] = getattr(ovl, key).mmio
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    args = ap.parse_args()

    img = cv2.imread(args.image)
    if img is None:
        print("PREPROC_FAIL image")
        return 1
    h, w = img.shape[:2]
    print("IMAGE %dx%d" % (w, h))
    bgr = np.ascontiguousarray(img)

    ovl = Overlay(str(BIT))
    wins = find_windows(ovl)
    regs = parse_regs(HWH)
    ctl = wins["s_axi_control"]
    pr = wins["s_axi_control_r"]

    in_buf = allocate(shape=(bgr.size,), dtype=np.uint8)
    out_buf = allocate(shape=(320 * 320 * 3,), dtype=np.uint8)
    in_buf[:] = bgr.reshape(-1)
    out_buf[:] = 0
    in_buf.flush()
    out_buf.flush()

    # HLS 可能把 in/out 命名为 in_r/out_r（与 C++ 关键字/端口名冲突时）
    def base_name(name):
        for cand in (name, name + "_r"):
            if ("s_axi_control_r", cand + "_1") in regs or ("s_axi_control_r", cand) in regs:
                return cand
        raise KeyError(name)

    for name, buf in (("in", in_buf), ("out", out_buf)):
        nm = base_name(name)
        if ("s_axi_control_r", nm + "_1") in regs:
            o1 = regs[("s_axi_control_r", nm + "_1")]
            o2 = regs[("s_axi_control_r", nm + "_2")]
        else:
            o1 = regs[("s_axi_control_r", nm)]
            o2 = o1 + 4
        pa = int(buf.physical_address)
        pr.write(o1, pa & 0xFFFFFFFF)
        pr.write(o2, (pa >> 32) & 0xFFFFFFFF)

    ctl.write(regs[("s_axi_control", "W_IN")], w)
    ctl.write(regs[("s_axi_control", "H_IN")], h)

    t0 = time.perf_counter()
    ctl.write(0x00, 0x01)
    while True:
        if ctl.read(0x00) & 0x2:
            break
        if time.perf_counter() - t0 > 10:
            print("PREPROC_FAIL timeout")
            return 1
    dt = time.perf_counter() - t0

    out_buf.invalidate()
    got = np.array(out_buf).reshape(3, 320, 320)

    ref = cv2.resize(img, (320, 320), interpolation=cv2.INTER_NEAREST)
    rgb = cv2.cvtColor(ref, cv2.COLOR_BGR2RGB)
    exp = np.stack([rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]])
    d = np.abs(got.astype(int) - exp.astype(int))
    print("PREPROC_MS %.2f" % (dt * 1e3))
    print("DIFF mean %.3f max %d equal %.1f%%" % (d.mean(), d.max(), 100 * (d == 0).mean()))
    ok = d.max() <= 1
    print("PREPROC_OK" if ok else "PREPROC_FAIL diff")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())