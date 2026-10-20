# zynq-yolo —— 2026 全国大学生嵌入式芯片与系统设计竞赛（AMD 赛道）参赛工程

> 参赛信息：**2026 全国大学生嵌入式芯片与系统设计竞赛** / **FPGA 创新设计赛道** / **AMD 3.3 自主选题**
> 本仓库是比赛的**完整工程包**（源码 + 仿真 + 综合实现报告 + 上板脚本与实测输出 + 可复现构建脚本 + 技能包 + 大模型协作记录）。

| | |
|---|---|
| 本目录装什么 | 仓库根：项目简介、**复现步骤**、目录对照说明；以及三份治理文件 `LICENSE` / `.gitignore` / `.gitattributes` / `Makefile` |
| 谁产生 | 手写 |
| 进 git | 是（`README.md` 是评审入口，指南 3.3.5.4 将其列为仓库必备文件） |

---

## 0. 当前状态（先读这段，避免误读）

- 本项目是**基于 Zynq-7020 的足球比赛视频分析系统**：PC 经**以太网**送入比赛视频，板端用
  **PL 通用 INT8 卷积引擎 + PS 前后处理**跑 YOLO，输出球员/球/轨迹/简单事件/倒地结果。
- **输入源已于 2026-10-20 变更（D015）**：**不再使用板载摄像头**；所有视频由 PC 经网口送入。
  原 DVP 采集链（`vid_dvp_capture` 等）退出主线，仅保留为历史/回归资产。
- 仓库已有可运行的设计真源：`conv_engine` HLS 内核（含 csim/出片）、PYNQ 驱动、球员跟踪与
  倒地带后处理工具、YOLO 模型与逐层金标准；网络视频入口、整机实时闭环仍在建设中。
- 目录职责与命名规则的**唯一事实源**是 [`docs/repo_spec.md`](docs/repo_spec.md)；本 README 与之冲突时，以该文件为准。
- 已裁定/待裁定事项以 [`ai/coordination/DECISIONS.md`](ai/coordination/DECISIONS.md) 为准；
  §10 的占位符只用于**尚未裁定**的参数，不用于已经裁定的功能范围。

---

## 1. 参赛与硬件信息

| 项 | 值 | 出处 |
|---|---|---|
| 竞赛 | 2026 全国大学生嵌入式芯片与系统设计竞赛 | — |
| 赛道 / 选题 | FPGA 创新设计赛道 / AMD 3.3 自主选题 | 指南（`ref/guide/guide_amd_2026.pdf`） |
| 器件型号 | `xc7z020clg400-2`（Zynq-7000，PS = 双核 Cortex-A9 + PL = Artix-7 级逻辑） | `docs/repo_spec.md` §6 |
| 开发板 | 正点原子 **领航者 ZYNQ 7020**，底板 **V3.7**（音频 WM8960）+ 核心板 ZYNQ_CORE_2V5 | `docs/repo_spec.md` §6 |
| ★ 底板版本陷阱 | **V3.7 ≠ V3.9**：V3.9 音频改 ES8388、I2S 引脚不同；`D:\FPGA\NAVIGATOR_ZYNQ_IO.xdc` 是 **V3.9** 文件，**勿用** | `docs/repo_spec.md` §6 |
| 资源上限（7020） | 53 200 LUT6 / 106 400 FF / **220 DSP48** / **4.9 Mb BRAM** / 1 GB DDR3（★ 见下方口径告诫） | `docs/repo_spec.md` §6 · 一手还原 [`ref/hardware.md`](ref/hardware.md) §B |
| 外设硬限制 | **无 MIPI D-PHY**（只能 DVP 摄像头）· **无 HDMI 输入** · **无 GTX/GTH**（无 PCIe/SFP/10G）· DDR3 挂在 PS 侧，PL 须走 AXI HP | `docs/repo_spec.md` §6 |
| 网络 | 双千兆：PS 侧 YT8521S（GEM）· PL 侧 YT8531（RGMII） | `docs/repo_spec.md` §6 |
| 视频输入（本项目） | **不使用板载摄像头**；所有视频由 **PC 经以太网**送入（D015）。具体走 PS GEM 还是 PL RGMII、用什么协议，尚未冻结 | `ai/coordination/DECISIONS.md` D015 |
| ★ 算力警示 | 旧项目结论**中文原文**：**「DSP 仅 220 个 — 不做大算力 CNN」** | `D:\FPGA\oi\CLAUDE.md:206` 逐字，见 [`ref/hardware.md`](ref/hardware.md) §C |
| 引脚两条死线 | ⛔ **J4 pin 2 绝对禁用**（是 `cam_sda`，与板载座硬并联）· ⛔ **LCD 与 J3 互斥**（同一批 `B34_L13–L24`） | [`ref/hardware.md`](ref/hardware.md) §F |
| 许可 | MIT（指南 3.3.3.3 推荐 MIT / Apache-2.0），见 [`LICENSE`](LICENSE) | 指南 3.3.3.3 |

