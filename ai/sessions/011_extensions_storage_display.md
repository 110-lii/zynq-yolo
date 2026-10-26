# Session 011 — 提交前扩展：PL 跟踪辅助 / SD / PC / 显示

| 字段 | 内容 |
|---|---|
| 会话号 | 011 |
| 日期 | 2026-10-25 |
| 执行 AI（模型 ID） | `magpie/group/1101` |
| 执行客户端 / 工具 | `kilo`（CLI，Windows / PowerShell） |
| 角色 | 单人 |
| 任务目标 | 按用户要求把部分算法放回 PL（跟踪相关），并补齐 SD 存储、HDMI/LCD 显示、PC 软件 |
| 上下文投喂 | `tools/board_pipeline.py`、`src/hls/preproc`、`src/hls/mask`、`ref/hardware.md`、`docs/system_status.md` |
| 关联决策 | `D018`（范围扩展）、`D019`（track_preproc）、`D020`–`D025`（显示 overlay / LCD 型号） |
| 复核方 | 未复核 |

## 结论先行

1. **PL 跟踪辅助**：新增 `track_preproc` HLS 顶层，把 `preproc`（BGR→320 RGB）与
   `mask_preproc`（白/运动掩码）融合；4 个 `m_axi` 接 HP0–HP3。PC 算法对拍
   `TRACK_PREPROC_REF OK`（RGB/MASK 逐字节一致）。用户批准后已完成 **HLS csynth + BD 出片**：
`board/overlay/track_preproc.bit`（LUT 7,374 / FF 8,340 / DSP 13 / BRAM 3，WNS **+2.457 ns**）；
**上板逐位对通过**：TRACK_PREPROC_BOARD_OK，720p 32.39 ms/帧。
2. **SD 存储**：`result_store.py` JSONL + summary + fsync + 轮转；自检通过；板上 20 帧落盘 20 行 + summary。
3. **PC 软件**：`result_stream.py` + `pc_dashboard.py`；本地回环 2 帧；板→PC 20 帧 `PC_DASHBOARD_OK`。
4. **HDMI/LCD**：`hdmi_status.bit`（Setup +3.938 ns）与 `lcd_status.bit`
   （Setup +4.283 ns）均已出片。根因是 AXI BRAM 双端口 Port A=写/Port B=读；改
   **`SINGLE_PORT_BRAM=1`**（D022）后均 **`DISPLAY_BOARD_OK`**（读回一致），画面待目视。
   **合并显示**（D023）：`display_both.bit` 一次加载、HDMI `0x40000000` +
   LCD `0x40020000` 同时刷新，`DISPLAY_BOTH_BOARD_OK`。
   **跟踪+双屏**（D024）：`track_display.bit` 把 `track_preproc` 与双屏放同一 overlay，
   `TRACK_DISPLAY_BOARD_OK`、20 帧 `PIPELINE_OK`。
5. **板上管线**：`PIPELINE_OK frames=20 detections=4 med_infer_ms=992.95`，proposal 3→2 名球员，
   `pl_ms=20.03 ms/帧`；仓库自检 `CHECK_REPO_OK`；提交包 `SUBMISSION_PACK_OK`。

## 设计要点

- 不把 SORT 硬塞进 PL：PL 做像素级运动/白块掩码，PS 做关联/ID/异常分支；
- 融合 IP 的原因：PYNQ 同时只能加载一个 overlay，两个 IP 无法同时用；
- 无新 bitstream 时 `--pl-track` 自动回退 PS，保证旧路径可跑。

## 自我纠错

1. TB argc/argv 下标错 → `<7`、`argv[5]/argv[6]`；判据 `TRACK_TB_OK`。
2. Dashboard 第 0 帧显示 -1 → 显式 `is None`；判据首行 `FRAME 0`。
3. headless 回放空队列死循环 → 循环条件 + `continue`；判据 `PC_DASHBOARD_OK frames=3`。
4. “写 fb”≠“HDMI 硬件已支持” → 区分渲染软件与显示 overlay，报告 §8 登记。

## 产出

| 路径 | 说明 |
|---|---|
| `src/hls/track/` | HLS 源码/对拍/导出脚本 |
| `build/scripts/bd_track_preproc.tcl` | 4 HP 的 BD 出片脚本 |
| `tools/track_preproc.py` | PYNQ 驱动 |
| `tools/result_store.py` / `result_stream.py` | SD 存储 / 推 PC |
| `tools/pc_dashboard.py` | PC 端软件 |
| `tools/status_display.py` | HDMI/LCD 状态面板 |
| `src/rtl/io/hdmi/`、`build/scripts/bd_hdmi_status.tcl` | HDMI 1bpp BRAM + TMDS overlay（已出片） |
| `src/rtl/io/lcd/`、`build/scripts/bd_lcd_status.tcl` | LCD 800×480 1bpp BRAM + RGB LCD overlay（已出片） |
| `tools/bram_fb.py` | 1bpp 帧缓存写入（0x40000000） |
| `report/design_report.md` §2.9/§4.7/§8 | 报告口径 |

## 未决

- HDMI/LCD/合并双屏/跟踪+双屏 overlay 的**帧缓存读回均已通过**，实际屏幕画面待目视；
  板子在 2026-10-25 测试 vendor 彩色条时掉线（D025），需重新上电后补实拍；
- 球 backfill 结构改动（沿用 010 的结论）；
- 多机位标定。
