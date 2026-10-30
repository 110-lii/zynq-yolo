# bw/ —— AXI 带宽标定内核

> 上级：[`../../README.md`](../../README.md)

| | |
|---|---|
| 本目录装什么 | 用于测 32/64-bit 读写与拷贝带宽的极简内核 |
| 谁产生 | 手写 + Vitis HLS |
| 进 git | 是 |

## 内容

| 路径 | 说明 |
|---|---|
| `src/` | 带宽标定实现 |
| `scripts/` | csynth / 导出脚本 |

## 规则

- 综合时长按十分钟级计，改完先想清楚再跑
- 上板对照脚本在 `board/scripts/`，不在此目录

## 怎么用

- `python src/hls/bw/scripts/export_bw.py`（脚本名以目录内实际文件为准）
