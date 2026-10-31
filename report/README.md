# report/ —— 设计报告 + 大模型协作记录（提交件成品层）

> 上级：[`../README.md`](../README.md)

| | |
|---|---|
| 本目录装什么 | 提交给评委的《设计报告》正稿、**大模型协作记录（整理成提交件的成品）**、框图与截图 |
| 谁产生 | 手写；协作记录由 `ai/` 过程原始层整理而来 |
| 进 git | 是（「大模型协作与技能包」15 分 +「文档质量与可复现性」15 分都落在本目录） |

---

## 内容

| 路径 | 说明 |
|---|---|
| `README.md` | 本文件：报告必须覆盖的 7 项 + 两张预置表 + 本目录规则 |
| `design_report.md` | **设计报告正稿（Markdown 源）**：§1–§7 对应指南 3.3.5.3 的 7 项，§8 已知边界，§9 交付物对照 |
| `design_report.docx` | 由 `tools/md_to_docx.py` 从 Markdown 生成的提交用文档 |
| `technical_document.md` / `.docx` / `.pdf` | **赛事门户技术文档**（按官方模板的五部分结构：作品概述 / 系统组成 / 完成情况 / 总结 / 参考文献）；docx 由 `tools/md_to_docx.py` 生成，PDF 由 `tools/make_portal_assets.py --pdf` 生成 |
| `submission_checklist.md` | 提交前逐项核对清单（工程包/技能包/报告/目录/权重 + 4 条命令） |
| `llm_logs/` | **大模型协作记录（提交件成品）**：6 篇成稿 + 索引，逐条回指 `ai/sessions/` |
| `figures/` | `system_arch.png`、`software_pipeline.png`、`optimization.png`、`status_panel_lcd.png`、`status_panel_hdmi.png`、`pc_pitch.png`（由 `tools/make_report_figures.py` 确定性生成）；`pc_dashboard_video_ball.png`、`lcd_panel_ball_20261031.png`、`hdmi_osd_boxes.png`（由板上 JSONL 实帧渲染，非合成） |

### `report/llm_logs/` 与 `ai/` 的分工（**勿混**）

| 目录 | 层次 | 读者 | 内容形态 | 能改吗 |
|---|---|---|---|---|
| `ai/` | **过程原始层**：逐条会话、原始提示词与模型回答、协作协调文件 | 本队自己（可追溯、可复盘） | 原样留存，不修饰 | ✅ 追加，不重写 |
| `report/llm_logs/` | **提交件成品层**：从 `ai/` 里挑出并整理过的记录 | 评委（陌生人） | 成篇、可读、每条有结论与教训 | ✅ 可反复整理 |

> 硬规则：`report/llm_logs/` 的每一条记录**必须能指回 `ai/` 中的原始出处**（写清 `ai/sessions/...` 哪个文件、哪一段）。
> 只放结论、指不回原始记录的，不算协作记录，**不计入本目录**。

---

## 设计报告必须覆盖的 7 项（指南 3.3.5.3，缺一即不完整）

| # | 必须覆盖 | 指南原文要点（`text 964-971`） | 本仓库落点 | 状态 |
|---|---|---|---|---|
| 1 | 选题背景与创新点 | 为什么选这个题目，它解决了什么实际问题，前人做过什么 | `design_report.md` §1 | ✅ 已完成 |
| 2 | 设计原理与功能框图 | 原理阐述 + 功能框图 | `design_report.md` §2 + `figures/` | ✅ 已完成 |
| 3 | 软硬件划分依据与接口设计 | 为什么这样划分；AXI/寄存器等接口契约 | `design_report.md` §3 + `../docs/architecture.md` | ✅ 已完成（D017） |
| 4 | 优化过程（含优化前后性能与资源对比表） | 必须给出优化**前后**的性能与资源对比 | `design_report.md` §4 | ✅ 已完成（基线与优化行均标出处） |
| 5 | **大模型协作记录** | 提示词、模型回答、**自我纠错轨迹**、智能体工作流设计（如有） | `llm_logs/`（成品）← `../ai/`（原始） | ✅ 已完成（6 篇 + 索引，覆盖 001–022 会话） |
| 6 | 技能包的提炼过程 | 从哪些失败或观察中总结、如何验证有效、边界如何确定 | `design_report.md` §6 + `../skill/` | ✅ 已完成（2 个技能包） |
| 7 | 复现说明 | 一份可供他人**从零开始完整执行**的操作步骤 | `design_report.md` §7 + 根 `README.md` | ✅ 已完成 |

> 报告正文：`design_report.md`（Markdown 源，**v1.3 · 2026-10-31**）+ `design_report.docx`（由
> `tools/md_to_docx.py` 生成）。赛事门户另要一份按官方模板排版的技术文档：
> `technical_document.md` → `.docx` → `.pdf`（由 `tools/make_portal_assets.py --pdf` 转换）。
> 已裁定项以 `../ai/coordination/DECISIONS.md` 为准；
> **D003（加速器实现方式）、D005（数据集许可）、D006（AXI 契约数值）、D007（旧项目复用范围）
> 已于 2026-10-30 由用户逐条裁定**（混合路线 / 内部使用声明 / 冻结 v1 / 只复用引脚资料），
> 报告按裁定口径书写。未测量/未保留的数字在正文中
> 明确写“未测量/未保留”，不编造。

