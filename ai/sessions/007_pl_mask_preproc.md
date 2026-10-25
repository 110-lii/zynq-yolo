# Session 007 — PL 掩码预处理器（运动 + 白球）

| 字段 | 内容 |
|---|---|
| 会话号 | 007 |
| 日期 | 2026-10-21 |
| 执行 AI（模型 ID） | `group/1101` |
| 执行客户端 / 工具 | `kilo`（CLI，Windows 10 / PowerShell） |
| 角色 | 单人 |
| 任务目标 | 把 PS 上最贵的像素阈值运算搬到 PL：输出运动掩码 + 白球掩码，PS 只做形态学/连通域/闸门 |
| 上下文投喂 | 板端 profile（resize 3.9 / 补偿 15.8 / 连通域 9.6 / 球员闸门 38.2 / 球 ROI 8.4 / 绘制 4.3 ms） |
| **原文位置** | **待导出**（CLI 会话） |
| 关联决策 | `D016` |
| 复核方 | 未复核 |

## 设计

`src/hls/mask/src/mask_preproc.cpp`：

- 输入 `cur` / `prev`（BGR uint8），输出每像素 1 字节：bit0=白（max(BGR)>white_thr 且 max−min<sat_thr），bit1=运动（|G−Gprev|>diff_thr）。
- 单 m_axi bundle（读两帧 + 写掩码）→ S_AXI_HP0；s_axilite 标量。
- 形态学与连通域**留在 PS**：稀疏掩码上 cv2 只要 2–4 ms，且方便调参。

## 验证

| 项目 | 结果 |
|---|---|
| csim（37×23，逐位） | **MASK_TB_OK 851** |
| csynth | DSP 0 / BRAM 2 / LUT 2181（4%）/ FF 1851（1%），**II=4** |
| 640×360 预估 | ~11 ms/帧（PS 原 41 ms） |
| 出片 | **WNS +3.632 ns** @ 83.33 MHz；LUT 3414（6.4%）、BRAM 1、DSP 0 |
| 板端 v1（字节访问） | **逐位 0 mismatch**，但 **54.6 ms / 18.3 FPS**（字节级 AXI 事务是瓶颈，不是逻辑） |
| v2（4 像素/字） | csim MASK_TB_OK 864；csynth II=6、LUT 2512 / FF 2390 / BRAM 2 / DSP 0；640×360 预估 ~4.2 ms；板端 **20.2 ms** |
| v3（+outstanding=16） | 板端 **20.2 ms**（无变化） |
| v4（16 像素/轮 + cur/prev/out 三 bundle → HP0/1/2） | csim 864；csynth 39 拍/轮；板端 **16.1 ms / 62 FPS**，逐位 0 mismatch |
| 接入 fast_events（PL 掩码） | **11.1–11.8 FPS**，反而略低于纯 PS 的 **12.3 FPS** |

## 出片坑（两次）

1. **core_revision 溢出**：HLS 2020.2 把 Revision 写成 `YYMMDDHHMM`（2610211218 > 2^31），IP packer 用 int32 解析失败。修法：把 `run_ippack.tcl` 的 `set Revision` 改成小整数，再手工跑。
2. **必须在 IP 目录内跑 run_ippack.tcl**：第一次从仓库根目录 `-source <绝对路径>` 跑，只生成了残缺的 component.xml（缺 `xilinx_verilogsynthesis` fileSet），BD 报 *IP does not support 'Synthesis' outputs*。用 `workdir=build/hls_export/exp_mask/sol_mask/impl/ip` 重跑后正常。

## 产出

| 路径 | 说明 |
|---|---|
| `src/hls/mask/src/mask_preproc.cpp` | 新增：掩码核 |
| `src/hls/mask/tb/tb_mask_preproc.cpp` | 新增：逐位 TB（`MASK_TB_OK`） |
| `src/hls/mask/scripts/{csim,csynth,export}_mask.tcl` | 新增：仿真/综合/导出 |
| `build/scripts/bd_mask_preproc.tcl` | 新增：BD + 出片（`BD_DONE_OK`） |
| `src/ps/pynq_driver/mask_preproc.py` | 新增：PYNQ 驱动（寄存器偏移取自 `xmask_preproc_hw.h`） |
| `src/ps/host/test_mask_preproc.py` | 新增：板端逐位 + 速度测试（`MASK_OVL_OK`） |

## 结论（重要）

PL 掩码核本身正确、资源可忽略（LUT ~5.3K、DSP 0），但**在 640×360 上不划算**：

- PL 每帧 16.1 ms ≈ PS 上被替代的「补偿 15.8 ms + 帧差」，没有净收益；
- 白球掩码没有形态学，ROI 端的候选处理反而变贵（PL 路径 6.4 ms/ROI vs PS 1.4 ms/ROI）；
- 整条管线 11.1 FPS < 纯 PS 12.3 FPS。

⇒ 真正瓶颈是 **球员闸门（10 ROI 约 38 ms）** 与 ROI 数量，不是像素阈值。

## 未决

- 板端出片与 `MASK_OVL_OK` 尚未跑完（BD 第二次构建中）。
- 驱动按 64 位指针低/高 32 位写入；`.hwh` 地址布局变化时需按 mem_id 查找，不硬编码。
- 接入 `fast_events.py` 后，PS 的白球/帧差两段（41 ms / 9.6 ms）由掩码 DMA 替代，预计 81 ms → ~45 ms（22 FPS）。