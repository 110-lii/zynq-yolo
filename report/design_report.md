# 基于 Zynq-7020 的足球转播视频目标与事件分析系统

**设计报告（提交稿 v1.0）**

- 日期：2026-10-25
- 器件：Xilinx XC7Z020-CLG400-2（正点原子 领航者 ZYNQ 7020，底板 V3.7）
- 工具链：Vivado / Vitis / Vitis_HLS 2020.2（本机唯一安装版本；与指南推荐版本的差异见 §7.6）
- 代码与复现入口：[`../README.md`](../README.md)、[`../docs/repo_spec.md`](../docs/repo_spec.md)
- 详细指标口径：[`../docs/system_status.md`](../docs/system_status.md)

---

## 0. 摘要

本项目让一块只有 **220 个 DSP、4.9 Mb BRAM、双核 Cortex-A9 @766 MHz** 的 Zynq-7020，
对足球比赛转播视频做**可观测的目标与事件分析**：逐帧球员检测与轨迹、足球定位、球出界/进球/
球权事件，以及人员倒地候选。项目不做越位、犯规、红黄牌等判罚推理。

最终上板路线是 **PL 预处理 + PS ncnn 检测**：

| 环节 | 实现 | 实测 |
|---|---|---|
| PL 预处理 | BGR888 → 3×320×320 RGB 平面，最近邻，II=1 | **18.8 ms/帧**，IP LUT **3,812（7 %）**，DSP 1 |
| PS 检测 | YOLOv8n-0.25，单类 `person`，320×320，ncnn fp32 双线程 | **0.95–1.0 s/帧**，与 PyTorch 输出差约 **2 px** |
| PS 跟踪 | 轻量 SORT + 相位相关机位运动补偿 | 帧间位移中位 **0.2 px**，全程 **34** 个 ID |
| PS 球场坐标 | 6 点单应（DLT）+ 机位有效性三态 | 全场标定残差均值 **1.11 m**；中圈片段 **0.33 m** |
| PS 球链路 | 白块/圆度候选 + 3 层 C 分类器 + 轨迹确认 | 候选召回 **87.3 %**；分类器 P **0.667** / R **0.848**；逐帧确认 **80 %（16/20）** |
| 事件 | 出界（FIFA 整球过线口径）、进球、球权 | 202 帧板上运行：**144 帧**输出球场坐标球，**49 帧**给出球权，`events` 字段同步输出 |
| PL 跟踪辅助 | `track_preproc`：预处理 + 白/运动掩码，单 IP / 4 HP | **已出片并上板逐位对通过**（LUT 7,374 / WNS +2.457 ns；720p 32.39 ms、管线 20.03 ms） |
| 存储/显示/上位机 | SD JSONL + PC Dashboard + HDMI/LCD 状态面板 | 软件自检通过；**HDMI/LCD/合并双屏 overlay 均已出片、帧缓存数字通路读回通过**（D020–D024）；**上板两屏均有画面 ⇒ 显示通过**（014） |
| 历史验证 | PL 通用卷积引擎 `conv_engine` | 单层逐值 **6/6 bit-exact**；整网 416/YOLOv5n **9.63 s/帧**，已退出主线 |

**设计取舍一句话**：在 7020 上，PL 卷积引擎“算得动但喂不快”；把像素级预处理放 PL、
把轻量检测和全部几何/事件放 PS，是功能正确率、资源与时序三者同时可交付的路线（D017）。

---

## 1. 选题背景与创新点

### 1.1 问题背景

足球转播画面有三个客观困难，它们决定了技术路线，而不是模型越大越好：

1. **球太小**。1280×720 转播画面里足球通常只有 **4–12 px**。仓库实测
   `data/results/ball_visibility_sweep.json`：40 帧、25 组配置，recall@conf 0.25 随球在网络
   输入中的像素数变化为 4 px→7.5 %、6 px→33 %、8 px→65 %、10 px→72 %、16 px→88 %；
   **没有任何配置达到 90 %**。整帧输入 320→0/40 帧、640→3/40、1280→26/40 后饱和。
   ⇒ 瓶颈是物理尺寸，不是模型容量；把球塞回全图检测不可行。
2. **白线是亚像素**。12 cm 画线在中距离机位下只有 **0.24–0.58 px**；
   `data/results/pitch_geometry.json` 的 10 组样本 **n_pass = 0**。⇒ 不能靠画面白线自动反解球场几何，
   必须用**人工标定**。
3. **算力与存储都紧**。7020 只有 220 DSP、4.9 Mb BRAM，DDR3 只挂 PS 侧。
   仓库同芯片调研锚点 ARC2020：416×416 YOLOv3-tiny、16-bit、160 DSP 时 **532 ms / 1.88 fps**
   （`docs/yolo_on_zynq7020_survey.md` §3）。⇒ 不能承诺 25–30 fps 的整网 CNN。

### 1.2 题目范围

按 D010/D011/D012/D017，交付四件**可观测**的事：

1. **人的识别与轨迹**：逐帧检测球员，输出图像坐标、球场坐标、跟踪 ID 与置信度；
2. **球的识别**：在 ROI/候选层面定位足球，输出图像坐标与球场坐标；
3. **简单事件**：球出界后的球权归属、进球、出界（角球/球门球由出界位置推导）；
4. **人员倒地识别**：输出倒地**候选**，供人工复核/后续姿态模型升级。

**明确不做**：越位、犯规、红黄牌等判罚推理；**HDMI 视频环出/OSD 叠框**（状态面板输出见 D018）；
板载摄像头采集（D015）。

### 1.3 创新点

1. **异构划分避开 7020 的真实瓶颈**：PL 只做像素级、规则、可流水化的预处理（18.8 ms/帧），
   PS 用 ncnn 跑 0.386 GMAC 的单类 person 模型（0.95–1.0 s/帧）。我们没有把“PL 必须有算法”
   理解成“PL 必须跑整网”——整网 PL 路线实测 9.63 s/帧，功能上可用、演示上不可接受。
2. **球不靠全图检测**：白块/圆度候选（召回 87.3 %）+ 32×32 小 CNN 分类器（C/NEON，8 ms/候选）
   + 轨迹确认（逐帧 80 %）。这是针对“球只有 4–12 px”的**结构性**方案，而不是调低 conf。
3. **坐标与事件带有效性闸门**：机位三态（VALID / LIMITED / INVALID）+ 单应 + 球场范围门限；
   未标定或无效机位**不输出坐标/事件**，避免“有数字但不可信”。
4. **可验证的工程资产**：PL 卷积引擎保留 6/6 逐值 bit-exact；AXI 带宽标定给出
   32-bit 读 222 MB/s、64-bit 读 444 MB/s、拷贝 444 MB/s，解释了旧引擎为何慢。

---

## 2. 设计原理与功能框图

### 2.1 系统功能框图

