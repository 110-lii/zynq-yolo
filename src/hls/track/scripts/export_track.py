#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导出 track_preproc IP（HLS csynth + 打包，处理 2020.2 core_revision 溢出）。

用法：python src/hls/track/scripts/export_track.py
判据：末行 IP_EXPORT_OK track_preproc
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True

REPO = Path(__file__).resolve().parents[4]
TCL = REPO / "src" / "hls" / "track" / "scripts" / "export_track.tcl"
WORK = REPO / "build" / "hls_track"
IPREPO = REPO / "build" / "ip_repo"
HLS_BAT = Path(r"D:\Xilinx\Vitis_HLS\2020.2\bin\vitis_hls.bat")
VIVADO_BAT = Path(r"D:\Xilinx\Vivado\2020.2\bin\vivado.bat")
SAFE_REVISION = "20261025"
REV_RE = re.compile(r'^(\s*set\s+Revision\s+)"(\d+)"', re.M)


def patch_revision(ip_dir):
    tcl = ip_dir / "run_ippack.tcl"
    txt = tcl.read_text(encoding="utf-8", errors="replace")
    m = REV_RE.search(txt)
    if not m:
        return False, None
    old = m.group(2)
    if int(old) <= 2147483647:
        return False, old
    txt = REV_RE.sub(lambda mm: '%s"%s"' % (mm.group(1), SAFE_REVISION), txt, count=1)
    tcl.write_text(txt, encoding="utf-8", newline="\n")
    return True, old


def find_ip_dir():
    cands = list(WORK.glob("exp_track/sol_*/impl/ip"))
    return cands[0] if cands else None


def run(cmd, cwd, log):
    with open(log, "w", encoding="utf-8", newline="\n") as f:
        subprocess.run(cmd, cwd=cwd, stdout=f, stderr=subprocess.STDOUT)


def main():
    log = REPO / ".research" / "export_track.txt"
    log.parent.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    print("HLS: csynth + export_design ...")
    run([str(HLS_BAT), "-f", str(TCL)], REPO, log)
    txt = log.read_text(encoding="utf-8", errors="replace")
    if "EXPORT_DONE" in txt:
        for line in txt.splitlines():
            if line.startswith("EXPORT_DONE"):
                print(line)
        print("IP_EXPORT_OK track_preproc")
        return 0
    if "bad lexical cast" not in txt:
        print("IP_EXPORT_FAIL track_preproc: HLS 失败且不是 core_revision 溢出")
        for line in txt.splitlines():
            if line.startswith(("ERROR", "EXPORT_FAIL")):
                print("   ", line)
        return 1
    ip_dir = find_ip_dir()
    if ip_dir is None:
        print("IP_EXPORT_FAIL track_preproc: 找不到 impl/ip")
        return 1
    changed, old = patch_revision(ip_dir)
    print("Revision %s -> %s" % (old, SAFE_REVISION if changed else old))
    run([str(VIVADO_BAT), "-notrace", "-mode", "batch", "-source", "run_ippack.tcl"],
        ip_dir, REPO / ".research" / "pack_track.txt")
    comp = ip_dir / "component.xml"
    if not comp.exists():
        print("IP_EXPORT_FAIL track_preproc: component.xml 不存在")
        return 1
    dst = IPREPO / "track_preproc"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(ip_dir, dst, ignore=shutil.ignore_patterns(".Xil", "*.jou", "*.log", "*.str"))
    print("IP_EXPORT_OK track_preproc IP=%s" % dst.relative_to(REPO))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
