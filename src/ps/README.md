# ps/ —— PS 侧：PYNQ 驱动与主机脚本

> 上级：[`../README.md`](../README.md)

| | |
|---|---|
| 本目录装什么 | 板上 Python 驱动与 PC 侧测试脚本 |
| 谁产生 | 手写 |
| 进 git | 是 |

## 内容

| 路径 | 说明 |
|---|---|
| `pynq_driver/` | PYNQ 驱动（卷积引擎 / 掩码） |
| `host/` | PC 侧脚本与板级测试入口 |

## 规则

- 驱动只做 MMIO/DMA 搬运与最小校验，业务逻辑放 `tools/`
- 驱动里的默认地址/位宽必须与 `docs/accel_interface.md` 一致

## 怎么用

- 板上：`python3 -c "from pynq import Overlay; Overlay('board/overlay/track_preproc.bit')"`
