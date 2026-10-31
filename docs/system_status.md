# 系统状态（2026-10-31）

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
| HDMI 状态面板（PL overlay） | `src/rtl/io/hdmi/`、`build/scripts/bd_hdmi_status.tcl`、`tools/bram_fb.py` | **已出片** `board/overlay/hdmi_status.bit`（1bpp BRAM 28,800×32，基址 0x40000000）；**BRAM_OK 通过**：`BRAM_FB_FRAME_OK 1280x720` + 上板 `HDMI_LIVE`（扫描范围/DE/59.2 fps）；**VISUAL_OK 待人眼**（10-31 的 `HDMI_VISUAL_OK` 只覆盖 `flow_system.bit` 的视频通路，不含本 1bpp 状态面板）。读侧已在 019 重写为预取移位寄存器（原缺陷：把「地址到数据 1 拍」写死 + `pixel_data` 寄存器输出被组合选通） |
| LCD 状态面板（**10-29 整体重做**） | `src/rtl/io/lcd/lcd_probe_core.v`、`lcd_probe_top.v`、`build/scripts/bd_lcd_probe.tcl`、`tools/check_lcd4_board.py` | **已出片** `board/overlay/lcd_probe.bit`（10-29 23:11；800×480@33.333 MHz **4bpp/16 色**，48,000 图像字 + 16 调色板字 = 192 KB，AXI 段 256 K；480×272@9.000 MHz 核心供 A/B；cfg 切核心/图案/帧缓存；按键 `key[0]=L14/key[1]=K16` @ `0x41260000`；WNS **+1.839 ns** / WHS **+0.018 ns**）。**10-30 上板**：`READBACK_OK bad=0 checked=10`（重启前后各一次，PCLK 33.335 MHz）⇒ `BRAM_OK` 过；`tag4` 自证图**人眼确认**（斜向 16 色条纹）⇒ 该图 `VISUAL_OK` 过。判据例外：第 0/1 行只判 `lb`（RTL 的 `st/cnt` 在上一行装载拍更新，0 被排除、1 撞消隐行），脚本已写清。历史：016 把像素时钟从 25 改到 33.333 MHz；017 复测乱码；018/020 重写读侧；020 板端 480×272 基本正确但用户要求 800×480；021 从零重做 |
| LCD 4bpp 赛前 UI（D013） | `tools/lcd_ui.py`、`tools/match_setup.py` | schema `zynq-yolo/match-config/2`，7 项（A/B 球衣、A/B 守门员、裁判、上半场方向、开始比赛）；确认时校验两队不同色；落盘 `board/ui_config.json`；主机侧回放 `LCD_UI_SAVED`；**真按键未按过** |
| LCD 软件回退 | `tools/status_display.py`（fbdev/PNG） | `STATUS_DISPLAY_SELFTEST OK` |
| SD 证据帧 | `tools/result_store.py::save_evidence`、`board_pipeline --store-evidence` | `RESULT_STORE_OK`（evidence=2；限流 6/分钟）；**未上板端到端** |
| PC 上位机界面 | `tools/pc_dashboard.py`（状态条/球场/事件流/证据帧/12 列 CSV） | `PC_DASHBOARD_SELFTEST OK`；界面 PNG 已人眼核对 |
| LCD/HDMI 状态面板 8 项 | `tools/status_display.py`（800x480 / 1280x720） | `STATUS_DISPLAY_SELFTEST OK`；两尺寸 PNG 已人眼核对 |
| HDMI 视频环出（带框，D026） | `src/rtl/io/hdmi/fb_scanout.v`、`hdmi_video_top.v`、`build/scripts/bd_hdmi_video.tcl`、`tools/hdmi_video.py` | 仿真 `TB_FB_SCANOUT_OK`（2048 像素逐点 0 错，覆盖 stride≠width×4）；`HDMI_VIDEO_SELFTEST OK`（32bpp 打包往返 dmax=0）；主机侧 OSD 出图正确（图 `report/figures/hdmi_osd_boxes.png`，坐标为演示给定）；**10-30 上板 `HDMI_VIDEO_BOARD_OK frames=313 under=0`**（305 帧内行欠载 0）⇒ `BRAM_OK` 级过；**VISUAL_OK 已过**（2026-10-31 13:56 人眼确认 13:37 片「全对」；同一片固化 overlay 上跑真视频环出 `HDMI_VIDEO_OK` 25 帧 `under=0`） |

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
> 当前（2026-10-30 上板）：HDMI 状态面板 `BRAM_OK` 通过；**LCD 4bpp `READBACK_OK bad=0 checked=10`
> 通过，且 `tag4` 自证图人眼确认**；**HDMI 带框环出 `under=0` 通过（机器级）**；
> 已过人眼：LCD 自证图、彩色状态面板、赛前 UI（真按键）、HDMI 彩条与带框环出（2026-10-31 13:56，证据 `data/results/hdmi_visual_ok.json`）。
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
     **板上还没验**。离线 `tools/track_players.py` 不输出每人的队别（只有颜色过滤）；`--calib` 可把脚点映射到米制（`points[].X/Y`，输入 `sx/sy`）。
  3. **显示两级判据的未做部分**见上文「边界」。
  4. 202 帧板上运行的原始 `/tmp/out.json` 未随包归档（证据链缺口）。
