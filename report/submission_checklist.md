# 提交前检查清单（2026-10-31）

> 本清单按指南 **3.3.5** 的四类提交物与**赛事门户的七类上传件**逐项核对。勾选状态以
> **仓库内实际产物**为准，不以口头说明为准；每个“✅”后面都能在仓库里找到文件。

## 0. 赛事门户上传件（2026-10-31 截图口径）

| # | 上传项 | 门户要求 | 当前产物 | 状态 |
|---|---|---|---|---|
| 1 | 演示视频 | MP4 / AVC(H.264) / ≤3 min / ≤300 MB | **尚无**；分镜脚本已出（`report/demo_video_storyboard.md`） | ⛔ **待拍摄/剪辑**（需板子与屏的实拍，脚本可照拍） |
| 2 | 技术文档 | PDF / ≤50 MB | `report/technical_document.docx` + `.pdf`（0.9 MB，2026-10-31 22:1x 按修订后正文重生成） | ⚠️ **正文已复核并改掉已撤回的 `HDMI_VISUAL_OK`**；实物照片仍待补（文中 `【待补…】` 标记） |
| 3 | 全部代码 | RAR / ≤200 MB | `dist/zynq-yolo_submission_20261031_2211.rar`（23.2 MB）+ 同名 `.zip`（28.8 MB） | ✅ **已出包**（`SUBMISSION_PACK_OK`，678 文件） |
| 4 | 承诺及使用授权书 | PDF / ≤50 MB | 官方空白模板已给（`D:\xiazai\…pdf`） | ⛔ **需打印、全体签名、扫描上传**（只能人工） |
| 5 | 作品海报（选填） | PDF / PPT / ≤50 MB | 尚无 | ⏳ 选填，可后补 |
| 6 | GitHub 链接 | 仓库地址 | `https://github.com/110-lii/zynq-yolo.git` | ✅ **已 push**，`ahead_of_origin_main=0` |
| 7 | 作品全貌图 | 系统全貌 | `report/figures/system_arch.png` 是框图，不是实物照 | ⛔ **待拍摄**（正面 + 斜 45°，只能人工） |

> 门户红字：视频、技术文档**不得出现学校名称、指导教师姓名**等与作品无关的信息。
> 仓库正文已核对无此类信息；视频与实物照片拍摄时同样注意。

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
| HDMI/LCD/PC 方案 | `docs/display_overlay_plan.md`、`docs/flow_overlay.md`、`docs/hdmi_video_osd.md`、`tools/status_display.py`、`tools/pc_dashboard.py`、`tools/hdmi_video.py`、`board/overlay/*.bit` | 软件 ✅；HDMI 状态面板 **`BRAM_OK` 通过 / 1bpp `VISUAL_OK` 待人眼**；LCD 4bpp **上板 `READBACK_OK bad=0` 且自证图/彩色面板/赛前 UI 人眼确认**；PC 面板 ✅ 含比赛画面视图（`--video`）；HDMI 带框环出 **`BRAM_OK` 通过、`VISUAL_OK` 不成立**（13:56 曾记 `HDMI_VISUAL_OK`，20:23 复测出现整行循环移位，**当日撤回**；今日定量为恒定 `+313` 列，间歇缺陷）——**演示不走 HDMI**（见 §3.1、§3.2、§7.5） |

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
| 5 | 大模型协作记录 | `report/llm_logs/01–06` + 索引 | ✅ |
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
| LCD 状态面板（10-29 重做） | `src/rtl/io/lcd/lcd_probe_*.v` + `build/scripts/bd_lcd_probe.tcl` + `tools/check_lcd4_board.py` | 已出片（4bpp/16 色/按键；WNS +1.839 ns）；✅ **上板 `READBACK_OK bad=0 checked=10`**（10-30，两次一致）；✅ 自证图 `tag4` 人眼确认；⏳ 彩色面板/赛前 UI 画面待人眼 |
| HDMI+LCD 合并（现行） | `build/scripts/bd_flow_system.tcl` + `board/overlay/flow_system.bit` | ✅ `FLOW_SYSTEM_BD_OK`（10-30：WNS +3.042 ns、LUT 44.9%、BRAM 86/140）+ 10-31 修复片 **WNS +4.184 ns**；✅ 上板 `flow_overlay.py --selftest`：LCD `bad=0/16`、HDMI `frames +492 under=0`；✅ **真视频环出 `HDMI_VIDEO_OK` 25 帧 `under=0`**（机器级）。❌ **静态六屏 `HDMI_VISUAL_OK` 已于 20:23 撤回**（13:56 人眼记过，同片复测出现整行循环移位；今日定量恒定 `+313` 列，间歇缺陷，sha256 仍为 `ad628200…104a`）；实时叠框回放仍无人眼结论 |
| HDMI+LCD 合并（旧） | `build/scripts/bd_display_both.tcl` + `board/overlay/display_both.bit` | ❌ 已由 `flow_system` 取代：1bpp 文字方案，且落后于 018–021 的新读侧，不再重出片 |
| 跟踪+双屏合并 | `build/scripts/bd_track_display.tcl` + `board/overlay/track_display.bit` | ✅ `TRACK_PREPROC_BOARD_OK`（逐字节对拍）；⚠️ 合并画面同上门槛 |
| 板上管线 + SD + PC | `board_pipeline.py --pl-track` 20 帧；`result_stream` → Dashboard | ✅ `PIPELINE_OK`、20 行 JSONL、`PC_DASHBOARD_OK frames=20` |
| 决策登记 | D018/D019/D020/D021/D022/D023/D024/D025/D026 | ✅ |