![系统功能框图](figures/system_arch.png)

> 图 1：系统功能框图。PL/PS 分界线、跨界线编号与接口契约见
> `docs/architecture.md` §2/§4；图中数字出处见本节各小节。

### 2.2 数据流

```text
PC（视频文件 / 推流，tools/stream_sender.py）
  │  以太网（D015）
  ▼
PS：接收 → 帧重组 → DDR 帧缓冲
  ▼
PL：track_preproc（D019；当前主线仍可用 preproc 单独出片）
  ├─ 3×320×320 RGB 平面（最近邻，II=1，18.8 ms/帧）→ PS ncnn
  └─ 320×320 白/运动掩码 → PS 跟踪运动门控（--motion-gate）
  ▼
PS：ncnn YOLOv8n-0.25 单类 person（0.95–1.0 s/帧）
  ▼
PS：SORT 跟踪（相位相关运动补偿 + 可选 PL 运动门控）→ 球员轨迹
  ▼
PS：球链路（白块/圆度候选 → C 分类器 8 ms/候选 → 轨迹确认）
  ▼
PS：单应 → 球场坐标 → 事件（出界/进球/球权）
  │  结果只序列化一次（D014）
  ├──► 以太网推给 PC（result_stream → pc_dashboard）
  ├──► SD 卡 JSONL + summary（result_store）
  └──► HDMI/LCD 状态面板（status_display；fbdev 或 PNG）
```

![软件流水线](figures/software_pipeline.png)

> 图 2：PS 侧软件流水线与数据依赖。检测每 `--every` 帧执行一次，跟踪每帧预测；
> 球链路可用 `--ball-every` 独立节奏。

### 2.3 PL 预处理

源码：`src/hls/preproc/src/preproc.cpp`；驱动：`tools/board_detect.py:init_pl`；
比特流：`board/overlay/preproc.bit`。

- **功能**：BGR888 输入 → RGB 三平面输出（channel-first，ncnn 布局），最近邻缩放。
- **接口**：`s_axilite` 的 `W_IN` / `H_IN`；`m_axi` 的 `gmem_in` / `gmem_out`；
  软件只写起始寄存器并轮询 done，不硬编码寄存器偏移（从 `.hwh` 解析）。
- **性能**：18.8 ms/帧；IP LUT 3,812（7 %）、DSP 1（会话 009）。
  BD 顶层（含 PS7/互连）LUT 4,736（8.90 %）、FF 5,260（4.94 %）、DSP 13（5.91 %）、
  BRAM 1.5（1.07 %）、WNS **+3.724 ns**（`build/reports/resource/preproc_bd_util.rpt`、
  `build/reports/timing/preproc_bd_timing.rpt`）。
- **为什么最近邻**：双线性版本功能正确、与 OpenCV 差 1 LSB，但 HLS 综合 LUT 175 %、
  DSP 904 % 且 30 分钟不收敛；最近邻每像素只读 3 字节、无行缓冲、无插值乘法，
  逻辑量小一到两个数量级（会话 009）。双线性源码保留在
  `src/hls/preproc/src/preproc_bilinear.cpp.bak`，作为后续升级入口。

### 2.4 PS 检测

- 模型：YOLOv8n-0.25，单类 `person`，输入 320×320，**0.386 GMAC/帧**；
  留出集 P 0.860 / R 0.666 / mAP50 0.739 / mAP50-95 0.379（会话 009）。
- 运行时：ncnn fp32 双线程 **0.95 s/帧**（单线程 1.74 s），与 PyTorch 输出差约 2 px。
- **为什么不在 PL 跑整网**：`conv_engine` 416/YOLOv5n 整网 9.63 s/帧（≈0.10 FPS）；
  字节访存版基线 20.8 s/帧，32-bit 字访存优化后 9.63 s（2.16×），仍远慢于 PS ncnn。
- **为什么不做 INT8**：板端实测只快 17 %（0.96→0.82 s），精度从 0.403 掉到 0.282；
  Cortex-A9 没有 int8 点积指令，量化收益不足以抵消精度损失（会话 009）。

### 2.5 跟踪与机位补偿

- 轻量 SORT（`tools/board_track.py`）：IoU 关联 + 距离兜底 + 最大丢失帧；
- 机位运动：相邻帧降采样到 320×180 做 `cv2.phaseCorrelate`，把位移按检测间隔归一；
- 实测：帧间位移中位 **0.2 px**、p95 **0.8 px**，全程 **34** 个 ID（会话 009）。
- 关键修正：跟踪速度必须除以检测间隔，否则 25 帧间隔会造成 4.5 px/帧漂移；
  距离兜底从“1.2×框高”收紧到 ≤40 px 且面积比 0.6–1.6，消除跨屏错配。

### 2.6 球链路

1. **候选**：白块 + 圆度 + 尺寸 + 场地约束，排除转播四角 overlay；召回 **87.3 %**
   （样本 181 正 / 2247 负，`data/results/ball_gate.json`）。
2. **分类**：32×32 输入，3 层 3×3 卷积 + 1×1 头；C/NEON 前向 **8 ms/候选**
   （numpy 为 0.6–1.2 s/候选）。留出集 th=0.7 时 **P 0.667 / R 0.848**。
3. **轨迹确认**：速度阈值从“按 1 s 步长”改为逐帧 3 px 后，20 个已知球中
   **16 个在 15 px 内确认（80 %）**；此前只有 5 %。
4. **为什么不用全图检测**：见 §1.1 的 4–12 px 实测；全图 ball mAP50 接近 0。

### 2.7 球场坐标与事件

- **标定**：人工点选 6 个已知球场点（`tools/label_field_points.py`），DLT 求单应；
  全场标定残差均值 **1.11 m**、最大 2.91 m；中圈 5 点片段残差 **0.11–0.56 m（均值 0.33 m）**。
- **有效性**：机位三态（VALID / LIMITED_FIELD_VIEW / INVALID）；377 帧实测
  300 帧 LIMITED、77 帧 INVALID（集中在 12–15 s 特写）。无效帧不输出球场坐标。
- **事件**：
  - 出界：球心越过边线/底线 **0.11 m**（FIFA 整球过线口径，可在 `tools/events.py` 调整），
    输出 `out_side`（left/right/top/bottom）；
  - 进球：球心越过球门线且在门框宽度内；
  - 球权：出界帧最近球员（`possession` / `possession_dist`）；
  - **角球/球门球**：需要“最后触球队伍 + 进攻方向”，当前只输出出界侧与最近球员，
    尚未做完整规则引擎（见 §8 第 3 条）。
- **板上结果**：202 帧运行中 **144 帧**输出球场坐标球（全部为已标定片段），
  **49 帧**给出球权，`events` 同步输出；该片段球未越线，`out_of_play=false` 与画面一致。

### 2.8 人员倒地（候选）

