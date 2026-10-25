"""跑 Vitis HLS 导出 axi_bw，并绕过 2020.2 的 core_revision 日期溢出。

用法：
    python src/hls/bw/scripts/export_bw.py
    python src/hls/bw/scripts/export_bw.py --reuse   # 跳过 HLS，只重新打包

成功判据：`IP_EXPORT_OK axi_bw IP=build/ip_repo/axi_bw`（不看退出码）。
"""
import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True

REPO = Path(__file__).resolve().parents[4]
TCL = REPO / "src" / "hls" / "bw" / "scripts" / "export_bw.tcl"
WORK = REPO / "build" / "hls_bw"
IPREPO = REPO / "build" / "ip_repo"

HLS_BAT = Path(r"D:\Xilinx\Vitis_HLS\2020.2\bin\vitis_hls.bat")
VIVADO_BAT = Path(r"D:\Xilinx\Vivado\2020.2\bin\vivado.bat")

SAFE_REVISION = "20261019"
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
    cands = list(WORK.glob("exp_axi_bw/sol_*/impl/ip"))
    return cands[0] if cands else None


def run(cmd, cwd, log):
    with open(log, "w", encoding="utf-8", newline="\n") as f:
        p = subprocess.run(cmd, cwd=cwd, stdout=f, stderr=subprocess.STDOUT)
    return p.returncode


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse", action="store_true")
    args = ap.parse_args(argv)

    log = REPO / ".research" / "export_axi_bw.txt"
    log.parent.mkdir(parents=True, exist_ok=True)

    if not args.reuse:
        print("跑 HLS：export_bw.tcl（csynth + export_design）")
        run([str(HLS_BAT), "-f", str(TCL)], REPO, log)
        txt = log.read_text(encoding="utf-8", errors="replace")
        if "EXPORT_DONE" in txt:
            for line in txt.splitlines():
                if line.startswith("EXPORT_DONE"):
                    print(line)
            print("IP_EXPORT_OK axi_bw (HLS 原生导出成功)")
            return 0
        if "bad lexical cast" not in txt:
            print("IP_EXPORT_FAIL axi_bw : HLS 导出失败且不是已知的 core_revision 溢出")
            for line in txt.splitlines():
                if line.startswith(("ERROR", "EXPORT_FAIL")):
                    print("   ", line)
            return 1
        print("命中 core_revision 溢出（2026 日期触发），转手动打包")

    ip_dir = find_ip_dir()
    if ip_dir is None:
        print("IP_EXPORT_FAIL axi_bw : 找不到 impl/ip 目录")
        return 1
    changed, old = patch_revision(ip_dir)
    if old is None:
        print("IP_EXPORT_FAIL axi_bw : run_ippack.tcl 里找不到 set Revision")
        return 1
    print("Revision %s -> %s" % (old, SAFE_REVISION if changed else old))

    pack_log = REPO / ".research" / "pack_axi_bw.txt"
    run([str(VIVADO_BAT), "-notrace", "-mode", "batch", "-source", "run_ippack.tcl"],
        ip_dir, pack_log)

    comp = ip_dir / "component.xml"
    if not comp.exists():
        print("IP_EXPORT_FAIL axi_bw : 打包后 component.xml 不存在，见 %s" % pack_log)
        return 1

    dst = IPREPO / "axi_bw"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(ip_dir, dst,
                    ignore=shutil.ignore_patterns(".Xil", "*.jou", "*.log", "*.str"))

    for pat in ("*.jou", "*.log"):
        for p in ip_dir.glob(pat):
            p.unlink()
    xil = ip_dir / ".Xil"
    if xil.is_dir():
        shutil.rmtree(xil)

    print("IP_EXPORT_OK axi_bw IP=%s" % dst.relative_to(REPO))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
