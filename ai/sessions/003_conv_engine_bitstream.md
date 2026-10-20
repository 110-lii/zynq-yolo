# Session 003 — conv_engine HLS 内核落地、INT8 量化验证与出片（单路主线）

> 整理层记录。对应原始层分片 [`../raw/2026-10-19_8160c00c.md`](../raw/2026-10-19_8160c00c.md)（主会话，46 条提示词，
> 汇总见 [`../raw/_prompts_all.md`](../raw/_prompts_all.md) P8–P53）。

| 字段 | 内容 |
|---|---|
| 会话号 | 003 |
| 日期 | 2026-10-19（整理于 2026-10-20） |
| 执行 AI（模型 ID） | `group/1101`（客户端 `dsh`，DeepSeek Harness Agent Teams，含子代理） |
| 执行客户端 / 工具 | `dsh` |
| 角色 | Lead（+多个子代理：调研、图片搜集、HLS 构建） |
| 任务目标 | 把「YOLO 出人」从裁定变成硬件：PC 金标准 → INT8 量化验证 → HLS 卷积内核 → Vivado BD 出片（时序收敛） |
| 上下文投喂 | `docs/yolo_on_zynq7020_survey.md`（同芯片锚点 ARC2020 1.88 fps）、`D:\FPGA\oi` 旧项目 PS7 配置、`ref/hardware.md`（DSP 220 上限）、指南 3.3.5.x |
| **原文位置** | [`../raw/2026-10-19_8160c00c.md`](../raw/2026-10-19_8160c00c.md) 全程；关键用户指令：P13（YOLO 裁定）、P26（资源质疑）、P34/P35（双路→先单路）、P37–P39（路线选择）、P46–P48（颜色/边线/12 条人工判罚） |
| 关联决策 | D004（YOLO 原则裁定，第 4 次变更）、D010（范围收窄「看得见的目标」，用户 P38 回复 `a`）；D003/D006 的**事实进展**（HLS 落地、AXI 冻结）但状态仍 `discussing` |
| 复核方 | 未复核（数字均出自可重跑产物：`build/reports/`、`data/results/`、`data/golden/*.json`） |

---

## 关键轮次（用户指令 → 走向）

**P13（15:12）「现在思路和之前差不多，但是要把识别判定交给YOLO模型，先把项目git，上传github」**
→ D004 原则裁定 + git 建仓（`bb556e6`）+ GitHub `110-lii/zynq-yolo`。此为当天分水岭：之前是骨架，之后是工程。

**P26（16:49）「你确定这个可以在板子上搭载，资源确定够吗」**
→ 触发资源实测路线：先 `hls_probe` 扫并行度，再 `hls_engine` 真内核收敛（详见纠错轨迹 2/3），
最终 `p8_1_40_a0`：LUT 66.5% / DSP 58.6%（`docs/accel_interface.md` §7.2）。

**P34/P35（18:50/18:54）双路交叉验证想法 →「我们现在先把单路跑通了再说」」**
→ 范围收缩为单路。BD 设计（`build/scripts/bd_conv_engine.tcl`）按单内核两 AXI-Lite 从口落定。

**P37–P39（19:21–19:27）「先看整网端到端跑通」→ `a` → `b`**
→ PC 侧整网：`tools/int8_sim.py`（INT8 量化验证）+ `tools/pynq_net.py`（整网编排，后端可插拔）+ golden 270 张量×2 分辨率。

**P38 同日另一次（D010 的 `a`）**：题目收窄到「看得见的目标」；三条范围外各挂实测产物（球可见性扫描 / 白线几何 / 12 条人工裁定）。

**P46–P48（20:47–21:44）球衣颜色 / 边线弧顶 / 12 条真实判罚**
→ 可行性探针族（`jersey_color.py` / `pitch_geometry.py` / `find_incidents.py`）+ 用户交付 12 条裁定（`data/samples/incidents/MANIFEST.csv`），构成 D010 范围外 (b)(c) 的证据。

---

## ★ 自我纠错轨迹

