#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""int8_sim.py —— 「只把 Conv 量化成 INT8、其余算子仍走 float32」的整网精度模拟。

为什么需要它
------------
PL 侧加速器只负责卷积（`src/hls/accel/`），激活/池化/拼接/上采样/DFL 解码都交给
PS 侧 numpy。所以真实系统里的数值口径是：

    Conv    : int8 输入 × int8 权重 -> int32 累加 -> per-channel requant -> int8 输出
    其余算子 : float32（读 int8 时反量化为 float，写出时再量化）

本脚本用 onnxruntime 的 **QDQ 静态量化**（`op_types_to_quantize=['Conv']`）把这套口径
模拟出来，然后和 float32 原始输出比对。目的不是"跑得快"，而是**在写 HLS 之前先确认
这个量化方案能不能用**——如果整网 INT8 后检测结果已经崩了，那 HLS 写对也没意义。

★ 一个必须知道的口径细节
    QDQ 模型里 Conv 仍是 float 实现，但**输入已被舍入到 int8 网格**，而 int8×int8 的
    乘积在 float32 里是精确的（|127×127|=16129 < 2^24），所以累加结果与硬件 int32
    累加**逐位相同**。差异只出现在最后一步 requant 的舍入方式上（ORT 用 float 舍入，
    硬件用 `M >> shift` 定点），量级 1 LSB，不影响本步结论。

用法
----
    python tools/int8_sim.py                        # 用 data/samples 里的真实图
    python tools/int8_sim.py --input normal         # 用金标准同款伪随机输入（可复现对比）
    python tools/int8_sim.py --model data/models/yolov8n_320.onnx
