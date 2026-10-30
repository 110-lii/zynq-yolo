#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""board_clock.py —— 把 PC 的时间对齐到板子（含 Zynq PS RTC），走 Jupyter 通道。

为什么需要它：板子没有外网、NTP 用不上，掉电重启后系统时间会退回一个固定值，
状态面板右上角的时钟就会是错的。这里把「PC 的当前时间」推给板子，并尽量写进
PS RTC（如果这块板子的 RTC 真的有 32.768 kHz 晶振和内核驱动）。

用法（主机侧，板子只要 Jupyter 通）：
    py tools/board_clock.py status          # 看板子时间 / RTC 是否存在 / 偏差多少
    py tools/board_clock.py sync            # 用本机时间校准板子系统时间
    py tools/board_clock.py sync --rtc      # 再写进 RTC（掉电保持；需要 sudo）
    py tools/board_clock.py restore         # 重启后按上次同步值恢复（没有 RTC 时的退路）

2026-10-29 实测：这块 PYNQ 镜像**没有可用 RTC** —— /dev/rtc*、/proc/driver/rtc、
设备树 RTC 节点、rtc 内核模块全部没有（Zynq PS RTC 未在设备树里使能，板子多半也没有
32.768 kHz 晶振）。所以「对齐时间」= 每次上电后由 PC 同步一次，并把时间戳落在
/home/xilinx/.zynq-yolo-clock，重启后用 restore 恢复到上次同步值。

令牌：BOARD_CLOCK_STATUS / BOARD_CLOCK_SYNC_OK / BOARD_CLOCK_RTC_* / BOARD_CLOCK_FAIL
"""
import re
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import board_jupyter

STATUS_CODE = r'''
import subprocess
def sh(c):
    return subprocess.run(c, shell=True, capture_output=True, text=True).stdout.strip()
print("BOARD_CLOCK_STATUS epoch=%.0f" % __import__("time").time())
print("  date: %s" % sh("date '+%Y-%m-%d %H:%M:%S %Z'"))
print("  rtc_dev: %s" % sh("ls /dev/rtc* 2>&1"))
print("  rtc_sysfs: %s" % sh("ls /sys/class/rtc 2>&1"))
print("  driver_rtc: %s" % sh("cat /proc/driver/rtc 2>&1 | head -9"))
print("  hwclock: %s" % sh("hwclock -r 2>&1"))
print("  sudo_n: %s" % ("ok" if subprocess.run("sudo -n true", shell=True,
      capture_output=True).returncode == 0 else "needs-password"))
print("  zynq_rtc_drv: %s" % sh("lsmod 2>/dev/null | grep -i rtc"))
print("  dt_rtc_node: %s" % sh("ls -d /proc/device-tree/*rtc* 2>&1 | head -2"))
_hw = sh("ls /dev/rtc* 2>/dev/null")
print("BOARD_CLOCK_RTC_%s" % ("OK dev=%s" % _hw if _hw else
      "UNAVAILABLE 没有 /dev/rtc* 也没有 /proc/driver/rtc（设备树无 RTC 节点）"))
try:
    print("  state_file: %s" % open("/home/xilinx/.zynq-yolo-clock").read().strip())
except Exception as _e:
    print("  state_file: none")
'''

SYNC_CODE = r'''
import subprocess, time
EPOCH = __EPOCH__
RTC = __RTC__
STATE = "/home/xilinx/.zynq-yolo-clock"
def sh(c):
    return subprocess.run(c, shell=True, capture_output=True, text=True).stdout.strip()
def sudo(c):
    r = subprocess.run("sudo -n " + c, shell=True, capture_output=True, text=True)
    if r.returncode == 0:
        return r.stdout.strip(), True
    r = subprocess.run("echo xilinx | sudo -S -p '' " + c, shell=True,
                       capture_output=True, text=True)
    return (r.stdout + r.stderr).strip(), r.returncode == 0
out, ok = sudo("date -s @%d" % EPOCH)
print("BOARD_CLOCK_SET sudo_ok=%d %s" % (ok, out))
print("BOARD_CLOCK_DELTA %.3f s" % (time.time() - EPOCH))
if RTC:
    out, ok = sudo("hwclock -w")
    print("BOARD_CLOCK_RTC_WRITE ok=%d %s" % (ok, out))
    out, ok = sudo("hwclock -r")
    print("BOARD_CLOCK_RTC_READ ok=%d %s" % (ok, out))
# 没有 RTC 设备时的退路：把时间戳落盘，重启后能恢复到「上次同步值」
open(STATE, "w").write("%.0f\n" % time.time())
print("BOARD_CLOCK_STATE_WRITE %s" % STATE)
print("BOARD_CLOCK_SYNC_OK epoch=%.0f rtc=%d" % (time.time(), 1 if RTC else 0))
'''

RESTORE_CODE = r'''
import subprocess, time
STATE = "/home/xilinx/.zynq-yolo-clock"
def sudo(c):
    r = subprocess.run("sudo -n " + c, shell=True, capture_output=True, text=True)
    if r.returncode == 0:
        return r.stdout.strip(), True
    r = subprocess.run("echo xilinx | sudo -S -p '' " + c, shell=True,
                       capture_output=True, text=True)
    return (r.stdout + r.stderr).strip(), r.returncode == 0
try:
    saved = float(open(STATE).read().strip())
except Exception:
    saved = 0.0
now = time.time()
print("BOARD_CLOCK_STATE saved=%.0f now=%.0f lag=%.0f s" % (saved, now, now - saved))
if saved > now + 1.0:
    out, ok = sudo("date -s @%d" % int(saved))
    print("BOARD_CLOCK_RESTORE ok=%d %s" % (ok, out))
    print("BOARD_CLOCK_RESTORE_DONE epoch=%.0f note=按上次同步值恢复，停机期间的时间无法得知" % time.time())
else:
    print("BOARD_CLOCK_RESTORE_SKIP 系统时间不比落盘值旧")
'''


def _status():
    board_jupyter.run(STATUS_CODE)
    return 0


def _sync(rtc):
    epoch = int(time.time())
    print("HOST_TIME %s epoch=%d" % (time.strftime("%Y-%m-%d %H:%M:%S"), epoch))
    code = SYNC_CODE.replace("__EPOCH__", str(epoch)).replace(
        "__RTC__", "True" if rtc else "False")
    board_jupyter.run(code)
    return 0


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if args[0] == "status":
        return _status()
    if args[0] == "sync":
        return _sync("--rtc" in args)
    if args[0] == "restore":
        board_jupyter.run(RESTORE_CODE)
        return 0
    print("BOARD_CLOCK_FAIL unknown_cmd %s" % args[0])
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