| 项目 | 内容 |
|---|---|
| **错在哪** | 按 100 MHz 约束首次实现后认为「时序应该没问题，HLS csynth 报 Estimated Fmax 136.99 MHz」 |
| **怎么发现** | Vivado `report_timing_summary`：WNS **−1.247 ns**，失败端点 226/77 469，最差路径 22 级逻辑、route 占 49.8% |
| **证据** | `build/reports/timing/conv_engine_bd_timing.rpt`（首版被 83.33 MHz 复跑覆盖，首版数字逐字保留在 `docs/accel_interface.md` §7.4 表） |
| **怎么改** | FCLK0 100→**83.333333 MHz**（1000/12 标准分频点），重跑实现 |
| **判据** | 复跑后 WNS **+0.025 ns**、TNS 0、结论行 `All user specified timing constraints are met.`；`conv_engine.bit`(4 045 674 B)+`.hwh`(285 760 B) 落盘 `board/overlay/` |
| **根因归纳** | HLS 的 Fmax 估计不含布线延迟，与实现后真实 Fmax 差 55%；报告必须区分「HLS 估计」与「实现后实测」 |

| 项目 | 内容 |
|---|---|
| **错在哪** | 以为「累加器落 BRAM 是 LUT 大头（6 448 地址 mux），全展开到寄存器（ACC_FULL=1）就能省 LUT」 |
| **怎么发现** | 跑了 `p8_1_40_a1` 对照：LUT 从 35 385 **涨到** 70 928（133%） |
| **证据** | `build/hls_engine/logs/csynth_*.log` 的 CSYNTH_DONE 行；结论已写进 `docs/accel_interface.md` §6 教训段 |
| **怎么改** | 保持 ACC_FULL=0；真正的旋钮是 TILE_H：4→1（LUT 138%→66.5%） |
| **判据** | `p8_1_40_a0` 成为唯一装得下的配置（LUT 66.5% / DSP 58.6%），后续 PAR_OUT 扫 16/24/32 全部超限佐证 |
| **根因归纳** | HLS 资源成本在**副本数**不在分区方式；直觉（「展开=省」）必须用对照实验证伪 |

| 项目 | 内容 |
|---|---|
| **错在哪** | 测试向量第一版随手取 `M∈[2^30,2^31)`、`S∈[25,40]`，输出全撞 0/255 钳位，「算错了」和「算对了」输出一样 |
| **怎么发现** | 失配率 40%，且差点把「bias 漏加」这个真 bug 误判成「钳位正常」 |
| **证据** | `sim/scripts/make_hls_case.py` 注释 + `docs/accel_interface.md` §9.5 |
| **怎么改** | 由 `acc` 实际量级反推 M/S，让输出铺满 0..255；bias 折叠 bug 随之暴露（csim 在 (0,0,0) 逐位比对变红）并修复（`acc` 初值 = `pm_bias` 而非清零） |
| **判据** | csim 三层令牌：`CSIM_DONE <tag> OK` / `TB_CONV_OK <case> <n>` / `CSIM_ALL_OK <n>`，6 用例全绿 |
| **根因归纳** | **判据必须先能失败**：一个在有 bug 时也能通过的测试不是测试（仓库第二条同型教训） |

| 项目 | 内容 |
|---|---|
| **错在哪** | requant 写 `ap_int<64>(a*b)`：括号内先按 32 位溢出再转 64；改成 `ap_int<64>(a)*ap_int<64>(b)` 又生成 64×64 乘法器（每个 4 DSP，requant 独吃 105 DSP） |
| **怎么发现** | DSP 报告超预算 + 综合网表里出现 `mul_64s_64s_64` 实例 |
| **证据** | `conv_engine.cpp` requant 段「坑 1/坑 2」注释（两轮实测过程逐字留档）；§7.4 最差路径也在该段 |
| **怎么改** | 操作数扩 `ap_int<48>`、乘积 `ap_int<64>` 承接 ⇒ 48×48→64（3 DSP/个） |
| **判据** | DSP 总量 129/220（58.6%）收敛，csim 逐位不变 |
| **根因归纳** | C++ 类型转换优先级 + HLS 位宽即资源：写法定死乘法器规格，差一个括号就是 4×DSP |

