"""并行跑多个 Vitis HLS csynth 配置，汇总资源/时序。

为什么用 Python 而不是 PowerShell 编排：
    首版写成 `run_probe.ps1`，在里面用了 `$args` —— 那是 **PowerShell 的自动变量，不允许赋值**，
    脚本直接语法错误（`unexpected token '}'`，报错行号还是错位的，很难查）。
    Python 的 `subprocess` 语义明确、无需绕开自动变量，且本仓库其余工具都是 Python ⇒ 统一。

为什么并行：本机 16 物理核 / 24 逻辑核，而 `vitis_hls` 的 csynth 主要是单线程。
    N 个配置串行 = N × (3~6.5 分钟)；并行 = max(各配置) + 争用开销。
    首版串行跑 4 个配置花了 20 分钟，且中途改代码导致前面全白跑。

每个配置独立进程、独立工程目录、独立日志 ⇒ 任一失败不影响其它。

判据：
    末行 `PROBE_ALL_DONE`；每个配置在日志里出现 `CSYNTH_DONE <tag> ...` 或 `CSYNTH_FAIL <tag> : ...`。
    汇总表落 `data/results/hls_probe.json`（进 git：它是报告里资源数字的来源）。

用法：
    python src/hls/accel/scripts/run_probe.py                 # 用缺省配置
    python src/hls/accel/scripts/run_probe.py --max-jobs 6
    python src/hls/accel/scripts/run_probe.py --configs p4,4,64,128,40,40 p8,8,64,128,40,40
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[4]
TCL = Path(__file__).resolve().parent / "csynth_one.tcl"
LOGD = ROOT / "build" / "hls_probe" / "logs"
PROBE = ROOT / "build" / "hls_probe" / "params"
HLS_BAT = Path(r"D:\Xilinx\Vitis_HLS\2020.2\bin\vitis_hls.bat")

# 缺省配置：以 YOLOv8n 的真实 3x3 层为参考（C_IN/C_OUT 取自模型的 conv 层）
#   model.0  :   3 -> 16   @160（320 输入时）
#   model.8  :  64 -> 128  @40
#   model.22 : 128 -> 128  @20（检测头）
# 固定一个代表性尺寸，只扫并行度 PAR_OUT —— 目的是回答"XC7Z020 上能开多大"。
DEFAULT_CONFIGS = [
    "p2,2,64,128,40,40",
    "p4,4,64,128,40,40",
    "p8,8,64,128,40,40",
    "p16,16,64,128,40,40",
]

LINE = re.compile(r"CSYNTH_(DONE|FAIL)\s+(\S+)\s*(.*)")


def parse_log(p: Path):
    if not p.is_file():
        return None
    txt = p.read_text(encoding="utf-8", errors="replace")
    hits = LINE.findall(txt)
    if not hits:
        return None
    status, tag, rest = hits[-1]
    if status == "FAIL":
        return {"status": "FAIL", "tag": tag, "detail": rest.strip()[:200]}
    g = dict(re.findall(r"(\w+)=([^\s]+)", rest))
    return {
        "status": "OK", "tag": tag,
        "dsp": g.get("DSP"), "bram18": g.get("BRAM18"),
        "lut": g.get("LUT"), "ff": g.get("FF"),
        "ii": g.get("II"), "lat": g.get("Lat"), "fmax_mhz": g.get("FmaxMHz"),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="并行 HLS csynth 探针")
    ap.add_argument("--configs", nargs="*", default=None,
                    help='每项 "tag,PAR_OUT,C_IN,C_OUT,H,W"')
    ap.add_argument("--max-jobs", type=int, default=4,
                    help="并发上限（默认 4；每个 HLS 实例约 1–2 GB 内存）")
    ap.add_argument("--hls", default=str(HLS_BAT))
    args = ap.parse_args(argv)

    configs = args.configs or DEFAULT_CONFIGS
    hls = Path(args.hls)
    if not hls.is_file():
        print("找不到 vitis_hls：%s" % hls, file=sys.stderr)
        return 2
    LOGD.mkdir(parents=True, exist_ok=True)

    print("并行 HLS csynth 探针")
    print("  配置数 = %d   并发上限 = %d" % (len(configs), args.max_jobs))
    print("  日志目录 = %s" % LOGD)
    print("")

    running = []   # [(tag, Popen, logpath)]
    done = []

    for cfg in configs:
        parts = cfg.split(",")
        if len(parts) != 6:
            print("  跳过非法配置（应为 6 段）：%s" % cfg, file=sys.stderr)
            continue
        tag = parts[0]
        log = LOGD / ("%s.log" % tag)

        # 并发闸门
        while len(running) >= args.max_jobs:
            time.sleep(5)
            still = []
            for t, p, lp in running:
                if p.poll() is None:
                    still.append((t, p, lp))
                else:
                    done.append((t, lp))
                    print("  完成 %s（退出码 %s）" % (t, p.returncode))
            running = still
            _dump(done, configs, LOGD)

        print("  启动 %-6s (%s)" % (tag, "/".join(parts[1:])))
        # ★ 参数传递：**不能用 `-tclargs`**（两个坑都实测过）：
        #   `-f S -tclargs ...` ⇒ Tcl 收到 tag="-f"；
        #   `-tclargs ... -f S` ⇒ hls::main 把 "p2" 当脚本名去 source，报 "can't be opened"。
        #   ⇒ 改为把参数写成一个 tcl 片段，用环境变量指过去，脚本自己 source。
        pfile = PROBE / ("params_%s.tcl" % tag)
        pfile.parent.mkdir(parents=True, exist_ok=True)
        pfile.write_text(
            "set cfg_tag  %s\n"
            "set cfg_par  %s\n"
            "set cfg_cin  %s\n"
            "set cfg_cout %s\n"
            "set cfg_h    %s\n"
            "set cfg_w    %s\n" % (parts[0], parts[1], parts[2], parts[3], parts[4], parts[5]),
            encoding="utf-8", newline="\n")

        env = dict(os.environ)
        env["HLS_PROBE_PARAMS"] = str(pfile)

        fh = open(log, "w", encoding="utf-8", errors="replace")
        p = subprocess.Popen([str(hls), "-f", str(TCL)],
                             stdout=fh, stderr=subprocess.STDOUT,
                             cwd=str(ROOT), env=env)
        running.append((tag, p, log))

    print("")
    print("等待剩余 %d 个…" % len(running))
    for t, p, lp in running:
        p.wait()
        done.append((t, lp))
        print("  完成 %s（退出码 %s）" % (t, p.returncode))

    rows = _dump(done, configs, LOGD)

    # ---- 汇总表 ----
    print("")
    print("=" * 84)
    print("== HLS 并行探针汇总（xc7z020clg400-2，10ns 时钟；上限 LUT 53200 / FF 106400 / DSP 220 / BRAM36 140）")
    print("=" * 84)
    print("%-6s %-8s %-10s %-10s %-12s %-10s %-8s %s"
          % ("tag", "PAR_OUT", "DSP", "BRAM18", "LUT", "FF", "II", "FmaxMHz"))
    print("-" * 84)
    for r in rows:
        cfg = next((c for c in configs if c.split(",")[0] == r["tag"]), "")
        par = cfg.split(",")[1] if cfg else "-"
        if r["status"] == "OK":
            print("%-6s %-8s %-10s %-10s %-12s %-10s %-8s %s"
                  % (r["tag"], par, r["dsp"], r["bram18"], r["lut"], r["ff"],
                     r["ii"], r["fmax_mhz"]))
        else:
            print("%-6s %-8s FAIL  %s" % (r["tag"], par, r.get("detail", "")))
    print("")
    print("PROBE_ALL_DONE")
    return 0


def _dump(done, configs, logd: Path):
    """把已完成配置的结果写成 json（每完成一个就刷新一次，避免中断丢结果）。"""
    rows = []
    for cfg in configs:
        tag = cfg.split(",")[0]
        log = logd / ("%s.log" % tag)
        r = parse_log(log)
        if r:
            rows.append(r)
        elif any(t == tag for t, _ in done):
            rows.append({"status": "FAIL", "tag": tag, "detail": "日志里没有 CSYNTH_* 标记"})
    out = ROOT / "data" / "results" / "hls_probe.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"part": "xc7z020clg400-2", "clock_ns": 10.0,
                            "results": rows}, indent=2, ensure_ascii=False))
    return rows


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    sys.exit(main())
