# 提交前检查清单（2026-11-01）

> 本清单按指南 **3.3.5** 的四类提交物与**赛事门户「作品管理」提交流程**逐项核对（门户口径以
> 2026-11-01 的页面截图为准，共 13 项，其中 10 项标红星必填）。勾选状态以**仓库内实际产物**为准，
> 不以口头说明为准；每个「✅」后面都能在仓库里找到文件。
>
> **提交截止：2026-11-04 18:00**（官网/高校通知一致口径；赛程以 <http://www.fpgachina.cn/> 公布为准）。

## 0. 赛事门户「作品管理」上传件（2026-11-01 截图口径）

| # | 门户字段 | 必填 | 门户要求 | 当前产物 | 状态 |
|---|---|---|---|---|---|
| 1 | 作品名称 | — | 文本 | 面向争抢场景的足球视频分析系统 | ✅ 已填 |
| 2 | **演示视频** | ★ | MP4 / AVC(H.264) / **≤3 min** / ≤300 MB；**不得出现校名、指导教师姓名** | **尚无成片**；分镜脚本 `report/demo_video_storyboard.md` 已按 `--demo` 改写，命令逐条上板验证过 | ⛔ **待拍摄/剪辑**（只能人工） |
| 3 | **技术文档** | ★ | PDF / ≤50 MB；门户提供模板（五部分：作品概述/系统组成/完成情况/总结/参考文献） | `report/technical_document.md` → `.docx`（1.7 MB）+ `.pdf`（**5.1 MB**），2026-11-01 18:2x 按本文重生成；已补 D027–D031 与整段实测 | ✅ **正文已更新**；文中 `【待补…】` 三处实物照片仍待补 |
| 4 | **上传全部代码** | ★ | **RAR** / ≤200 MB | `dist/zynq-yolo_submission_<stamp>.rar`（`tools/make_portal_assets.py --rar`）+ 同名 `.zip`；只留当天最新一份 | ⚠️ **每次改完必须重出 RAR**（zip ≠ RAR，门户只收 RAR） |
| 5 | **参赛作品承诺及使用授权书** | ★ | PDF / ≤50 MB | 官方空白模板（`D:\xiazai\` 下） | ⛔ **需打印、签名、扫描上传**（只能人工操作） |
| 6 | 作品海报 | 选填 | PDF 或 PPT / ≤50 MB | 提示词与版式已备好，可出 A4 一页 | ⏳ 选填；进决赛另需**英文一页 A4 海报**（指南「六、其它」） |
| 7 | **GitHub 链接** | ★ | ≤200 字 | `https://github.com/110-lii/zynq-yolo` | ⚠️ **本机 `main` 领先 origin 11 个提交，需 push 后再填** |
| 8 | **作品全貌图** | ★ | JPG/PNG / ≤10 MB / **1 张** | 尚无实物照（`report/figures/system_arch.png` 是框图，不能替代） | ⛔ **待拍摄**（板子 + HDMI + LCD + PC 同框） |
| 9 | **开发板重要角度高清照片** | ★ | JPG/PNG / ≤10 MB / **最多 4 张** | 尚无 | ⛔ **待拍摄**（建议：正面、斜 45°、接口侧、通电工作特写） |
| 10 | **作品简介** | ★ | 文本框（约 300–500 字） | 已拟稿（见 §0.1） | ⚠️ 门户里那段末尾疑似多了 `PRINT` 字样，粘贴前核对 |
| 11 | 是否参加其他赛事或往届赛事并获奖 | ★ | 单选 | 否 | ✅ |
| 12 | 是否为老师科研项目或实验室作品 | ★ | 单选 | 否 | ✅ |
| 13 | **三个关键词** | ★ | 3 个关键词 | 建议：`异构软硬件划分` / `小目标球检测` / `运行时自标定`（见 §0.2） | ⚠️ 尚未填 |

> 门户红字：演示视频与技术文档**不得出现学校名称、指导教师姓名**等与作品无关的信息。
> 仓库正文已核对无校名/学院/指导教师/学号；视频与照片拍摄时同样注意。
> ⚠️ 视频是 **H.264**，不是 H.265/AV1；拍完用 `ffprobe` 确认 `codec_name=h264` 且时长 ≤180 s。

### 0.1 作品简介（可直接粘贴，约 340 字）

> 本作品是一套面向比赛视频的分析系统。它的核心不是一台摄像机，而是一个能接入视频、输出结论的
> 处理节点：素材由电脑送入板端，同一套处理核既跑现场录像，也跑赛后片段。
>
> 软硬切分是系统的重心。7020 芯片只有 220 个 DSP，整网卷积要 9.63 秒/帧，放不下大算力 CNN；
> 像素级、可流水化的部分放到 PL，一次产出 320×320 RGB 与运动掩码；判断类任务放到 PS，用 ncnn
> 跑单类 person，球不靠全图检测，用白块候选加小型分类器与轨迹确认，球丢失时如实标记，不靠参数把帧数放大。
>
> 球出界后，系统在球场坐标系里给出出界方向、是否进球，以及 3 m 内离球最近球员的球权；机位未标定就
> 不输出坐标、不下结论，人员倒地只给候选帧转人工复核。系统基于 Zynq-7000：PL 以 HLS/RTL 做像素预处理、
> 运动掩码与状态屏帧缓存，PS 基于 PYNQ 做标定、跟踪与判定。

