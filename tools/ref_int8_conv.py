#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ref_int8_conv.py -- conv_engine 的**逐位**numpy 参考实现（HLS csim 的唯一判据）。

契约真源：[`../docs/accel_interface.md`](../docs/accel_interface.md) §1（三行公式）、
§2（NCHW 布局）、§3（定点口径）、§5（层范围）。本文件**逐字**实现 §3，不做任何"等价改写"。

## 它算什么

    acc[c]  = SUM_{ci,kh,kw} x_q[ci][h*stride+kh-pad][w*stride+kw-pad] * w_q[c][ci][kh][kw]
              + bias_i32[c]                          # bias 已含 -zx*SUM(w) 折叠，见 §3.2
    q_pre   = clamp( round( acc[c] * M[c] * 2^-S[c] ) + zp_pre , 0, 255 )
    q_out   = LUT[ q_pre ]

三条硬口径（契约没写细，这里**必须**钉死，否则 csim 永远对不上）：

1. **累加是整数，不是 float 模拟**。x_q 是 uint8、w_q 是 int8，乘积精确，int32 累加精确。
   本文件用 int64 累加**只为检测溢出**（累加结果超出 int32 就说明契约 §3.3 的前提破了，
   此时硬件会回卷、PC 不会，必须报错而不是悄悄算个"更好"的数）。实测 63 层全部 |acc| < 2^31。
2. **requant 的舍入 = 加半后向下取整**（round-half-up，ties 朝 +∞）：
       r = floor( (acc*M + 2^(S-1)) / 2^S )
   这正是 HLS 里 `(acc * M + (1 << (S-1))) >> S` 的语义（Vivado HLS 的 `>>` 对负数做算术移位
   = 向下取整）。**不能**用 C 的 `/`（对负数截断到 0，会差 1 LSB），也不能用 numpy 的
   `np.round`（half-to-even，和硬件不一致）。契约 §3.3 只写了 `round(...)`，没定义 ties，
   ⇒ 已在 `tools/README.md` 登记为契约待补项，本文件取「加半后下取整」并作为判据。
3. **64 位中间量**（§3.3 要求）：acc 最大 ~2^31、M < 2^31 ⇒ 乘积 ~2^62，必须 int64。

## 数据从哪来

读 `data/layers/yolov8n_320_layers.json`（[`export_layers.py`](export_layers.py) 产出）+
`data/layers/bin/<layer>/` 下的**裸小端二进制**：`w.bin`(int8)、`params.bin`(int32×[C_OUT][3])、
`lut.bin`(uint8[256])、`in.bin`(uint8)、`out.bin`(uint8)。`in.bin`/`out.bin` 直接就是 HLS
testbench 该喂 / 该比的激励与期望，csim 只要**逐元素相同**即通过。

## 两重判据

* **自洽判据**（ref vs 包里的 `out.bin`）：必须 **0 个元素不同**。这条只有回归价值。
* **外部判据**（ref vs `data/golden/*_golden.npz` 的真实 float 输出，按本层 (so,zo) 反量化）：
  差异只能来自「输入被量化到 uint8」这一件事，因此必须满足
      max|Δ| <= 2 LSB、逐通道平均偏差 <= 0.5 LSB、全体平均偏差 <= 0.10 LSB。
  逐通道平均偏差这一条是**专门用来抓 §3.2 漏折叠 `-zx*SUM(w)` 的**：漏了它不会让
  逐元素差异变随机，而是给出**逐通道常数偏移**（每条通道偏一个数，且各通道不同），
  均值偏差会立刻爆表。所以本工具同时打印「差异是整体偏移 / 逐通道偏移 / 随机噪声」三类特征。

