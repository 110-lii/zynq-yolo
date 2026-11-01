"""板端诊断 4：**逐列打点**，直接量出「源第 j 列被送到屏幕第几列」。

原理（见 hdmi_video_top.v 的位置加权和）：
    源图只让第 j 列取值为 1、其余全 0 ⇒ 每行只有那一列贡献 (屏幕列号)×1，
    720 行累加 ⇒ pos = 720 × (屏幕列号)，即 屏幕列 = pos/720 − 1。
    这个量对「顺序」敏感、与整帧累加和不同，不需要看屏、也不需要猜模型。

扫一遍源列即可得到完整的一维映射；若屏幕列号是单调的，就是纯平移/缩放，
若来回跳，就是取数/落点的问题。
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

    def probe(src_col, settle=0.12):
        img = np.zeros((H, W), dtype=np.uint32)
        img[:, src_col] = 1
        for b in out.bufs:
            b[:] = img
            b.flush()
        out.cfg.write(0x0, out.bufs[0].physical_address)
        pack = (W * 4) | (W << 16) | (1 << 27) | (7 << 29)
        out.cfg.write(0x8, pack)
        time.sleep(settle)
        pos = int(out.st.read(0x8)) & M32
        # 多次读，取最后两次一致的值（避免刚好读到跨帧的一半）
        for _ in range(6):
            time.sleep(0.05)
            v = int(out.st.read(0x8)) & M32
            if v == pos:
                break
            pos = v
        out.cfg.write(0x8, out.pack)
        return pos

    results = {}
    for j in list(range(0, W, 64)) + [511, 512, 513, 1023, 1279]:
        pos = probe(j)
        if pos % H == 0:
            scr = pos // H - 1
            results[j] = scr
            print("MAP src=%4d -> screen=%4d" % (j, scr))
        else:
            print("MAP src=%4d -> pos=0x%08x (非整行，说明该列在多处出现)" % (j, pos))

    print("DIAG_DONE n=%d" % len(results))


if __name__ == "__main__":
    main()