> ⚠️ **数字口径告诫（写报告前必读）**：上表是**二手汇总**，一手还原在 [`ref/hardware.md`](ref/hardware.md)。
> 下列项已被标 **UNVERIFIED**，**不得直接写进 `report/`**：BRAM「140 × RAMB36」（一手出处 `CLAUDE.md:210` 只写「4.9 Mb」）、
> **Vitis_HLS 的具体版本号**、**LCD 时序参数**、**触摸 IC 具体型号**、**DDR3「34.1 Gbps」的测量口径**。
> 需要用这些数字时，先把出处补齐。详见 [`ref/hardware.md`](ref/hardware.md) §K。

---

## 2. 选题与创新点

**题目（工作名）**：基于 Zynq-7020 的足球比赛视频分析系统。目标是让一块资源有限的
Zynq-7020 对比赛视频做**可观测的目标与事件分析**，而不是做判罚推理。

**已裁定的交付范围**（D010 / D011 / D012）：

1. **人的识别与轨迹** —— 逐帧检测球员，输出可用的轨迹数据；
2. **球的识别** —— 检测足球；
3. **简单事件** —— 球出界后的球权归属、角球还是球门球；
4. **人员倒地识别** —— 识别倒地的球员/人员。

**明确不做**：越位、犯规、红黄牌等判罚类推理；HDMI/OSD 视频输出；板载摄像头采集（D015）。

**创新点（待正式润色）**：在只有 220 DSP 的 7020 上，用「PL 通用 INT8 卷积引擎 + PS 前后处理」
跑 YOLO；用触屏人工录入双方/裁判球衣颜色与半场，把不可靠的颜色自动推断变成一次人工设置，
服务于球员/非球员区分（D012）与球权判定。

---

## 3. 系统架构

**数据流**（D015 后的主线）：

```text
PC（视频文件 / 实时推流）
   │  以太网（PS GEM 或 PL RGMII，未冻结）
   ▼
PS：网络接收 → 帧重组 → DDR 帧缓冲
   ▼
PS：letterbox 416×416 + int8 量化 + 逐层调度
   ▼
PL：conv_engine 通用卷积引擎（一次算一层，无状态）
   ▼
PS：后处理 → 检测框 / 球员轨迹 / 队别 / 事件
   │  结果只序列化一次（D014）
   ├──► 以太网推给 PC
   └──► 落 SD 卡（与推送格式同构）
```

**软硬件划分原则**：时序/协议栈/分支密集的部分放 PS（网络、预处理、后处理、事件判定）；
唯一有算力收益的卷积放 PL。触屏 LCD 只作**数据输入端**（录球衣颜色与半场），不显示视频（D013）。

**详细依据与接口契约**见 [`docs/architecture.md`](docs/architecture.md)、
[`docs/integration_rtl_plan.md`](docs/integration_rtl_plan.md) 与
[`docs/accel_interface.md`](docs/accel_interface.md)。

---

## 4. 目录对照说明

