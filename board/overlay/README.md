# overlay/ —— 比特流与硬件描述

> 上级：[`../../README.md`](../../README.md)

| | |
|---|---|
| 本目录装什么 | PYNQ `Overlay()` 加载所需的 .bit/.hwh |
| 谁产生 | Vivado 产生 |
| 进 git | 是 |

## 内容

| 路径 | 说明 |
|---|---|
| `*.bit` | 比特流 |
| `*.hwh` | 硬件描述（必须与 .bit 同批） |

## 规则

- `.bit` 与 `.hwh` 必须同批；时间戳不一致就要怀疑加载错配
- 换片后先跑对应自证脚本，再谈业务指标

## 怎么用

- 板端：`from pynq import Overlay; Overlay('/home/xilinx/zynq-yolo/board/overlay/lcd_probe.bit')`
