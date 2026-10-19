#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""export_layers.py -- 把 yolov8n_320 的 64 个 Conv 导成 PL 侧 conv_engine 能直接吃的**逐层参数包**。

契约真源：[`../docs/accel_interface.md`](../docs/accel_interface.md) §2（布局）、§3（定点口径）、
§5（层范围）。本工具只负责"照契约把数算出来并摆成契约要求的字节序"，**不重新定义任何口径**。

## 产出（`data/layers/`）

    yolov8n_320_layers.json        # 清单（入库，是"这些字节从哪来"的凭据）
    bin/<key>/w.bin                # int8   [C_OUT][C_IN][K][K]，内层按 kh*K+kw
    bin/<key>/params.bin           # int32  [C_OUT][3] = {bias_i32, M, S}
    bin/<key>/lut.bin              # uint8  [256]
    bin/<key>/in.bin               # uint8  [C_IN][H_IN][W_IN]
    bin/<key>/out.bin              # uint8  [C_OUT][H_OUT][W_OUT]（参考实现的输出 = csim 的期望）

全部小端、连续、裸二进制 —— 就是 `m_axi` 侧的 `memcpy` 源。`*.bin` 已被 `.gitignore` 忽略
（可从本脚本重建），`*.json` 入库作为凭证。

## 六个口径（每一个都对应契约的一句，写在这里是为了被审计）

1. **权重**：直接取 `data/models/quant/yolov8n_320_wq.npz` 里**已量化好的 int8**（`quantize_weights.py`
   产出），**绝不重算** —— 否则 ref 与硬件拿到的 w 不是同一批数。`__scale` 即 §3.3 的 `sw[c]`。
2. **激活 scale/zero_point（§3.1 的 sx/zx、sp/zp_pre、so/zo）**：契约只规定怎么*用*它们，
   没规定怎么*算*（见 `tools/README.md` 的契约待补项）。本工具一律用
   `scale=(max-min)/255`、`zero_point=clamp(round(-min/scale), 0, 255)`（ORT MinMax 口径），
   min/max 取 `data/golden/*_golden.json` 里记录的 **float32 真实值** —— 那是唯一可复算的锚点。
   首层输入是图输入 `images`（golden 里没有），用 `default_rng(20261019)` 确定性重算。
3. **`bias_i32`（§3.2）**：`round(b_f/(sx*sw[c])) - zx*SUM(w_q[c])`。**折叠项不能漏**，
   漏了就是逐通道常数偏移。
4. **`M`/`S`（§3.3）**：`m = sx*sw[c]/sp`，取 **S = 使 m*2^S 落在 [2^30, 2^31) 的最小整数**，
   `M = round(m*2^S)`（half-away-from-zero）。M 恒 < 2^31，乘积用 int64。
5. **`lut`（§3.4）**：`LUT[q] = clamp(round(act((q-zp_pre)*sp)/so) + zo, 0, 255)`，
   act ∈ {silu, identity}。**恒等层也走 LUT**（sp≠so 时它不是恒等映射）。
6. **`in.bin`**：把该层**真实输入**（golden float16 张量）按 (sx,zx) 量化成 uint8。
   它等价于 `images_QuantizeLinear_Output`，是"硬件该看到的输入"。

## act 怎么判（不是猜的，是图结构推的）

`Conv` 的输出若只接一个 `Sigmoid`→`Mul` 对，就是 SiLU（ONNX 把 SiLU 拆成两个算子）；
若直接进 `Reshape`，就是**无激活**（6 个 head 的 `cv*.2.2` 输出被 reshape 给 DFL）。
判据：golden 里存在 `<conv_out>_act_Mul_output_0` ⇒ act=silu，否则 act=none。
act=none 时令 `sp=so, zp_pre=zo`（输出张量就是 conv 输出张量本身）⇒ LUT 退化为恒等映射。

## 范围（§5）