- 离线两级候选（strong/weak）：框高相对站立基线下降 + 宽高比上升；
  强判据从 5 帧改为 **4 帧**后，强层候选从 2 个增至 **4 个**：3 个已知真倒地进入，
  另 1 个（`inc_10_tid8279`）人工复核为**弯腰/俯身误判** ⇒ 强层精度 **3/4**；
  这是候选数与候选精度，不是检出率（`docs/player_tracking_v1.md` §7.1 / §7.3）。
- 人工复核 26/132 个候选：**3 个真倒地**（#1/#2/#22），其余含大量教练/裁判；
  候选池纯度约 **2.3 %**。⇒ 纯几何启发式只能做**粗筛**，不能作为独立判据。
- 当前定位：**候选输出 + 人工复核**；真正可用的倒地检测需要姿态/躯干方向信息
  （`yolov8n-pose` 肩-髋角度，且只对框高 ≥80 px 的目标启用）。报告不把它写成已解决的精度指标。

### 2.9 存储、显示与上位机（D018/D019）

**PL 跟踪辅助**（`src/hls/track/src/track_preproc.cpp`，D019）：把原 `preproc` 与
`mask_preproc` 合成一个 HLS 顶层，一次调用同时输出 3×320×320 RGB 平面和 320×320
白/运动掩码（bit0 白、bit1 运动）；4 个 `m_axi` 分别接 HP0–HP3，单 overlay 即可让
PS 同时拿到 ncnn 输入和跟踪用的运动信息，避免“两个 overlay 不能同时加载”的限制。
PS 侧 `--motion-gate` 用掩码过滤“无运动的新轨迹”，默认关闭，保留回退。
源码、BD Tcl、PYNQ 驱动已落盘；**HLS csynth + BD 实现已通过并出片**
（`board/overlay/track_preproc.bit`，BD LUT 7,374 / FF 8,340 / DSP 13 / BRAM 3，
WNS **+2.457 ns**、WHS +0.036 ns）；**上板逐位对已通过**（TRACK_PREPROC_BOARD_OK；720p 32.39 ms、管线 20.03 ms），
没有可用 bitstream 时自动回退 PS。

**SD 卡增量存储**（`tools/result_store.py`）：每帧/事件一行 JSONL，`summary.json` 记录
计数、文件列表、耗时；每 N 行 `fsync`，单文件超 `max_mb` 自动轮转；崩溃最多丢最后 N 行。
自检 `RESULT_STORE_SELFTEST OK`。

**PC 端软件**（`tools/pc_dashboard.py` + `tools/result_stream.py`）：板端按行 TCP 推送，
PC 端接收/回放，画 105×68 球场俯视图（球员 ID、球、事件），支持 CSV 导出和无显示
`--headless`。本地端到端 3 帧测试通过。

**HDMI 状态面板（D020，已出片）**：`src/rtl/io/hdmi/` 把 1bpp 帧缓存
（1280×720，28,800×32-bit，AXI BRAM 基址 `0x40000000`）读成 RGB，经 vendor
`video_driver` + `dvi_transmitter_top` 输出 TMDS；外部 50 MHz `sys_clk` 经 `clk_wiz`
产生 74.25/371.25 MHz。`tools/status_display.py --fb-bram 0x40000000` 负责把面板写进
BRAM（`tools/bram_fb.py`，PYNQ MMIO 或 `/dev/mem`）。实测出片 WNS 最差 Setup
**+3.938 ns**、Hold **+0.065 ns**；**帧缓存数字通路读回通过**（DISPLAY_BOARD_OK）；
但**上板无图像**（只亮背光、无内容）：该令牌只证明 BRAM 写得进、读得出，
**不等于画面能显示**（`ai/sessions/013`）。

**LCD 状态面板（D021，已出片）**：`src/rtl/io/lcd/` 用 1bpp 帧缓存
（800×480，12,000×32-bit，基址 `0x40000000`，64 KB）+ vendor `lcd_driver`
（`lcd_id=4384`）驱动 RGB LCD；外部 50 MHz 经 `clk_wiz` 出 33.333 MHz。实测
Setup **+4.283 ns**、Hold **+0.121 ns**；**帧缓存数字通路读回通过**（DISPLAY_BOARD_OK）；
但**上板全黑**：加载另一项目（`D:\FPGA\oi`）的 bit 后同一块 LCD 有画面 ⇒ LCD 硬件正常、
本仓库 LCD overlay 有 bug（`ai/sessions/013` 及后续对照）。
若镜像本身有 LCD fbdev，同一 `status_display.py` 也可直接写 `/dev/fbX` 作为回退。

**合并显示（D023）**：`display_both.bit` 内含两块独立 1bpp BRAM 与两个单端口
AXI BRAM Controller（HDMI `0x40000000`、LCD `0x40020000`），一次加载即可同时刷新
两块屏；`status_display.py --display-bit display_both.bit --also-lcd-base 0x40020000`
一次写双屏。上板 `DISPLAY_BOTH_BOARD_OK`（= 双 BRAM 读回一致），48 个 BRAM、Setup +3.874 ns；
**画面通过**（014：两块屏的 64×64 棋盘格人眼可见）。

**跟踪 + 双屏合并（D024）**：`track_display.bit` 在同一 overlay 里放
`track_preproc`（HP0–HP3）+ HDMI/LCD 双 BRAM（`0x40020000`/`0x40040000`），
板上可以一边跑 `--pl-track` 一边刷双屏，不需要换 overlay。资源 LUT **7,984** /
FF **8,835** / BRAM **51**；Setup **+2.918 ns**。上板
`TRACK_PREPROC_BOARD_OK` + `TRACK_DISPLAY_BOARD_OK`（前者是逐字节对拍，后者只是 BRAM 读回），
20 帧管线 `PIPELINE_OK`；**双屏画面未验证**（014 只修单屏 overlay，合并版尚未重出片）。

![LCD 状态面板示意图](figures/status_panel_lcd.png)

> 图 4：LCD 800×480 状态面板（合成样本；由 `tools/status_display.py` 渲染）。

![HDMI 状态面板示意图](figures/status_panel_hdmi.png)

> 图 5：HDMI 1280×720 状态面板（同一渲染器，合成样本）。

![PC 端球场视图示意图](figures/pc_pitch.png)

> 图 6：PC 端 105×68 球场视图（合成样本；由 `tools/pc_dashboard.py` 渲染）。

---

## 3. 软硬件划分依据与接口设计

### 3.1 划分依据

