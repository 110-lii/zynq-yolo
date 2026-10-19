"""算清三件事：能否兼得（精度+帧率）、能否双路、硬盘到底够不够。

背景（全部本机实测，出处见 data/results/）：
  · csynth 实测 PAR_OUT=16：DSP 96/220、LUT 28492/53200、Slack +4.66ns @10ns
  · YOLOv8n @320 = 2.186 GOP/帧；@224 = 1.071；@192 = 0.787
  · 器件峰值：220 DSP × 2 × 频率

三个问题分开算，因为它们的瓶颈**不是同一个**：
  Q1 兼得      → 瓶颈是算力（GOPS）
  Q2 双路      → 瓶颈是 **DSP 数量**（同一份硬件要跑两遍）
  Q3 带宽      → 瓶颈是 AXI HP 端口吞吐
"""

from __future__ import annotations

import sys

# 不生成 __pycache__：本仓库的 tools/check_repo.py 检查 d) 是**扫盘**的，不看 .gitignore，
# 留下 .pyc 会直接判 FAIL。实测语义（勿想当然）：模块**自身体内**置位拦不住自己的 .pyc
# （CPython 先编译并写缓存，再执行模块体），只有在本模块 import 别的本地模块**之前**
# 置位才有效。所以这段必须放在所有 import 之前。
sys.dont_write_bytecode = True

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DSP_TOTAL, LUT_TOTAL, BRAM_TOTAL, FF_TOTAL = 220, 53200, 280, 106400
GOP_320 = 2.186

# csynth 实测（PAR_OUT -> DSP, LUT, BRAM18）
MEASURED = {2: (13, 9136, 16), 4: (25, 12098, 24), 8: (48, 17177, 40), 16: (96, 28492, 72)}

def gop(res: float) -> float:
    """YOLOv8n 在给定分辨率的 GOP/帧（面积线性缩放，近似）。"""
    return GOP_320 * (res / 320.0) ** 2


