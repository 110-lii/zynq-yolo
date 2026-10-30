# scripts/ —— 仿真脚本

> 上级：[`../../README.md`](../../README.md)

| | |
|---|---|
| 本目录装什么 | 调用 iverilog 的入口 |
| 谁产生 | 手写 |
| 进 git | 是 |

## 内容

| 路径 | 说明 |
|---|---|
| `run_sim.py` | 用例表 + 编译/运行/断言 |

## 规则

- 工具链在 `D:\iverilog\bin`，不进全局 PATH
- 失败必须非零退出并打印失败用例名

## 怎么用

- `py sim/scripts/run_sim.py`
