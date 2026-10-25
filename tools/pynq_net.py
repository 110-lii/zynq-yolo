#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""tools/pynq_net.py -- YOLOv8n@320 整网推理的 PC 侧编排（后端可插拔）。

为什么需要它
============
PL 侧只做一件事：无状态卷积内核 `conv_engine`（一次算一层）。层序、MaxPool /
Concat / Resize / Split、DFL 解码、内存摆放全部由 PS 负责（契约
`docs/accel_interface.md` §1）。本文件就是那「PS 那一半」在 PC 上的可执行版本：

    Conv   -> 交给 backend.conv(...)      （现在 = numpy 直接算 INT8；将 = PL）
    其余   -> 本文件用 numpy 实现          （Dequantize -> float32 -> Quantize）

队列就绪后，只换一个后端类即可（`NumpyBackend` -> `PlBackend`），网络结构、
量化参数、算子实现一行都不用改。这是本文件存在的唯一理由。

数值口径（与 tools/int8_sim.py 完全一致，那是已经验证过的方案）
-------------------------------------------------------------
    Conv    : uint8 激活 × int8 权重 -> int32 累加 -> per-channel requant -> uint8 输出
    其余算子 : float32（读 uint8 时反量化，写 uint8 时量化）
    bias    : 已按契约 §3.2 折叠 (-zx*Σw)
    requant : M (int32, Q31) * 2^-S，64 位中间量（契约 §3.3）
    LUT     : 契约 §3.4 的生成式（本版 act="none"，因为 ONNX 图里 SiLU 是
              独立的 Sigmoid+Mul 两个节点，融合需要跳节点，v1 不做）

三个模式
--------
    --check        整网 INT8 跑一遍，与 ORT float32 参考 + golden 比对，末行令牌
                   PYNQ_NET_OK / PYNQ_NET_FAIL。判据：cosine >= 0.999 且检测框一致。
    --check-ops    纯 float32 路径跑一遍，逐个算子输出与 golden（270 个张量）比对。
                   这是「17 种算子的语义都实现对了」的证明；12 种非卷积算子出错
                   在这一步暴露得最清楚（Resize/Split/Softmax/Slice 是经典坑）。
    --plan         导出逐层量化参数包（对应 tools/export_layers.py 的口径预览）。
    --backend pl --dry-run
                   不碰板子，只按契约 §5 判定「哪些层能送 PL、哪些必须 numpy
                   兜底」，证明后端可插拔。

诚实限制（不写就变成吹牛）
--------------------------
  * 激活 scale/zp 一律从 `data/golden/yolov8n_320_golden.npz` 的**实测范围**取
    （= 校准集只有一张图）。这是「同分布上界」，不是生产可用的标定。真要上板，
    换 tools/export_layers.py 的标定集。
  * requant 舍入用「+2^(S-1) 再算术右移」（HLS 友好），与 ORT 的 float 舍入差
    ~1 LSB —— 这也是 tools/int8_sim.py 里已经写明的同一条限制。
  * `PlBackend` 只有骨架：所有 PYNQ / 寄存器细节一律 `<待实测>`，不许臆造。

用法
----
    & D:\\python\\python.exe tools/pynq_net.py --check
    & D:\\python\\python.exe tools/pynq_net.py --check-ops
    & D:\\python\\python.exe tools/pynq_net.py --plan
    & D:\\python\\python.exe tools/pynq_net.py --backend pl --dry-run
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # 必须在任何本地 import 之前：否则 __pycache__ 会让 check d) 失败

import argparse
import dataclasses
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

REPO = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = REPO / "data" / "models" / "yolov8n_320.onnx"
DEFAULT_GOLDEN = REPO / "data" / "golden" / "yolov8n_320_golden.npz"
DEFAULT_INPUT_SEED = 20261019
OUT_DIR = REPO / "data" / "results"

# 契约 docs/accel_interface.md §5 的 v1 层范围（超出的层必须 PS/numpy 兜底）
PL_K_ALLOWED = (1, 3)
PL_STRIDE_ALLOWED = (1, 2)
PL_PAD_ALLOWED = (0, 1)
PL_CIN_RANGE = (3, 512)
PL_COUT_RANGE = (16, 256)
PL_HW_RANGE = (10, 320)

# --check 的判据（写死在这里，免得"通过"变成随口一说）
THRESH_COSINE = 0.999
THRESH_BOX_PX = 3.0
THRESH_SCORE = 0.01


# ---------------------------------------------------------------------------
# 1. 图加载（只依赖 onnx，不依赖 onnxruntime 的图解释器 —— 我们要自己解释）
# ---------------------------------------------------------------------------
@dataclasses.dataclass
class Node:
    idx: int
    op_type: str
    name: str
    inputs: List[str]
    outputs: List[str]
    attrs: Dict[str, Any]


@dataclasses.dataclass
class Graph:
    path: Path
    nodes: List[Node]
    initializers: Dict[str, np.ndarray]  # 含 Constant 节点的值，统一按名字取
    input_name: str
    input_shape: List[int]
    output_names: List[str]
    opset: int
    shapes: Dict[str, Tuple[int, ...]] = dataclasses.field(default_factory=dict)

    def op_counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for n in self.nodes:
            out[n.op_type] = out.get(n.op_type, 0) + 1
        return out


def _chw_shape_dims(shape: Sequence[int]) -> Tuple[int, int, int]:
    """(C,H,W)：接受 [C,H,W] 或 [1,C,H,W]（batch 维恒为 1）。"""
    if len(shape) == 4:
        return int(shape[1]), int(shape[2]), int(shape[3])
    return int(shape[0]), int(shape[1]), int(shape[2])


def _attr_value(attr) -> Any:
    import onnx

    t = attr.type
    if t == onnx.AttributeProto.INT:
        return int(attr.i)
    if t == onnx.AttributeProto.FLOAT:
        return float(attr.f)
    if t == onnx.AttributeProto.STRING:
        return attr.s.decode("utf-8")
    if t == onnx.AttributeProto.INTS:
        return [int(v) for v in attr.ints]
    if t == onnx.AttributeProto.FLOATS:
        return [float(v) for v in attr.floats]
    if t == onnx.AttributeProto.STRINGS:
        return [s.decode("utf-8") for s in attr.strings]
    if t == onnx.AttributeProto.TENSOR:
        from onnx import numpy_helper

        return numpy_helper.to_array(attr.t)
    return None


def load_graph(path: Path) -> Graph:
    import onnx
    from onnx import numpy_helper

    model = onnx.load(str(path))
    init = {i.name: numpy_helper.to_array(i) for i in model.graph.initializer}

    nodes: List[Node] = []
    for idx, n in enumerate(model.graph.node):
        attrs = {a.name: _attr_value(a) for a in n.attribute}
        nodes.append(
            Node(
                idx=idx,
                op_type=n.op_type,
                name=n.name,
                inputs=[x for x in n.input],
                outputs=[x for x in n.output],
                attrs=attrs,
            )
        )
        # Constant 节点的值并进 initializers：解释器里 get() 只查一个地方
        if n.op_type == "Constant" and n.output:
            v = attrs.get("value")
            if v is None:
                raise RuntimeError("Constant 节点没有 value 属性：%s" % n.name)
            init[n.output[0]] = np.asarray(v)

    inp = model.graph.input[0]
    shape = [int(d.dim_value) for d in inp.type.tensor_type.shape.dim]
    opset = 0
    for o in model.opset_import:
        if o.domain in ("", "ai.onnx"):
            opset = int(o.version)

    # 形状传播：自己算 / 靠 golden 都不如让 onnx 推断一次。建 plan 时要用每层
    # 卷积的 [C,H,W]，这是「几何」不是「数值」，与输入内容无关（batch 固定 1）。
    shapes: Dict[str, Tuple[int, ...]] = {}
    try:
        inferred = onnx.shape_inference.infer_shapes(model)
        for vi in list(inferred.graph.value_info) + list(inferred.graph.output):
            dims = vi.type.tensor_type.shape.dim
            shapes[vi.name] = tuple(int(d.dim_value) if d.HasField("dim_value") else -1
                                    for d in dims)
    except Exception as exc:  # noqa: BLE001 - 推断失败不致命，调用方会退回 golden 形状
        print("WARNING: onnx 形状推断失败（%s），几何退回 golden 形状" % exc)
    shapes[inp.name] = tuple(shape)

    return Graph(
        path=path,
        nodes=nodes,
        initializers=init,
        input_name=inp.name,
        input_shape=shape,
        output_names=[o.name for o in model.graph.output],
        opset=opset,
        shapes=shapes,
    )


# ---------------------------------------------------------------------------
# 2. 非卷积算子（numpy 实现）—— 12 种 + Constant
#    约定：float 张量一律 float32；形状/索引张量一律 int64（与 ORT 一致）
# ---------------------------------------------------------------------------
F32 = np.float32
I64 = np.int64


def _as_f32(a: np.ndarray) -> np.ndarray:
    return a if a.dtype == F32 else a.astype(F32)