| 功能 | PS | PL | 依据（实测） |
|---|:---:|:---:|---|
| 视频接收 / 帧缓冲 | ✅ | | 协议栈、重传、文件/流解析是分支密集控制流；DDR3 只挂 PS |
| 预处理 BGR→320×320 RGB | 回退 | ✅ | 纯像素流、无分支、II=1；PL 18.8 ms/帧，IP LUT 3,812；`track_preproc` 另行出运动/白块掩码（D019） |
| person 检测 YOLOv8n-0.25 | ✅ | | 0.386 GMAC；PS ncnn 0.95–1.0 s/帧；PL 整网 9.63 s/帧 |
| SORT 跟踪 / 机位补偿 | ✅ | 运动掩码 | PS 做帧间关联/ID/异常分支；PL 出运动掩码，PS 可门控无运动新轨迹（D019） |
| 球候选 / 分类 / 轨迹 | ✅ | 白块掩码 | PL 出白块掩码；PS 做连通域/分类/轨迹（8 ms/候选） |
| 单应 / 球场坐标 / 事件 | ✅ | | 6 点标定 + 浮点几何 + 阈值/规则密集 |
| 结果输出（以太网/SD/显示） | ✅ | | 复用 Linux 网络栈/文件系统/fbdev；PL 不参与（D014/D018） |
| 触屏录入球衣颜色/半场 | ✅ | | 人机界面，事件驱动（D013） |

**原则**：PL 只做“像素级、规则、可流水化”的部分；分支密集或访存不规则的部分留 PS。
这样既满足演示约束“PL 上必须有实际算法”，又避开 PL 卷积引擎“算得动但喂不快”的瓶颈。

### 3.2 接口设计（`preproc` 主线）

| # | 接口 | 方向 | 位宽 / 格式 | 时钟域 | 握手/背压 | 错误处理 |
|---|---|---|---|---|---|---|
| 1 | `s_axi_control.W_IN` / `H_IN` | PS→PL | 32-bit 无符号 | AXI-Lite / PL FCLK 同域 | AXI-Lite 写 | 软件拒绝非正数 |
| 2 | `AP_CTRL`（0x00） | PS↔PL | bit0 start / bit1 done | 同上 | 软件轮询 bit1 | 调用方超时 |
| 3 | `gmem_in`（BGR888） | PS DDR→PL | 8-bit × 1280×720×3 | AXI HP | `m_axi` 突发，16 outstanding | 由 `W_IN/H_IN` 约束 |
| 4 | `gmem_out`（RGB 平面） | PL→PS DDR | 8-bit × 3×320×320，channel-first | AXI HP | `m_axi` 突发，16 outstanding | 固定 307,200 B |

`track_preproc`（D019）的 4 个数据口：`gmem_in`→HP0、`gmem_rgb`→HP1、`gmem_prev`→HP2、
`gmem_mask`→HP3；`s_axi_control` 复用同一套 `W_IN/H_IN` + 阈值寄存器。

寄存器偏移**不硬编码**：`tools/board_detect.py:init_pl` 从 `.hwh` 的
`ADDRESSBLOCK/REGISTER` 解析 `s_axi_control` / `s_axi_control_r` 窗口，兼容 HLS 把
`in/out` 改名为 `in_r/out_r` 的情况。`conv_engine` 的 5 个 `m_axi` + 1 个 `s_axilite`
契约保留在 `docs/accel_interface.md`，但已退出主线（D017）。

### 3.3 为什么保留 `conv_engine`

它不是“没做出来”，而是“做出来并验证过，但整网性能不合格”：

- 单层逐值 **6/6 bit-exact**（`board/logs/board_jtag_layer_test_20261020.md`）；
- 32-bit 字访存版整网 **9.63 s/帧**，比字节访存基线 20.8 s 快 **2.16×**；
- 资源：LUT 23,552（44.27 %）、FF 21,991（20.67 %）、DSP 172（78.18 %）、
  BRAM 12.5（8.93 %）、WNS **+1.350 ns @83.33 MHz**；
- AXI 标定：32-bit 读 222.18 MB/s、64-bit 读 444.26 MB/s、32-bit 写 222.18 MB/s、
  拷贝 444.36 MB/s（100 MHz，`board/logs/board_axi_bw_20261022.md`）。
- 结论：在 7020 上它占掉 78 % DSP 却只给 0.10 FPS；因此只作为技术验证与回归资产保留。

---

## 4. 优化过程（含优化前后性能与资源对比）

### 4.1 PL 卷积引擎：字节访存 → 32-bit 字访存

| 版本 | 资源 LUT / FF / DSP / BRAM | WNS | 整网耗时 | conv 耗时 | 出处 |
|---|---|---:|---:|---:|---|
| 基线：字节访存（005 收敛版） | **13,076 / 未记录 / 162 / 9.5** | **+1.254 ns** | **20.8 s/帧** | ~18.5 s | `ai/sessions/005_engine_load_weight_pipeline.md`（§参考文档出处）、`board/logs/board_stream_new_20261022.md`（旧版本列） |
| 优化：32-bit 字访存（008） | **23,552 / 21,991 / 172 / 12.5** | **+1.350 ns @83.33 MHz** | **9.63 s/帧** | **8.13 s** | `build/reports/resource/conv_engine_bd_util.rpt`、`build/reports/timing/conv_engine_bd_timing.rpt`、`board/logs/board_stream_new_20261022.md` |

> 资源代价如实记录：32-bit 字访存把 LUT 从 13,076 增到 23,552、DSP 从 162 增到 172，
> 换来整网 20.8→9.63 s；这是“用资源换事务效率”，不是免费加速。

- 其他耗时（PS 首层/非卷积/后处理）从 ~2.3 s 降到 **1.45 s**；
- 单层验收 `BOARD_LAYER_OK all_6_cases`，聚合 **0.1156 GOP/s**（旧 0.0846）；
- 关键认知：瓶颈不是总字节数，而是**每次字节访存的固定开销**；32-bit 打包后 conv 提升 2.27×。

### 4.2 AXI 带宽标定

| 模式 | 改造前 | 改造后 | 提升 | 出处 |
|---|---:|---:|---:|---|
| 32-bit 读 | 10–22 MB/s（旧 `conv_engine` 实测区间） | **222.18 MB/s** | 10–20× | `board/logs/board_axi_bw_20261022.md` |
| 64-bit 读 | — | **444.26 MB/s** | — | 同上 |
| 32-bit 写 | — | **222.18 MB/s** | — | 同上 |
| 32-bit 拷贝（读+写并发） | — | **444.36 MB/s** | — | 同上 |

> 32-bit 口理论峰值 400 MB/s，实测 222 MB/s = **55.5 %**；64-bit 口同样 55.5 %。
> 限制因素是 outstanding 数量与 DDR 往返延迟，不是端口位宽。

### 4.3 检测路线：PL 整网 → PS ncnn

