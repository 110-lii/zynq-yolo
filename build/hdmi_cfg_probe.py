#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hdmi_cfg_probe.py —— 0x41200000 到底是不是那个 HDMI 配置 GPIO？

2026-10-30 20:52 的 fb 诊断给出两条互相矛盾的证据：
    · DDR 里的像素是对的（绕过缓存 `/dev/mem` 回读 16/16 全对）⇒ 不是 cache/flush 的事；
    · 可 `gpio_hdmi_cfg`（0x41200000）读回来**不是写进去的值**：`pack` 恒为 0，
      `addr` 每次读都不一样、且两轮差值完全相同（0x1840000/6 s）。
      一个「写进去读不回来、自己还在涨」的寄存器，最可能是**读到的是别的东西**。
所以这个探针只做一件事：写已知图案、立刻读回，并与两个已知好用的 GPIO 对照
（0x41220000 = LCD cfg，0x41280000 = LCD 帧时钟计数）。
如果 0x41200000 连 0xDEADBEEF 都读不回来，而对照组正常 ⇒ 问题在地址/读通路，
不在「缓存没刷」，`Buffer.flush()` 这条归因可以彻底作废。

用法：py build/hdmi_cfg_probe.py --wait 5400
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import run_board_acceptance as r  # noqa: E402

LOG = ROOT / "build" / "hdmi_cfg_probe.log"

CODE = r'''
import time
import numpy as np
from pynq import Overlay, MMIO
ov = Overlay('/home/xilinx/zynq-yolo/board/overlay/flow_system.bit')
print('IPLIST', sorted((getattr(ov, 'ip_dict', None) or {}).keys()))
for nm in ('gpio_hdmi_cfg', 'gpio_hdmi_st'):
    d = (ov.ip_dict or {}).get(nm) or {}
    print('IPMAP %s phys=0x%08X type=%s' % (nm, int(d.get('phys_addr') or 0), d.get('type')))

def dump(tag, base):
    m = MMIO(base, 0x10000)
    print('%s BEFORE r0=0x%08X r4=0x%08X r8=0x%08X rC=0x%08X' % (
        tag, *[int(m.read(k)) & 0xFFFFFFFF for k in (0, 4, 8, 12)]))
    m.write(0, 0xDEADBEEF)
    m.write(8, 0x0D001400)
    print('%s AFTER  r0=0x%08X r4=0x%08X r8=0x%08X rC=0x%08X' % (
        tag, *[int(m.read(k)) & 0xFFFFFFFF for k in (0, 4, 8, 12)]))

dump('HDMI_CFG 0x41200000', 0x41200000)
dump('CTRL_LCDCFG 0x41220000', 0x41220000)
dump('CTRL_LCDT 0x41280000', 0x41280000)

# 假设「cfg/st 两个 GPIO 的地址被对调了」：在 0x41210000 上按 cfg 的布局写一次，
# 再按 status 去读 0x41200000。若画面出来 ⇒ 假设成立，改 hdmi_video.py 的默认地址即可。
from pynq import allocate
import sys
sys.path.insert(0, '/home/xilinx/zynq-yolo/tools')
from hdmi_video import pack_frame, W, H
buf = allocate(shape=(H, W), dtype=np.uint32)
img = np.zeros((H, W, 3), np.uint8); img[:, :] = (0, 0, 255)     # 整屏红
np.copyto(buf, pack_frame(img))
if callable(getattr(buf, 'flush', None)):
    buf.flush()

for tag, base in (('PRIMARY 0x41200000', 0x41200000), ('SWAPPED 0x41210000', 0x41210000)):
    m = MMIO(base, 0x10000)
    m.write(8, 1280 * 4 | (1280 << 16) | (1 << 27))
    m.write(0, int(buf.physical_address))
    t0 = time.time()
    while time.time() - t0 < 8:
        time.sleep(0.5)
    print('TRY %s addr=0x%08X pack=0x%08X buf=0x%08X'
          % (tag, int(MMIO(base, 0x10000).read(0)) & 0xFFFFFFFF,
             int(MMIO(base, 0x10000).read(8)) & 0xFFFFFFFF, int(buf.physical_address)))
print('TRIAL_DONE 屏上若出现整屏红，说明 cfg 实际在 0x41210000')
print('DONE')
'''


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def main():
    want = float(sys.argv[sys.argv.index("--wait") + 1]) if "--wait" in sys.argv else 5400
    t0 = time.time()
    for attempt in range(400):
        if time.time() - t0 > want:
            break
        if not r.board_alive(timeout=25):
            time.sleep(20)
            continue
        log("BOARD_ONLINE 第 %d 次" % (attempt + 1))
        rc, out = r.run_board(["python3", "-c", CODE], timeout=600)
        for ln in out:
            log("  " + ln)
        if any(ln.startswith("DONE") for ln in out):
            log("完成")
            return 0
        time.sleep(20)
    log("没跑成")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
