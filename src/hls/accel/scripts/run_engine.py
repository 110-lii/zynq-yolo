"""并行跑多个 conv_engine 实现参数组合的 Vitis HLS csynth，汇总资源/时序。

为什么用 Python 而不是 PowerShell：`$args` 是 PowerShell 自动变量不可赋值，
首版 run_probe.ps1 直接语法错误且报错行号错位（详见 run_probe.py 文件头）。

为什么并行：本机 16 物理核 / 24 逻辑核；csynth 主要是单线程，N 个配置串行 = N×4 分钟，
并行 = max(各配置) + 争用。

判据：
    总末行 `ENGINE_ALL_DONE`
    每配置 `CSYNTH_DONE <tag> ...` 或 `CSYNTH_FAIL <tag> : ...`
    汇总表落 `data/results/hls_engine.json`（进 git：它是报告里资源数字的来源）

用法：
    python src/hls/accel/scripts/run_engine.py
    python src/hls/accel/scripts/run_engine.py --configs p8_4_40,p8,4,40 p16_2_40,p16,2,40
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
TCL = Path(__file__).resolve().parent / "csynth_engine.tcl"
LOGD = ROOT / "build" / "hls_engine" / "logs"
PARD = ROOT / "build" / "hls_engine" / "params"
OUTJ = ROOT / "data" / "results" / "hls_engine.json"
HLS_BAT = Path(r"D:\Xilinx\Vitis_HLS\2020.2\bin\vitis_hls.bat")

# 默认组合：以"真实 YOLOv8n 最重的 3x3 层"为参照（C_IN=64/C_OUT=128 之类已不再是编译期参数，
# 只有实现参数影响资源）。先只扫 PAR_OUT，TILE 固定 4/40（与探针 v11 同几何，便于对比）。
DEFAULT_CONFIGS = [
    "p4_4_40,4,4,40",
    "p8_4_40,8,4,40",
    "p16_4_40,16,4,40",
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
        return {"status": "FAIL", "tag": tag, "detail": rest.strip()[:400]}
    g = dict(re.findall(r"(\w+)=([^\s]+)", rest))
    return {
        "status": "OK",
        "tag": tag,
        "dsp": g.get("DSP"),
        "bram18": g.get("BRAM18"),
        "lut": g.get("LUT"),
        "ff": g.get("FF"),
        "ii": g.get("II"),
        "lat": g.get("Lat"),
        "fmax_mhz": g.get("FmaxMHz"),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hls", default=str(HLS_BAT))
    ap.add_argument("--max-jobs", type=int, default=4)
    ap.add_argument("--configs", nargs="*", default=None,
                    help="每项 tag,par,tile_h,tile_w")
    ap.add_argument("--timeout", type=int, default=5400)
    a = ap.parse_args(argv)

    cfg_specs = a.configs if a.configs else DEFAULT_CONFIGS
    LOGD.mkdir(parents=True, exist_ok=True)
    PARD.mkdir(parents=True, exist_ok=True)
    OUTJ.parent.mkdir(parents=True, exist_ok=True)

    env0 = os.environ.copy()
    env0.pop("PYTHONDONTWRITEBYTECODE", None)

    running = []  # [(tag, Popen, logpath)]
    results = {}
    t_start = time.time()

    for spec in cfg_specs:
        parts = spec.split(",")
        if len(parts) not in (4, 5):
            print(f"CONFIG_BAD {spec}")
            continue
        tag, par, th, tw = parts[0], parts[1], parts[2], parts[3]
        # 可选第 5 字段：ACC_FULL（0=只按 PAR_OUT 分区，1=累加器全展开落寄存器）
        accfull = parts[4] if len(parts) == 5 else "0"
        pfile = PARD / f"params_{tag}.tcl"
        # 显式 LF：Windows 上 write_text 默认 CRLF，会与 .gitattributes 的 eol=lf 冲突
        pfile.write_text(
            f"set cfg_tag    {tag}\n"
            f"set cfg_par    {par}\n"
            f"set cfg_tile_h {th}\n"
            f"set cfg_tile_w {tw}\n"
            f"set cfg_acc_full {accfull}\n",
            encoding="ascii", newline="\n")

        while len(running) >= a.max_jobs:
            time.sleep(2)
            for item in list(running):
                if item[1].poll() is not None:
                    running.remove(item)

        logp = LOGD / f"csynth_{tag}.log"
        env = env0.copy()
        env["HLS_ENGINE_PARAMS"] = str(pfile)
        logf = open(logp, "w", encoding="utf-8", newline="\n")
        print(f"START {tag} (PAR_OUT={par} TILE_H={th} TILE_W={tw} ACC_FULL={accfull}) -> {logp.name}")
        p = subprocess.Popen([str(a.hls), "-f", str(TCL)],
                             cwd=str(ROOT), env=env,
                             stdout=logf, stderr=subprocess.STDOUT)
        running.append((tag, p, logp, logf))

    deadline = t_start + a.timeout
    for item in list(running):
        tag, p, logp, logf = item
        try:
            p.wait(timeout=max(30, deadline - time.time()))
        except subprocess.TimeoutExpired:
            p.kill()
            print(f"TIMEOUT {tag}")
        logf.close()
        results[tag] = parse_log(logp) or {"status": "NOLOG", "tag": tag}

    print()
    print("=" * 78)
    print(f"{'tag':<12}{'status':<8}{'DSP':>6}{'BRAM18':>8}{'LUT':>8}{'FF':>9}{'II':>5}{'Fmax':>7}")
    print("-" * 78)
    for tag, r in results.items():
        if r["status"] == "OK":
            print(f"{tag:<12}{'OK':<8}{r['dsp']:>6}{r['bram18']:>8}{r['lut']:>8}"
                  f"{r['ff']:>9}{r['ii']:>5}{str(r['fmax_mhz']):>7}")
        else:
            print(f"{tag:<12}{r['status']:<8}  {r.get('detail','')[:60]}")
    print("=" * 78)

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "tool": "Vitis HLS 2020.2",
        "part": "xc7z020clg400-2",
        "clock_ns": 10.0,
        "source": "src/hls/accel/src/conv_engine.cpp",
        "note": "实现参数扫描；几何为运行期参数，故不含 C_IN/C_OUT/H/W",
        "results": results,
    }
    OUTJ.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8", newline="\n")
    print(f"WROTE {OUTJ}")

    ok = all(r["status"] == "OK" for r in results.values()) and results
    print("ENGINE_ALL_DONE" if ok else "ENGINE_HAS_FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