| 方案 | 模型/输入 | 耗时 | 资源占用 | 结论 | 出处 |
|---|---|---:|---|---|---|
| PL `conv_engine` 整网 | YOLOv5n @416 | **9.63 s/帧（≈0.10 FPS）** | LUT 23,552 / DSP 172（78 %） | 功能可跑，演示不可接受 | `board/logs/board_stream_new_20261022.md` |
| PS ncnn | YOLOv8n-0.25 @320，0.386 GMAC | **0.95–1.0 s/帧** | 不占 PL DSP | **主线** | `ai/sessions/009_ps_pipeline_pl_preproc.md` |

- 模型算力从 YOLOv5n@416 的约 0.944 GMAC 降到 **0.386 GMAC**（约 41 %）；
- 精度：P 0.860 / R 0.666 / mAP50 0.739 / mAP50-95 0.379（留出集，会话 009）。

![检测路线单帧耗时对比](figures/optimization.png)

> 图 3：检测路线单帧耗时对比（对数坐标）。出处：`board/logs/board_stream_new_20261022.md`、
> `ai/sessions/009_ps_pipeline_pl_preproc.md`。

### 4.4 球分类器：numpy → C/NEON

| 版本 | 单候选耗时 | 10 候选（含启动） | 备注 | 出处 |
|---|---:|---:|---|---|
| numpy 前向 | 0.6–1.2 s | 6–12 s | 板上 ncnn 转换 SIGFPE，改 numpy 兜底 | 会话 009 |
| **C/NEON 前向** | **~8 ms** | **0.19 s** | 与 numpy 逐值一致；管线 28.1 s→24.4 s | 会话 009 |

- 优化手段：`tools/export_ball_c.py` 把 BN 折进卷积，生成 `build/ball_weights.h`；
  `tools/ball_cls_c.c` 纯 C 前向（conv3x3+ReLU+maxpool×2 + avgpool8 + 1×1 conv），
  板上 `gcc -O3 -mcpu=cortex-a9 -mfpu=neon` 编译；
- 踩坑：静态数组 `t0f[16*16*16]` 装不下 16×32×32 输出，越界覆盖相邻缓冲导致正样本分数偏低；
  分类器块内 `idx` 覆盖帧计数器导致 `TypeError`。

### 4.5 预处理：双线性 → 最近邻

| 版本 | 资源 | 综合结果 | 耗时 | 结论 |
|---|---|---|---:|---|
| 双线性 | LUT **175 %**、DSP **904 %** | 30 分钟不收敛 | — | 功能正确但不可实现 |
| **最近邻** | IP LUT **3,812（7 %）**、DSP 1 | 通过，WNS **+3.724 ns** | **18.8 ms/帧** | **主线** |

### 4.6 端到端结果

| 指标 | 结果 | 出处 |
|---|---|---|
| 球员检测 | 0.95–1.0 s/帧，与 PyTorch 差约 2 px | 会话 009 |
| 跟踪 | 帧间位移中位 0.2 px、p95 0.8 px，34 个 ID | 会话 009 |
| 球场坐标 | 6 点标定残差均值 1.11 m；中圈 0.11–0.56 m（均值 0.33 m） | 会话 009、`docs/system_status.md` |
| 球候选召回 | 87.3 % | 会话 009 |
| 球分类器 | P 0.667 / R 0.848（th=0.7） | 会话 009 |
| 球链路确认 | 80 %（16/20，15 px 容差） | 会话 009 |
| 板上球输出 | 144/202 帧球场坐标，49 帧球权，`events` 同步 | `board/logs/board_pipeline_20261024.md` |
| 机位有效性 | 377 帧：300 LIMITED / 77 INVALID | 会话 009 |
| 板子稳定性 | 20 分钟双核满载：164 轮、约 1148 次推理、无复位 | 会话 009 |

### 4.7 扩展功能验证（D018/D019）

| 功能 | 判据/测试 | 结果 |
|---|---|---|
| SD 增量存储 | `python tools/result_store.py --selftest` | `RESULT_STORE_SELFTEST OK`（6 行、summary 计数正确） |
| PC Dashboard | `python tools/pc_dashboard.py --selftest` + 本地回放 | `PC_DASHBOARD_SELFTEST OK`；3 帧回放 `PC_DASHBOARD_OK frames=3` |
| 板→PC 推送 | 本地 TCP 回环 | `RESULT_STREAM_OK sent=2`；PC 端收到 2 帧 |
| HDMI/LCD 面板（软件） | `python tools/status_display.py --selftest` | `STATUS_DISPLAY_SELFTEST OK`，PNG 800×480 生成 |
| HDMI 面板（PL overlay） | `bd_hdmi_status.tcl` + `tools/bram_fb.py --selftest` | **已出片**：Setup +3.938 ns / Hold +0.065 ns；`BRAM_FB_SELFTEST OK`；**数字通路读回通过**（DISPLAY_BOARD_OK）；**画面已通过**（014） |
| LCD 面板（PL overlay） | `bd_lcd_status.tcl` + `tools/bram_fb.py --selftest` | **已出片**：Setup +4.283 ns / Hold +0.121 ns；**数字通路读回通过**（DISPLAY_BOARD_OK）；**画面已通过**（014） |
| HDMI+LCD 合并 | `display_both.bit` + `--also-lcd-base 0x40020000` | **已出片**：LUT 908 / BRAM 48，Setup +3.874 ns；`DISPLAY_BOTH_BOARD_OK`（双屏读回一致，仅数字通路）；**画面待重出片**（014 只修单屏 overlay） |
| PL 跟踪辅助（PC 算法） | `python tools/check_track_preproc.py` | `TRACK_PREPROC_REF OK`（RGB/MASK 逐字节一致） |
| PL 跟踪辅助（HLS/BD） | `export_track.py` → `bd_track_preproc.tcl` | **已出片**：LUT 7,374 / FF 8,340 / DSP 13 / BRAM 3；WNS **+2.457 ns**、WHS +0.036 ns、PW +4.870 ns |
| PL 跟踪辅助（上板） | `tools/check_track_preproc_board.py` | **`TRACK_PREPROC_BOARD_OK`**：RGB/掩码逐字节一致；720p **32.39 ms/帧** |
| 板上管线 + PL 门控 | `board_pipeline.py --pl-track --motion-gate`，20 帧 match 片段 | `PIPELINE_OK`；4 次检测、中位 992.95 ms；proposal 3→2 名球员；`pl_ms` 20.03 ms/帧 |
| SD 存储（板上） | 同一次运行 `--store-dir` | 20 行 JSONL + `summary.json`（`counts.frame=20`） |
| 板→PC 推送 | `result_stream.py` → `pc_dashboard.py --headless` | `RESULT_STREAM_OK sent=20`、`PC_DASHBOARD_OK frames=20` |

> 扩展功能的共同原则：**软件先可用、硬件再出片**。SD/PC/面板不依赖新 bitstream，
> 现在就能跑；`track_preproc` 必须经过 HLS csynth → BD 实现 → 上板逐位对，才允许在
> 报告里写“PL 已验证”。没有新 bitstream 时 `--pl-track` 自动回退 PS 路径。

