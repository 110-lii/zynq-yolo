# 系统状态（2026-10-30）

## 架构

```
视频帧
 ├─ [PL] preproc：BGR → 320×320 RGB 平面（18.8 ms，LUT 7%）
 │        └─ 可选 track_preproc：同一 IP 另出 320×320 白/运动掩码（D019，已出片）
 ├─ [PS] ncnn person 检测（约 0.95 s/帧）
 ├─ [PS] SORT 跟踪 + 机位运动补偿（可选 PL 运动门控）
 ├─ [PS] 球：白块候选 → C 分类器(8 ms) → 轨迹确认（每 2 帧）
 ├─ [PS] 单应 → 球场坐标（有标定时）
 ├─ [PS] 事件：出界 / 进球 / 球权（FIFA 整球过线口径，可调）
 └─ [PS] 输出：SD JSONL（result_store）/ PC Dashboard（result_stream）/ HDMI-LCD（status_display）
        └─ [PL] HDMI 带框环出（D026：fb_scanout 经 HP0 读 DDR，pclk 74.25 MHz 同源）
```

## 实测指标

| 模块 | 结果 |
|---|---|
| 球员检测 | 0.95–1.0 s/帧，与 PyTorch 差约 2 px |
| 跟踪 | 帧间位移 0.2 px，全程 34 个 ID |
| 球场坐标 | 6 点标定，残差均值 1.11 m |
| 镜头有效性 | 三态；无效帧不输出坐标 |
| PL 预处理 | 18.8 ms，WNS +3.72 ns，LUT 3,812 |
| 球候选召回 | 87.3%（白块法，已排除转播四角）|
| 球分类器 | P 0.67 / R 0.85（th=0.7），板上 8 ms/候选 |
| 球链路确认 | 逐帧 80%（16/20 已知球，15 px 内）|
| 球片段标定（中圈 5 点） | 残差 **0.11–0.56 m**（均值 0.33 m）|
| 板上球输出（已标定片段） | **144/202 帧全部为球场坐标**，`events` 同步输出 |
| 事件判定 | 144 帧全部 `out_of_play=false`（球未越线，符合画面）|
| 球权判定 | 144 帧中 **49 帧**给出球员 ID（最近距离 1.87 m）|

## 运行方式（板上）

```bash
# 完整管线（PL 预处理 + 事件）
python3 tools/board_pipeline.py --video <clips.mp4> --every 25 \
  --max-det 0 --ball-every 2 --pl --calib data/calib/field_calib.json \
  --proposal --out /tmp/out.json
```

输出每帧：`players[]`（id/球场x,y/conf）、`ball`（x,y/valid/space）、
`camera_state`、`events`（出界/进球/球权）。

> ⚠️ 上述 202 帧运行的原始 `/tmp/out.json` **未随包归档**；指标汇总在本表，
> 过程记录见 `ai/sessions/010_board_pipeline_ball_backfill.md`。这是证据链的已知缺口，
> 报告 §8 已如实登记。

## 已知边界

1. 单应只对**标定过的机位**有效；未标定时球退化为图像坐标、`events` 不输出；有效区域判定已从“标定点多边形”改为“球场范围 ±5 m”（5 点集中时多边形太窄，会误丢球）；
2. 球在快速移动/遮挡时确认率下降（约 28–70% 帧有球）；
3. 出界口径默认“球心越线 0.11 m”，可在 `tools/events.py` 调整；
4. 板子偶发整机复位（已确认非镜像问题，满载压测 20 分钟正常）；
5. **球帧数不能用容错参数放大**：`--ball-lost-sec 0.4` 在板上把球帧从 144 抬到 182（+26.4 %），
   但人工抽查新增 38 帧**全部不是球**（静态白块被黏住）⇒ 该开关**已否决、默认关闭**；
   根因是候选质量，不是丢失窗口参数（`ai/sessions/012`）。
6. **球链路的第一决定因素是分辨率（2026-10-27 实测）**：仓库内
   `data/samples/incidents/*.mp4` 是 **640×360 降采样**版；同一段 30 s 出界片段用同一套球链路
   （候选 → 分类器 th 0.5 → 轨迹确认）逐帧确认率只有 **0.1 % / 0.0 % / 0.4 %**；
   从母带 **1280×720 重切**后为 **5.9 % / 19.1 % / 38.7 %**（inc_06 / 07 / 12）。
   ⇒ **用 360p 片段评估球能力会得出错误的「不可用」结论**；写报告引用球指标时必须写明源分辨率。
   工具 `tools/ball_chain_clip.py`，汇总 `data/results/ball_chain_clips.json`。