## 分层测试集与全流程 overlay（2026-10-30）

- **测试集分层**（`tools/split_scenes.py`）：从整场比赛按「人员聚集度」切成
  `contest`（争抢）/`normal`（普通）两类，**各 8 段** × 10 s；两道硬门槛
  （草皮占比 ≥0.35、人物框中位高 ≤0.30 画面高）排掉名单图与特写。
  191 个探针点被剔除，保留 103 个有效点（`data/samples/scenes/MANIFEST.csv`）。
  扩样本走 `--reuse`（复用 `_probe.json` 的逐点打分，不重扫全场，分钟级）。
  **标签是几何代理，不是人工真值**（`human_ok` 列留空等人填，`tools/scene_sheet.py` 出对照图）。
- **分层测试表**（`tools/check_scene_suite.py`，主机 640@0.25，各 8 段 × 50 帧）：
  争抢段球检出 **15.8%**、普通段 **20.2%**；聚集度 0.159 vs 0.298。
  **要分开说**：聚集度两类完全不重叠（p=0.0002），但那是按聚集度定义切出来的构造性质；
  球检出的差异**不显著（置换检验 p=0.61）**，逐段 0%–52%。
  所以「争抢画面球更难检出」这条**不成立**（5+5 时的 11.6% vs 19.6% 是小样本巧合）。
  站得住的只有：**普通场景里球也只有约 1/5 的帧检得到** —— 这解释了 `ball_gate` 的必要性。
- **全流程固化 overlay**（`flow_system.bit`）：LCD 4bpp + 按键 + HDMI 32bpp 合进一个
  bitstream，一次加载小屏大屏同时可用。地址只迁了两条（`gpio_id`/`gpio_t` →
  `0x4127/0x4128`），其余工具零改动。详见 `docs/flow_overlay.md`。
  **2026-10-30 已上板** `flow_overlay.py --selftest`：LCD `bad=0/16` + pclk 计数、
  HDMI `frames 564→1056 under=0` ⇒ `BRAM_OK` 过。
  **人眼分两半**：LCD 那一屏（8 条 100 px 竖彩块）**已由用户确认** ⇒ `LCD_VISUAL_OK`；
  HDMI 那一半**未过** —— 详见下一小节「HDMI 帧缓存段」。
- **HDMI 帧缓存段（2026-10-30 20:3x–22:0x）**：五色卡 + 收尾横幅跑完
  （`frames 6105→7916 under=0`）人眼仍啥也看不到。用 `hdmi_testpattern.bit`
  （纯 RTL 彩条，不碰 DDR）切链：**人眼看到 8 条彩条** ⇒ TMDS/像素时钟/引脚/显示器全好，
  故障在「DDR 帧缓存 → `fb_scanout`」这一段。`under=0` 不能证明数据对（读回全零时它同样是 0），
  `Buffer.flush()` 也**没有**修好它。定位脚本 `build/fb_diag.py`：写完一屏纯色后
  用 `/dev/mem` 绕过缓存把同一物理地址读回逐字比对（机器判），再给人眼看红/绿/蓝黄三屏。
