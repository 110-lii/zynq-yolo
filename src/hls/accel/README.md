# accel/ —— 通用 INT8 卷积引擎（历史资产）

> 上级：[`../../README.md`](../../README.md)

| | |
|---|---|
| 本目录装什么 | `conv_engine` 的 C++ 源码、脚本与测试台 |
| 谁产生 | 手写 + Vitis HLS |
| 进 git | 是 |

## 内容

| 路径 | 说明 |
|---|---|
| `include/` | 内核头文件 |
| `src/` | 卷积引擎 C++ 实现 |
| `scripts/` | csynth / 导出脚本 |
| `tb/` | C 仿真测试台与激励 |

## 规则

- 综合时长按十分钟级计，改完先想清楚再跑
- 上板对照脚本在 `board/scripts/`，不在此目录

## 怎么用

- `python src/hls/accel/scripts/export_accel.py`（脚本名以目录内实际文件为准）