7. **出界瞬间的镜头不可默认**：inc_06 在越线时刻切成近景，球被人抱在手里、不在草皮上；
   白块法只在草皮包围盒内找白块 ⇒ 该帧候选为 0。`ball_cv_probe.candidates(grass_fallback=True)`
   是为「球落在广告牌/看台背景」加的回退（**默认关闭**，不改板上行为）。inc_12 的远射偏出片段
   在 22.4–25.0 s 连续确认 44 帧（人眼核对为真球）⇒ 球链路能跟到越线，但**该机位未标定**，
   因此仍不输出出界方向。

## 扩展功能（2026-10-25，D018/D019）

| 功能 | 落点 | 状态 |
|---|---|---|
| PL 跟踪辅助（预处理+运动/白块掩码，单 IP） | `src/hls/track/src/track_preproc.cpp`、`build/scripts/bd_track_preproc.tcl`、`tools/track_preproc.py` | **已出片** `board/overlay/track_preproc.bit`（4.05 MB；BD LUT 7,374 / FF 8,340 / DSP 13 / BRAM 3，WNS **+2.457 ns**）；**上板逐位对通过**（TRACK_PREPROC_BOARD_OK；720p 32.39 ms；管线内 20.03 ms） |
| SD 卡增量存储 | `tools/result_store.py`（JSONL + summary + fsync/轮转） | `RESULT_STORE_SELFTEST OK` |
| 板上结果推 PC | `tools/result_stream.py` | 本地回环测试 `RESULT_STREAM_OK sent=2` |
| PC 端软件 | `tools/pc_dashboard.py`（TCP/回放 + 球场画布 + CSV） | `PC_DASHBOARD_SELFTEST OK`，本地端到端 3 帧通过 |
| HDMI 状态面板（PL overlay） | `src/rtl/io/hdmi/`、`build/scripts/bd_hdmi_status.tcl`、`tools/bram_fb.py` | **已出片** `board/overlay/hdmi_status.bit`（1bpp BRAM 28,800×32，基址 0x40000000）；**BRAM_OK 通过**：`BRAM_FB_FRAME_OK 1280x720` + 上板 `HDMI_LIVE`（扫描范围/DE/59.2 fps）；**VISUAL_OK 待人眼**。读侧已在 019 重写为预取移位寄存器（原缺陷：把「地址到数据 1 拍」写死 + `pixel_data` 寄存器输出被组合选通） |
| LCD 状态面板（**10-29 整体重做**） | `src/rtl/io/lcd/lcd_probe_core.v`、`lcd_probe_top.v`、`build/scripts/bd_lcd_probe.tcl`、`tools/check_lcd4_board.py` | **已出片** `board/overlay/lcd_probe.bit`（10-29 23:11；800×480@33.333 MHz **4bpp/16 色**，48,000 图像字 + 16 调色板字 = 192 KB，AXI 段 256 K；480×272@9.000 MHz 核心供 A/B；cfg 切核心/图案/帧缓存；按键 `key[0]=L14/key[1]=K16` @ `0x41260000`；WNS **+1.839 ns** / WHS **+0.018 ns**）。**未上板**：BRAM_OK 与 VISUAL_OK 都没做。历史：016 把像素时钟从 25 改到 33.333 MHz（25 MHz+1056×525=45.1 Hz，屏按 60 Hz 设计）；017 复测乱码；018/020 重写读侧；020 板端 480×272 基本正确但用户要求 800×480；021 按用户要求从零重做 |
| LCD 4bpp 赛前 UI（D013） | `tools/lcd_ui.py`、`tools/match_setup.py` | schema `zynq-yolo/match-config/2`，7 项（A/B 球衣、A/B 守门员、裁判、上半场方向、开始比赛）；确认时校验两队不同色；落盘 `board/ui_config.json`；主机侧回放 `LCD_UI_SAVED`；**真按键未按过** |
| LCD 软件回退 | `tools/status_display.py`（fbdev/PNG） | `STATUS_DISPLAY_SELFTEST OK` |
| SD 证据帧 | `tools/result_store.py::save_evidence`、`board_pipeline --store-evidence` | `RESULT_STORE_OK`（evidence=2；限流 6/分钟）；**未上板端到端** |
| PC 上位机界面 | `tools/pc_dashboard.py`（状态条/球场/事件流/证据帧/12 列 CSV） | `PC_DASHBOARD_SELFTEST OK`；界面 PNG 已人眼核对 |
| LCD/HDMI 状态面板 8 项 | `tools/status_display.py`（800x480 / 1280x720） | `STATUS_DISPLAY_SELFTEST OK`；两尺寸 PNG 已人眼核对 |
| HDMI 视频环出（带框，D026） | `src/rtl/io/hdmi/fb_scanout.v`、`hdmi_video_top.v`、`build/scripts/bd_hdmi_video.tcl`、`tools/hdmi_video.py` | 仿真 `TB_FB_SCANOUT_OK`（2048 像素逐点 0 错，覆盖 stride≠width×4）；`HDMI_VIDEO_SELFTEST OK`（32bpp 打包往返 dmax=0）；主机侧 OSD 出图正确（图 `report/figures/hdmi_osd_boxes.png`，坐标为演示给定）；**`hdmi_video.bit` 从未上板，`under` 未观测** |