`K∈{1,3}`、`stride∈{1,2}`、`pad∈{0,1}`、`C_IN 3..512`、`C_OUT 16..256`、`H/W 10..320`。
**超范围的层不塞进 `layers[]`**，进 `unsupported[]` 并标 `reason`（当前只有
`/model.22/dfl/conv/Conv`：输入 reshape 成 `[1,16,4,2100]`，`W=2100`、`H=4` 超范围）。
它的算术本身并不特殊（K=1/stride=1/pad=0），所以照样把 `w/params/lut/in/out` 写出来，
给 PS 侧 numpy 实现当**比对基准**用 —— 但硬件不跑它，故不计入 `layers[]`。

## 用法

    python tools/export_layers.py                    # 导出全部层，末行 EXPORT_LAYERS_OK
    python tools/export_layers.py --out data/layers  # 指定输出目录
    python tools/export_layers.py --no-bin           # 只写清单（快速自检几何/量化，不落二进制）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.dont_write_bytecode = True  # 禁止 tools/__pycache__（check_repo.py 检查 d 会 FAIL）

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
# §1/§3 的数值实现只有一份，就在 ref_int8_conv.py；本工具 import 它而不是抄一遍。
from ref_int8_conv import (  # noqa: E402
    conv_engine,
    quantize_u8,
    round_half_away_from_zero,
)

REPO = Path(__file__).resolve().parents[1]
DEFAULT_ONNX = REPO / "data" / "models" / "yolov8n_320.onnx"
DEFAULT_WQ = REPO / "data" / "models" / "quant" / "yolov8n_320_wq.npz"
DEFAULT_GOLDEN_JSON = REPO / "data" / "golden" / "yolov8n_320_golden.json"
DEFAULT_GOLDEN_NPZ = REPO / "data" / "golden" / "yolov8n_320_golden.npz"
DEFAULT_OUT = REPO / "data" / "layers"

CONTRACT = "docs/accel_interface.md"
SCHEMA = "zynq-yolo/layers/v1"
INPUT_SEED = 20261019

# ---- 契约 §5 的支持范围（超一条就进 unsupported[]，不是"尽量跑"）
LIM_K = (1, 3)
LIM_STRIDE = (1, 2)
LIM_PAD = (0, 1)
LIM_CIN = (3, 512)
LIM_COUT = (16, 256)
LIM_HW = (10, 320)