---

## 产出文件（全部已提交，见 git log `2b373e3..a8e1e33` 与 `937ecef`）

| 路径 | 说明 |
|---|---|
| `docs/accel_interface.md` | 新增：三方共用接口契约（布局/定点/签名/层范围/资源实测） |
| `src/hls/accel/src/conv_engine.cpp` | 新增：真内核（运行期几何 + 行缓冲流式 + per-channel requant） |
| `src/hls/accel/tb/tb_conv_engine.cpp`、`scripts/*` | 新增：TB + csynth/csim/export 脚本（含 core_revision 溢出绕过 `export_engine.py`） |
| `tools/int8_sim.py`、`tools/pynq_net.py`、`tools/ref_int8_conv.py` 等 | 新增：量化验证 / 整网编排 / 契约参考实现 |
| `data/golden/yolov8n_{320,640}_golden.json` | 新增：270 张量×2 分辨率金标准凭据 |
| `build/scripts/bd_conv_engine.tcl`、`ps7_config.tcl` | 新增：BD 生成 + PS7 配置（出片真源） |
| `board/overlay/conv_engine.{bit,hwh}` | 新增：83.33 MHz 时序收敛轮出片（产物基线） |
| `data/results/{ball_visibility_sweep,pitch_geometry,ball_cv}.json` 等 | 新增：D010 三条范围外的实测依据 |
| `ai/coordination/DECISIONS.md` | 更新：D010 登记、D004 第 4 次变更 |

## 未决问题

- **未上板**：`board_layer_test.json` 仍是 PC 侧占位（`bitfile_present: false` 时写入）；`elapsed_ns`/`gops` 为 null，**整网 fps 没有可信实测**（§7.5 禁引 csynth Latency）。
- D003/D005/D006/D007 台账状态仍 `discussing`（事实进展 ≠ 状态变更，见 DECISIONS 汇总段）。
- `tools/collect_metrics.py` / `md_to_docx.py` 仍未创建（tools/README 现状表）。

## 参考文档出处

| 出处 | 用在哪 |
|---|---|
| `docs/accel_interface.md` §7.2/§7.4 | 资源收敛表与时序数字（本文纠错轨迹 1/2 判据） |
| `build/reports/timing/conv_engine_bd_timing.rpt` | WNS +0.025 / 端点 77 491 / 12 ns 约束 |
| `build/reports/resource/conv_engine_bd_util.rpt` | LUT 34 724 (65.27%) / FF 30 570 / BRAM 9 / DSP 220 (100%) |
| `docs/repo_spec.md` §5 | 令牌判据纪律（DONE_OK / PROGRAM_OK / 不看退出码） |
| UG949 / AMD 2025.2 教程（版本随引用） | Foundational 派版本控制（D008，承 Session 001） |

## 结论与沉淀

**可用结论（带数字）：**

- XC7Z020 上单内核 INT8 卷积引擎可收敛在 LUT 66.5% / DSP 58.6%（`p8_1_40_a0`），时序 83.33 MHz 收敛（WNS +0.025 ns）。
- HLS Estimated Fmax（136.99 MHz）与实现后真实 Fmax（≈88.5 MHz）差 55% —— 报告引用必须注明口径。
- INT8（QUInt8 激活 + QInt8 权重）在真实转播帧上可用 onnxruntime 正常加载并保持精度（`tools/int8_sim.py`）。
- 传统 CV 在 1280×720 转播画面定位球不可行（人眼 2/10，判据假绿分析见 `data/results/ball_cv.json` verdict）。

**可沉淀为技能包的内容：**

- [ ] → `skill/`：①「HLS csynth Fmax ≠ 实现 Fmax」口径纪律（本案 55% 差）；②「测试向量必须由量级反推 M/S 否则钳位假绿」。已有 `onboard-test-criteria` 技能覆盖判据先能失败的通则，本两案可作其附录案例。

**关联记录：** Session [001](001_repo_skeleton.md) / [002](002_yolo_survey_intake.md)