- **HDMI 是三处独立缺陷叠在一起**（同日夜里定位，见 `ai/sessions/022` §7.13、§7.15）：
  ⓪ `bd_flow_system.tcl` 漏了 HP0 的 DDR 地址段 —— `hdmi_video_0/m_axi` **没有地址空间**，
  构建时只有一条 `CRITICAL WARNING [BD 41-1356]`。后果：读请求在互连里解不到从设备，
  但 `frames` 60/s、`under=0`、cfg 写读闭环全绿，只有屏上一个颜色不变。
  已补显式分配 + 构建期断言（分配不到直接 `FLOW_SYSTEM_BD_FAIL`）。
  ① `fb_scanout` 行推进写成 `de && (local_x == width_use-1)`，而真实时序里
  `local_x` 最大 1277 ⇒ 条件永不成立 ⇒ **每行都读第 0 条行缓存**（整屏纯色/竖条对、
  横条和文字全丢），且 `st_under` 对这个缺陷**永远报 0**。2026-10-31 改为
  **`cur_line = ypos-1`**（`video_driver` 自己的场计数，由构造保证），不再数 de 拍——
  前两版（等式判据、脉冲计数）都依赖推出来的相位，这是第三次也是唯一不靠推的写法。
  ② `flow_system.bit` 里 `0x41200000`（HDMI cfg）写不进去、读回的是 `0x41210000`
  的状态值 —— 配置从未进过 PL，屏上必然全黑。已把 cfg 挪到外层 `axi_smc`（FCLK 域，
  与 LCD 那几个已验证可写的 GPIO 同一条总线），状态 GPIO 留 pclk 域。
  **三处都还没拿到人眼结论**，`HDMI_VISUAL_OK` 不记。
- **23:30–23:40 上板进展（§7.15）**：地址段补上后，PL 侧新增的**帧像素累加和**
  （`gpio_hdmi_st` 通道 2，`0x41210008`）在五种纯色下各自反解出 914,087–914,335 个像素、
  彼此一致（921,600 的 99.2%）⇒ **DDR 读回的确实是主机写进去的像素**，DDR 段通过。
  人眼：`cfg_pack[28]` 内置彩条（不碰 DDR）**看到了，且最左段是按行灰阶** ⇒
  TMDS / 像素时钟 / 引脚 / 显示器 / 行计数全对。切回帧缓存后画面为
  「左边白、其余黑、白里有黑横条、还在闪」⇒ 剩余问题定位在
  **`fb_scanout` 的行缓存↔行号映射**，不在数据也不在 TMDS。几何探针
  `build/hdmi_geom_probe.py` 已就绪，板子 23:39 掉线，待上电重跑。
- **10-31 离线（板子离线，无上板证据）**：把 BLOCK 那次的累加和逐位反解
  （`M = sum·0xFFFFFF⁻¹ mod 2³²`）得 **92,978 个白像素**，正确值是 16,640 ⇒
  屏幕上**每一行的左边 128 列都是白的**，与用户描述逐字吻合 —— 是「每行都读到靠前的行」，
  不是少数几行错。同日仿真（新增整帧 TB + 系统级 TB，直接例化真的 `video_driver`）
  抓到三处真缺陷：每行最后一列恒黑（`in_range` 错与 `de` 相与）、
  `ahead` 无符号回绕会让取数**永久冻结**（放慢从机可复现）、`st_sum` 累加口径比屏幕早一拍。
  三处已修，`SIM_PASS 11/11`；**仍无任何人眼结论**，`HDMI_VISUAL_OK` 不记。
- **10-31 稍后：BLOCK 与 VRAMP 互相矛盾，改成直接量**。VRAMP 的 `0x3ACB0000`
  要求 `1280·S ≡ 15051 (mod 65536)`，而左边恒被 256 整除 ⇒ **无解**，
  说明「每一行都读靠前的行」这个模型本身不完整，不能再靠解析。新增**单行/单列探针**：
  整幅图只有源行 r 白，`sum = count·1280·0x00FFFFFF (mod 2³²)`，
  `0x00FFFFFF` 与 2³² 互素 ⇒ `count` 唯一可反解（列同理除 720）。
  同一条算式先在 `tb_hdmi_scanout_sys` 的 `+ROW=`/`+COL=` 模式里对真 RTL 跑过
  8/8（含「列 0 显示 3 次、列 1279 显示 0 次」两个边界），`CHECKED=1843200 ERRS=0`；
  探针已加进 `build/hdmi_geom_probe.py`（9 行 + 9 列，约 10 s）。
  **仍在等板子，无上板数据。**