### 0.2 三个关键词

`异构软硬件划分` · `小目标球检测` · `运行时自标定`

（第三个指 D031：HDMI 行内平移每次起管线现场标定并补正，残余量复测为 0。）

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
| HDMI/LCD/PC 方案 | `docs/display_overlay_plan.md`、`docs/flow_overlay.md`、`docs/hdmi_video_osd.md`、`tools/status_display.py`、`tools/pc_dashboard.py`、`tools/hdmi_video.py`、`board/overlay/*.bit` | 软件 ✅；HDMI 状态面板 **`BRAM_OK` 通过 / 1bpp `VISUAL_OK` 待人眼**；LCD 4bpp **上板 `READBACK_OK bad=0` 且自证图/彩色面板人眼确认（赛前选色 UI 已按 D028 删除）**；PC 面板 ✅ 含比赛画面视图（`--video`）；HDMI 带框环出 **`BRAM_OK` 通过、`VISUAL_OK` 不记**（13:56 曾记 `HDMI_VISUAL_OK`，20:23 复测出现整行循环移位，**当日撤回**）。**2026-11-01（D031）**：缺陷定量为「屏幕列 = 源列 + K」的**纯循环平移**，起管线时现场标定 K 并写帧前补正，复测 **`HDMI_ALIGN shift=415 residue=0`** ⇒ **演示已走 HDMI 回显**，但**屏上观感仍要人眼看**（见 §3.1、§3.2、§7.5） |

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
| LCD 状态面板（10-29 重做） | `src/rtl/io/lcd/lcd_probe_*.v` + `build/scripts/bd_lcd_probe.tcl` + `tools/check_lcd4_board.py` | 已出片（4bpp/16 色/按键；WNS +1.839 ns）；✅ **上板 `READBACK_OK bad=0 checked=10`**（10-30，两次一致）；✅ 自证图 `tag4` 人眼确认；⏳ 彩色面板画面待人眼 |
| HDMI+LCD 合并（现行） | `build/scripts/bd_flow_system.tcl` + `board/overlay/flow_system.bit` | **交付片 `flow_system.bit`（sha256 `1397ce68…`）**：WNS **+2.362 ns**、Slice LUT **22 858（42.97%）**、BRAM **86/140**、DSP **4/220**（`build/reports/timing/flow_system_bd_timing.rpt`、`build/reports/resource/flow_system_bd_util.rpt`，10-31 19:24/19:25 同批）。历史中间片另有 +3.042 / +4.184 ns，**不作交付结论**。✅ 上板 `flow_overlay.py --selftest`：LCD `bad=0/16`、HDMI `frames +492 under=0`；✅ **真视频环出 `HDMI_VIDEO_OK` 25 帧 `under=0`**（机器级）；✅ **2026-11-01：行内平移现场标定 + 补正，`HDMI_ALIGN shift=415 residue=0`**（D031）。❌ `HDMI_VISUAL_OK` 仍不记（屏上观感需人眼） |
| HDMI+LCD 合并（旧） | `build/scripts/bd_display_both.tcl` + `board/overlay/display_both.bit` | ❌ 已由 `flow_system` 取代：1bpp 文字方案，且落后于 018–021 的新读侧，不再重出片 |
| 跟踪+双屏合并 | `build/scripts/bd_track_display.tcl` + `board/overlay/track_display.bit` | ✅ `TRACK_PREPROC_BOARD_OK`（逐字节对拍）；⚠️ 合并画面同上门槛 |
| 板上管线 + SD + PC | `board_pipeline.py --pl-track` 20 帧；`result_stream` → Dashboard | ✅ `PIPELINE_OK`、20 行 JSONL、`PC_DASHBOARD_OK frames=20` |
| 上电即用（D029，11-01） | `tools/board_idle.py` + `tools/install_boot.py` + `docs/streaming_quickstart.md` | ✅ **上板并真重启验过**：两服务自启、白屏 `frames=31/35 under=0`、重启后推流 `PIPELINE_OK`；推流时 HDMI 回显带框图（`HDMI_PREVIEW_ON/OFF white_restored`）；⏳ 白屏与回显画面待人眼 |
| 决策登记 | D018–D031（含 D029 上电即用、D030 LCD 面板、D031 HDMI 平移补正） | ✅ |

## 3.2 交付前补齐（2026-10-27，D026）

