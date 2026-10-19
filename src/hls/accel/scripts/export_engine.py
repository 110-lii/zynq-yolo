"""编排 Vitis HLS 的 IP 导出，并绕过 2020.2 在 2026 年必然触发的 core_revision 溢出。

为什么需要这个脚本（不是多此一举）
------------------------------------
`export_engine.tcl` 里的 `export_design -format ip_catalog` **在本机会必然失败**，
实测逐字报错：

    bad lexical cast: source type value could not be interpreted as target
        while executing
    "rdi::set_property core_revision 2610192155 {component component_1}"
        invoked from within
    "set_property core_revision $Revision $core"
        (file "run_ippack.tcl" line 2174)
    ERROR: [IMPL 213-28] Failed to generate IP.

根因：HLS 生成的 `run_ippack.tcl:57` 把 `Revision` 设成 **YYMMDDHHMM** 拼成的十进制数
（`set Revision "2610192155"` = 26-10-19 21:55）。这个值 **大于 2^31-1 = 2147483647**，
Vivado 的 IP packer 用 int32 接它 ⇒ 解析失败。2020.2 发布时不可能预见到 2026 年的
日期会撑爆这个字段 —— 这是**日期触发的工具链 bug**，不是我们的设计问题，也不是
可以靠改 HLS 代码绕开的。

对策：让 `export_design` 跑到失败（此时 `<ip 目录>` 已经铺好了 hdl/ bd/ drivers/ 等
全部内容，只差最后一步打包），然后
  1) 把 `run_ippack.tcl` 里的 `set Revision "<大数>"` 改成一个小整数；
  2) 在该目录下直接跑 Vivado 打包（等价于 Xilinx 自己生成的 `pack.bat`：
     `vivado -notrace -mode batch -source run_ippack.tcl`）；
  3) 校验 `component.xml` 真的生成了 —— **判据是文件在不在，不是退出码**。
最后把打好的 IP 复制到 `build/ip_repo/<name>/` 这个稳定路径，供 Vivado 工程引用。

用法
----
    python src/hls/accel/scripts/export_engine.py --tag p8_1_40
    python src/hls/accel/scripts/export_engine.py --tag p8_1_40 --par 8 --tile-h 1 --tile-w 40
    python src/hls/accel/scripts/export_engine.py --tag p8_1_40 --reuse   # 跳过 HLS，直接打包
"""
import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True

REPO = Path(__file__).resolve().parents[4]
SCRIPTS = REPO / "src" / "hls" / "accel" / "scripts"
WORK = REPO / "build" / "hls_export"
IPREPO = REPO / "build" / "ip_repo"

HLS_BAT = Path(r"D:\Xilinx\Vitis_HLS\2020.2\bin\vitis_hls.bat")
VIVADO_BAT = Path(r"D:\Xilinx\Vivado\2020.2\bin\vivado.bat")

# core_revision 只能是小整数；用固定值而不是日期，避免下一次导出又被日期撑爆。
SAFE_REVISION = "20261019"

REV_RE = re.compile(r'^(\s*set\s+Revision\s+)"(\d+)"', re.M)


def run(cmd, cwd=None, log=None, timeout=None):
    """跑外部命令，stdout+stderr 合并写日志文件。**不用管道**（本仓库既有教训）。"""
    with open(log, "w", encoding="utf-8", newline="\n") as f:
        p = subprocess.run(cmd, cwd=cwd, stdout=f, stderr=subprocess.STDOUT,
                           timeout=timeout)
    return p.returncode


def write_params(tag, par, th, tw):
    d = WORK / "params"
    d.mkdir(parents=True, exist_ok=True)
    p = d / ("params_%s.tcl" % tag)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write("set cfg_tag    {%s}\n" % tag)
        f.write("set cfg_par    %d\n" % par)
        f.write("set cfg_tile_h %d\n" % th)
        f.write("set cfg_tile_w %d\n" % tw)
    return p


def patch_revision(ip_dir):
    """把 run_ippack.tcl 里超 int32 的 Revision 换成小整数。返回 (是否改过, 原值)。"""
    tcl = ip_dir / "run_ippack.tcl"
    txt = tcl.read_text(encoding="utf-8", errors="replace")
    m = REV_RE.search(txt)
    if not m:
        return False, None
    old = m.group(2)
    if int(old) <= 2147483647:
        return False, old          # 没溢出，别乱动
    txt = REV_RE.sub(lambda mm: '%s"%s"' % (mm.group(1), SAFE_REVISION), txt, count=1)
    tcl.write_text(txt, encoding="utf-8", newline="\n")
    return True, old


