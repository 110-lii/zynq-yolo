"""本地校验 board_detect.detect 的 letterbox 正/逆变换（不需要板子，不跑模型）。

做法：把 subprocess.run 换成假的，返回写死的 DET 行，
      同时截获真正写到临时文件里的 320x320x3 RGB 缓存。
检查点：
  1) 1280x720 -> 320：缩放 0.25、上下各补 40 行 114 灰边
  2) 框逆变换：网络坐标 -> 原图坐标 与手算一致
  3) 左右两侧的 114 补边确实存在（抽外框像素）
  4) squash 旧路径仍是 1280/320 的拉伸比（对照）
"""
import os
import subprocess
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import board_detect  # noqa: E402

img = os.path.join(tempfile.mkdtemp(), "f.png")
import cv2  # noqa: E402

cv2.imwrite(img, np.full((720, 1280, 3), 200, np.uint8))

captured = {"raw": None}
real_run = subprocess.run


class Fake:
    returncode = 0
    # 网络坐标里一个框：贴在画面顶部正中，故意落在补边区下方
    stdout = "DET 130.0 90.0 190.0 150.0 0.900\nINFER_MS 12.3\n"


def fake_run(cmd, **kw):
    with open(cmd[3], "rb") as fh:
        captured["raw"] = np.frombuffer(fh.read(), np.uint8).copy()
    return Fake()


subprocess.run = fake_run
try:
    dets, ms = board_detect.detect(img, "x", "y", "z", 0.25, 0.45, size=320)
finally:
    subprocess.run = real_run

raw = captured["raw"].reshape(320, 320, 3)
fails = []


def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + ((" " + extra) if extra else ""))
    if not ok:
        fails.append(name)


check("缓存尺寸 320x320x3", raw.shape == (320, 320, 3), str(raw.shape))
# 1280x720 -> 320 高 180，上下各 (320-180)//2 = 70 行补边
check("顶部补边 70 行且为 114", np.all(raw[:70] == 114), "rows0-69")
check("底部补边 70 行且为 114", np.all(raw[250:] == 114), "rows250-319")
check("中间是画面（非 114）", np.all(raw[70:250] == 200))

# 逆变换：scale = 320/1280 = 0.25；pad_y = 70
# x' = (130-0)/0.25 = 520 ; y' = (90-70)/0.25 = 80
x1, y1, x2, y2, s = dets[0]
check("框逆变换", abs(x1 - 520) < 1e-6 and abs(y1 - 80) < 1e-6
      and abs(x2 - 760) < 1e-6 and abs(y2 - 320) < 1e-6,
      "got %.1f %.1f %.1f %.1f" % (x1, y1, x2, y2))
check("INFER_MS 透传", ms == 12.3, str(ms))

# 对照：squash 旧路径
subprocess.run = fake_run
try:
    dets2, _ = board_detect.detect(img, "x", "y", "z", 0.25, 0.45, size=320, letterbox=False)
finally:
    subprocess.run = real_run
raw2 = captured["raw"].reshape(320, 320, 3)
check("squash 无补边", np.all(raw2 == 200))
check("squash 逆变换 x=520", abs(dets2[0][0] - 520) < 1e-6, "%.1f" % dets2[0][0])
check("squash 逆变换 y=202.5", abs(dets2[0][1] - 202.5) < 1e-6, "%.1f" % dets2[0][1])

print("LETTERBOX_CHECK " + ("OK" if not fails else "FAIL " + ",".join(fails)))
sys.exit(0 if not fails else 1)
