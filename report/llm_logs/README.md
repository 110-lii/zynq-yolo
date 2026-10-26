# 大模型协作记录（提交件成品层）

> 上级：[`../../README.md`](../../README.md) · 设计报告：[`../design_report.md`](../design_report.md)

本目录是从 [`../../ai/`](../../ai/) **过程原始层**中挑出、整理成篇的提交件。
每条记录都写明它回指 `ai/` 的哪个文件/哪一节；只放结论、指不回原始记录的，不算协作记录。

| # | 主题 | 原始出处 | 对应报告节 |
|---|---|---|---|
| [01](01_conv_engine_optimization.md) | PL 卷积引擎：字节访存 → 32-bit 字访存，20.8 s → 9.63 s | `ai/sessions/005`、`ai/sessions/008` | §4.1 / §4.2 |
| [02](02_route_selection_and_preproc.md) | 路线选择：PL 掩码为何不划算、为什么最终选 PL 预处理 + PS ncnn | `ai/sessions/007`、`ai/sessions/009` | §4.3 / §4.5 / §5.2 |
| [03](03_agent_workflow_and_evidence.md) | 智能体工作流与证据纪律：写范围、判据、自检门禁 | `ai/sessions/001`、`ai/sessions/002`、`ai/coordination/DECISIONS.md` | §5.3 / §6 |
| [04](04_extensions_storage_display.md) | 提交前扩展：PL 跟踪辅助、SD 存储、PC 软件、HDMI/LCD 面板 | 本次会话（2026-10-25） | §2.9 / §4.7 / §8 |

## 整理规则

1. **可回指**：每条结论后面写 `出处：ai/sessions/...`；
2. **保留失败**：只写“成功了”的记录不算协作记录；每篇至少一条**自我纠错**，
   写清“错在哪 → 怎么发现 → 证据 → 怎么改 → 判据”；
3. **不编造**：模型回答只摘录 `ai/sessions/` 中已有的要点；用户提示词逐字引用时加引号；
4. **区分原始层与成品层**：`ai/` 不重写、只追加；本目录可反复整理。
