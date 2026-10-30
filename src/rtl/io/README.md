# io/ —— I/O 显示通路 RTL

> 上级：[`../../README.md`](../../README.md)

| | |
|---|---|
| 本目录装什么 | HDMI 与 LCD 两条显示通路的读侧与顶层 |
| 谁产生 | 手写 |
| 进 git | 是 |

## 内容

| 路径 | 说明 |
|---|---|
| `hdmi/` | TMDS 输出 + DDR 扫描（D020/D026） |
| `lcd/` | RGB LCD + 4bpp 探针（D021/021） |

## 规则

- 帧缓存格式（位宽/字序/字节序）是跨 PS/RTL 的契约，改动必须同时改 `tools/bram_fb.py` 与 `tools/check_lcd4_board.py`
- HDMI 侧读 AXI3(HP) 必须经 smartconnect；HP ACLK 必须与 pclk 同源

## 怎么用

- `py sim/scripts/run_sim.py --only fb_scanout`
