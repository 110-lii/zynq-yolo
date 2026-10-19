"""生成 conv_engine 的 C 仿真测试用例（含逐位参考结果）。

为什么要这个脚本：HLS 的 C 仿真不能自己算期望值 —— 如果 TB 和 RTL 用同一份代码算，
两边一起错也全绿，比对就没有判别力。这里**按契约 docs/accel_interface.md 独立重写一遍**
定点语义（numpy，逐位模拟 int32 回绕、64 位乘积、算术右移、LUT），作为期望值。

与 tools/ref_int8_conv.py 的分工：
    本脚本   —— 造**随机**数据，只为验证"硬件算得和契约一致"，不碰真实模型权重
    ref_int8_conv.py —— 用**真实**量化参数跑真实层，验证"量化导出链没错"

用例目录格式（tb_conv_engine.cpp 读这个格式）：
    <case>/meta.txt  <case>/in.bin  <case>/w.bin
    <case>/params.bin  <case>/lut.bin  <case>/out_ref.bin

用法：
    python sim/scripts/make_hls_case.py --all
    python sim/scripts/make_hls_case.py --case k3s1_c3_40 --cin 3 --cout 16 --h 40 --w 40 --k 3 --stride 1 --pad 1

判据：末行 `CASES_OK <n>` 或 `CASES_FAIL ...`
"""
from __future__ import annotations

import argparse
import sys
import zlib
from pathlib import Path

sys.dont_write_bytecode = True

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
OUTD = ROOT / "data" / "layers" / "cases"

# 与内核默认值保持一致的实现参数（列分块宽度），用于检查"W_OUT 不是 TILE_W 整数倍"的边界
TILE_W = 40

# 覆盖这些维度组合（每个都对应一类真实层形状或一类边界）：
#   name                       C_IN C_OUT H_IN W_IN  K stride pad   说明
DEFAULT_CASES = [
    # 最常见的 3x3 / stride1 / pad1（YOLOv8n 里 39 层是这个形状）
    ("k3s1_c3_40",               3,  16,  40,  40, 3, 1, 1),
    # 1x1（25 层），且 C_OUT 不是 PAR_OUT 的整数倍 ⇒ 走尾块补齐分支
    ("k1s1_tailcout",           64,  20,  20,  20, 1, 1, 0),
    # 3x3 / stride2（model.1/3/5/7 的降采样），验证 stride 对行/列索引的作用
    ("k3s2_c16_40",             16,  32,  40,  40, 3, 2, 1),
    # W_OUT=10 远小于 TILE_W ⇒ 列分块只剩一个不满的 tile，验证 wo_abs<W_OUT 守卫
    ("k1s1_smallw",             32,  16,  10,  10, 1, 1, 0),
    # C_IN 最大档（真实网络 model.9 是 512 入），验证 ci 长循环不溢出
    ("k3s1_cin512",            512,  16,  10,  10, 3, 1, 1),
    # pad=0 且 K=3（输出比输入小 2），验证无 padding 的边界
    ("k3s1_pad0",               16,  16,  20,  20, 3, 1, 0),
]


