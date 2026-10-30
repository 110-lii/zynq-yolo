# calib/ —— 标定参数

> 上级：[`../README.md`](../README.md)

| | |
|---|---|
| 本目录装什么 | 球场单应（人工标定）与其点对 |
| 谁产生 | 工具产生（`tools/field_coords.py` 等） |
| 进 git | 是 |

## 内容

| 路径 | 说明 |
|---|---|
| `*.json` | 单应矩阵与点对（单位：米/像素） |

## 规则

- 单应只对**标定过的机位**有效；未标定不得输出球场坐标
- 标定点数与残差要写进 JSON，报告引用时必须带出处

## 怎么用

- `python3 tools/field_coords.py --from-pairs <pairs.json> --out data/calib/field_calib.json`