> 指南 3.3.5.4 规定：**采用其推荐目录结构的队伍可省略《目录对照说明》**。
> 本仓库严格采用推荐结构（`README.md` / `src/` / `sim/` / `build/` / `board/` / `data/` / `skill/` / `report/`，并按评分项增设 `ai/`、`docs/`、`tools/`）；
> 但为评审清晰，仍**逐目录给出一句话说明**，并给出与指南 3.3.5.4 的对应关系。

### 4.1 目录职责表（唯一裁决，照抄 `docs/repo_spec.md` §2）

| 目录 | 职责（一句话） | 谁产生 | 进 git | 指南对应 |
|---|---|---|---|---|
| `README.md` | 项目简介 + **复现步骤** + 目录对照说明 | 手写 | ✅ | 3.3.5.4 / 3.3.5.3 |
| `docs/` | **本骨架规范** + 架构图 + 接口契约 + 寄存器映射 + 资源预算 | 手写 | ✅ | 3.3.5.3 |
| `src/` | **设计源码**：RTL / HLS / PS 侧软件 | 手写 | ✅ | 3.3.5.1 ① |
| `src/rtl/` | 手写 Verilog（会被综合进最终作品） | 手写 | ✅ | — |
| `src/hls/` | Vitis HLS C/C++ 加速器 + testbench + 约束脚本 | 手写 | ✅ | — |
| `src/ps/` | PS 侧 Python：PYNQ 驱动 / Overlay 加载 / host 应用 | 手写 | ✅ | 3.3.2 |
| `sim/` | **仿真与验证**：testbench、激励向量、回归脚本与结果 | 手写 | ✅ | 3.3.5.1 ② |
| `build/` | **可复现构建脚本** + 综合/实现报告 | 手写（脚本）/ Vivado（报告） | ✅（报告只留最新+基线） | 3.3.5.1 ③⑤ |
| `board/` | **上板工程、运行脚本、实测输出**（Overlay、日志、实拍图） | 手写 + 板级产生 | ✅ | 3.3.5.1 ④ |
| `data/` | **测试数据与参考结果**：算法参数/权重、标定、样本、金标准、结果 | 手写 + 工具产生 | ✅（大文件除外） | 3.3.5.1 ④ |
| `skill/` | **技能包**：每项含 `README.md`（适用范围/使用方法/失效条件） | 手写 | ✅ | 3.3.5.2 |
| `report/` | **设计报告 + 大模型协作记录** | 手写 | ✅ | 3.3.5.3 |
| `ai/` | 大模型协作**过程性**记录：原始层 / 整理层 / 协作协调 | 脚本 + 手写 | ✅ | 3.3.5.3 |
| `tools/` | 仓库校验、文档生成、度量采集脚本 | 手写 | ✅ | 3.3.5.2（校验脚本类技能） |
| `.research/` | 外部资料抽取的中间产物（指南 txt 等） | 工具产生 | ⚠️ 可忽略 | — |

补充说明（`docs/repo_spec.md` §2 表内未列出、但本仓库确实存在的目录）：

| 目录 | 一句话说明 | 进 git |
|---|---|---|
| `ref/` | 第三方文献与板卡资料（指南 PDF、原理图、数据手册、引脚表），**只作查证不作提交**，版权归第三方 | ❌ 已由 `.gitignore` 排除 |

### 4.2 `src/` 细分（`docs/repo_spec.md` §2.1）

| 路径 | 职责 |
|---|---|
| `src/rtl/common/` | 时钟、复位、CDC、FIFO 等与题目无关的基础设施（前缀 `inf_`） |
| `src/rtl/video/` | 视频输入与预处理：格式转换、缩放（前缀 `vid_`）。**主线不用板载摄像头（D015）**，DVP 模块仅作历史资产 |
| `src/rtl/accel/` | 加速器数据通路：DMA 搬运、流控、层间接口（前缀 `acc_`） |
| `src/rtl/io/` | 面向外设的接口：AXI-Lite 寄存器、UART、以太网、LCD（前缀 `io_`） |
| `src/rtl/ip/` | 需要保留的 IP 配置引脚/约束（`.xci` 只读，勿手改） |
| `src/hls/accel/src/` | HLS 顶层与算子实现（`.cpp`） |
| `src/hls/accel/include/` | HLS 头文件与定点类型定义（`.h` / `.hpp`） |
| `src/hls/accel/tb/` | HLS C 仿真 testbench |
| `src/hls/accel/scripts/` | `csynth.tcl` / `cosim.tcl` / `export.tcl` 等 HLS 构建脚本 |
| `src/ps/pynq_driver/` | PYNQ Overlay 封装类、寄存器映射、DMA 搬运与缓存一致性处理 |
| `src/ps/host/` | 上位机侧脚本（推流、评测、可视化） |

