# 系统架构（D017 定稿）

> 上级：[`../README.md`](../README.md) · 同层：[`repo_spec.md`](repo_spec.md)
>
> **状态：✅ 已按 D017 定稿（2026-10-25）。** 数据流、软硬件划分、`preproc` 接口契约与
> 资源/时序实测均已填入；历史 416/`conv_engine` 路线见
> [`integration_rtl_plan.md`](integration_rtl_plan.md)（顶部已标“已取代”）。
>
> ★ **2026-10-20 输入源变更（D015）**：本项目**不使用板载摄像头**，视频由 **PC 经以太网送入**。
> 原 DVP 摄像头采集链退出主线，仅保留为历史/回归资产。

---

## 0. 本文的用途与边界

指南 3.3.5.3 要求设计报告包含「设计原理与功能框图」「软硬件划分依据与接口设计」。
本文是这两节在**仓库里**的工作底稿：先在仓库中把架构想清楚、把依据写下来，再誊进 `report/`。

**分工**：

| 内容 | 放哪 |
|---|---|
| 架构的**依据与推理** | 本文（可改、可辩论、带出处） |
| 架构的**最终定稿与图** | [`../report/`](../report/)，图源放 [`../report/figures/`](../report/figures/) |
| 架构的**决策状态** | [`../ai/coordination/DECISIONS.md`](../ai/coordination/DECISIONS.md) |
| 本文引用的**硬件事实** | [`../ref/hardware.md`](../ref/hardware.md)（每条带 `文件:行号`） |

---

## 1. 选题与创新点 —— 已裁定范围摘要

> 依据：D010（看得见的目标）、D011（人+球+简单事件+倒地+轨迹）、D012（球员/非球员区分）、
> D013（触屏作数据输入）、D014（以太网 + SD 双通道输出）、D015（视频由 PC 经以太网送入）。
> **正式题名、创新点表述与指标承诺仍待写进 `report/`；本表只记录已裁定的功能边界。**

| 项 | 已裁定内容 |
|---|---|
| 题目（工作名） | 基于 Zynq-7020 的**足球比赛视频分析系统**（正式题名待定） |
| 目标用户与场景 | 赛事/训练视频的离线或近实时分析；视频源为 **PC**，经**以太网**送入板端 |
| 核心交付 | ① 人的识别与轨迹 ② 球的识别 ③ 简单事件（球出界后的球权归属、进球、出界；**角球/球门球缺“最后触球+进攻方向”，不在范围**）④ 人员倒地识别（候选）⑤ **HDMI 带框环出**（D026） |
| 明确不做 | 越位/犯规/红黄牌等判罚推理；板载摄像头采集 |
| 说明 | v1 曾把「HDMI/OSD 视频输出」列为不做，**2026-10-27 的 D026 已改为要做**（判据：PL 行欠载 `under` 稳定为 0 + 人眼） |
| 创新点（正式表述见 `report/design_report.md`） | 在 220 DSP 的 7020 上做异构划分：**PL 预处理（BGR→320×320 RGB，18.8 ms/帧）+ PS ncnn YOLOv8n-0.25 检测**；球用 ROI/白块候选 + 小型 C 分类器 + 轨迹确认；球衣颜色由触屏人工录入，辅助球员/非球员区分与队别归属 |
| 与旧项目 `D:\FPGA\oi` 的关系 | 原则：**复用管线，PYNQ 软硬件框架重做**（D001）；具体复用范围仍为 D007 `discussing` |

---

## 2. 系统功能框图 —— 已裁定数据流（文字版）

框图定稿后放 `report/figures/system_block.<fmt>`。当前只能给出已裁定的**文字版数据流**：

