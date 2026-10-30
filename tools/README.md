# tools/ —— 仓库校验、文档生成、度量采集脚本

> 上级：[`../README.md`](../README.md)
> 唯一事实源：[`../docs/repo_spec.md`](../docs/repo_spec.md) §1 / §6（对应指南 3.3.5.2 校验脚本类技能）

| | |
|---|---|
| 本目录装什么 | **工具脚本**：仓库合规自检、度量采集、报告渲染 |
| 谁产生 | 手写 |
| 进 git | 是 |

## 内容

> ⚠️ 本节原写「`tools/` 为空目录、脚本一个都没创建」——**早已过时**（2026-10-20 更正）。
> 现状：**118 个自有 `.py`**（`tools/check_repo.py` 的 check f 实测口径，2026-10-25 复核）已落地。
> 下表只列关键脚本；完整清单看目录。状态列**如实**标注，**不要假装已存在/已运行**。

| 路径 | 说明 | 状态 |
|---|---|---|
| `check_repo.py` | **仓库自检**：命名纯 ASCII / 必需目录 / 每目录 README / 禁入文件 / 权重表 / py 语法 | ✅ 已创建（令牌 `CHECK_REPO_OK`） |
| `collect_metrics.py` | **资源/时序度量采集**：解析 `build/reports/*` 产指标表 | **TODO（未创建）** |
| `md_to_docx.py` | **报告渲染**：把 `report/design_report.md` 渲染为 `.docx`（标题/表格/图片/代码块） | ✅ 已创建 |
| `make_report_figures.py` | **报告图**：系统框图、软件流水线、检测路线耗时对比（确定性输出到 `report/figures/`） | ✅ 已创建 |
| `make_submission.py` | **提交包**：按 git 边界 + 运行必需件导出，生成 manifest/SHA256 并在导出目录跑 `check_repo.py` | ✅ 已创建（令牌 `SUBMISSION_PACK_OK`） |
| `ball_visibility_sweep.py` | **球可见性扫描**（D010 范围收缩依据之一） | ✅ 已创建 |
| `ball_cv.py` | **传统 CV 球定位可行性原型**：结论 `no_work`（人眼 2/10，判据假绿分析） | ✅ 已创建（结果 `data/results/ball_cv.json`） |
| `ball_policy.py` | **球链路策略纯函数**：丢失窗口按秒换算、预测点越界放置；供 `board_pipeline.py` 与离线自检共用同一判据 | ✅ 已创建（`--ball-lost-sec` / `--ball-predict-past-boundary` 的默认值与原逻辑逐字段一致） |
| `check_ball_switches.py` | **两个球开关的离线验证（不需要板子）**：合成轨迹 + 真实标定；覆盖默认等价、跨缺口重捕获、越界预测恢复与事件代价 | ✅ 已跑（`BALL_SWITCHES_OK`；板上验证**尚未做**） |
| `pitch_geometry.py` | **白线反解球场几何可行性**：n=10 n_pass=0（D010 范围外 b 的依据） | ✅ 已创建 |
| `person_scale_check.py` | **人可见性 vs 输入分辨率**（D010 收窄后「人看得见」判据） | ✅ 已创建 |
| `int8_sim.py` / `pynq_net.py` | INT8 量化验证 / 整网 PC 编排（后端可插拔，PL 后端待接） | ✅ 已创建 |
| `make_calib_from_video.py` / `find_incidents.py` / `log_incident_decisions.py` / `make_incident_review.py` / `conf_calibration.py` / `draw_detections.py` / `eval_calib_set.py` / `class_coverage.py` / `fetch_calib_real.py` / `onnx_probe.py` / `fps_feasibility.py` / `design_tradeoffs.py` / `export_layers.py` / `make_golden.py` / `ref_int8_conv.py` | 数据集 / 标定 / 评测 / 金标准 / 契约参考实现族 | ✅ 已创建 |
| `track_players.py` | **球员检测/跟踪/轨迹/倒地候选**（D011）：YOLOv8n + ByteTrack + 相机运动补偿（ECC 仿射，落脚点反变换回首帧坐标系）；输出 `data/results/player_tracks.json` 与逐段 `tracks/*.{json,csv}`；**`--calib` 时可把脚点映射到球场米制**（写 `points[].X/Y`，输入用稳定后的 `sx/sy`；不给就仍是像素） | ✅ 已创建（12 段实测 942 轨迹；倒地候选分 strong/weak 两级，**未经人工确认**，见 [`../docs/player_tracking_v1.md`](../docs/player_tracking_v1.md)） |
| `fall_review.py` | **倒地候选人工复核表**：每候选出「前/峰/后」三帧联系表 + CSV 待填 `judgement`；`--decide` 写回后才有「人工确认的倒地数」 | ✅ 已创建（26 张候选图；**刻意不产出 precision/recall**，理由见该文件头注释） |
| `classify_players.py` | **球员/非球员颜色分类**（D013）：预设色板 -> 躯干主色 -> 判 A/B/ref/unknown；含草坪抑制与 purity 自证；半场归属逐帧投票 + 翻转点检测 | ✅ 已创建（**注意**：只产出颜色标签，属「手段」；D013-a 目标需看 `color_prior.py`，见 [`../docs/color_prior_v1.md`](../docs/color_prior_v1.md)） |
| `color_prior.py` | **用颜色提升球员识别**（D013-a）：① 剔非球员 ② 低置信补检（带/不带颜色先验对照） ④ 分队；输出结构量（色相紧致度、场地内占比、keep_ratio） | ✅ 已创建（**无人工真值 ⇒ 不产出 precision/recall**；详见 [`../docs/color_prior_v1.md`](../docs/color_prior_v1.md)） |
| `make_player_labels.py` | **球员/非球员人工标注包**（D013-a 评估用）：第一批分三层（hi / lo_kept / lo_drop）采样出图 → `data/annot/labels.csv`；`--hi-unknown` 生成第二批（hi 层取色失败按原因分层）→ `data/annot/labels2.csv`；`--score` 给出「颜色先验 vs 无颜色先验」的 precision 对比 | ✅ 第一批 73/73 已标（有颜色 85.1% vs 无 71.0%）；第二批 159/159 已标 |
| `hi_color_diag.py` | **hi 层颜色 unknown 原因诊断**：把 `unknown` 拆成 too_small / purity / rect / color 四类，跑全量 12 段，回答「调颜色阈值到底有没有用」 | ✅ 已跑（12 585 hi 框，2 596 unknown；too_small 仅 14.7% ⇒ 阈值不是白调，见 `docs/hi_layer_color_gate.md`） |
| `hi_unknown_analysis.py` | **第二批标注判决**：按失败原因给非球员率 + Wilson 95% 区间，按 `hi_color_diag.json` 的真实占比做总体加权，`color_dist` 切片。（初版曾用「与第一批重叠的 37 框」估 kappa，**该结论已作废** —— `box_idx` 不是框的身份，见 `docs/hi_layer_color_gate.md` §6.4 更正） | ✅ 已跑（purity 97.4% / rect 67.6% / color 43.6% / too_small 12.9%；总体估计 60% 非球员；**仅层间相对高低可用**） |
| `label_web.py` | **球员/非球员标注网页**（本地 8765，键盘 p/n/u，自动存 `data/annot/labels.csv`，顶部可看分层加权 precision 对比） | ✅ 支持多批次：`?batch=1` 第一批、`?batch=2` 第二批、`?batch=3` 重复标注、`?batch=4` 新比赛（跨场），写入各自 CSV；已跑通写入/读回/非法 id 400 |
| `player_labels_pdf.py` | 把标注包排成 PDF（每页一个框 + 勾选栏） | ✅ 已创建（74 页，`report/player_labels_20261020.pdf`） |
| `batch3.py` | **第三批标注包（严格重复标注）**：从批1/批2 已标框里按 4 层（hi / lo_kept / lo_drop / hi_unk）各 15 个共 60 个重新出图到 `data/annot/frames3/`，写 `labels3.csv`（label 全空）+ `MANIFEST3.json`（来源只记在这里，CSV 不留痕迹）。框身份用 `(clip, frame, conf, h_px)`，**不用 `box_idx`**（它只是该 stratum 内的枚举下标）。`--audit` 只审计不出图 | ✅ 已创建（60/60 逐像素与原图一致；`REPEAT3_PACK_OK 60 15 15 15 15` / `REPEAT3_PIXELS` / `REPEAT3_AUDIT`） |
| `repeat_agreement.py` | **重复标注一致性**：按 `(clip, frame, conf, h_px)` 对齐 labels / labels2 / labels3，算三分类 Cohen's kappa、仅 P/N kappa、P/N 真冲突率、U 分歧率，并按 stratum 拆开 + 输出列联表与冲突明细（可查图）；labels3 未标完时明确报「未标完」**不给数字**（`docs/repo_spec.md` §5） | ✅ 已创建 |
| `player_gate.py` | **球员判定门控层**（D013-a 收口）：把「按取色失败原因分层」的判决落成可测量的规则表（`too_small`→keep / `purity`→drop / `rect`→drop / `color`→demote），叠加几何守卫（场内/远端人群）与 hull 纯度复核；带 v1/v2/legacy/noprior 等 8 组消融对照 + 空间/场景/conf 三组切片 | ✅ 已跑 12 段全量（`data/results/player_gate.json`）：v1 丢 834 框 → v2 只丢 98 框且救回 736 个（88.2%，新误杀 0）。设计、消融与**已知缺口**见 [`../docs/player_gate_v1.md`](../docs/player_gate_v1.md) |
| `cross_match_eval.py` | **跨比赛可迁移性评估**：给定新比赛视频目录，自动抽 20s 切片 → 跑 hi 层检测 + 取色失败原因分层 → 与旧基线（`hi_color_diag.json`）并排对照；色板必须人工传入（D013 LCD 方案的命令行形态）；`--play-windows` 先在整场录像里按草坪占比检出比赛段（避开广告/演播室/看台特写）；`--palette-refs` 接入按场校准的实测参考色（**只换参考色，不动阈值**）；`--color-metric v1\|v2` 切换取色口径，并带 **G6 草坪伪改进守卫** + `color_metric_not_applied` 硬判据（防止「开关没接上」的静默假绿） | ✅ **已跑真新比赛**：判据 G1–G6 定义 + 三步验证法见 [`../docs/cross_match_eval.md`](../docs/cross_match_eval.md) §13/§14；**实测结论见 [`../docs/cross_match_results.md`](../docs/cross_match_results.md)**（v1 unknown 47.7% → v2 25.0%，能判出率 52.3%→76.3%；**v2 `default_recommended:false`，等第四批标注**）。自检产物另存 `cross_match_selfcheck.json`（不再覆盖正式结果） |
| `palette_probe.py` | **色板可分辨性闸门**（跨比赛评估的前置条件）：两队色的 sibling margin 是否够、草坪/藏青/黄/紫/白球衣真实像素会不会被误判给任一队；`cross_match_eval.py` 开跑前自动调用 | ✅ 已创建（独立可跑：`python tools/palette_probe.py <A> <B>`）。实测白队像素 97% 判成蓝色此类**静默误判**能被挡下（见 `docs/cross_match_eval.md` §9） |
| `kit_calib.py` | **按场校准球衣色板**：比赛段内取帧 → 取躯干色 → 按「到两队参考色的较近者」粗分两簇 → 输出「预置参考色 vs 实测中位数」下的取色成功率，回答「换场后 unknown_rate 涨的那 30 pt 有多少是色板没校准」 | ✅ 已跑新场（`data/results/kit_calib.json`）：229 样本，colored 61.1%→**74.2%**（+13.1 pt）。结论：色板参考色是**按场**的量，见 `docs/cross_match_eval.md` §12.3 |
| `make_newmatch_labels.py` | **第四批标注包（新比赛 · 跨场）**：比赛段 → 检测 → 按取色失败原因分层（purity/color/too_small/rect/colored）各抽 K 个，出 320×320 单框图 + `labels4.csv`（label 空）+ `MANIFEST4.json`；`--score` 在标完后给每层 N 率 + Wilson CI，并与旧场层内 N 率并排做**保序检验** | ✅ 已建包（83 框：purity 20 / color 20 / too_small 20 / **rect 仅 3**（新场 rect 层候选只有 3 个）/ colored 20）；`NEWLABEL_PACK_OK 83 20 20 20 3 20`。**尚未标注**，`--score` 会明确报 `not_all_labeled` |
| `match_setup.py` | **赛前输入（D013 的逻辑真源）**：录入双方队服颜色 / 裁判颜色 / 各自半场 → `data/config/match_<id>.json`；带 schema、颜色名校验（必须是 `PALETTE_REF` 的键）、两队同色/同半场拒绝、裁判色与队色相近告警，并直接打印可复制的下游命令（`--palette` / `--palette-refs`）。`--serve` 提供**网页前端作为 LCD 触屏的模拟**（真机 UI 复用同一份 schema 与校验） | ✅ 已实现（CLI / --list / --show / --print-cmd / --serve 8767 全跑通；负例 `same_team_color`、`same_half_side` 均被拒）。已录 `data/config/match_migu968460165.json`（white/blue，裁判 red，A 先开左半） |
| `capture_golden.py` | **采集链（M1）上板黄金模型 / 比对器**：`--out` 按 `vid_test_pattern.v` 的闭式公式生成期望 DDR 镜像（每像素 32 位小端 = B8,G8,R8,0x00；RGB565→RGB888 位复制），`--compare <dump>` 与 JTAG dump 回的 DDR 逐字比对并按字节坐标报首个不符 | ✅ 已实现（`--out` 生成 `build/board/capture_golden.bin`，`crc32=d1c9c45a`、`frame_bytes=1228800`；比对器已自测：自身比对 `CAPTURE_MATCH_OK`、改 1 字节必 `CAPTURE_MATCH_FAIL` 并定位到 `(x=360,y=1)`） | `CAPTURE_GOLDEN_OK path=.. frame_bytes=.. words=.. crc32=..` / `CAPTURE_MATCH_OK words=.. crc32=..` / `CAPTURE_GOLDEN_FAIL` / `CAPTURE_MATCH_FAIL mismatched=n/total` |
| `track_preproc.py` | **PL 跟踪辅助驱动**（D019）：加载 `track_preproc.bit`，一次返回 3×320×320 RGB + 320×320 白/运动掩码 | ✅ 已出片；上板逐位对通过（`TRACK_PREPROC_BOARD_OK`） |
| `check_track_preproc.py` | **PC 算法对拍**（不跑 HLS）：g++ 编译 TB，合成 64×64 帧，逐字节比 RGB/掩码 | ✅ `TRACK_PREPROC_REF OK` |
| `check_track_preproc_board.py` | **PL 跟踪辅助上板对拍**：加载 overlay 后逐字节比 RGB/掩码并测 720p 耗时 | ✅ `TRACK_PREPROC_BOARD_OK`（720p 32.39 ms） |
| `check_board_acceptance.py` | **板上一条命令验收球链路三组参数**：base / `--ball-lost-sec` / `--ball-predict-past-boundary` 各跑一遍并出对照表；`--selftest` 验判定逻辑、`--replay` 做离线统计、`--reuse` 复用已有结果 | ✅ 板上实测 `BOARD_ACCEPTANCE_OK`（`ball_clip.mp4` + `field_calib_ball.json`：base 144/48、lostsec 182/48、predict 144/48）；⚠️ **但 lostsec 新增 38 帧经人工抽查全是假球 ⇒ 该开关已否决**（见 `ai/sessions/012`） |
|　| ⚠️ **标定必须配对**：`ball_clip.mp4` 只能用 `data/calib/field_calib_ball.json`；配 `field_calib.json` 会判 `calibration_mismatch` → 镜头无效 → 球全部不输出（0 帧），三组都会"通"但统计全 0 | |
| `check_display_board.py` | **显示 overlay 上板门禁**：写 1bpp 帧缓存并 AXI 读回（HDMI/LCD/双屏）；`--lcd-gpio` 验像素内容/扫描范围/像素时钟，`--hdmi-gpio` 验读口地址覆盖与帧率 | ✅ `DISPLAY_BOARD_OK` / `DISPLAY_BOTH_BOARD_OK` / `DISPLAY_BOARD_LIVE OK` / `DISPLAY_BOARD_HDMI_LIVE OK` |
| `lcd_probe.py` | **LCD 双分辨率探针（板端）**：加载 `lcd_probe.bit`，用 cfg 切 800×480@33.333 / 480×272@9.000、切纯 PL 图案/帧缓存，读 ID/时序/实测像素时钟并写帧缓存读回；帧缓存**默认 4bpp 16 色**（`--bpp 1` 回落黑白），地址 `0x40000000`（800，192 KB）/`0x40080000`（480）；`--keys` 读按键 GPIO `0x41260000`；`--dbg-row` / `--dbg-scan` 逐行读回「读侧实际送屏的字」（`0x4123`=line_buf、`0x4124`=stag_flat、`0x4125`=行 DE 拍数/预取次数，cfg[15:6] 选行） | 上板实测 800×480 / pclk 33.337 MHz；读侧根因已定位（见 `ai/sessions/021`） |
| `lcd_data.py` | **LCD 帧缓存打包器（独立口径，不经过 PIL）**：每行 W/(32/bpp) 个 32-bit 字、字内**最低 bpp 位 = 最左像素**；`--bpp 4`（默认）生成 16 色 tag4 诊断图（第 r 行第 k 字 = `0x11111111*((r+k)&15)`）+ 末尾 16 个调色板字，`--bpp 1` 是旧黑白口径；`--selftest` 验两种 bpp 的打包/地址 | `--selftest` 通过（1bpp 800=12000、4bpp 800=48000+16） |
| `lcd_probe_host.py` | 主机侧包装：`build` / `deploy` / `run`，把出片与 Jupyter 通道串起来 | ⚠️ Vivado 一步**不能在工具沙箱内跑**（见 021 记录） |
| `lcd_ui.py` | **赛前设置界面（板端，800×480 4bpp）**：球衣颜色 A/B + 双方守门员，**用板载两颗 PL 键操作**（key0=下一项、key1=换色/确认，GPIO `0x41260000`），显式 16 色调色板（8 种球衣色必须精确在板内，否则量化会把红/橙、蓝/紫并掉）；确认后落 `board/ui_config.json`（颜色名 + RGB + 时间戳）供下游读；`--once` / `--script 0,1,1`（回放按键）/ `--keys`（只读电平判极性）/ `--preview-only`（主机侧出「面板真实所见」的量化 PNG） | ⚠️ 版面主机侧已过；**上板待验** |
| `board_jupyter.py` | **Jupyter 控制通道**（SSH 不通时的唯一入口）：`run(code)` 在板端内核执行；`--push` 分片 base64 写**任意路径**并回读 sha256（`--put` 只能写 Jupyter 根目录 `/home/xilinx/jupyter_notebooks`）；`--get` 取回文件 | ✅ 已用于本仓多数板端实验 |
| `result_store.py` | **SD 卡增量存储**：JSONL + `summary.json` + fsync + 轮转 | ✅ 已创建（`RESULT_STORE_SELFTEST OK`） |
| `result_stream.py` | **板→PC 推送**：按行 TCP 发 JSONL，断线重连/尾随 | ✅ 已创建（本地回环 `sent=2`） |
| `pc_dashboard.py` | **PC 端软件**：TCP/回放 + 105×68 球场画布 + CSV + headless | ✅ 已创建（`PC_DASHBOARD_SELFTEST OK`） |
| `hdmi_video.py` | **HDMI 视频环出（D026，PS 侧）**：按 JSONL 把球员绿框/#id、球红圈、顶部事件条画到真实画面上，打包 32bpp 写 DDR 帧缓存，AXI GPIO 告诉 PL 基址/跨距/宽；`--png-out` 可**不接板出图**，`--selftest` 验打包往返 | ✅ `HDMI_VIDEO_SELFTEST OK`；主机侧出图已验证（框/球/事件条都在）⚠️ **上板未验** |
| `check_full_flow.py` | **整体流程一条命令**：时间对齐 → 赛前配置 → 结果落盘 → 板→PC 推流 → LCD 状态面板 → 赛前 UI，每步一条令牌，最后打 PASS/FAIL 表；`--dry-run` 主机跑、`--board` 含真板 | ✅ `FULL_FLOW_OK`（主机侧 ok=6 skip=1 fail=0，skip=时间对齐；含 team 步）；`--board` 待板 |
| `check_team_assign.py` | **真实画面上的队别投票检查**：吃 `track_players.py` 的 tracks JSON + 原片（不重跑检测），按 `--palette A=red,B=white` 投票并打印 `TEAM_EVAL tracks/voted/labeled/A/B/unknown`；票率 <20% 时给 `TEAM_EVAL_WARN`（换错色板会先在这里塌）| inc_01 `labeled=55/77`、inc_06 `labeled=53/64`；**无人工真值，不作准确率** |
| `team_assign.py` | **球衣颜色 → 队别（D012/D013）**：读 `match-config/2`（`board/ui_config.json`）把 A/B/守门员/裁判色板转给 `classify_players.classify()`，按 `track_id` **累积投票**（默认 ≥3 票且多数占比 ≥0.6 才给结论，否则 unknown）；提供 `Assigner.observe/team/teams`，被 `board_pipeline --ui-config` 调用，产出 `players[*].team` 与 `events.possession_team`；`--show-config` 打印解析后的色板 | `TEAM_ASSIGN_SELFTEST OK`（合成红/蓝/黄/草坪帧）；**真实画面与板上未验** |
| `check_lcd4_board.py` | **4bpp 读侧上板自证**：写 tag4（第 r 行第 k 字 = `0x11111111*((r+k)&15)`）+ 16 个调色板字，再逐行读 `0x4123/0x4124/0x4125` 的抓取寄存器，与期望值逐项比对（`lb==st`、`de=800`）；PS 侧读写回环自洽，只有让 PL 去读、图案自带坐标才能验地址口径 | ⚠️ 等板子上线（判据 `READBACK_OK bad=0`） |
| `status_display.py` | **HDMI/LCD 状态面板**：800×480/1280×720，fbdev/PNG/窗口/BRAM。`--fb-bram` 的打包版面**跟着 `--size` 走**（`layout=WxH words=N` 打进令牌；写错版面不报错、只会让相邻行文字相叠，见 `ai/sessions/021`）；LCD 走 **4bpp + 语义调色板**（按类型上色：帧/人数/球/事件/相机/存储/PL/时钟各一色，`LCD_PALETTE` 显式给定，不用自动量化）；1bpp 小字用 ≤20px 加 1px 描边 | ✅ 面板已上屏人眼通过（1bpp）；⚠️ 4bpp 上色版**待上板** |
| `board_clock.py` | **板子时间对齐（主机侧）**：`status` 看板子时间与 PS RTC 是否存在、`sync [--rtc]` 把本机时间推给板子并写进 RTC | ✅ 已创建（等上电实测） |
| `bram_fb.py` | **帧缓存打包/写屏**：1bpp（HDMI 28,800 / LCD 12,000 字）与 **4bpp 16 色**（`pack_4bpp` / `write_image4`，可按给定调色板量化，图像字后紧跟 16 个调色板字 —— 换色只重写这 16 字，不用重新出片）；PYNQ MMIO 或 `/dev/mem` 写 AXI BRAM；`--verify-frame` 把 RTL 仿真 dump 的整场帧与 `pack_1bpp` 重算结果对拍（Python↔RTL 位序） | ✅ `BRAM_FB_SELFTEST OK` / `BRAM_FB_FRAME_OK` |
| `make_report_figures.py` | 报告图：系统框图/软件流水线/耗时对比/状态面板/PC 球场视图 | ✅ 已创建 |
| （其他脚本） | 需要时新增；新增后必须在上表登记并补 `README` 说明 | — |

