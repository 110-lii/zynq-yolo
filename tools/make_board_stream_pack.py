#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""tools/make_board_stream_pack.py -- PC 侧生成板端可执行的流式测试包。

产出（只写这三个位置）：
    data/layers/stream_pack/<key>/{w,params,lut}.bin
    data/layers/board_net_plan.json
    build/stream/reference_output.bin + reference.json

板端只需要 tools/pynq_net.py + 上述 JSON/二进制，不需要 onnx / onnxruntime / cv2。

数值口径完全复用 tools/pynq_net.py：
    load_graph -> ranges_from_ort -> build_plan(act="none")
不使用 data/layers/yolov8n_320_layers.json 的融合 act="silu" 参数包。
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # 必须在任何本地 import 之前：不得生成 __pycache__

import argparse
import base64
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import pynq_net as pn  # noqa: E402

SCHEMA = "zynq-yolo/board-stream-plan/v1"
CONF_THR = 0.25
IOU_THR = 0.45


# ---------------------------------------------------------------------------
# 小工具：ASCII / JSON 安全序列化
# ---------------------------------------------------------------------------
def _ascii(text: Any) -> str:
    return str(text).encode("ascii", "replace").decode("ascii")


def _one_line(text: Any) -> str:
    return " ".join(_ascii(text).split())


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _array_le_bytes(arr: np.ndarray) -> bytes:
    """连续、小端、无头部的裸数组字节（与契约 §2/§9 一致）。"""
    a = np.asarray(arr)
    if a.dtype.byteorder == ">" and a.dtype.itemsize > 1:
        a = a.astype(a.dtype.newbyteorder("<"))
    return np.ascontiguousarray(a).tobytes()


