# docs/ —— 设计文档与仓库规范

> 上级：[`../README.md`](../README.md)

| | |
|---|---|
| 本目录装什么 | **仓库骨架规范** + **系统架构文档**（暂为骨架，待用户裁定题目后填充） |
| 谁产生 | 手写 |
| 进 git | 是 |

---

## 内容

| 文件 | 说明 | 状态 |
|---|---|---|
| [`repo_spec.md`](repo_spec.md) | ★ **仓库骨架规范（唯一事实源）**：目录职责、命名规则、README 模板、真源/产物规则、构建判据、本机环境事实、待定项 | ✅ 已完成 |
| [`architecture.md`](architecture.md) | 系统架构：已按 D017 填入数据流、软硬件划分依据、`preproc` 接口契约与实测资源/时序 | ✅ 已完成（D017） |
| [`system_status.md`](system_status.md) | **当前系统状态（唯一现役口径）**：PL 预处理 + PS ncnn 架构、实测指标、板上运行命令、已知边界 | ✅ 已完成（2026-10-25） |
| [`display_overlay_plan.md`](display_overlay_plan.md) | **HDMI/LCD 显示 overlay**：1bpp BRAM + TMDS/LCD 驱动、引脚/资源/出片门禁 | ✅ 均已出片且读回通过；画面待目视 |
| [`amd_official_layout.md`](amd_official_layout.md) | **AMD 官方布局与版本控制建议查证记录**：UG949 受控源文件清单、AMD 5/6 种版本控制方法论、PYNQ 官方模板逐字要点、本仓库选 Foundational 派的理由与代价 | ✅ 已完成 |
| [`yolo_on_zynq7020_survey.md`](yolo_on_zynq7020_survey.md) | **YOLO 在 XC7Z020 上的技术调研**：五条路线判定（含「Vitis AI DPU 在 7000 系不存在」的一手核查）、同芯片实测锚点（ARC2020，1.88 fps）、开源实现清单、推荐路线 D 与验证阶梯；**§9 是引用本文数字前必读的未验证项清单** | ✅ 已完成 |
| [`accel_interface.md`](accel_interface.md) | **`conv_engine` 加速器接口契约**：AXIS 流格式、`tuser`/`tlast` 语义、int8 量化口径、单层吞吐与资源实测、§9 逐字节一致性用例 | ✅ 数值已冻结，待用户确认（D006） |
| [`integration_rtl_plan.md`](integration_rtl_plan.md) | **历史方案：整机 RTL 集成计划（D004 = 416 精度优先档）** —— 已被 D017 取代，仅保留 416 路线的设计、容量冲突与放弃理由 | ⛔ 历史（勿按此复现） |
| [`player_tracking_v1.md`](player_tracking_v1.md) | 球员追踪 v1 口径：YOLOv8n + ByteTrack + ECC 相机运动补偿；旧场 12 段 4355 帧 → 942 条轨迹的产出与已知局限 | ✅ 已完成（旧场） |
| [`player_labels_v1.md`](player_labels_v1.md) | 球员人工标注 v1：分层抽样口径、`labels.csv` 字段与判定含义 | ✅ 已完成（旧场） |
| [`player_gate_v1.md`](player_gate_v1.md) | 球员门槛 v2（规则表 + 几何守卫 + hull 复核）：8 组消融，v1 丢 834 框 → v2 只丢 98 框且救回 736（88.2%）、新误杀 0 | ✅ 已完成（旧场） |
| [`hi_layer_color_gate.md`](hi_layer_color_gate.md) | hi 层取色失败原因分层（purity/rect/color/too_small）与标注可信度（仅 P/N 三批 κ=0.9286）；**跨场层间保序不成立**的留档 | ✅ 已完成 |
| [`newmatch_label_pack.md`](newmatch_label_pack.md) | 新比赛分层标注包（第四批，83 框）：抽取口径、标注判决、层内 N 率与 Wilson CI、保序检验 | ✅ 已完成 |
| [`cross_match_eval.md`](cross_match_eval.md) | **跨场评估框架**：G1–G6 判据、`--play-windows`/`--palette-refs`/`--color-metric` 开关、四档实测、v2 假绿留档、§13.8 证据索引（sha256） | ✅ 已完成 |
| [`cross_match_results.md`](cross_match_results.md) | **跨场实测结论**（结论优先）：彩色分支过紧已修 / `too_small`·`rect` 跨分辨率判定是伪影 / 非队色在 v2 下仍被分队（未修） | ✅ 已完成 |
| [`color_prior_v1.md`](color_prior_v1.md) | 颜色先验 v1 口径：`PALETTE_REF` 色板、躯干取色、分类与距离度量 | ✅ 已完成（v2 见 `cross_match_eval.md`） |

---

## 规则

1. **本目录只放 `.md`。** 图片放 [`../report/figures/`](../report/figures/)，报告成品放 [`../report/`](../report/)。
2. 中文只在**正文**里；**文件名必须纯 ASCII 小写**（指南 3.3.5.4）。
3. **`repo_spec.md` 是唯一事实源** —— 目录职责有争议时以它裁决，不要在别处另立一套。
4. 任何**数字**（资源、频率、时序余量、帧率）必须标出处：要么指向 `build/reports/`，要么指向 `board/logs/` 里的实测记录。
   **承旧项目纪律：数字必须标来源，否则不写。**

---

## 怎么用

| 我想… | 去哪 |
|---|---|
| 知道某个目录该放什么 | [`repo_spec.md`](repo_spec.md) §2 |
| 知道文件名怎么起 | [`repo_spec.md`](repo_spec.md) §1 |
| 知道哪些文件不该进 git | [`repo_spec.md`](repo_spec.md) §4 + 根 [`.gitignore`](../.gitignore) |
| 知道「官方推荐架构」到底指哪几层、为什么本仓库不用 Vivado 默认工程布局 | [`amd_official_layout.md`](amd_official_layout.md) |
| 知道 YOLO 在这块板子上到底跑不跑得动、有哪些前人实现 | [`yolo_on_zynq7020_survey.md`](yolo_on_zynq7020_survey.md)（数字引用前先看 §9） |
| 知道整机 PL 还要写哪些模块、每个模块怎么验 | [`integration_rtl_plan.md`](integration_rtl_plan.md) §3 / §6 |
| 知道 `conv_engine` 的流接口和量化口径 | [`accel_interface.md`](accel_interface.md) |
| 知道构建「成功」怎么判 | [`repo_spec.md`](repo_spec.md) §5（日志令牌 `DONE_OK` / `PROGRAM_OK`） |
| 知道板子和工具链的硬约束 | [`repo_spec.md`](repo_spec.md) §6 + [`../ref/hardware.md`](../ref/hardware.md) |
| 知道哪些事还没定 | [`repo_spec.md`](repo_spec.md) §7 + [`../ai/coordination/DECISIONS.md`](../ai/coordination/DECISIONS.md) |
| 写设计报告 | [`../report/README.md`](../report/README.md) |
