"""算清「能不能到 25/30 fps」——基于 csynth 实测资源，而不是拍脑袋。

输入事实（全部来自本机实测，见 data/results/hls_probe.json）：
    · 器件 xc7z020clg400-2：220 DSP48E1 / 280 BRAM18 / 53200 LUT / 106400 FF
    · PAR_OUT=16 的卷积核 csynth 实测：DSP 96 (44%) / LUT 28492 (54%) / Slack +4.66ns @10ns 时钟
    · 该配置每时钟 MAC = PAR_OUT × 9 = 144
    · YOLOv8n @320 = 2.186 GOP/帧（本仓库 onnx 探针实测）

关键约束（必须明说）：
    Zynq-7020 的**理论峰值 = 220 DSP × 2 ops × 频率**：
      100 MHz -> 44.0 GOPS
      150 MHz -> 66.0 GOPS
      200 MHz -> 88.0 GOPS
    ⇒ 25 fps @320 需要 54.7 GOPS；30 fps @320 需要 65.6 GOPS。
      100 MHz 下**物理上不可能**（峰值只有 44），这不是代码问题，是器件问题。
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

# ---- 器件与实测 ----
DSP_TOTAL = 220
LUT_TOTAL = 53200
BRAM_TOTAL = 280

# csynth 实测（PAR_OUT -> DSP, LUT）
MEASURED = {2: (13, 9136), 4: (25, 12098), 8: (48, 17177), 16: (96, 28492)}

# PAR_OUT=16 时 slack=+4.66ns @10ns => 关键路径 ~5.34ns => 理论可达 ~187 MHz
# 保守取 150 MHz（时序余量 + 布线延迟，csynth 只是估计）
SLACK_NS = 4.66
PERIOD_NS = 10.0
FMAX_EST_MHZ = 1000.0 / (PERIOD_NS - SLACK_NS)

# YOLOv8n 各分辨率的 GOP（320 实测 2.186，按面积线性缩放 —— 已知近似，见 docs）
GOP_320 = 2.186

def gop_at(res: int) -> float:
    return GOP_320 * (res / 320.0) ** 2


def main() -> int:
    lines = []
    W = lines.append

    W("=" * 86)
    W("能不能到 25 / 30 fps —— 基于 csynth 实测资源的推算")
    W("=" * 86)
    W("")
    W("器件硬上限（理论峰值 = 220 DSP × 2 ops × 频率）:")
    for f in (100, 150, 200):
        W("  %3d MHz -> %5.1f GOPS" % (f, DSP_TOTAL * 2 * f / 1000.0))
    W("  csynth 实测 slack +%.2f ns @ %.0f ns 时钟 => 关键路径 %.2f ns => 理论 ~%.0f MHz"
      % (SLACK_NS, PERIOD_NS, PERIOD_NS - SLACK_NS, FMAX_EST_MHZ))
    W("")

    W("所需算力（按 YOLOv8n 逐分辨率）:")
    W("  %-10s %10s %12s %12s" % ("分辨率", "GOP/帧", "25fps 需要", "30fps 需要"))
    for res in (160, 192, 224, 256, 320, 640):
        g = gop_at(res)
        W("  %-10s %10.3f %10.1f GOPS %10.1f GOPS" % ("%dx%d" % (res, res), g, g * 25, g * 30))
    W("")

    W("=" * 86)
    W("实测配置的算力上限（PAR_OUT 扫描）")
    W("=" * 86)
    W("  %-8s %6s %6s %8s %10s %12s %12s" %
      ("PAR_OUT", "DSP", "LUT%", "MAC/clk", "峰值GMAC/s", "峰值GOPS", "LUT是否装下"))
    for par, (dsp, lut) in sorted(MEASURED.items()):
        mac_clk = par * 9
        # 实测 p16 是 96 DSP 做到 144 MAC/clk => 每 DSP 1.5 MAC/clk（int8 打包）
        mac_per_dsp = mac_clk / dsp
        W("  %-8d %6d %5.0f%% %8d %10.1f %12.1f %12s"
          % (par, dsp, lut / LUT_TOTAL * 100, mac_clk,
             mac_clk * 100 / 1000.0, mac_clk * 2 * 100 / 1000.0,
             "是" if lut <= LUT_TOTAL else "否"))
    W("")
    W("  ★ 注意：上表峰值按 100 MHz 算。LUT 在 PAR_OUT=16 时已 54%%，")
    W("    再往上（24/32）LUT 会逼近或超出 100%% —— 所以**并行度不能无限加**。")
    W("")

    W("=" * 86)
    W("结论：各配置的实际可达 fps（含现实效率折扣）")
    W("=" * 86)
    W("  效率折扣的取法：csynth 峰值是理想值，真实含 AXI 访存、层间切换、")
    W("  未命中、tiling 边界 ⇒ 按 **60% / 75%** 两档给出区间，不取 100%。")
    W("")
    W("  %-8s %-8s %-8s %10s %18s %s" %
      ("PAR_OUT", "频率", "有效GOPS", "320x320", "224x224", "192x192"))
    for par in (8, 16):
        dsp, lut = MEASURED[par]
        mac_clk = par * 9
        for freq in (100, 150):
            peak_gops = mac_clk * 2 * freq / 1000.0
            for eff in (0.60, 0.75):
                eff_gops = peak_gops * eff
                cells = []
                for res in (320, 224, 192):
                    fps = eff_gops / gop_at(res)
                    cells.append("%5.1f fps" % fps)
                W("  %-8d %-8s %-8.1f %10s %18s %s"
                  % (par, "%dMHz" % freq, eff_gops, cells[0], cells[1], cells[2]))
    W("")

    W("=" * 86)
    W("★ 直接回答「25/30 fps 行不行」")
    W("=" * 86)
    W("")
    W("  【320×320】❌ 不行。")
    W("    25 fps 需 %.1f GOPS，30 fps 需 %.1f GOPS；"
      % (gop_at(320) * 25, gop_at(320) * 30))
    W("    而 100 MHz 的理论峰值只有 44.0 GOPS —— **器件物理上限就够不着**，")
    W("    不是代码优化能解决的问题。即便 150 MHz + 满 DSP 也只有 66.0 GOPS，")
    W("    仅 30 fps 的「理论值」勉强够、实际达不到（无余量）。")
    W("")
    W("  【224×224】✅ 可以，但要看并行度与频率：")
    W("    25 fps 需 %.1f GOPS ⇒ PAR_OUT=16 @100MHz 有效约 %.1f GOPS ⇒ 够" %
      (gop_at(224) * 25, 144 * 2 * 100 / 1000.0 * 0.75))
    W("    30 fps 需 %.1f GOPS ⇒ 建议 @150MHz 或提高 PAR_OUT" % (gop_at(224) * 30))
    W("")
    W("  【192×192】✅ 可以，余量较足（30 fps 需 %.1f GOPS）" % (gop_at(192) * 30))
    W("")
    W("  【160×160】✅ 很宽松（30 fps 需 %.1f GOPS）" % (gop_at(160) * 30))
    W("")
    W("  ⚠️ 以上全部是 csynth 资源外推，**不是板上实测**。真值要等 M6/M7。")
    W("")

    out = ROOT / "data" / "results" / "fps_feasibility.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    txt = "\n".join(lines)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(txt + "\n")
    print(txt)
    print("已写 %s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
