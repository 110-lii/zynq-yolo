"""导出 ONNX 模型的**逐层中间张量**作为金标准（golden）。

为什么需要这个（而不是只存最终输出）：
    PL 上的卷积引擎是一层一层做的。若只有最终输出的 golden，那么**任何一层出错都只能
    在最后表现为"框不对"**，无法定位是哪一层错的 —— 这正是旧项目 D:\\FPGA\\oi 踩过的坑
    （球检测 0 命中，根因最后定位到某个中间级的择优选错，花了很久）。
    ⇒ 每层的输入/输出都要有 golden，才能做「逐层比对」，把错误钉死在具体层。

怎么拿中间张量：
    ONNX Runtime 支持注册「中间输出」——但**不能**直接把内部张量名塞进 `run(output_names)`，
    那样会报 `Invalid output name`（`value_info` 只是形状注释，不是 graph.output）。
    正确做法是**改写图**：为每个想看的张量加一个 `Identity` 节点，把其输出登记进 `graph.output`，
    再让 ORT 去算。本工具就是这么做的，且用的是内存中的模型副本，**不修改磁盘上的 .onnx**。

尺寸与存储：
    YOLOv8n @320 的全部中间张量 FP32 约几十 MB。**大文件不入 git**（data/README.md 规则），
    所以默认只存：
      · `*_golden.npz` —— 全部中间张量的**压缩**版（float16 存储，体积减半，比对时按 float16 比）
      · `*_golden.json` —— 每个张量的 shape / dtype / 统计量（**小而可入库**，是"金标准确实存在"的凭据）
    需要全精度时用 `--dtype float32`。

判据：
    末行 `GOLDEN_OK`；写出的 npz 能被 `--self-check` 重新读回并与 json 里的 sha256 对上。

用法：
    python tools/make_golden.py --model data/models/yolov8n_320.onnx --out data/golden
    python tools/make_golden.py --model ... --self-check     # 只校验已有 golden
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from pathlib import Path
from typing import Dict, List

import numpy as np

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def make(model_path: Path, out_dir: Path, dtype: str, max_tensors: int) -> int:
    try:
        import onnx
        import onnxruntime as ort
    except ImportError:
        print("需要 onnx + onnxruntime", file=sys.stderr)
        return 2

    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- 1. 读模型的输入规格（不猜） ----
    m = onnx.load(str(model_path))
    m = onnx.shape_inference.infer_shapes(m)
    inp = m.graph.input[0]
    dims = [d.dim_value for d in inp.type.tensor_type.shape.dim]
    if any(d <= 0 for d in dims):
        print("模型是动态形状（%s），本工具只支持静态形状" % dims, file=sys.stderr)
        return 2
    n, c, h, w = dims
    print("  输入：%s %s" % (inp.name, dims))

    # ---- 2. 收集要看的中间张量名 ----
    #     跳过 Constant 的输出（那是常量，不是计算）；其余 value_info 全拿
    names: List[str] = []
    for vi in m.graph.value_info:
        t = vi.type.tensor_type
        if t.elem_type == 0:
            continue
        names.append(vi.name)
    names = names[:max_tensors]
    print("  中间张量 %d 个（上限 %d）" % (len(names), max_tensors))

    # ---- 2b. ★ 关键：ORT **不能**直接把内部张量当输出请求 ----
    #     首版直接把这些名字塞进 `sess.run(output_names)`，报
    #         InvalidArgument : Invalid output name:/model.0/conv/Conv_output_0
    #     原因：`value_info` 只是**形状注释**，图里并没有把它们声明成 graph.output。
    #     ⇒ 必须**改写图**：为每个要看的张量加一个 Identity 节点，其输出登记到 graph.output。
    #       这样 ORT 才会真的把这些中间结果算出来交给我们。
    #     ⚠️ 用一个全新模型副本，别污染磁盘上的模型文件。
    import onnx as _onnx

    existing_out = {o.name for o in m.graph.output}
    added = []
    for idx, nm in enumerate(names):
        if nm in existing_out:
            continue
        # 加 Identity，输出名加前缀避免与原图重名
        # ⚠️ 每个节点的 name 必须唯一：首版全用同一个 "golden_pick"，
        #    ONNX 直接报 `two nodes with same node name`（模型加载失败，不是运行期错误）。
        new_name = "golden__" + nm
        node = _onnx.helper.make_node(
            "Identity", [nm], [new_name], name="golden_pick_%04d" % idx)
        m.graph.node.append(node)
        info = _onnx.helper.ValueInfoProto()
        info.name = new_name
        m.graph.output.append(info)
        added.append((nm, new_name))
    print("  改写图：新增 %d 个 Identity 输出" % len(added))
    m = _onnx.shape_inference.infer_shapes(m, strict_mode=False)

    # ---- 3. 造输入：固定种子，保证可复现 ----
    #     ⚠️ 用确定性伪随机而不是零输入：全零输入会让 ReLU/BN 的输出退化，
    #        金标准就失去了区分能力（一个"永远输出 0"的实现也能通过）。
    rng = np.random.default_rng(20261019)
    x = rng.standard_normal((n, c, h, w), dtype=np.float32)

    so = ort.SessionOptions()
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL  # 别让优化改写图
    buf_model = io.BytesIO()
    buf_model.write(m.SerializeToString())
    buf_model.seek(0)
    sess = ort.InferenceSession(buf_model.getvalue(), so, providers=["CPUExecutionProvider"])

    req = [o.name for o in sess.get_outputs()]
    outs = sess.run(req, {inp.name: x})

    # 原图输出名 → 保持稳定 key
    orig_out = {o.name for o in onnx.load(str(model_path)).graph.output}

    want = np.float16 if dtype == "float16" else np.float32
    arrays: Dict[str, np.ndarray] = {}
    meta: Dict[str, Dict] = {}
    for name, arr in zip(req, outs):
        a = np.asarray(arr)
        if a.dtype == object:
            continue
        # 还原成「原张量名」作为 key：去掉我们自己加的 golden__ 前缀，
        # 这样下游按 ONNX 里的节点名就能对上，不受本工具的改写细节影响。
        key_src = name[len("golden__"):] if name.startswith("golden__") else name
        key = key_src.replace("/", "_").replace(":", "_") or "tensor"
        arrays[key] = a.astype(want)
        # ⚠️ 有些中间张量是空的（size==0，例如某些 Reshape/Split 的占位分支）。
        #    np.min/max 对空数组会抛 `zero-size array to reduction operation`，
        #    首版就在这里崩了 ⇒ 空张量单独走一条分支，统计值写 None（**不写 0**，
        #    因为 0 是一个合法数值，会把"空"伪装成"全零"，下游无法区分）。
        if a.size == 0:
            meta[key] = {
                "onnx_name": key_src,
                "is_graph_output": key_src in orig_out,
                "shape": list(a.shape),
                "dtype_src": str(a.dtype),
                "dtype_stored": str(want.__name__),
                "empty": True,
                "min": None, "max": None, "mean": None, "std": None,
                "nonzero_frac": 0.0,
            }
            continue
        af = a.astype(np.float64)
        meta[key] = {
            "onnx_name": key_src,
            "is_graph_output": key_src in orig_out,
            "shape": list(a.shape),
            "dtype_src": str(a.dtype),
            "dtype_stored": str(want.__name__),
            "empty": False,
            "min": float(np.min(af)),
            "max": float(np.max(af)),
            "mean": float(np.mean(af)),
            "std": float(np.std(af)),
            "nonzero_frac": float(np.count_nonzero(a) / a.size),
        }

    # ---- 4. 写 npz（压缩）+ json（小、可入库） ----
    stem = model_path.stem
    npz = out_dir / ("%s_golden.npz" % stem)
    js = out_dir / ("%s_golden.json" % stem)

    buf = io.BytesIO()
    np.savez_compressed(buf, **arrays)
    raw = buf.getvalue()
    # ★ 显式二进制写 + 固定时间戳无关（npz 里 zip 头带时间，故哈希只作"内容指纹"用途）
    with open(npz, "wb") as f:
        f.write(raw)

    info = {
        "model": str(model_path.name),
        "model_sha256": _sha256_file(model_path),
        "input_name": inp.name,
        "input_shape": dims,
        "input_dtype": str(x.dtype),
        "input_seed": 20261019,
        "input_note": "确定性伪随机（default_rng(20261019)），非零输入 —— 全零输入会让 ReLU/BN 退化、金标准失去区分力",
        "stored_dtype": str(want.__name__),
        "ort_version": ort.__version__,
        "tensor_count": len(arrays),
        "npz_sha256": hashlib.sha256(raw).hexdigest(),
        "tensors": meta,
    }
    with open(js, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(info, indent=2, ensure_ascii=False))

    print("  写出 %s (%.2f MB)" % (npz.name, npz.stat().st_size / 1e6))
    print("  写出 %s (%.1f KB, 入库)" % (js.name, js.stat().st_size / 1024))
    print("")
    print("  —— 抽查 3 个张量 ——")
    for k in list(meta)[:3]:
        d = meta[k]
        print("    %-46s shape=%-18s mean=%+.4f nonzero=%.1f%%"
              % (k[:46], d["shape"], d["mean"], d["nonzero_frac"] * 100))
    print("")
    print("GOLDEN_OK")
    return 0


def self_check(out_dir: Path) -> int:
    js_files = sorted(out_dir.glob("*_golden.json"))
    if not js_files:
        print("找不到任何 *_golden.json", file=sys.stderr)
        return 2
    bad = 0
    for js in js_files:
        info = json.loads(js.read_text(encoding="utf-8"))
        npz = out_dir / (js.name.replace("_golden.json", "_golden.npz"))
        if not npz.is_file():
            print("  FAIL %-34s 缺 npz" % js.name)
            bad += 1
            continue
        act = _sha256_file(npz)
        exp = info.get("npz_sha256")
        ok_hash = (act == exp)
        with np.load(npz) as z:
            n = len(z.files)
            ok_n = (n == info["tensor_count"])
            # 抽查：json 里记的 shape 必须与 npz 里的一致
            ok_shape = all(
                list(z[k].shape) == v["shape"]
                for k, v in list(info["tensors"].items())[:20] if k in z.files
            )
        tag = "OK " if (ok_hash and ok_n and ok_shape) else "FAIL"
        print("  %s %-34s tensors=%d hash=%s shape=%s"
              % (tag, js.name, n, "match" if ok_hash else "MISMATCH",
                 "match" if ok_shape else "MISMATCH"))
        if not (ok_hash and ok_n and ok_shape):
            bad += 1
    print("")
    if bad:
        print("GOLDEN_FAIL —— %d 个不一致" % bad)
        return 1
    print("GOLDEN_OK")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="导出 ONNX 逐层中间张量作为金标准")
    ap.add_argument("--model", default=None, help="onnx 文件（--self-check 时可不给）")
    ap.add_argument("--out", default=str(ROOT / "data" / "golden"), help="输出目录")
    ap.add_argument("--dtype", choices=("float16", "float32"), default="float16",
                    help="存储精度（默认 float16，体积减半；比对时同样按该精度）")
    ap.add_argument("--max-tensors", type=int, default=400, help="最多导出多少个中间张量")
    ap.add_argument("--self-check", action="store_true", help="只校验已有 golden")
    args = ap.parse_args(argv)

    out_dir = Path(args.out)
    if args.self_check:
        return self_check(out_dir)
    if not args.model:
        ap.error("需要 --model（或用 --self-check）")
    p = Path(args.model)
    if not p.is_file():
        print("找不到模型：%s" % p, file=sys.stderr)
        return 2
    return make(p, out_dir, args.dtype, args.max_tensors)


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    sys.exit(main())
