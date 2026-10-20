"""RTL 仿真回归入口：一条命令编译全部 testbench，并给出可判定的结论。

用法
----
    & D:\\python\\python.exe sim\\scripts\\run_sim.py
    & D:\\python\\python.exe sim\\scripts\\run_sim.py --list
    & D:\\python\\python.exe sim\\scripts\\run_sim.py --only vid_dvp_capture
    & D:\\python\\python.exe sim\\scripts\\run_sim.py --trace          # 打开 TB 的逐拍打印

判据令牌
--------
    SIM_PASS <passed>/<total> cases
    SIM_FAIL <reason>

设计
----
* `CASES` 是唯一真源：每条给出 testbench、依赖的 RTL 源、**期望令牌**。
* 期望令牌 = TB 自己打印的字符串（如 `VID_DVP_CAPTURE_TB_PASS`）。脚本只 grep
  令牌，不解析波形，也**不靠退出码**（`docs/repo_spec.md` §5：命令成功与否看令牌）。
* 工具选择 `auto`：先 PATH 里的 `iverilog`，再本机固定的 `D:\\iverilog\\bin\\iverilog.exe`
  （快速回归用）；都没有就退到 Vivado 2020.2 的 `xvlog`/`xelab`/`xsim`。
  官方工具链仍是 Vivado xsim，iverilog 只是本机的快检工具——RTL 按 Verilog-2001
  可综合子集写，两边的差异只会出现在 TB 的不可综合写法上。
* 产物（vvp/日志/波形）只落 `build/sim/`，不进 git（`docs/repo_spec.md` §4）。
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import os
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "build" / "sim"

#: (用例名, TB 相对路径, [RTL 源相对路径...], TB 自报的成功令牌, xsim 顶层名)
CASES = [
    (
        "vid_dvp_capture",
        "sim/tb/video/vid_dvp_capture_tb.v",
        ["src/rtl/video/vid_dvp_capture.v"],
        "VID_DVP_CAPTURE_TB_PASS",
        "vid_dvp_capture_tb",
    ),
]


def find_iverilog() -> list[str] | None:
    """返回 [iverilog, vvp] 的绝对路径；找不到返回 None。"""
    exe = shutil.which("iverilog")
    if exe:
        vvp = shutil.which("vvp")
        if vvp:
            return [exe, vvp]
    fixed = Path(r"D:\iverilog\bin\iverilog.exe")
    if fixed.is_file() and (fixed.parent / "vvp.exe").is_file():
        return [str(fixed), str(fixed.parent / "vvp.exe")]
    return None


def find_xsim() -> list[str] | None:
    """返回 [xvlog, xelab, xsim] 的绝对路径；找不到返回 None。"""
    names = ["xvlog", "xelab", "xsim"]
    paths = [shutil.which(n) for n in names]
    if all(paths):
        return paths  # type: ignore[return-value]
    return None


def run(cmd: list[str], cwd: Path) -> tuple[int, str]:
    p = subprocess.run(
        cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    return p.returncode, p.stdout


def tail(text: str, n: int = 25) -> str:
    lines = text.strip().splitlines()
    return "\n".join(lines[-n:])


def run_case_iverilog(
    tools: list[str], tb: Path, srcs: list[Path], token: str, trace: bool
) -> tuple[bool, str]:
    out = BUILD / (tb.stem + ".vvp")
    cmd = [tools[0], "-g2012", "-o", str(out)]
    cmd += [str(p.relative_to(ROOT)) for p in srcs]
    cmd += [str(tb.relative_to(ROOT))]
    code, text = run(cmd, ROOT)
    if code != 0:
        return False, "compile failed\n" + tail(text)
    args = [str(out)] + (["+trace"] if trace else [])
    code, text = run([tools[1]] + args, ROOT)
    log = BUILD / (tb.stem + ".log")
    log.write_text(text, encoding="utf-8")
    ok = token in text
    if not ok:
        text = "no token " + token + "\n" + tail(text)
    return ok, text


def run_case_xsim(
    tools: list[str], tb: Path, srcs: list[Path], token: str
) -> tuple[bool, str]:
    xvlog, xelab, xsim = tools
    code, text = run([xvlog, "--nolog"] + [str(p) for p in srcs] + [str(tb)], ROOT)
    if code != 0:
        return False, "xvlog failed\n" + tail(text)
    top = tb.stem
    code, text = run([xelab, "--nolog", "-debug", "typical", top, "-s", "simsnap"], ROOT)
    if code != 0:
        return False, "xelab failed\n" + tail(text)
    code, text = run([xsim, "simsnap", "--nolog", "--runall"], ROOT)
    ok = token in text
    if not ok:
        text = "no token " + token + "\n" + tail(text)
    return ok, text


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="RTL 仿真回归（令牌判定，不看退出码）")
    ap.add_argument("--only", help="只跑名字包含该子串的用例")
    ap.add_argument("--list", action="store_true", help="列出用例后退出")
    ap.add_argument("--tool", choices=["auto", "iverilog", "xsim"], default="auto")
    ap.add_argument("--trace", action="store_true", help="给 vvp 传 +trace")
    args = ap.parse_args(argv)

    cases = [c for c in CASES if not args.only or args.only in c[0]]
    if args.list:
        for name, tb, srcs, token, _ in cases:
            print("%-24s %s  token=%s" % (name, tb, token))
        return 0
    if not cases:
        print("SIM_FAIL no_case_matched --only=%s" % args.only)
        return 2

    if args.tool in ("auto", "iverilog"):
        iv = find_iverilog()
    else:
        iv = None
    if iv:
        engine, tools = "iverilog", iv
    else:
        xs = find_xsim()
        if not xs:
            print("SIM_FAIL no_simulator（没找到 iverilog，也没找到 Vivado xsim）")
            return 2
        engine, tools = "xsim", xs

    BUILD.mkdir(parents=True, exist_ok=True)
    print("engine=%s  cases=%d  build=%s" % (engine, len(cases), BUILD))
    passed = 0
    failed: list[str] = []
    for name, tb_rel, src_rels, token, top in cases:
        tb = ROOT / tb_rel
        srcs = [ROOT / s for s in src_rels]
        missing = [str(p) for p in [tb, *srcs] if not p.is_file()]
        if missing:
            print("FAIL %-24s 缺文件: %s" % (name, ", ".join(missing)))
            failed.append(name)
            continue
        if engine == "iverilog":
            ok, text = run_case_iverilog(tools, tb, srcs, token, args.trace)
        else:
            ok, text = run_case_xsim(tools, tb, srcs, token)
        if ok:
            passed += 1
            n_fail = 0
            for line in text.splitlines():
                if re.search(r"\bFAIL\b", line):
                    n_fail += 1
            print("PASS %-24s %s（令牌 %s）" % (name, tb_rel, token))
            if n_fail:
                print("     ⚠ 日志里还有 %d 行 FAIL，但令牌在——请人工确认" % n_fail)
        else:
            failed.append(name)
            print("FAIL %-24s %s" % (name, tb_rel))
            print(text)

    total = len(cases)
    if failed:
        print("SIM_FAIL %d/%d 失败: %s" % (len(failed), total, ", ".join(failed)))
        return 1
    print("SIM_PASS %d/%d cases" % (passed, total))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