```
[PC：视频文件 / 推流（tools/stream_sender.py）]
        │  以太网（D015）
        ▼
[PS：接收 → 帧重组 → DDR 帧缓冲]
        │
        ▼
[PL：track_preproc（D019）— BGR888 → 3×320×320 RGB + 320×320 白/运动掩码]
        │                    │
        │ RGB 平面            │ 运动掩码（跟踪门控）
        ▼                    ▼
[PS：ncnn YOLOv8n-0.25 单类 person（0.95–1.0 s/帧）]
        │
        ▼
[PS：SORT 跟踪（可选 PL 运动门控）→ 球链路（白块候选 + C 分类器 8 ms + 轨迹确认）]
        │
        ▼
[PS：单应 → 球场坐标 → 事件（出界/进球/球权）]
        │  结果只序列化一次
        ├──► [以太网推给 PC（result_stream → pc_dashboard）]
        ├──► [SD 卡 JSONL + summary（result_store）]
        └──► [HDMI/LCD 状态面板（status_display；fbdev/PNG）]
```

> `preproc` 单独出片仍是已验证主线；`track_preproc` 是 D019 的融合升级，
> 已出片并**上板逐位对通过**（`TRACK_PREPROC_BOARD_OK`）。无新 bitstream 时
> `--pl-track` 自动回退 PS。

> 已废弃主线：`conv_engine` 整网逐层调度（416×416 / YOLOv5n）实测 9.63 s/帧（≈0.10 FPS），
> 见 D017 与 `docs/integration_rtl_plan.md` 顶部说明。它仍作为 6/6 bit-exact 的
> 技术验证资产保留。

**画图规矩**（先定规矩，后填内容）：

1. 框图必须标出 **PS 侧 / PL 侧的分界线** —— 这是 Zynq 设计图与普通 FPGA 框图的分水岭。
2. 每个跨界线必须编号，并与 §4 接口契约表一一对应。**线上没有编号的箭头 = 未定义接口。**
3. 每个处理块旁标注它落在 PL 的哪个子目录前缀（见 `src/README.md` 的前缀约定）。
4. 图上出现的每个数字（位宽、频率、帧率）必须在图注中给出出处。

---

## 3. 软硬件划分依据（D017 定案）

指南 3.3.5.3 明确要求写「**划分依据**」，不是只写划分结果。下表每行都是**实测后**的选择：

| 功能 | 放 PS | 放 PL | 判据（为什么） |
|---|---|---|---|
| 视频接收 / 帧缓冲 | ✅ | | 协议栈、重传、文件/流解析是分支密集的控制流；DDR3 只挂 PS |
| 预处理（BGR→320×320 RGB 平面） | 回退 | ✅ | 纯像素流、无分支、可 II=1 流水；实测 **18.8 ms/帧**，IP LUT **3,812**。PS 回退用 `cv2.resize` |
| person 检测（YOLOv8n-0.25） | ✅ | | 0.386 GMAC/帧；PS ncnn 实测 **0.95–1.0 s/帧**。PL `conv_engine` 整网实测仅 **9.63 s/帧（≈0.10 FPS）**，且 416 档与片上容量冲突，故退出主线（D017） |
| SORT 跟踪 / 机位运动补偿 | ✅ | | 帧间关联、ID 管理、异常分支；无固定数据并行 |
| 球候选 / 分类 / 轨迹 | ✅ | | 候选少（每帧几个 ROI）、C 分类器 **8 ms/候选**； ROI 尺寸随画面变化，不适合固定流水 |
| 单应 / 球场坐标 / 事件 | ✅ | | 6 点标定 + 浮点几何 + FIFA 整球过线口径，分支与阈值密集 |
| 结果输出（以太网 + SD） | ✅ | | 复用 Linux 网络栈/文件系统；PL 不参与（D014） |
| 触屏录入球衣颜色 / 半场 | ✅ | | 人机界面，事件驱动（D013） |

**一句话原则**：PL 只做「像素级、规则、可流水化」的部分；检测/跟踪/几何/事件这些
**分支密集或访存不规则**的部分留在 PS。这样既满足「PL 上必须有实际算法」的演示约束，
又避开 7020 上 PL 卷积引擎「算得动但喂不快」的实测瓶颈。

**可用的论证维度**（来自本机硬约束，见 [`../ref/hardware.md`](../ref/hardware.md)）：

