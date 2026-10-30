# Session 020 — LCD 链重写与分辨率定位：最终按 800×480 交付，ID 实测 000

| 字段 | 内容 |
|---|---|
| 日期 | 2026-10-29 |
| 执行 | `magpie/group/120`（Kilo 桌面端，接 018/019 之后） |
| 任务类型 | RTL 重写 / 仿真验证 / 板端定位 / 出片（最终 800×480 未装机） |
| 目标 | 按“重新写 LCD 的代码和逻辑”的要求重写显示链，并在上板后定位花屏根因 |
| 上下文 | `src/rtl/io/lcd/{lcd_driver,lcd_fb_reader,lcd_status_top,lcd_timing}.v`、`sim/tb/io/lcd/*`、`build/scripts/bd_{display_both,lcd_status,track_display}.tcl`、`tools/{check_display_board,bram_fb,status_display}.py` |

## 结论

LCD 显示链已整体重写为项目内时序发生器 + 双行缓存读取器，最终代码按用户要求配置为 **800×480@61.6 Hz（30 MHz、Th 928、Tv 525）**，仿真与出片均通过。

但板端实测发现一个必须记录的硬事实：

- 用原厂 `rd_id` 口径（消隐期释放 RGB 总线，采样 R7/G7/B7）读回 **`{B7,G7,R7}=000`**，对应 **480×272**；
- 按 480×272 / 9 MHz / 15 字每行出片后，分屏图案**基本正确**，只在右侧剩一条窄黑边；
- 用户随后明确要求“没换屏，就按 800×480”，因此最终代码/bit 回到 800×480 档；
- 最终 800×480 bit 已出片（WNS +3.815 / WHS +0.082），但**上传时板子掉线**，尚未装机、未做 800×480 人眼验收。板端当前仍保留上一版 480×272 bit。

## 最终结构

```text
PS → AXI BRAM（1bpp，25 字/行 × 480 行 = 12,000 字）
                 │
                 ▼
lcd_fb_reader：A/B 双行缓存（各 25×32 分布式 RAM）
                 │  当前行移位输出；另一块在整行时间预取下一行
                 ▼
lcd_timing：h_cnt/v_cnt → lcd_de / xpos(1..800) / ypos(1..480)
                 │
                 ▼
lcd_rgb = lcd_de ? pixel_data : 24'bz   // 消隐期释放，供屏 ID 电阻
```

关键口径：

- 像素时钟 **30.000 MHz**，`H_TOTAL=928`（800 + 40 hbp + 88 hfp）、`V_TOTAL=525`，`H_SYNC=0`、`V_SYNC=2`、`V_BACK=33`、`V_FRONT=10`；
- DE 模式：HS/VS/BL/RST 恒高，行场由 DE 划分（与官方 4.3 寸例程一致）；
- `pixel_xpos` 有效值 1..800，`pixel_ypos` 有效值 1..480；`pixel_ypos` 在整条有效行（含行消隐）保持当前行号；
- 读侧每个字留 4 拍（支持 1..2 拍 BRAM 读延迟），25 字共 100 拍；A/B 切换在 `xpos==800`，不依赖行消隐长度；
- `pixel_data` 为组合输出，与 `pixel_xpos` 同拍；
- `dbg1 = {7'b0, lcd_de, pixel_data}`；`dbg2 = {id_bits, 7'b0, v_cnt, h_cnt}`，`id_bits` 在复位后第一拍采样。

## 证据链

| # | 证据 | 位置 |
|---|---|---|
| 1 | 5 图案整场 800×480（1,920,000 px）0 错 | `sim/tb/io/lcd/lcd_fb_reader_tb.v`，`LCD_FB_READER_TB_PASS` |
| 2 | BRAM 读延迟 1/2 拍各一场全过 | `sim/tb/io/lcd/lcd_fb_reader_latency_tb.v`，`LCD_FB_READER_LATENCY_TB_PASS` |
| 3 | RTL dump 与 `pack_1bpp` 对拍 0/384,000 | `tools/bram_fb.py --verify-frame --size 800x480`，`BRAM_FB_FRAME_OK` |
| 4 | 全量回归 7/7 | `SIM_PASS 7/7 cases` |
| 5 | checker 自检 | `py tools/check_display_board.py --selftest`，`DISPLAY_SELFTEST OK` |
| 6 | 屏 ID 实测 | 2000 次采样均为 `LCD_ID_BITS {0: 2000}`（000 ⇒ 480×272） |
| 7 | 480×272 上板判据 | `words=4080`、`h=[0,524] v=[0,285]`、`pclk=9.000MHz`、`DISPLAY_BOARD_OK`；人眼：左黑右白基本正确，右侧窄黑边 |
| 8 | 最终 800×480 出片 | `DISPLAY_BOTH_BD_OK`，WNS **+3.815 ns** / WHS **+0.082 ns**，`All user specified timing constraints are met` |

