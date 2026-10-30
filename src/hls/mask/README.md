# mask/ —— 白/运动掩码预处理

> 上级：[`../../README.md`](../../README.md)

| | |
|---|---|
| 本目录装什么 | `mask_preproc` 的源码与脚本 |
| 谁产生 | 手写 + Vitis HLS |
| 进 git | 是 |

## 内容

| 路径 | 说明 |
|---|---|
| `src/` | 掩码实现（bit0 白、bit1 运动） |
| `scripts/` | csynth / 导出脚本 |
| `tb/` | C 仿真测试台 |

## 规则

- 综合时长按十分钟级计，改完先想清楚再跑
- 上板对照脚本在 `board/scripts/`，不在此目录

## 怎么用

- `python src/hls/mask/scripts/export_mask.py`（脚本名以目录内实际文件为准）
