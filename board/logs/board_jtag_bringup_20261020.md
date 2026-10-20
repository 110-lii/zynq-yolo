# 板级实测记录：JTAG 模式下的 conv_engine 首次真机运行（2026-10-20）

> 本文件是**不可重建证据**（repo_spec §4）：记录了板子真实发生的事，**包括走过的弯路**。
> 结论一句话：**最终 `JTAG_TEST_OK 6 / 6`，54 784 字节逐字节相等，连续 3 次复现。**
> 原始 stdout 见 [`board_jtag_layer_test_20261020.md`](board_jtag_layer_test_20261020.md)，
> 机读结果见 [`../../data/results/jtag_layer_test.json`](../../data/results/jtag_layer_test.json)。

## 0. 场景

| 项 | 值 |
|---|---|
| 板卡 | 正点原子 领航者 ZYNQ 7020，底板 V3.7，器件 `xc7z020clg400-2` |
| 拨码状态 | **JTAG 模式**（用户 2026-10-20 确认；SD 卡里**有** PYNQ 镜像，但 PS 不从 SD 启动） |
| 人是否在板边 | **否**（用户在外，无法翻拨码）⇒ PYNQ/ssh 路径不可用 |
| JTAG 链（实测） | `localhost:3121/xilinx_tcf/Digilent/C3059298ABCD`，链上 `arm_dap (idcode 4ba00477)` + `xc7z020 (idcode 23727093)` |
| 工具 | Vitis 2020.2 `xsct.bat` |

## 1. 方法：不开操作系统，纯 JTAG 驱动 PS + PL

```text
rst -system                        （PS 复位；不先做会继承 wedge 状态导致后续全错）
  -> ps7_init                      （BD 生成的 ps7_init.tcl：PLL/时钟/DDR/MIO，与 FSBL 同套寄存器）
  -> stop 两个 A9 核               （★ 不按停则核执行 DDR 里的垃圾数据，与 PL 抢内存）
  -> fpga conv_engine.bit          （PL 跑起来，FCLK0 = 83.33 MHz）
  -> ps7_post_config               （打开 PS<->PL 电平移位器）
  -> memmap 0x4000_0000/0x4001_0000（否则 hw_server 拒访 PL 侧地址）
  -> dow -data in/w/params/lut/out_ref 到 DDR 固定地址
  -> mwr 口 B 5 个 64 位指针 + 口 A 11 个标量（契约 docs/accel_interface.md §4）
  -> mwr AP_CTRL=0x1，mrd 轮询 ap_done(bit1)，稳定 2 s
  -> mrd 块读输出区，与 out_ref 逐字节比对
```

脚本：[`../scripts/jtag_layer_test.tcl`](../scripts/jtag_layer_test.tcl)（PC 侧 `xsct` 执行，纯 ASCII）。

## 2. 最终结果（判据令牌）

| 用例 | 覆盖点 | 字节比对 |
|---|---|---|
| `k1s1_smallw` | 1×1 卷积、`W_OUT=10` 小于列分块 | **0/1600** |
| `k1s1_tailcout` | `C_OUT=20` 不是 `PAR_OUT(8)` 整数倍 ⇒ 尾块守卫 | **0/8000** |
| `k3s1_c3_40` | 最常见 3×3/s1/p1 | **0/25600** |
| `k3s1_cin512` | `C_IN=512` 上限 | **0/1600** |
| `k3s1_pad0` | `K=3` 且 `pad=0` | **0/5184** |
| `k3s2_c16_40` | `stride=2` 降采样 | **0/12800** |
| **合计** | 6/6 | **0 / 54 784 字节** |

伴随令牌：`PS7_INIT_OK`（PS 初始化）、`PROGRAM_OK`（PL 配置）、每用例 `AP_DONE polls=N`、
汇总 `JTAG_TEST_OK 6 / 6`。**连续 3 次独立运行结果相同。**

⇒ 这条证据回答的问题：**`conv_engine.bit` 在真实 XC7Z020 上按契约 §1/§2/§3 算出的字节，
与独立 numpy 实现的金标准逐字节一致。** 这是本项目第一次真机功能验证。

## 3. ⚠️ 这条证据**不能**回答的问题

- **吞吐（GOPS / fps）：没有测，也不能用本路径测。** JTAG 的 `mrd/mwr` 往返是毫秒级，
  本脚本刻意不输出任何耗时数字（`data/results/jtag_layer_test.json` 里没有 elapsed 字段）。
  吞吐必须等 SD 启动 PYNQ 后用 `src/ps/host/run_layer_test.py` 实测。