| 项 | 产物 | 状态（机器判据） | 上板 |
|---|---|---|---|
| SD 证据帧落盘 | `tools/result_store.py::save_evidence`、`board_pipeline --store-evidence` | ✅ `RESULT_STORE_OK`（含每分钟限流 6 张） | ⛔ 待板 |
| PC 上位机界面 | `tools/pc_dashboard.py`（状态条 + 球场 + 事件流 + 证据帧 + 12 列 CSV） | ✅ `PC_DASHBOARD_SELFTEST OK`；界面 PNG 已人眼核对 | 无需板 |
| LCD 状态面板信息 | `tools/status_display.py`（8 项，800x480 / 1280x720） | ✅ `STATUS_DISPLAY_SELFTEST OK`；两尺寸 PNG 已人眼核对 | ⛔ 待板（fbdev/BRAM 通路） |
| HDMI 视频环出（带框画面） | `src/rtl/io/hdmi/fb_scanout.v`+`hdmi_video_top.v`、`build/scripts/bd_hdmi_video.tcl`、`tools/hdmi_video.py` | ✅ `TB_FB_SCANOUT_OK`（2048 像素逐点 0 错）；✅ PS 侧 PNG 预览；✅ **真视频 `HDMI_VIDEO_OK` 25 帧 `under=0`（机器级）**；❌ `HDMI_VISUAL_OK` 已撤回（`under=0` 与累加和对整行移位都是瞎的）；✅ **2026-11-01（D031）**：单列探针把映射定量为 `屏列 = 源列 + K`（K=414/415），写帧前按列索引补正，复测 `residue=0` | ⚠️ 机器判据成立（列映射恒等、行号正确），**屏上观感待肉眼确认**；`--demo` 已含 HDMI 回显 |

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
   - `VISUAL_OK`（人眼看画面）：LCD **自证图 `tag4`、彩色状态面板已人眼确认**（赛前选色 UI 曾真按键走通，2026-11-01 按 D028 删除）；
     **HDMI 带框环出静态六屏 `HDMI_VISUAL_OK` 已于当日 20:23 撤回** —— 13:56 人眼记过一次
     （证据当时在 `data/results/hdmi_visual_ok.json`），同片复测又出现整行循环移位
     （`px = c + 437 / +441`），今日定量为恒定 `+313` 列；证据文件现为 `HDMI_VISUAL_RETRACTED`。
     ⇒ **HDMI 侧 `VISUAL_OK` 不成立**，且 `under=0`、累加和、「每源列 1 次」这三类判据
     对整行旋转都是瞎的，不能拿来替代人眼。
     **1bpp HDMI 状态面板与实时叠框回放仍未人眼确认**，不宣称「画面通过」。
   - `display_both.bit` / `track_display.bit` 落后于新读侧，需重出片。
6. **视频入口（D015）：两个方向都已实现，上板验证进度不同**
   - **板 → PC（输出）**：`video_back.py`，**已上板通过**（`VIDEO_BACK_OK 200 21.56 4.9`、
     `VIDEO_BACK_OK 190 19.65 5.0`，后者屏上有框有球），交付演示走这一向。
   - **PC → 板（输入）**：`video_send.py` ↔ `board_pipeline.py --listen`（D027：PS GEM + TCP +
     JPEG + `ZYVI`，端口 5007）。✅ **上板等价性已验**（2026-10-31 当夜补验）：同一段 c03
     走文件源与网络源都是 `frames=250 detections=17`，带球帧集合完全相同（100–114、140–144）；
     `VIDEO_SEND_OK 250 58.74 0.53 2.2`。⚠️ **JPEG 有损，档位会改变判决**：q80 时多出 10 帧
     假球（f45–54，实为球员白鞋），故默认质量定为 **95**。判据只比「帧数/判决」，不比「逐位」；
     **未验**：`--camera` 摄像头源、长时段连续传输的丢帧/超时。
7. **队别已接入主线、未验证**：`tools/team_assign.py` + `board_pipeline --ui-config` 输出
   `players[*].team` 与 `events.possession_team`（主机侧 `TEAM_ASSIGN_SELFTEST OK`）；
   **真实画面与板上未验**；离线 `track_players.py` 仍不输出队别（像素坐标、未接单应）。
8. **数据集来源与许可（D005）已裁定为「内部使用声明」**：来源/用途/不随包分发写在
   `data/README.md` 的「来源与用途」一节；视频与训练集不进 `dist/`。

## 8. 签字

- 报告版本：`design_report.md` **v1.3（2026-10-31）**；`design_report.docx` 由同一 md 重新生成
- 门户技术文档：`report/technical_document.md` → `.docx` → `.pdf`（13 页 / 1.1 MB）
- 提交包：`dist/zynq-yolo_submission_<YYYYMMDD_HHMM>/` + 同名 `.zip`（旧包删除，只留当天一份）
- 门户代码包：同一目录打成 `.rar`（`tools/make_portal_assets.py --rar …`）
- 校验：`SUBMISSION_MANIFEST.csv` 的 `sha256` 列逐文件可核
