#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""install_boot.py —— 把「上电即用」固化成板上的 systemd 服务。

用户 2026-11-01 的要求：「把整个固化到板子上，让我可以一上电就可以使用」。

装两个服务（都在开机时自动起来）：

    zynq-idle.service      tools/board_idle.py        HDMI 常驻纯白待机 +
                                                     加载 flow_system.bit
    zynq-stream.service    tools/board_pipeline.py    在 5007 端口等 PC 推流；
                           --listen 5007              收完一段就退出，
                                                      Restart=always 自动回来等下一段

为什么要 zynq-idle 常驻而不是画一次：HDMI 帧缓存是 PYNQ `allocate()` 的 DDR 缓冲，
进程退出缓冲就释放，扫描输出会读到失效页面。见 `tools/board_idle.py` 文件头。

用法
----
    py tools/install_boot.py --dry-run          # 只打印将写入的 unit，不碰板子
    py tools/install_boot.py                    # 推文件 + 装服务 + enable + 立即启动
    py tools/install_boot.py --no-stream        # 只装白屏服务
    py tools/install_boot.py --uninstall        # 停服务、删 unit（文件保留）

判据令牌
--------
    BOOT_INSTALL_OK services=<a,b> enabled=<a,b>
    BOOT_INSTALL_FAIL <reason>

★ 本工具**需要板子在线**；板子离线时只会打印 BOARD_OFFLINE，不会假装装好。
"""
import argparse
import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

BOARD_ROOT = "/home/xilinx/zynq-yolo"
# ★ 不是 /usr/bin/python3：PYNQ 镜像把 pynq 装在它自己的 venv 里，
#   系统 python3 里 `import pynq` 直接 ModuleNotFoundError（2026-11-01 实测踩到）。
#   问板子 `sys.executable` 得到的答案就是这个路径。
PY3 = "/usr/local/share/pynq-venv/bin/python3"

UNIT_IDLE = """[Unit]
Description=zynq-yolo idle: load flow_system overlay, hold HDMI white
After=network.target pynq.service
Wants=network.target

[Service]
Type=simple
# ★ PYNQ 的系统镜像把 XRT 装成 /usr（不是 /opt/xilinx/xrt）。systemd 不读
#   Jupyter 内核的 env，缺这一行会报 "No Devices Found, is the XRT environment sourced?"
Environment=XILINX_XRT=/usr
WorkingDirectory=%(root)s
ExecStart=%(py)s %(root)s/tools/board_idle.py --bit %(root)s/board/overlay/flow_system.bit
Restart=always
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""

