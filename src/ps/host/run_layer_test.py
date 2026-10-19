#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_layer_test.py -- 板上单层验收测试（conv_engine 真板跑 + 逐字节比对）。

**这个脚本只能在板子上跑**（领航者 ZYNQ 7020 / PYNQ 2.7）。PC 上没有 /dev/mem、
没有 pynq，加载 Overlay 就失败；那种情况下本脚本会打
`BOARD_LAYER_FAIL not_on_board` 并退出，**不会**退化成"PC 上模拟一遍"。

它回答三个问题（缺一不可，见 docs/accel_interface.md 与任务判据）：
  1. AP_DONE 真的置起来了吗？      -> 超时给 `ap_done_timeout`，绝不当成功
  2. 输出和 PC 金标准一样吗？      -> 与 out_ref.bin 逐字节比对，报 bad/total
                                     与首个不等的 (c,h,w) 及两边取值
  3. 实测多快？                    -> perf_counter 量 ap_start->ap_done，
                                     除以 MAC 数得 GOPS（本仓库唯一实测数）

用法（板上，仓库根目录）：
    sudo python3 src/ps/host/run_layer_test.py                # 全部用例
    sudo python3 src/ps/host/run_layer_test.py --case k3s1_c3_40
    sudo python3 src/ps/host/run_layer_test.py --list
    sudo python3 src/ps/host/run_layer_test.py --repeat 5 --json /tmp/board.json

输出纪律：stdout 只打 ASCII 令牌（板子控制台是 GBK，中文/箭头会
UnicodeEncodeError）；详细中文说明写进 README 与 JSON 的 ASCII 字段。
所有写文件显式 newline="\n"（.gitattributes eol=lf）、encoding="utf-8"。

末行令牌：
    BOARD_LAYER_OK <case> <elapsed_ns> <gops>      单用例全通过
    BOARD_LAYER_OK all_<n>_cases <elapsed_ns> <gops>   全部用例通过（聚合）
    BOARD_LAYER_FAIL <reason>                       任何一环失败
