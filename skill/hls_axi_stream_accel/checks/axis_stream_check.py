#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""axis_stream_check.py —— AXI-Stream 主机侧的**协议规则检查器**（独立、无依赖）

为什么需要它：HLS 导出的 `m_axis` 在仿真里"看起来对"，上板却丢拍，最常见的原因不是算法，
而是握手规则被违反 —— 而波形肉眼看几千拍是不现实的。把规则变成脚本，才有判据。

它检查的规则（AXI4-Stream 最小集）：
  R1  `tvalid && !tready` 期间，`tdata` / `tlast` / `tkeep` 必须保持不变；
  R2  `tvalid` 一旦拉高，必须一直保持到该拍握手完成（不允许中间撤回）；
  R3  握手上限：`tvalid && !tready` 连续超过 `--max-stall` 拍记为违规（默认 64）——
      这**不是**协议要求，是工程门限：长时间拖欠通常意味着下游没在消费；
  R4  可选 `--expect-beats N`：握手总拍数必须等于 N（防"少发/多发"）。

输入格式（每行一拍，`#` 开头为注释）：

    # cycle tvalid tready tlast tdata tkeep
    0 0 0 0 0 0
    1 1 0 0 0x11223344 0xf
    2 1 1 0 0x11223344 0xf

末行：`AXIS_CHECK_OK beats=N` 或 `AXIS_CHECK_FAIL n=N first=cycle@rule`

用法：
    python axis_stream_check.py trace.txt [--max-stall 64] [--expect-beats 128]
    python axis_stream_check.py --selftest

★ 边界：它只查**主机侧行为**（tvalid 方），不查从机是否该拉 tready、也不查
  数据内容对不对 —— 内容正确性要在 testbench 里逐位比（见本包 README §2.1）。
"""
from __future__ import annotations

import argparse
import sys


def _parse_int(tok):
    tok = tok.strip().replace("_", "")
    return int(tok, 0) if tok.lower().startswith("0x") else int(tok)


def parse_trace(text):
    rows = []
    for ln, line in enumerate(text.splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 4:
            raise ValueError("line %d: 至少要有 cycle/tvalid/tready/tlast 四列" % ln)
        cyc, tv, tr, tl = (_parse_int(p) for p in parts[:4])
        data = _parse_int(parts[4]) if len(parts) > 4 else 0
        keep = _parse_int(parts[5]) if len(parts) > 5 else 0
        rows.append((cyc, tv, tr, tl, data, keep))
    return rows


def check(rows, max_stall=64):
    errs = []
    beats = 0
    prev = None
    stall = 0
    for r in rows:
        cyc, tv, tr, tl, data, keep = r
        if prev is not None:
            pcyc, ptv, ptr, ptl, pdata, pkeep = prev
            stalled = ptv and not ptr
            if stalled:
                stall += 1
                if (data, tl, keep) != (pdata, ptl, pkeep):
                    errs.append((cyc, "R1", "tvalid&&!tready 期间 tdata/tlast/tkeep 变了"))
                if not tv:
                    errs.append((cyc, "R2", "tvalid 在握手完成前被撤回"))
                if stall > max_stall:
                    errs.append((cyc, "R3", "连续拖欠超过 %d 拍" % max_stall))
                    stall = 0
            else:
                stall = 0
        if tv and tr:
            beats += 1
        prev = r
    return beats, errs


def _selftest():
    ok = True
    good = """# cycle tvalid tready tlast tdata tkeep
0 0 0 0 0 0
1 1 0 0 0x11223344 0xf
2 1 0 0 0x11223344 0xf
3 1 1 0 0x11223344 0xf
4 1 0 1 0xdeadbeef 0xf
5 1 1 1 0xdeadbeef 0xf
"""
    beats, errs = check(parse_trace(good))
    if beats != 2 or errs:
        print("SELFTEST good trace FAIL", beats, errs)
        ok = False

    bad_r1 = """0 1 0 0 0x1 0xf
1 1 0 0 0x2 0xf
2 1 1 0 0x2 0xf
"""
    _b, errs = check(parse_trace(bad_r1))
    if not any(e[1] == "R1" for e in errs):
        print("SELFTEST R1 not detected")
        ok = False

    bad_r2 = """0 1 0 0 0x1 0xf
1 0 0 0 0x1 0xf
"""
    _b, errs = check(parse_trace(bad_r2))
    if not any(e[1] == "R2" for e in errs):
        print("SELFTEST R2 not detected")
        ok = False

    stall = ["0 1 0 0 0x1 0xf"] * 5
    _b, errs = check(parse_trace("\n".join(stall)), max_stall=3)
    if not any(e[1] == "R3" for e in errs):
        print("SELFTEST R3 not detected")
        ok = False

    print("AXIS_CHECK_SELFTEST", "OK" if ok else "FAIL")
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("trace", nargs="?")
    ap.add_argument("--max-stall", type=int, default=64)
    ap.add_argument("--expect-beats", type=int, default=None)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest()
    if not args.trace:
        ap.print_help()
        return 2
    with open(args.trace, "r", encoding="utf-8") as f:
        rows = parse_trace(f.read())
    if not rows:
        print("AXIS_CHECK_FAIL no samples")
        return 1
    beats, errs = check(rows, args.max_stall)
    if args.expect_beats is not None and beats != args.expect_beats:
        errs.append((rows[-1][0], "R4", "握手拍数 %d != 期望 %d" % (beats, args.expect_beats)))
    if errs:
        cyc, rule, msg = errs[0]
        print("AXIS_CHECK_FAIL n=%d first=%d@%s %s" % (len(errs), cyc, rule, msg))
        return 1
    print("AXIS_CHECK_OK beats=%d" % beats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
