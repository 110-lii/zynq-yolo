# HDMI / LCD 显示 overlay 方案（历史方案：1bpp 面板）

> 上级：[`../README.md`](../README.md) · 相关：[`system_status.md`](system_status.md)、
> [`flow_overlay.md`](flow_overlay.md)、[`hdmi_video_osd.md`](hdmi_video_osd.md)、
> [`../report/design_report.md`](../report/design_report.md) §2.9/§8。
> 状态：**本文记录 D020–D024 的 1bpp 方案**。现行主线已改为 `flow_system.bit`
> （LCD 4bpp + HDMI 32bpp 视频，见 `flow_overlay.md`）；1bpp 状态面板 `hdmi_status.bit`
> 的读回通过，但**画面未做人眼终验**，不作为提交时的「已显示」证据。
> 按 DECISIONS 纪律，Vivado 综合/出片前须用户批准。

## 为什么不能只靠 `status_display.py`

`status_display.py` 只做“渲染 + 写已有 framebuffer”。如果 PYNQ 镜像没有把 HDMI/LCD
注册成 `/dev/fbX`，它只能出 PNG（会明确打印 `STATUS_DISPLAY_NO_FB`）。
板卡的 HDMI/LCD 都接在 PL 引脚上（`ref/hardware.md` §F/§G），因此要真正显示，
需要一个 **PL 显示 overlay**。

## 方案 A：HDMI 状态面板（1bpp framebuffer + TMDS）

- **分辨率/时序**：1280×720@30（vendor `video_driver.v` 参数：H_TOTAL 1650、V_TOTAL 750，
  pixel clock 74.25 MHz，5× 371.25 MHz）。
- **帧缓存**：1bpp 1280×720 = **115,200 B**，约 **29 个 RAMB36**（140 可用，余量足够）；
  PS 只写状态面板变化区域（整帧 28,800 个 32-bit 字，1 FPS 下 AXI-Lite 可承受；
  更高刷新率改用 HP + DMA）。
- **读侧**：按 pixel_xpos/ypos 读 1 bit → 0/1 映射背景/前景色 → 24-bit RGB。
- **TMDS**：复用 vendor `dvi_transmitter_top.v` / `dvi_encoder.v` / `serializer_10_to_1.v`
  （需保留版权头；若比赛不允许复用，需用 `selectio` 原语重写）。
- **引脚**（vendor XDC）：`tmds_data_p[2:0]` = J20/K19/G19，`tmds_clk_p` = J18，
  `IOSTANDARD TMDS_33`；`sys_clk` U18、`sys_rst_n` N16。
- **时钟**：`clk_wiz` 50 MHz → 74.25 / 371.25 MHz；PS FCLK 83.33/100 MHz 只用于 AXI-Lite。
- **PS 接口**：`s_axi_control`（面板基址、刷新标志）+ 一块 BRAM 写窗口；
  或 AXI-Stream + DMA。首版建议 AXI-Lite，逻辑最小。

### 已实现（D020）

| 项 | 值 |
|---|---|
| 比特流 | `board/overlay/hdmi_status.bit`（4,045,674 B）+ `.hwh` |
| 时钟 | 外部 `sys_clk` U18 50 MHz → `clk_wiz` 74.25 / 371.25 MHz |
| 帧缓存 | 1bpp，1280×720，28,800×32-bit，AXI BRAM 基址 **0x40000000**，范围 128 KB |
| 写侧 | `tools/bram_fb.py`（PYNQ MMIO 或 `/dev/mem`），`status_display.py --fb-bram 0x40000000` |
| 实现报告 | `build/reports/timing/hdmi_status_bd_timing.rpt`、`build/reports/resource/hdmi_status_bd_util.rpt` |
| 时序 | Setup **+3.938 ns**、Hold **+0.065 ns**、PW +5.146 ns（0 failing） |
| 资源 | BD 报告 LUT **1,663** / FF **1,379** / DSP **1**；`blk_mem_gen` OOC 为 **32×RAMB36** |
| 验证边界 | **帧缓存读回通过（D022；DISPLAY_BOARD_OK），画面待目视**；不写成“已显示” |

## 方案 B：LCD 状态面板（800×480 RGB888）—— 已出片（D021）

| 项 | 值 |
|---|---|
| 比特流 | `board/overlay/lcd_status.bit`（4,045,674 B）+ `.hwh` |
| 时钟 | 外部 `sys_clk` U18 50 MHz → `clk_wiz` 33.333 MHz |
| 帧缓存 | 1bpp，800×480，12,000×32-bit，AXI BRAM 基址 **0x40000000**，范围 64 KB |
| 显示时序 | `lcd_timing`（项目内，DE 模式，30 MHz，Th 928 / Tv 525，800×480） |
| 实现报告 | `build/reports/timing/lcd_status_bd_timing.rpt`、`build/reports/resource/lcd_status_bd_util.rpt` |
| 时序 | Setup **+4.283 ns**、Hold **+0.121 ns**、PW +5.146 ns（0 failing） |
| 资源 | BD 报告 LUT **548** / FF **637** / DSP **1**；`blk_mem_gen` OOC 为 16×RAMB36 |
| 验证边界 | **帧缓存读回通过（D022；DISPLAY_BOARD_OK），画面待目视** |

