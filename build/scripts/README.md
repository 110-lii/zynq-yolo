# scripts/ —— 可复现构建 Tcl

> 上级：[`../../README.md`](../../README.md)

| | |
|---|---|
| 本目录装什么 | 每个 overlay 一份 BD/实现脚本 |
| 谁产生 | 手写 |
| 进 git | 是 |

## 内容

| 路径 | 说明 |
|---|---|
| `bd_preproc.tcl` | 主线预处理 overlay |
| `bd_track_preproc.tcl` | 跟踪辅助 overlay |
| `bd_lcd_probe.tcl` | LCD 4bpp 探针 |
| `bd_hdmi_video.tcl` | HDMI 带框环出 |
| `bd_conv_engine.tcl / bd_axi_bw.tcl` | 历史验证 |

## 规则

- 脚本必须参数化器件与工具链路径（不要硬编码版本路径）
- 一次只跑一个 Vivado；并行会 OOM 或留下僵尸 OOC run

## 怎么用

- 见各文件头；成功标志是日志里 `write_bitstream Complete`