"""

from __future__ import annotations

import sys

# 不生成 __pycache__：本仓库的 tools/check_repo.py 检查 d) 是**扫盘**的，不看 .gitignore，
# 留下 .pyc 会直接判 FAIL。实测语义（勿想当然）：模块**自身体内**置位拦不住自己的 .pyc
# （CPython 先编译并写缓存，再执行模块体），只有在本模块 import 别的本地模块**之前**
# 置位才有效。所以这段必须放在所有 import 之前。
sys.dont_write_bytecode = True

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

REPO = Path(__file__).resolve().parents[1]
SAMPLES = REPO / "data" / "samples"
OUT_DIR = REPO / "data" / "results"

# ---------------------------------------------------------------------------
# 输入准备
# ---------------------------------------------------------------------------
def load_image_input(path: Path, size: int) -> np.ndarray:
    """读一张图并预处理成 ultralytics 的推理输入：BGR->RGB、resize、/255、CHW、NCHW。

    刻意不用 ultralytics 的 LetterBox：这里要的是**确定性**的预处理，好让
    float32 与 INT8 两条链路吃到**逐位相同**的输入。LetterBox 的填充量会
    随图片尺寸变化，反而不便于复现。
    """
    import cv2

    img = cv2.imread(str(path))
    if img is None:
        raise SystemExit("读不到图片：%s" % path)
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    img = cv2.resize(img, (size, size), interpolation=cv2.INTER_LINEAR)
    x = img.astype(np.float32) / 255.0
    x = np.transpose(x, (2, 0, 1))[None, ...]
    return np.ascontiguousarray(x, dtype=np.float32)


def make_normal_input(shape: List[int], seed: int = 20261019) -> np.ndarray:
    """与 tools/make_golden.py 完全相同的确定性伪随机输入（seed 一致）。"""
    n, c, h, w = shape
    rng = np.random.default_rng(seed)
    return rng.standard_normal((n, c, h, w), dtype=np.float32)


# ---------------------------------------------------------------------------
# 校准数据读取器
# ---------------------------------------------------------------------------
class OnceReader:
    """把一张输入喂给 onnxruntime 的静态量化校准器（只喂一次）。

    刻意**不**继承 CalibrationDataReader：那个基类只是鸭子类型的便利包装，
    这里显式实现 `get_next()` 就够，能少一个 import 依赖面。
    """

    def __init__(self, name: str, x: np.ndarray) -> None:
        self._name = name
        self._x = x
        self._used = False

    def get_next(self) -> Optional[Dict[str, np.ndarray]]:
        if self._used:
            return None
        self._used = True
        return {self._name: self._x}


# ---------------------------------------------------------------------------
# 跑一次 ORT，拿指定名字的输出
# ---------------------------------------------------------------------------
def run_ort(model_path: Path, feed: Dict[str, np.ndarray], want: List[str]) -> Dict[str, np.ndarray]:
    import onnxruntime as ort

    so = ort.SessionOptions()
    so.log_severity_level = 3
    sess = ort.InferenceSession(str(model_path), so, providers=["CPUExecutionProvider"])
    return dict(zip(want, sess.run(want, feed)))


# ---------------------------------------------------------------------------
# 误差度量
# ---------------------------------------------------------------------------
def compare(a: np.ndarray, b: np.ndarray) -> Dict[str, float]:
    """比较两个同形状数组：a = 参考(float32)，b = 被测(INT8 模拟)。

    为什么同时给「全体」和「非零」两套误差：YOLO 输出里有大量精确 0（padding 区、
    被 sigmoid 压到 0 的分支），全体相对误差会被这些小分母放大得毫无意义。
    """
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    if a.shape != b.shape:
        return {"shape_mismatch": 1.0, "a_shape": list(a.shape), "b_shape": list(b.shape)}  # type: ignore[dict-item]

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
        out["nonzero_frac"] = float(np.mean(nz))
        out["rel_err_p50"] = float(np.percentile(rel, 50))
        out["rel_err_p99"] = float(np.percentile(rel, 99))
        out["rel_err_max"] = float(np.max(rel))
    return out


# ---------------------------------------------------------------------------
# 从 QDQ 模型里抽出每个 Conv 的 scale / zero_point（HLS 要用）
# ---------------------------------------------------------------------------
def dump_qdq_scales(qdq_path: Path) -> Dict[str, dict]:
    import onnx
    from onnx import numpy_helper

    m = onnx.load(str(qdq_path))
    init = {i.name: numpy_helper.to_array(i) for i in m.graph.initializer}

    def read_q(node) -> Optional[dict]:
        """把一个 QuantizeLinear 节点的 scale/zp 读成可序列化的 dict。"""
        if len(node.input) < 2:
            return None
        s = init.get(node.input[1])
        if s is None:
            return None
        z = init.get(node.input[2]) if len(node.input) > 2 else np.array([0], dtype=np.int8)
        return {
            "scale": np.atleast_1d(s).astype(np.float64).tolist(),
            "zero_point": np.atleast_1d(z).astype(np.int64).tolist() if z is not None else [0],
            "per_channel": int(np.atleast_1d(s).size > 1),
        }

    out: Dict[str, dict] = {}
    for n in m.graph.node:
        if n.op_type != "Conv":
            continue
        # QDQ 下 Conv 的输入名形如 "<tensor>_quantized"
        entry: Dict[str, object] = {"name": n.name}
        for i, inp in enumerate(n.input):
            role = {0: "act", 1: "w", 2: "bias"}.get(i, "in%d" % i)
            entry[role] = inp
        out[n.name] = entry

    # 再扫一遍 QuantizeLinear，按它输出的名字索引，方便按 scale 查
    q_by_name: Dict[str, dict] = {}
    for n in m.graph.node:
        if n.op_type == "QuantizeLinear":
            q = read_q(n)
            if q is not None:
                q_by_name[n.output[0]] = q
    return {"convs": out, "quantize_nodes": q_by_name}


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Conv-only INT8 整网精度模拟")
    ap.add_argument("--model", default=str(REPO / "data" / "models" / "yolov8n_320.onnx"))
    ap.add_argument("--input", default="auto",
                    help="auto=优先用 data/samples 里的真实图；normal=金标准同款伪随机；或给图片路径")
    ap.add_argument("--tag", default="", help="输出文件名后缀，避免不同输入互相覆盖")
    args = ap.parse_args(argv)

    model = Path(args.model)
    if not model.is_absolute():
        model = REPO / model
    if not model.exists():
        print("模型不存在：%s" % model)
        return 2

    import onnx

    m = onnx.load(str(model))

    # ★ QDQ 量化器会把 Conv 的 bias 量化成 int32，并给 DequantizeLinear 加 `axis` 属性；
    #   而 int32 输入的 DequantizeLinear **只有 opset >= 21 才有**。本模型导出时是
    #   opset 12（ultralytics 默认），直接量化会得到 ORT 拒绝加载的非法模型：
    #     Error Unrecognized attribute: axis for operator DequantizeLinear
    #   ⇒ 先做一次纯算子版本升级（不动数值、不动权重），再量化。
    #   这一步只影响"模拟"，不进硬件产物。
    cur = next((o.version for o in m.opset_import if o.domain in ("", "ai.onnx")), 0)
    if cur < 21:
        from onnx import version_converter

        m = version_converter.convert_version(m, 21)
        bump = REPO / "build" / "onnx_opset21_conv"
        bump.parent.mkdir(parents=True, exist_ok=True)
        bump = bump.with_name(bump.name + "_" + model.stem + ".onnx")
        onnx.save(m, str(bump))
        print("      opset %d -> 21（临时副本 %s）" % (cur, bump.relative_to(REPO)))
        model = bump

    inp = m.graph.input[0]
    shape = [d.dim_value for d in inp.type.tensor_type.shape.dim]
    in_name = inp.name
    out_names = [o.name for o in m.graph.output]
    size = int(shape[-1])

    # ---- 选输入 ----
    if args.input == "normal":
        x = make_normal_input(shape)
        input_desc = "确定性伪随机 default_rng(20261019)（与 data/golden 同款）"
    elif args.input == "auto":
        cand = [p for p in (["zidane.jpg"] and [SAMPLES / "zidane.jpg"]) if p.exists()]
        if cand:
            x = load_image_input(cand[0], size)
            input_desc = "真实图片 %s" % cand[0].name
        else:
            x = make_normal_input(shape)
            input_desc = "确定性伪随机 default_rng(20261019)（未找到 data/samples/zidane.jpg）"
    else:
        p = Path(args.input)
        if not p.is_absolute():
            p = REPO / p
        x = load_image_input(p, size)
        input_desc = "真实图片 %s" % p.name

    print("=" * 74)
    print("Conv-only INT8 精度模拟")
    print("  模型   : %s" % model.relative_to(REPO))
    print("  输入   : %s %s" % (in_name, shape))
    print("  数据   : %s" % input_desc)
    print("  取值范围 min=%.4f max=%.4f" % (float(x.min()), float(x.max())))
    print("=" * 74)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tag = args.tag or ("normal" if args.input == "normal" else "image")
    qdq_path = OUT_DIR / ("int8_%s_qdq.onnx" % tag)

    # ---- ① 静态量化：只把 Conv 变 INT8 ----
    from onnxruntime.quantization import (CalibrationDataReader, QuantFormat,
                                          QuantType, quantize_static)

    class _Reader(CalibrationDataReader):
        """ORT 要求的是 CalibrationDataReader 子类，这里薄包装 OnceReader。"""

        def __init__(self) -> None:
            self._inner = OnceReader(in_name, x)

        def get_next(self):  # type: ignore[override]
            return self._inner.get_next()

    print("[1/3] 静态量化（QuantFormat=QDQ, 只量化 Conv, per_channel 权重）...")
    # ★ 激活必须用 QUInt8，不能用 QInt8 —— 实测 QInt8 会生成一个 ORT 拒绝加载的模型：
    #     Node (/model.22/Reshape_output_0_q) Op (QuantizeLinear)
    #     [TypeInferenceError] output_dtype INT8 does not match y_zero_point type UINT8
    #   这是 ORT 量化器在 DFL/Reshape 一带的 dtype 归类缺陷（opset 12 与 21 均复现）。
    #   QUInt8 能正常加载，且正好对应多数 INT8 加速器的实际口径（uint8 激活 + zero_point）。
    quantize_static(
        model_input=str(model),
        model_output=str(qdq_path),
        calibration_data_reader=_Reader(),
        quant_format=QuantFormat.QDQ,
        per_channel=True,
        activation_type=QuantType.QUInt8,
        weight_type=QuantType.QInt8,
        op_types_to_quantize=["Conv"],
        extra_options={"ActivationSymmetric": False, "WeightSymmetric": True},
    )
    print("      -> %s (%.1f KB)" % (qdq_path.relative_to(REPO), qdq_path.stat().st_size / 1024))

    # ---- ② 两条链路各跑一次 ----
    print("[2/3] 跑 float32 原始模型与 INT8 模拟模型...")
    ref = run_ort(model, {in_name: x}, out_names)
    sim = run_ort(qdq_path, {in_name: x}, out_names)

    # ---- ③ 比对 ----
    print("[3/3] 比对最终输出：")
    report: Dict[str, object] = {
        "model": str(model.relative_to(REPO)),
        "input": input_desc,
        "input_shape": shape,
        "outputs": {},
    }
    ok = True
    for name in out_names:
        c = compare(ref[name], sim[name])
        report["outputs"][name] = c  # type: ignore[index]
        print("  [%s] shape=%s" % (name, list(ref[name].shape)))
        for k in ("max_err_over_maxabs", "mean_err_over_maxabs", "rel_err_p50", "rel_err_p99", "cosine"):
            if k in c:
                print("      %-22s %.6g" % (k, c[k]))
        if c.get("cosine", 0.0) < 0.999:
            ok = False

    # ---- 抽 scale（给 HLS 用）----
    scales = dump_qdq_scales(qdq_path)
    n_q = len(scales["quantize_nodes"])  # type: ignore[arg-type]
    report["quantize_node_count"] = n_q
    (OUT_DIR / ("int8_%s_report.json" % tag)).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    (OUT_DIR / ("int8_%s_scales.json" % tag)).write_text(
        json.dumps(scales, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print()
    print("QDQ 里 QuantizeLinear 节点数 = %d" % n_q)
    print("明细 -> %s" % (OUT_DIR / ("int8_%s_report.json" % tag)).relative_to(REPO))
    print("scale -> %s" % (OUT_DIR / ("int8_%s_scales.json" % tag)).relative_to(REPO))
    print("INT8_SIM_OK" if ok else "INT8_SIM_DEGRADED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