---

## 5. 大模型协作记录

完整的提示词、模型回答、自我纠错轨迹与智能体工作流见
[`llm_logs/`](llm_logs/)；原始过程记录见 [`../ai/`](../ai/)。

### 5.1 协作方式

- **执行环境**：Kilo CLI（Windows / PowerShell），主模型 `magpie/group/1101`；
- **分工**：Lead 负责拆任务、划定写范围、收口与验收；子代理负责单点调查/实现，
  每个任务有明确“只写这些文件”的边界，避免并发改同一文件；
- **证据纪律**：每个数字必须能指回 `build/reports/`、`board/logs/`、`data/results/`
  或 `ai/sessions/`；写不出出处的数字不进入报告；
- **原始层 vs 成品层**：`ai/` 保留逐条会话原文；`report/llm_logs/` 是从中挑出、整理成篇的提交件，
  每条都能回指原始出处。

### 5.2 自我纠错轨迹（摘录）

| # | 错误假设 | 实测/纠正 | 影响 |
|---|---|---|---|
| 1 | “PL 通用卷积引擎能跑整网，只要优化访存” | 32-bit 字访存把整网从 20.8 s 降到 9.63 s，但仍是 0.10 FPS；PS ncnn 0.95 s | 路线改为 D017（PL 预处理 + PS ncnn） |
| 2 | “跟踪位移按 25 帧计算，每帧都能累加” | 位移未除以检测间隔，框漂移 4.5 px/帧；归一后 0.2 px | 跟踪器修正 |
| 3 | “球的静止阈值按 1 秒一步设计” | 逐帧跟踪时球每帧只移 5–15 px，全被过滤；改 3 px 后确认率 5 %→80 % | 球链路可用 |
| 4 | “双线性预处理可以直接上板” | HLS LUT 175 %、DSP 904 %、30 分钟不收敛；改最近邻后 LUT 7 % | PL 预处理落地 |
| 5 | “板端 TFLite / OpenCV DNN 可直接跑 YOLOv8” | 工程记录：TFLite runtime 崩溃、`cv2.dnn` 对 opset 11/12 报同一断言（完整日志未保留） | 改 ncnn |
| 6 | “球候选多了，补轨迹就能提高确认率” | 两次 backfill 试验把 144 帧回退到极少帧、球权归零；根因是球路径每 2 帧一次，5 次丢失只覆盖 0.4 s（`ai/sessions/010`） | 已回滚稳定版，结构改动待做（不是已解决） |
| 7 | “纯 CV 颜色/形状/帧差能替代球检测” | 三级过滤后确认的仍是场边静态白块；候选召回高但单帧判别不足 | 改为“候选 + 小 CNN + 轨迹” |

> 第 6 条是**未完成项**，如实记录：当前稳定版是 144/202 帧；backfill 结构改动
> （`--ball-every 1`、按秒计丢失窗口、预测位置绕过标定边界过滤）尚未落地。
> 报告不把“计划中的优化”写成“已完成的结果”。

### 5.3 智能体工作流设计

1. **调查任务**：只读检索/复现，产出带出处的调研文档（如 `docs/yolo_on_zynq7020_survey.md`）；
2. **实现任务**：Lead 先冻结接口/写范围，子代理只改指定文件；改完必须跑对应验收脚本；
3. **验收任务**：独立复核数字与链接（如 `tools/check_repo.py`、`tools/check_ball_events.py`）；
4. **沉淀任务**：把失败与修正写进 `ai/sessions/`，再提炼为 `skill/` 技能包与 `report/llm_logs/` 成品。

---

## 6. 技能包的提炼过程

技能包本体在 [`../skill/`](../skill/)，每个包含“适用范围 / 使用方法 / 失效条件 /
提炼来源 / 已验证效果”五节。当前提交两个包：

| 技能包 | 形态 | 提炼来源（真实失败/观察） | 已验证效果 |
|---|---|---|---|
| [`pynq_overlay_bringup`](../skill/pynq_overlay_bringup/README.md) | 通用 PYNQ（额外加分） | BD 漏接 `s_axi_control_r` 导致 `KeyError`；HLS 把 `in/out` 改名 `in_r/out_r`；DMA 缓存一致性；板子偶发整机复位 | 同一套脚本在 `axi_bw`、`preproc` 两块 overlay 上各跑通一次；20 分钟满载无复位 |
| [`hls_axi_stream_accel`](../skill/hls_axi_stream_accel/README.md) | HLS/AXI 加速器（通用） | 字节事务 10–22 MB/s；32-bit 字访存后 222 MB/s；outstanding/DDR 往返是上限；逐层 bit-exact 必须做 | 单层 6/6 bit-exact；AXI 四模式标定 `AXI_BW_OK`；conv 20.8 s→9.63 s |

**提炼方法**：

1. 先有 `ai/sessions/` 里的真实失败（不是“最佳实践”想象）；
2. 把失败拆成“触发条件 → 可观察现象 → 定位步骤 → 修复/规避”；
3. 用**能失败**的判据验证：`BOARD_LAYER_OK all_6_cases`、`AXI_BW_OK`、`CHECK_REPO_OK`、
   球链路 16/20；判据不通过就不算技能包有效；
4. 写清失效条件（工具版本、器件、与本队题目绑定的部分），换题目/换板卡是否仍可用。

**通用 PYNQ 加分口径**：`pynq_overlay_bringup` 不依赖足球题目——换任何 PYNQ overlay
仍可用；`hls_axi_stream_accel` 也不绑定本题模型，换加速器仍可用。

---

## 7. 复现说明

### 7.1 前置条件

| 依赖 | 版本/说明 |
|---|---|
| Vivado / Vitis / Vitis_HLS | **2020.2**（本机唯一安装；脚本参数化，不硬编码绝对路径） |
| Python | 3.8+；仓库自检只用标准库 |
| 开发板 | 正点原子 领航者 ZYNQ 7020（底板 V3.7，核心板 ZYNQ_CORE_2V5） |
| PYNQ | SD 启动 PYNQ 2.7（Ubuntu 20.04 / kernel 5.4.0） |
| 网络 | 板端 `eth0` 默认 `192.168.2.99`（PC 同网段） |
| 运行必需件 | `board/overlay/*.bit/.hwh`、`build/models/person_v8n25.{param,bin}`、`build/models/ball_roi.npz`、`data/calib/*.json` |

> `*.bin`、`*.mp4` 等大文件默认不入 git；提交包由 `tools/make_submission.py` 按
> `SUBMISSION_MANIFEST.csv`（提交包根目录）显式带入，或用 `data/download/` 的脚本 + SHA256 重建。

### 7.2 从零复现（PC 侧）

