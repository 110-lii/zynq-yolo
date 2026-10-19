"""并行跑 conv_engine 的 C 仿真（csim），逐个用例与 numpy 参考实现逐位比对。

为什么单独一个脚本而不是塞进 run_engine.py：
    csynth 回答"资源/时序够不够"，csim 回答"算得对不对"。两者的判据、失败模式、
    耗时都不同（csynth 3–6 分钟/配置，csim 1–2 分钟/用例且要解析 TB 输出）。
    混在一起会让"资源通过了但结果错了"这种情况看不出来。

判据（三层，缺一不可）：
    1. Tcl 层：日志里出现 `CSIM_DONE <tag> OK`（csim_design 没抛错）
    2. TB 层：日志里出现 `TB_CONV_OK <case> <n>`（逐位比对全等）
    3. 汇总层：全部用例 OK 才算过，末行 `CSIM_ALL_OK <n>` / `CSIM_HAS_FAIL`
★ 第 2 层不能省。csim_design 成功只说明"跑完了"，TB 返回非 0 也可能只被当成普通输出。

用法：
    python src/hls/accel/scripts/run_csim.py
    python src/hls/accel/scripts/run_csim.py --cases k3s1_c3_40 k1s1_tailcout
    python src/hls/accel/scripts/run_csim.py --par 16 --max-jobs 4
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
TCL = Path(__file__).resolve().parent / "csim_engine.tcl"
CASES = ROOT / "data" / "layers" / "cases"
LOGD = ROOT / "build" / "hls_csim" / "logs"
PARD = ROOT / "build" / "hls_csim" / "params"
OUTJ = ROOT / "data" / "results" / "hls_csim.json"
HLS_BAT = Path(r"D:\Xilinx\Vitis_HLS\2020.2\bin\vitis_hls.bat")

DONE = re.compile(r"CSIM_(DONE|FAIL)\s+(\S+)\s*(.*)")
TBOK = re.compile(r"TB_CONV_OK\s+(\S+)\s+(\d+)")
TBFAIL = re.compile(r"TB_CONV_FAIL\s+(\S+)\s*:?\s*(.*)")


def parse_log(p: Path):
    if not p.is_file():
        return {"status": "NOLOG", "detail": str(p)}
    txt = p.read_text(encoding="utf-8", errors="replace")
    tb_ok = TBOK.search(txt)
    tb_bad = TBFAIL.search(txt)
    done = DONE.findall(txt)
    if tb_ok:
        return {"status": "OK", "checked": int(tb_ok.group(2))}
    if tb_bad:
        return {"status": "MISMATCH", "detail": tb_bad.group(2).strip()[:300]}
    if done and done[-1][0] == "FAIL":
        return {"status": "CSIMFAIL", "detail": done[-1][2].strip()[:300]}
    # 既没 TB 结论也没 Tcl 结论 —— 多半是编译错误
    errs = [ln for ln in txt.splitlines() if "ERROR" in ln or "error:" in ln]
    return {"status": "NO_VERDICT", "detail": " | ".join(errs[:3])[:300]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hls", default=str(HLS_BAT))
    ap.add_argument("--par", type=int, default=8)
    ap.add_argument("--tile-h", type=int, default=4)
    ap.add_argument("--tile-w", type=int, default=40)
    ap.add_argument("--cases", nargs="*", default=None)
    ap.add_argument("--max-jobs", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=3600)
    a = ap.parse_args(argv)

    all_cases = sorted([d.name for d in CASES.iterdir() if d.is_dir()])
    use = a.cases if a.cases else all_cases
    missing = [c for c in use if not (CASES / c).is_dir()]
    if missing:
        print(f"CASES_MISSING {missing}（先跑 sim/scripts/make_hls_case.py --all）")
        return 1

    LOGD.mkdir(parents=True, exist_ok=True)
    PARD.mkdir(parents=True, exist_ok=True)
    OUTJ.parent.mkdir(parents=True, exist_ok=True)

    env0 = os.environ.copy()
    env0.pop("PYTHONDONTWRITEBYTECODE", None)

    running = []      # 只用于控制并发：已完成就移出
    started = []      # 全部已启动的项：等待与解析用这个
    results = {}
    print(f"PAR_OUT={a.par} TILE_H={a.tile_h} TILE_W={a.tile_w}  cases={len(use)}")

    for case in use:
        tag = f"{case}_p{a.par}"
        pfile = PARD / f"params_{tag}.tcl"
        # 显式 LF：Windows 上 write_text 默认 CRLF，会与 .gitattributes 的 eol=lf 冲突
        pfile.write_text(
            f"set cfg_tag    {tag}\n"
            f"set cfg_par    {a.par}\n"
            f"set cfg_tile_h {a.tile_h}\n"
            f"set cfg_tile_w {a.tile_w}\n"
            f"set cfg_case   {{{(CASES / case).as_posix()}}}\n",
            encoding="ascii", newline="\n")

        while len(running) >= a.max_jobs:
            time.sleep(2)
            # ★ 只从 running 里移除，绝不丢弃 —— 第一版这里直接把项 pop 掉，
            #   导致超出并发数的用例既没被等待也没被解析，汇总表里静默少几行
            #   （6 个用例只报了 3 个，且返回码看起来"正常失败"）。
            for item in list(running):
                if item[1].poll() is not None:
                    running.remove(item)

        logp = LOGD / f"csim_{tag}.log"
        env = env0.copy()
        env["HLS_ENGINE_PARAMS"] = str(pfile)
        logf = open(logp, "w", encoding="utf-8", newline="\n")
        print(f"START {tag}")
        p = subprocess.Popen([str(a.hls), "-f", str(TCL)],
                             cwd=str(ROOT), env=env,
                             stdout=logf, stderr=subprocess.STDOUT)
        item = (tag, p, logp, logf)
        running.append(item)
        started.append(item)

    deadline = time.time() + a.timeout
    for (tag, p, logp, logf) in started:
        try:
            p.wait(timeout=max(30, deadline - time.time()))
        except subprocess.TimeoutExpired:
            p.kill()
        logf.close()
        results[tag] = parse_log(logp)

    print()
    print("=" * 78)
    for tag, r in results.items():
        if r["status"] == "OK":
            print(f"{tag:<26} OK       checked={r['checked']}")
        else:
            print(f"{tag:<26} {r['status']:<10} {r.get('detail', '')[:56]}")
    print("=" * 78)

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "tool": "Vitis HLS 2020.2 csim",
        "part": "xc7z020clg400-2",
        "par_out": a.par, "tile_h": a.tile_h, "tile_w": a.tile_w,
        "cases_dir": "data/layers/cases",
        "note": "out_ref 由 sim/scripts/make_hls_case.py 独立实现契约 §1/§3 生成",
        "results": results,
    }
    OUTJ.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8", newline="\n")
    print(f"WROTE {OUTJ}")

    nok = sum(1 for r in results.values() if r["status"] == "OK")
    print(f"CSIM_ALL_OK {nok}" if nok == len(results) else "CSIM_HAS_FAIL")
    return 0 if nok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
