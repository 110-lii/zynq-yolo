# samples/ —— 样本片段与标定素材

> 上级：[`../README.md`](../README.md)

| | |
|---|---|
| 本目录装什么 | 转播片段、标定用截图等 |
| 谁产生 | 外部采集 + 工具切分 |
| 进 git | 部分（大文件除外） |

## 内容

| 路径 | 说明 |
|---|---|
| `incidents/` | 事件片段 |
| `calib_real/` | 真实机位标定素材 |

## 规则

- **引用球指标时必须写源分辨率**：360p 降采样片段会得出错误结论（`docs/system_status.md` 已知边界 6）
- 数据集来源与许可（D005）仍 `discussing`，不得宣称可再分发

## 怎么用

- `python3 tools/ball_chain_clip.py --clip <mp4>`
