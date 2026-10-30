# checks/ —— 拿到就能跑的检查

> 上级：[`../README.md`](../README.md)

| | |
|---|---|
| 本目录装什么 | 本技能包自带的**独立检查脚本**（不依赖本仓库的数据/模型） |
| 谁产生 | 手写 |
| 进 git | 是 |

## 内容

| 路径 | 说明 |
|---|---|
| `axis_stream_check.py` | AXI-Stream **握手规则检查器**：吃一份逐拍 trace（cycle/tvalid/tready/tlast/tdata/tkeep），查 R1（`tvalid&&!tready` 期间数据变化）、R2（握手完成前撤回 `tvalid`）、R3（连续拖欠超门限）、R4（握手拍数与期望不符） |

## 规则

- 检查必须能在**陌生机器**上独立跑：只依赖 Python 3 标准库，不读本仓库任何路径；
- 每个检查都要有 `--selftest`（**正例通过 + 负例被抓到**），否则不算能防回归。

## 怎么用

```bash
python checks/axis_stream_check.py --selftest                 # 期望 AXIS_CHECK_SELFTEST OK
python checks/axis_stream_check.py trace.txt                  # 期望 AXIS_CHECK_OK beats=N
python checks/axis_stream_check.py trace.txt --expect-beats 128
```

trace 从 testbench 里 dump 出来（`$fdisplay` 一拍一行），例如：

```verilog
always @(posedge aclk) if (!aresetn)
    $fdisplay(fd, "%0d %0d %0d %0d %0h %0h", cyc, tvalid, tready, tlast, tdata, tkeep);
```

> 边界：它只查**主机侧行为**，不查从机是否该拉 `tready`，也不查数据内容 ——
> 内容正确性必须用逐位比对（见本包 README §2.1）。