## 规则

- **状态必须诚实**：未创建的脚本标 `TODO`，禁止在 README 或报告中声称其已存在/已运行。
- 脚本一律可**一条命令**运行，且**不以退出码单独判定成功**（§5）：应打印明确结论/令牌（如 `CHECK_OK` / `CHECK_FAIL`）与失败明细。
  > 本项目既有判据令牌：构建 `DONE_OK`、上板 `PROGRAM_OK`。工具脚本可沿用同名风格。
- 脚本的判据来源必须是 [`../docs/repo_spec.md`](../docs/repo_spec.md)，**不得在脚本里另立一套规则**；规范更新时同步改脚本。
- `collect_metrics.py` 产出的数字必须**标来源文件路径**（§6 纪律：数字必须标来源）。
- 命名硬约束（§1）：脚本名与目录名只用 `a-z 0-9 _ - .`；日期后缀用 `_YYYYMMDD`；**文件正文可用中文**。
- 禁止把工具运行产物（`__pycache__/`、临时 `.log`）提交入库（§1 规则 6）。
- 本目录脚本属「校验脚本类技能」，可被 `skill/` 引用；若沉淀为技能包，`skill/` 下每项须含 `README.md`（适用范围/使用方法/失效条件，指南 3.3.5.2）。
- 窗口/环境：Windows + `pwsh`；本机 Python 为 `D:\python\python.exe` **3.13.1**（第三方库清单见 [`../docs/repo_spec.md`](../docs/repo_spec.md) §6 环境事实表）；本机工具链仅 Vivado / Vitis / Vitis_HLS **2020.2**。
- **本目录脚本必须零第三方依赖或用纯标准库**（`check_repo.py` 即纯标准库：`os` / `re` / `sys` / `pathlib`），以保证评审机器上 `python tools/check_repo.py` 直接可跑。

