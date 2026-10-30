# hdmi/ —— HDMI 显示通路

> 上级：[`../../../README.md`](../../../README.md)

| | |
|---|---|
| 本目录装什么 | 状态面板（1bpp）与带框环出（32bpp, D026） |
| 谁产生 | 手写 RTL + vendor IP |
| 进 git | 是 |

## 内容

| 路径 | 说明 |
|---|---|
| `fb_reader.v` | 1bpp 帧缓存读侧（预取移位寄存器，019 重写） |
| `fb_scanout.v` | 32bpp DDR 扫描主机（S_AXI_HP0，stride 可配） |
| `hdmi_status_top.v` | 状态面板顶层 |
| `hdmi_video_top.v` | 带框环出顶层 |
| `video_driver.v` | 1280×720 时序（vendor） |
| `dvi_transmitter_top.v` | TMDS 编码（vendor） |

## 规则

- `fb_scanout` 内没有跨时钟桥：HP0 ACLK 必须与 pclk 74.25 MHz 同源
- 行欠载计数 `under` 是上板的机器判据（`0x41210000`），为 0 也不代表画面正确

## 怎么用

- `py tools/hdmi_video.py --selftest` → 期望 `HDMI_VIDEO_SELFTEST OK`
- `py sim/scripts/run_sim.py --only fb_scanout` → 期望 `TB_FB_SCANOUT_OK`