## 用法

    python tools/ref_int8_conv.py --all                 # 全部层，末行 REF_CONV_OK / REF_CONV_FAIL
    python tools/ref_int8_conv.py --layer /model.8/cv2/conv/Conv
    python tools/ref_int8_conv.py --all --json data/results/ref_int8_conv.json
    python tools/ref_int8_conv.py --all --no-golden     # 只做自洽判据（不推荐）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.dont_write_bytecode = True  # 禁止 tools/__pycache__（check_repo.py 检查 d 会 FAIL）

import numpy as np

REPO = Path(__file__).resolve().parents[1]
LAYERS_DIR = REPO / "data" / "layers"
DEFAULT_MANIFEST = LAYERS_DIR / "yolov8n_320_layers.json"
DEFAULT_GOLDEN_NPZ = REPO / "data" / "golden" / "yolov8n_320_golden.npz"
RESULTS_DIR = REPO / "data" / "results"

# ---- 外部判据阈值（见模块 docstring；全部按"LSB"计，不按相对值 —— 相对值在半数像素为 0 时无意义）
MAX_ABS_LSB = 2.0           # 单元素最大 |Δ|
MAX_CHANNEL_BIAS_LSB = 0.5  # 任一输出通道的平均 |Δ|（抓 -zx*SUM(w) 漏折叠）
MAX_MEAN_BIAS_LSB = 0.10    # 全体平均 |Δ|（抓整体偏移 / 舍入口径错）

INT32_MAX = (1 << 31) - 1
INT32_MIN = -(1 << 31)


# ---------------------------------------------------------------------------
# 数值小工具：舍入口径必须显式，不许依赖 numpy 默认
# ---------------------------------------------------------------------------
def round_half_away_from_zero(x) -> np.ndarray:
    """四舍五入、ties 远离 0 == C 的 `round()` 语义。

    **不要**用 `np.round`：它是 half-to-even（银行家舍入），
    和 C/HLS 的 `round()`/加半取整都不同，会在 0.5 处差 1。
    本函数只用于 **PS 侧生成 LUT / 量化 scale** 这些浮点步骤；
    requant 的整数舍入走 `_requant_int`（加半后下取整），两者刻意分开。
    """
    x = np.asarray(x, dtype=np.float64)
    return np.sign(x) * np.floor(np.abs(x) + 0.5)


