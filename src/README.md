# src/ —— 设计源码（RTL / HLS / PS 侧软件）

> 上级：[`../README.md`](../README.md)
> 唯一事实源：[`../docs/repo_spec.md`](../docs/repo_spec.md) §2.1 / §2.2

| | |
|---|---|
| 本目录装什么 | 本作品全部**设计真源**：手写 Verilog、Vitis HLS 加速器 C/C++、PS 侧 Python（PYNQ 驱动与 host 应用） |
| 谁产生 | 手写 |
| 进 git | 是（设计真源，只能在这里改设计） |

## 内容

### 子树（与磁盘实际结构一致）

```text
src/
├── rtl/                        # 手写 Verilog，会被综合进最终作品
│   ├── common/                 # 时钟、复位、CDC、FIFO 等与题目无关的基础设施
│   ├── video/                  # 视频输入与预处理：格式转换、缩放（主线不用板载摄像头，D015）
│   ├── accel/                  # 加速器数据通路：DMA 搬运、流控、层间接口
│   ├── io/                     # 面向外设的接口：AXI-Lite 寄存器、UART、以太网、LCD
│   └── ip/                     # 需要保留的 IP 配置引脚/约束（.xci 只读，勿手改）
├── hls/
│   └── accel/
│       ├── src/                # HLS 顶层与算子实现（.cpp）
│       ├── include/            # HLS 头文件与定点类型定义（.h / .hpp）
│       ├── tb/                 # HLS C 仿真 testbench
│       └── scripts/            # csynth.tcl / cosim.tcl / export.tcl 等 HLS 构建脚本
└── ps/
    ├── pynq_driver/            # PYNQ Overlay 封装类、寄存器映射、DMA 搬运与缓存一致性处理
    └── host/                   # 上位机侧脚本（推流、评测、可视化）
```

### 目录 ↔ 职责 ↔ 前缀 一一对应（§2.1 / §2.2）

RTL 模块名以**子系统前缀**开头，前缀与目录一一对应，便于按名索骥：

| 目录 | 职责 | 模块前缀 |
|---|---|---|
| `rtl/common/` | 时钟 / 复位 / CDC / FIFO | `inf_` |
| `rtl/video/` | 视频输入与预处理（DVP 模块为历史资产，D015） | `vid_` |
| `rtl/accel/` | 加速器数据通路 | `acc_` |
| `rtl/io/` | 外设接口（AXI-Lite / 以太网 / LCD） | `io_` |
| `hls/accel/` | HLS 导出的 IP 顶层命名 | `hls_` |
| `rtl/ip/` | IP 配置引脚/约束（`.xci`） | 不适用（IP 自带命名） |
| `ps/pynq_driver/`、`ps/host/` | PS 侧 Python | 不适用（Python 命名） |

> ⚠️ **当前磁盘事实（2026-10-20 更新）**：`src/` 下已有**设计真源**落地 ——
> - `src/hls/accel/`：`conv_engine.cpp`（通用 INT8 卷积引擎，契约 [`../docs/accel_interface.md`](../docs/accel_interface.md)）
>   + `acc_conv3x3.cpp`（早期探针）+ `tb/tb_conv_engine.cpp` + 7 个构建/导出脚本；
>   已出片 `board/overlay/conv_engine.bit`（83.33 MHz 时序收敛，见该契约 §7.4）。
> - `src/ps/`：`pynq_driver/conv_engine.py`（PYNQ 驱动，**未上板**）+ `host/run_layer_test.py`（板上验收脚本，**未上板**）。
> - `src/rtl/`：已有 `video/vid_dvp_capture.v`、`video/vid_test_pattern.v`、`common/inf_axis_async_fifo.v`。
>   **这三者是摄像头采集链的历史资产，已随 D015 退出主线**，保留用于回归与 CDC 参考；
>   主线视频入口改为 PS 侧网络接收（待实现）。
>
> 下文出现的 `inf_reset_sync.v` 等文件名仍是**命名示例**（文件不存在）。
>
> 例：`src/rtl/common/inf_reset_sync.v` 必须在 `rtl/common/`；`src/rtl/video/vid_dvp_capture.v` 必须在 `rtl/video/`。
> **跨目录放错 = 违反 §2.2**，`tools/check_repo.py` 应能检出（该脚本已创建，`CHECK_REPO_OK`）。

