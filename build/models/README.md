# models/ —— 板上运行必需模型

> 上级：[`../../README.md`](../../README.md)

| | |
|---|---|
| 本目录装什么 | ncnn param/bin/table、球分类器权重 |
| 谁产生 | 工具产生 |
| 进 git | 是（小文件） |

## 内容

| 路径 | 说明 |
|---|---|
| `person_v8n25.*` | 球员检测 ncnn 模型 |
| `ball_roi.npz` | 球 ROI 分类器权重 |

## 规则

- 只放「板上跑得起来」的最小集；训练中间产物放 `data/`

## 怎么用

- `py tools/make_submission.py` 会显式带入本目录必需件
