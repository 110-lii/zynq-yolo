# layers/ —— 逐层参数与计划

> 上级：[`../README.md`](../README.md)

| | |
|---|---|
| 本目录装什么 | 模型切分后的权重/LUT/执行计划 |
| 谁产生 | 工具产生 |
| 进 git | 是 |

## 内容

| 路径 | 说明 |
|---|---|
| `*.json` | 执行计划与参数包 |
| `cases/` | 单层测试用例（生成物） |

## 规则

- `cases/` 下是生成物，子目录不再单独写 README

## 怎么用

- `py tools/export_layers.py` / `py tools/make_board_stream_pack.py`