"""

import sys

# 必须在任何本地 import 之前：禁止板上生成 __pycache__
# （tools/check_repo.py 的 check d) 会扫 __pycache__ 判 FAIL）。
sys.dont_write_bytecode = True

import argparse
import json
import os
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
CASES_DIR = os.path.join(REPO_ROOT, "data", "layers", "cases")
DRIVER_DIR = os.path.join(REPO_ROOT, "src", "ps", "pynq_driver")
DEFAULT_BITFILE = os.path.join(REPO_ROOT, "board", "overlay", "conv_engine.bit")
DEFAULT_JSON = os.path.join(REPO_ROOT, "data", "results", "board_layer_test.json")

# 末行令牌/退出码约定：0 = 全通过；2 = 失败（令牌给出原因）
EXIT_OK = 0
EXIT_FAIL = 2


# ------------------------------------------------------------------ 小工具


def _now_utc():
    """UTC 时间戳字符串（ASCII，不用本地时区，便于和日志对齐）。"""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _write_json(path, obj):
    """写 JSON（显式 \n、UTF-8、ASCII 转义，保证板上任何控制台都能落盘）。"""
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, indent=2, sort_keys=True, ensure_ascii=True)
        fh.write("\n")
    return path


def _list_cases(cases_dir):
    """列出 cases_dir 下所有含 meta.txt 的子目录名（ASCII 排序）。"""
    if not os.path.isdir(cases_dir):
        return []
    names = []
    for name in sorted(os.listdir(cases_dir)):
        if os.path.isfile(os.path.join(cases_dir, name, "meta.txt")):
            names.append(name)
    return names


def _read_ref(case_dir, meta):
    """读 out_ref.bin（PC 金标准），长度必须 = C_OUT*H_OUT*W_OUT。"""
    shape = (int(meta["C_OUT"]), int(meta["H_OUT"]), int(meta["W_OUT"]))
    count = shape[0] * shape[1] * shape[2]
    path = os.path.join(case_dir, "out_ref.bin")
    actual = os.path.getsize(path)
    if actual != count:
        raise ValueError(
            "out_ref.bin size %d != C_OUT*H_OUT*W_OUT %d (%r)" % (
                actual, count, shape))
    ref = np.fromfile(path, dtype=np.uint8)
    if ref.size != count:
        raise ValueError("read %d bytes from %s, expected %d" % (
            ref.size, path, count))
    return ref.reshape(shape)


def _compare(got, ref):
    """逐字节比对，返回 (bad, total, first) —— first 为 None 或 (c,h,w,a,b)。"""
    if got.shape != ref.shape:
        raise ValueError("shape mismatch: got %r ref %r" % (got.shape, ref.shape))
    flat_got = got.reshape(-1)
    flat_ref = ref.reshape(-1)
    diff = np.flatnonzero(flat_got != flat_ref)
    total = int(flat_ref.size)
    bad = int(diff.size)
    first = None
    if bad:
        idx = int(diff[0])
        c, h, w = np.unravel_index(idx, got.shape)
        first = (int(c), int(h), int(w), int(flat_got[idx]), int(flat_ref[idx]))
    return bad, total, first


def _bad_token(result):
    """把失败原因压成一个 ASCII 令牌片段（无空格）。"""
    reason = str(result.get("reason", "unknown"))
    return "".join(ch if (ch.isalnum() or ch in "._-") else "_" for ch in reason)


# ------------------------------------------------------------------ 主流程


def _load_driver(driver_dir):
    """import conv_engine（板上的本地模块）。失败时转成可判读的令牌。"""
    if driver_dir not in sys.path:
        sys.path.insert(0, driver_dir)
    try:
        from conv_engine import ConvEngineOverlay  # noqa: F401
        from conv_engine import parse_meta        # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "cannot import conv_engine from %s: %s" % (driver_dir, exc))
    return ConvEngineOverlay, parse_meta


def _parse_args(argv):
    ap = argparse.ArgumentParser(
        description="Board-side single-layer test for conv_engine (PYNQ only).")
    ap.add_argument("--bitfile", default=DEFAULT_BITFILE,
                    help="overlay .bit path (default: board/overlay/conv_engine.bit)")
    ap.add_argument("--cases-dir", default=CASES_DIR,
                    help="case root (default: data/layers/cases)")
    ap.add_argument("--driver-dir", default=DRIVER_DIR,
                    help="dir containing conv_engine.py")
    ap.add_argument("--case", action="append", default=None,
                    help="run only this case (repeatable); default: all cases")
    ap.add_argument("--list", action="store_true", help="list cases and exit")
    ap.add_argument("--repeat", type=int, default=3,
                    help="runs per case for timing (default 3; min is reported)")
    ap.add_argument("--timeout", type=float, default=10.0,
                    help="ap_done poll timeout in seconds (default 10)")
    ap.add_argument("--no-download", action="store_true",
                    help="bind the already-programmed bitstream (skip download)")
    ap.add_argument("--json", default=DEFAULT_JSON,
                    help="result JSON path (default: data/results/board_layer_test.json)")
    return ap.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)

    result = {
        "tool": "src/ps/host/run_layer_test.py",
        "status": "BOARD_LAYER_FAIL",
        "reason": "",
        "generated_utc": _now_utc(),
        "repo_root": REPO_ROOT,
        "bitfile": args.bitfile,
        "repeat": int(args.repeat),
        "timeout_s": float(args.timeout),
        "downloaded": not args.no_download,
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "cases": [],
        "notes": [
            "elapsed_ns is perf_counter(ap_start write -> ap_done observed).",
            "gops = MACs / elapsed; MACs = C_OUT*C_IN*K*K*H_OUT*W_OUT.",
            "bytes compared against out_ref.bin (PC golden, little-endian raw).",
        ],
    }

    cases = _list_cases(args.cases_dir)
    if args.list:
        for name in cases:
            print("CASE %s" % name)
        print("BOARD_LAYER_FAIL list_only")
        result["reason"] = "list_only"
        _write_json(args.json, result)
        return EXIT_FAIL

    selected = cases if not args.case else list(args.case)
    if not selected:
        result["reason"] = "no_cases_found"
        print("BOARD_LAYER_FAIL no_cases_found")
        _write_json(args.json, result)
        return EXIT_FAIL

    missing = [c for c in selected if not os.path.isfile(
        os.path.join(args.cases_dir, c, "meta.txt"))]
    if missing:
        result["reason"] = "case_not_found:" + ",".join(missing)
        print("BOARD_LAYER_FAIL case_not_found %s" % " ".join(missing))
        _write_json(args.json, result)
        return EXIT_FAIL

    # --- 环境门槛：先确认"我们真的在板子上"，再谈数字 -----------------
    if not os.path.isfile(args.bitfile):
        result["reason"] = "overlay_missing"
        print("BOARD_LAYER_FAIL overlay_missing %s" % args.bitfile)
        _write_json(args.json, result)
        return EXIT_FAIL

    try:
        ConvEngineOverlay, parse_meta = _load_driver(args.driver_dir)
    except ImportError as exc:
        result["reason"] = "not_on_board"
        result["notes"].append("driver import failed: %s" % exc)
        print("BOARD_LAYER_FAIL not_on_board")
        _write_json(args.json, result)
        return EXIT_FAIL

    try:
        import pynq  # noqa: F401
    except ImportError as exc:
        result["reason"] = "not_on_board"
        result["notes"].append("pynq import failed: %s" % exc)
        print("BOARD_LAYER_FAIL not_on_board")
        _write_json(args.json, result)
        return EXIT_FAIL

    # --- 加载 Overlay（地址全部从 .hwh/ip_dict 解析，不硬编码） ---------
    t_load0 = time.perf_counter()
    try:
        ovl = ConvEngineOverlay(args.bitfile, download=not args.no_download)
    except Exception as exc:  # noqa: BLE001 - 板上异常类型不可控，需落盘
        result["reason"] = "overlay_load_error"
        result["notes"].append("overlay load failed: %r" % (exc,))
        print("BOARD_LAYER_FAIL overlay_load_error")
        _write_json(args.json, result)
        return EXIT_FAIL
    result["overlay_load_s"] = round(time.perf_counter() - t_load0, 6)

    try:
        desc = ovl.describe()
    except Exception as exc:  # noqa: BLE001
        desc = "describe() unavailable: %r" % (exc,)
    result["windows"] = desc
    result["ip_dict_keys"] = list(ovl.ip_keys)
    print("OVERLAY_LOADED")
    print("WINDOWS %s" % desc)

    # --- 逐用例跑 -------------------------------------------------------
    n_ok = 0
    n_fail = 0
    agg_macs = 0
    agg_ns = 0

    for case in selected:
        case_dir = os.path.join(args.cases_dir, case)
        entry = {
            "case": case,
            "ap_done": False,
            "bytes_total": 0,
            "bytes_bad": -1,
            "mismatch": None,
            "elapsed_ns_runs": [],
            "elapsed_ns": None,
            "gops": None,
            "macs": None,
            "sentinel_intact": None,
            "ok": False,
            "reason": "",
        }
        result["cases"].append(entry)

        try:
            meta = parse_meta(case_dir)
            macs = (int(meta["C_OUT"]) * int(meta["C_IN"]) *
                    int(meta["K"]) * int(meta["K"]) *
                    int(meta["H_OUT"]) * int(meta["W_OUT"]))
            entry["macs"] = macs
            meta_in, _buffers = ovl.load_layer(case_dir)
            entry["meta"] = {k: meta_in[k] for k in sorted(meta_in)}
            ref = _read_ref(case_dir, meta_in)
        except Exception as exc:  # noqa: BLE001
            entry["reason"] = "load_error"
            entry["detail"] = repr(exc)
            print("CASE_FAIL %s load_error" % case)
            n_fail += 1
            continue

        run_ok = True
        run_err = ""
        done_all = True
        for r in range(max(1, int(args.repeat))):
            try:
                run = ovl.run_once(meta_in)
            except Exception as exc:  # noqa: BLE001
                run_ok = False
                run_err = "run_error"
                entry["detail"] = repr(exc)
                break
            elapsed_ns = int(round(run["elapsed_s"] * 1e9))
            entry["elapsed_ns_runs"].append(elapsed_ns)
            if not run["done"]:
                done_all = False
                run_ok = False
                run_err = "ap_done_timeout"
                entry["ap_ctrl_final"] = run["ap_ctrl_final"]
                break
            print("RUN %s iter=%d elapsed_ns=%d ap_done=1" % (
                case, r, elapsed_ns))
        entry["ap_done"] = bool(run_ok and done_all)

        if not run_ok:
            entry["reason"] = run_err
            print("CASE_FAIL %s %s" % (case, run_err))
            n_fail += 1
            continue

        # 取输出并与金标准逐字节比对
        try:
            got, _out_buf = ovl.get_out()
            bad, total, first = _compare(got, ref)
        except Exception as exc:  # noqa: BLE001
            entry["reason"] = "compare_error"
            entry["detail"] = repr(exc)
            print("CASE_FAIL %s compare_error" % case)
            n_fail += 1
            continue

        # 0xCC 哨兵：全 sentinel 说明 PL 根本没写输出（比"全 0 且 ref 也全 0"更能区分）
        sentinel = np.uint8(0xCC)
        entry["sentinel_intact"] = bool(np.all(got == sentinel))
        entry["bytes_total"] = total
        entry["bytes_bad"] = bad
        entry["mismatch"] = None
        if first is not None:
            c, h, w, got_v, ref_v = first
            entry["mismatch"] = {"c": c, "h": h, "w": w,
                                 "board": got_v, "ref": ref_v}
            print("MISMATCH %s bad=%d/%d first=(c=%d,h=%d,w=%d) "
                  "board=%d ref=%d" % (case, bad, total, c, h, w, got_v, ref_v))

        elapsed_ns = min(entry["elapsed_ns_runs"])
        entry["elapsed_ns"] = elapsed_ns
        entry["elapsed_ns_min"] = elapsed_ns
        entry["elapsed_ns_max"] = max(entry["elapsed_ns_runs"])
        entry["gops"] = (macs / (elapsed_ns * 1e-9)) / 1e9
        entry["ok"] = bool(bad == 0 and entry["ap_done"])

        if not entry["ok"]:
            entry["reason"] = "mismatch" if bad else "ap_done_not_seen"
            print("CASE_FAIL %s bad=%d/%d" % (case, bad, total))
            n_fail += 1
            continue

        n_ok += 1
        agg_macs += macs
        agg_ns += elapsed_ns
        print("CASE_OK %s bytes=%d elapsed_ns=%d gops=%.4f" % (
            case, total, elapsed_ns, entry["gops"]))

    # --- 收尾 -----------------------------------------------------------
    result["cases_ok"] = n_ok
    result["cases_fail"] = n_fail

    if n_fail == 0 and n_ok == len(selected):
        agg_gops = (agg_macs / (agg_ns * 1e-9)) / 1e9 if agg_ns else 0.0
        result["status"] = "BOARD_LAYER_OK"
        result["reason"] = ""
        result["aggregate"] = {"macs": agg_macs, "elapsed_ns": agg_ns,
                               "gops": agg_gops}
        _write_json(args.json, result)
        print("RESULT_JSON %s" % args.json)
        if len(selected) == 1:
            one = result["cases"][0]
            print("BOARD_LAYER_OK %s %d %.4f" % (
                one["case"], one["elapsed_ns"], one["gops"]))
        else:
            print("BOARD_LAYER_OK all_%d_cases %d %.4f" % (
                len(selected), agg_ns, agg_gops))
        return EXIT_OK

    if n_ok == 0 and n_fail == len(selected):
        first_reason = result["cases"][0].get("reason", "unknown")
        result["reason"] = first_reason
    else:
        result["reason"] = "partial_fail:%d_ok_%d_fail" % (n_ok, n_fail)
    _write_json(args.json, result)
    print("RESULT_JSON %s" % args.json)
    print("BOARD_LAYER_FAIL %s" % _bad_token(result))
    return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
