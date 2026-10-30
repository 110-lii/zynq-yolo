"""lcd_probe_host.py -- 主机侧（Windows）驱动：出片 → 上传 → 板端跑探针。

用法（在仓库根目录）：
    py tools/lcd_probe_host.py build
    py tools/lcd_probe_host.py deploy
    py tools/lcd_probe_host.py run --size 800x480 --pattern 0
    py tools/lcd_probe_host.py all

说明：Vivado 只能在主机跑；板端执行走 tools/board_jupyter.py 的 Jupyter 通道。
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIVADO = Path(r"D:\Xilinx\Vivado\2020.2\bin\vivado.bat")
TCL = ROOT / "build" / "scripts" / "bd_lcd_probe.tcl"
LOG = ROOT / "build" / "vivado" / "logs" / "lcd_probe_build.log"
JRN = ROOT / "build" / "vivado" / "logs" / "lcd_probe_build.jou"
BIT = ROOT / "board" / "overlay" / "lcd_probe.bit"
HWH = ROOT / "board" / "overlay" / "lcd_probe.hwh"
BOARD = "192.168.2.99:9090"
REMOTE_ROOT = "/home/xilinx/zynq-yolo"

# deploy 要传的（远端路径与仓库内路径一致，板端脚本就按这个布局找 bit）
DEPLOY = [
    (BIT, REMOTE_ROOT + "/board/overlay/lcd_probe.bit"),
    (HWH, REMOTE_ROOT + "/board/overlay/lcd_probe.hwh"),
    (ROOT / "tools" / "lcd_probe.py", REMOTE_ROOT + "/tools/lcd_probe.py"),
    (ROOT / "board" / "fb" / "tag4.bin", REMOTE_ROOT + "/board/fb/tag4.bin"),
]


def sh(cmd):
    print("+", " ".join(str(c) for c in cmd))
    return subprocess.call([str(c) for c in cmd], cwd=str(ROOT))


def do_build(_):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    rc = sh([VIVADO, "-mode", "batch", "-source", TCL, "-log", LOG, "-journal", JRN])
    if rc != 0:
        print("LCD_PROBE_HOST_FAIL vivado rc=%d" % rc)
        return rc
    if not BIT.exists():
        print("LCD_PROBE_HOST_FAIL no_bit %s" % BIT)
        return 1
    print("LCD_PROBE_HOST_BUILD_OK %s" % BIT)
    return 0


def _jput(local, remote):
    """板端 Jupyter 的 contents 根是 /home/xilinx/jupyter_notebooks，不能直接写绝对路径。
    先传到根目录下的临时名，再用内核把文件 move 到真正的目标路径。"""
    local = Path(local)
    tmp = local.name + ".tmp"
    rc = sh([sys.executable, ROOT / "tools" / "board_jupyter.py", "--put", local, tmp])
    if rc != 0:
        return rc
    code = (
        "import shutil, os\n"
        "src = '/home/xilinx/jupyter_notebooks/{tmp}'\n"
        "os.makedirs(os.path.dirname('{remote}'), exist_ok=True)\n"
        "shutil.move(src, '{remote}')\n"
        "print('MOVED {remote} bytes=%d' % os.path.getsize('{remote}'))\n"
    ).format(tmp=tmp, remote=remote)
    return _jrun(code)


def _jrun(code):
    p = subprocess.run([sys.executable, str(ROOT / "tools" / "board_jupyter.py")],
                       input=code, text=True, cwd=str(ROOT))
    return p.returncode


def do_deploy(_):
    rc = 0
    for local, remote in DEPLOY:
        if not Path(local).is_file():
            print("LCD_PROBE_HOST_FAIL missing %s" % local)
            rc |= 1
            continue
        rc |= _jput(local, remote)
    print("LCD_PROBE_HOST_DEPLOY_%s" % ("OK" if rc == 0 else "FAIL"))
    return rc


def do_run(args):
    argv = ["python3", "tools/lcd_probe.py", "--size", args.size,
            "--pattern", str(args.pattern)]
    if args.fb_bin:
        argv += ["--fb-bin", args.fb_bin]
    if args.phase:
        argv += ["--phase", str(args.phase)]
    if args.hold:
        argv += ["--hold", "%.1f" % args.hold]
    # 走 Jupyter 内核时不能给 shell 串：内核按 Python/魔法解析，
    # "cd X && python3 ..." 会被 cd 魔法当成目录名（Errno 2）。一律用 subprocess。
    code = (
        "import subprocess\n"
        "p = subprocess.run(%r, cwd=%r, capture_output=True, text=True)\n"
        "print(p.stdout[-4000:])\n"
        "print('STDERR>', p.stderr[-1500:])\n"
        "print('RC', p.returncode)\n"
    ) % (argv, REMOTE_ROOT)
    print("BOARD>", " ".join(argv))
    return _jrun(code)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build").set_defaults(fn=do_build)
    sub.add_parser("deploy").set_defaults(fn=do_deploy)
    r = sub.add_parser("run")
    r.add_argument("--size", choices=["800x480", "480x272"], default="800x480")
    r.add_argument("--pattern", type=int, default=0)
    r.add_argument("--phase", type=int, default=0)
    r.add_argument("--fb-bin", default="")
    r.add_argument("--hold", type=float, default=0.0)
    r.set_defaults(fn=do_run)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