def find_ip_dir(tag):
    cands = list((WORK / ("exp_" + tag)).glob("sol_*/impl/ip"))
    return cands[0] if cands else None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="p8_1_40")
    ap.add_argument("--par", type=int, default=8)
    ap.add_argument("--tile-h", type=int, default=1)
    ap.add_argument("--tile-w", type=int, default=40)
    ap.add_argument("--reuse", action="store_true",
                    help="跳过 HLS 综合/导出，直接用已有 ip 目录打包")
    args = ap.parse_args(argv)
    tag = args.tag
    log = REPO / ".research" / ("export_%s.txt" % tag)
    log.parent.mkdir(parents=True, exist_ok=True)

    if not args.reuse:
        params = write_params(tag, args.par, args.tile_h, args.tile_w)
        print("参数文件 %s -> tag=%s par=%d tile=%dx%d"
              % (params.relative_to(REPO), tag, args.par, args.tile_h, args.tile_w))
        env = {"HLS_EXPORT_PARAMS": str(params)}
        import os
        e = dict(os.environ)
        e.update(env)
        print("跑 HLS：export_engine.tcl（csynth + export_design，预期 export 会失败）")
        with open(log, "w", encoding="utf-8", newline="\n") as f:
            subprocess.run([str(HLS_BAT), "-f", str(SCRIPTS / "export_engine.tcl")],
                           cwd=str(REPO), stdout=f, stderr=subprocess.STDOUT, env=e)
        txt = log.read_text(encoding="utf-8", errors="replace")
        if "EXPORT_DONE" in txt:
            for line in txt.splitlines():
                if line.startswith("EXPORT_DONE"):
                    print(line)
                    print("IP_EXPORT_OK %s (HLS 原生导出成功，未触发绕过)" % tag)
                    return 0
        if "bad lexical cast" not in txt:
            print("IP_EXPORT_FAIL %s : HLS 导出失败，且不是已知的 core_revision 溢出" % tag)
            for line in txt.splitlines():
                if line.startswith("ERROR"):
                    print("   ", line)
            return 1
        print("命中已知的 core_revision 溢出（2026 年日期触发），转手动打包")

    ip_dir = find_ip_dir(tag)
    if ip_dir is None:
        print("IP_EXPORT_FAIL %s : 找不到 impl/ip 目录" % tag)
        return 1
    changed, old = patch_revision(ip_dir)
    if old is None:
        print("IP_EXPORT_FAIL %s : run_ippack.tcl 里找不到 set Revision" % tag)
        return 1
    print("Revision %s -> %s（%s）"
          % (old, SAFE_REVISION if changed else old,
             "已改，因超出 int32" if changed else "未超 int32，保持原值"))

    pack_log = REPO / ".research" / ("pack_%s.txt" % tag)
    print("跑 Vivado 打包：%s" % (ip_dir / "run_ippack.tcl").relative_to(REPO))
    run([str(VIVADO_BAT), "-notrace", "-mode", "batch", "-source", "run_ippack.tcl"],
        cwd=str(ip_dir), log=pack_log)

    # ★ 判据是 component.xml 在不在，不是退出码
    comp = ip_dir / "component.xml"
    if not comp.exists():
        print("IP_EXPORT_FAIL %s : 打包后 %s 不存在" % (tag, comp.relative_to(REPO)))
        print("     详情见 %s" % pack_log.relative_to(REPO))
        return 1

    dst = IPREPO / ("conv_engine_" + tag)
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(ip_dir, dst,
                    ignore=shutil.ignore_patterns(".Xil", "*.jou", "*.log", "*.str"))

    # ★ `tools/check_repo.py` 的检查 d) 是**扫盘**的（不看 git），而 Vivado 会在 ip 目录里
    #   留下 `vivado.jou` / `vivado.log` / `.Xil/`。它们已被 .gitignore 挡住，但扫盘仍会
    #   判 FAIL —— 与 `__pycache__` 完全同一个坑（见 ai/tools/README.md）。所以打包成功后
    #   当场清掉，而不是靠 .gitignore 或事后手删（后者必复发）。
    n_clean = 0
    for pat in ("*.jou", "*.log"):
        for p in ip_dir.glob(pat):
            p.unlink()
            n_clean += 1
    for name in (".Xil",):
        p = ip_dir / name
        if p.is_dir():
            shutil.rmtree(p)
            n_clean += 1
    if n_clean:
        print("清理 Vivado 残留 %d 项（否则 check d 扫盘会 FAIL）" % n_clean)

    ok = (dst / "component.xml").exists()
    print("component.xml %s，复制到 %s" % ("OK" if ok else "缺失", dst.relative_to(REPO)))
    if not ok:
        print("IP_EXPORT_FAIL %s : 复制后 component.xml 丢失" % tag)
        return 1
    print("IP_EXPORT_OK %s IP=%s" % (tag, dst.relative_to(REPO)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