INT32_MAX = (1 << 31) - 1


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(str(path), "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def golden_key(onnx_name: str) -> str:
    """ONNX 张量名 -> golden/npz 的 key（`tools/make_golden.py:142-143` 同一规则）。"""
    src = onnx_name[len("golden__"):] if onnx_name.startswith("golden__") else onnx_name
    return src.replace("/", "_").replace(":", "_") or "tensor"


def layer_key(onnx_name: str) -> str:
    """ONNX 节点名 -> 包内 key（与 wq.npz 的 `_model.0_conv_Conv__w` 同一前缀）。"""
    return onnx_name.replace("/", "_").replace(":", "_")


def act_silu(x):
    """SiLU = x*sigmoid(x)。ONNX 里是 Sigmoid+Mul 两个算子，这里按 §3.4 合并成一个函数。"""
    x = np.asarray(x, dtype=np.float64)
    with np.errstate(over="ignore"):
        return x / (1.0 + np.exp(-x))


def minmax_to_qparams(mn: float, mx: float) -> Dict[str, float]:
    """ORT MinMax 口径：`scale=(max-min)/255`，`zp=clamp(round(-min/scale), 0, 255)`。

    **两种退化必须显式处理**，否则会除以 0 或得到 zp 全错：
    * `max == min`（常数张量）：scale 置 1.0、zp 置 0（张量退化成单点，量化域里它就是一个数）。
    * `min >= 0`（全正张量）：zp 会被 clamp 到 0，这是对的 —— QUInt8 的 0 就是实数 0 附近。
    """
    if not (mx > mn):
        return {"scale": 1.0, "zero_point": 0, "min": float(mn), "max": float(mx),
                "degenerate": True}
    scale = (float(mx) - float(mn)) / 255.0
    if not (scale > 0.0):
        return {"scale": 1.0, "zero_point": 0, "min": float(mn), "max": float(mx),
                "degenerate": True}
    zp = int(np.clip(round_half_away_from_zero(-float(mn) / scale), 0, 255))
    return {"scale": float(scale), "zero_point": zp, "min": float(mn), "max": float(mx),
            "degenerate": False}


def pick_shift(m: float) -> tuple:
    """§3.3：把实数乘子 `m` 抬成 Q31 定点。返回 (M, S)，保证 `M ∈ [2^30, 2^31)`。

    S 的定义：**使 `m*2^S` 落在 [2^30, 2^31) 的最小整数**。于是
        m*2^S ≈ M/2^S*2^S ... 即 requant 用 `acc*M >> S` 实现 `acc*m`，
        M 用满 31 位动态范围（精度最好），S 至少 31（m<1 时更大）。
    m<=0（权重通道全零 -> sw 被置 1.0，理论上不会出现）时退化为 M=0,S=31。
    """
    if not (m > 0.0):
        return 0, 31
    s = 30
    # 逐步抬：m*2^s >= 2^30 时停（最多几十次，不必用 log2 的浮点误差决定整数）
    while m * (1 << s) < (1 << 30):
        s += 1
        if s > 62:
            raise SystemExit("pick_shift: 乘子 %r 过小，定点抬不上去" % (m,))
    M = int(round_half_away_from_zero(m * float(1 << s)))
    if M >= (1 << 31):
        # 四舍五入把 M 顶到 2^31（边界情形）：再抬一位，M 落在 [2^30, 2^31) 内
        s += 1
        M = int(round_half_away_from_zero(m * float(1 << s)))
    if M < (1 << 30):
        raise SystemExit("pick_shift 内部矛盾：M=%d 未落在 [2^30,2^31)" % M)
    return M, s


def build_lut(sp: float, zp_pre: int, so: float, zo: int, act: str) -> np.ndarray:
    """§3.4：`LUT[q] = clamp(round(act((q-zp_pre)*sp)/so) + zo, 0, 255)`，q=0..255。

    恒等层也必须走这条式子（sp≠so 时 LUT 不是恒等映射，跳过就会整体偏移）。
    """
    q = np.arange(256, dtype=np.int64)
    x_f = (q - int(zp_pre)).astype(np.float64) * float(sp)
    y_f = act_silu(x_f) if act == "silu" else x_f
    return quantize_u8(y_f, float(so), int(zo))


# ---------------------------------------------------------------------------
# ONNX 图：只为拿到 (输入张量名, 输出张量名, 权重/偏置初始化器, Conv 属性)
# ---------------------------------------------------------------------------
def load_onnx_facts(onnx_path: Path) -> dict:
    import onnx  # 延迟导入：--help 不该因为没装 onnx 就崩

    m = onnx.load(str(onnx_path))
    inits = {t.name: t for t in m.graph.initializer}
    graph_inputs = {}
    for vi in m.graph.input:
        dims = [d.dim_value for d in vi.type.tensor_type.shape.dim]
        graph_inputs[vi.name] = dims
    graph_outputs = {vi.name for vi in m.graph.output}

    convs = []
    for node in m.graph.node:
        if node.op_type != "Conv":
            continue
        a = {p.name: onnx.helper.get_attribute_value(p) for p in node.attribute}
        convs.append({
            "name": node.name,
            "in": node.input[0],
            "w_init": node.input[1],
            "b_init": node.input[2] if len(node.input) > 2 else None,
            "out": node.output[0],
            "kernel_shape": list(a.get("kernel_shape") or [0, 0]),
            "strides": list(a.get("strides") or [1, 1]),
            "pads": list(a.get("pads") or [0, 0, 0, 0]),
            "dilations": list(a.get("dilations") or [1, 1]),
            "group": int(a.get("group", 1)),
        })
    opsets = [(o.domain or "ai.onnx", o.version) for o in m.opset_import]
    return {"convs": convs, "inits": inits, "graph_inputs": graph_inputs,
            "graph_outputs": graph_outputs, "opsets": opsets,
            "node_count": len(m.graph.node)}


def init_array(inits: dict, name: str) -> np.ndarray:
    t = inits[name]
    from onnx import numpy_helper
    return np.array(numpy_helper.to_array(t))


# ---------------------------------------------------------------------------
# 单层导出
# ---------------------------------------------------------------------------
def export_one(cv: dict, facts: dict, gjson: dict, gnpz, wq, root: Path,
               write_bin: bool, idx: int) -> dict:
    gin = facts["graph_inputs"]
    gt = gjson["tensors"]

    # ---------- 1) 几何：形状全部来自 golden 清单（float32 真跑出来的 shape），
    #                只有图输入 images 从 onnx 图输入取。
    out_key = golden_key(cv["out"])
    in_key = golden_key(cv["in"])
    if out_key not in gt:
        raise SystemExit("%s: 输出张量 %r 不在 golden 清单里" % (cv["name"], out_key))
    oshape = [int(d) for d in gt[out_key]["shape"]]
    if cv["in"] in gin:
        ishape = [int(d) for d in gin[cv["in"]]]
    else:
        if in_key not in gt:
            raise SystemExit("%s: 输入张量 %r 不在 golden 清单里" % (cv["name"], in_key))
        ishape = [int(d) for d in gt[in_key]["shape"]]

    if len(ishape) != 4 or len(oshape) != 4:
        raise SystemExit("%s: 只支持 4 维 NCHW，实际 in=%r out=%r" % (cv["name"], ishape, oshape))
    cin, hin, win = ishape[1], ishape[2], ishape[3]
    cout, hout, wout = oshape[1], oshape[2], oshape[3]
    kh, kw = cv["kernel_shape"]
    if kh != kw:
        raise SystemExit("%s: 非方形核 K=%dx%d 契约 §2 不支持" % (cv["name"], kh, kw))
    k = int(kh)
    sh, sw_ = cv["strides"]
    if sh != sw_:
        raise SystemExit("%s: 非方形 stride %dx%d 契约 §2 不支持" % (cv["name"], sh, sw_))
    stride = int(sh)
    pt, pb, pl, pr = cv["pads"]
    if not (pt == pb == pl == pr):
        raise SystemExit("%s: 非对称 padding %r 契约 §2 不支持" % (cv["name"], cv["pads"]))
    pad = int(pt)
    if cv["dilations"] != [1, 1]:
        raise SystemExit("%s: dilation=%r 契约 §5 不支持" % (cv["name"], cv["dilations"]))
    if cv["group"] != 1:
        raise SystemExit("%s: group=%d（depthwise）契约 §5 不支持" % (cv["name"], cv["group"]))
    exp_h = (hin + 2 * pad - k) // stride + 1
    exp_w = (win + 2 * pad - k) // stride + 1
    if (exp_h, exp_w) != (hout, wout):
        raise SystemExit("%s: 形状不自洽：按公式 H_OUT=%d W_OUT=%d，golden 给 %dx%d"
                         % (cv["name"], exp_h, exp_w, hout, wout))

    # ---------- 2) 权重与 sw：直接用已量化的 int8，不重算
    wkey = layer_key(cv["name"])
    wq_w = wq[wkey + "__w"]
    wq_s = wq[wkey + "__scale"]
    w_i8 = np.asarray(wq_w, dtype=np.int8)
    if w_i8.shape != (cout, cin * k * k):
        raise SystemExit("%s: wq 权重形状 %r != (C_OUT=%d, C_IN*K*K=%d)"
                         % (cv["name"], w_i8.shape, cout, cin * k * k))
    sw = np.asarray(wq_s, dtype=np.float64).reshape(cout)
    if not np.all(sw > 0.0):
        raise SystemExit("%s: sw 出现非正数" % cv["name"])

    # ---------- 3) 输出端 / 激活前端的量化参数 + act
    # conv 输出名形如 `/model.0/conv/Conv_output_0`；SiLU 的 Mul 输出名把
    # `conv/Conv_output_0` 换成 `act/Mul_output_0`（同级目录）。
    out_t = gt[out_key]
    prefix = cv["out"].rsplit("/", 1)[0]           # 如 /model.0/conv
    act_dir = prefix.rsplit("/", 1)[0]             # 如 /model.0
    cand = act_dir + "/act/Mul_output_0"
    if golden_key(cand) in gt and golden_key(cand) in gnpz.files:
        act = "silu"
        post_key = golden_key(cand)
        sp_q = minmax_to_qparams(out_t["min"], out_t["max"])          # 激活前 = conv 输出
        so_q = minmax_to_qparams(gt[post_key]["min"], gt[post_key]["max"])  # 激活后 = Mul 输出
    else:
        act = "none"
        post_key = out_key
        # 无激活：输出张量 == conv 输出张量，两组参数必须相同，LUT 才是恒等映射
        sp_q = so_q = minmax_to_qparams(out_t["min"], out_t["max"])

    # ---------- 4) 输入量化参数
    if cv["in"] in gin:
        x_f = np.random.default_rng(INPUT_SEED).standard_normal(tuple(ishape), dtype=np.float32)
        sx_q = minmax_to_qparams(float(x_f.min()), float(x_f.max()))
        in_src = "synthetic:np.random.default_rng(%d).standard_normal(%r, dtype=float32)" \
                 % (INPUT_SEED, list(ishape))
    else:
        x_f = np.asarray(gnpz[in_key], dtype=np.float16).astype(np.float64)
        sx_q = minmax_to_qparams(gt[in_key]["min"], gt[in_key]["max"])
        in_src = "golden_npz:" + in_key

    sx, zx = float(sx_q["scale"]), int(sx_q["zero_point"])
    sp, zp_pre = float(sp_q["scale"]), int(sp_q["zero_point"])
    so, zo = float(so_q["scale"]), int(so_q["zero_point"])

    # ---------- 5) bias_i32（§3.2 折叠）
    if cv["b_init"] is not None:
        b_f = init_array(facts["inits"], cv["b_init"]).reshape(-1).astype(np.float64)
        if b_f.size != cout:
            raise SystemExit("%s: bias 长度 %d != C_OUT=%d" % (cv["name"], b_f.size, cout))
        has_bias = True
    else:
        b_f = np.zeros(cout, dtype=np.float64)
        has_bias = False
    sum_w = w_i8.astype(np.int64).sum(axis=1)              # SUM_{ci,kh,kw} w_q[c]
    bias_i32 = (round_half_away_from_zero(b_f / (sx * sw)) - zx * sum_w).astype(np.int64)
    if np.any(np.abs(bias_i32) > INT32_MAX):
        raise SystemExit("%s: bias_i32 溢出 int32" % cv["name"])

    # ---------- 6) M/S（§3.3）
    m_real = sx * sw / sp
    MS = [pick_shift(float(m)) for m in m_real]
    M = np.array([a for a, _ in MS], dtype=np.int64)
    S = np.array([b for _, b in MS], dtype=np.int64)

    # ---------- 7) LUT（§3.4）
    lut = build_lut(sp, zp_pre, so, zo, act)

    # ---------- 8) 组装 §2 布局的字节
    w_bytes = np.ascontiguousarray(w_i8).tobytes()
    params_i32 = np.empty((cout, 3), dtype="<i4")
    params_i32[:, 0] = bias_i32.astype("<i4")
    params_i32[:, 1] = M.astype("<i4")
    params_i32[:, 2] = S.astype("<i4")
    params_bytes = params_i32.tobytes()
    lut_bytes = np.ascontiguousarray(lut, dtype=np.uint8).tobytes()

    in_q = quantize_u8(x_f.reshape(cin, hin, win), sx, zx)
    in_bytes = in_q.tobytes()

    if cv["in"] in gin:
        # 首层：输入是图输入，in.bin 就是"喂给硬件的激励"，无 golden 输入张量可比
        in_key_used = None
    else:
        in_key_used = in_key

    # 参考输出（§1 三行），同时给出 csim 的期望
    out_ref, acc_max, q_pre = conv_engine(
        in_q, np.ascontiguousarray(w_i8), bias_i32, M, S, lut,
        zx, zp_pre, stride, pad)
    if out_ref.shape != (cout, hout, wout):
        raise SystemExit("%s: ref 输出形状 %r != %r" % (cv["name"], out_ref.shape, (cout, hout, wout)))
    out_bytes = np.ascontiguousarray(out_ref, dtype=np.uint8).tobytes()

    # ---------- 9) 落盘
    files = {"w": "bin/%s/w.bin" % wkey, "params": "bin/%s/params.bin" % wkey,
             "lut": "bin/%s/lut.bin" % wkey, "in": "bin/%s/in.bin" % wkey,
             "out": "bin/%s/out.bin" % wkey}
    if write_bin:
        d = root / "bin" / wkey
        d.mkdir(parents=True, exist_ok=True)
        for nm, payload in (("w", w_bytes), ("params", params_bytes), ("lut", lut_bytes),
                            ("in", in_bytes), ("out", out_bytes)):
            (d / (nm + ".bin")).write_bytes(payload)

    return {
        "idx": idx,
        "key": wkey,
        "onnx_name": cv["name"],
        "geom": {"C_IN": int(cin), "C_OUT": int(cout), "H_IN": int(hin), "W_IN": int(win),
                 "H_OUT": int(hout), "W_OUT": int(wout), "K": int(k),
                 "stride": int(stride), "pad": int(pad)},
        "quant": {
            "sx": sx, "zx": zx, "sp": sp, "zp_pre": zp_pre, "so": so, "zo": zo,
            "has_bias": bool(has_bias),
            # 逐通道数组**不**抄进 json（在 params.bin 里），只留可核对的摘要
            "M_min": int(M.min()), "M_max": int(M.max()),
            "S_min": int(S.min()), "S_max": int(S.max()),
            "sw_min": float(sw.min()), "sw_max": float(sw.max()),
            "sum_w_min": int(sum_w.min()), "sum_w_max": int(sum_w.max()),
            "bias_i32_min": int(bias_i32.min()), "bias_i32_max": int(bias_i32.max()),
        },
        "act": act,
        "act_check": {
            "post_act_name": cand if act == "silu" else cv["out"],
            "rule": "golden 里存在 <conv_out 同目录>/act/Mul_output_0 => silu；否则 none",
        },
        "sources": {"in": in_src, "weights": "wq:" + wkey,
                    "stats": "golden_json min/max（float32 真值）"},
        "files": files,
        "sha256": {"w": sha256_bytes(w_bytes), "params": sha256_bytes(params_bytes),
                   "lut": sha256_bytes(lut_bytes), "in": sha256_bytes(in_bytes),
                   "out": sha256_bytes(out_bytes)},
        "bytes": {"w": len(w_bytes), "params": len(params_bytes), "lut": len(lut_bytes),
                  "in": len(in_bytes), "out": len(out_bytes)},
        "ref": {"acc_max_abs": int(acc_max),
                "acc_int32_pct": float(100.0 * acc_max / INT32_MAX),
                "q_pre_min": int(q_pre.min()), "q_pre_max": int(q_pre.max()),
                "out_max": int(out_ref.max()), "out_nonzero": int(np.count_nonzero(out_ref))},
        "golden": {"input": in_key_used, "pre_act": out_key, "post_act": post_key},
    }


def range_violations(g: dict) -> List[str]:
    """对照契约 §5 逐条检查；返回人类可读的不支持原因（空 = 支持）。"""
    bad = []
    if g["K"] not in LIM_K:
        bad.append("K=%d 不在 %r" % (g["K"], list(LIM_K)))
    if g["stride"] not in LIM_STRIDE:
        bad.append("stride=%d 不在 %r" % (g["stride"], list(LIM_STRIDE)))
    if g["pad"] not in LIM_PAD:
        bad.append("pad=%d 不在 %r" % (g["pad"], list(LIM_PAD)))
    if not (LIM_CIN[0] <= g["C_IN"] <= LIM_CIN[1]):
        bad.append("C_IN=%d 不在 %r" % (g["C_IN"], list(LIM_CIN)))
    if not (LIM_COUT[0] <= g["C_OUT"] <= LIM_COUT[1]):
        bad.append("C_OUT=%d 不在 %r" % (g["C_OUT"], list(LIM_COUT)))
    for nm in ("H_IN", "W_IN"):
        if not (LIM_HW[0] <= g[nm] <= LIM_HW[1]):
            bad.append("%s=%d 不在 %r" % (nm, g[nm], list(LIM_HW)))
    return bad


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="导出 yolov8n_320 的逐层 INT8 参数包（契约 %s §2/§3/§5）" % CONTRACT)
    ap.add_argument("--onnx", default=str(DEFAULT_ONNX))
    ap.add_argument("--wq", default=str(DEFAULT_WQ))
    ap.add_argument("--golden-json", default=str(DEFAULT_GOLDEN_JSON))
    ap.add_argument("--golden-npz", default=str(DEFAULT_GOLDEN_NPZ))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--no-bin", action="store_true", help="只写清单，不落 .bin")
    args = ap.parse_args(argv)

    onnx_path, wq_path = Path(args.onnx), Path(args.wq)
    gj_path, gn_path = Path(args.golden_json), Path(args.golden_npz)
    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = REPO / out_dir

    for p, what in ((onnx_path, "onnx 模型"), (wq_path, "量化权重 npz"),
                    (gj_path, "golden 清单"), (gn_path, "golden npz")):
        if not p.is_file():
            raise SystemExit("找不到%s：%s" % (what, p))

    print("=" * 78)
    print("export_layers.py -- 逐层 INT8 参数包导出")
    print("  契约      : %s §2(布局) / §3(定点) / §5(范围)" % CONTRACT)
    print("  onnx      : %s" % onnx_path)
    print("  量化权重  : %s" % wq_path)
    print("  golden    : %s" % gj_path)
    print("  输出目录  : %s" % out_dir)
    print("=" * 78)

    facts = load_onnx_facts(onnx_path)
    gjson = json.loads(gj_path.read_text(encoding="utf-8"))
    gnpz = np.load(str(gn_path))
    wq = np.load(str(wq_path))

    print("  onnx 节点 %d 个 / Conv %d 个 / opset %r"
          % (facts["node_count"], len(facts["convs"]), facts["opsets"]))
    print("  golden 张量 %d 个（%s）/ wq 键 %d 个（%d conv × 2）"
          % (len(gjson["tensors"]), gjson.get("stored_dtype"), len(wq.files),
             len(facts["convs"])))
    n_wq = sum(1 for c in facts["convs"] if layer_key(c["name"]) + "__w" in wq.files)
    if n_wq != len(facts["convs"]):
        raise SystemExit("wq.npz 只覆盖 %d/%d 个 Conv" % (n_wq, len(facts["convs"])))

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "bin").mkdir(parents=True, exist_ok=True)

    layers: List[dict] = []
    unsupported: List[dict] = []
    for cv in facts["convs"]:
        try:
            rec = export_one(cv, facts, gjson, gnpz, wq, out_dir, not args.no_bin, len(layers))
        except SystemExit:
            raise
        bad = range_violations(rec["geom"])
        if bad:
            rec["unsupported"] = True
            rec["reason"] = "; ".join(bad)
            rec["note"] = ("算术本身合法（K/stride/pad 都合规），但 H/W/C 超契约 §5；"
                           "硬件不跑它，PS 侧用 numpy 算。这些 .bin 是给 PS 实现当比对基准的。")
            # .bin 已在 export_one 里写出，只是不放进 layers[]（硬件不会跑它）
            unsupported.append(rec)
            print("  [skip] %-44s 超范围：%s" % (rec["onnx_name"][:44], rec["reason"]))
            continue
        layers.append(rec)

    # 重新编号：layers[] 用 0..N-1 连续（ref --all 与 Lead 的按序循环都依赖这个）；
    # unsupported[] 单独从 0 编号，两者不混用同一 idx 空间。
    for i, rec in enumerate(layers):
        rec["idx"] = i
    for i, rec in enumerate(unsupported):
        rec["idx"] = i

    # ---- 汇总
    def tot(key: str) -> int:
        return sum(r["bytes"][key] for r in layers) + sum(r["bytes"][key] for r in unsupported)

    sums = {k: tot(k) for k in ("w", "params", "lut", "in", "out")}
    sums["all"] = sum(sums.values())
    n_act = {a: sum(1 for r in layers if r["act"] == a) for a in ("silu", "none")}

    manifest = {
        "schema": SCHEMA,
        "generated_by": "tools/export_layers.py",
        "contract": {"doc": CONTRACT, "sections": ["§2 布局", "§3 定点", "§5 范围"]},
        "source": {
            "onnx": {"path": onnx_path.relative_to(REPO).as_posix() if onnx_path.is_relative_to(REPO) else str(onnx_path),
                     "sha256": sha256_file(onnx_path), "nodes": facts["node_count"],
                     "convs": len(facts["convs"]), "opsets": [list(o) for o in facts["opsets"]],
                     "graph_inputs": {k: v for k, v in facts["graph_inputs"].items()}},
            "quant_weights": {"path": wq_path.relative_to(REPO).as_posix() if wq_path.is_relative_to(REPO) else str(wq_path),
                              "sha256": sha256_file(wq_path), "keys": len(wq.files)},
            "golden_json": {"path": gj_path.relative_to(REPO).as_posix() if gj_path.is_relative_to(REPO) else str(gj_path),
                            "sha256": sha256_file(gj_path), "tensors": len(gjson["tensors"])},
            "golden_npz_sha256": gjson.get("npz_sha256", ""),
            "input_seed": INPUT_SEED,
        },
        "calibration": {
            "activation": "per-tensor QUInt8",
            "stats_source": "data/golden/*_golden.json 的 min/max（float32 真实激活范围）",
            "formula": "scale=(max-min)/255; zero_point=clamp(round(-min/scale),0,255)",
            "rounding": "round-half-away-from-zero（float64 算术）",
            "known_gap": ("契约 §3 只规定怎么用 sx/zx/sp/zp_pre/so/zo，没规定怎么算它们的值；"
                          "本清单采用上面这条 ORT MinMax 口径。若改了口径，M/S/bias_i32/lut 全变，"
                          "必须重跑 ref_int8_conv.py 的逐位比对。"),
            "note": ("ORT 的 quantize_static 标定在 4 个 head 张量上给出的 scale 与本口径不同"
                     "（cv3.0/cv3.1/cv3.2 的 Conv 输出与 dfl Conv 输出），原因是标定期取到的 range "
                     "与 float 实跑的 min/max 不一致；本清单一律以 golden 的 min/max 为准。"),
        },
        "layout": {
            "endianness": "little",
            "w": "int8 [C_OUT][C_IN][K][K]，内层按 kh*K+kw",
            "params": "int32 [C_OUT][3] = {bias_i32, M, S}，第 c 通道在 params[3c..3c+2]",
            "lut": "uint8 [256]",
            "in": "uint8 [C_IN][H_IN][W_IN]",
            "out": "uint8 [C_OUT][H_OUT][W_OUT]",
            "padding": "零 padding 在 uint8 域填 zp_in（不是填 0），PL 越界读 zp_in",
        },
        "totals": {
            "conv_total": len(facts["convs"]),
            "layers": len(layers),
            "unsupported": len(unsupported),
            "act_silu": n_act["silu"],
            "act_none": n_act["none"],
            "bytes": sums,
        },
        "layers": layers,
        "unsupported": unsupported,
    }

    man_path = out_dir / "yolov8n_320_layers.json"
    with open(man_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")

    print("")
    print("-" * 78)
    print("  Conv 总数        : %d  =>  可上硬件 %d 层 + 超范围 %d 层"
          % (len(facts["convs"]), len(layers), len(unsupported)))
    print("  激活分布         : silu %d 层 / none %d 层" % (n_act["silu"], n_act["none"]))
    print("  字节数           : w=%d  params=%d  lut=%d  in=%d  out=%d  合计=%d (%.2f MB)"
          % (sums["w"], sums["params"], sums["lut"], sums["in"], sums["out"], sums["all"],
             sums["all"] / 1048576.0))
    print("  权重字节          : %d (%.4f MB)" % (sums["w"], sums["w"] / 1048576.0))
    mx = max(layers, key=lambda r: r["ref"]["acc_max_abs"])
    print("  累加器最大绝对值  : %d (%.2f%% of int32)  @ %s"
          % (mx["ref"]["acc_max_abs"], mx["ref"]["acc_int32_pct"], mx["key"]))
    print("  清单             : %s (%.1f KB)"
          % (man_path, man_path.stat().st_size / 1024.0))
    if unsupported:
        for r in unsupported:
            print("  unsupported      : %-40s %s" % (r["onnx_name"][:40], r["reason"]))
    print("")
    print("EXPORT_LAYERS_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
