# HDMI/LCD 状态面板上板验证（2026-10-25）

> 板子：192.168.2.99（PYNQ 2.7 / Linux 5.4.0）。最终判据：
> **`DISPLAY_BOARD_OK`（帧缓存写入 + AXI 读回一致）**；实际屏幕画面仍由人眼确认。

## 1. 第一次失败（保留作证据）

```text
DISPLAY_BOARD_BASE 0x40000000
DISPLAY_BOARD_WRITE PYNQ words 28800 probe 1480 exp 0xfc000000 got 0x8
DISPLAY_BOARD_READBACK False
DISPLAY_BOARD_FAIL
```

LCD 同样 `0x8`。

## 2. 根因（已定位）

AXI BRAM Controller v4.1 在**双端口模式**下：

- **Port A = 写口**；
- **Port B = 读口**。

原 BD 把 `bram_ctrl/BRAM_PORTA` 接到 BRAM Port A，**Port B 悬空并被 HWH 绑成常量
`0x8`**；因此 AXI 读永远读回 `0x8`，写也只落在写口、显示读不到。这不是地址映射问题，
也不是 PYNQ 的 HWH 解析问题。

## 3. 修复

改成 **`C_SINGLE_PORT_BRAM = 1`**：AXI BRAM Controller 用 **Port A 同时做读写**，
BRAM **Port B 留给显示读侧**（`fb_reader` / `lcd_fb_reader`），两者不再抢端口。

对应 Tcl：
- `build/scripts/bd_hdmi_status.tcl`
- `build/scripts/bd_lcd_status.tcl`

## 4. 修复后实测

HDMI：

```text
RW 0 0xdeadbeef 0xdeadbeef OK
RW 100 0x11223344 0x11223344 OK
RW 1480 0xfc000000 0xfc000000 OK
RW 28799 0xa5a5a5a5 0xa5a5a5a5 OK
DISPLAY_BOARD_BASE 0x40000000
DISPLAY_BOARD_WRITE PYNQ words 28800 probe 1480 exp 0xfc000000 got 0xfc000000
DISPLAY_BOARD_READBACK True
DISPLAY_BOARD_OK
```

LCD：

```text
DISPLAY_BOARD_BASE 0x40000000
DISPLAY_BOARD_WRITE PYNQ words 12000 probe 450 exp 0xfc000000 got 0xfc000000
DISPLAY_BOARD_READBACK True
DISPLAY_BOARD_OK
```

用真实板端结果写面板：

```bash
# HDMI
python3 tools/status_display.py --replay /tmp/sdtest3/run_*.jsonl \
  --size 1280x720 --display-bit board/overlay/hdmi_status.bit --fps 1 --max-frames 3
# LCD
python3 tools/status_display.py --replay /tmp/sdtest3/run_*.jsonl \
  --size 800x480 --display-bit board/overlay/lcd_status.bit --fps 1 --max-frames 3
```

两次都输出 `STATUS_DISPLAY_BRAM ... via PYNQ` 与 `STATUS_DISPLAY_OK frames=3`。

## 5. LCD 型号修正（D025）

黑屏的另一个原因是 `lcd_status_top` 用了 7 寸 800×480 的 `lcd_id=16'h7084`；
本板是 4.3 寸 ATK-MD0430R-800480，ID 应为 **`16'h4384`**。已重出
`lcd_status.bit`/`track_display.bit`；板上画面待板子恢复后确认。

## 6. 结论

- **HDMI/LCD 的帧缓存写入与 AXI 读回已通过**；
- 实际屏幕是否点亮/内容是否正确，仍需现场目视确认（脚本不冒充“看到画面”）；
- 这次失败与修复已登记为 **D022**，报告 §8 已从“显示未通过”改为“读回通过、画面待目视”。