```powershell
git clone https://github.com/110-lii/zynq-yolo zynq-yolo
cd zynq-yolo

# 1) 仓库自检：末行必须是 CHECK_REPO_OK
python tools\check_repo.py

# 2) 构建比特流（按需；脚本入口在 build/scripts/）
#    preproc: build/scripts/bd_preproc.tcl
#    conv_engine（历史验证）: build/scripts/bd_conv_engine.tcl
#    axi_bw（带宽标定）: build/scripts/bd_axi_bw.tcl
vivado -mode batch -source build/scripts/bd_preproc.tcl

# 3) 生成提交包（含运行必需件 + 清单 + SHA256）
python tools\make_submission.py --out dist
```

### 7.3 板上运行（完整管线）

```bash
# 把仓库与运行必需件推到板端（PC 侧）
powershell -File board/scripts/deploy_to_board.ps1

# 板端：完整管线（PL 跟踪辅助 + 检测 + 跟踪 + 球 + 事件 + SD 存储）
python3 tools/board_pipeline.py --video <clips.mp4> --every 25 \
  --max-det 0 --ball-every 2 --pl-track --motion-gate \
  --calib data/calib/field_calib.json --proposal \
  --store-dir /home/xilinx/zynq-yolo --out /home/xilinx/out.json

# 板端：结果推 PC（另开窗口）
python3 tools/result_stream.py --file /home/xilinx/zynq-yolo/run_*.jsonl \
  --host <PC_IP> --port 5005 --follow

# 板端：HDMI PL overlay（D020）：加载 bit 后写 1bpp BRAM（基址 0x40000000）
python3 tools/status_display.py --replay /home/xilinx/out.json --size 1280x720 \
  --display-bit board/overlay/hdmi_status.bit
# 板端：LCD PL overlay（D021）
python3 tools/status_display.py --replay /home/xilinx/out.json --size 800x480 \
  --display-bit board/overlay/lcd_status.bit
# 板端：HDMI+LCD 合并（D023），一次写双屏
python3 tools/status_display.py --replay /home/xilinx/out.json --size 1280x720 \
  --display-bit board/overlay/display_both.bit --also-lcd-base 0x40020000
# 回退：镜像本身有 fbdev 时
python3 tools/status_display.py --replay /home/xilinx/out.json --size 800x480 --fb /dev/fb1
```

PC 端：

```powershell
python tools\pc_dashboard.py --listen 0.0.0.0:5005 --csv session.csv
python tools\pc_dashboard.py --replay data\results\run_*.jsonl --speed 1
```

输出每帧：`players[]`（id / 球场 x,y / conf）、`ball`（x,y / valid / space）、
`camera_state`、`events`（出界/进球/球权）；开启 `--pl-track` 时另有 `pl_ms`。

### 7.4 验收判据

| 项 | 判据 | 期望 |
|---|---|---|
| 仓库 | `python tools/check_repo.py` | 末行 `CHECK_REPO_OK` |
| PL 卷积引擎（历史） | `board/scripts/jtag_layer_test.tcl` | `BOARD_LAYER_OK all_6_cases` |
| AXI 带宽 | `board/scripts/run_axi_bw.sh` | `AXI_BW_OK` 四模式 |
| 板上球输出 | `tools/board_pipeline.py` + `data/calib/field_calib.json`（汇总见 `board/logs/board_pipeline_20261024.md`） | 144/202 帧球场坐标；49 帧球权；`events` 非空 |
| 球链路（已知球评估） | `ai/sessions/009` 的 20 个已知球、15 px 容差 | 16/20 确认（80 %） |
| HDMI 状态面板 | `board/overlay/hdmi_status.bit` + `status_display.py --fb-bram 0x40000000` | 帧缓存读回已通过（`DISPLAY_BOARD_OK`）；**屏幕画面已通过**（014：HDMI/LCD 棋盘格均可见）；显示类判据仍应分 BRAM_OK／VISUAL_OK 两级 |
| SD 存储 | `python tools/result_store.py --selftest` | `RESULT_STORE_SELFTEST OK` |
| PC 软件 | `python tools/pc_dashboard.py --selftest` + 回放 | `PC_DASHBOARD_SELFTEST OK` |
| HDMI/LCD 面板 | `python tools/status_display.py --selftest` | `STATUS_DISPLAY_SELFTEST OK` |
| PL 跟踪辅助（已出片） | `src/hls/track/scripts/export_track.py` → `bd_track_preproc.tcl` | `IP_EXPORT_OK` + `write_bitstream completed successfully` |
| PL 跟踪辅助（上板） | `tools/check_track_preproc_board.py` | **`TRACK_PREPROC_BOARD_OK`**：RGB/掩码逐字节一致；720p 32.39 ms/帧 |

> `tools/check_ball_events.py` / `check_ball_field.py` 是 PC 侧旧片段的**抽查脚本**，
> 不是最终验收：它们的输出随片段/阈值变化（本次抽查 56/202 帧在场），不能替代上表
> 的板上运行口径。报告只把“板上 144/202、49 帧球权”作为交付指标，原始 `/tmp/out.json`
> 未随包归档（已知缺口，见 §8 第 2 条）。

### 7.5 目录与产物

| 路径 | 内容 |
|---|---|
| `src/hls/`、`src/ps/` | HLS 源码（含 `track_preproc`）、PYNQ 驱动、板上运行脚本 |
| `build/scripts/` | 可复现构建 Tcl（BD/综合/实现，含 `bd_track_preproc.tcl`、`bd_hdmi_status.tcl`、`bd_lcd_status.tcl`） |
| `src/rtl/io/hdmi/`、`src/rtl/io/lcd/` | HDMI TMDS / RGB LCD 状态面板 RTL（D020/D021） |
| `tools/result_store.py`、`result_stream.py`、`pc_dashboard.py`、`status_display.py`、`bram_fb.py` | SD/PC/显示扩展（D018） |
| `build/reports/` | 综合/实现资源与时间报告（只留最新 + baseline） |
| `board/overlay/`、`board/logs/` | 比特流/硬件描述、实测日志 |
| `data/results/` | 指标 JSON（**数字来源**） |
| `report/` | 本报告、图、协作记录 |
| `skill/` | 技能包 |
| `ai/` | 原始协作记录 |

### 7.6 工具链版本差异

指南推荐 Vivado/Vitis 2026.1 或 2025.2；本机只有 **2020.2**。按指南 3.3.3.1
“使用其他版本须在报告中说明并保证脚本可复现”：

- 所有构建入口在 `build/scripts/`，工具链路径由变量注入，不硬编码 `D:\Xilinx\...`；
- 版本差异、IP 版本与复现注意事项登记在本节与 `README.md` §9；
- 2020.2 已覆盖 Zynq-7020 器件与 PYNQ 2.7 镜像兼容性；升级 2025.2 需要重新验证
  BD/IP 迁移与 PYNQ 镜像，不在本次提交范围内。

---