### 4.3 命名前缀 ↔ 目录一一对应（`docs/repo_spec.md` §2.2）

| 前缀 | 子系统 | 目录 |
|---|---|---|
| `inf_` | 时钟 / 复位 / CDC / FIFO | `src/rtl/common/` |
| `vid_` | 视频采集与预处理 | `src/rtl/video/` |
| `acc_` | 加速器数据通路 | `src/rtl/accel/` |
| `io_` | 外设接口（AXI-Lite / 以太网 / LCD） | `src/rtl/io/` |
| `hls_` | HLS 导出的 IP 顶层命名 | `src/hls/accel/` |

**Testbench 规则**：`sim/` **镜像** `src/rtl/` 的子目录结构；`x.v` 的 testbench 命名为 `x_tb.v`。

---

## 5. 怎么用：从零复现

### 5.1 前置条件

| 依赖 | 版本 | 说明 |
|---|---|---|
| Vivado / Vitis / Vitis_HLS | **2020.2**（本机实际安装版本，位于 `D:\Xilinx\*`） | 见本文第 9 节「工具链版本说明」 |
| Python | 3.8+（仅标准库即可跑 `tools/check_repo.py`） | 本机 `D:\python\python.exe` |
| GNU Make | 可选（Windows 用 `mingw32-make`，Linux/WSL 用 `make`） | 只做转发，不装也能直接跑脚本 |
| 开发板 | 正点原子 领航者 ZYNQ 7020（底板 **V3.7**）+ JTAG 下载器 + SD 卡 | 见 §1 |

### 5.2 步骤

```bash
# 0) 取得仓库（git clone 或解压评审包），进入根目录
cd zynq-yolo

# 1) 仓库自检：命名规则 / 必需目录 / 禁入文件
#    期望看到 SUMMARY: X PASS / 0 FAIL 以及末尾的 CHECK_REPO_OK 令牌
python tools/check_repo.py          # 或： mingw32-make check

# 2) 仿真回归（脚本由 sim/scripts/ 提供；尚未创建时 make 会打印 TODO）
mingw32-make sim

# 3) 构建：综合 → 实现 → 出比特流
#    脚本入口为 build/scripts/build_bitstream.tcl，工具链路径通过变量注入：
#    （VIVADO 默认取 PATH 中的 vivado，可用 VIVADO=... 覆盖，脚本内不硬编码版本路径）
mingw32-make bit

# 4) 上板：加载 Overlay 并运行实测（脚本与证据见 board/README.md）
#    证据落盘位置：board/logs/（串口输出）、board/images/（实拍图）
#    ※ 脚本尚未创建时 make 会打印 TODO；上板脚本落地后在此补具体命令

# 5) 生成提交件报告
mingw32-make report
```

> 仓库当前处于骨架阶段：`src/` 尚无设计实现，因此第 2–4 步的脚本可能尚未存在。**这是诚实状态，不是故障**；对应 target 会打印 `TODO: <脚本路径>` 而不是假装成功。

### 5.3 成功判据（必读，`docs/repo_spec.md` §5）

> **成功判据是日志令牌 `DONE_OK` / `PROGRAM_OK`，不是退出码。**
> Vivado 的 `vivado.bat` 即使内部报了 `error`，也可能返回退出码 0；只看退出码会把失败判成成功。