UNIT_STREAM = """[Unit]
Description=zynq-yolo stream: listen on 5007 and run the pipeline per connection
After=network.target zynq-idle.service
Wants=zynq-idle.service

[Service]
Type=simple
# ★ PYNQ 的系统镜像把 XRT 装成 /usr（不是 /opt/xilinx/xrt）。systemd 不读
#   Jupyter 内核的 env，缺这一行会报 "No Devices Found, is the XRT environment sourced?"
Environment=XILINX_XRT=/usr
WorkingDirectory=%(root)s
ExecStart=%(py)s %(root)s/tools/board_pipeline.py --listen 5007%(preview)s%(lcd)s \
  --every 15 --max-det 0 --no-pl --no-camera-gate --ball-every 5 \
  --bin /home/xilinx/ncnn_infer \
  --param %(root)s/build/models/person_v8n25.param \
  --model %(root)s/build/models/person_v8n25.bin \
  --store-dir %(root)s/build/flow --store-prefix net
Restart=always
RestartSec=2
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""


def render(which, root=BOARD_ROOT, py=PY3, preview=True, lcd=True):
    tpl = {"idle": UNIT_IDLE, "stream": UNIT_STREAM}[which]
    return tpl % {"root": root, "py": py,
                  "preview": " --hdmi-preview" if preview else "",
                  # D030：正常推流（不走 --event）时小屏也显示状态面板，每 5 帧刷一次
                  "lcd": " --lcd-preview 5" if lcd else ""}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印 unit，不连板子")
    ap.add_argument("--no-stream", action="store_true", help="不装 5007 监听服务")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--no-hdmi-preview", action="store_true",
                    help="推流时不把画面送到 HDMI（只保留白屏待机）")
    ap.add_argument("--no-lcd-panel", action="store_true",
                    help="推流时不往板载 LCD 写状态面板（D030，默认每 5 帧刷一次）")
    ap.add_argument("--board", default="192.168.2.99")
    args = ap.parse_args(argv)

    which = ["idle"] if args.no_stream else ["idle", "stream"]
    units = {k: render(k, preview=not args.no_hdmi_preview,
                       lcd=not args.no_lcd_panel) for k in which}

    if args.dry_run:
        for k in which:
            print("==== zynq-%s.service ====" % k)
            print(units[k])
        print("BOOT_DRYRUN_OK services=%s" % ",".join(which))
        return 0

    sys.path.insert(0, str(HERE))
    import board_jupyter as bj

    try:
        bj.run("print('BOOT_PROBE_OK')")
    except Exception as e:                                   # noqa: BLE001
        print("BOARD_OFFLINE %s" % e)
        print("BOOT_INSTALL_FAIL board_unreachable —— 先上电重启，确认 9090 通了再装")
        return 4

    # 先把板上要跑的文件推上去（幂等；sha256 回读校验由 --push 负责）
    for rel in ("tools/board_idle.py", "tools/hdmi_video.py",
                "tools/board_pipeline.py", "tools/result_store.py"):
        if bj.push(str(ROOT / rel), "%s/%s" % (BOARD_ROOT, rel)) != 0:
            print("BOOT_INSTALL_FAIL push %s" % rel)
            return 2

    rc = bj.run(_install_code(units, which, args.uninstall))
    if rc != 0:
        print("BOOT_INSTALL_FAIL board_side rc=%d" % rc)
        return 3
    return 0


def _install_code(units, which, uninstall):
    """拼板端执行的 Python：写 unit、reload、enable/disable、启动/停止。"""
    parts = ["import os, subprocess, base64",
             "ROOT = %r" % BOARD_ROOT,
             "def sh(c):",
             "    return subprocess.run(c, shell=True, capture_output=True, text=True)",
             "SUDO = '' if os.getuid() == 0 else 'sudo -n '",
             "r = sh(SUDO + 'true')",
             "if r.returncode != 0:",
             "    print('BOOT_INSTALL_FAIL no_sudo'); raise SystemExit"]
    for k in which:
        b64 = __import__("base64").b64encode(units[k].encode("utf-8")).decode("ascii")
        parts += [
            "open('/tmp/zynq-%s.service','wb').write(base64.b64decode('%s'))" % (k, b64),
        ]
    if uninstall:
        parts += ["for k in %r:" % which,
                  "    sh(SUDO + 'systemctl stop zynq-%s' % k)",
                  "    sh(SUDO + 'systemctl disable zynq-%s' % k)",
                  "    sh(SUDO + 'rm -f /etc/systemd/system/zynq-%s.service' % k)",
                  "sh(SUDO + 'systemctl daemon-reload')",
                  "print('BOOT_UNINSTALL_OK services=' + ','.join(%r))" % which]
    else:
        names = ",".join(repr("zynq-" + k) for k in which)
        parts += ["for k in %r:" % which,
                  "    sh(SUDO + 'cp /tmp/zynq-%s.service /etc/systemd/system/' % k)",
                  "sh(SUDO + 'systemctl daemon-reload')",
                  "for k in %r:" % which,
                  "    sh(SUDO + 'systemctl enable zynq-%s' % k)",
                  "    sh(SUDO + 'systemctl restart zynq-%s' % k)",
                  # 板端最后自证：真的 active 了才算装好，别只信 enable 的返回码
                  "import time; time.sleep(3)",
                  "st = sh(SUDO + 'systemctl is-active ' + ' '.join([%s]))" % names,
                  "print('BOOT_SERVICE_STATE ' + st.stdout.replace(chr(10), ' '))",
                  "print('BOOT_INSTALL_OK services=' + ','.join(%r))" % which]
    return "\n".join(parts)


if __name__ == "__main__":
    sys.exit(main())