## 分辨率定位过程（重要）

1. 800×480 / 33.333 MHz / Th=1056 上板：白屏正常，竖条/分屏/文字整体错乱；
2. 800×480 / 30 MHz / Th=928 上板：现象不变；
3. 实现 `rd_id` 采样（RGB 消隐期高阻），读回 000 ⇒ 480×272；
4. 480×272 / 9 MHz / Th=525 上板：`words=4080`、`pclk=9.000MHz` 机器判据通过，分屏基本正确，仅右侧窄黑边；
5. 用户确认“没换屏，就按 800×480” ⇒ 最终代码/bit 回到 800×480 档。

## 本轮改动

| 文件 | 改动 |
|---|---|
| `src/rtl/io/lcd/lcd_timing.v` | 新增：800×480 时序发生器（30 MHz、928×525），1 基像素坐标 |
| `src/rtl/io/lcd/lcd_fb_reader.v` | 重写：A/B 双行缓存，25×32，整行时间预取，有效期移位输出 |
| `src/rtl/io/lcd/lcd_status_top.v` | 重写：`lcd_timing + lcd_fb_reader`；RGB 改 `inout`，消隐期高阻并采样 3 位 ID |
| `sim/tb/io/lcd/lcd_fb_reader_tb.v` | 5 图案 800×480 整场回归，dump 帧供 Python 对拍 |
| `sim/tb/io/lcd/lcd_fb_reader_latency_tb.v` | 1/2 拍读延迟回归 |
| `sim/scripts/run_sim.py` | LCD 依赖换成 `lcd_timing.v`，post-check 加 `--size 800x480` |
| `build/scripts/bd_display_both.tcl` | LCD MMCM 30.000 MHz；RGB 端口改 IO；注释更新 |
| `build/scripts/bd_lcd_status.tcl` | LCD MMCM 30.000 MHz；RGB 端口改 IO |
| `build/scripts/bd_track_display.tcl` | 新增第二个 LCD MMCM（33.333 → 30.000）；RGB 端口改 IO |
| `tools/check_display_board.py` | LCD 默认 800×480 / Th 928 / Tv 525 / 30 MHz；`--pclk-mhz`；自检同步 |
| `tools/bram_fb.py` / `status_display.py` / `pc_dashboard.py` | 板端同步更新（480×272 试验时也用过） |
| `docs/display_overlay_plan.md` | LCD 口径回到 800×480 / 30 MHz / Th 928 |

`vendor lcd_driver.v` 保留作参考，三个 BD 脚本已不再加入工程。

## 最终出片

| 项 | 结果 |
|---|---|
| 日志 | `build/vivado/logs/display_both_lcd_800x480.log` |
| 判据 | `DISPLAY_BOTH_BD_OK` + `write_bitstream completed successfully` |
| 产物 | `board/overlay/display_both.bit`，SHA256 `2A829C052F44D6B55DF9CD32AEC8BBA401274C268C8345BCAB752D055EE665F7` |
| 时序 | WNS **+3.815 ns**、WHS **+0.082 ns** |
| 资源 | LUT **2,087**（3.92%）、FF **3,793**（3.56%）、BRAM **48**（34.29%）、DSP **2** |

## 未完成 / 未验证

| 项 | 状态 | 怎么算过 |
|---|---|---|
| 最终 800×480 bit 上传/装机 | **未做：上传时板子掉线** | 板端 `display_both.bit` 哈希应为 `2A82…65F7` |
| 800×480 人眼验收 | **未做** | 接 800×480 屏后：左黑右白分屏正确；8 行 panel 可读 |
| 480×272 右窄黑边 | 未继续修（用户改回 800×480） | 若回到 480×272，再查 DE/前肩相位 |
| HDMI 侧 | 本轮未改 | 仍按 019 的整场仿真与上板判据 |

板端当前 overlay 仍是 480×272 版（SHA256 `22D9C0610A9FB80875FC3C80DED25CC44D414D4E6DBEF3E72CDE8B809FA6229C`），与本机 800×480 版不一致。板子恢复后先重新上传，再做 800×480 目视；若屏仍是当前这块，ID 仍会是 000。
