# 系统状态（2026-10-25）

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

## 扩展功能（2026-10-25，D018/D019）

| 功能 | 落点 | 状态 |
|---|---|---|
| PL 跟踪辅助（预处理+运动/白块掩码，单 IP） | `src/hls/track/src/track_preproc.cpp`、`build/scripts/bd_track_preproc.tcl`、`tools/track_preproc.py` | **已出片** `board/overlay/track_preproc.bit`（4.05 MB；BD LUT 7,374 / FF 8,340 / DSP 13 / BRAM 3，WNS **+2.457 ns**）；**上板逐位对通过**（TRACK_PREPROC_BOARD_OK；720p 32.39 ms；管线内 20.03 ms） |
| SD 卡增量存储 | `tools/result_store.py`（JSONL + summary + fsync/轮转） | `RESULT_STORE_SELFTEST OK` |
| 板上结果推 PC | `tools/result_stream.py` | 本地回环测试 `RESULT_STREAM_OK sent=2` |
| PC 端软件 | `tools/pc_dashboard.py`（TCP/回放 + 球场画布 + CSV） | `PC_DASHBOARD_SELFTEST OK`，本地端到端 3 帧通过 |
| HDMI 状态面板（PL overlay） | `src/rtl/io/hdmi/`、`build/scripts/bd_hdmi_status.tcl`、`tools/bram_fb.py` | **已出片** `board/overlay/hdmi_status.bit`（4.05 MB；1bpp BRAM 28,800×32，基址 0x40000000；Setup +3.938 ns / Hold +0.065 ns）；**帧缓存数字通路读回通过**（DISPLAY_BOARD_OK；D022 单端口修复）；**HDMI 画面已通过**（014：1280×720 棋盘格人眼可见）。根因是 BRAM 读口 `rstb` 极性接反——`peripheral_aresetn`（低有效）直连高有效的 `rstb`，读口全程复位、`doutb` 恒 0；另修 fb_reader 左移却取 `sr[0]`、`pixel_xpos` 从 2 起而字边界等 1 |
| LCD 状态面板（PL overlay） | `src/rtl/io/lcd/`、`build/scripts/bd_lcd_status.tcl`、`tools/bram_fb.py` | **已出片** `board/overlay/lcd_status.bit`（4.05 MB；1bpp BRAM 12,000×32，基址 0x40000000；lcd_id=4384（4.3 寸 800×480）；Setup +4.283 ns / Hold +0.121 ns）；**帧缓存数字通路读回通过**（DISPLAY_BOARD_OK；D022 单端口修复）；**LCD 画面已通过**（014：800×480 棋盘格人眼可见）。与 HDMI 同一组缺陷：BRAM 读口 `rstb` 极性接反（读口常复位）+ fb_reader 移位方向 + `pixel_xpos` 偏移；像素时钟改用 25 MHz（与 oi 项目一致） |
| LCD 软件回退 | `tools/status_display.py`（fbdev/PNG） | `STATUS_DISPLAY_SELFTEST OK` |

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
> 若 PYNQ 镜像没有 `/dev/fbX`，可加载已出片的 PL 显示 overlay（D020–D024：HDMI TMDS /
> RGB LCD / 合并双屏 / 跟踪+双屏），用 `--display-bit` 写 1bpp BRAM。帧缓存读回均已通过，
> **单屏画面已通过（014）**；`DISPLAY_BOARD_OK` 本身只证明
> 帧缓存数字通路，不等于画面能显示，报告 §8 已登记。

## 已有验证与待办

- **已完成**：中圈片段 5 点标定 + 202 帧板上运行，144 帧球场坐标、49 帧球权、
  `events` 同步输出（`report/design_report.md` §2.7/§4.6）。
- **仍待人工输入**：其它机位各标一次（`tools/label_field_points.py`），才能验收新机位的 `events`；
  多机位可后续做镜头切换检测 + 标定切换。
- **未完成**：球 backfill 结构改动（`ai/sessions/010`）；倒地识别仍是候选粗筛：只输出**候选**，
  4 帧门槛后强层 4 个候选 = 3 真 + 1 假（#8279 弯腰误判），**精度 3/4**，
  这不是检出率、也不能外推（`docs/player_tracking_v1.md` §7）。