---

## 预置表 1：优化前后对比表（已填；详细分析见 `design_report.md` §4）

| 改动 | 资源 LUT / FF / DSP / BRAM | WNS | 吞吐或帧率 | 出处 |
|---|---|---|---|---|
| 基线：`conv_engine` 字节访存（005 收敛版） | 13,076 / 未记录 / 162 / 9.5 | **+1.254 ns** | **20.8 s/帧** | `ai/sessions/005`、`board/logs/board_stream_new_20261022.md` |
| 优化：`conv_engine` 32-bit 字访存（008） | 23,552 / 21,991 / 172 / 12.5 | **+1.350 ns @83.33 MHz** | **9.63 s/帧（≈0.10 FPS）** | `build/reports/resource/conv_engine_bd_util.rpt`、`build/reports/timing/conv_engine_bd_timing.rpt`、`board/logs/board_stream_new_20261022.md` |
| 最终主线：PL `preproc` + PS ncnn YOLOv8n-0.25@320 | 4,736 / 5,260 / 13 / 1.5（BD 顶层） | **+3.724 ns** | PL 预处理 **18.8 ms**；PS 检测 **0.95–1.0 s/帧** | `build/reports/resource/preproc_bd_util.rpt`、`build/reports/timing/preproc_bd_timing.rpt`、`ai/sessions/009` |

**填表纪律（承 `docs/repo_spec.md` §6「数字必须标来源」）：**

1. 表中**每一行的每一个数字**都必须填「出处」，出处只能是以下三种之一：
   - `docs/repo_spec.md` §6 环境事实表（器件/资源上限等静态事实）；
   - `build/reports/` 下的综合 / 实现 / 时序报告（`DONE_OK` 令牌，承 §5）；
   - `board/logs/` 下的实测日志或实测记录（**不能由脚本重建，必须落盘**，承 §4）。
2. 查不到出处的数字**不得写入本表**；宁可留 `待填`。
3. 第 1 行必须是**基线（优化前）**，否则不成「优化前后对比」。
4. 资源列按 `LUT / FF / DSP / BRAM` 四项分列填写；BRAM 写 `RAMB36` 个数或 Mb。
5. WNS 单位 ns，须写明时钟约束与时钟频率（Vivado 版本一并写，本机为 2020.2，承 §6）。

---

## 预置表 2：评测指标权重表（照抄指南 3.3.5.5）

| 评测项 | 权重 |
|---|---|
| 创新性与应用价值 | 30 |
| 性能与资源优化效果 | 20 |
| 功能正确性与设计完整性 | 20 |
| 大模型协作与技能包 | 15 |
| 文档质量与可复现性 | 15 |
| **合计** | **100** |

> 原文说明（`text 1008-1010` 逐字）：「两组采用同一套评测项与权重，但分组独立排名、独立评奖，评分时结合各自组别的考察侧重把握尺度。各项均含评委评定成分；**功能正确性与可复现性两项**以提交物核查与实测数据为主要依据。」
> 注意原文这句**简称**了表内两项：其一是「功能正确性**与设计完整性**」，其二是「文档质量**与可复现性**」—— 两处措辞不完全一致，本仓库引用时按表格全称，不按这句简称。
> 推论：`report/`（15 分）与 `skill/`+`ai/`（15 分）合计 30 分，与实现本身同等重要。

---

## 规则

- **命名**：本目录及子目录名、文件名一律纯 ASCII 小写，只用 `a-z 0-9 _ - .`（指南 3.3.5.4）；中文只出现在文件正文。
- **数字**：任何进入本目录的数字必须能标出来源（§6）；不得写「大概/约」类无出处数字。
- **产物**：`.dcp` / `.bit` / `*.rpt` / `*.log` 等可重建产物不进本目录，只进 `build/reports/`、`board/logs/`。
- **不编造**：题目、算法形态、数据集、接口数值等未裁定项一律写 `<待用户裁定：见 docs/repo_spec.md §7>`。
- **自检**：每次提交前跑 `python tools/check_repo.py`，末行须出现 `CHECK_REPO_OK`。

---

## 怎么用

```powershell
cd D:\zynq-yolo
python tools\check_repo.py        # 仓库自检；末行看 SUMMARY / CHECK_REPO_OK
```

陌生人从零复现本目录内容：

1. 先读 `..\docs\repo_spec.md`（唯一事实源）与 `..\README.md`（项目简介 + 复现步骤）。
2. 按上表 7 项逐条写报告正文；每完成一项，把「状态」列改成 `已完成`。
3. 优化对比表每行补「出处」；用 `..\build\scripts\` 下的脚本重建 `build\reports\`，用 `..\board\scripts\` 下的脚本重跑实测并落盘 `board\logs\`。
4. 从 `..\ai\` 挑记录整理进 `llm_logs\`，逐条标注回到 `ai/` 的出处。
5. 提交前跑 `python tools\check_repo.py`，确认末行 `CHECK_REPO_OK`。