## 3.2 交付前补齐（2026-10-27，D026）

| 项 | 产物 | 状态（机器判据） | 上板 |
|---|---|---|---|
| SD 证据帧落盘 | `tools/result_store.py::save_evidence`、`board_pipeline --store-evidence` | ✅ `RESULT_STORE_OK`（含每分钟限流 6 张） | ⛔ 待板 |
| PC 上位机界面 | `tools/pc_dashboard.py`（状态条 + 球场 + 事件流 + 证据帧 + 12 列 CSV） | ✅ `PC_DASHBOARD_SELFTEST OK`；界面 PNG 已人眼核对 | 无需板 |
| LCD 状态面板信息 | `tools/status_display.py`（8 项，800x480 / 1280x720） | ✅ `STATUS_DISPLAY_SELFTEST OK`；两尺寸 PNG 已人眼核对 | ⛔ 待板（fbdev/BRAM 通路） |
| HDMI 视频环出（带框画面） | `src/rtl/io/hdmi/fb_scanout.v`+`hdmi_video_top.v`、`build/scripts/bd_hdmi_video.tcl`、`tools/hdmi_video.py` | ✅ `TB_FB_SCANOUT_OK`（2048 像素逐点 0 错）；✅ PS 侧 PNG 预览；✅ **真视频 `HDMI_VIDEO_OK` 25 帧 `under=0`（机器级）**；❌ **静态六屏 `HDMI_VISUAL_OK` 已撤回**（20:23 复现整行循环移位；`under=0` 与累加和在整行移位时照样成立，不构成画面正确的证据） | ❌ **上板画面不成立**：恒定 `+313` 列整行旋转。实时叠框回放亦未做人眼确认 |

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
python tools/make_portal_assets.py --pdf    # report/technical_document.pdf（≤50 MB）
python tools/make_portal_assets.py --rar dist/zynq-yolo_submission_<stamp> --rar-exe <Rar.exe>  # ≤200 MB
python tools/make_portal_assets.py --check --video <demo.mp4> --auth <授权书.pdf> --rar <代码.rar>
```

当前实测：

- `check_repo.py`：`SUMMARY: 6 PASS / 0 FAIL / 0 WARN`，`CHECK_REPO_OK`；
- `make_submission.py`：**文件数与大小以当天最后一次生成为准**，正文见 `dist/` 下同名 `.zip`
  与包内 `SUBMISSION_MANIFEST.csv`（本表不再抄具体数字，避免像 10-25 版那样与实物不符）；
- 新增自检（2026-10-30）：`py tools/check_full_flow.py --dry-run` → `FULL_FLOW_OK ok=6 skip=1 fail=0`（含队别投票步）；
  **接板** `py tools/check_full_flow.py --board` → **`FULL_FLOW_OK ok=8 skip=0 fail=0`**（新增 deploy 步）；
  `py sim/scripts/run_sim.py` → `SIM_PASS 9/9`。

## 7. 已知开放项（不阻塞提交，但必须如实写在报告里）

1. 球在快速移动/遮挡时确认率下降；backfill 结构改动未落地（§8 第 2 条、`ai/sessions/010`）；
   `--ball-lost-sec 0.4` 已试并否决：球帧 144→182（+26.4 %）但新增 38 帧人工抽查全非球，
   开关默认关闭（`ai/sessions/012`）；
2. 倒地识别是候选粗筛（132 候选 / 人工复核 26 / 3 真；4 帧门槛后强层 4 候选 = 3 真 1 假，
   精度 **3/4**），不宣称召回、也不把候选数当检出数（§8 第 4 条）；
3. 单应只对已标定机位有效，多机位需各标一次（§8 第 1 条）；
4. 工具链是 Vivado 2020.2，与指南推荐 2026.1/2025.2 不同；已参数化并登记差异（§7.6）；
5. **显示按两级判据如实登记**（2026-10-31 更新）：
   - `BRAM_OK`（写得进、读得回、扫描范围/速率对）：**HDMI 状态面板已过**；
     **LCD 4bpp 上板已过**（`READBACK_OK bad=0 checked=10`，PCLK 33.335 MHz）；
     **HDMI 带框环出已过**（`HDMI_VIDEO_BOARD_OK frames=313 under=0`）。
   - `VISUAL_OK`（人眼看画面）：LCD **自证图 `tag4`、彩色状态面板、赛前 UI（真按键）已人眼确认**；
     **HDMI 带框环出静态六屏 `HDMI_VISUAL_OK` 已于当日 20:23 撤回** —— 13:56 人眼记过一次
     （证据当时在 `data/results/hdmi_visual_ok.json`），同片复测又出现整行循环移位
     （`px = c + 437 / +441`），今日定量为恒定 `+313` 列；证据文件现为 `HDMI_VISUAL_RETRACTED`。
     ⇒ **HDMI 侧 `VISUAL_OK` 不成立**，且 `under=0`、累加和、「每源列 1 次」这三类判据
     对整行旋转都是瞎的，不能拿来替代人眼。
     **1bpp HDMI 状态面板与实时叠框回放仍未人眼确认**，不宣称「画面通过」。
   - `display_both.bit` / `track_display.bit` 落后于新读侧，需重出片。
6. **视频入口未落地（D015），但只有一半**：`board_pipeline.py` 仍只吃板本地 `--video`；
   PC→板的网络**输入**端（`stream_sender.py` / `board_net.py`）是早期 bring-up，未接主线；
   协议/端口在台账仍 `discussing`。**板→PC 的网络输出端已上板通过**
   （`video_back.py`，`VIDEO_BACK_OK 250 19.61 5.1`），交付演示走这一向。
7. **队别已接入主线、未验证**：`tools/team_assign.py` + `board_pipeline --ui-config` 输出
   `players[*].team` 与 `events.possession_team`（主机侧 `TEAM_ASSIGN_SELFTEST OK`）；
   **真实画面与板上未验**；离线 `track_players.py` 仍不输出队别（像素坐标、未接单应）。
8. **数据集来源与许可（D005）已裁定为「内部使用声明」**：来源/用途/不随包分发写在
   `data/README.md` 的「来源与用途」一节；视频与训练集不进 `dist/`。

## 8. 签字

- 报告版本：`design_report.md` **v1.3（2026-10-31）**；`design_report.docx` 由同一 md 重新生成
- 门户技术文档：`report/technical_document.md` → `.docx` → `.pdf`（11 页 / 0.9 MB）
- 提交包：`dist/zynq-yolo_submission_<YYYYMMDD_HHMM>/` + 同名 `.zip`（旧包删除，只留当天一份）
- 门户代码包：同一目录打成 `.rar`（`tools/make_portal_assets.py --rar …`）
- 校验：`SUBMISSION_MANIFEST.csv` 的 `sha256` 列逐文件可核
