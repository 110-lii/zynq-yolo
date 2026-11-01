"""板端诊断 6：**验证补正**——把源图预旋转 414 列再写进帧缓存，看映射是否变成恒等。

补正公式（由诊断 4/5 实测：屏幕列 = 源列 + 414 (mod 1280)）：
    buf[i] = src[(i + 414) mod 1280]   ≡  np.roll(src, -414, axis=1)
这样屏幕第 C 列取 buffer 的第 (C-414) 列 = 源第 C 列。
判据：打点后每个源列 j 都应落在屏幕列 j（K=0），且连测稳定。
"""
import sys
import time

import numpy as np

sys.path.insert(0, "/home/xilinx/zynq-yolo/tools")

import hdmi_video  # noqa: E402

W, H = 1280, 720
M32 = 0xFFFFFFFF
K = 414


def main():
    out = hdmi_video.HdmiVideoOut()

    def probe(src_col, roll, settle=0.12):
        img = np.zeros((H, W), dtype=np.uint32)
        img[:, src_col] = 1
        buf = np.roll(img, -roll, axis=1) if roll else img
        for b in out.bufs:
            b[:] = buf
            b.flush()
        out.cfg.write(0x0, out.bufs[0].physical_address)
        out.cfg.write(0x8, (W * 4) | (W << 16) | (1 << 27) | (7 << 29))
        time.sleep(settle)
        pos = int(out.st.read(0x8)) & M32
        for _ in range(6):
            time.sleep(0.04)
            v = int(out.st.read(0x8)) & M32
            if v == pos:
                break
            pos = v
        out.cfg.write(0x8, out.pack)
        return (pos // H - 1) if pos % H == 0 else None

    for j in (0, 1, 100, 512, 700, 866, 1000, 1279):
        s0 = probe(j, 0)
        s1 = probe(j, K)
        print("FIX src=%4d 未补正->%s  补正后->%s" % (j, s0, s1))

    ok = all(probe(j, K) == j for j in (0, 333, 777, 1279))
    print("FIX_VERDICT", "IDENTITY" if ok else "STILL_OFF")
    print("DIAG_DONE")


if __name__ == "__main__":
    main()