- **时序**：800×480，pixel clock 30.0 MHz（`lcd_timing.v`；DE 模式，HS/VS 恒高，
  行场由 DE 划分；porch 参数与 `ref/hardware.md` §G.3 一致）。
- **帧缓存**：1bpp 800×480 = **48,000 B**，约 **12 个 RAMB36**。
- **引脚**：`lcd_rgb[23:0]`、`lcd_hs`、`lcd_vs`、`lcd_de`、`lcd_clk`、`lcd_bl`、`lcd_rst`
  （vendor XDC，W18…/N18/T20/U20/P19/M20/L17）。⚠️ **LCD 与 J3 互斥**（`ref/hardware.md` §F.2）。
- **驱动**：项目内 `lcd_timing.v` + `lcd_fb_reader.v`（A/B 双行缓存预取），像素数据来自 1bpp BRAM 读侧（同 HDMI 的位图格式）。
- **PS 接口**：同 HDMI 的 AXI-Lite 写窗口。

## 软件侧已经就绪

`tools/status_display.py` 的属性：
- `render(st, w, h)` → BGR 图；
- `write_fb(img, "/dev/fbX")` → 32bpp BGRA / 16bpp RGB565；
- `--png` / `--window` 回退。

**接入显示 overlay 时不需要改 PC/上层逻辑**：只要把 1bpp 位图写到 BRAM 窗口，
PL 读侧按上面的时序输出即可。PS 侧一帧 800×480 1bpp 的打包/写入脚本可作为
`tools/status_display.py --fb` 的替代路径。

## 方案 C：合并 HDMI+LCD（D023，已出片）

一块 overlay 同时刷新两块屏，避免来回加载：

| 项 | 值 |
|---|---|
| 比特流 | `board/overlay/display_both.bit`（4,045,674 B）+ `.hwh` |
| HDMI 帧缓存 | `0x40000000`，32,768×32-bit（用 28,800） |
| LCD 帧缓存 | `0x40020000`，16,384×32-bit（用 12,000） |
| AXI | 两个独立单端口 `axi_bram_ctrl`，挂在同一 GP0 smartconnect |
| 时钟 | 50 MHz → 74.25 / 371.25 / 33.333 MHz |
| 资源/时序 | LUT **908** / FF **923** / BRAM **48**；Setup **+3.874 ns**、Hold **+0.105 ns** |
| 上板 | `DISPLAY_BOTH_BOARD_OK`；HDMI 28,800 / LCD 12,000 字读回一致；双屏画面待目视 |

```bash
python3 tools/status_display.py --replay /tmp/sdtest3/run_*.jsonl \
  --size 1280x720 --display-bit board/overlay/display_both.bit \
  --also-lcd-base 0x40020000 --fps 1
```

## 方案 D：跟踪 + 双屏（D024，已出片/上板）

`track_display.bit` 在同一个 overlay 内同时放：

- `track_preproc`（HP0–HP3，track 控制 `0x40000000`）；
- HDMI 1bpp BRAM `0x40020000`（128 KB）；
- LCD 1bpp BRAM `0x40040000`（64 KB）。

资源/时序：LUT **7,984** / FF **8,835** / DSP 13 / BRAM **51**；
Setup **+2.918 ns**、Hold **+0.036 ns**。上板 `TRACK_PREPROC_BOARD_OK`、
`TRACK_DISPLAY_BOARD_OK`，同一 overlay 跑 20 帧管线 `PIPELINE_OK`。
这样 `--pl-track` 与双屏可以同时工作。

```bash
python3 tools/status_display.py --replay /tmp/live/run_*.jsonl --size 1280x720 \
  --display-bit board/overlay/track_display.bit \
  --fb-bram 0x40020000 --also-lcd-base 0x40040000
```

## 出片门禁（必须按顺序）

1. 复用/重写 RTL 的版权与引脚复核（HDMI/LCD XDC 均来自 vendor 例程）；
2. `clk_wiz` + 1bpp BRAM + 读侧 + TMDS/LCD 驱动的仿真（至少 1 帧逐像素）；
3. BD 集成 + XDC + 时序（pixel clock 与 5× 时钟必须过 WNS）；
4. 出片 + 上板：先显示固定测试图，再显示 PS 写入的状态面板；
5. 把资源/时序/实拍写入 `build/reports/`、`board/logs/`，报告才可写“已验证”。

> 当前状态：HDMI（D020）/ LCD（D021）/ 合并双屏（D023）/ 跟踪+双屏（D024）的
> **出片与帧缓存读回均已完成**（`DISPLAY_BOARD_OK`、`DISPLAY_BOTH_BOARD_OK`、
> `TRACK_DISPLAY_BOARD_OK`）；**屏幕上的实际画面仍待目视确认**（脚本只证明数据链路）。
> 板子在 2026-10-25 测试 vendor 彩色条时掉线（D025），需重新上电后补实拍；
> 报告 §8 第 8 条已如实登记。
