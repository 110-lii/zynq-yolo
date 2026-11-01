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
| `hdmi_osd_boxes.png` | HDMI 带框环出的主机侧渲染（框坐标为演示给定） |
| `pc_push_loop_20261101.png` | PC 面板：**推流闭环**（`pc_dashboard --push` 把板上现跑的 JSONL 拉回后渲染；2026-11-01 上板实测） |
| `board/board_01_full.jpg` … `board_04_hdmi_lcd.jpg` | **2026-11-01 实拍**的 ATK-DF7010/7020P 开发板四角度（全貌 / 核心板 / 接口侧 / HDMI+LCD 走线），用于技术文档 §2.2.1 与 §3.2.2 |

## 规则

- 图必须在报告里有编号与出处；**渲染图不得写成屏的实拍**
- 能用 `tools/make_report_figures.py` 重建的图，改代码后要重跑
- `board/` 下是**实拍**，与 `*.png` 的工具生成图性质不同：实拍可作实物佐证，
  但未通电的画面不得说成「工作照」；屏上结论仍以 `ai/sessions/` 里的人眼记录为准

## 怎么用

- `py tools/make_report_figures.py`
