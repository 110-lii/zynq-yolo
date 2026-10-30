# lcd/ —— LCD 显示通路

> 上级：[`../../../README.md`](../../../README.md)

| | |
|---|---|
| 本目录装什么 | 800×480@33.333 MHz 的 4bpp/16 色面板与双分辨率探针 |
| 谁产生 | 手写 RTL |
| 进 git | 是 |

## 内容

| 路径 | 说明 |
|---|---|
| `lcd_probe_core.v` | 4bpp 读侧 + 调色板 + 图案发生器（021 从零重做） |
| `lcd_probe_top.v` | 探针顶层：cfg 切核心/图案/帧缓存 + 按键 |
| `lcd_driver.v / lcd_timing.v` | vendor 驱动与早期时序（历史） |
| `lcd_fb_reader.v / lcd_status_top.v` | 018–020 的读侧（已被 probe 取代，保留历史） |

## 规则

- 帧缓存是 **4bpp**：8 像素/字，**最低 4 位是最左像素**；WPR=100，48,000 图像字 + 16 调色板字
- BRAM 是字节寻址口径：RTL 必须驱动 `{word_addr, 2'b00}`，否则地址错位（见 `lcd_probe_core.v` 文件头）
- 调色板每场垂直消隐装载 16×24-bit；改颜色不需要重出片

## 怎么用

- `py tools/check_lcd4_board.py`（板端）→ 期望 `READBACK_OK bad=0`，每行 `de=800`