def _binop(kind: str, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """ONNX 的 Add/Sub/Mul/Div：整数走 int64（Div 为向零截断），浮点走 float32。"""
    a = np.asarray(a)
    b = np.asarray(b)
    int_a = np.issubdtype(a.dtype, np.integer)
    int_b = np.issubdtype(b.dtype, np.integer)
    if int_a and int_b:
        a64 = a.astype(I64)
        b64 = b.astype(I64)
        if kind == "Div":
            # ONNX 规范：整数 Div 向零截断（numpy // 是向下取整，负半边不一样）
            return np.trunc(a64.astype(np.float64) / b64.astype(np.float64)).astype(I64)
        return {"Add": np.add, "Sub": np.subtract, "Mul": np.multiply}[kind](a64, b64)
    a32 = _as_f32(a)
    b32 = _as_f32(b)
    return {"Add": np.add, "Sub": np.subtract, "Mul": np.multiply, "Div": np.divide}[kind](a32, b32)


def _sliding_window_view(x: np.ndarray, window_shape, axis):
    """numpy<1.20 compatibility shim for np.lib.stride_tricks.sliding_window_view.

    The board image ships numpy 1.17.4 (Python 3.8), where the native helper does
    not exist.  The as_strided fallback reproduces the native layout: window axes
    are moved to the end, followed by the window dimensions.
    """
    native = getattr(np.lib.stride_tricks, "sliding_window_view", None)
    if native is not None:
        return native(x, window_shape, axis=axis)

    x = np.asarray(x)
    window_shape = tuple(window_shape) if isinstance(window_shape, (tuple, list)) else (int(window_shape),)
    axis = tuple(axis) if isinstance(axis, (tuple, list)) else (int(axis),)
    if len(window_shape) != len(axis):
        raise ValueError("window_shape and axis must have the same length")
    axis = tuple(int(a) % x.ndim for a in axis)
    if len(set(axis)) != len(axis):
        raise ValueError("duplicate axis")
    other = [i for i in range(x.ndim) if i not in axis]
    perm = tuple(other + list(axis))
    y = np.transpose(x, perm)
    n_other = len(other)
    out_shape = list(y.shape[:n_other])
    strides = list(y.strides[:n_other])
    for i, win in enumerate(window_shape):
        dim = y.shape[n_other + i]
        if win > dim:
            raise ValueError("window larger than dimension")
        out_shape.append(dim - win + 1)
        strides.append(y.strides[n_other + i])
    out_shape.extend(window_shape)
    strides.extend(y.strides[n_other + i] for i in range(len(window_shape)))
    return np.lib.stride_tricks.as_strided(
        y, shape=tuple(out_shape), strides=tuple(strides), writeable=False)


def _op_constant(node: Node, rt: "Runtime") -> np.ndarray:
    return rt.graph.initializers[node.outputs[0]]


def _op_sigmoid(node: Node, rt: "Runtime") -> np.ndarray:
    x = _as_f32(rt.get(node.inputs[0]))
    # 数值稳定写法：x 很负时 exp(-x) 会 overflow 成 inf（虽然 1/(1+inf)=0 是对的，
    # 但会刷 RuntimeWarning，而且 float16 golden 里那一侧本来也是 0）。
    out = np.empty_like(x)
    pos = x >= 0
    out[pos] = F32(1.0) / (F32(1.0) + np.exp(-x[pos]))
    e = np.exp(x[~pos])
    out[~pos] = e / (F32(1.0) + e)
    return out


def _op_add(node: Node, rt: "Runtime") -> np.ndarray:
    return _binop("Add", rt.get(node.inputs[0]), rt.get(node.inputs[1]))


def _op_sub(node: Node, rt: "Runtime") -> np.ndarray:
    return _binop("Sub", rt.get(node.inputs[0]), rt.get(node.inputs[1]))


def _op_mul(node: Node, rt: "Runtime") -> np.ndarray:
    return _binop("Mul", rt.get(node.inputs[0]), rt.get(node.inputs[1]))


def _op_div(node: Node, rt: "Runtime") -> np.ndarray:
    return _binop("Div", rt.get(node.inputs[0]), rt.get(node.inputs[1]))


def _op_pow(node: Node, rt: "Runtime") -> np.ndarray:
    x = _as_f32(rt.get(node.inputs[0]))
    y = _as_f32(rt.get(node.inputs[1]))
    return np.power(x, y).astype(F32)


def _op_concat(node: Node, rt: "Runtime") -> np.ndarray:
    axis = int(node.attrs.get("axis", 0))
    xs = [rt.get(n) for n in node.inputs]
    if np.issubdtype(xs[0].dtype, np.integer):
        xs = [x.astype(I64) for x in xs]
    else:
        xs = [_as_f32(x) for x in xs]
    return np.concatenate(xs, axis=axis)


def _op_split(node: Node, rt: "Runtime") -> List[np.ndarray]:
    x = rt.get(node.inputs[0])
    axis = int(node.attrs.get("axis", 0))
    split = node.attrs.get("split", None)
    if split is None and len(node.inputs) > 1:
        # opset 12: split sizes live in the second input (initializer).
        split = rt.get(node.inputs[1])
    if split is None:
        n = len(node.outputs)
        if x.shape[axis] % n != 0:
            raise RuntimeError("Split 无法均分：%s axis=%d size=%d n=%d"
                               % (node.name, axis, x.shape[axis], n))
        return list(np.split(x, n, axis=axis))
    # ★ ONNX 的 Split.split 是「每段长度」，np.split 收的是「切分点」—— 差一层 cumsum。
    #   直接 np.split(x, sizes) 会静默切错（本模型 8 个 Split 全是 [16,16] 这类等长对，
    #   所以形状看起来还对，只有数值错，属于最难查的一类）。
    sizes = [int(v) for v in split]
    if sum(sizes) != x.shape[axis]:
        raise RuntimeError("Split 长度和 %d != 轴长 %d：%s" % (sum(sizes), x.shape[axis], node.name))
    return list(np.split(x, [int(v) for v in np.cumsum(sizes)[:-1]], axis=axis))


def _op_reshape(node: Node, rt: "Runtime") -> np.ndarray:
    x = rt.get(node.inputs[0])
    raw = np.atleast_1d(rt.get(node.inputs[1])).astype(I64)
    shape = [int(v) for v in raw]
    out_shape: List[int] = []
    for i, v in enumerate(shape):
        if v == 0:  # ONNX: 0 = 沿用输入该维
            out_shape.append(int(x.shape[i]))
        else:
            out_shape.append(v)
    return x.reshape(out_shape)


def _op_maxpool(node: Node, rt: "Runtime") -> np.ndarray:
    x = _as_f32(rt.get(node.inputs[0]))
    k = [int(v) for v in node.attrs["kernel_shape"]]
    s = [int(v) for v in node.attrs.get("strides", [1] * len(k))]
    pads = [int(v) for v in node.attrs.get("pads", [0] * (2 * len(k)))]
    dil = [int(v) for v in node.attrs.get("dilations", [1] * len(k))]
    if dil != [1, 1]:
        raise NotImplementedError("MaxPool dilations != 1 未实现：%s" % node.name)
    ceil_mode = int(node.attrs.get("ceil_mode", 0))
    if ceil_mode:
        raise NotImplementedError("MaxPool ceil_mode=1 未实现：%s" % node.name)
    phb, pw, phe, pwe = pads[0], pads[1], pads[2], pads[3]
    hax, wax = x.ndim - 2, x.ndim - 1  # 支持 [C,H,W] 与 [1,C,H,W] 两种排布
    pad_width = [(0, 0)] * x.ndim
    pad_width[hax] = (phb, phe)
    pad_width[wax] = (pw, pwe)
    # ONNX MaxPool 越界按 -inf 处理（不能填 0：会吃掉负激活）
    xp = np.pad(x, pad_width, constant_values=F32(-np.inf))
    v = _sliding_window_view(xp, (k[0], k[1]), axis=(hax, wax))
    ho = (xp.shape[hax] - k[0]) // s[0] + 1
    wo = (xp.shape[wax] - k[1]) // s[1] + 1
    v = v[..., : (ho - 1) * s[0] + 1: s[0], : (wo - 1) * s[1] + 1: s[1], :, :]
    return v.max(axis=(-2, -1)).astype(F32)


def _op_resize(node: Node, rt: "Runtime") -> np.ndarray:
    x = _as_f32(rt.get(node.inputs[0]))
    mode = node.attrs.get("mode", "nearest")
    coord = node.attrs.get("coordinate_transformation_mode", "half_pixel")
    nearest_mode = node.attrs.get("nearest_mode", "round_prefer_floor")
    # Resize-11+ 输入顺序：X, roi, scales[, sizes]
    scales = None if len(node.inputs) < 3 or node.inputs[2] == "" else rt.get(node.inputs[2])
    sizes = None if len(node.inputs) < 4 or node.inputs[3] == "" else rt.get(node.inputs[3])
    if sizes is not None and np.asarray(sizes).size > 0:
        target = [int(v) for v in np.atleast_1d(sizes)]
        sc = [target[i] / x.shape[i] for i in range(len(target))]
    else:
        sc = [float(v) for v in np.atleast_1d(scales)]
    if len(sc) != x.ndim:
        raise RuntimeError("Resize scales 维数不符：%s %s vs %s" % (node.name, sc, x.shape))
    hax, wax = x.ndim - 2, x.ndim - 1  # 支持 [C,H,W] 与 [1,C,H,W] 两种排布
    sc_h, sc_w = sc[hax], sc[wax]
    out_h = int(np.floor(x.shape[hax] * sc_h))
    out_w = int(np.floor(x.shape[wax] * sc_w))
    if mode != "nearest":
        raise NotImplementedError("Resize mode=%s 未实现（本模型只有 nearest）" % mode)
    if coord != "asymmetric":
        raise NotImplementedError(
            "Resize coordinate_transformation_mode=%s 未实现（本模型是 asymmetric；"
            "half_pixel 的取整规则不同，别混）" % coord)
    if nearest_mode != "floor":
        raise NotImplementedError("Resize nearest_mode=%s 未实现（本模型是 floor）" % nearest_mode)
    # asymmetric + floor: x_orig = x_out / scale, index = floor(x_orig)，再夹到边界
    # np.intp, not int64: the 32-bit Zynq ARM has intp=int32 and numpy<1.20
    # rejects a "safe" int64->int32 cast in np.take().
    ih = np.floor(np.arange(out_h, dtype=np.float64) / sc_h).astype(np.intp)
    iw = np.floor(np.arange(out_w, dtype=np.float64) / sc_w).astype(np.intp)
    ih = np.clip(ih, 0, x.shape[hax] - 1)
    iw = np.clip(iw, 0, x.shape[wax] - 1)
    return np.take(np.take(x, ih, axis=hax), iw, axis=wax)


def _op_transpose(node: Node, rt: "Runtime") -> np.ndarray:
    x = rt.get(node.inputs[0])
    perm = node.attrs.get("perm", None)
    return np.transpose(x, perm if perm is not None else None)


def _op_softmax(node: Node, rt: "Runtime") -> np.ndarray:
    x = _as_f32(rt.get(node.inputs[0]))
    axis = int(node.attrs.get("axis", -1))
    m = np.max(x, axis=axis, keepdims=True)
    e = np.exp(x - m)
    return (e / np.sum(e, axis=axis, keepdims=True)).astype(F32)


def _op_shape(node: Node, rt: "Runtime") -> np.ndarray:
    x = rt.get(node.inputs[0])
    return np.array(x.shape, dtype=I64)


def _op_gather(node: Node, rt: "Runtime") -> np.ndarray:
    x = rt.get(node.inputs[0])
    idx = np.atleast_1d(rt.get(node.inputs[1])).astype(np.intp)
    axis = int(node.attrs.get("axis", 0))
    return np.take(x, idx, axis=axis)


def _op_slice(node: Node, rt: "Runtime") -> np.ndarray:
    x = rt.get(node.inputs[0])
    starts = np.atleast_1d(rt.get(node.inputs[1])).astype(I64)
    ends = np.atleast_1d(rt.get(node.inputs[2])).astype(I64)
    axes = (np.atleast_1d(rt.get(node.inputs[3])).astype(I64)
            if len(node.inputs) > 3 and node.inputs[3] != "" else np.arange(starts.size, dtype=I64))
    steps = (np.atleast_1d(rt.get(node.inputs[4])).astype(I64)
             if len(node.inputs) > 4 and node.inputs[4] != "" else np.ones(starts.size, dtype=I64))
    slicers: List[slice] = [slice(None)] * x.ndim
    for j in range(starts.size):
        ax = int(axes[j])
        dim = x.shape[ax]
        st = int(starts[j])
        en = int(ends[j])
        sp = int(steps[j])
        if st < 0:
            st += dim
        if en < 0:
            en += dim
        # ONNX 允许 INT64_MAX 表示“到末尾”
        if en > dim:
            en = dim
        st = max(0, min(st, dim))
        slicers[ax] = slice(st, en, sp)
    return x[tuple(slicers)]


OP_IMPL = {
    "Constant": _op_constant,
    "Sigmoid": _op_sigmoid,
    "Add": _op_add,
    "Sub": _op_sub,
    "Mul": _op_mul,
    "Div": _op_div,
    "Pow": _op_pow,
    "Concat": _op_concat,
    "Split": _op_split,
    "Reshape": _op_reshape,
    "MaxPool": _op_maxpool,
    "Resize": _op_resize,
    "Transpose": _op_transpose,
    "Softmax": _op_softmax,
    "Shape": _op_shape,
    "Gather": _op_gather,
    "Slice": _op_slice,
}


# ---------------------------------------------------------------------------
# 3. 卷积：numpy 的 float32 / INT8 两种实现（INT8 严格照契约 §1/§2/§3）
# ---------------------------------------------------------------------------
def _im2col(x: np.ndarray, k: int, sh: int, sw: int,
            phb: int, phe: int, pw: int, pwe: int,
            pad_value, dtype) -> Tuple[np.ndarray, int, int]:
    """NCHW 单张量 -> [C*K*K, Ho*Wo] 列矩阵（行主序 ci,kh,kw 与外层权重展平一致）。"""
    c, h, w = x.shape
    xp = np.pad(x, ((0, 0), (phb, phe), (pw, pwe)), constant_values=pad_value)
    ho = (xp.shape[1] - k) // sh + 1
    wo = (xp.shape[2] - k) // sw + 1
    v = _sliding_window_view(xp, (k, k), axis=(1, 2))
    v = v[:, : (ho - 1) * sh + 1: sh, : (wo - 1) * sw + 1: sw, :, :]
    cols = np.ascontiguousarray(np.transpose(v, (0, 3, 4, 1, 2)), dtype=dtype)
    return cols.reshape(c * k * k, ho * wo), ho, wo


def _chw(x: np.ndarray) -> np.ndarray:
    """整网内部统一按 [1,C,H,W] 走（batch=1）。卷积核只吃 3D，这里剥掉 batch 维。

    为什么坚持 4D：ONNX 图里 Split/Concat 的 axis=1 是**通道**轴，Reshape 的目标
    形状也带 batch。把 batch 维丢掉、再手工把 axis 1 映射到 0，会在 8 个 Split 和
    17 个 Concat 上到处埋雷。
    """
    if x.ndim == 4:
        if x.shape[0] != 1:
            raise NotImplementedError("只支持 batch=1，实际 %s" % (x.shape,))
        return x[0]
    if x.ndim == 3:
        return x
    raise NotImplementedError("卷积输入应为 3D/4D，实际 %s" % (x.shape,))


def float_conv(xf: np.ndarray, layer: "LayerPlan") -> np.ndarray:
    """纯 float32 卷积（只用于 --check-ops：证明算子语义/几何正确）。"""
    x3 = _chw(xf)
    cols, ho, wo = _im2col(x3, layer.k, layer.stride, layer.stride,
                           layer.pad_h_begin, layer.pad_h_end,
                           layer.pad_w_begin, layer.pad_w_end, F32(0.0), F32)
    w2d = layer.w_f.reshape(layer.c_out, -1)
    acc = w2d @ cols
    if layer.bias_f is not None:
        acc = acc + layer.bias_f[:, None].astype(F32)
    out = acc.reshape(layer.c_out, ho, wo).astype(F32)
    return out[None] if xf.ndim == 4 else out


def int8_conv(xq: np.ndarray, layer: "LayerPlan") -> np.ndarray:
    """契约 §1 的三行公式，int32 累加 + int64 requant，逐位可核。

    xq 已是 uint8（无符号激活），w_q 是 int8（有符号权重），所以：
        acc[c] = Σ xq*wq + bias_i32[c]      （bias 已折叠 -zx*Σw，见契约 §3.2）
        q_pre  = clamp(round(acc*M*2^-S) + zp_pre, 0, 255)
        q_out  = LUT[q_pre]
    """
    if xq.dtype != np.uint8:
        raise TypeError("int8_conv 的输入必须是 uint8（QUInt8 激活），实际 %s" % xq.dtype)
    x3 = _chw(xq)
    taps = int(layer.c_in) * int(layer.k) * int(layer.k)
    # ★ 累加精度选择：int64 matmul 在 A9 上不给 BLAS 用，首层 6×6（taps=108，
    #   74.7M MAC）实测 5.75 s。float32 的每个部分和只要绝对值 < 2^24 就是
    #   **精确整数**（乘积 ≤ 255×128 = 32640 可精确表示；和是整数且不越界），
    #   加法顺序无关 ⇒ 与 int64 逐位等价，但能走 BLAS。
    #   判据：2 × taps × 255 × 128 < 2^24（bias 与累加同量级，留一倍余量）。
    if 2 * taps * 255 * 128 < (1 << 24):
        cols, ho, wo = _im2col(x3, layer.k, layer.stride, layer.stride,
                               layer.pad_h_begin, layer.pad_h_end,
                               layer.pad_w_begin, layer.pad_w_end,
                               np.uint8(layer.zp_in), np.float32)
        acc = layer.w_q.astype(np.float32) @ cols
        acc = acc + layer.bias_i32.astype(np.float32)[:, None]
        acc = acc.astype(I64)          # 已是精确整数，等价于 int64 累加结果
    else:
        cols, ho, wo = _im2col(x3, layer.k, layer.stride, layer.stride,
                               layer.pad_h_begin, layer.pad_h_end,
                               layer.pad_w_begin, layer.pad_w_end, np.uint8(layer.zp_in), I64)
        acc = layer.w_q.astype(I64) @ cols  # [C_OUT, Ho*Wo] int64（矩阵乘法精确，无溢出担忧）
        acc = acc + layer.bias_i32.astype(I64)[:, None]
    # requant: round(acc * M * 2^-S) == (acc*M + 2^(S-1)) >> S （M,S 均 int）
    half = (I64(1) << (layer.s.astype(I64) - I64(1)))[:, None]
    q = (acc * layer.m.astype(I64)[:, None] + half) >> layer.s.astype(I64)[:, None]
    q = q + I64(layer.zp_pre)
    np.clip(q, 0, 255, out=q)
    out = layer.lut[q]
    out = out.reshape(layer.c_out, ho, wo).astype(np.uint8)
    return out[None] if xq.ndim == 4 else out


# ---------------------------------------------------------------------------
# 4. 量化参数（契约 §3）：每层一份，Conv 之外全是 float32
# ---------------------------------------------------------------------------
@dataclasses.dataclass
class LayerPlan:
    """一层的全部参数：几何 + 定点。对应契约 §4 的 5 个缓冲。"""

    conv_name: str
    in_name: str
    out_name: str
    c_in: int
    c_out: int
    k: int
    stride: int
    pad_h_begin: int
    pad_h_end: int
    pad_w_begin: int
    pad_w_end: int
    h_in: int
    w_in: int
    h_out: int
    w_out: int
    # 权重（两条路径都留着：w_f 给 float32 参考，w_q 给 INT8）
    w_f: np.ndarray          # float32 [C_OUT, C_IN, K, K]
    w_q: np.ndarray          # int8    [C_OUT, C_IN*K*K]
    w_scale: np.ndarray      # float32 [C_OUT]  per-output-channel
    bias_f: Optional[np.ndarray]
    # 定点标量
    bias_i32: np.ndarray     # int32 [C_OUT]
    m: np.ndarray            # int32 [C_OUT]
    s: np.ndarray            # int32 [C_OUT]
    sx: float
    zx: int
    sp: float
    zp_pre: int
    so: float
    zo: int
    act: str
    lut: np.ndarray          # uint8 [256]
    macs: int

    @property
    def zp_in(self) -> int:
        """契约 §4 的 zp_in ≡ 本节里的 zx（输入激活零点）。零 padding 要填它，不是 0。"""
        return self.zx

    def vs_contract(self) -> Tuple[bool, List[str]]:
        """契约 §5 的层范围判定。返回 (能不能送 PL, 不能的理由)。"""
        why: List[str] = []
        if self.k not in PL_K_ALLOWED:
            why.append("K=%d 不在 (1,3)" % self.k)
        if self.stride not in PL_STRIDE_ALLOWED:
            why.append("stride=%d 不在 (1,2)" % self.stride)
        if not (min(self.pad_h_begin, self.pad_h_end, self.pad_w_begin, self.pad_w_end) in PL_PAD_ALLOWED
                and max(self.pad_h_begin, self.pad_h_end, self.pad_w_begin, self.pad_w_end) in PL_PAD_ALLOWED):
            why.append("pad=(%d,%d,%d,%d) 不在 0..1" % (self.pad_h_begin, self.pad_h_end,
                                                       self.pad_w_begin, self.pad_w_end))
        if not (PL_CIN_RANGE[0] <= self.c_in <= PL_CIN_RANGE[1]):
            why.append("C_IN=%d 不在 %s" % (self.c_in, PL_CIN_RANGE))
        if not (PL_COUT_RANGE[0] <= self.c_out <= PL_COUT_RANGE[1]):
            why.append("C_OUT=%d 不在 %s" % (self.c_out, PL_COUT_RANGE))
        if not (PL_HW_RANGE[0] <= self.h_in <= PL_HW_RANGE[1]):
            why.append("H_IN=%d 不在 %s" % (self.h_in, PL_HW_RANGE))
        if not (PL_HW_RANGE[0] <= self.w_in <= PL_HW_RANGE[1]):
            why.append("W_IN=%d 不在 %s（DFL 的 2100 就栽在这）" % (self.w_in, PL_HW_RANGE))
        return (not why), why


def quant_params(minv: float, maxv: float) -> Tuple[float, int]:
    """非对称 uint8：q = round(x/s) + zp，反量化 x = (q-zp)*s。

    范围退化（max==min）时给一个 ±0.5 的兜底区间 —— 不能除零，也不能静默编数。
    """
    if not np.isfinite(minv) or not np.isfinite(maxv):
        raise ValueError("激活范围非有限：min=%r max=%r" % (minv, maxv))
    if maxv - minv < 1e-8:
        c = 0.5 * (minv + maxv)
        minv, maxv = c - 0.5, c + 0.5
    s = (maxv - minv) / 255.0
    zp = int(round(-minv / s))
    zp = max(0, min(255, zp))
    return float(s), zp


def make_lut(sp: float, zp_pre: int, so: float, zo: int, act: str = "none") -> np.ndarray:
    """契约 §3.4 的 LUT 生成式。act ∈ {none, silu, relu}。

    ★ 恒等层也必须走 LUT（不能跳过），否则 sp 与 so 不同时会把一个 scale 当另一个用。
    v1 的整网用 act="none"：ONNX 图里 SiLU 是独立的 Sigmoid+Mul 节点，融合需要
    在节点循环里跳过它们，属于 v2 的事（见 src/ps/README.md 的 TODO）。
    """
    q = np.arange(256, dtype=F32)
    xf = (q - F32(zp_pre)) * F32(sp)
    if act == "none":
        yf = xf
    elif act == "silu":
        yf = xf / (F32(1.0) + np.exp(-xf))
    elif act == "relu":
        yf = np.maximum(xf, F32(0.0))
    else:
        raise ValueError("未知 act=%s" % act)
    qo = np.rint(yf / F32(so)) + zo
    return np.clip(qo, 0, 255).astype(np.uint8)


def requant_params(sx: float, sw: np.ndarray, sp: float) -> Tuple[np.ndarray, np.ndarray]:
    """契约 §3.3：M = round(sx*sw/sp * 2^31)，再整体缩放使 M 落在 [2^30, 2^31)。"""
    m_float = (sx * sw.astype(np.float64)) / float(sp)
    if np.any(m_float <= 0):
        raise ValueError("requant 乘子非正：min=%r（scale 有问题）" % float(m_float.min()))
    mf = m_float * float(1 << 31)
    s = np.full(mf.shape, 31, dtype=np.int64)
    # ★ 规范化必须「同向」：M 乘 2 必须让 S 加 1（M*2^-S 才守恒）。反过来写就是乘子
    #   被整体放大了 2^(2k) 倍 —— 我第一次写成 S-1，结果 64 层卷积全部饱和到 255，
    #   cosine 掉到 0.87，但形状全对，只看形状根本查不出来。
    # 逐通道用显式掩码：只动越界的那几个通道的 S，其余通道不能被顺手改。
    while True:
        hi = mf >= float(1 << 31)
        if not np.any(hi):
            break
        mf = np.where(hi, mf * 0.5, mf)
        s = np.where(hi, s - 1, s)
    while True:
        lo = mf < float(1 << 30)
        if not np.any(lo):
            break
        mf = np.where(lo, mf * 2.0, mf)
        s = np.where(lo, s + 1, s)
    if np.any(s < 1):
        raise ValueError("requant S=%s < 1：sp 远大于 sx*sw，定点放不下" % s.tolist())
    if np.any(s > 62):
        raise ValueError("requant S=%s > 62：超出 int64 安全移位范围" % s.tolist())
    m = np.clip(np.rint(mf), 1, (1 << 31) - 1).astype(np.int64)
    return m.astype(np.int32), s.astype(np.int32)


def quantize_weights_per_channel(w_f: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """QInt8 per-output-channel 对称量化（对齐 int8_sim 的 WeightSymmetric=True）。"""
    co = w_f.shape[0]
    w2d = w_f.reshape(co, -1).astype(np.float64)
    sw = np.max(np.abs(w2d), axis=1) / 127.0
    sw = np.where(sw <= 0, 1e-8, sw)
    wq = np.clip(np.rint(w2d / sw[:, None]), -127, 127).astype(np.int8)
    return wq, sw.astype(F32)


def _tensor_range(ranges: Dict[str, Tuple[float, float]], name: str) -> Optional[Tuple[float, float]]:
    return ranges.get(name)


def ranges_from_golden(graph: Graph, golden: Dict[str, np.ndarray]) -> Dict[str, Tuple[float, float]]:
    """校准集 = golden 那一次前向（单张伪随机图），按 ONNX 张量名建 [min,max] 表。"""
    out: Dict[str, Tuple[float, float]] = {}
    for node in graph.nodes:
        for nm in node.outputs:
            key = nm.replace("/", "_")
            if key in golden and golden[key].size:
                out[nm] = (float(np.min(golden[key])), float(np.max(golden[key])))
    return out


def ranges_from_ort(model_path: Path, graph: Graph, x: np.ndarray) -> Dict[str, Tuple[float, float]]:
    """在**给定输入**上重新标定：跑一遍 ORT float32，取每个节点输出的实测 [min,max]。

    这不是"偷看答案"：量化 scale 本来就必须来自标定集；对 --input image 这种
    换分布输入，用 golden（伪随机图）的 range 会把真实图压进 5 个量化级。生产上
    应该用一批真实图标定一次、把 scale 冻成常量；这里只有一张图，如实标注。
    """
    names = [o for n in graph.nodes for o in n.outputs]
    outs = ort_reference(model_path, {graph.input_name: x}, names)
    out: Dict[str, Tuple[float, float]] = {}
    for nm in names:
        a = outs[nm]
        if a.size:
            out[nm] = (float(np.min(a)), float(np.max(a)))
    return out


def resolve_calib_set(spec: str) -> List[Path]:
    """把 `--calib` 的取值解析成一组图片路径。

    支持三种写法（按优先级）：
      · 一个目录        -> 取该目录下全部 *.jpg/*.jpeg/*.png（排序保证可复现）
      · 逗号分隔的路径  -> 逐个校验存在
      · 单个文件        -> 只标定这一张（等于退回单图，但保留统一入口）

    ★ 为什么排序：同一批图必须每次以相同顺序喂给标定，否则范围虽然一样
      （min/max 与顺序无关），但**调试时复现不了**。排序是免费的可复现性。
    """
    s = spec.strip()
    cand = Path(s)
    if not cand.is_absolute():
        cand = REPO / cand
    if cand.is_dir():
        exts = (".jpg", ".jpeg", ".png", ".bmp")
        files = sorted([q for q in cand.iterdir()
                        if q.is_file() and q.suffix.lower() in exts])
        if not files:
            raise SystemExit("标定目录里没有图片：%s" % cand)
        return files
    parts = [x.strip() for x in s.split(",") if x.strip()]
    out: List[Path] = []
    for part in parts:
        q = Path(part)
        if not q.is_absolute():
            q = REPO / q
        if not q.is_file():
            raise SystemExit("标定图片不存在：%s" % q)
        out.append(q)
    if not out:
        raise SystemExit("--calib 没有解析出任何图片：%r" % spec)
    return out


def ranges_from_ort_multi(model_path: Path, graph: Graph,
                          images: Sequence[Path],
                          pct: float = 99.99) -> Dict[str, Tuple[float, float]]:
    """用**多张图**跑 ORT float32，取每个张量的 min/max **包络**作为标定范围。

    为什么是包络而不是平均：量化的 scale 由 (max-min) 决定；取包络保证
    「这一批图里出现过的所有值」都能被表示，不会因为平均而被压到饱和。
    单图时包络退化为该图自身 —— 这正是之前误差偏大的原因。

    ⚠️ 局限（必须知道）：包络对**离群值敏感**。若某张图有极端值，会把 scale 拉大，
       使正常图的有效量化位减少。标准做法是取**百分位**（如 99.99%）而不是硬 min/max。
       这里先用包络，因为它是确定性的、可解释的；百分位版本留作后续优化，
       改的时候要重跑同一套判据。
    """
    size = int(graph.input_shape[-1])
    names = [o for n in graph.nodes for o in n.outputs]

    # ★ 用**百分位包络**而不是硬 min/max。
    #   原因（实测）：硬 min/max 会被单张图的离群值撑大 ——
    #   16 张标定图下分数最大差从 0.056 降到 0.0195，但仍超 0.010，
    #   说明少数极端值把 scale 拉大、正常值得不到足够量化位。
    #   标准 PTQ 做法是取 99.9~99.99 百分位：牺牲极少数离群点（它们本就饱和），
    #   换取绝大多数值多 1~2 个有效位。
    #   ⚠️ pct 是**可调参数**，改它必须重跑同一套判据，不能"调到通过为止"。
    per_tensor: Dict[str, List[np.ndarray]] = {}
    for path in images:
        x = load_image_input(path, size)
        outs = ort_reference(model_path, {graph.input_name: x}, names)
        for nm in names:
            a = outs[nm]
            if not a.size:
                continue
            per_tensor.setdefault(nm, []).append(
                np.asarray(a, dtype=np.float32).reshape(-1))
    lo_p = (100.0 - pct) / 2.0
    hi_p = 100.0 - lo_p
    out: Dict[str, Tuple[float, float]] = {}
    for nm, chunks in per_tensor.items():
        cat = np.concatenate(chunks)
        out[nm] = (float(np.percentile(cat, lo_p)), float(np.percentile(cat, hi_p)))
    return out


def _chw_shape(arr: np.ndarray) -> Tuple[int, int, int]:
    """golden 里的激活是 4D [1,C,H,W]，统一取 (C,H,W)。"""
    if arr.ndim == 4:
        return int(arr.shape[1]), int(arr.shape[2]), int(arr.shape[3])
    return int(arr.shape[0]), int(arr.shape[1]), int(arr.shape[2])


def build_plan(graph: Graph, ranges: Dict[str, Tuple[float, float]],
               calib_x: np.ndarray) -> List[LayerPlan]:
    """为每个 Conv 节点生成一份 LayerPlan。

    激活 scale/zp 来源 `ranges`（ONNX 张量名 -> (min,max)），见 ranges_from_golden /
    ranges_from_ort。模型输入 `images` 若不在 ranges 里，退回 calib_x 自身的范围。
    """
    in_range = ranges.get(graph.input_name,
                          (float(np.min(calib_x)), float(np.max(calib_x))))
    plans: List[LayerPlan] = []
    for node in graph.nodes:
        if node.op_type != "Conv":
            continue
        w_name = node.inputs[1]
        w_f = graph.initializers[w_name].astype(F32)
        c_out, c_in, k = int(w_f.shape[0]), int(w_f.shape[1]), int(w_f.shape[2])
        if w_f.shape[3] != k:
            raise RuntimeError("非方核未支持：%s %s" % (node.name, w_f.shape))
        if int(node.attrs.get("group", 1)) != 1:
            raise NotImplementedError("grouped/depthwise Conv 未支持：%s" % node.name)
        dil = node.attrs.get("dilations", [1, 1])
        if dil != [1, 1]:
            raise NotImplementedError("dilation != 1 未支持：%s" % node.name)
        pad = [int(v) for v in node.attrs.get("pads", [0, 0, 0, 0])]
        strides = [int(v) for v in node.attrs.get("strides", [1, 1])]
        if strides[0] != strides[1]:
            raise NotImplementedError("非方形 stride 未支持：%s %s" % (node.name, strides))

        in_name, out_name = node.inputs[0], node.outputs[0]
        rin = ranges.get(in_name)
        if rin is None:
            if in_name == graph.input_name:
                rin = in_range
            else:
                raise KeyError("标定表里没有 Conv 输入 %s（不许静默编数）" % in_name)
        rout = ranges.get(out_name)
        if rout is None:
            raise KeyError("标定表里没有 Conv 输出 %s（无法定标）" % out_name)
        if in_name not in graph.shapes:
            raise KeyError("形状推断里没有 %s（无法定几何）" % in_name)
        c_i, h_i, w_i = _chw_shape_dims(graph.shapes[in_name])
        if c_i != c_in:
            raise RuntimeError("C_IN 对不上：权重 %d，输入张量 %d（%s）" % (c_in, c_i, node.name))
        h_out = (h_i + pad[0] + pad[2] - k) // strides[0] + 1
        w_out = (w_i + pad[1] + pad[3] - k) // strides[1] + 1
        if out_name in graph.shapes:
            gshape = _chw_shape_dims(graph.shapes[out_name])
            if gshape != (c_out, h_out, w_out):
                raise RuntimeError(
                    "几何对不上：%s 算得 out=%s，形状推断是 %s（属性读错了）"
                    % (node.name, (c_out, h_out, w_out), gshape))

        # ---- 权重 ----
        w_q, w_scale = quantize_weights_per_channel(w_f)
        bias_f = None
        if len(node.inputs) >= 3 and node.inputs[2] != "":
            bias_f = graph.initializers[node.inputs[2]].astype(F32)
        bias_f_full = bias_f if bias_f is not None else np.zeros(c_out, dtype=F32)

        # ---- 激活定点（契约 §3.1）----
        sx, zx = quant_params(*rin)
        sp, zp_pre = quant_params(*rout)
        so, zo = sp, zp_pre  # v1 act="none"：激活前后同一个张量，量程自然相同
        act = "none"

        # ---- bias 折叠（契约 §3.2）：★ 最容易漏的一条 ----
        sum_w = w_q.astype(np.int64).sum(axis=1)  # [C_OUT]
        bias_i32 = np.rint(bias_f_full.astype(np.float64) / (sx * w_scale.astype(np.float64))).astype(np.int64)
        bias_i32 = bias_i32 - int(zx) * sum_w
        bias_i32 = np.clip(bias_i32, -(1 << 31), (1 << 31) - 1).astype(np.int32)

        m, s = requant_params(sx, w_scale, sp)
        lut = make_lut(sp, zp_pre, so, zo, act)

        plans.append(
            LayerPlan(
                conv_name=node.name, in_name=in_name, out_name=out_name,
                c_in=c_in, c_out=c_out, k=k, stride=strides[0],
                pad_h_begin=pad[0], pad_h_end=pad[2], pad_w_begin=pad[1], pad_w_end=pad[3],
                h_in=h_i, w_in=w_i, h_out=h_out, w_out=w_out,
                w_f=w_f, w_q=w_q, w_scale=w_scale, bias_f=bias_f,
                bias_i32=bias_i32, m=m, s=s,
                sx=sx, zx=zx, sp=sp, zp_pre=zp_pre, so=so, zo=zo,
                act=act, lut=lut,
                macs=int(c_out) * int(c_in * k * k) * int(h_out * w_out),
            )
        )
    return plans


# ---------------------------------------------------------------------------
# 5. 后端抽象（★ 可插拔点：将来只改这一个类）
# ---------------------------------------------------------------------------
class Backend:
    """卷积后端协议。约定：

    * `quantized=True` 的后端收 uint8、吐 uint8（定点路径 = 硬件口径）
    * `quantized=False` 的后端收 float32、吐 float32（只用于 --check-ops）
    * 一个后端只管「一层卷积」，不管层序 —— 层序在 run_network 里
    """

    name = "abstract"
    quantized = True

    def conv(self, x: np.ndarray, layer: LayerPlan) -> np.ndarray:  # pragma: no cover
        raise NotImplementedError


class FloatBackend(Backend):
    """float32 参考后端：证明 12 种非卷积算子和几何是对的。"""

    name = "float32"
    quantized = False

    def conv(self, x: np.ndarray, layer: LayerPlan) -> np.ndarray:
        return float_conv(x, layer)


class NumpyBackend(Backend):
    """现在用的后端：numpy 直接算 INT8 卷积（逐位按契约 §1/§3）。"""

    name = "numpy"
    quantized = True

    def conv(self, x: np.ndarray, layer: LayerPlan) -> np.ndarray:
        return int8_conv(x, layer)


class PlBackend(Backend):
    """PL 后端骨架：把一层卷积派发给 PL 的 `conv_engine`（契约 §4）。

    ★ 现在**不可用**：PL 侧 HLS 还在写，真正的 PYNQ 调用细节（寄存器名、buffer
      物理地址、cache 同步）一个都没实测过。本类只做两件已确定的事：

      1. 契约 §5 的层范围判定（`supports`）—— DFL 的 W=2100 / C_OUT=1 必须回到
         numpy 兜底，这个决策现在就能定，且与硬件无关；
      2. 把「怎么调用」编成成功路径的形状（`conv` -> `driver.run_conv`），好让
         换后端时不需要改 run_network。

    `<待实测>` 清单见 src/ps/pynq_driver/conv_driver.py 与 src/ps/README.md。
    """

    name = "pl"
    quantized = True

    def __init__(self, driver=None, fallback: Optional[Backend] = None) -> None:
        # driver: 鸭子类型对象，需实现 run_conv(layer, xq) -> yq（uint8）
        self.driver = driver
        self.fallback = fallback or NumpyBackend()
        self.pl_layers: List[str] = []
        self.fallback_layers: List[Tuple[str, List[str]]] = []

    def supports(self, layer: LayerPlan) -> Tuple[bool, List[str]]:
        return layer.vs_contract()

    def conv(self, x: np.ndarray, layer: LayerPlan) -> np.ndarray:
        ok, why = self.supports(layer)
        if not ok:
            # 契约 §5 的例外：不在范围内的层由 PS/numpy 兜底（DFL 只占 0.01% MAC）
            self.fallback_layers.append((layer.conv_name, why))
            return self.fallback.conv(x, layer)
        if self.driver is None:
            raise NotImplementedError(
                "PlBackend 还没有 driver：PL 侧 conv_engine 未就绪。"
                "已实测为零，argv 里用 --backend pl --dry-run 只能看分层结果。"
                "待办见 src/ps/pynq_driver/conv_driver.py 的 <待实测> 清单。")
        self.pl_layers.append(layer.conv_name)
        return self.driver.run_conv(layer, x)


# ---------------------------------------------------------------------------
# 6. 解释器（层序在这里 —— PL 不保存网络结构，这就是 PS 的职责）
# ---------------------------------------------------------------------------
class Runtime:
    def __init__(self, graph: Graph) -> None:
        self.graph = graph
        self.tensors: Dict[str, np.ndarray] = {}

    def get(self, name: str) -> np.ndarray:
        if name == "":
            raise KeyError("空输入名（可选输入没填，调用的算子没处理这种情况）")
        if name in self.tensors:
            return self.tensors[name]
        if name in self.graph.initializers:
            return self.graph.initializers[name]
        raise KeyError("取不到张量：%s（不属于任何已执行节点、也不是 initializer）" % name)


def _quantize_u8(x: np.ndarray, scale: float, zp: int) -> np.ndarray:
    q = np.rint(_as_f32(x) / F32(scale)) + zp
    return np.clip(q, 0, 255).astype(np.uint8)


def _dequantize_u8(q: np.ndarray, scale: float, zp: int) -> np.ndarray:
    return ((q.astype(F32) - F32(zp)) * F32(scale)).astype(F32)


def run_network(graph: Graph, x: np.ndarray, backend: Backend,
                plans: Sequence[LayerPlan]) -> Tuple[Dict[str, np.ndarray], Dict[str, Any]]:
    """按 ONNX 的拓扑序逐节点执行。Conv -> backend，其余 -> numpy。"""
    plan_by_conv = {p.conv_name: p for p in plans}
    rt = Runtime(graph)
    rt.tensors[graph.input_name] = x
    stats: Dict[str, Any] = {
        "nodes_executed": 0,
        "conv_via_backend": 0,
        "op_counts": {},
        "conv_time_s": 0.0,
        "other_time_s": 0.0,
        "slowest_convs": [],
    }
    t_all0 = time.perf_counter()
    for node in graph.nodes:
        t0 = time.perf_counter()
        if node.op_type == "Conv":
            layer = plan_by_conv[node.name]
            xf = rt.get(node.inputs[0])
            if backend.quantized:
                xq = _quantize_u8(xf, layer.sx, layer.zx)
                yq = backend.conv(xq, layer)
                yf = _dequantize_u8(yq, layer.so, layer.zo)
            else:
                yf = backend.conv(_as_f32(xf), layer)
            rt.tensors[node.outputs[0]] = yf
            dt = time.perf_counter() - t0
            stats["conv_time_s"] += dt
            stats["slowest_convs"].append((dt, node.name, layer.macs))
            stats["conv_via_backend"] += 1
        else:
            impl = OP_IMPL.get(node.op_type)
            if impl is None:
                raise NotImplementedError("算子 %s 没有 numpy 实现（%s）" % (node.op_type, node.name))
            out = impl(node, rt)
            if isinstance(out, (list, tuple)):
                if len(out) != len(node.outputs):
                    raise RuntimeError("Split 输出个数不符：%s" % node.name)
                for nm, val in zip(node.outputs, out):
                    rt.tensors[nm] = val
            else:
                rt.tensors[node.outputs[0]] = out
            stats["other_time_s"] += time.perf_counter() - t0
        stats["nodes_executed"] += 1
        stats["op_counts"][node.op_type] = stats["op_counts"].get(node.op_type, 0) + 1
    stats["wall_time_s"] = time.perf_counter() - t_all0
    stats["slowest_convs"] = sorted(stats["slowest_convs"], reverse=True)[:5]
    return rt.tensors, stats


# ---------------------------------------------------------------------------
# 7. 误差度量 / 检测框解码（与 tools/int8_sim.py 同口径）
# ---------------------------------------------------------------------------
def compare(a: np.ndarray, b: np.ndarray) -> Dict[str, float]:
    """a = 参考，b = 被测。口径照抄 tools/int8_sim.py 的 compare()。"""
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    if a.shape != b.shape:
        return {"shape_mismatch": 1.0}
    diff = np.abs(a - b)
    scale = float(np.max(np.abs(a))) or 1.0
    out: Dict[str, float] = {
        "max_abs_err": float(np.max(diff)),
        "mean_abs_err": float(np.mean(diff)),
        "max_err_over_maxabs": float(np.max(diff) / scale),
        "mean_err_over_maxabs": float(np.mean(diff) / scale),
        "cosine": float(np.dot(a.ravel(), b.ravel()) / ((np.linalg.norm(a) * np.linalg.norm(b)) + 1e-12)),
    }
    nz = np.abs(a) > (scale * 1e-6)
    if np.any(nz):
        rel = diff[nz] / np.abs(a[nz])
        out["rel_err_p50"] = float(np.percentile(rel, 50))
        out["rel_err_p99"] = float(np.percentile(rel, 99))
    return out


def decode_detections(out: np.ndarray, conf_thr: float = 0.25) -> Dict[str, np.ndarray]:
    """output0 [1,84,2100] -> 检测框。

    ★ 这里必须说清楚：output0 的前 4 行**不是** ltrb 距离，是 (cx, cy, w, h) × stride
      —— 图里 `/model.22/dfl/conv` 只吐 4 个 ltrb 距离，随后 Sub/Add_1/Add_2/Div_1
      已经把 dist2bbox 做完了，`Mul_2` 只是乘 stride。把前 4 行当 ltrb 画框会**整片
      错位**（这是我实际踩到的坑，写在这里防止再犯）。第 5..84 行是已过 Sigmoid 的
      80 类分数。
    """
    o = np.asarray(out, dtype=np.float64)[0]  # [84, 2100]
    xywh = o[:4].T                            # [2100, 4]  (cx, cy, w, h) 单位=输入像素
    scores_all = o[4:].T                      # [2100, 80]
    cls_id = np.argmax(scores_all, axis=1)
    score = scores_all[np.arange(scores_all.shape[0]), cls_id]
    keep = score > conf_thr
    xywh_k = xywh[keep]
    half = xywh_k[:, 2:] * 0.5
    boxes = np.concatenate([xywh_k[:, :2] - half, xywh_k[:, :2] + half], axis=1)  # x1y1x2y2
    return {"boxes": boxes, "scores": score[keep], "cls": cls_id[keep]}


def _iou_one(box: np.ndarray, boxes: np.ndarray) -> np.ndarray:
    x1 = np.maximum(box[0], boxes[:, 0])
    y1 = np.maximum(box[1], boxes[:, 1])
    x2 = np.minimum(box[2], boxes[:, 2])
    y2 = np.minimum(box[3], boxes[:, 3])
    inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    area_a = (box[2] - box[0]) * (box[3] - box[1])
    area_b = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    return inter / np.maximum(area_a + area_b - inter, 1e-9)


def nms(boxes: np.ndarray, scores: np.ndarray, cls: np.ndarray, iou_thr: float = 0.45) -> np.ndarray:
    """按类别做的 numpy NMS（对齐 ultralytics 默认 0.45）。"""
    keep = []
    for c in np.unique(cls):
        idx = np.where(cls == c)[0]
        order = idx[np.argsort(-scores[idx])]
        while order.size:
            i = order[0]
            keep.append(i)
            rest = order[1:]
            if rest.size == 0:
                break
            order = rest[_iou_one(boxes[i], boxes[rest]) <= iou_thr]
    return np.array(sorted(keep), dtype=np.int64)


def match_detections(ref: Dict[str, np.ndarray], sim: Dict[str, np.ndarray],
                     iou_thr: float = 0.5) -> Dict[str, Any]:
    """贪心按 IoU 匹配（同类），给出「框差 / 分数差 / 漏检多检」。"""
    rb, rs, rc = ref["boxes"], ref["scores"], ref["cls"]
    sb, ss, sc = sim["boxes"], sim["scores"], sim["cls"]
    used = np.zeros(len(sb), dtype=bool)
    pairs = []
    for i in np.argsort(-rs):
        cand = np.where((~used) & (sc == rc[i]))[0]
        if cand.size == 0:
            continue
        ious = _iou_one(rb[i], sb[cand])
        j = cand[int(np.argmax(ious))]
        if ious.max() >= iou_thr:
            used[j] = True
            pairs.append((i, j))
    if not pairs:
        return {"matched": 0, "n_ref": int(len(rb)), "n_sim": int(len(sb)),
                "max_corner_err_px": None, "max_score_err": None, "unmatched_ref": int(len(rb)),
                "unmatched_sim": int(len(sb))}
    ir = np.array([p[0] for p in pairs])
    js = np.array([p[1] for p in pairs])
    corner_err = float(np.max(np.abs(rb[ir] - sb[js])))
    score_err = float(np.max(np.abs(rs[ir] - ss[js])))
    return {
        "matched": int(len(pairs)),
        "n_ref": int(len(rb)),
        "n_sim": int(len(sb)),
        "max_corner_err_px": corner_err,
        "max_score_err": score_err,
        "unmatched_ref": int(len(rb) - len(pairs)),
        "unmatched_sim": int(len(sb) - len(pairs)),
    }


# ---------------------------------------------------------------------------
# 8. 输入准备 / ORT 参考
# ---------------------------------------------------------------------------
def make_normal_input(shape: Sequence[int], seed: int = DEFAULT_INPUT_SEED) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.standard_normal(tuple(shape), dtype=F32)


def load_image_input(path: Path, size: int) -> np.ndarray:
    import cv2

    img = cv2.imread(str(path))
    if img is None:
        raise SystemExit("读不到图片：%s" % path)
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    img = cv2.resize(img, (size, size), interpolation=cv2.INTER_LINEAR)
    x = img.astype(F32) / F32(255.0)
    x = np.transpose(x, (2, 0, 1))[None, ...]
    return np.ascontiguousarray(x, dtype=F32)


def ort_reference(model_path: Path, feed: Dict[str, np.ndarray],
                  want: Sequence[str]) -> Dict[str, np.ndarray]:
    """用 onnxruntime 跑参考值，可取任意中间张量。

    ★ 不能直接把内部张量的名字塞进 `sess.run(output_names)`。`value_info` 只是
      **形状注释**，图里并没有把它们登记进 `graph.output`，ORT 会报
        InvalidArgument : Invalid output name:/model.0/conv/Conv_output_0
      （同一个坑在 tools/make_golden.py 里已经踩过并修过一次。）
      正确做法：**改写图** —— 给每个想要的张量加一个 Identity 节点，
      把它的输出登记进 graph.output，再让 ORT 去算。
      这里用内存中的模型副本，不修改磁盘上的 .onnx。
    """
    import io

    import onnx
    import onnxruntime as ort

    m = onnx.load(str(model_path))
    m = onnx.shape_inference.infer_shapes(m, strict_mode=False)

    existing = {o.name for o in m.graph.output}
    idx = 0
    # 原张量名 -> 图里的实际输出名（可能是原名，也可能是我们加的 ref__ 前缀）
    out_name_of: Dict[str, str] = {}
    for nm in want:
        if nm in existing:
            out_name_of[nm] = nm
            continue
        new_name = "ref__" + nm
        out_name_of[nm] = new_name
        # ★ 每个 Identity 的 node name 必须唯一，否则 ONNX 拒绝加载：
        #   `two nodes with same node name`（同坑，make_golden.py 已记录）
        m.graph.node.append(
            onnx.helper.make_node("Identity", [nm], [new_name],
                                  name="ref_pick_%05d" % idx))
        idx += 1
        vi = onnx.helper.ValueInfoProto()
        vi.name = new_name
        m.graph.output.append(vi)

    buf = io.BytesIO(m.SerializeToString())
    so = ort.SessionOptions()
    so.log_severity_level = 3
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    sess = ort.InferenceSession(buf.getvalue(), so, providers=["CPUExecutionProvider"])

    # ★ 必须用**图里的实际输出名**去 run，再把结果映射回原张量名。
    #   直接传 want 会报 Invalid output name（这正是上一次失败的原因）。
    graph_names = [out_name_of[nm] for nm in want]
    got = sess.run(graph_names, feed)
    return {nm: v for nm, v in zip(want, got)}


def load_golden(path: Path) -> Dict[str, np.ndarray]:
    with np.load(path) as z:
        return {k: z[k] for k in z.files}


# ---------------------------------------------------------------------------
# 9. 三个模式
# ---------------------------------------------------------------------------
def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8", newline="\n")


def _is_relative_to(path: Path, base: Path) -> bool:
    """Python 3.8 compatibility (Path.is_relative_to is 3.9+)."""
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def make_input(args, graph: Graph) -> Tuple[np.ndarray, str, str]:
    shape = graph.input_shape
    if args.input == "normal":
        return (make_normal_input(shape), "normal",
                "确定性伪随机 default_rng(%d)（与 golden 同款）" % DEFAULT_INPUT_SEED)
    if args.input == "auto":
        p = REPO / "data" / "samples" / "zidane.jpg"
        if p.exists():
            return load_image_input(p, shape[-1]), "image", "真实图片 %s" % p.name
        return (make_normal_input(shape), "normal", "未找到 zidane.jpg，退回伪随机")
    p = Path(args.input)
    if not p.is_absolute():
        p = REPO / p
    return load_image_input(p, shape[-1]), "image", "真实图片 %s" % p.name


def cmd_check(args) -> int:
    model = Path(args.model)
    golden_path = Path(args.golden)
    if not model.is_absolute():
        model = REPO / model
    if not golden_path.is_absolute():
        golden_path = REPO / golden_path

    graph = load_graph(model)
    if graph.input_shape[-1] != 320:
        print("WARNING: 模型输入不是 320，golden 只对 320 有")
    golden = load_golden(golden_path)
    x, tag, input_desc = make_input(args, graph)

    # 标定表来源：normal 输入用 golden（与金标准同一次前向）；换分布（真实图）则在
    # 该图上重新标定，否则真实图的 [0,1] 会被伪随机图的 [-4.6,4.6] scale 压成 5 级。
    if tag == "normal":
        ranges = ranges_from_golden(graph, golden)
        scales_src = "golden（伪随机标定图，与金标准同一次前向）"
    elif getattr(args, "calib", None):
        # ★ 多图标定（标准 PTQ 做法）：用一组图统计每个张量的**真实动态范围**。
        #   为什么必须多图：单图标定时该图没覆盖到的张量范围会被低估 ⇒ scale 偏小 ⇒
        #   饱和截断 ⇒ 误差逐层累积。实测单图 zidane.jpg 时分数最大差 0.056（超阈值 0.010）。
        calib_paths = resolve_calib_set(args.calib)
        ranges = ranges_from_ort_multi(model, graph, calib_paths)
        scales_src = "多图标定 %d 张（%s；真实范围取 min/max 包络）" % (
            len(calib_paths), args.calib)
    else:
        ranges = ranges_from_ort(model, graph, x)
        scales_src = "单图自身前向实测（标定集=该图；建议改用 --calib 多图标定）"

    print("=" * 78)
    print("pynq_net --check : YOLOv8n@320 整网 INT8（Conv 走 backend，其余 float32）")
    print("  模型     : %s" % model.name)
    print("  节点数   : %d，算子种类 %d" % (len(graph.nodes), len(graph.op_counts())))
    print("  golden   : %s（%d 个张量）" % (golden_path.name, len(golden)))
    print("  输入     : %s, %s" % (input_desc, list(x.shape)))
    print("  标定     : %s" % scales_src)
    print("=" * 78)

    t_plan0 = time.perf_counter()
    plans = build_plan(graph, ranges, x)
    t_plan = time.perf_counter() - t_plan0
    total_macs = sum(p.macs for p in plans)

    backend = NumpyBackend()
    tensors, stats = run_network(graph, x, backend, plans)

    out_name = graph.output_names[0]
    y = tensors[out_name]

    # ---- 参考：ORT float32（与 int8_sim 同口径）；normal 输入再叠一层 golden ----
    ref = ort_reference(model, {graph.input_name: x}, [out_name])[out_name]
    c_ref = compare(ref, y)
    report: Dict[str, Any] = {
        "tool": "tools/pynq_net.py",
        "mode": "check",
        "model": model.name,
        "backend": backend.name,
        "input": input_desc,
        "input_shape": list(x.shape),
        "scales_source": scales_src,
        "nodes_executed": stats["nodes_executed"],
        "op_counts": stats["op_counts"],
        "conv_layers": len(plans),
        "total_macs": total_macs,
        "total_gop": 2.0 * total_macs / 1e9,
        "plan_time_s": t_plan,
        "wall_time_s": stats["wall_time_s"],
        "output_shape": list(y.shape),
        "vs_ort_float32": c_ref,
        "detections": {},
    }
    print("节点全部执行：%d / %d（卷积 %d 层，MAC %.4f G = %.4f GOP@2）"
          % (stats["nodes_executed"], len(graph.nodes), len(plans),
             total_macs / 1e9, 2.0 * total_macs / 1e9))
    print("定点参数生成 %.2f s；整网 forward %.2f s（conv %.2f s / 非 conv %.2f s）"
          % (t_plan, stats["wall_time_s"], stats["conv_time_s"], stats["other_time_s"]))
    print("输出张量：%s %s" % (out_name, list(y.shape)))
    print("")
    print("-- 对 ORT float32 参考（口径同 tools/int8_sim.py）--")
    for k in ("cosine", "max_abs_err", "mean_abs_err", "max_err_over_maxabs",
              "mean_err_over_maxabs", "rel_err_p50", "rel_err_p99"):
        if k in c_ref:
            print("   %-22s %.6g" % (k, c_ref[k]))

    # ---- golden 对比（同一输入才有意义）----
    c_gold = None
    if tag == "normal" and out_name.replace("/", "_") in golden:
        g = golden[out_name.replace("/", "_")].astype(F32)
        c_gold = compare(g, y)
        c_ort_gold = compare(g, ref)
        report["vs_golden"] = c_gold
        report["ort_float32_vs_golden"] = c_ort_gold
        print("-- 对 golden（float16 存储，故 ORT 自己也有 %.3g 的存储误差）--"
              % c_ort_gold.get("max_abs_err", 0.0))
        for k in ("cosine", "max_abs_err", "mean_abs_err", "max_err_over_maxabs",
                  "mean_err_over_maxabs", "rel_err_p50", "rel_err_p99"):
            if k in c_gold:
                print("   %-22s %.6g" % (k, c_gold[k]))

    # ---- 检测框一致性 ----
    det_ref = decode_detections(ref, args.conf)
    det_sim = decode_detections(y, args.conf)
    keep_ref = nms(det_ref["boxes"], det_ref["scores"], det_ref["cls"], args.iou)
    keep_sim = nms(det_sim["boxes"], det_sim["scores"], det_sim["cls"], args.iou)
    nms_ref = {"boxes": det_ref["boxes"][keep_ref], "scores": det_ref["scores"][keep_ref],
               "cls": det_ref["cls"][keep_ref]}
    nms_sim = {"boxes": det_sim["boxes"][keep_sim], "scores": det_sim["scores"][keep_sim],
               "cls": det_sim["cls"][keep_sim]}
    m_raw = match_detections(det_ref, det_sim)
    m_nms = match_detections(nms_ref, nms_sim)
    report["detections"] = {
        "conf_thr": args.conf, "iou_thr": args.iou,
        "n_anchor_ref": int(len(det_ref["boxes"])), "n_anchor_sim": int(len(det_sim["boxes"])),
        "n_nms_ref": int(len(nms_ref["boxes"])), "n_nms_sim": int(len(nms_sim["boxes"])),
        "match_raw": m_raw, "match_nms": m_nms,
    }
    print("")
    print("-- 检测框（conf>%.2f，NMS IoU %.2f）--" % (args.conf, args.iou))
    print("   候选(未 NMS)：参考 %d / 被测 %d" % (len(det_ref["boxes"]), len(det_sim["boxes"])))
    print("   NMS 后       ：参考 %d / 被测 %d" % (len(nms_ref["boxes"]), len(nms_sim["boxes"])))
    if m_nms["matched"]:
        print("   匹配 %d 对：框角最大差 %.3f px，分数最大差 %.4f"
              % (m_nms["matched"], m_nms["max_corner_err_px"], m_nms["max_score_err"]))
    print("   未匹配：参考 %d / 被测 %d" % (m_nms["unmatched_ref"], m_nms["unmatched_sim"]))

    # ---- 判据 ----
    ok = True
    reasons = []
    if not (c_ref["cosine"] >= THRESH_COSINE):
        ok = False
        reasons.append("cosine %.6f < %.3f" % (c_ref["cosine"], THRESH_COSINE))
    if m_nms["n_ref"] != m_nms["n_sim"]:
        ok = False
        reasons.append("NMS 后检测框数不等 %d vs %d" % (m_nms["n_ref"], m_nms["n_sim"]))
    if m_nms["max_corner_err_px"] is not None and m_nms["max_corner_err_px"] > THRESH_BOX_PX:
        ok = False
        reasons.append("框角最大差 %.3f px > %.1f" % (m_nms["max_corner_err_px"], THRESH_BOX_PX))
    if m_nms["max_score_err"] is not None and m_nms["max_score_err"] > THRESH_SCORE:
        ok = False
        reasons.append("分数最大差 %.4f > %.3f" % (m_nms["max_score_err"], THRESH_SCORE))
    report["criteria"] = {
        "cosine_min": THRESH_COSINE, "box_px_max": THRESH_BOX_PX, "score_max": THRESH_SCORE,
        "ok": ok, "failed": reasons,
    }
    print("")
    print("判据：cosine >= %.3f、NMS 框数相等、框角 <= %.1f px、分数 <= %.3f"
          % (THRESH_COSINE, THRESH_BOX_PX, THRESH_SCORE))
    if not ok:
        for r in reasons:
            print("   FAIL: %s" % r)

    # ---- PL 分层（后端可插拔的证据）----
    pl = PlBackend(driver=None)
    split = [(p.conv_name, pl.supports(p)) for p in plans]
    n_pl = sum(1 for _, (good, _w) in split if good)
    fallback = [(nm, w) for nm, (good, w) in split if not good]
    report["pl_split"] = {
        "pl_eligible": n_pl,
        "numpy_fallback": len(fallback),
        "fallback_detail": [{"layer": nm, "reasons": w} for nm, w in fallback],
    }
    print("")
    print("PL 分层（契约 §5）：%d 层可送 PL，%d 层必须 numpy 兜底" % (n_pl, len(fallback)))
    for nm, w in fallback:
        print("   - %s : %s" % (nm, "; ".join(w)))

    tag_out = (args.tag or tag)
    out_path = (Path(args.json) if args.json
                else OUT_DIR / ("pynq_net_%s.json" % tag_out))
    if not out_path.is_absolute():
        out_path = REPO / out_path
    _write_json(out_path, report)
    print("")
    shown = out_path.relative_to(REPO) if _is_relative_to(out_path, REPO) else out_path
    print("明细 -> %s" % shown)
    print("PYNQ_NET_OK" if ok else "PYNQ_NET_FAIL")
    return 0 if ok else 1


def cmd_check_ops(args) -> int:
    """纯 float32 路径逐节点对比 golden：证明 17 种算子的语义都实现对了。"""
    model = Path(args.model)
    golden_path = Path(args.golden)
    if not model.is_absolute():
        model = REPO / model
    if not golden_path.is_absolute():
        golden_path = REPO / golden_path
    graph = load_graph(model)
    golden = load_golden(golden_path)
    x = make_normal_input(graph.input_shape)

    plans = build_plan(graph, ranges_from_golden(graph, golden), x)
    tensors, stats = run_network(graph, x, FloatBackend(), plans)

    print("=" * 78)
    print("pynq_net --check-ops : 纯 float32 逐算子 vs golden（%d 张量）" % len(golden))
    print("=" * 78)
    worst: List[Tuple[float, str, float]] = []
    missing: List[str] = []
    empty: List[str] = []
    n_bad = 0
    for node in graph.nodes:
        for nm in node.outputs:
            key = nm.replace("/", "_")
            if key not in golden:
                missing.append(nm)
                continue
            ref = golden[key].astype(F32)
            got = tensors[nm]
            if ref.size == 0:
                # 两个 Resize 的 roi 是空张量（TENSOR[0]），没有可比的值
                empty.append(nm)
                continue
            if tuple(ref.shape) != tuple(got.shape):
                print("SHAPE MISMATCH %-46s ref=%s got=%s" % (nm, ref.shape, got.shape))
                return 1
            d = float(np.max(np.abs(ref.astype(np.float64) - got.astype(np.float64))))
            scale = max(float(np.max(np.abs(ref))), 1e-6)
            rel = d / scale
            worst.append((rel, nm, d))
            if rel > 2e-2:
                n_bad += 1
    worst.sort(reverse=True)
    for rel, nm, d in worst[:10]:
        flag = "" if rel <= 2e-2 else "  <-- 超阈值"
        print("   rel=%.3e  abs=%.3e  %s%s" % (rel, d, nm, flag))
    print("")
    print("对比张量 %d 个（另：空张量 %d 个跳过，golden 缺 %d 个）"
          % (len(worst), len(empty), len(missing)))
    print("节点执行 %d 个，卷积 %d 层，wall %.2f s"
          % (stats["nodes_executed"], len(plans), stats["wall_time_s"]))
    for nm in missing[:5]:
        print("   golden 缺 - %s" % nm)
    ok = (n_bad == 0 and not missing)
    print("逐算子最大相对误差 %.3e，超阈值 2e-2 的张量 %d 个（float16 存储本身有 ~1e-3）"
          % (worst[0][0], n_bad))
    print("PYNQ_OPS_OK" if ok else "PYNQ_OPS_FAIL")
    return 0 if ok else 1


def cmd_plan(args) -> int:
    """导出逐层参数包（预览 contract §4 的 5 个缓冲），并做契约 §5 判定。"""
    model = Path(args.model)
    golden_path = Path(args.golden)
    if not model.is_absolute():
        model = REPO / model
    if not golden_path.is_absolute():
        golden_path = REPO / golden_path
    graph = load_graph(model)
    golden = load_golden(golden_path)
    x = make_normal_input(graph.input_shape)
    plans = build_plan(graph, ranges_from_golden(graph, golden), x)
    pl = PlBackend(driver=None)

    layers = []
    n_pl = 0
    for p in plans:
        good, why = pl.supports(p)
        n_pl += int(good)
        layers.append({
            "name": p.conv_name,
            "in": p.in_name, "out": p.out_name,
            "geom": {"C_IN": p.c_in, "C_OUT": p.c_out, "H_IN": p.h_in, "W_IN": p.w_in,
                     "K": p.k, "stride": p.stride,
                     "pad": [p.pad_h_begin, p.pad_w_begin, p.pad_h_end, p.pad_w_end]},
            "H_OUT": p.h_out, "W_OUT": p.w_out,
            "quant": {"sx": p.sx, "zx": p.zx, "sp": p.sp, "zp_pre": p.zp_pre,
                      "so": p.so, "zo": p.zo, "act": p.act},
            "w_scale": [float(v) for v in p.w_scale],
            "bias_i32": [int(v) for v in p.bias_i32],
            "M": [int(v) for v in p.m],
            "S": [int(v) for v in p.s],
            "lut_sha1_head": None,  # 不塞 256 字节进 json，下面单独存 npz
            "macs": p.macs,
            "pl_eligible": good,
            "pl_blocked_by": why,
        })
    total_macs = sum(p.macs for p in plans)
    out_json = REPO / "data" / "results" / "pynq_net_plan.json"
    out_npz = REPO / "data" / "results" / "pynq_net_plan.npz"
    for p, d in zip(plans, layers):
        d["lut_sha1_head"] = hashlib_sha1(p.lut)
    _write_json(out_json, {
        "tool": "tools/pynq_net.py", "mode": "plan", "model": model.name,
        "contract": "docs/accel_interface.md",
        "conv_layers": len(plans), "pl_eligible": n_pl,
        "numpy_fallback": len(plans) - n_pl,
        "total_macs": total_macs, "total_gop": 2.0 * total_macs / 1e9,
        "note": "w_scale/bias_i32/M/S/lut 与契约 §2/§3 一一对应；wq 的字节序留给 export_layers.py",
        "layers": layers,
    })
    arrays = {}
    for p in plans:
        safe = p.conv_name.replace("/", "_")
        arrays[safe + "__w"] = p.w_q
        arrays[safe + "__lut"] = p.lut
    np.savez_compressed(out_npz, **arrays)
    print("逐层参数 -> %s" % out_json.relative_to(REPO))
    print("权重/LUT -> %s" % out_npz.relative_to(REPO))
    print("卷积 %d 层，可送 PL %d 层，numpy 兜底 %d 层，总 MAC %.4f G（%.4f GOP@2）"
          % (len(plans), n_pl, len(plans) - n_pl, total_macs / 1e9, 2.0 * total_macs / 1e9))
    print("PYNQ_PLAN_OK")
    return 0


def hashlib_sha1(arr: np.ndarray) -> str:
    import hashlib

    return hashlib.sha1(np.ascontiguousarray(arr).tobytes()).hexdigest()[:12]


def cmd_pl_dry_run(args) -> int:
    """不碰板子：只报「哪些层能送 PL、哪些必须兜底、为什么」。"""
    model = Path(args.model)
    golden_path = Path(args.golden)
    if not model.is_absolute():
        model = REPO / model
    if not golden_path.is_absolute():
        golden_path = REPO / golden_path
    graph = load_graph(model)
    golden = load_golden(golden_path)
    x = make_normal_input(graph.input_shape)
    plans = build_plan(graph, ranges_from_golden(graph, golden), x)
    pl = PlBackend(driver=None)
    print("=" * 78)
    print("pynq_net --backend pl --dry-run : 契约 §5 分层（不执行、不碰硬件）")
    print("=" * 78)
    n_pl = 0
    fallback: List[Tuple[str, List[str]]] = []
    mac_pl = 0
    mac_fb = 0
    for p in plans:
        good, why = pl.supports(p)
        if good:
            n_pl += 1
            mac_pl += p.macs
        else:
            fallback.append((p.conv_name, why))
            mac_fb += p.macs
    total = mac_pl + mac_fb
    print("卷积层 %d：可送 PL %d 层（%.4f GMAC，%.4f%%），numpy 兜底 %d 层（%.4f GMAC，%.4f%%）"
          % (len(plans), n_pl, mac_pl / 1e9, 100.0 * mac_pl / max(total, 1),
             len(fallback), mac_fb / 1e9, 100.0 * mac_fb / max(total, 1)))
    for nm, why in fallback:
        print("   - %-40s %s" % (nm, "; ".join(why)))
    print("")
    print("PlBackend 未实现项（全部 <待实测>，见 src/ps/pynq_driver/conv_driver.py）：")
    print("   - s_axilite 标量寄存器名（契约 §4 的 C_IN..zp_pre）在 hwh 里的实际命名")
    print("   - m_axi 指针寄存器名 / 物理地址写入方式（PYNQ allocate 的 buffer 是否可直用）")
    print("   - AP_DONE 轮询细节、超时量级")
    print("   - PL 写完输出后的 cache 失效（sync_from_device）")
    print("PYNQ_PLAN_OK")
    return 0


# ---------------------------------------------------------------------------
# 10. CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="YOLOv8n@320 整网 PC 侧编排（后端可插拔：numpy / PL）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--model", default=str(DEFAULT_MODEL.relative_to(REPO)),
                    help="ONNX 模型（相对仓库根）")
    ap.add_argument("--golden", default=str(DEFAULT_GOLDEN.relative_to(REPO)),
                    help="golden npz（相对仓库根）")
    ap.add_argument("--calib", default=None,
                    help="多图标定集：目录 / 逗号分隔路径 / 单文件（推荐用目录）")
    ap.add_argument("--input", default="normal",
                    help="normal=金标准同款伪随机；image/auto=真实图；或给图片路径")
    ap.add_argument("--conf", type=float, default=0.25, help="检测框分数阈值")
    ap.add_argument("--iou", type=float, default=0.45, help="NMS IoU 阈值")
    ap.add_argument("--tag", default="", help="输出 json 后缀")
    ap.add_argument("--json", default="", help="输出 json 路径（默认 data/results/pynq_net_<tag>.json）")
    ap.add_argument("--check", action="store_true", help="整网 INT8 校验（主判据）")
    ap.add_argument("--check-ops", action="store_true", help="纯 float32 逐算子 vs golden")
    ap.add_argument("--plan", action="store_true", help="导出逐层量化参数包")
    ap.add_argument("--backend", default="numpy", choices=["numpy", "pl"], help="卷积后端")
    ap.add_argument("--dry-run", action="store_true", help="--backend pl 时只做分层，不碰硬件")
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.check_ops:
        return cmd_check_ops(args)
    if args.plan:
        return cmd_plan(args)
    if args.backend == "pl":
        if args.dry_run:
            return cmd_pl_dry_run(args)
        print("PlBackend 需要 PL 侧 conv_engine 的 bitstream，目前没有实测过的实现。")
        print("现在只能跑：--backend pl --dry-run（分层判定，不碰硬件）")
        print("PYNQ_NET_NO_HARDWARE")
        return 3
    # 默认动作 = --check
    return cmd_check(args)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