| 维度 | 本机事实 | 对划分的影响 |
|---|---|---|
| 算力 | 220 DSP48；双 A9 @766 MHz | DSP 密集型放 PL；串行/控制/协议栈放 PS |
| 存储 | 1 GB DDR3 **只挂 PS 侧**，PL 须经 AXI HP | PL 需要大缓冲时走 HP 口，不要在 PL 内堆大 BRAM |
| BRAM | **4.9 Mb**（`CLAUDE.md:210` 逐字；`≈140 × RAMB36` 为推算值，一手出处未给 RAMB 个数，见 `ref/hardware.md` §K 第 1 条） | 行缓冲可以，整帧缓存要慎重 |
| 视频输入 | 硬件只有 DVP，**无 MIPI D-PHY、无 HDMI 输入**；但**本项目不使用板载摄像头**（D015），视频由 PC 经以太网送入 | 输入通路起点改为 PS 网络接收；PL 只保留卷积引擎 |
| 网络 | 双千兆：PS 侧 YT8521S（GEM）· PL 侧 YT8531（RGMII） | 视频入口可选 PS GEM 或 PL RGMII，**未冻结**（见 `integration_rtl_plan.md` §7-1） |
| 高速接口 | **无 GTX/GTH**（无 PCIe/SFP/10G） | 不能靠高速串行扩展带宽 |
| 软件生态 | PYNQ（Ubuntu + Jupyter），初级组推荐 | 控制面/可视化/实验记录用 Python 成本最低 |

**硬规矩**：上表每一行的"本机事实"都必须能在 `ref/hardware.md` 找到出处行号。
写不出出处的结论不许进这张表。

---

## 4. 接口契约（`preproc` 上板主线，D017）

> 承旧项目纪律（`D:\FPGA\oi\project\doc\p1_interface_freeze.md`）：**只冻结接口，不冻结算法。**
> 接口改了要牵连上下游，算法改了不用。所以接口必须**先定、定死、留痕**。

`preproc` 的 HLS 接口见 `src/hls/preproc/src/preproc.cpp:24-28`，PYNQ 侧按名字访问
（`tools/board_detect.py:init_pl`），寄存器偏移由 `.hwh` 解析，不硬编码：

| # | 接口 | 方向 | 位宽 / 格式 | 时钟域 | 握手/背压 | 错误处理 | 状态 |
|---|---|---|---|---|---|---|---|
| 1 | `s_axi_control.W_IN` / `H_IN` | PS→PL | 32-bit 无符号 | AXI-Lite / PL FCLK 同域 | AXI-Lite 写 | 非正数由软件侧拒绝 | ✅ 已上板 |
| 2 | `s_axi_control` `AP_CTRL`（0x00） | PS↔PL | bit0 start / bit1 done | 同上 | 软件轮询 bit1 | 超时由调用方处理 | ✅ 已上板 |
| 3 | `gmem_in`（BGR888） | PS DDR→PL | 8-bit × 1280×720×3，行主序 | AXI HP | `m_axi` 突发，`num_read_outstanding=16` | 帧尺寸由 `W_IN/H_IN` 约束 | ✅ 已上板 |
| 4 | `gmem_out`（RGB 平面） | PL→PS DDR | 8-bit × 3×320×320，channel-first | AXI HP | `m_axi` 突发，`num_write_outstanding=16` | 固定 307,200 B | ✅ 已上板 |

`track_preproc`（D019，融合升级）的 4 个数据口：

| # | 接口 | 方向 | 格式 | HP |
|---|---|---|---|---|
| 1 | `gmem_in` | PS DDR→PL | BGR888 W×H×3 | HP0 |
| 2 | `gmem_prev` | PS DDR→PL | 上一帧 BGR888 | HP2 |
| 3 | `gmem_rgb` | PL→PS DDR | 3×320×320 RGB 平面 | HP1 |
| 4 | `gmem_mask` | PL→PS DDR | 320×320（bit0 白 / bit1 运动） | HP3 |

