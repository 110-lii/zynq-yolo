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
| PL 跟踪辅助源码/BD | `src/hls/track/`、`build/scripts/bd_track_preproc.tcl`、`tools/track_preproc.py`、`board/overlay/track_preproc.bit` | ✅ 已出片；✅ 上板逐位对通过（`TRACK_PREPROC_BOARD_OK`） |
| HDMI/LCD/PC 方案 | `docs/display_overlay_plan.md`、`tools/status_display.py`、`tools/pc_dashboard.py`、`board/overlay/hdmi_status.bit` | ✅ HDMI/LCD 已出片；✅ 帧缓存数字通路读回通过；✅ **画面已通过**（014：两屏棋盘格均可见） |

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
| 5 | 大模型协作记录 | `report/llm_logs/01–04` + 索引 | ✅ |
| 6 | 技能包提炼过程 | §6 + `skill/` | ✅ |
| 7 | 复现说明 | §7 + 根 `README.md` §5 | ✅ |

## 3.1 提交前扩展功能（D018–D025）

| 项 | 产物 | 状态 |
|---|---|---|
| SD 增量存储 | `tools/result_store.py`（JSONL+summary+fsync+轮转） | ✅ 自检通过 |
| 板上→PC 推送 | `tools/result_stream.py` | ✅ 本地回环通过 |
| PC 端软件 | `tools/pc_dashboard.py`（TCP/回放+球场+CSV） | ✅ 自检/回放通过 |
| HDMI/LCD 状态面板 | `tools/status_display.py`（fbdev/PNG） | ✅ 自检通过；硬件看 fbdev |
| PL 跟踪辅助 | `src/hls/track/` + `build/scripts/bd_track_preproc.tcl` + `board/overlay/track_preproc.bit` + `tools/track_preproc.py` | ✅ 已出片（WNS +2.457 ns）；✅ 上板逐位对通过（720p 32.39 ms） |
| HDMI 状态面板 | `src/rtl/io/hdmi/` + `build/scripts/bd_hdmi_status.tcl` + `tools/bram_fb.py` | ✅ 已出片（Setup +3.938 ns）；✅ 数字通路读回通过；✅ 画面已通过（014） |
| LCD 状态面板 | `src/rtl/io/lcd/` + `build/scripts/bd_lcd_status.tcl` + `tools/bram_fb.py` | ✅ 已出片（Setup +4.283 ns）；✅ 数字通路读回通过；✅ 画面已通过（014） |
| HDMI+LCD 合并 | `build/scripts/bd_display_both.tcl` + `board/overlay/display_both.bit` | ✅ 已出片（Setup +3.874 ns）；✅ `DISPLAY_BOTH_BOARD_OK`（数字通路）；⚠️ 合并版待重出片（014 只修单屏） |
| 跟踪+双屏合并 | `build/scripts/bd_track_display.tcl` + `board/overlay/track_display.bit` | ✅ 已出片（Setup +2.918 ns）；✅ `TRACK_DISPLAY_BOARD_OK`（BRAM 读回）；⚠️ 合并版待重出片（014 只修单屏） |
| 板上管线 + SD + PC | `board_pipeline.py --pl-track` 20 帧；`result_stream` → Dashboard | ✅ `PIPELINE_OK`、20 行 JSONL、`PC_DASHBOARD_OK frames=20` |
| 决策登记 | D018/D019/D020/D021/D022/D023/D024/D025 | ✅ |

## 4. 目录与命名（指南 3.3.5.4）

| 项 | 判据 | 状态 |
|---|---|---|
| 推荐目录结构 | `README.md` / `src` / `sim` / `build` / `board` / `data` / `skill` / `report` | ✅ |
| 文件名纯 ASCII | `python tools/check_repo.py` a) | ✅ |
| 每目录 README | 同上 c) | ✅ |
| 无禁入文件 | 同上 d) | ✅ |

## 5. 评测权重（指南 3.3.5.5）

`report/README.md` 预置表 2 含 30/20/20/15/15；`check_repo.py` e) 校验。 ✅

## 6. 提交前必须跑的命令

```bash
python tools/check_repo.py          # 末行 CHECK_REPO_OK
python tools/check_track_preproc.py # TRACK_PREPROC_REF OK（PL 算法对拍）
python tools/result_store.py --selftest
python tools/pc_dashboard.py --selftest
python tools/status_display.py --selftest
python tools/bram_fb.py --selftest
python tools/make_report_figures.py # 6 张图确定性重生成
python tools/md_to_docx.py          # report/design_report.docx
python tools/make_submission.py     # 末行 SUBMISSION_PACK_OK；dist/ 下 zip
```

当前实测：

- `check_repo.py`：`SUMMARY: 6 PASS / 0 FAIL / 0 WARN`，`CHECK_REPO_OK`；
- `make_submission.py`：539 files（约 84.3 MB），zip 约 19.3 MB，导出目录自检通过。

## 7. 已知开放项（不阻塞提交，但必须如实写在报告里）

1. 球在快速移动/遮挡时确认率下降；backfill 结构改动未落地（§8 第 2 条、`ai/sessions/010`）；
   `--ball-lost-sec 0.4` 已试并否决：球帧 144→182（+26.4 %）但新增 38 帧人工抽查全非球，
   开关默认关闭（`ai/sessions/012`）；
2. 倒地识别是候选粗筛（132 候选 / 人工复核 26 / 3 真；4 帧门槛后强层 4 候选 = 3 真 1 假，
   精度 **3/4**），不宣称召回、也不把候选数当检出数（§8 第 4 条）；
3. 单应只对已标定机位有效，多机位需各标一次（§8 第 1 条）；
4. 工具链是 Vivado 2020.2，与指南推荐 2026.1/2025.2 不同；已参数化并登记差异（§7.6）；
5. **显示面板未通过**：HDMI/LCD 上板均无画面，`DISPLAY_BOARD_OK` 只证明帧缓存数字通路
   （§8 第 8 条、`ai/sessions/013`）；HDMI 疑物理层故障、LCD overlay 有 bug，均未修复。

## 8. 签字

- 报告版本：`design_report.md` v1.0（2026-10-25）
- 提交包：`dist/zynq-yolo_submission_<YYYYMMDD_HHMM>/` + 同名 `.zip`
- 校验：`SUBMISSION_MANIFEST.csv` 的 `sha256` 列逐文件可核。
