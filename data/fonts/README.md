# fonts/ —— 板端字体子集

> 上级：[`../README.md`](../README.md)

| | |
|---|---|
| 本目录装什么 | 状态面板与 UI 用的点阵/子集字体 |
| 谁产生 | 工具产生 |
| 进 git | 是 |

## 内容

| 路径 | 说明 |
|---|---|
| `*.bin/.json` | 字形数据与映射 |

## 规则

- 4bpp 面板上小字号不要加粗/描边（会糊，见 `ai/sessions/021`）

## 怎么用

- 由 `tools/status_display.py` / `tools/lcd_ui.py` 读取