| 阶段 | 成功判据（日志令牌） | 日志落盘位置 |
|---|---|---|
| 综合 / 实现 / 出比特流 | 日志出现 **`DONE_OK`** | `build/reports/`（只留最新 + `baseline*/`） |
| JTAG 下载 / Overlay 加载 | 日志出现 **`PROGRAM_OK`** | `board/logs/`（`.md` 形式保留） |

配套纪律：

- 凡是**不能由脚本重建**的东西（串口日志、肉眼观察、实拍截图、实测数字）**必须落盘**；能重建的（`.bit` / `.rpt` / `.dcp` / `.log`）由 `.gitignore` 排除。
- 进入 `report/` 的每个数字都必须能追溯到 `docs/repo_spec.md` §6 环境事实表或 `build/reports/`（**数字必须标来源**）。

---

## 6. 命名规则（强制，`docs/repo_spec.md` §1）

1. **目录名与文件名：纯 ASCII**，只用 `a-z 0-9 _ -` 与 `.`；**禁止中文、空格、括号、全角字符**。
2. **文件正文可以用中文**（指南明确允许）。
3. 目录名一律**小写**；`snake_case` 用下划线，不用驼峰。
4. 每个目录都必须有一份 `README.md`，首行说明「本目录装什么 / 谁产生」。
5. 版本/日期后缀用 `_YYYYMMDD`，例如 `reports_20261019`。
6. 仓库内**禁止**出现 `build*.log`、`vivado*.log|jou`、`.Xil/`、`*.dcp`、`__pycache__/`、`.venv/`。
7. **`.gitkeep` 是空目录占位，不是垃圾**：git 不跟踪空目录，骨架里 31 个预留目录靠它才能在 clone 后存活。
   **不要删除、不要往里面写内容、不要加入 `.gitignore`**。承 §7「真源 vs 产物」：目录结构本身是提交件的一部分。

自检命令：`python tools/check_repo.py`（逐条打印 `PASS` / `FAIL`，末尾输出 `SUMMARY:` 与 `CHECK_REPO_OK`）。

---

## 7. 真源 vs 产物（`docs/repo_spec.md` §4）

> **能由仓库内脚本一条命令重建的，就是产物；产物不进 git 或只保留基线。**

| 类型 | 位置 | 能改吗 | 进 git |
|---|---|---|---|
| 设计真源 | `src/` | ✅ 只在这里改设计 | ✅ |
| 构建真源 | `build/scripts/` | ✅ | ✅ |
| 仿真真源 | `sim/tb`、`sim/scripts` | ✅ | ✅ |
| 上板真源 | `board/scripts`、`src/ps` | ✅ | ✅ |
| 文档真源 | 所有 `.md` | ✅ | ✅ |
| **产物** | `build/reports/*`、`board/overlay/*.bit`、`board/logs/*` | ❌ 会被重建覆盖 | 只留有价值的基线 |

---

## 8. 本机环境事实（写报告必须引用，`docs/repo_spec.md` §6）

| 项 | 值 |
|---|---|
| 器件型号 | `xc7z020clg400-2`（旧项目 5 个 `.xpr` 与全部 `build.tcl` 一致） |
| 开发板 | 正点原子 **领航者 ZYNQ 7020**，底板 **V3.7**（音频 WM8960）+ 核心板 ZYNQ_CORE_2V5 |
| 底板版本陷阱 | **V3.7 ≠ V3.9**（V3.9 音频改 ES8388 且 I2S 引脚不同）；`D:\FPGA\NAVIGATOR_ZYNQ_IO.xdc` 是 **V3.9** 文件，**勿用** |
| 工具链 | 本机仅 **Vivado / Vitis / Vitis_HLS 2020.2**（`D:\Xilinx\*`） |
| 指南推荐版本 | 2026.1 或 2025.2；**用 2020.2 须在报告说明并保证脚本可复现**（指南 3.3.3.1） |
| 资源上限（7020） | 53 200 LUT6 / 106 400 FF / **220 DSP48** / **4.9 Mb BRAM** / 1 GB DDR3 |
| 外设硬限制 | **无 MIPI D-PHY**（只能 DVP 摄像头）· **无 HDMI 输入** · **无 GTX/GTH**（无 PCIe/SFP/10G）· DDR3 挂在 PS 侧，PL 须走 AXI HP |
| 网络 | 双千兆：PS 侧 YT8521S（GEM）· PL 侧 YT8531（RGMII） |
| PYNQ | 曾用 SD 启动 PYNQ（Ubuntu 20.04 / kernel 5.4.0）成功加载自研 Overlay（`.bit` + `.hwh`） |
| ★ 算力警示 | 旧项目结论**中文原文**：**「DSP 仅 220 个 — 不做大算力 CNN」**（`D:\FPGA\oi\CLAUDE.md:206` 逐字） |

