# hls/ —— HLS 内核（Vitis HLS 2020.2）

> 上级：[`../README.md`](../README.md)

| | |
|---|---|
| 本目录装什么 | 所有 HLS C++ 内核及其脚本/测试台 |
| 谁产生 | 手写 + Vitis HLS（csynth/导出 IP） |
| 进 git | 是 |

## 内容

| 路径 | 说明 |
|---|---|
| `accel/` | 通用 INT8 卷积引擎（历史验证资产，已退出主线） |
| `preproc/` | BGR→3×320×320 RGB 最近邻预处理（主线） |
| `mask/` | 白/运动掩码预处理 |
| `track/` | preproc + mask 合并的 `track_preproc`（主线，单 IP / 4 HP） |
| `bw/` | AXI 带宽标定内核 |

## 规则

- 每个内核必须能在 `scripts/` 里一条命令 csynth + 导出，不手点 GUI
- HLS 结果里的「通过」只算 IP 级；上板必须另做逐位对（见 `board/`）

## 怎么用

- `python src/hls/track/scripts/export_track.py` 导出 `track_preproc` IP
- 综合/实现由 `build/scripts/bd_*.tcl` 统一驱动，不在这里跑 Vivado