板上命令（板端）：

```bash
# PL 跟踪辅助 + SD 存储 + 结果文件
python3 tools/board_pipeline.py --video <clips.mp4> --every 25 --max-det 0 \
  --ball-every 2 --pl-track --motion-gate \
  --calib data/calib/field_calib.json --proposal \
  --store-dir /home/xilinx/zynq-yolo --store-fsync 30 --store-max-mb 64 \
  --out /home/xilinx/out.json

# 推送最新 JSONL 给 PC（另开一个窗口）
python3 tools/result_stream.py --file /home/xilinx/zynq-yolo/run_*.jsonl \
  --host <PC_IP> --port 5005 --follow

# 有 fbdev 时直接输出状态面板（HDMI:1280x720 / LCD:800x480）
python3 tools/status_display.py --replay /home/xilinx/out.json \
  --size 800x480 --fb /dev/fb1
```

PC 端：

```powershell
python tools\pc_dashboard.py --listen 0.0.0.0:5005 --csv session.csv
python tools\pc_dashboard.py --replay data\results\run_*.jsonl --speed 1
```

> **边界**：`status_display.py` 只负责把状态面板渲染到已存在的 framebuffer；
> 若 PYNQ 镜像没有 `/dev/fbX`，可加载已出片的 PL 显示 overlay，用 `--display-bit` 写帧缓存。
> **判据分两级，全文统一**：`BRAM_OK` = 写得进、读得回、扫描范围/速率对（机器可判）；
> `VISUAL_OK` = 屏幕上真的看到且内容正确（只能人眼）。
> 当前：HDMI 状态面板 `BRAM_OK` 通过；**LCD 4bpp 新片两级都没做**（未上板）；
> 合并版 `display_both.bit`/`track_display.bit` 落后于 018–021 的新读侧，需重出片。
> 任何一级都不得代替另一级 —— 本文件此前把 `DISPLAY_BOARD_OK` 与「画面已通过」混写，
> 2026-10-30 已按此口径更正。

## 已有验证与待办

- **已完成**：中圈片段 5 点标定 + 202 帧板上运行，144 帧球场坐标、49 帧球权、
  `events` 同步输出（`report/design_report.md` §2.7/§4.6）。
- **仍待人工输入**：其它机位各标一次（`tools/label_field_points.py`），才能验收新机位的 `events`；
  多机位可后续做镜头切换检测 + 标定切换。
- **未完成**：球 backfill 结构改动（`ai/sessions/010`）；倒地识别仍是候选粗筛：只输出**候选**，
  4 帧门槛后强层 4 个候选 = 3 真 + 1 假（#8279 弯腰误判），**精度 3/4**，
  这不是检出率、也不能外推（`docs/player_tracking_v1.md` §7）。
- **未完成（2026-10-30 明确登记）**：
  1. **视频入口还是本地文件**：D015 已裁定「PC 经以太网送入」，但 `board_pipeline.py` 只吃
     `--video <板本地文件>`；`tools/stream_sender.py` / `src/ps/host/board_net.py` 是早期
     `conv_engine` 的流式 bring-up（TCP + `ZYOL` 头），未接主线。协议/端口仍 `discussing`。
  2. **队别已接入主线但未在真实画面/板上验证**：`tools/team_assign.py` 把 `match-config/2` 色板
     转成 `classify_players.classify()` 的 palette，并按 `track_id` 投票（≥3 票、多数占比 ≥0.6）；
     `board_pipeline.py` 新增 `--ui-config/--no-team`（默认读 `board/ui_config.json`），
     输出 `players[*].team` 与 `events.possession_team`。
     主机侧 `TEAM_ASSIGN_SELFTEST OK`；真实片段（不重跑检测，直接吃已有 tracks JSON）
     inc_01 `labeled=55/77 A=28 B=27`、inc_06 `labeled=53/64 A=21 B=32`
     （`tools/check_team_assign.py`）——**这不是准确率**，没有人工队别真值。
     **板上还没验**。离线 `tools/track_players.py` 仍不输出队别（轨迹为像素、未接单应）。
  3. **显示两级判据的未做部分**见上文「边界」。
  4. 202 帧板上运行的原始 `/tmp/out.json` 未随包归档（证据链缺口）。