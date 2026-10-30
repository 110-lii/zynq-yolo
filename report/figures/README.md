# figures/ —— 设计报告插图

> 上级：[`../README.md`](../README.md)

| | |
|---|---|
| 本目录装什么 | 报告引用的确定性生成图与少量实拍/渲染图 |
| 谁产生 | 工具产生 |
| 进 git | 是 |

## 内容

| 路径 | 说明 |
|---|---|
| `system_arch.png` | 系统功能框图 |
| `optimization.png` | 优化前后对比 |
| `lcd_ui_4bpp.png` | 4bpp 赛前 UI（调色板量化后的主机侧渲染） |
| `hdmi_osd_boxes.png` | HDMI 带框环出的主机侧渲染（框坐标为演示给定） |

## 规则

- 图必须在报告里有编号与出处；**渲染图不得写成屏的实拍**
- 能用 `tools/make_report_figures.py` 重建的图，改代码后要重跑

## 怎么用

- `py tools/make_report_figures.py`
