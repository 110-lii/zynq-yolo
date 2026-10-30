# preproc/ —— BGR→RGB 平面预处理（主线）

> 上级：[`../../README.md`](../../README.md)

| | |
|---|---|
| 本目录装什么 | `preproc` 的 C++ 源码、脚本与测试台 |
| 谁产生 | 手写 + Vitis HLS |
| 进 git | 是 |

## 内容

| 路径 | 说明 |
|---|---|
| `src/` | 预处理实现（最近邻，II=1） |
| `scripts/` | csynth / 导出脚本 |
| `tb/` | C 仿真测试台 |

## 规则

- 综合时长按十分钟级计，改完先想清楚再跑
- 上板对照脚本在 `board/scripts/`，不在此目录

## 怎么用

- `python src/hls/preproc/scripts/export_preproc.py`（脚本名以目录内实际文件为准）