## 怎么用

```powershell
# 1. ✅ 仓库自检（纯标准库，无第三方依赖）
& D:\python\python.exe D:\zynq-yolo\tools\check_repo.py
#    期望末行：SUMMARY: 6 PASS / 0 FAIL / 0 WARN  +  结论令牌 CHECK_REPO_OK
#    退出码 0 = 无 FAIL；有 FAIL 时为 1。也可在仓库根直接 `mingw32-make check`。

# 2. 【TODO，未创建】采集资源/时序度量
#    python D:\zynq-yolo\tools\collect_metrics.py --reports D:\zynq-yolo\build\reports
#    期望：输出指标表（含来源文件路径），可直接粘进 ../report/

# 3. 【TODO，未创建】渲染报告
#    python D:\zynq-yolo\tools\md_to_docx.py --src D:\zynq-yolo\report --out D:\zynq-yolo\report\

# 4. 第三批重复标注（隔一段时间再标一遍，验「同一张图会不会标出两个答案」）
#    & D:\python\python.exe D:\zynq-yolo\tools\batch3.py            # 生成 60 框 -> data/annot/frames3/ + labels3.csv
#    & D:\python\python.exe D:\zynq-yolo\tools\batch3.py --audit    # 只审计已出批次，不出图
#    & D:\python\python.exe D:\zynq-yolo\tools\label_web.py --port 8765 --batch 3
#    标完后：& D:\python\python.exe D:\zynq-yolo\tools\repeat_agreement.py
#    期望末行：REPEAT_AGREE_OK <kappa> <n>；未标完时为 REPEAT_AGREE_FAIL not_all_labeled（不再给数字）

# 5. 建议节奏：每次改动 src/ 或 build/ 后跑一次 check_repo.py，
#    每轮构建后跑一次 collect_metrics.py（见 ../build/README.md 的报告轮转约定）
```

