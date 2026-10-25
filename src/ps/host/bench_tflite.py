"""板上 TFLite INT8 推理基准（只测速度，不测精度）。

用法：
    python3 src/ps/host/bench_tflite.py --model /home/xilinx/zynq-yolo/build/models/person_v8n25_320_int8.tflite
"""
import argparse
import time

import numpy as np

try:
    from tflite_runtime.interpreter import Interpreter
except ImportError:
    from tensorflow.lite.python.interpreter import Interpreter


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--warmup", type=int, default=5)
    ap.add_argument("--runs", type=int, default=20)
    args = ap.parse_args()

    it = Interpreter(model_path=args.model, num_threads=2)
    it.allocate_tensors()
    inp = it.get_input_details()[0]
    out = it.get_output_details()[0]
    print("INPUT", inp["shape"].tolist(), inp["dtype"].__name__)
    print("OUTPUT", out["shape"].tolist(), out["dtype"].__name__)

    x = np.zeros(inp["shape"], dtype=inp["dtype"])
    it.set_tensor(inp["index"], x)
    for _ in range(args.warmup):
        it.invoke()

    ts = []
    for _ in range(args.runs):
        t0 = time.perf_counter()
        it.invoke()
        ts.append(time.perf_counter() - t0)
    ts.sort()
    best = ts[0]
    med = ts[len(ts) // 2]
    print("TFLITE_BENCH runs=%d best_ms=%.2f med_ms=%.2f best_fps=%.2f med_fps=%.2f"
          % (args.runs, best * 1e3, med * 1e3, 1 / best, 1 / med))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())