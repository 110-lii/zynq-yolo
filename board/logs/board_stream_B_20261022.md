# 步骤 B 实测：权重 12 字节对齐（2026-10-22）

- 比特流：`board/overlay/conv_engine.bit`，83.33 MHz，WNS **+0.794 ns**
- 权重布局：每 (co,ci) 记录 K≤2 → 4 B，K=3 → 12 B；**补齐只在运行时做**
  （`board_net.py` / `pynq_driver/conv_engine.py`），盘上 `w.bin` 保持 ONNX 原布局
- 单层：`BOARD_LAYER_OK all_6_cases`，聚合 **0.2114 GOP/s**

## 单层对比

| 用例 | 步骤 B | 上一版（72 次散读） | 原始（字节事务） |
|---|---:|---:|---:|
| k1s1_smallw | 1.40 ms | 1.65 ms | 1.55 ms |
| k1s1_tailcout | 6.48 ms | 8.58 ms | 8.60 ms |
| k3s1_c3_40 | 2.96 ms | 3.55 ms | 3.58 ms |
| k3s1_cin512 | **32.3 ms** | 68.8 ms | 68.8 ms |
| k3s1_pad0 | 2.44 ms | 4.49 ms | 4.58 ms |
| k3s2_c16_40 | 5.58 ms | 10.14 ms | 10.15 ms |
| 聚合 GOP/s | **0.2114** | 0.1156 | 0.0846 |

## 整网

| 项 | 步骤 B | 上一版 | 基线 |
|---|---:|---:|---:|
| conv | **6.25 s** | 8.13 s | ~18.5 s |
| other | 1.44 s | 1.45 s | ~2.3 s |
| 合计 | **7.75 s** | 9.63 s | 20.8 s |
| 检出 | det=2 | det=2 | — |

相对基线 **2.68×**。

## 关键教训

1. 补齐必须**只在运行时**做。盘上 pack 被改成 12 B 后，`make_layer_plan` 按 9 B
   读取会直接 `layer_plan_error`（整网那次 det=114 是失败后的假结果）。
2. BD 里改 `PCW_FPGA0_PERIPHERAL_FREQMHZ` **不影响板子**：实际 `fclk0=83.33 MHz`，
   PS7 时钟由启动 ps7_init 决定。换频率要走 PYNQ `Clocks.fclk0_mhz`。
3. 本版关键路径 11.21 ns ⇒ 上限约 89 MHz，83.33 已是当前结构的上限。

## 下一步

- 步骤 C：ping-pong 重叠（每 ci 装载与计算并行），目标 conv 6.25 → 2.5–3 s。
- 步骤 D：层序列，去掉 other 的 1.44 s。
