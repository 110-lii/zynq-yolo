# HDMI / LCD 状态面板出片记录（2026-10-25）

> 性质：构建 + 上板验证记录。最终 BRAM 读回通过；实际画面待目视。
> 原始 Vivado 日志：`build/hdmi_status_stdout.txt`、`build/lcd_status_stdout.txt`
> （被 `.gitignore` 排除，可由下面的命令重建）。

## 1. HDMI（D020）

```powershell
python src\hls\track\scripts\export_track.py        # 与本记录无关，见 D019
vivado -mode batch -source build/scripts/bd_hdmi_status.tcl
```

| 项 | 结果 |
|---|---|
| 比特流/握手 | `board/overlay/hdmi_status.bit`（4,045,674 B）+ `.hwh` |
| 帧缓存 | 1bpp 1280×720，28,800×32-bit，AXI BRAM 基址 **0x40000000**，范围 128 KB |
| 时钟 | `sys_clk` U18 50 MHz → `clk_wiz` 74.25 / 371.25 MHz |
| 时序 | Setup **+3.938 ns**、Hold **+0.065 ns**、PW +5.146 ns（0 failing） |
| 资源（BD 报告） | LUT **1,663**、FF **1,379**、DSP **1**；`blk_mem_gen` OOC 32×RAMB36 |
| 判据 | `HDMI_STATUS_BD_OK`、`write_bitstream completed successfully` |
| 已修复/验证 | SINGLE_PORT_BRAM=1 后 DISPLAY_BOARD_OK；画面待目视 |

## 2. LCD（D021）

```powershell
vivado -mode batch -source build/scripts/bd_lcd_status.tcl
```

| 项 | 结果 |
|---|---|
| 比特流/握手 | `board/overlay/lcd_status.bit`（4,045,674 B）+ `.hwh` |
| 帧缓存 | 1bpp 800×480，12,000×32-bit，基址 **0x40000000**，范围 64 KB |
| 时钟 | `sys_clk` U18 50 MHz → `clk_wiz` 33.333 MHz |
| 时序 | Setup **+4.283 ns**、Hold **+0.121 ns**、PW +5.146 ns（0 failing） |
| 资源（BD 报告） | LUT **548**、FF **637**、DSP **1**；`blk_mem_gen` OOC 16×RAMB36 |
| 判据 | `LCD_STATUS_BD_OK`、`write_bitstream completed successfully` |
| 已修复/验证 | SINGLE_PORT_BRAM=1 后 DISPLAY_BOARD_OK；画面待目视 |

## 3. 板上复跑（已执行；画面待目视）

```bash
# HDMI
python3 tools/status_display.py --replay /home/xilinx/out.json \
  --size 1280x720 --display-bit board/overlay/hdmi_status.bit

# LCD（LCD 与 J3 互斥）
python3 tools/status_display.py --replay /home/xilinx/out.json \
  --size 800x480 --display-bit board/overlay/lcd_status.bit
```

实测（见 [`board_display_20261025.md`](board_display_20261025.md) §4）：脚本打印
`STATUS_DISPLAY_BRAM` 与 `STATUS_DISPLAY_OK frames=3`。**数据链路已验证；屏幕实际画面
在拿到实拍前不写成已验证**（板子于同日 vendor 彩色条测试时掉线，见 D025）。