## 现状

| 脚本 | 状态 | 判据令牌 |
|---|---|---|
| `check_repo.py` | ✅ 已实现（6 项检查：命名 / 必需目录与根文件 / 每目录 README / 禁入文件 / 权重表 / py 语法） | `CHECK_REPO_OK` |
| `collect_metrics.py` | ⬜ TODO | —— |
| `md_to_docx.py` | ⬜ TODO | —— |
| `ball_visibility_sweep.py` | ✅ 已实现（分辨率 × 切图 × 模型 × 置信度四组扫描 + 基线/阴性/阳性对照） | `SWEEP_DONE <n>` / `SWEEP_FAIL <原因>` |
| `ball_cv.py` | ✅ 已实现（运动残差 + 外观先验 + 时空关联；含 null/static/shuffle/synthetic/ablate 五类对照） | `BALL_CV_DONE`（结果 JSON 内 `verdict_class`） |
| `check_ball_switches.py` | ✅ 已实现（A：`--ball-lost-sec` 默认值/同窗口等价/8 帧缺口重捕获；B：`--ball-predict-past-boundary` 默认逐字段回归/作用域/真实标定轨迹/事件代价） | `BALL_SWITCHES_OK` / `BALL_SWITCHES_FAIL <原因>` |
| `pitch_geometry.py` | ✅ 已实现（白线掩膜覆盖率判据 + 阴性/阳性对照） | `PITCH_GEOM_*` |
| `int8_sim.py` / `pynq_net.py` | ✅ 已实现 | `PYNQ_NET_OK` / `PYNQ_NET_FAIL` 等 |
| `batch3.py` | ✅ 已实现（60 框 4 层各 15；出图后自检逐像素一致；`box_idx` 不参与框身份） | `REPEAT3_PACK_OK <n> <分层计数>` / `REPEAT3_PIXELS` / `REPEAT3_AUDIT <行数> <唯一键> <共享键> <严格同框>` / `REPEAT3_FAIL <原因>` |
| `repeat_agreement.py` | ✅ 已实现（三分类 & 仅 P/N 的 Cohen's kappa、P/N 真冲突率、U 分歧率、按 stratum 拆分、列联表、冲突明细；未标完不下结论） | `REPEAT_AGREE_OK <kappa> <n>` / `REPEAT_AGREE_FAIL <reason>`（`not_all_labeled` / `kappa_low` / `no_aligned` / `no_pn_pairs`） |
| `player_gate.py` | ✅ 已实现（规则表 + 几何守卫 + hull 复核；8 组消融：after/legacy/noprior/nostruct/norules/nohull/v1struct/minimal；可被 `color_prior.py --gate` 复用） | `PLAYER_GATE_OK <n_kept> <n_dropped> <指标>` / `PLAYER_GATE_FAIL <原因>`（结果 `data/results/player_gate.json`；`cov_est` 恒为 `unreliable`，理由见 `docs/player_gate_v1.md` §5.1b） |
| `cross_match_eval.py` | ✅ 已实现（切片抽取 → hi 层分层统计 → 与旧基线并排 + 判据 G1–G5 + 门控一致性反推 + 色板闸门；色板人工传入，不写死） | `CROSS_MATCH_OK <n_clips> <n_hi> <max_drift>` / `CROSS_MATCH_FAIL <reason>`（字符串见下）；自检模式另打一行 `SELF_CHECK_OK pipeline n_clips=<n> n_hi=<n> max_drift=<f>`（同场对基线无判别力，不套 G1–G5） |
| `palette_probe.py` | ✅ 已实现并接入 `cross_match_eval.py`（兄弟色 margin ≥0.10；草坪/藏青/黄/紫/白球衣真实像素不得被判成 A/B 且离该队参考色 >1.5×MAX_COLOR_DIST） | `PALETTE_PROBE_OK` / `PALETTE_PROBE_FAIL <palette_indistinct ...>`（未过闸门时主脚本 `CROSS_MATCH_FAIL`，可用 `--allow-indistinct-palette` 显式放行） |
| `kit_calib.py` | ✅ 已实现（比赛段取帧 → 躯干色 → 按最近参考色粗分两簇 → 预置 vs 实测中位数两套参考色下的 colored/unknown） | `KIT_CALIB_OK <n> <colored_before_pct> <colored_after_pct>` / `KIT_CALIB_FAIL <reason>`（结果 `data/results/kit_calib.json`） |
| `capture_golden.py` | ✅ 已实现（闭式图样模型 `R5=x&0x1F G6=y&0x3F B5=(x+y)&0x1F` → RGB888 位复制 → 32 位小端字；`--h/--v` 与文件顶部常量不一致时 `--out` 报 FAIL，强制与 RTL/BD 同步） | `CAPTURE_GOLDEN_OK path=<f> frame_bytes=<n> words=<n> crc32=<h>` / `CAPTURE_MATCH_OK words=<n> crc32=<h>` / `CAPTURE_GOLDEN_FAIL <reason>` / `CAPTURE_MATCH_FAIL mismatched=<n>/<total>`（附前 5 个不符的字节坐标与期望值） |