> 该表由「文件体系」任务维护；任何进入 `report/` 的数字必须能在此表、[`ref/hardware.md`](ref/hardware.md)
> 或 `build/reports/` 找到出处。**本表是二手汇总**，一手还原与 **UNVERIFIED 清单**见 [`ref/hardware.md`](ref/hardware.md) §K。

---

## 9. 工具链版本说明（2020.2 vs 指南推荐）

- 指南 3.3.3.1 **推荐 Vivado / Vitis 2026.1 或 2025.2**；使用其他版本**须在报告中说明并保证脚本可复现**。
- 本队现状：本机**仅安装 2020.2**（`D:\Xilinx\Vivado|Vitis|Vitis_HLS`，本机实测）。
- 本队选择与理由：**先用 2020.2 把「综合 → 实现 → 出比特流 → 上板」全链路打通，再评估是否升级**。理由是 2020.2 已能满足 Zynq-7020 的器件支持与 PYNQ 镜像兼容性，而升级工具链会带来 IP 版本与工程迁移风险；**版本差异统一登记进设计报告**，并保证：
  1. 构建脚本**参数化工具链版本**，不硬编码 `D:\Xilinx\...` 之类的绝对路径；
  2. 所有构建入口由 `build/scripts/` 提供，陌生人换版本只需改变量；
  3. 报告里登记「实际使用版本 + 与指南推荐版本的差异 + 复现注意事项」。

---

## 10. 待用户裁定项（`docs/repo_spec.md` §7）

以下内容**尚未由用户确认**，任何成员**不得**据以写入 `src/`、`build/`、`board/` 的实现：

1. **核心加速器的实现方式**（HLS / RTL / 混合）—— 仍未裁定（台账 **D003**）。
   技术基础见 [`docs/yolo_on_zynq7020_survey.md`](docs/yolo_on_zynq7020_survey.md)（该文推荐 D 路线：PL 通用量化卷积引擎 + PS 前后处理）。
2. **核心算法的具体形态与输入规格** —— ★ **原则已由用户 2026-10-19 裁定：识别判定交给 YOLO 模型**（台账 **D004**）。
   **仍待细化的参数**：具体型号 / 输入分辨率（416 还是 160–224 或 YOLO-Fastest 级）/ 类别集合 / 训练来源。
   **视频来源已另行裁定（D015）**：不使用板载摄像头，视频由 PC 经以太网送入；网络端口/协议仍待定。
   ⚠️ 这组参数**直接决定性能分怎么写**（416 ⇒ 约 2 fps；160–224 ⇒ 6–25 fps，均为估算），取舍见调研文档 §3.1 与 §7.1 第 3 条。
3. 数据集来源与许可（台账 **D005**）。
4. 软硬件划分边界与 AXI 接口契约的具体数值（台账 **D006**）。
5. 与 `D:\FPGA\oi` 旧项目资产的具体复用范围（已确认原则：**复用管线，PYNQ 软硬件框架重做**；台账 **D007**）。