## 规则

- **只在本目录改设计**（§4）：任何设计变更必须落到 `src/`，再由脚本重建产物。
- **命名硬约束**（§1）：目录名与文件名只用 `a-z 0-9 _ - .`；禁止中文、空格、括号、全角字符。**文件正文可用中文**。
- 目录名一律小写，`snake_case` 用下划线，不用驼峰。
- RTL 模块名必须带子系统前缀（`inf_` / `vid_` / `acc_` / `io_`），且前缀与所在目录一致。
- 对应 testbench **不放这里**：`x.v` 的 testbench 命名为 `x_tb.v`，放 `sim/`（见 [`../sim/README.md`](../sim/README.md)）。
- `src/rtl/ip/*.xci` 为 Vivado 生成配置，**只读、勿手改**。
- 禁止把 `build/` 产物、`.bit`、`.dcp`、`__pycache__/`、`.venv/` 放进本目录（见根 `.gitignore`）。
- **待裁定项**：核心加速器实现方式（HLS / RTL / 混合）、核心算法的具体形态与输入规格、数据集来源与许可、软硬件划分边界与 AXI 接口契约具体数值、与旧项目资产的具体复用范围 —— 全部写上 `<待用户裁定：见 docs/repo_spec.md §7>`，**不得擅自填入**。
  > ★ 事实进展（2026-10-20，**不是状态变更**，见 [`../ai/coordination/DECISIONS.md`](../ai/coordination/DECISIONS.md) 汇总段）：
  > D003 已按 **HLS 单内核路线**落地出片、D006 的 AXI 数值已冻结（`docs/accel_interface.md` §4），
  > 但两者都是 Lead 的工程选择、**未经用户裁定**，台账状态仍为 `discussing`。
  > D004 原则已裁定（YOLO 识别），参数待细化；D011 已把交付范围定为「人 + 球 + 简单事件 + 倒地 + 轨迹」，
  > 不做越位/犯规判罚；D015 已取消板载摄像头采集，视频改由 PC 经以太网送入。

## 怎么用

```powershell
# 1. 查看本目录结构（应与上面子树一致）
Get-ChildItem -Recurse -Directory D:\zynq-yolo\src

# 2. 新增一个 RTL 模块（先确定前缀，再放到对应子目录）
#    例：新建复位同步器 -> src/rtl/common/inf_reset_sync.v
#    例：新建 DVP 采集   -> src/rtl/video/vid_dvp_capture.v
#    对应 tb 放 sim/tb/ 下同名镜像路径：sim/tb/common/inf_reset_sync_tb.v

# 3. ✅ 已有的 HLS 内核：src/hls/accel/
#    csim 判据（6 用例逐位比对）: python src/hls/accel/scripts/run_csim.py --par 8
#    csynth（出片口径 p8_1_40）: python src/hls/accel/scripts/run_engine.py / export_engine.py
#    设计变更后必须重跑 csim（契约 §8 变更纪律）

# 4. 设计定稿后由 build/scripts/ 的脚本综合实现（见 ../build/README.md）
```

> **本机环境事实**（写报告必须引用，出处见 §6 表；细节以 [`../docs/repo_spec.md`](../docs/repo_spec.md) 为准）：
> 器件 `xc7z020clg400-2`；本机工具链仅 Vivado / Vitis / Vitis_HLS **2020.2**（指南推荐 2026.1 或 2025.2，用 2020.2 须在报告中说明并保证脚本可复现）；
> 资源上限 53 200 LUT6 / 106 400 FF / **220 DSP48** / 4.9 Mb BRAM；**无 MIPI D-PHY、无 HDMI 输入、无 GTX/GTH**；
> 旧项目结论**中文一手原文**（`D:\FPGA\oi\CLAUDE.md:206`，逐字含表格管道符）`| DSP 仅 220 个 | 不做大算力 CNN |`。
> ⚠️ 英文形式「DSP only 220 — 不做大算力 CNN」是**转述**，不是一手原文，报告里请用中文原文。

> **待裁定的加速器/算法形态细节** `<待用户裁定：见 docs/repo_spec.md §7>`
> ★ 已落地部分不受此约束：`conv_engine` HLS 内核是按 D003 的**工程选择**（Lead，非用户裁定）
> 实现的，接口契约见 [`../docs/accel_interface.md`](../docs/accel_interface.md)。
