"""board_diag.py —— 板端稳定性诊断（一次跑完，输出紧凑）"""
import subprocess


def sh(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    return (r.stdout or "") + (r.stderr or "")


print("UPTIME_S", open("/proc/uptime").read().split()[0])
print("ISSUE", " ".join(open("/etc/issue").read().split())[:60])
print("KERNEL", sh(["uname", "-r"]).strip())

d = sh(["dmesg"])
keys = ["panic", "oops", "watchdog", "thermal", "mmc0: error", "mmc1: error",
        "ext4-fs error", "remount", "read-only", "i/o error", "under-voltage"]
for k in keys:
    hits = [l for l in d.splitlines() if k in l.lower()]
    print("DMESG %-14s %d" % (k, len(hits)), hits[-1][:100] if hits else "")

# 根文件系统是否只读
for line in open("/proc/mounts"):
    if " / " in line:
        print("ROOTFS", line.strip()[:120])

# 上次启动的结束方式
prev = sh(["journalctl", "-b", "-1", "-n", "6", "--no-pager"])
print("PREV_BOOT_TAIL")
for l in prev.strip().splitlines()[-6:]:
    print("  ", l[:110])

# 上次启动的错误级日志
errs = sh(["journalctl", "-b", "-1", "-p", "err", "--no-pager"])
lines = [l for l in errs.splitlines() if l.strip() and not l.startswith("--")]
print("PREV_ERR", len(lines))
for l in lines[-8:]:
    print("  ", l[:110])

# 启动次数
boots = sh(["journalctl", "--list-boots", "--no-pager"])
print("BOOT_COUNT", max(0, len(boots.strip().splitlines()) - 1))