`hdmi_status`（D020，独立 overlay）：PS 通过 AXI BRAM Controller 写
1280×720 1bpp 帧缓存（28,800×32-bit，基址 `0x40000000`）；PL `fb_reader` 读 Port B，
经 vendor `video_driver` + `dvi_transmitter_top` 输出 TMDS。外部 50 MHz `sys_clk` 经
`clk_wiz` 产生 74.25/371.25 MHz；不复用主检测 overlay（PYNQ 同时只能加载一个）。

> `conv_engine` 的 5 个 `m_axi` + 1 个 `s_axilite` 契约仍在
> [`accel_interface.md`](accel_interface.md)，但已**退出主线**（D017），只作历史验证。

**接口条目"写完"的标准**（五问全答上才算完）：

1. **位宽与格式** —— 包括字节序、有符号与否。
2. **时钟域** —— 跨域的，跨域点在哪、用什么同步器（两级 FF / 异步 FIFO / 握手）。
3. **背压** —— 有 `ready` 还是没有；没有背压的通路，溢出怎么办。
4. **帧/包的边界怎么定义** —— 是数据事件还是时间事件。**边界必须长在数据上**，不能靠外部计时。
5. **出错时输出什么** —— 沉默、饱和、还是置错误位。下游必须能区分"没数据"和"数据无效"。

---

## 5. 资源与时序预算（`preproc` 实测，Vivado 2020.2）

`实测`列来自 `build/reports/resource/preproc_bd_util.rpt` 与
`build/reports/timing/preproc_bd_timing.rpt`（BD 顶层，含 PS7 与互连）：

| 资源 | 器件上限（`xc7z020clg400-2`） | 预算 | 实测 | 出处 |
|---|---|---|---|---|
| LUT6 | 53 200 | ≤ 10 % | **4,736（8.90 %）** | `build/reports/resource/preproc_bd_util.rpt` |
| FF | 106 400 | ≤ 10 % | **5,260（4.94 %）** | 同上 |
| DSP48 | 220 | 0 | **13（5.91 %）** | 同上 |
| BRAM (RAMB36) | 140 | ≤ 5 | **1.5（1.07 %）** | 同上 |
| WNS | ≥ 0 | ≥ +1.0 ns | **+3.724 ns** | `build/reports/timing/preproc_bd_timing.rpt` |

> `preproc` IP 自身 LUT 为 **3,812**（`docs/system_status.md`）；BD 顶层多出的部分
> 是 PS7、AXI 互连与复位逻辑。时序报告：Setup 0 failing、Hold 0 failing、PW 0 failing。

`track_preproc`（D019，已出片）BD 顶层：LUT **7,374（13.86 %）**、FF **8,340（7.84 %）**、
DSP **13（5.91 %）**、BRAM **3（2.14 %）**；WNS **+2.457 ns**、WHS **+0.036 ns**、
PW **+4.870 ns**（`build/reports/{resource,timing}/track_preproc_bd_*.rpt`）。
已上板逐位对通过（`TRACK_PREPROC_BOARD_OK`；720p 32.39 ms/帧，板上记录见
[`../board/logs/board_track_preproc_20261025.md`](../board/logs/board_track_preproc_20261025.md)）。

**硬规矩**：

- **实测列没填出处就不许写。** 承旧项目纪律「数字必须标来源」。
- 预算总和超上限时，先砍预算再动手，不要"先实现再看能不能装下"。
- WNS 必须贴 0 报告，不能只写"满足时序"。余量 < 0.1 ns 的要在报告里明说。

---

## 6. 待办

- [x] 题目与创新点口径 → §1（正式表述在 `report/design_report.md`）
- [x] 系统功能框图 → §2 + `report/figures/system_arch.png`
- [x] 逐功能论证软硬件划分 → §3（D017）
- [x] 冻结 `preproc` 接口契约 → §4（已完成上板验证）
- [x] 资源与时序预算 → §5（实测出处已填）
- [x] 优化前后对比表 → `report/design_report.md` §4（基线资源/时序取自 `ai/sessions/005`，优化行取自 `build/reports/`）