## 8. 已知边界与未完成项

1. **单应只对已标定机位有效**：未标定或 INVALID 机位不输出球场坐标/事件；
   多机位需要每个机位各标一次，或后续做镜头切换检测 + 标定切换。
2. **球在快速移动/遮挡时确认率下降**：约 28–70 % 帧有球；`--ball-every 2` 下
   5 次丢失只覆盖约 0.4 s，backfill 结构改动尚未落地（见 §5.2 第 6 条）。
   板上 202 帧运行的原始 `/tmp/out.json` **未随包归档**，指标汇总在
   `docs/system_status.md` 与 `ai/sessions/010`；这是证据链的已知缺口。
3. **出界口径**默认“球心越线 0.11 m”，可在 `tools/events.py` 调整；
   角球/球门球需要“最后触球队伍 + 进攻方向”，当前只输出出界侧与最近球员，
   **尚未做完整规则引擎**；不把“出界位置”冒充“角球/球门球判定”。
4. **倒地识别是候选粗筛**：132 个候选中人工复核 26 个、3 个真倒地；抽样不是随机的，
   候选池纯度约 2.3 % 不可外推。4 帧门槛后强层 4 个候选 = 3 真 + 1 假，**精度 3/4**；
   不宣称召回，也不把候选数当检出数。要产品化需姿态模型 + 更大规模复核。
5. **帧率不是本项目的首要指标**：功能优先（D017）；当前 PS 检测约 1 FPS，
   低帧率可接受。若要求实时，需要换更小模型/多线程/NEON 定点，属于后续工作。
6. **工具链是 2020.2**：与指南推荐版本不同，已按指南要求给出参数化构建与差异说明。
7. **PL 跟踪辅助已上板逐位对**：`TRACK_PREPROC_BOARD_OK`，RGB/掩码逐字节一致；
   1280×720 实测 **32.39 ms/帧**，640×360 管线内 **20.03 ms/帧**（比旧 preproc 18.8 ms 慢，因为多读 prev、多写掩码）。
   这是实测代价，不是免费加速。
8. **HDMI/LCD 画面已通过（014）**：原先读回恒为 0x8 的根因是 AXI BRAM
   Controller 双端口模式 **Port A=写、Port B=读**，原 BD 只接 Port A；改
   `C_SINGLE_PORT_BRAM=1` 后 AXI 用 Port A 读写、显示用 Port B，HDMI/LCD 均
   **`DISPLAY_BOARD_OK`**（28,800 / 12,000 字读回一致）。**该令牌只证明帧缓存数字通路，
   不等于画面能显示**：上板 HDMI 只亮背光、LCD 全黑；已排除 AXI/BRAM、TMDS 四引脚
   （对 AMD 官方 pinout）、外部 50 MHz 晶振、时序收敛；另用 `D:\FPGA\oi` 的 bit 对照，
   同一块 LCD 有画面而 HDMI 仍无 ⇒ LCD 硬件好、本仓库 LCD overlay 有 bug，HDMI 疑物理层
   故障，**两者均未修复**。现场记录见
   [`../board/logs/board_display_20261025.md`](../board/logs/board_display_20261025.md)。
9. **SD 路径依赖挂载**：`--store-dir` 默认示例 `/home/xilinx/zynq-yolo`；若 SD 卡挂载点不同，
   由启动脚本传入；存储失败会打印 `STORE_UNAVAILABLE` 并继续跑（不静默丢结果）。

---

## 9. 交付物对照（指南 3.3.5）

| 指南要求 | 本仓库落点 | 状态 |
|---|---|---|
| 3.3.5.1 工程包：源码、仿真/验证、综合实现报告、上板工程与实测、可复现构建脚本 | `src/`、`sim/`、`build/`、`board/`、`data/` | ✅ |
| 3.3.5.2 技能包：适用范围/使用方法/失效条件 | `skill/pynq_overlay_bringup/`、`skill/hls_axi_stream_accel/` | ✅ |
| 3.3.5.3 设计报告 7 项 | 本文件 §1–§7 | ✅ |
| 3.3.5.3 大模型协作记录：提示词/回答/自我纠错/工作流 | `report/llm_logs/` ← `ai/` | ✅ |
| 3.3.5.4 推荐目录结构 | 根 `README.md` §4；`tools/check_repo.py` | ✅ |
| 3.3.5.5 评测权重 | `report/README.md` 预置表 2 | ✅ |
| D018 扩展：SD/HDMI-LCD/PC | `tools/result_store.py`、`tools/status_display.py`、`tools/pc_dashboard.py`、`tools/result_stream.py` | ✅ 软件自检通过 |
| D019 扩展：PL 跟踪辅助 | `src/hls/track/`、`build/scripts/bd_track_preproc.tcl`、`board/overlay/track_preproc.bit`、`tools/track_preproc.py` | ✅ 已出片并上板逐位对通过（720p 32.39 ms） |
| D020 扩展：HDMI 状态面板 | `src/rtl/io/hdmi/`、`build/scripts/bd_hdmi_status.tcl`、`board/overlay/hdmi_status.bit`、`tools/bram_fb.py` | ✅ 已出片（Setup +3.938 ns）；✅ 数字通路读回通过；✅ 画面已通过（014） |
| D021 扩展：LCD 状态面板 | `src/rtl/io/lcd/`、`build/scripts/bd_lcd_status.tcl`、`board/overlay/lcd_status.bit` | ✅ 已出片（Setup +4.283 ns）；✅ 数字通路读回通过；✅ 画面已通过（014） |
| D022 修复：BRAM 单端口 | `bd_hdmi_status.tcl`/`bd_lcd_status.tcl` 的 `SINGLE_PORT_BRAM=1` | ✅ HDMI/LCD 均 `DISPLAY_BOARD_OK`（数字通路）；⚠️ 合并版待重出片（014 只修单屏） |

---

## 10. 结论

本项目在 Zynq-7020 上把“足球转播视频的可观测目标与事件分析”做成了**可上板、可复现**的分析链路：PL 预处理 18.8 ms/帧、PS ncnn 检测 0.95–1.0 s/帧、跟踪 0.2 px、
球链路 80 % 确认（16/20，PC 侧已知球评估）、144/202 帧球场坐标与 49 帧球权输出；
显示面板已在 014 打通（HDMI/LCD 棋盘格人眼可见）；
按 D022 口径只有帧缓存数字通路通过，画面仍需人眼判 VISUAL。倒地只到候选层（强层精度 3/4），
球的 backfill 结构改动未落地。
我们没有把资源花在“PL 跑整网”
这条实测不可行的路上，而是用异构划分、ROI 球链路和带有效性闸门的几何/事件，
在 220 DSP 的器件上交付了功能闭环；同时保留 6/6 bit-exact 的卷积引擎与完整 AXI 标定，
作为可复用的技术资产和后续优化基线。
