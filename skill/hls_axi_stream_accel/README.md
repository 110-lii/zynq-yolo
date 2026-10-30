# hls_axi_stream_accel —— HLS + AXI 加速器的性能与正确性套路

> 上级：[`../README.md`](../README.md) · 类型：**HLS/AXI 通用技能包（不绑定本题模型，换加速器仍可用）**

## 1. 适用范围

- **工具/器件**：Vitis_HLS / Vivado 2020.2，Zynq-7000（本队 XC7Z020-CLG400-2）；
- **接口形态**：`m_axi`（DDR 读写）+ `s_axilite`（标量/控制）+ AXI-Stream 或裸内存布局；
- **适用任务**：自研卷积/掩码/预处理等流式加速器，要求“先逐位对，再谈吞吐”；
- **不覆盖**：Vitis AI DPU（7000 系不存在）、UltraScale+ 的 HBM/多 DDR 口、
  DATAFLOW 下同一 bundle 被多个 process 读的复杂拓扑。

## 2. 使用方法

### 2.1 先冻结数值契约，再动性能

1. 固定输入/输出布局、量化口径、边界处理（padding/越界）；
2. 写 testbench，**逐位比对**（不是 cosine/近似）：
   `BOARD_LAYER_OK all_6_cases`、`MASK_TB_OK`、`TB_CONV_OK` 这类令牌是放行条件；
3. 再做 csynth，检查 **II=1**、资源、时序；不通过就回源码，不要先出片。

### 2.2 访存四件事（按收益排序）

| 序 | 改动 | 典型收益 | 判据 |
|---|---|---|---|
| 1 | 字节指针 → **32-bit 字指针** | 单层 10–22 MB/s → 222 MB/s | AXI 标定 `AXI_BW_OK` |
| 2 | 条件读 → **无条件连续读 + 数据选择** | 可合并突发 | 读吞吐接近理论 55% |
| 3 | 补 `num_read_outstanding` / `max_read_burst_length` | 32 → 64 beat 突发 | 64-bit 读 444 MB/s |
| 4 | 散装权重读 → **展平 + `PIPELINE II=1`** | 20.8 → 9.63 s（整网） | scores 逐位不变 |

### 2.3 先标定，再优化

用 `axi_bw` 这种“只搬数据、不算”的核先测板上限。本队实测（100 MHz）：
32-bit 读/写 222 MB/s、64-bit 读 444 MB/s、拷贝 444 MB/s；32-bit 理论 400 MB/s，
实测 55.5 %。**限制因素是 outstanding 与 DDR 往返，不是端口位宽**。
如果单层吞吐远低于这个上限，先查事务粒度/条件读，不要先加并行度。

### 2.4 资源与片上容量

- `PAR_OUT` 每翻倍，DSP 近似翻倍；本队 `p16/p24/p32` 的 csynth 分别 241/353/465 DSP，
  全部超 220，不能出片；
- 大张量不要整帧进 BRAM：416×416 首层输出 692,224 B 已超片上预算，
  必须行带拆分/分块；
- **DATAFLOW 按 bundle 拆分读口不可行**：同一 bundle 不能被多个 process 读；
  拆开后 FF 曾涨到 2623 %（约 139.6 万），已放弃。正确做法是**从顶层拆成多个
  `m_axi` bundle**，而不是在 DATAFLOW 内拆。

## 3. 失效条件（可观察现象）

| 条件 | 现象 | 处理 |
|---|---|---|
| 指针是 `ap_uint<8>*` | m_axi 事务粒度 1 字节，吞吐 10–22 MB/s | 改 32-bit 字指针，布局不变 |
| 读地址挂在 `ok ? ... : ...` 上 | 无法合并突发，II 看似 1 但吞吐低 | 无条件读窗口 + 数据选择 |
| 权重按 `[co][ci][9]` 散读 | 每 ci ~720 拍，整网慢 | 展平成记录 + `PIPELINE II=1` |
| `ARRAY_PARTITION` 维度写错 | 静默降级为 BRAM，II 变差 | 看 csynth 的循环表/资源表 |
| `core_revision` 溢出 | IP packer 解析失败 | 改小 Revision，在 IP 目录内跑 `run_ippack.tcl` |
| 只跑 csim 没跑 csynth | csim 过、出片失败/资源爆 | csim + csynth + 出片三级门禁 |
| 用 cosine 代替逐位 | 数值“看起来对”，边界错 | 逐字节/逐元素 `array_equal` |

## 3.1 自带检查

`checks/axis_stream_check.py`：AXI-Stream **握手规则检查器**（只依赖 Python 标准库）。
把 TB 里 `$fdisplay` dump 的逐拍 trace 丢给它，能抓 R1（`tvalid&&!tready` 期间数据变化）、
R2（握手完成前撤回 `tvalid`）、R3（连续拖欠超门限）、R4（拍数与期望不符）四类问题。

```bash
python checks/axis_stream_check.py --selftest      # AXIS_CHECK_SELFTEST OK（正例+三个负例）
python checks/axis_stream_check.py trace.txt       # AXIS_CHECK_OK beats=N
```

> 为什么放在技能包里：这类违规在波形上肉眼几乎看不出来，而它一旦发生，
> 「csynth 过了、逐位比也对」仍然会在整机上丢拍。

## 4. 提炼来源

- `ai/sessions/005_engine_load_weight_pipeline.md`：装载裁剪、权重流水、PS 首层 float32
  精确累加；41.4→20.8 s；
- `ai/sessions/007_pl_mask_preproc.md`：字节→4 像素/字→16 像素/轮 + 三 bundle，
  54.6→16.1 ms；但整条管线没有净收益（11.1 FPS < 纯 PS 12.3 FPS）；
- `ai/sessions/008_conv_engine_axi_rewrite.md`：字节事务→32-bit 字，6/6 逐位，
  20.8→9.63 s；
- `board/logs/board_axi_bw_20261022.md`：四模式带宽标定；
- `docs/accel_interface.md` §7：资源/时序与出片配置。

## 5. 已验证的效果

| 判据 | 结果 |
|---|---|
| 逐位一致 | `BOARD_LAYER_OK all_6_cases`（6/6）；`MASK_TB_OK`；`TB_CONV_OK` 6/6 |
| AXI 带宽 | `AXI_BW_OK`：32-bit 读 222.18 MB/s、64-bit 读 444.26 MB/s、拷贝 444.36 MB/s |
| 整网优化 | 41.4 → 20.8 s（1.99×）→ 9.63 s（相对 20.8 s 再 2.16×） |
| 资源 | conv_engine：LUT 23,552（44.27 %）、DSP 172（78.18 %）、WNS +1.350 ns @83.33 MHz |
| 反例 | PL 掩码 v4 16.1 ms/帧，但整条管线 11.1 FPS < 纯 PS 12.3 FPS ⇒ “单核快”不等于“系统快” |

> **本项目最终结论**：这套 HLS/AXI 方法把 conv_engine 从 41.4 s 优化到 9.63 s，
> 但 7020 上仍不足以做主检测；它作为 6/6 bit-exact 的技术验证资产保留（D017）。
> 技能包本身可复用于任何 Zynq-7000 流式加速器。