> 骨架已为以上每一项预留落点；**待裁定后再填内容**。本 README 的 §2 / §3 因此只放占位符。
>
> ★ **措辞沿革（留档，勿删）**：第 2 项原先写作「核心算法的具体形态与输入规格（**是否采用学习模型本身也尚未裁定**）」，
> 因为当时用户**尚未说明题目改造计划**，而工作目录名 `zynq-yolo` 里的 "yolo" **只是目录名**，不足以当依据。
> 2026-10-19 用户明确「把识别判定交给 YOLO 模型」后，该中性表述**退役**，但**参数**仍未定 —— 本条现在拆成「原则（已定）+ 参数（待定）」两段读。
> ⚠️ 同时**不得遗忘算力上限**：`| DSP 仅 220 个 | 不做大算力 CNN |`（一手原文，见 [`ref/hardware.md`](ref/hardware.md) §C）——
> 选定 YOLO 之后，型号/量化/剪枝/分帧策略都受这条约束限制。


---

## 11. 许可与引用

- 本仓库代码与文档以 **MIT License** 发布，见 [`LICENSE`](LICENSE)。（指南 3.3.3.3 强烈建议开源，推荐 MIT / Apache-2.0。）
- `ref/` 下的指南 PDF、板卡原理图、数据手册、摄像头/LCD 模块文档**版权归第三方**，仅作查证，**不进 git、不作提交**。
- 引用 Xilinx/AMD 文档时请写明文档编号（如 UG585 / UG901 / UG904 / UG949 / UG1399）与章节，便于评审复核。

---

## 12. 相关文档

| 文档 | 内容 |
|---|---|
| [`docs/repo_spec.md`](docs/repo_spec.md) | ★ **唯一事实源**：命名规则、目录职责、模板、真源/产物、判据、环境事实、待定项 |
| [`docs/amd_official_layout.md`](docs/amd_official_layout.md) | **AMD 官方布局与版本控制建议查证记录**：UG949 受控源文件清单、AMD 6 种版本控制方法论、PYNQ 官方模板逐字要点、本仓库为何选 Foundational 派及其代价 |
| [`docs/architecture.md`](docs/architecture.md) | 系统架构文档**骨架**（软硬件划分、接口契约、资源预算）—— 待用户裁定题目后填充 |
| [`docs/yolo_on_zynq7020_survey.md`](docs/yolo_on_zynq7020_survey.md) | **YOLO on XC7Z020 技术调研**：五条路线判定（含「Vitis AI DPU 在 7000 系不存在」一手核查）、同芯片实测锚点（ARC2020 / 1.88 fps）、开源实现清单、推荐路线与验证阶梯。**引用其数字前必读 §9 未验证项** |
| [`ref/hardware.md`](ref/hardware.md) | **硬件能力清单**（逐条 `文件:行号`）+ **UNVERIFIED 清单** —— 写报告取数的**一手**来源 |
| [`ref/pitfalls_from_oi.md`](ref/pitfalls_from_oi.md) | **踩坑清单 P1–P10**（现象 / 触发 / 定位 / 规避 / 出处），承旧项目已验证经验 |
| [`ai/coordination/DECISIONS.md`](ai/coordination/DECISIONS.md) | 决策台账（`decided` 4 条 / `discussing` 5 条 / `rejected` 0 条）+ **编号纪律**（本仓库 `DNNN` 与旧项目台账不是同一命名空间） |
| [`report/README.md`](report/README.md) | 设计报告必须覆盖的 7 项、优化前后对比表与权重表 |
| [`skill/README.md`](skill/README.md) | 技能包规范（适用范围 / 使用方法 / 失效条件） |
| [`ai/README.md`](ai/README.md) | 大模型协作三层记录体系（原始层 / 整理层 / 沉淀层） |
| [`ai/tools/README.md`](ai/tools/README.md) | 会话导出器与逐字自证脚本（`export_transcript_dsh.py` / `verify_raw_match.py`） |
| [`tools/README.md`](tools/README.md) | 仓库自检与文档工具（`check_repo.py` 等，含 TODO 状态） |

---

**一句话复现判据**：`python tools/check_repo.py` 打印 `CHECK_REPO_OK`；`mingw32-make bit` 的日志出现 `DONE_OK`；`mingw32-make` 上板流程的日志出现 `PROGRAM_OK`。**不要用退出码判断成功。**
