"""capture_golden.py -- 采集链（M1）上板测试的黄金模型：生成期望 DDR 镜像 / 比对实测 dump。

背景
----
`src/rtl/video/vid_test_pattern.v` 在 PL 里生成一个**公式已知**的 DVP 图样，
经 `vid_dvp_capture` → 跨时钟 FIFO → AXI DMA 落进 DDR。上板判据是「DDR 里的
字节和这里算出来的**逐字节相等**」，所以本脚本同时是：

* 黄金模型（`--out`）：把期望的 32 位像素字写成二进制，供 `dow -data` 送进 DDR；
* 比对器（`--compare`）：拿 JTAG dump 回来的二进制，逐字比对并给出令牌。

图样公式（**必须与 RTL 逐字一致**，见 `vid_test_pattern.v` 文件头）
--------------------------------------------------------------
对像素 `(x, y)`（x 行内序号、y 行号，均 0 起）：

    R5 = x & 0x1F ; G6 = y & 0x3F ; B5 = (x + y) & 0x1F
    高字节 = {R5, G6[5:3]}        低字节 = {G6[2:0], B5}     （RGB565，高字节先到）

采集模块把 RGB565 用**位复制**升到 RGB888（`vid_dvp_capture.v`）：

    R8 = {R5, R5[4:2]} ; G8 = {G6, G6[5:4]} ; B8 = {B5, B5[4:2]}

DDR 里每个像素一个 32 位字（小端）：`[7:0]=B8, [15:8]=G8, [23:16]=R8, [31:24]=0`
（高字节补零的原因见 `vid_dvp_capture.v` 文件头：AXI DMA 只支持 8/16/32/64 的流位宽）

用法
----
    # 1) 生成黄金镜像（默认 build/board/capture_golden.bin）
    & D:\\python\\python.exe tools\\capture_golden.py --out

    # 2) 上板跑完后比对 JTAG dump
    & D:\\python\\python.exe tools\\capture_golden.py --compare build\\board\\capture_dump.bin

判据令牌
--------
    CAPTURE_GOLDEN_OK path=<f> frame_bytes=<n> words=<n> crc32=<h>    生成成功
    CAPTURE_GOLDEN_FAIL <reason>                                      任何失败
    CAPTURE_MATCH_OK words=<n> crc32=<h>                              逐字相等
    CAPTURE_MATCH_FAIL <reason>                                       有不符（会打印前 5 个）

★ 两个令牌都打印到 stdout 的独立行，**不看退出码**（`docs/repo_spec.md` §5）。
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import binascii
import struct
from pathlib import Path

# 控制台默认是 GBK，而本脚本的中文输出是 UTF-8（与 tools/ball_cv.py 同口径）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "build" / "board" / "capture_golden.bin"

# 与 RTL 参数一致（改 BD 的 H_ACTIVE/V_ACTIVE 时这里必须同步改，脚本会交叉核对）
H_ACTIVE = 640
V_ACTIVE = 480
PIXEL_BYTES = 4


def r5_to_r8(r5: int) -> int:
    """5 位红 → 8 位，位复制（R5[4:2] 复制到低 3 位）。"""
    return ((r5 & 0x1F) << 3) | ((r5 & 0x1F) >> 2)


def g6_to_g8(g6: int) -> int:
    """6 位绿 → 8 位，位复制（G6[5:4] 复制到低 2 位）。"""
    return ((g6 & 0x3F) << 2) | ((g6 & 0x3F) >> 4)


def b5_to_b8(b5: int) -> int:
    """5 位蓝 → 8 位，位复制。"""
    return ((b5 & 0x1F) << 3) | ((b5 & 0x1F) >> 2)


def build_frame(h: int, v: int) -> bytes:
    """生成一帧的 32 位像素字序列（小端字节序）。"""
    out = bytearray(h * v * PIXEL_BYTES)
    off = 0
    for y in range(v):
        g6 = y & 0x3F
        g8 = g6_to_g8(g6)
        for x in range(h):
            r5 = x & 0x1F
            b5 = (x + y) & 0x1F
            r8 = r5_to_r8(r5)
            b8 = b5_to_b8(b5)
            # 小端：低字节先 → B8, G8, R8, 0x00
            out[off] = b8
            out[off + 1] = g8
            out[off + 2] = r8
            out[off + 3] = 0
            off += PIXEL_BYTES
    return bytes(out)


def crc32_of(data: bytes) -> str:
    return "%08x" % (binascii.crc32(data) & 0xFFFFFFFF)


def cmd_out(args: argparse.Namespace) -> int:
    h, v = args.h, args.v
    if (h, v) != (H_ACTIVE, V_ACTIVE):
        print("CAPTURE_GOLDEN_FAIL 参数与 RTL 不一致：脚本内 H_ACTIVE/V_ACTIVE = %d/%d，"
              "而命令行给了 %d/%d —— 改 BD 时请同步改本文件顶部常量"
              % (H_ACTIVE, V_ACTIVE, h, v))
        return 1
    data = build_frame(h, v)
    path = Path(args.out) if args.out else DEFAULT_OUT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    words = len(data) // PIXEL_BYTES
    print("帧参数：%dx%d 像素，每像素 %d 字节" % (h, v, PIXEL_BYTES))
    print("期望前 4 个 32 位字（小端字节序，hex 显示的是**字节串**）：")
    for i in range(4):
        chunk = data[i * 4:(i + 1) * 4]
        word = struct.unpack("<I", chunk)[0]
        print("  [%d] bytes=%s  word=0x%08X  (R=%02X G=%02X B=%02X)"
              % (i, chunk.hex(), word, chunk[2], chunk[1], chunk[0]))
    print("CAPTURE_GOLDEN_OK path=%s frame_bytes=%d words=%d crc32=%s"
          % (path, len(data), words, crc32_of(data)))
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    h, v = args.h, args.v
    exp = build_frame(h, v)
    dump_path = Path(args.compare)
    if not dump_path.is_file():
        print("CAPTURE_MATCH_FAIL dump_missing path=%s" % dump_path)
        return 1
    got = dump_path.read_bytes()
    # 允许 dump 比期望长（DMA 多写的尾巴不算错，但要报出来）
    if len(got) < len(exp):
        print("CAPTURE_MATCH_FAIL dump_short got=%d expect=%d bytes" % (len(got), len(exp)))
        return 1

    n_words = len(exp) // 4
    bad = 0
    first_bad: list[tuple[int, int, int, int, int, int]] = []
    for i in range(n_words):
        a = exp[i * 4:(i + 1) * 4]
        b = got[i * 4:(i + 1) * 4]
        if a != b:
            bad += 1
            if len(first_bad) < 5:
                # 像素坐标：x = i % h, y = i // h
                first_bad.append((i, i % h, i // h,
                                  struct.unpack("<I", b)[0], struct.unpack("<I", a)[0],
                                  got[i * 4 + 3]))
    extra = len(got) - len(exp)
    print("dump=%s  实测字节=%d  期望字节=%d  多出=%d"
          % (dump_path, len(got), len(exp), extra))
    if bad == 0 and extra == 0:
        print("CAPTURE_MATCH_OK words=%d crc32=%s" % (n_words, crc32_of(exp)))
        return 0
    if bad == 0 and extra != 0:
        # 多出尾巴：不判错，但必须让人看见（说明 DMA 长度或图样发生器行为与预期不符）
        print("CAPTURE_MATCH_OK words=%d crc32=%s （⚠ 尾巴多出 %d 字节，DMA 长度口径需复核）"
              % (n_words, crc32_of(exp), extra))
        return 0
    print("不符字数=%d / %d（%.4f%%）" % (bad, n_words, 100.0 * bad / n_words))
    for (i, x, y, gotw, expw, hi) in first_bad:
        print("  首个不符 [%d] (x=%d,y=%d) 实测=0x%08X 期望=0x%08X 高字节=%02X"
              % (i, x, y, gotw, expw, hi))

    # ---- 错位诊断（不是为了让判据变宽，而是为了把「整周期错位」这个盲区说清楚）----
    # 图样 R5=x&0x1F、G6=y&0x3F ⇒ 水平周期 32 px、垂直周期 64 行（`vid_test_pattern.v`）。
    # 所以「整 32 px / 整 64 行」的错位在这张图样下**与正确帧不可区分**。
    # 做法：枚举这些不可区分的候选平移量（含少量组合），用抽样比对给出「整帧平移 d 像素后
    # 是否处处相符」的结论——命中就明确说「字节对、但整体平移了 d」，而不是只报一个红叉。
    hit = _diagnose_shift(got, exp, h, v, n_words)
    if hit is not None:
        d, frac = hit
        print("CAPTURE_SHIFT_DETECTED offset_px=%d matched=%.4f" % (d, frac))
        print("  ⇒ 字节本身是对的，但整帧平移了 %d 像素；本图样对「整 32 px / 整 64 行」的错位"
              "本来就看不出（见 docs/integration_rtl_plan.md §9-11）" % d)
    else:
        print("错位诊断：不是整 32 px / 整 64 行的平移（若确为对齐问题，应落在其它字节上）")
    print("CAPTURE_MATCH_FAIL mismatched=%d/%d" % (bad, n_words))
    return 1


def _shift_score(got: bytes, exp: bytes, d_words: int, samples: int = 4096) -> float:
    """快速打分：把实测帧整体前移 d 个像素（循环含义），抽样比对字节相符比例。"""
    n = len(exp) // 4
    if d_words == 0 or n == 0:
        return 0.0
    stride = max(1, n // samples)
    same = 0
    tested = 0
    for i in range(0, n, stride):
        j = (i + d_words) % n
        if got[i * 4:(i + 1) * 4] == exp[j * 4:(j + 1) * 4]:
            same += 1
        tested += 1
    return same / float(tested) if tested else 0.0


def _diagnose_shift(got: bytes, exp: bytes, h: int, v: int, n_words: int):
    """枚举「本图样下不可判」的平移候选，返回 (d_px, 相符率) 或 None。"""
    cands = [32 * k for k in range(1, 20)]                 # 整 32 px 的水平错位
    cands += [h * 64 * m for m in range(1, max(1, v // 64) + 1)]   # 整 64 行的垂直错位
    cands += [32 * k + h * 64 for k in (1, 2, 3, 5, 7)]     # 少量组合
    best = (0, 0.0)
    for d in cands:
        if d >= n_words:
            continue
        s = _shift_score(got, exp, d)
        if s > best[1]:
            best = (d, s)
        if s >= 0.999:
            return d, s
    if best[1] >= 0.98:
        return best
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="采集链上板测试的黄金模型与比对器（令牌判定）")
    ap.add_argument("--out", nargs="?", const=str(DEFAULT_OUT), default=None,
                    help="生成黄金镜像（可省略路径，用默认 build/board/capture_golden.bin）")
    ap.add_argument("--compare", metavar="DUMP", help="比对 JTAG dump 回来的二进制")
    ap.add_argument("--h", type=int, default=H_ACTIVE, help="行内像素数（默认 %d）" % H_ACTIVE)
    ap.add_argument("--v", type=int, default=V_ACTIVE, help="行数（默认 %d）" % V_ACTIVE)
    args = ap.parse_args(argv)

    if args.out and args.compare:
        print("CAPTURE_GOLDEN_FAIL --out 与 --compare 只能二选一")
        return 2
    if args.out:
        return cmd_out(args)
    if args.compare:
        return cmd_compare(args)
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())