def _ser(value: Any) -> Any:
    """Node attr 的递归 JSON 序列化。

    ndarray -> {"__ndarray__": true, dtype, shape, data_b64}
    bytes   -> {"__bytes__": true, data_b64}
    list/tuple -> list（递归）
    int/float/str/bool/None 原样
    """
    if isinstance(value, np.ndarray):
        return {
            "__ndarray__": True,
            "dtype": str(value.dtype),
            "shape": [int(v) for v in value.shape],
            "data_b64": _b64(_array_le_bytes(value)),
        }
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, (bytes, bytearray)):
        return {"__bytes__": True, "data_b64": _b64(bytes(value))}
    if isinstance(value, (list, tuple)):
        return [_ser(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _ser(v) for k, v in value.items()}
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise TypeError("无法序列化的 attr 类型：%s" % type(value).__name__)


def _deser(value: Any) -> Any:
    """_ser 的逆过程（自校验时从 JSON 重建 Node attrs）。"""
    if isinstance(value, dict):
        if value.get("__ndarray__"):
            return _decode_array(value)
        if value.get("__bytes__"):
            return base64.b64decode(value["data_b64"])
        return {k: _deser(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deser(v) for v in value]
    return value


def _decode_array(rec: Dict[str, Any]) -> np.ndarray:
    dtype = np.dtype(str(rec["dtype"]))
    raw = base64.b64decode(rec["data_b64"])
    if dtype.itemsize > 1:
        dtype = dtype.newbyteorder("<")
    arr = np.frombuffer(raw, dtype=dtype).reshape([int(v) for v in rec["shape"]]).copy()
    return arr.astype(np.dtype(str(rec["dtype"])), copy=False)


def _layer_key(conv_name: str) -> str:
    """与 tools/export_layers.py 的 layer_key() 完全一致。"""
    return conv_name.replace("/", "_").replace(":", "_")


def _resolve(path: Path) -> Path:
    return path if path.is_absolute() else (REPO / path)


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # ensure_ascii=True：JSON 本身也保持纯 ASCII，板端 3.8 的 json 直接可读。
    path.write_text(json.dumps(obj, ensure_ascii=True, indent=2) + "\n",
                    encoding="utf-8", newline="\n")


# ---------------------------------------------------------------------------
# 1. 逐层二进制包
# ---------------------------------------------------------------------------
def _write_layer_bins(plan_dir: Path, p: pn.LayerPlan) -> Dict[str, int]:
    key = _layer_key(p.conv_name)
    d = plan_dir / key
    d.mkdir(parents=True, exist_ok=True)

    w = np.ascontiguousarray(p.w_q, dtype=np.int8)
    if w.shape != (p.c_out, p.c_in * p.k * p.k):
        raise RuntimeError("权重形状不符 %s: %s" % (p.conv_name, w.shape))

    params = np.empty((p.c_out, 3), dtype="<i4")
    params[:, 0] = p.bias_i32.astype("<i4", copy=False)
    params[:, 1] = p.m.astype("<i4", copy=False)
    params[:, 2] = p.s.astype("<i4", copy=False)

    lut = np.ascontiguousarray(p.lut, dtype=np.uint8)
    if lut.shape != (256,):
        raise RuntimeError("LUT 形状不符 %s: %s" % (p.conv_name, lut.shape))

    payloads = {
        "w": w.tobytes(),
        "params": params.tobytes(),
        "lut": lut.tobytes(),
    }
    for name, raw in payloads.items():
        (d / (name + ".bin")).write_bytes(raw)

    return {
        "w": int(w.size * w.dtype.itemsize),
        "params": int(params.size * params.dtype.itemsize),
        "lut": int(lut.size * lut.dtype.itemsize),
    }


def _layer_record(p: pn.LayerPlan, pack_rel: str = "stream_pack") -> Dict[str, Any]:
    good, _why = p.vs_contract()
    if not (p.pad_h_begin == p.pad_h_end == p.pad_w_begin == p.pad_w_end):
        raise RuntimeError(
            "非对称 pad 无单值 geom.pad 可表达：%s (%d,%d,%d,%d)"
            % (p.conv_name, p.pad_h_begin, p.pad_h_end, p.pad_w_begin, p.pad_w_end))
    key = _layer_key(p.conv_name)
    return {
        "conv_name": p.conv_name,
        "key": key,
        "in_name": p.in_name,
        "out_name": p.out_name,
        "geom": {
            "C_IN": int(p.c_in),
            "C_OUT": int(p.c_out),
            "H_IN": int(p.h_in),
            "W_IN": int(p.w_in),
            "H_OUT": int(p.h_out),
            "W_OUT": int(p.w_out),
            "K": int(p.k),
            "stride": int(p.stride),
            "pad": int(p.pad_h_begin),
        },
        "quant": {
            "sx": float(p.sx),
            "zx": int(p.zx),
            "sp": float(p.sp),
            "zp_pre": int(p.zp_pre),
            "so": float(p.so),
            "zo": int(p.zo),
        },
        "act": "none",
        "unsupported": bool(not good),
        "files": {
            "w": "%s/%s/w.bin" % (pack_rel, key),
            "params": "%s/%s/params.bin" % (pack_rel, key),
            "lut": "%s/%s/lut.bin" % (pack_rel, key),
        },
        "macs": int(p.macs),
    }


# ---------------------------------------------------------------------------
# 2. 参考输出与检测
# ---------------------------------------------------------------------------
def _decode_yolov5(out: np.ndarray, imgsz: int) -> Dict[str, Any]:
    """Classic YOLOv5 ONNX output0 is [1, N, 85] with xywh already in pixels.

    Channels: x, y, w, h, obj, 80 class probabilities (obj/cls already sigmoid).
    """
    pred = np.asarray(out, dtype=np.float32)[0]
    if pred.ndim != 2 or pred.shape[1] != 85:
        raise RuntimeError("YOLOv5 output0 shape is not [N,85]: %s" % (pred.shape,))
    xywh = pred[:, :4]
    obj = pred[:, 4]
    cls_prob = pred[:, 5:]
    cls = np.argmax(cls_prob, axis=1)
    conf = obj * cls_prob[np.arange(cls.size), cls]
    keep = conf >= CONF_THR
    xywh = xywh[keep]
    conf = conf[keep]
    cls = cls[keep]
    xyxy = np.empty_like(xywh)
    xyxy[:, 0] = xywh[:, 0] - xywh[:, 2] * 0.5
    xyxy[:, 1] = xywh[:, 1] - xywh[:, 3] * 0.5
    xyxy[:, 2] = xywh[:, 0] + xywh[:, 2] * 0.5
    xyxy[:, 3] = xywh[:, 1] + xywh[:, 3] * 0.5
    np.clip(xyxy[:, 0::2], 0, imgsz, out=xyxy[:, 0::2])
    np.clip(xyxy[:, 1::2], 0, imgsz, out=xyxy[:, 1::2])
    keep2 = pn.nms(xyxy, conf, cls.astype(np.float32), IOU_THR)
    return {
        "boxes": xyxy[keep2],
        "scores": conf[keep2],
        "cls": cls[keep2],
        "n_candidates": int(keep.sum()),
    }


def _reference(out: np.ndarray, image_path: Path, wall_time_s: float,
               imgsz: int) -> Tuple[bytes, Dict[str, Any]]:
    out_le = np.ascontiguousarray(out, dtype="<f4")
    raw = out_le.tobytes()
    if out.ndim == 3 and out.shape[-1] == 85:
        det = _decode_yolov5(out, imgsz)
        boxes = det["boxes"]
        scores = det["scores"]
        cls = det["cls"]
        n_candidates = det["n_candidates"]
    else:
        det = pn.decode_detections(out, CONF_THR)
        keep = pn.nms(det["boxes"], det["scores"], det["cls"], IOU_THR)
        boxes = det["boxes"][keep]
        scores = det["scores"][keep]
        cls = det["cls"][keep]
        n_candidates = len(det["boxes"])
    meta = {
        "image": str(image_path),
        "input_shape": [1, 3, imgsz, imgsz],
        "output_shape": [int(v) for v in out.shape],
        "sha256": hashlib.sha256(raw).hexdigest(),
        "wall_time_s": float(wall_time_s),
        "detections": {
            "conf_thr": CONF_THR,
            "iou_thr": IOU_THR,
            "n_candidates": int(n_candidates),
            "count": int(len(boxes)),
            "boxes": [[float(v) for v in row] for row in boxes],
            "scores": [float(v) for v in scores],
            "cls": [int(v) for v in cls],
        },
    }
    return raw, meta


# ---------------------------------------------------------------------------
# 3. 从写出的 JSON + 二进制重建并自校验
# ---------------------------------------------------------------------------
def _pack_file(plan_path: Path, plan_dir: Path, rec: Dict[str, Any],
               kind: str, ext: str) -> Path:
    rel = Path(str(rec["files"][kind]))
    cand = plan_path.parent / rel
    if cand.is_file():
        return cand
    alt = plan_dir / str(rec["key"]) / ("%s.%s" % (kind, ext))
    if alt.is_file():
        return alt
    raise FileNotFoundError("包文件不存在：%s（JSON 指向 %s）" % (alt, cand))


def _reload_and_check(plan_path: Path, plan_dir: Path,
                      x: np.ndarray, ref: np.ndarray) -> float:
    obj = json.loads(plan_path.read_text(encoding="utf-8"))
    if obj.get("schema") != SCHEMA:
        raise RuntimeError("schema 不符：%r" % obj.get("schema"))

    nodes: List[pn.Node] = []
    for idx, n in enumerate(obj["nodes"]):
        nodes.append(pn.Node(
            idx=idx,
            op_type=str(n["op_type"]),
            name=str(n["name"]),
            inputs=[str(v) for v in n["inputs"]],
            outputs=[str(v) for v in n["outputs"]],
            attrs={str(k): _deser(v) for k, v in n.get("attrs", {}).items()},
        ))

    inits: Dict[str, np.ndarray] = {
        str(k): _decode_array(v) for k, v in obj["initializers"].items()
    }

    graph2 = pn.Graph(
        path=plan_path,
        nodes=nodes,
        initializers=inits,
        input_name=str(obj["input_name"]),
        input_shape=[int(v) for v in obj["input_shape"]],
        output_names=[str(obj["output_name"])],
        opset=0,
    )

    plans2: List[pn.LayerPlan] = []
    for conv_name, rec in obj["layers"].items():
        geom = rec["geom"]
        quant = rec["quant"]
        key = str(rec["key"])
        if key != _layer_key(conv_name):
            raise RuntimeError("key 与 conv_name 不符：%s vs %s" % (key, conv_name))
        c_in = int(geom["C_IN"])
        c_out = int(geom["C_OUT"])
        k = int(geom["K"])
        expected = {
            "w": c_out * c_in * k * k,
            "params": c_out * 3 * 4,
            "lut": 256,
        }
        for kind, size in expected.items():
            pth = _pack_file(plan_path, plan_dir, rec, kind, "bin")
            actual = pth.stat().st_size
            if actual != size:
                raise RuntimeError(
                    "文件大小不符 %s/%s.bin: %d != %d" % (key, kind, actual, size))

        w_path = _pack_file(plan_path, plan_dir, rec, "w", "bin")
        p_path = _pack_file(plan_path, plan_dir, rec, "params", "bin")
        l_path = _pack_file(plan_path, plan_dir, rec, "lut", "bin")

        w_q = np.fromfile(str(w_path), dtype=np.int8).reshape(c_out, c_in * k * k)
        params = np.fromfile(str(p_path), dtype="<i4").reshape(c_out, 3)
        lut = np.fromfile(str(l_path), dtype=np.uint8)

        plans2.append(pn.LayerPlan(
            conv_name=conv_name,
            in_name=str(rec["in_name"]),
            out_name=str(rec["out_name"]),
            c_in=c_in,
            c_out=c_out,
            k=k,
            stride=int(geom["stride"]),
            pad_h_begin=int(geom["pad"]),
            pad_h_end=int(geom["pad"]),
            pad_w_begin=int(geom["pad"]),
            pad_w_end=int(geom["pad"]),
            h_in=int(geom["H_IN"]),
            w_in=int(geom["W_IN"]),
            h_out=int(geom["H_OUT"]),
            w_out=int(geom["W_OUT"]),
            # 板端 INT8 路径不用 w_f / w_scale / bias_f；不序列化、不臆造数值。
            w_f=np.zeros((c_out, c_in, k, k), dtype=np.float32),
            w_q=w_q,
            w_scale=np.zeros((c_out,), dtype=np.float32),
            bias_f=None,
            bias_i32=np.array(params[:, 0], dtype=np.int32, copy=True),
            m=np.array(params[:, 1], dtype=np.int32, copy=True),
            s=np.array(params[:, 2], dtype=np.int32, copy=True),
            sx=float(quant["sx"]),
            zx=int(quant["zx"]),
            sp=float(quant["sp"]),
            zp_pre=int(quant["zp_pre"]),
            so=float(quant["so"]),
            zo=int(quant["zo"]),
            act="none",
            lut=lut,
            macs=int(rec["macs"]),
        ))

    tensors2, _stats2 = pn.run_network(graph2, x, pn.NumpyBackend(), plans2)
    out2 = tensors2[str(obj["output_name"])]

    print("self-check: layer files verified=%d (w/params/lut sizes)" % len(plans2))
    cmp = pn.compare(ref, out2)
    bytes_identical = (np.ascontiguousarray(ref).tobytes()
                       == np.ascontiguousarray(out2).tobytes())
    max_abs_err = float(cmp.get("max_abs_err", float("inf")))
    cosine = float(cmp.get("cosine", -1.0))
    if bytes_identical:
        # 逐字节相同的两个向量的余弦在数学上恒为 1；避免 dot/norm 的末位舍入
        # 把判据误报成 0.9999999999999999。
        cosine = 1.0
        max_abs_err = 0.0

    print("self-check: max_abs_err=%.17g cosine=%.17g bytes_identical=%s"
          % (max_abs_err, cosine, "yes" if bytes_identical else "no"))
    if not bytes_identical or max_abs_err != 0.0 or cosine != 1.0:
        raise RuntimeError(
            "自校验失败：max_abs_err=%r cosine=%r bytes_identical=%s"
            % (max_abs_err, cosine, bytes_identical))
    return cosine


# ---------------------------------------------------------------------------
# 4. 主流程
# ---------------------------------------------------------------------------
def build(args: argparse.Namespace) -> int:
    model = _resolve(Path(args.model))
    image = _resolve(Path(args.image))
    plan_dir = _resolve(Path(args.out_dir))
    plan_path = _resolve(Path(args.plan_out))
    ref_dir = _resolve(Path(args.ref_dir))

    if not model.is_file():
        raise FileNotFoundError("模型不存在：%s" % model)
    if not image.is_file():
        raise FileNotFoundError("图片不存在：%s" % image)

    imgsz = int(args.imgsz)
    t0 = time.perf_counter()
    graph = pn.load_graph(model)
    if [int(v) for v in graph.input_shape] != [1, 3, imgsz, imgsz]:
        raise RuntimeError("模型输入形状不是 [1,3,%d,%d]：%s" % (imgsz, imgsz, graph.input_shape))
    if "output0" not in graph.output_names:
        raise RuntimeError("模型输出不含 output0：%s" % graph.output_names)

    x = pn.load_image_input(image, imgsz)
    ranges = pn.ranges_from_ort(model, graph, x)
    plans = pn.build_plan(graph, ranges, x)
    if not plans:
        raise RuntimeError("build_plan 没有产生任何 Conv 层")

    # 只排除 Conv 的权重/偏置 initializer；其余小常量全部保留（板端 22 个，约 42 KB）。
    conv_weight_names = set()
    for node in graph.nodes:
        if node.op_type != "Conv":
            continue
        if len(node.inputs) > 1 and node.inputs[1]:
            conv_weight_names.add(node.inputs[1])
        if len(node.inputs) > 2 and node.inputs[2]:
            conv_weight_names.add(node.inputs[2])
    other_refs = set()
    for node in graph.nodes:
        if node.op_type == "Conv":
            continue
        for nm in node.inputs:
            if nm in conv_weight_names:
                other_refs.add(nm)
    if other_refs:
        raise RuntimeError("非 Conv 节点引用了被排除的权重：%s" % sorted(other_refs))

    # ---- 逐层写包 ----
    layer_records: Dict[str, Dict[str, Any]] = {}
    bytes_written = {"w": 0, "params": 0, "lut": 0}
    for p in plans:
        sizes = _write_layer_bins(plan_dir, p)
        for k in bytes_written:
            bytes_written[k] += sizes[k]
        layer_records[p.conv_name] = _layer_record(p, plan_dir.name)

    # ---- plan JSON ----
    initializers = {}
    for name, arr in graph.initializers.items():
        if name in conv_weight_names:
            continue
        initializers[str(name)] = {
            "dtype": str(np.asarray(arr).dtype),
            "shape": [int(v) for v in np.asarray(arr).shape],
            "data_b64": _b64(_array_le_bytes(arr)),
        }

    plan_obj = {
        "schema": SCHEMA,
        "input_name": graph.input_name,
        "input_shape": [int(v) for v in graph.input_shape],
        "output_name": "output0",
        "input_quant": {"sx": float(plans[0].sx), "zx": int(plans[0].zx)},
        "nodes": [
            {
                "op_type": node.op_type,
                "name": node.name,
                "inputs": list(node.inputs),
                "outputs": list(node.outputs),
                "attrs": {str(k): _ser(v) for k, v in node.attrs.items()},
            }
            for node in graph.nodes
        ],
        "initializers": initializers,
        "layers": layer_records,
    }
    _write_json(plan_path, plan_obj)
    plan_bytes = plan_path.stat().st_size

    # ---- 参考前向 + 产物 ----
    tensors, stats = pn.run_network(graph, x, pn.NumpyBackend(), plans)
    out = tensors["output0"]
    raw, ref_meta = _reference(out, image, float(stats["wall_time_s"]), imgsz)
    ref_dir.mkdir(parents=True, exist_ok=True)
    (ref_dir / "reference_output.bin").write_bytes(raw)
    _write_json(ref_dir / "reference.json", ref_meta)

    # ---- 自校验：只用写出的 JSON + 二进制重建 ----
    cosine = _reload_and_check(plan_path, plan_dir, x, out)

    n_unsup = sum(1 for rec in layer_records.values() if rec["unsupported"])
    det = ref_meta["detections"]
    print("model=%s image=%s" % (model.name, image.name))
    print("nodes=%d initializers=%d layers=%d unsupported=%d"
          % (len(graph.nodes), len(initializers), len(plans), n_unsup))
    print("bytes: w=%d params=%d lut=%d plan=%d"
          % (bytes_written["w"], bytes_written["params"],
             bytes_written["lut"], plan_bytes))
    print("output_shape=%s detections=%d candidates=%d wall=%.3fs total=%.3fs"
          % (ref_meta["output_shape"], det["count"], det["n_candidates"],
             ref_meta["wall_time_s"], time.perf_counter() - t0))
    print("BOARD_PACK_OK %d %d %.6f" % (len(plans), plan_bytes, cosine))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="生成板端 YOLOv8n@320 流式测试包（JSON + 裸二进制）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--image", default="data/samples/zidane.jpg",
                    help="参考图片（相对仓库根）")
    ap.add_argument("--imgsz", type=int, default=320,
                    help="模型输入分辨率（正方形，32 的倍数）")
    ap.add_argument("--model", default="data/models/yolov8n_320.onnx",
                    help="ONNX 模型（相对仓库根）")
    ap.add_argument("--out-dir", default="data/layers/stream_pack",
                    help="逐层二进制输出目录")
    ap.add_argument("--plan-out", default="data/layers/board_net_plan.json",
                    help="板端 plan JSON 输出路径")
    ap.add_argument("--ref-dir", default="build/stream",
                    help="参考输出与 reference.json 输出目录")
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="ascii", errors="replace")
    except Exception:
        pass
    args = build_parser().parse_args(argv)
    try:
        return build(args)
    except Exception as exc:  # noqa: BLE001 - 失败也必须给出唯一最终令牌
        print("BOARD_PACK_FAIL %s" % _one_line(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
