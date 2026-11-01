"""板端诊断 5：细扫接缝 + 稳定性，确认「屏幕列 = 源列 + K (mod 1280)」是不是纯循环平移。

- 逐列打点（源图只有第 j 列 = 1）⇒ pos = 720 × (屏列 + 1)，直接读出映射；
- 重点看接缝附近（源 860..870）和 0..5，确认是 mod 1280 循环还是别的形态；
- 最后连测 20 次，看 K 是否会漂（历史上记成「间歇」，必须量清楚）。
"""
import sys
import time

import numpy as np

sys.path.insert(0, "/home/xilinx/zynq-yolo/tools")

import hdmi_video  # noqa: E402

W, H = 1280, 720
M32 = 0xFFFFFFFF


def main():
    out = hdmi_video.HdmiVideoOut()

    def probe(src_col, settle=0.10):
        img = np.zeros((H, W), dtype=np.uint32)
        img[:, src_col] = 1
        for b in out.bufs:
            b[:] = img
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
        return pos

    def screen_of(j):
        pos = probe(j)
        return (pos // H - 1) if pos % H == 0 else None

    ks = []
    for j in [0, 1, 2, 3, 4, 5, 857, 858, 859, 860, 861, 862, 863, 864, 865, 866, 867, 868, 1275, 1276, 1277, 1278, 1279]:
        s = screen_of(j)
        if s is None:
            print("SEAM src=%4d -> 非整行" % j)
        else:
            k = (s - j) % W
            ks.append(k)
            print("SEAM src=%4d -> screen=%4d  K=%d" % (j, s, k))

    # 稳定性：同一列连测 20 次
    stable = []
    for _ in range(20):
        s = screen_of(512)
        stable.append(s)
        time.sleep(0.05)
    print("STABLE src=512 screens=%s" % sorted(set(stable)))

    # ramp 再看一眼（长等候，排除过渡帧）
    ramp = np.empty((H, W), dtype=np.uint32)
    ramp[:] = np.arange(W, dtype=np.uint32) + np.uint32(1)
    for b in out.bufs:
        b[:] = ramp
        b.flush()
    out.cfg.write(0x8, (W * 4) | (W << 16) | (1 << 27) | (7 << 29))
    time.sleep(2.0)
    print("RAMP pos=0x%08x sum=0x%08x" % (int(out.st.read(0x8)) & M32,
                                          int(out.st.read(0x0)) & M32))
    out.cfg.write(0x8, out.pack)
    print("DIAG_DONE Ks=%s" % sorted(set(ks)))


if __name__ == "__main__":
    main()