def build_case(name, cin, cout, hin, win, k, stride, pad) -> bool:
    # ★ 种子必须由名字**稳定**推导。绝不能用内置 hash(name)——CPython 的字符串哈希
    #   每个进程不同（PYTHONHASHSEED 随机化），会让"同一个用例"每次生成不同数据，
    #   重跑时的比对就失去意义。
    seed = zlib.crc32(name.encode("ascii"))
    rng = np.random.default_rng(seed)

    hout = (hin + 2 * pad - k) // stride + 1
    wout = (win + 2 * pad - k) // stride + 1

    # ---- 输入 / 权重：覆盖各自完整取值域 ----
    in_q = rng.integers(0, 256, size=(cin, hin, win), dtype=np.uint8)
    w_q = rng.integers(-128, 128, size=(cout, cin, k, k), dtype=np.int8)

    # ---- 零点 ----
    zp_in = int(rng.integers(0, 256))
    zp_pre = int(rng.integers(64, 192))

    # LUT 用**随机表**而不是恒等表：恒等表会把"索引错了"伪装成"数值差一点"，
    # 随机表能让任何 LUT 下标错误立刻变成大片不等。
    lut = rng.integers(0, 256, size=256, dtype=np.uint8)

    # ---- 独立实现契约 §1/§3（逐位模拟硬件语义）----
    # padding 在 uint8 域填 zp_in，不是 0
    padded = np.full((cin, hin + 2 * pad, win + 2 * pad), zp_in, dtype=np.uint8)
    padded[:, pad:pad + hin, pad:pad + win] = in_q

    # 取整窗口：[cin][k][k][hout][wout]
    windows = np.empty((cin, k, k, hout, wout), dtype=np.int64)
    for kh in range(k):
        for kw in range(k):
            # 输出 (ho,wo) 的第 (kh,kw) 个抽头 = padded[:, ho*stride+kh, wo*stride+kw]
            rows = np.arange(hout) * stride + kh
            cols = np.arange(wout) * stride + kw
            windows[:, kh, kw, :, :] = padded[:, rows][:, :, cols]

    # int32 累加（硬件是 ap_int<32>，会回绕；这里在 int64 里算完再截断，
    # 因为加法取模 2^32 满足结合律，结果与逐次回绕等价）
    wq64 = w_q.astype(np.int64)
    acc = np.tensordot(wq64, windows, axes=([1, 2, 3], [0, 1, 2]))  # [cout][hout][wout]
    acc = acc.astype(np.int64)
    # 截断到 int32（模拟回绕）
    acc32 = ((acc + 2 ** 31) % 2 ** 32 - 2 ** 31).astype(np.int32)

    # ---- 由 acc 的实际量级反推 M/S，让输出真的铺满 0..255 ----
    # ★ 为什么不能像第一版那样随便取 M∈[2^30,2^31)、S∈[25,40]：
    #   acc 的量级约 ±2e6，若 M/2^S ≈ 2^-5，v 会全部撞到 0/255 的钳位，
    #   于是"算错了"和"算对了"输出一模一样 —— 用例失去判别力（第一版就是这样，
    #   失配率只有 40%，而真正的原因是 bias 漏加，却差点被误判成"钳位正常"）。
    amax = np.abs(acc32.astype(np.int64)).max(axis=(1, 2)).astype(np.float64) + 1.0
    # 目标：acc*scale 落在 ±100 以内 ⇒ 加上 zp_pre(64..191) 后基本在 0..255，
    # 只留少量钳位用来验证 clamp 分支
    target_scale = 100.0 / amax
    e = np.floor(np.log2(target_scale)).astype(np.int64)
    mult = np.round(target_scale / np.exp2(e) * 2 ** 30).astype(np.int64)
    mult = np.clip(mult, 2 ** 30, 2 ** 31 - 1)
    shift = (30 - e).astype(np.int64)
    shift = np.clip(shift, 1, 62)          # 契约 §3.3：S 必须落在 [1,62]
    bias = (rng.standard_normal(cout) * (amax / 8.0)).astype(np.int64)

    # 契约 §3.2：acc[c] = Σ x_q*w_q + bias_i32[c] —— bias 是累加器**初值**的一部分
    acc_full = acc + bias[:, None, None]
    acc32 = ((acc_full + 2 ** 31) % 2 ** 32 - 2 ** 31).astype(np.int32)

    # requant：64 位乘积 + round-half-up + 算术右移
    prod = acc32.astype(np.int64) * mult[:, None, None]
    half = np.where(shift > 0, np.left_shift(np.int64(1), np.maximum(shift - 1, 0)), 0)
    shifted = (prod + half[:, None, None]) >> shift[:, None, None]      # numpy int64 >> 是算术右移
    v = shifted.astype(np.int32)                                        # 截断到 32 位
    v = (v + np.int32(zp_pre)).astype(np.int32)                         # int32 相加会回绕
    v = np.clip(v, 0, 255).astype(np.uint8)
    out_ref = lut[v]

    # ---- 落盘 ----
    d = OUTD / name
    d.mkdir(parents=True, exist_ok=True)
    in_q.tofile(d / "in.bin")
    w_q.tofile(d / "w.bin")
    params = np.empty((cout, 3), dtype=np.int32)
    params[:, 0] = bias.astype(np.int32)
    params[:, 1] = mult.astype(np.int32)
    params[:, 2] = shift.astype(np.int32)
    params.tofile(d / "params.bin")
    lut.tofile(d / "lut.bin")
    out_ref.tofile(d / "out_ref.bin")

    meta = (
        f"name={name}\n"
        f"C_IN={cin}\nC_OUT={cout}\nH_IN={hin}\nW_IN={win}\n"
        f"K={k}\nstride={stride}\npad={pad}\n"
        f"zp_in={zp_in}\nzp_pre={zp_pre}\nH_OUT={hout}\nW_OUT={wout}\n"
        f"# generated by sim/scripts/make_hls_case.py\n"
        f"# out_ref implements docs/accel_interface.md sec 1/3 independently (numpy)\n"
        f"# out_shape=({cout},{hout},{wout})  out_bytes={out_ref.size}\n"
    )
    # meta.txt 是**机器读**的文件（C TB 用 fopen + atol 解析），保持纯 ASCII
    (d / "meta.txt").write_text(meta, encoding="ascii", newline="\n")

    print(f"  {name:<16} C_IN={cin:<4} C_OUT={cout:<4} {hin}x{win} K{k} s{stride} p{pad}"
          f" -> {hout}x{wout}  out={out_ref.size}B  nonzero={int((out_ref != 0).sum())}")
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default=None)
    ap.add_argument("--cin", type=int, default=None)
    ap.add_argument("--cout", type=int, default=None)
    ap.add_argument("--h", type=int, default=None)
    ap.add_argument("--w", type=int, default=None)
    ap.add_argument("--k", type=int, default=None)
    ap.add_argument("--stride", type=int, default=None)
    ap.add_argument("--pad", type=int, default=None)
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args(argv)

    OUTD.mkdir(parents=True, exist_ok=True)
    print(f"输出目录：{OUTD}")

    if a.all or a.case is None:
        n = 0
        for (name, cin, cout, hin, win, k, stride, pad) in DEFAULT_CASES:
            print(f"[{n + 1}/{len(DEFAULT_CASES)}]")
            build_case(name, cin, cout, hin, win, k, stride, pad)
            n += 1
        print(f"CASES_OK {n}")
        return 0

    need = [a.cin, a.cout, a.h, a.w, a.k, a.stride, a.pad]
    if any(v is None for v in need):
        print("CASES_FAIL : --case 需要同时给 --cin --cout --h --w --k --stride --pad")
        return 1
    build_case(a.case, a.cin, a.cout, a.h, a.w, a.k, a.stride, a.pad)
    print("CASES_OK 1")
    return 0


if __name__ == "__main__":
    sys.exit(main())
