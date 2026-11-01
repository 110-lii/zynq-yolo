"""板端诊断 7：用 HdmiVideoOut 自己的标定接口量 K + 补正后复测，逐点打印。

与诊断 5/6 的区别：这里走的是**产品代码里的同一段函数**（calibrate_shift /
verify_shift），所以量出来的就是管线里会用的那个 K。
"""
import sys

sys.path.insert(0, "/home/xilinx/zynq-yolo/tools")

import hdmi_video  # noqa: E402

W = 1280


def main():
    out = hdmi_video.HdmiVideoOut()
    cols = (0, 320, 640, 960, 1279)
    for j in cols:
        scr = out._probe_screen_col(j)
        print("PROBE src=%4d -> screen=%s  K=%s"
              % (j, scr, None if scr is None else (scr - j) % W))
    k = out.calibrate_shift()
    print("CALIB shift=%d" % k)
    for j in cols:
        scr = out._probe_screen_col(j)
        print("AFTER src=%4d -> screen=%s" % (j, scr))
    print("VERIFY", out.verify_shift())
    print("DIAG_DONE")


if __name__ == "__main__":
    main()