def quantize_u8(y_f, s: float, z: int) -> np.ndarray:
    """浮点 -> QUInt8：`clamp(round(y/s) + z, 0, 255)`（契约 §3.4 同一口径）。"""
    if not (s > 0.0):
        raise SystemExit("非法 scale=%r（必须 > 0）" % (s,))
    q = round_half_away_from_zero(np.asarray(y_f, dtype=np.float64) / float(s)) + int(z)
    return np.clip(q, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# 包（data/layers）读取
# ---------------------------------------------------------------------------
def load_manifest(path: Path = DEFAULT_MANIFEST) -> dict:
    p = Path(path)
    if not p.is_file():
        raise SystemExit("找不到层参数包清单：%s（先跑 tools/export_layers.py）" % p)
    man = json.loads(p.read_text(encoding="utf-8"))
    if man.get("schema") != "zynq-yolo/layers/v1":
        raise SystemExit("清单 schema 不符：%r" % (man.get("schema"),))
    return man


def layer_root(man: dict) -> Path:
    return Path(man["_manifest_dir"])


def find_layer(man: dict, name: str) -> dict:
    """按 ONNX 节点名 / 包内 key / 序号 找层。三种都接受，找不到就报错列出候选。"""
    want = name.strip()
    want_slash = want.lstrip("/")
    for L in man["layers"]:
        onnx_nm = L["onnx_name"].lstrip("/")
        if want in (L["onnx_name"], L["key"], onnx_nm, want_slash):
            return L
        if want.isdigit() and int(want) == L["idx"]:
            return L
    raise SystemExit("找不到层 %r；可用 --layer 传 ONNX 名（如 /model.8/cv2/conv/Conv）、"
                     "包内 key 或序号（0..%d）" % (name, len(man["layers"]) - 1))


def load_layer_arrays(L: dict, root: Path) -> Dict[str, np.ndarray]:
    g = L["geom"]
    cin, cout, k = int(g["C_IN"]), int(g["C_OUT"]), int(g["K"])
    w = np.fromfile(str(root / L["files"]["w"]), dtype=np.int8)
    params = np.fromfile(str(root / L["files"]["params"]), dtype="<i4")
    lut = np.fromfile(str(root / L["files"]["lut"]), dtype=np.uint8)
    in_q = np.fromfile(str(root / L["files"]["in"]), dtype=np.uint8)
    out_q = np.fromfile(str(root / L["files"]["out"]), dtype=np.uint8)

    if w.size != cout * cin * k * k:
        raise SystemExit("%s: w.bin 长度 %d != C_OUT*C_IN*K*K=%d"
                         % (L["key"], w.size, cout * cin * k * k))
    if params.size != cout * 3:
        raise SystemExit("%s: params.bin 长度 %d != C_OUT*3=%d" % (L["key"], params.size, cout * 3))
    if lut.size != 256:
        raise SystemExit("%s: lut.bin 长度 %d != 256" % (L["key"], lut.size))
    if in_q.size != cin * int(g["H_IN"]) * int(g["W_IN"]):
        raise SystemExit("%s: in.bin 长度 %d != C_IN*H_IN*W_IN=%d"
                         % (L["key"], in_q.size, cin * int(g["H_IN"]) * int(g["W_IN"])))
    if out_q.size != cout * int(g["H_OUT"]) * int(g["W_OUT"]):
        raise SystemExit("%s: out.bin 长度 %d != C_OUT*H_OUT*W_OUT=%d"
                         % (L["key"], out_q.size, cout * int(g["H_OUT"]) * int(g["W_OUT"])))

    return {
        "w": w.reshape(cout, cin * k * k),
        "bias_i32": params.reshape(cout, 3)[:, 0].astype(np.int64),
        "M": params.reshape(cout, 3)[:, 1].astype(np.int64),
        "S": params.reshape(cout, 3)[:, 2].astype(np.int64),
        "lut": lut,
        "in_q": in_q.reshape(cin, int(g["H_IN"]), int(g["W_IN"])),
        "out_q": out_q.reshape(cout, int(g["H_OUT"]), int(g["W_OUT"])),
    }


# ---------------------------------------------------------------------------
# 契约 §1 的三行 —— 逐字实现
# ---------------------------------------------------------------------------
def conv_engine_acc(in_q: np.ndarray, w: np.ndarray, bias_i32: np.ndarray,
                    zp_in: int, stride: int, pad: int) -> Tuple[np.ndarray, int, int]:
    """§1 第 1 行：int32 域累加。返回 int64 累加结果（仅用于溢出检测，见 docstring）。

    越界像素读作 `zp_in`（契约 §2.2）—— 不是 0，也不做 per-pixel 减零点
    （零点已在 bias 里折叠，§3.2）。
    """
    cin, hin, win = in_q.shape
    if w.ndim != 2:
        raise SystemExit("权重必须是 2 维 (C_OUT, C_IN*K*K)，实际 shape=%r" % (w.shape,))
    cout, kk = w.shape
    k = int(round((kk // cin) ** 0.5)) if cin else 0
    if cin * k * k != kk:
        raise SystemExit("权重布局不符：期望 C_IN*K*K=%d，实际 %d" % (cin * k * k, kk))

    hout = (hin + 2 * pad - k) // stride + 1
    wout = (win + 2 * pad - k) // stride + 1
    if hout <= 0 or wout <= 0:
        raise SystemExit("输出尺寸非法：H_OUT=%d W_OUT=%d" % (hout, wout))

    if pad:
        xp = np.full((cin, hin + 2 * pad, win + 2 * pad), int(zp_in), dtype=np.uint8)
        xp[:, pad:pad + hin, pad:pad + win] = in_q
    else:
        xp = np.ascontiguousarray(in_q)

    acc = np.zeros((cout, hout, wout), dtype=np.int64)
    w4 = w.reshape(cout, cin, k, k).astype(np.int32)
    x32 = xp.astype(np.int32)
    hspan, wspan = stride * hout, stride * wout
    for kh in range(k):
        for kw in range(k):
            # (H_OUT,W_OUT) 上按 stride 抽样 => 等效于 §1 的 h*stride+kh-pad
            xs = x32[:, kh:kh + hspan:stride, kw:kw + wspan:stride]
            acc += np.tensordot(w4[:, :, kh, kw], xs, axes=([1], [0]))

    acc += bias_i32.reshape(-1, 1, 1)
    return acc, hout, wout


def check_acc_int32(acc: np.ndarray, key: str) -> int:
    """契约 §3.3 假设 acc 落在 int32 内。超了就报错（硬件会回卷，PC 不会）。"""
    mx = int(np.max(np.abs(acc))) if acc.size else 0
    if mx > INT32_MAX:
        raise SystemExit("%s: |acc| 最大 %d 超出 int32（%d）——契约 §3.3 前提被破坏，"
                         "硬件会回卷，参考实现拒绝假装正确" % (key, mx, INT32_MAX))
    return mx


def requant_int(acc: np.ndarray, M: np.ndarray, S: np.ndarray, zp_pre: int) -> np.ndarray:
    """§1 第 2 行：int 域 requant，**加半后下取整**（= HLS 的 (acc*M + 2^(S-1)) >> S）。

    逐通道 S 用一个循环按 S 值分组处理；S 是 int32 标量，必须 >= 1。
    """
    cout = acc.shape[0]
    if M.shape != (cout,) or S.shape != (cout,):
        raise SystemExit("M/S 形状不符")
    if np.any(S < 1):
        raise SystemExit("S 必须 >= 1，实际 min=%d" % int(S.min()))
    if np.any(M < 0):
        raise SystemExit("M 必须 >= 0（per-channel 权重 scale 为正），实际 min=%d" % int(M.min()))

    q = np.empty(acc.shape, dtype=np.int64)
    for s_val in np.unique(S):
        s = int(s_val)
        idx = np.nonzero(S == s)[0]
        p = acc[idx] * M[idx].reshape(-1, 1, 1)          # 64 位中间量（§3.3）
        q[idx] = np.floor_divide(p + (1 << (s - 1)), 1 << s)
    q += int(zp_pre)
    np.clip(q, 0, 255, out=q)
    return q.astype(np.uint8)


def conv_engine(in_q, w, bias_i32, M, S, lut, zp_in, zp_pre, stride, pad):
    """§1 全三行。返回 (out_uint8, acc_max_abs, q_pre)。"""
    acc, hout, wout = conv_engine_acc(in_q, w, bias_i32, zp_in, stride, pad)
    acc_max = check_acc_int32(acc, "conv")
    q_pre = requant_int(acc, M, S, zp_pre)
    out = lut[q_pre]                                     # §1 第 3 行：LUT 同时做激活 + 输出缩放
    return out, acc_max, q_pre


# ---------------------------------------------------------------------------
# 比对
# ---------------------------------------------------------------------------
def diff_profile(ref_u8: np.ndarray, got_u8: np.ndarray) -> dict:
    """逐元素差异统计 + **差异形状判别**（整体偏移 / 逐通道偏移 / 随机噪声）。"""
    if ref_u8.shape != got_u8.shape:
        return {"shape_mismatch": True, "ref_shape": list(ref_u8.shape),
                "got_shape": list(got_u8.shape)}
    d = ref_u8.astype(np.int16) - got_u8.astype(np.int16)
    n = int(d.size)
    cout = d.shape[0]
    ch_mean = d.reshape(cout, -1).mean(axis=1) if cout else np.zeros(1)
    return {
        "n": n,
        "n_exact": int(np.count_nonzero(d == 0)),
        "exact_frac": float(np.count_nonzero(d == 0) / n) if n else 1.0,
        "n_abs1": int(np.count_nonzero(np.abs(d) == 1)),
        "n_abs_gt1": int(np.count_nonzero(np.abs(d) > 1)),
        "max_abs": float(np.max(np.abs(d))) if n else 0.0,
        "mean_signed": float(d.mean()) if n else 0.0,
        "mean_abs": float(np.abs(d).mean()) if n else 0.0,
        "max_channel_mean_abs": float(np.max(np.abs(ch_mean))) if cout else 0.0,
        "max_channel_mean_signed": float(np.max(ch_mean)) if cout else 0.0,
        "min_channel_mean_signed": float(np.min(ch_mean)) if cout else 0.0,
    }


def classify(prof: dict) -> str:
    """把差异特征翻译成一句人话 —— 这正是"对不上"时第一个该看的东西。"""
    if prof.get("shape_mismatch"):
        return "SHAPE_MISMATCH"
    if prof["n_abs_gt1"] == 0 and prof["max_abs"] <= 1.0:
        if prof["max_channel_mean_abs"] <= 0.02 and abs(prof["mean_signed"]) <= 0.02:
            return "OK: 只有 +-1 LSB 舍入/量化噪声，无系统偏移"
        return "CHANNEL_OFFSET: 有逐通道常数偏移（怀疑 §3.2 的 -zx*SUM(w) 没折叠）"
    if prof["max_channel_mean_abs"] > 0.5:
        return "CHANNEL_OFFSET: 逐通道偏移很大（先查 bias 折叠与 sw 顺序）"
    if abs(prof["mean_signed"]) > 0.5:
        return "GLOBAL_OFFSET: 整体偏移（先查 zp_pre / LUT 的 zo 是否用错）"
    return "NOISE: 差异较大但无系统偏移（先查输入量化 scale 与 golden 是否同一张量）"


# ---------------------------------------------------------------------------
# 单层 / 全部层
# ---------------------------------------------------------------------------
def check_layer(L: dict, root: Path, golden: Optional[dict]) -> dict:
    g = L["geom"]
    q = L["quant"]
    arr = load_layer_arrays(L, root)
    out_ref, acc_max, q_pre = conv_engine(
        arr["in_q"], arr["w"], arr["bias_i32"], arr["M"], arr["S"], arr["lut"],
        int(q["zx"]), int(q["zp_pre"]), int(g["stride"]), int(g["pad"]))

    rec = {
        "key": L["key"],
        "onnx_name": L["onnx_name"],
        "idx": L["idx"],
        "geom": g,
        "act": L["act"],
        "acc_max_abs": acc_max,
        "acc_int32_headroom": float(INT32_MAX / acc_max) if acc_max else None,
        "q_pre_min": int(q_pre.min()) if q_pre.size else 0,
        "q_pre_max": int(q_pre.max()) if q_pre.size else 0,
        "lut_monotone": bool(np.all(np.diff(arr["lut"].astype(np.int16)) >= 0)),
    }

    # ---- 判据 1：与包内 out.bin 逐位相同（回归） ----
    prof_pkg = diff_profile(arr["out_q"], out_ref)
    rec["package"] = prof_pkg
    rec["package_ok"] = bool(prof_pkg.get("n_abs_gt1", 1) == 0 and prof_pkg.get("max_abs", 1) == 0.0)

    # ---- 判据 2：与 golden 的真实 float 输出反量化后比对 ----
    if golden is not None and L.get("golden", {}).get("post_act"):
        key = L["golden"]["post_act"]
        if key in golden["npz"].files:
            y = np.asarray(golden["npz"][key], dtype=np.float64).reshape(
                int(g["C_OUT"]), int(g["H_OUT"]), int(g["W_OUT"]))
            y_gold_u8 = quantize_u8(y, float(q["so"]), int(q["zo"]))
            prof_g = diff_profile(y_gold_u8, out_ref)
            rec["golden"] = prof_g
            rec["golden_verdict"] = classify(prof_g)
            rec["golden_ok"] = bool(
                prof_g["max_abs"] <= MAX_ABS_LSB
                and prof_g["max_channel_mean_abs"] <= MAX_CHANNEL_BIAS_LSB
                and abs(prof_g["mean_signed"]) <= MAX_MEAN_BIAS_LSB)
        else:
            rec["golden"] = None
            rec["golden_verdict"] = "GOLDEN_MISSING_KEY: %s" % key
            rec["golden_ok"] = False
    return rec


def _load_golden(path: Optional[Path]) -> Optional[dict]:
    if path is None:
        return None
    p = Path(path)
    if not p.is_file():
        raise SystemExit("找不到 golden npz：%s（可加 --no-golden 只做自洽判据）" % p)
    return {"npz": np.load(str(p))}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="conv_engine 逐位 numpy 参考实现（契约 §1/§2/§3）")
    ap.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    ap.add_argument("--golden", default=str(DEFAULT_GOLDEN_NPZ), help="golden npz（float16 逐层张量）")
    ap.add_argument("--no-golden", action="store_true", help="跳过与 golden 的比对（只做自洽判据）")
    ap.add_argument("--layer", default=None, help="只跑一层（ONNX 名 / key / 序号）")
    ap.add_argument("--all", action="store_true", help="跑全部层")
    ap.add_argument("--json", default="", help="把结果写到该 json（如 data/results/ref_int8_conv.json）")
    ap.add_argument("--quiet", action="store_true", help="只打印汇总，不逐层打印")
    args = ap.parse_args(argv)

    man = load_manifest(Path(args.manifest))
    man["_manifest_dir"] = str(Path(args.manifest).resolve().parent)
    root = layer_root(man)

    golden = None if args.no_golden else _load_golden(Path(args.golden))

    if args.layer:
        targets = [find_layer(man, args.layer)]
    elif args.all:
        targets = man["layers"]
    else:
        ap.error("需要 --layer NAME 或 --all")

    print("=" * 78)
    print("ref_int8_conv.py -- conv_engine 逐位参考实现")
    print("  manifest : %s" % Path(args.manifest))
    print("  golden   : %s" % ("(skipped)" if golden is None else args.golden))
    print("  层数     : %d（契约 §5 内；清单另记 unsupported %d 个）"
          % (len(man["layers"]), len(man.get("unsupported", []))))
    print("=" * 78)

    recs: List[dict] = []
    n_pkg_bad = 0
    n_gold_bad = 0
    for L in targets:
        rec = check_layer(L, root, golden)
        recs.append(rec)
        if not rec["package_ok"]:
            n_pkg_bad += 1
        if rec.get("golden") is not None and not rec.get("golden_ok", False):
            n_gold_bad += 1
        if rec.get("golden") is None and golden is not None:
            n_gold_bad += 1
        if not args.quiet:
            g = rec["geom"]
            line = ("  [%02d] %-44s %2dx%-3dx%-3d K%d s%d p%d %-4s  acc<=2^%.1f"
                    % (rec["idx"], rec["key"][:44], g["C_OUT"], g["H_IN"], g["W_IN"],
                       g["K"], g["stride"], g["pad"], rec["act"],
                       np.log2(max(rec["acc_max_abs"], 1))))
            print(line)
            p = rec["package"]
            print("        pkg : exact=%.4f%%  max|d|=%g  -> %s"
                  % (p.get("exact_frac", 0) * 100, p.get("max_abs", 0),
                     "bit-exact" if rec["package_ok"] else "MISMATCH"))
            if rec.get("golden"):
                gg = rec["golden"]
                print("        gold: exact=%.2f%%  |d|=1:%.2f%%  max|d|=%g  mean=%+.4f  "
                      "ch_bias=%.4f  -> %s"
                      % (gg["exact_frac"] * 100, 100.0 * gg["n_abs1"] / max(gg["n"], 1),
                         gg["max_abs"], gg["mean_signed"], gg["max_channel_mean_abs"],
                         rec["golden_verdict"]))
            elif golden is not None:
                print("        gold: %s" % rec.get("golden_verdict", "n/a"))

    # ---- 汇总 ----
    print("")
    print("-" * 78)
    tot_el = sum(r["package"].get("n", 0) for r in recs)
    print("  输出元素合计        : %d" % tot_el)
    print("  自洽判据（vs out.bin）: %d/%d 层逐位相同%s"
          % (len(recs) - n_pkg_bad, len(recs), "" if n_pkg_bad == 0 else "  <== %d 层不一致" % n_pkg_bad))
    if golden is not None:
        gs = [r for r in recs if r.get("golden")]
        if gs:
            el = sum(r["golden"]["n"] for r in gs)
            exact = sum(r["golden"]["n_exact"] for r in gs)
            a1 = sum(r["golden"]["n_abs1"] for r in gs)
            gt1 = sum(r["golden"]["n_abs_gt1"] for r in gs)
            worst = max(gs, key=lambda r: r["golden"]["max_abs"])
            worst_ch = max(gs, key=lambda r: r["golden"]["max_channel_mean_abs"])
            print("  外部判据（vs golden） : %d 层 / %d 元素" % (len(gs), el))
            print("      逐位相同          : %d (%.4f%%)" % (exact, 100.0 * exact / max(el, 1)))
            print("      |Δ| == 1 LSB      : %d (%.4f%%)" % (a1, 100.0 * a1 / max(el, 1)))
            print("      |Δ|  > 1 LSB      : %d (%.4f%%)" % (gt1, 100.0 * gt1 / max(el, 1)))
            print("      max|Δ|            : %g LSB  (%s)" % (worst["golden"]["max_abs"], worst["key"]))
            print("      最大逐通道平均偏差: %.4f LSB  (%s)"
                  % (worst_ch["golden"]["max_channel_mean_abs"], worst_ch["key"]))
            print("      阈值              : max|Δ|<=%g, 逐通道<=%g, 全体均值<=%g"
                  % (MAX_ABS_LSB, MAX_CHANNEL_BIAS_LSB, MAX_MEAN_BIAS_LSB))
            print("      超阈层数          : %d" % n_gold_bad)
        tot_layers = len(recs)
        print("  参考实现 LUT 单调性   : %d/%d 层单调递增"
              % (sum(1 for r in recs if r["lut_monotone"]), tot_layers))
        mx_acc = max(recs, key=lambda r: r["acc_max_abs"])
        print("  累加器最大绝对值      : %d (%.2f%% of int32)  @ %s"
              % (mx_acc["acc_max_abs"], 100.0 * mx_acc["acc_max_abs"] / INT32_MAX, mx_acc["key"]))

    ok = (n_pkg_bad == 0) and (n_gold_bad == 0)

    if args.json:
        out = Path(args.json)
        if not out.is_absolute():
            out = REPO / out
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "manifest": str(Path(args.manifest).as_posix()),
            "golden": None if golden is None else str(Path(args.golden).as_posix()),
            "thresholds": {"max_abs_lsb": MAX_ABS_LSB,
                           "max_channel_bias_lsb": MAX_CHANNEL_BIAS_LSB,
                           "max_mean_bias_lsb": MAX_MEAN_BIAS_LSB},
            "layer_count": len(recs),
            "package_mismatch_layers": n_pkg_bad,
            "golden_threshold_fail_layers": n_gold_bad,
            "ok": ok,
            "layers": recs,
        }
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        print("  明细 -> %s" % out)

    print("")
    print("REF_CONV_OK" if ok else "REF_CONV_FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
