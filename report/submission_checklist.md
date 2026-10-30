# 提交前检查清单（2026-10-30）

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
| HDMI/LCD/PC 方案 | `docs/display_overlay_plan.md`、`docs/hdmi_video_osd.md`、`tools/status_display.py`、`tools/pc_dashboard.py`、`tools/hdmi_video.py`、`board/overlay/*.bit` | 软件 ✅；HDMI 状态面板 **`BRAM_OK` 通过 / `VISUAL_OK` 待人眼**；LCD 4bpp 新片 **未上板**；HDMI 带框环出 **未上板**（见 §3.1、§3.2） |

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
| HDMI 状态面板 | `src/rtl/io/hdmi/` + `build/scripts/bd_hdmi_status.tcl` + `tools/bram_fb.py` | 已出片；✅ `BRAM_OK`（整场仿真 + 上板 `HDMI_LIVE`）；⏳ `VISUAL_OK` 待人眼 |
| LCD 状态面板（10-29 重做） | `src/rtl/io/lcd/lcd_probe_*.v` + `build/scripts/bd_lcd_probe.tcl` + `tools/check_lcd4_board.py` | 已出片（4bpp/16 色/按键；WNS +1.839 ns）；⏳ 未上板（两级判据都没做） |
| HDMI+LCD 合并 | `build/scripts/bd_display_both.tcl` + `board/overlay/display_both.bit` | ⚠️ 比特流落后于 018–021 的新读侧，需重出片 |
| 跟踪+双屏合并 | `build/scripts/bd_track_display.tcl` + `board/overlay/track_display.bit` | ✅ `TRACK_PREPROC_BOARD_OK`（逐字节对拍）；⚠️ 合并画面同上门槛 |
| 板上管线 + SD + PC | `board_pipeline.py --pl-track` 20 帧；`result_stream` → Dashboard | ✅ `PIPELINE_OK`、20 行 JSONL、`PC_DASHBOARD_OK frames=20` |
| 决策登记 | D018/D019/D020/D021/D022/D023/D024/D025/D026 | ✅ |

## 3.2 交付前补齐（2026-10-27，D026）

| 项 | 产物 | 状态（机器判据） | 上板 |
|---|---|---|---|
| SD 证据帧落盘 | `tools/result_store.py::save_evidence`、`board_pipeline --store-evidence` | ✅ `RESULT_STORE_OK`（含每分钟限流 6 张） | ⛔ 待板 |
| PC 上位机界面 | `tools/pc_dashboard.py`（状态条 + 球场 + 事件流 + 证据帧 + 12 列 CSV） | ✅ `PC_DASHBOARD_SELFTEST OK`；界面 PNG 已人眼核对 | 无需板 |
| LCD 状态面板信息 | `tools/status_display.py`（8 项，800x480 / 1280x720） | ✅ `STATUS_DISPLAY_SELFTEST OK`；两尺寸 PNG 已人眼核对 | ⛔ 待板（fbdev/BRAM 通路） |
| HDMI 视频环出（带框画面） | `src/rtl/io/hdmi/fb_scanout.v`+`hdmi_video_top.v`、`build/scripts/bd_hdmi_video.tcl`、`tools/hdmi_video.py` | ✅ `TB_FB_SCANOUT_OK`（2048 像素逐点 0 错）；✅ PS 侧 PNG 预览 | ⛔ 待板（bit 出片后先跑彩条） |

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
- `make_submission.py`：**文件数与大小以当天最后一次生成为准**，正文见 `dist/` 下同名 `.zip`
  与包内 `SUBMISSION_MANIFEST.csv`（本表不再抄具体数字，避免像 10-25 版那样与实物不符）；
- 新增自检（2026-10-30）：`py tools/check_full_flow.py --dry-run` → `FULL_FLOW_OK ok=6 skip=1 fail=0`（含队别投票步）；
  `py sim/scripts/run_sim.py` → `SIM_PASS 9/9`。

## 7. 已知开放项（不阻塞提交，但必须如实写在报告里）

1. 球在快速移动/遮挡时确认率下降；backfill 结构改动未落地（§8 第 2 条、`ai/sessions/010`）；
   `--ball-lost-sec 0.4` 已试并否决：球帧 144→182（+26.4 %）但新增 38 帧人工抽查全非球，
   开关默认关闭（`ai/sessions/012`）；
2. 倒地识别是候选粗筛（132 候选 / 人工复核 26 / 3 真；4 帧门槛后强层 4 候选 = 3 真 1 假，
   精度 **3/4**），不宣称召回、也不把候选数当检出数（§8 第 4 条）；
3. 单应只对已标定机位有效，多机位需各标一次（§8 第 1 条）；
4. 工具链是 Vivado 2020.2，与指南推荐 2026.1/2025.2 不同；已参数化并登记差异（§7.6）；
5. **显示按两级判据如实登记**（2026-10-30 更正，取代此前「未通过/已通过」两种互相打架的写法）：
   - `BRAM_OK`（写得进、读得回、扫描范围/速率对）：**HDMI 状态面板已过**；
     **LCD 4bpp 新片未上板**，这一级也没做。
   - `VISUAL_OK`（人眼看画面）：014 在 1bpp 单屏上见过棋盘格，但 018–021 重写读侧并把 LCD
     整体重做为 4bpp/16 色/按键之后**未再复看** ⇒ **当前不宣称任何一块屏「画面通过」**。
   - `display_both.bit` / `track_display.bit` 落后于新读侧，需重出片。
6. **视频入口未落地（D015）**：`board_pipeline.py` 仍只吃板本地 `--video`；网络接收端
   （`stream_sender.py` / `board_net.py`）是早期 bring-up，未接主线；协议/端口在台账仍 `discussing`。
7. **队别已接入主线、未验证**：`tools/team_assign.py` + `board_pipeline --ui-config` 输出
   `players[*].team` 与 `events.possession_team`（主机侧 `TEAM_ASSIGN_SELFTEST OK`）；
   **真实画面与板上未验**；离线 `track_players.py` 仍不输出队别（像素坐标、未接单应）。
8. **数据集来源与许可（D005）仍 `discussing`**：`data/` 内有比赛片段与训练集，
   但仓库里没有来源/许可声明；提交包只带运行必需件（模型/标定），不改变该状态。

## 8. 签字

- 报告版本：`design_report.md` **v1.1（2026-10-30）**；`design_report.docx` 由同一 md 重新生成
- 提交包：`dist/zynq-yolo_submission_<YYYYMMDD_HHMM>/` + 同名 `.zip`（旧包删除，只留当天一份）
- 校验：`SUBMISSION_MANIFEST.csv` 的 `sha256` 列逐文件可核
