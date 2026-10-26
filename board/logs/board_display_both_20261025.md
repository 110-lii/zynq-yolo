# HDMI + LCD 同时显示（D023）上板验证

> 板子：192.168.2.99；overlay：`board/overlay/display_both.bit/.hwh`。
> 目标：一次加载，同时刷 HDMI 1280×720 与 LCD 800×480 两块 1bpp 帧缓存。

## 设计

| 项 | HDMI | LCD |
|---|---|---|
| BRAM 基址 | `0x40000000` | `0x40020000` |
| 容量 | 32,768×32-bit（用 28,800） | 16,384×32-bit（用 12,000） |
| AXI | 独立 `axi_bram_ctrl`（单端口） | 独立 `axi_bram_ctrl`（单端口） |
| 显示读 | BRAM Port B | BRAM Port B |
| 时钟 | 74.25 / 371.25 MHz | 33.333 MHz |

资源/时序（BD 实现）：LUT **908** / FF **923** / DSP 1 / BRAM **48**；
Setup **+3.874 ns**、Hold **+0.105 ns**、PW +5.146 ns，0 failing。

## 上板结果

```text
DISPLAY_BOTH_BOARD_OK
HDMI words 28800 probe 1480 exp 0xfc000000 got 0xfc000000 OK
LCD  words 12000 probe 450  exp 0xfc000000 got 0xfc000000 OK
```

真实结果写双屏：

```bash
python3 tools/status_display.py --replay /tmp/sdtest3/run_*.jsonl \
  --size 1280x720 --display-bit board/overlay/display_both.bit \
  --also-lcd-base 0x40020000 --fps 1 --max-frames 3
```

输出：

```text
STATUS_DISPLAY_BIT board/overlay/display_both.bit base=0x40000000
STATUS_DISPLAY_BRAM 0x40000000 via PYNQ
STATUS_DISPLAY_LCD_BRAM 0x40020000 via PYNQ
STATUS_DISPLAY_OK frames=3
```

## 结论

- **HDMI 与 LCD 可以同时刷新**，不需要来回加载两个 overlay；
- 两块帧缓存写入与 AXI 读回均一致；
- 实际两块屏幕画面仍需现场目视确认（脚本只证明数据链路）。