def main() -> int:
    L = []
    W = L.append

    W("=" * 90)
    W("Q1「兼得」：如何同时要精度和帧率")
    W("=" * 90)
    W("")
    W("  A) 单分辨率直接折中 —— 只有一个变量，不可能兼得")
    W("")
    W("     %-10s %9s %12s %12s %12s" % ("分辨率", "GOP/帧", "需25fps", "需30fps", "相对320精度"))
    for r in (160, 192, 224, 256, 320):
        # 目标检出尺寸 ∝ 分辨率，面积占比 ∝ r^2 ⇒ 用相对面积当"小目标可检性"的粗代理
        W("     %-10s %9.3f %10.1f G %10.1f G %10.1f%%"
          % ("%dx%d" % (r, r), gop(r), gop(r) * 25, gop(r) * 30, (r / 320.0) ** 2 * 100))
    W("")
    W("     ⇒ 单分辨率下「精度」与「帧率」是**同一条曲线上的两个点**，只能选点，不能兼得。")
    W("")
    W("  B) ★ 真正能兼得的两条结构 —— 二选一：")
    W("")
    W("     B1【两阶段级联 / ROI 精修】：低分辨率全图找候选 → 高分辨率抠 ROI 精检")
    W("        算力 = 全图(低分辨率) + N×ROI(高分辨率，但面积很小)")
    W("        ROI 面积占比假设 10%（球场里球/人只占小部分）：")
    for base, roi in ((192, 320), (224, 320)):
        g_base = gop(base)
        # ROI 数量 8 个、每个占全图面积 1.25%（合计 10%）
        g_roi = gop(roi) * 0.10
        tot = g_base + g_roi
        W("          基准 %d + ROI %d @10%%面积 = %.3f GOP/帧" % (base, roi, tot))
        W("            100MHz/60%%效率 约 %.1f fps ; 150MHz/75%% 约 %.1f fps"
          % (144 * 2 * 100 / 1000 * 0.60 / tot, 144 * 2 * 150 / 1000 * 0.75 / tot))
    W("")
    W("        ★ 代价：**两遍推理**（调度复杂度上升），且第二遍要等第一遍结果 ⇒ 有额外延迟。")
    W("          但注意：ROI 面积只占 10% ⇒ 第二遍的算力代价很低（约为全图的 1/10）。")
    W("")
    W("     B2【时间维兼得：隔帧全图 + 中间帧跟踪】")
    W("        每 N 帧做一次高分辨率检测，其余帧用轻量跟踪（相关滤波/光流）外推")
    W("        ⇒ 对外**输出帧率 = 相机帧率**，而「检测」只在部分帧发生")
    W("        ★ 代价：跟踪器本身要算力（但远小于 CNN），且**遮挡/快速运动时会丢**")
    W("          对球场判罚这种要求「每一帧都可信」的场景，需谨慎 —— 建议只用于「平滑显示」，")
    W("          **判罚结论仍只用检测帧**。")
    W("")
    W("  C) ★ 还有一个免费的兼得：**降低有效算力需求**（不改分辨率）")
    W("     · 深度可分离卷积替换部分 3×3：算力降 8–9×，精度略降（MobileNet 已验证）")
    W("     · 剪枝 / 通道裁剪：YOLOv8n 本身可裁 20–30%，算力近线性下降")
    W("     · NMS/decode 放 PS（已计划），不占 PL")
    W("     ⇒ 这些是**模型层面**的优化，与硬件并行独立，可叠加。")
    W("")

    W("=" * 90)
    W("Q2 双路：同一份硬件跑两路视频")
    W("=" * 90)
    W("")
    W("  瓶颈**不是算力而是 DSP 数量** —— 两路要么时分复用，要么空间并行。")
    W("")
    W("  %-22s %10s %10s %12s %s" % ("方案", "单路DSP", "两路DSP", "占 220", "说明"))
    W("  " + "-" * 84)
    W("  %-22s %10s %10s %12s %s" % ("时分复用(1个引擎跑2路)", "96", "96", "44%",
                                     "两路各得一半帧率"))
    for par in (4, 8, 16):
        dsp, lut, bram = MEASURED[par]
        two = dsp * 2
        note = "两路各 1 个引擎，LUT 也要 ×2"
        W("  %-22s %10d %10d %11.0f%% %s" % ("空间并行(2个引擎) PAR=%d" % par, dsp, two,
                                            two / DSP_TOTAL * 100, note))
    W("")
    W("  ★ 关键账（空间并行）：")
    for par in (4, 8, 16):
        dsp, lut, bram = MEASURED[par]
        W("     PAR=%2d: DSP %3d/%3d (%.0f%%)  LUT %5d/%5d (%.0f%%)  BRAM18 %3d/%3d (%.0f%%)  %s"
          % (par, dsp * 2, DSP_TOTAL, dsp * 2 / DSP_TOTAL * 100,
             lut * 2, LUT_TOTAL, lut * 2 / LUT_TOTAL * 100,
             bram * 2, BRAM_TOTAL, bram * 2 / BRAM_TOTAL * 100,
             "✅ 双路可行" if lut * 2 <= LUT_TOTAL and dsp * 2 <= DSP_TOTAL else "❌ 装不下"))
    W("")
    W("  ⇒ **PAR_OUT=8 双引擎是双路的甜点**：DSP 96/220、LUT 34354/53200 (65%)、BRAM 80/280。")
    W("     两路各有 72 MAC/clk 的引擎，互不干扰。")
    W("  ⇒ PAR_OUT=16 双引擎 DSP 192/220 (87%) + LUT 56984/53200 (**107%，装不下**) ⇒ 不行。")
    W("")

    W("=" * 90)
    W("Q3 带宽：AXI HP 够不够（双路时最容易忽略的瓶颈）")
    W("=" * 90)
    W("")
    # 从 golden 统计每帧张量总量
    for res in (320, 640):
        p = ROOT / "data" / "golden" / ("yolov8n_%d_golden.json" % res)
        if not p.is_file():
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        tot_elems = 0
        for k, v in d["tensors"].items():
            n = 1
            for x in v["shape"]:
                n *= x
            tot_elems += n
        # INT8 激活：每个张量进出各一次 ⇒ 字节 ≈ 2 × 元素数
        bytes_per_frame = tot_elems * 2
        W("  %dx%d: %d 个张量, 元素总数 %.2f M" % (res, res, len(d["tensors"]), tot_elems / 1e6))
        W("         激活进出总量 ≈ %.2f MB/帧（INT8）" % (bytes_per_frame / 1e6))
        for fps in (15, 30):
            W("           @%2d fps ⇒ %6.1f MB/s（单路）" % (fps, bytes_per_frame * fps / 1e6))
            W("                      双路 ⇒ %6.1f MB/s" % (bytes_per_frame * fps * 2 / 1e6))
    W("")
    W("  ★ Zynq-7000 的 AXI HP 端口：2 个 HP 口，各 64-bit @ ~150MHz ⇒ 单口理论 ~1.2 GB/s，")
    W("    但**实测通常只能到 400–600 MB/s**（受 DDR3 效率、仲裁、突发长度影响）。")
    W("    ⇒ 单路 30fps 约 300–700 MB/s 已在边缘；**双路 30fps 极可能撞墙**。")
    W("    ⇒ 缓解：① 权重常驻 DDR 但**缓存到 PL 片上**（3.15 MB 放不进，但可做 2–3 层的窗口）")
    W("            ② 特征图用 INT8 + 分块(tiling)，减少往返")
    W("            ③ **两路共享权重**（同一模型）⇒ 权重只读一次，省一半带宽")
    W("")
    W("=" * 90)
    W("汇总：我的建议")
    W("=" * 90)
    W("")
    W("  1) 兼得：**基准 192×192 全图 + ROI 320 精修**（B1）。")
    W("     算力 ≈ 0.787 + 0.219 = 1.006 GOP/帧 ⇒ 比直接 320 全图(2.186)**省 54%**，")
    W("     而 ROI 内部是 320 分辨率 ⇒ 小目标检出能力接近 320 全图。")
    W("  2) 双路：**PAR_OUT=8 双引擎**（DSP 96/220、LUT 65%），两路并行互不干扰。")
    W("     每路 192 基准 + ROI ⇒ 单路约 1.0 GOP/帧。")
    W("  3) ⚠️ **双路 + 30fps 的带宽是最大风险**，必须先在板上实测 AXI HP 吞吐。")
    W("")

    txt = "\n".join(L)
    out = ROOT / "data" / "results" / "design_tradeoffs.txt"
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(txt + "\n")
    print(txt)
    print("已写 %s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
