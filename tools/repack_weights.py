"""把已生成的 stream_pack 权重按 (co,ci) 记录补齐到 4/12 字节。

背景：conv_engine v2 的权重装载要求每条 (co, ci) 记录按 4 字节（K*K<=4）或
12 字节（K=3）对齐，避免逐字节事务和动态移位器。plan 只记录文件路径，不记录
偏移，所以就地重写 w.bin 即可，不需要改 plan。

用法（板端或 PC，pack 根目录 = plan 所在目录）：
    python tools/repack_weights.py --plan data/layers/board_net_plan.json
"""
import argparse
import json
import os

import numpy as np


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default="data/layers/board_net_plan.json")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    root = os.path.dirname(os.path.abspath(args.plan))
    plan = json.load(open(args.plan, encoding="utf-8"))
    layers = plan["layers"]
    n_pad = n_skip = 0
    for name, rec in layers.items():
        g = rec["geom"]
        kk = int(g["K"]) ** 2
        rb = 4 if kk <= 4 else 12
        n = int(g["C_OUT"]) * int(g["C_IN"])
        path = os.path.join(root, rec["files"]["w"])
        raw = np.fromfile(path, dtype=np.int8)
        if raw.size == n * rb:
            n_skip += 1
            continue
        if raw.size != n * kk:
            raise SystemExit("SHAPE_FAIL %s size=%d expect %d or %d" % (name, raw.size, n * kk, n * rb))
        if args.dry_run:
            print("WOULD_PAD", name, n * kk, "->", n * rb)
            n_pad += 1
            continue
        out = np.zeros((n, rb), dtype=np.int8)
        out[:, :kk] = raw.reshape(n, kk)
        out.tofile(path)
        n_pad += 1
    print("REPACK_OK padded=%d already=%d" % (n_pad, n_skip))


if __name__ == "__main__":
    main()
