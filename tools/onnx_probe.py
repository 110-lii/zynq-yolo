"""统计 ONNX 模型的计算量与内存特征（用于判断 FPGA 可行性）。

为什么单独写这个脚本（而不是凭论文数字）：
    本项目要在 XC7Z020（220 DSP / 4.9 Mb BRAM）上跑 YOLO，
    决定 fps 的是 **MAC 总数**，决定能不能片上缓存的是 **权重字节数**。
    这两个量必须**在自己的模型文件上实测**，不能引用别人的表 —— 换模型/换分辨率就全变了。

用法：
    python tools/onnx_probe.py --model data/models/yolov8n.onnx
    python tools/onnx_probe.py --model data/models/yolov8n.onnx --json data/results/onnx_probe.json

判据（承 docs/repo_spec.md §6「数字必须标来源」）：
    本脚本输出的每个数字都来自**模型本身**，可重跑 ⇒ 可直接写进报告。
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
from typing import Dict, List

try:
    import onnx
    from onnx import TensorProto
except ImportError:
    sys.exit("需要 onnx： D:\\python\\python.exe -m pip install onnx")

# 每元素字节数（用于估权重体积）
BYTES = {
    TensorProto.FLOAT: 4,
    TensorProto.FLOAT16: 2,
    TensorProto.INT8: 1,
    TensorProto.UINT8: 1,
    TensorProto.INT16: 2,
    TensorProto.INT32: 4,
}


def _product(dims) -> int:
    n = 1
    for d in dims:
        if d <= 0:
            return 0
        n *= d
    return n


def probe(model_path: Path) -> Dict:
    m = onnx.load(str(model_path))
    # ★ 必须做一次 shape inference：ultralytics 导出的 onnx **不带** value_info，
    #   没有形状就算不出 Conv 的输出 H/W ⇒ MAC 会全部算成 0（实测踩过：
    #   首版直接读 g.value_info 得到 MAC 总数 0，而不是报错 —— 典型的"静默归零"）。
    try:
        m = onnx.shape_inference.infer_shapes(m, strict_mode=False)
    except Exception as e:  # 形状推断失败不致命，但要在结果里如实标记
        print("警告：shape_inference 失败（%s）—— MAC 可能不准" % e, file=sys.stderr)
    g = m.graph
    _SHAPE_CACHE.clear()

    # 初始值（权重/常量）→ 体积
    init_bytes = 0
    init_count = 0
    per_init: List[Dict] = []
    for t in g.initializer:
        n = _product(t.dims)
        b = n * BYTES.get(t.data_type, 4)
        init_bytes += b
        init_count += 1
        per_init.append({"name": t.name, "elems": n, "bytes": b})

    # 各算子的 MAC 估算：只统计真正做乘加的算子
    mac_total = 0
    per_node: List[Dict] = []
    op_counts: Dict[str, int] = {}
    for node in g.node:
        op_counts[node.op_type] = op_counts.get(node.op_type, 0) + 1
        mac = 0
        op = node.op_type
        try:
            if op in ("Conv", "ConvInteger", "QLinearConv"):
                # 权重形状 [M, C/group, kH, kW]，输出形状由 value_info 推
                w = next((t for t in g.initializer if t.name == node.input[1]), None)
                if w is not None and len(w.dims) == 4:
                    M, Cg, kH, kW = w.dims
                    group = 1
                    for a in node.attribute:
                        if a.name == "group":
                            group = a.i
                    # 输出空间尺寸：从 shape 推断；拿不到就跳过（不瞎猜）
                    out_shape = _shape_of(g, node.output[0])
                    if out_shape and len(out_shape) >= 4:
                        Ho, Wo = out_shape[2], out_shape[3]
                        mac = int(M) * (int(Cg) * int(kH) * int(kW)) * int(Ho) * int(Wo)
            elif op == "MatMul":
                a = _shape_of(g, node.input[0])
                b = _shape_of(g, node.input[1])
                if a and b and len(a) >= 2 and len(b) >= 2:
                    mac = int(a[-2]) * int(a[-1]) * int(b[-1])
            elif op == "Gemm":
                a = _shape_of(g, node.input[0])
                w = next((t for t in g.initializer if t.name == node.input[1]), None)
                if a and w is not None and len(w.dims) == 2:
                    mac = int(a[-2]) * int(w.dims[0]) * int(w.dims[1])
        except Exception:
            mac = 0
        if mac:
            mac_total += mac
            per_node.append({"op": op, "name": node.name, "mac": mac})

    per_node.sort(key=lambda x: -x["mac"])

    return {
        "model": str(model_path),
        "onnx_ir_version": m.ir_version,
        "opset": [f"{o.domain or 'ai.onnx'}:{o.version}" for o in m.opset_import],
        "nodes": len(g.node),
        "initializers": init_count,
        "weight_bytes": init_bytes,
        "weight_MB": round(init_bytes / 1e6, 4),
        "weight_MiB": round(init_bytes / (1024 ** 2), 4),
        "params_estimate": sum(p["elems"] for p in per_init),
        "mac_total": mac_total,
        "GMAC": round(mac_total / 1e9, 4),
        "GOP": round(mac_total * 2 / 1e9, 4),
        "op_histogram": dict(sorted(op_counts.items(), key=lambda kv: -kv[1])),
        "top_mac_layers": per_node[:15],
        "mac_concentration_top5": round(
            sum(x["mac"] for x in per_node[:5]) / mac_total, 4) if mac_total else 0,
        "top5_weight_layers": sorted(per_init, key=lambda x: -x["bytes"])[:8],
    }


_SHAPE_CACHE: Dict[str, object] = {}


def _shape_of(g, name: str):
    """从 value_info / 输入 / 输出里找张量形状；找不到返回 None。"""
    if not _SHAPE_CACHE:
        for coll in (g.value_info, g.input, g.output):
            for vi in coll:
                dims = [d.dim_value for d in vi.type.tensor_type.shape.dim]
                _SHAPE_CACHE[vi.name] = dims  # type: ignore[index]
    return _SHAPE_CACHE.get(name)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="统计 ONNX 模型的 MAC 与权重体积")
    ap.add_argument("--model", required=True, help="onnx 文件")
    ap.add_argument("--json", default=None, help="把完整结果写成 json")
    args = ap.parse_args(argv)

    p = Path(args.model)
    if not p.is_file():
        print("找不到模型：%s" % p, file=sys.stderr)
        return 2

    r = probe(p)

    print("=" * 68)
    print("ONNX 探针：%s" % r["model"])
    print("=" * 68)
    print("  节点数 / 权重张量数   : %d / %d" % (r["nodes"], r["initializers"]))
    print("  可量化参数总数        : %d  (%.3f M)" % (r["params_estimate"], r["params_estimate"] / 1e6))
    print("  权重体积 FP32         : %.4f MB (%.4f MiB)" % (r["weight_MB"], r["weight_MiB"]))
    print("  ★ INT16               : %.4f MB" % (r["params_estimate"] * 2 / 1e6))
    print("  ★ INT8                : %.4f MB" % (r["params_estimate"] * 1 / 1e6))
    print("  MAC 总数              : %d  (%.4f GMAC)" % (r["mac_total"], r["GMAC"]))
    print("  运算量（2 op / MAC）  : %.4f GOP/帧" % r["GOP"])
    print("  前 5 层 MAC 集中度    : %.1f%%" % (r["mac_concentration_top5"] * 100))
    print("")
    print("  —— 对 XC7Z020 的含义（BRAM 4.9 Mb = 0.613 MB，DSP 220）——")
    int8_mb = r["params_estimate"] / 1e6
    print("  INT8 权重 / BRAM 倍数 : %.1fx  %s"
          % (int8_mb / 0.613,
             "（放不进片上，必须 DDR 逐层流）" if int8_mb > 0.613 else "（可望片上缓存）"))
    if r["GOP"] > 0:
        # 以实测持续算力 10.45 GOPS（ARC2020 同芯片）为基数
        print("  单帧延迟 @10.45 GOPS  : %.1f ms  =>  %.2f fps（估算，基线取 ARC2020 实测）"
              % (r["GOP"] / 10.45 * 1000, 10.45 / r["GOP"]))
        print("  同上 @22 GOPS（乐观） : %.1f ms  =>  %.2f fps（估算，需 2x 效率）"
              % (r["GOP"] / 22.0 * 1000, 22.0 / r["GOP"]))
    else:
        print("  ⚠ MAC 统计为 0 —— 形状推断失败，**不得**据此写任何 fps 结论")
        print("    探测到 %d 个带权重的 Conv/MatMul 节点，请检查 shape inference"
              % len([n for n in g.node if n.op_type in ("Conv", "MatMul", "Gemm")]))
    print("")
    print("  —— op 直方图 ——")
    for k, v in list(r["op_histogram"].items())[:12]:
        print("    %-22s %d" % (k, v))
    print("")
    print("  —— MAC 最大的 8 个节点 ——")
    for x in r["top_mac_layers"][:8]:
        print("    %-10s %-34s %12d MAC  (%.1f%%)"
              % (x["op"], (x["name"] or "")[:34], x["mac"], x["mac"] / r["mac_total"] * 100))
    print("")
    print("PROBE_OK")

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        # ★ 显式 newline="\n"：默认 write_text 在 Windows 上写 CRLF，与 `.gitattributes` 的
        #   `* text=auto eol=lf` 冲突 ⇒ git add 时被规范化，「仓库里的副本 == 本地文件」当场失效。
        #   这是一个已经踩过两次的坑（ai/raw 分片、本 JSON），故所有写出点一律显式 LF。
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(r, indent=2, ensure_ascii=False))
        print("  已写 %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
