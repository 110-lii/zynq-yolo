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
| [`architecture.md`](architecture.md) | 系统架构文档骨架：软硬件划分、数据流、接口契约、资源预算 | ⬜ **骨架，待用户裁定题目后填充** |
| [`amd_official_layout.md`](amd_official_layout.md) | **AMD 官方布局与版本控制建议查证记录**：UG949 受控源文件清单、AMD 5/6 种版本控制方法论、PYNQ 官方模板逐字要点、本仓库选 Foundational 派的理由与代价 | ✅ 已完成 |
| [`yolo_on_zynq7020_survey.md`](yolo_on_zynq7020_survey.md) | **YOLO 在 XC7Z020 上的技术调研**：五条路线判定（含「Vitis AI DPU 在 7000 系不存在」的一手核查）、同芯片实测锚点（ARC2020，1.88 fps）、开源实现清单、推荐路线 D 与验证阶梯；**§9 是引用本文数字前必读的未验证项清单** | ✅ 已完成 |

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
| 知道构建「成功」怎么判 | [`repo_spec.md`](repo_spec.md) §5（日志令牌 `DONE_OK` / `PROGRAM_OK`） |
| 知道板子和工具链的硬约束 | [`repo_spec.md`](repo_spec.md) §6 + [`../ref/hardware.md`](../ref/hardware.md) |
| 知道哪些事还没定 | [`repo_spec.md`](repo_spec.md) §7 + [`../ai/coordination/DECISIONS.md`](../ai/coordination/DECISIONS.md) |
| 写设计报告 | [`../report/README.md`](../report/README.md) |
