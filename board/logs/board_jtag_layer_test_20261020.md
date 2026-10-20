# 板级实测原始日志：JTAG 路径 conv_engine 单层验收（2026-10-20）

> 不可重建证据（repo_spec §4）。命令与 stdout 原样保留（**未编辑**），
> 判据令牌：`JTAG_LAYER_OK` / `JTAG_TEST_OK`。
> 机读结果：[`../../data/results/jtag_layer_test.json`](../../data/results/jtag_layer_test.json)。

## 命令

```powershell
& D:\Xilinx\Vitis\2020.2\bin\xsct.bat board\scripts\jtag_layer_test.tcl
```

## 完整 stdout

```text
CASES: k1s1_smallw k1s1_tailcout k3s1_c3_40 k3s1_cin512 k3s1_pad0 k3s2_c16_40
attempting to launch hw_server

****** Xilinx hw_server v2020.2
  **** Build date : Nov 18 2020 at 10:01:48
    ** Copyright 1986-2020 Xilinx, Inc. All Rights Reserved.

INFO: hw_server application started
INFO: Use Ctrl-C to exit hw_server application



****** Xilinx hw_server v2020.2

  **** Build date : Nov 18 2020 at 10:01:48

    ** Copyright 1986-2020 Xilinx, Inc. All Rights Reserved.



INFO: hw_server application started

INFO: Use Ctrl-C to exit hw_server application




INFO: To connect to this hw_server instance use url: TCP:127.0.0.1:3121



TARGET: APU selected
running ps7_init (PLL/clock/DDR/MIO init, same registers as FSBL)...
PS7_INIT_OK
CORE_HALTED idx=2 state=1  APU
     2* ARM Cortex-A9 MPCore #0 (Suspended)
     3  ARM Cortex-A9 MPCore #1 (Running)
  4  xc7z020
CORE_HALTED idx=3 state=1  APU
     2  ARM Cortex-A9 MPCore #0 (Suspended)
     3* ARM Cortex-A9 MPCore #1 (Suspended)
  4  xc7z020
programming PL: D:/zynq-yolo/board/overlay/conv_engine.bit
PROGRAM_OK
POST_CONFIG_OK
MEMMAP_ADDED 0x40000000/0x40010000 64K each
---- CASE k1s1_smallw ----
VECTORS_LOADED in=3200 w=512 params=192 lut=256 out=1600
SENTINEL_FILLED words=400
AP_DONE polls=2
JTAG_LAYER_OK k1s1_smallw 0/1600
---- CASE k1s1_tailcout ----
VECTORS_LOADED in=25600 w=1280 params=240 lut=256 out=8000
SENTINEL_FILLED words=2000
AP_DONE polls=4
JTAG_LAYER_OK k1s1_tailcout 0/8000
---- CASE k3s1_c3_40 ----
VECTORS_LOADED in=4800 w=432 params=192 lut=256 out=25600
SENTINEL_FILLED words=6400
AP_DONE polls=1
JTAG_LAYER_OK k3s1_c3_40 0/25600
---- CASE k3s1_cin512 ----
VECTORS_LOADED in=51200 w=73728 params=192 lut=256 out=1600
SENTINEL_FILLED words=400
AP_DONE polls=10
JTAG_LAYER_OK k3s1_cin512 0/1600
---- CASE k3s1_pad0 ----
VECTORS_LOADED in=6400 w=2304 params=192 lut=256 out=5184
SENTINEL_FILLED words=1296
AP_DONE polls=1
JTAG_LAYER_OK k3s1_pad0 0/5184
---- CASE k3s2_c16_40 ----
VECTORS_LOADED in=25600 w=4608 params=384 lut=256 out=12800
SENTINEL_FILLED words=3200
AP_DONE polls=2
JTAG_LAYER_OK k3s2_c16_40 0/12800
---- SUMMARY ----
JTAG_TEST_OK 6 / 6
DETAIL: {k1s1_smallw OK} {k1s1_tailcout OK} {k3s1_c3_40 OK} {k3s1_cin512 OK} {k3s1_pad0 OK} {k3s2_c16_40 OK}
RESULT_JSON D:/zynq-yolo/data/results/jtag_layer_test.json

```

## 结果摘要

| 用例 | 字节比对 | 令牌 |
|---|---|---|
| k1s1_smallw | 0/1600 不符 | JTAG_LAYER_OK |
| k1s1_tailcout | 0/8000 不符 | JTAG_LAYER_OK |
| k3s1_c3_40 | 0/25600 不符 | JTAG_LAYER_OK |
| k3s1_cin512 | 0/1600 不符 | JTAG_LAYER_OK |
| k3s1_pad0 | 0/5184 不符 | JTAG_LAYER_OK |
| k3s2_c16_40 | 0/12800 不符 | JTAG_LAYER_OK |
| **合计** | **0 / 54 784 字节** | **JTAG_TEST_OK 6 / 6** |

## 重复性

同一条命令在 2026-10-20 连续运行 **3 次**，均为 JTAG_TEST_OK 6 / 6、0 字节不符。
（三次之间板子状态分别为：冷启动后首次、PS 复位后、以及一次 DAP wedge 恢复后。）
