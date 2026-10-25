# 提交前检查清单（2026-10-25）

> 本清单按指南 **3.3.5** 的四类提交物逐项核对。勾选状态以**仓库内实际产物**为准，
> 不以口头说明为准；每个“✅”后面都能在仓库里找到文件。

## 1. 工程包（指南 3.3.5.1）

| 项 | 产物 | 状态 |
|---|---|---|
| 完整工程与源码 | `src/`（HLS、PYNQ 驱动、host 脚本） | ✅ |
| 仿真/验证结果 | `sim/`、`board/logs/board_jtag_layer_test_20261020.md`（6/6 bit-exact） | ✅ |
| 综合/实现报告（资源/时序/性能） | `build/reports/resource/*.rpt`、`build/reports/timing/*.rpt`、`build/reports/drc/*.rpt` | ✅ |
| 上板工程/脚本/实测输出 | `board/overlay/*.bit/.hwh`、`board/scripts/`、`board/logs/` | ✅ |
| 可复现构建脚本 | `build/scripts/bd_preproc.tcl`、`bd_conv_engine.tcl`、`bd_axi_bw.tcl`、`bd_mask_preproc.tcl` | ✅ |
| 运行必需件（模型/标定） | `build/models/person_v8n25.{param,bin,table}`、`build/models/ball_roi.npz`、`data/calib/*.json` | ✅（`tools/make_submission.py` 显式带入） |

## 2. 技能包（指南 3.3.5.2）

| 项 | 产物 | 状态 |
|---|---|---|
| 通用 PYNQ（加分项） | `skill/pynq_overlay_bringup/`（README + PITFALLS + prompts + checks） | ✅ |
| HLS/AXI 通用 | `skill/hls_axi_stream_accel/`（README + PITFALLS） | ✅ |
| 每包五节齐全 | 适用范围 / 使用方法 / 失效条件 / 提炼来源 / 已验证效果 | ✅ |
| 清单回填 | `skill/README.md` 清单表 | ✅ |

## 3. 设计报告（指南 3.3.5.3，7 项）

| # | 项 | 落点 | 状态 |
|---|---|---|---|
| 1 | 选题背景与创新点 | `report/design_report.md` §1 | ✅ |
| 2 | 设计原理与功能框图 | §2 + `report/figures/system_arch.png`、`software_pipeline.png` | ✅ |
| 3 | 软硬件划分依据与接口设计 | §3 + `docs/architecture.md` | ✅ |
| 4 | 优化过程（前后性能/资源对比） | §4（基线行 + 优化行 + 出处） | ✅ |
| 5 | 大模型协作记录 | `report/llm_logs/01–03` + 索引 | ✅ |
| 6 | 技能包提炼过程 | §6 + `skill/` | ✅ |
| 7 | 复现说明 | §7 + 根 `README.md` §5 | ✅ |

## 4. 目录与命名（指南 3.3.5.4）

| 项 | 判据 | 状态 |
|---|---|---|
| 推荐目录结构 | `README.md` / `src` / `sim` / `build` / `board` / `data` / `skill` / `report` | ✅ |
| 文件名纯 ASCII | `python tools/check_repo.py` a) | ✅ |
| 每目录 README | 同上 c) | ✅ |
| 无禁入文件 | 同上 d) | ✅ |

## 5. 评测权重（指南 3.3.5.5）

`report/README.md` 预置表 2 含 30/20/20/15/15；`check_repo.py` e) 校验。 ✅

## 6. 提交前必须跑的 4 条命令

```bash
python tools/check_repo.py          # 末行 CHECK_REPO_OK
python tools/make_report_figures.py # 3 张图确定性重生成
python tools/md_to_docx.py          # report/design_report.docx
python tools/make_submission.py     # 末行 SUBMISSION_PACK_OK；dist/ 下 zip
```

当前实测：

- `check_repo.py`：`SUMMARY: 6 PASS / 0 FAIL / 0 WARN`，`CHECK_REPO_OK`；
- `make_submission.py`：459 files / 59,129,579 bytes，zip 约 17.7 MB，导出目录自检通过。

## 7. 已知开放项（不阻塞提交，但必须如实写在报告里）

1. 球在快速移动/遮挡时确认率下降；backfill 结构改动未落地（§8 第 2 条、`ai/sessions/010`）；
2. 倒地识别是候选粗筛（132 候选 / 人工复核 26 / 3 真），不宣称精度/召回（§8 第 4 条）；
3. 单应只对已标定机位有效，多机位需各标一次（§8 第 1 条）；
4. 工具链是 Vivado 2020.2，与指南推荐 2026.1/2025.2 不同；已参数化并登记差异（§7.6）。

## 8. 签字

- 报告版本：`design_report.md` v1.0（2026-10-25）
- 提交包：`dist/zynq-yolo_submission_<YYYYMMDD_HHMM>/` + 同名 `.zip`
- 校验：`SUBMISSION_MANIFEST.csv` 的 `sha256` 列逐文件可核。