- **整网（64 层）行为**：本轮只验单层内核，层间编排（`tools/pynq_net.py` 的 PL 后端）未上板。
- **温度/电压裕量**：83.33 MHz 时序 WNS 只有 +0.025 ns（`docs/accel_interface.md` §7.4），
  本轮是常温短时运行，不构成裕量论证。

## 4. 弯路档案（4 个「看起来像硬件坏了」的假故障）

> 这一段是本记录最有价值的部分：**四次都先怀疑设计，实际三次是测量方式错。**

| # | 现象 | 根因 | 修正 | 判据 |
|---|---|---|---|---|
| 1 | `Blocked address 0x40000000. PL AXI slave ports access is not allowed` | hw_server 默认不允许访问 PL 侧地址 | XSCT `memmap -addr ... -size ...` 预登记 | 登记后读到 `AP_CTRL=0x04`（idle） |
| 2 | 比对脚本报 `block_read_short got=0 ref=0` | `mrd` 实际输出格式是 `40000000:   00000004`（**无 `0x` 前缀**），正则全不匹配 | 改正则 | 同一命令解析出 6400/6400 字 |
| 3 | 输出大量错字；`targets` 显示两个核 `(Running)` | `ps7_init` 只初始化硬件**不停止 CPU**：A9 从复位向量执行未初始化 DDR 里的垃圾数据，一边跑一边写内存 | `ps7_init` 后 `stop` 两个核（→`Suspended`） | 错误量级下降；而且这解释了 DAP 间歇 `AHB AP transaction error` |
| 4 | 仍有 3 096/6 400 字不符，输出里残留 `0xCC` 哨兵；跨会话时 `ps7_init` 直接失败于 `0xF8006078`（`Access can hang PS interconnect`） | 前一次调试中止把 PS/DAP 留在 **wedge** 状态；仅 `ps7_init` 无法把它拉回 | 初始化序列前加 `rst -system`（并在失败时重置重试） | **同一用例由 3 096 字错 → 0 字错**（6400/6400 全等），且 6 用例全过 |

**方法论**：判定「硬件算错」之前，必须先用对照实验排除测量侧：访问许可 → 输出格式 →
CPU 抢占 → 平台复位状态。本轮三次假故障全部属于测量侧，真故障数为 0。

## 5. 会话中另一次「板子掉了」的误判（如实记录）

中途一度出现 `targets` / `jtag targets` 全空，看起来像板子掉电。复查发现：
- Windows 设备树里 **Digilent JTAG-SMT2（`USB\VID_0403&PID_6014\C3059298ABCD`）仍在位**；
- 重新扫描后链上 `arm_dap` + `xc7z020` **都在**，板子从未掉电。
⇒ 那是 **hw_server 侧状态**（多个实例抢同一下载器 + 上一条 wedge 状态），不是板子掉电。
**教训：判「板子掉电」之前先查 USB 设备树与 hw_server 实例，别只看 `targets` 为空。**

## 6. 复现步骤（板子 JTAG 模式、已上电）

```powershell
# 单用例
& D:\Xilinx\Vitis\2020.2\bin\xsct.bat board\scripts\jtag_layer_test.tcl k3s1_c3_40
#   期望：JTAG_LAYER_OK k3s1_c3_40 0/25600

# 全量 6 用例
& D:\Xilinx\Vitis\2020.2\bin\xsct.bat board\scripts\jtag_layer_test.tcl
#   期望：JTAG_TEST_OK 6 / 6，并生成 data/results/jtag_layer_test.json
```

## 7. 与 PYNQ 路径的关系

| 路径 | 前提 | 能回答 |
|---|---|---|
| 本记录（JTAG） | 板子 JTAG 模式、无 OS | **字节对不对**（已答：对） |
| [`../scripts/run_board_test.sh`](../scripts/run_board_test.sh) + `src/ps/host/run_layer_test.py` | SD 启动 PYNQ | **多快**（GOPS/fps，**仍缺**） |

两条路径互为复核：拨码切到 SD 启动后，PYNQ 侧重跑同一批用例应给出同样的 0 字节不符，
外加实测周期数。

## 8. 复现所需的本地产物

| 文件 | 作用 | 可否重建 |
|---|---|---|
| `board/overlay/conv_engine.bit` + `.hwh` | 83.33 MHz 时序收敛轮出片 | 可（`build/scripts/bd_conv_engine.tcl`） |
| `build/vivado/conv_engine_bd/.../ps7_init.tcl` | PS 初始化表 | 可（同上，BD 生成） |
| `data/layers/cases/*` | 6 个验收用例 | 可（`sim/scripts/make_hls_case.py`） |
| `board/scripts/jtag_layer_test.tcl` | 本轮脚本（含全部踩坑注释） | 真源，已入库 |