- **板端分层跑表 16/16 段有数**（`tools/scene_suite_board.py`；8 争抢 + 8 普通，
  逐段落盘在 `data/results/scene_suite_board.json`）。每检测帧原始
  15.44（争抢）/ 20.05（普通）个框，过草皮门限后 12.30 / 16.25，
  与主机的 14.4 / 17.4 人同量级 ⇒
  **之前「瓶颈是 320 输入」的结论被自己数据推翻**；输出 `players` 接近 0 是
  `camera_state` 按设计判 `INVALID_VIEW`（`grass<0.20 → close_up_or_crowd`）后清空坐标，
  不是检测看不见。争抢段**当晚补齐 8/8**（`build/_contest_rows.json`），
  `rowsok` 列同时补齐；另：报告旧版「过 score/尺寸门限 14.9」查无出处，已撤（见报告表下注）。
- **10-31 上板两轮（板子真的连上了，见 `ai/sessions/022` §7.22）**：
  **第一轮**（旧 bit，12:07 探针）：行号 Σ=181,248（应 258,840，整体偏移 −107.77 行）、
  源行 716–719 完全没显示、每列只显示一半、`under` 一直是 0 ⇒ 钉死成**取数吞吐不足**：
  每行 20 个突发、每个都等数据全回，一行只有 1650 pclk，延迟一超 18 拍就永远追不上。
  改成「请求/写入游标解耦 + 4 个突发在飞」、`BURST` 64→255。
  **第二轮**（12:52 的 bit）：机器判据全绿 —— Σ=258,840 ✓、每个源行恰好 1 次（含 719）✓、
  源列 0 三次 ✓、VRAMP 累加和 MATCH ✓、`under=0`；人眼：**黑横条没了、画面不跳了**。
  同时暴露出第二处缺陷：**整幅图右移 2 像素、最左 3 列重复源列 0、源列 1278/1279 永不显示**。
  根因是 `video_driver` 的 `xpos` 在 de 拍上取 **C+1**（不是旧注释写的 0…1279），
  同步 BRAM 读要求读地址**就是 `xpos`**，不能减 2。已改（13:37 新 bit，WNS 4.184 ns），
  `SIM_PASS 12/12`，**但本机网口在部署前断了（以太网「媒体已断开连接」），这一版还没上过板** ——
  **13:50 上电后部署该片，13:56 人眼确认「全对」**（EDGES 左边缘纯白、右边缘纯红、
  绿方块居中；8 横带/8 竖带顺序与厚度正确、无黑横条、不跳；45 横 + 80 竖线一条不少；
  运动方块无撕裂）⇒ **`HDMI_VISUAL_OK` 记录生效**，证据 `data/results/hdmi_visual_ok.json`
  （绑定 sha256 `ad628200…104a`）。
- **`--max-det` 不是人数**：它是「检测调用次数上限」（默认 8）。板上跑表要 `--max-det 0`。
- **`HDMI_VISUAL_OK`（13:56）当时有效，但没有覆盖后来暴露的相位缺陷**：17:40 前后用户三次报
  「右侧画面跑到左边」，`build/hdmi_pixel_probe.py` 在旧片上读到 `px=(c+425) mod 1280`
  （整幅列循环移位 + 128 行滞后，每次上电不同）。根因是行缓存**没有行号握手**：
  `ready[]` 只按 `line & 3` 索引，4 的整数倍的行滞后隐形；取数落后时还会去写正在显示的槽。
- **19:24 新片（sha256 `1397ce6813f9…`，时序全收敛）**：每槽加 11 位行号标签 `ltag`、
  落后即刻 `skip_now` 对准当前显示行、`MAX_OUT 4→8`、`BURST 255→128`（不跨 4 KB）、
  `QDEP 12→16`。上板 `hdmi_pixel_probe` **28/28 全 `OK`**（`px == c` 恒等映射，含 c=0/1279 与
  r=0/719）。列方向**已由机器证明修好**；**行方向任何整帧标量都测不出**（possum 只含列号），
  必须人眼看屏 —— 真视频环出 `HDMI_VIDEO_OK 559 25.0 under=0`（n03）/ `status under=0`（c03），
  **等人眼结论，未记 `HDMI_VISUAL_OK`**。
- **带框视频回传（网口，用户授权的退路）**：`tools/video_back.py`；板端要 `--no-pl`、
  `--max-det 0`，无标定时加 `--no-camera-gate`（否则 `INVALID_VIEW` 会清空全部坐标），
  `--tracks` 必须给 **全流程 JSONL**（`build/flow/*.jsonl`），给 summary `.json` 会静默不出框。
