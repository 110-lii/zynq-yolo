# Session 015 — 合并版 overlay 补齐 014 的三处修复，本机重出片（上板未验）

## 结论（先给结果）

**`display_both.bit` 已按 014 的三处根因修好并在本机重出片，时序收敛、网表核对通过；
但屏幕画面未验证** —— 板子 `192.168.2.99` 全程 ping 不通，按纪律不许把"出片成功"
写成"显示通过"。

同时修掉一个会让上板验收执行不下去的缺陷：`docs/board_acceptance_20261027.md` §1 里
写的 `check_display_board.py --bit ...` **这个参数根本不存在**（旧脚本只认 `--which hdmi|lcd`），
照单执行会直接报错。

## 为什么合并版一直是坏的

session 014 定位到三个叠加 bug（BRAM `rstb` 极性、`fb_reader` 位序、`xpos` 的 +2 偏移），
但它只改了**单屏**的三个 BD tcl 与两套 reader RTL。合并版 `bd_display_both.tcl` 里：

| 项 | 单屏（014 后） | 合并版（014 后，本轮之前） |
|---|---|---|
| BRAM `rstb` | 接 `xlconstant 0` | **接 `rst_*/peripheral_aresetn`（读口常复位）** |
| `proc_sys_reset.dcm_locked` | 接 `clk_wiz/locked` | **悬空（被 tie 成常量）** |
| LCD 像素时钟 | 独立 clk_wiz，25.000 MHz | 与 HDMI 共用，`clk_out3 = 33.333 MHz` |

也就是说：**014 之后任何一次加载合并版，必然复现 013 的全黑**，而且看起来像"修了没用"。

## 本轮改动

1. **两块 BRAM 的 `rstb` 改接 `xlconstant 0`**（HDMI/LCD 各一处）。
2. **`rst_hdmi` / `rst_lcd` 的 `dcm_locked` 显式接各自 clk_wiz 的 `locked`**。
3. **LCD 拆出独立 `clk_wiz_lcd`**（外部 50 MHz → 25.000 MHz）。
   理由：74.25 / 371.25 与 25 没有公共 VCO，塞进同一个 clk_wiz 只能出近似频率；
   25 MHz 是 4384 屏的官方值。HDMI 侧则照抄已验证的 `hdmi_status` 配置（输入取 PS `FCLK_CLK0`）。
4. `tools/check_display_board.py` 重写：支持 `--bit` / `--which both` /
   `--pattern white|chess|black|panel`，单屏与合并版共用一个脚本，**逐字**读回比对，
   `--selftest` 无板可跑（末行 `DISPLAY_SELFTEST_OK`）。
5. `docs/board_acceptance_20261027.md` §1 命令改为可执行写法，并按 white → chess → panel 分级。

## 本机实测（可复核）

```
DISPLAY_BOTH_BD_OK BIT=D:/zynq-yolo/board/overlay/display_both.bit
All user specified timing constraints are met.
clk_fpga_0                       period 12.000   83.333 MHz
sys_clk                          period 20.000   50.000 MHz
  clk_out1_system_clk_wiz_lcd_0  period 40.000   25.000 MHz   ← LCD
  clk_out1_system_clk_wiz_0      period 13.467   74.256 MHz   ← HDMI
  clk_out2_system_clk_wiz_0      period  2.693  371.280 MHz   ← TMDS 5x
WNS +3.986 ns / WHS +0.116 ns，无未约束路径
```

网表核对（`.../system/sim/system.v`，不靠看图）：

```verilog
.rstb(zero_dout)      // 两块 BRAM 都是常量 0
.enb(one_dout)
.dcm_locked(clk_wiz_locked)       // rst_hdmi
.dcm_locked(clk_wiz_lcd_locked)   // rst_lcd
```

hwh 地址：`hdmi_ctrl 0x40000000-0x4001FFFF`、`lcd_ctrl 0x40020000-0x4002FFFF`。

## 未验证（不许写成已通过）

| 项 | 状态 | 怎么才能算过 |
|---|---|---|
| 合并版两块屏的画面 | **未验**（板子 ping 不通） | 上板 `--pattern white` 后整屏发亮，再 `--pattern chess` 人眼看两块屏都是 64×64 棋盘格 |
| 状态面板 8 项 | 本机渲染过，板上未验 | `--pattern panel`，人眼看信息齐全、中文不乱码 |
| HDMI 视频环出 / SD 证据帧 / 上位机推送 | 未动，仍未上板 | 见 `docs/board_acceptance_20261027.md` §3–§5 |

## 过程事故（如实记录）

第一次启动 Vivado 报 `ERROR: [Common 17-217] Failed to load feature 'ipintegrator'`，
紧接着一次报 `couldn't read file .../tcllib1.11.1/math/bignum.tcl: no such file or directory`
—— 而该文件**实际存在且可读**（`package require math::bignum` 通过、`create_bd_cell
processing_system7:5.5` 单独跑也通过）。判定为**两次 Vivado 实例重叠**造成的假故障
（前一次的收尾与后一次启动争用），单独串行重跑即恢复到 `DISPLAY_BOTH_BD_OK`。
下次：出片前先 `tasklist | grep -i vivado` 确认没有残留实例。

## 纪律（给下一次）

1. **修一个 bug 要 grep 全部同类 BD 脚本**。014 修的是单屏，合并版脚本原样留着三个同样的错，
   而合并版恰恰是演示要用的那块。`grep -n "rstb\|dcm_locked" build/scripts/*.tcl` 一秒能查完。
2. **清单里写的命令要先在本机跑一遍**。`--bit` 这个不存在的参数在文档里躺了一整轮，
   上板时才发现，等于白等一个板子在线窗口。
3. 出片成功 ≠ 显示通过。本轮把「时序 + 网表 + 地址」当机器判据留在提交里，
   人眼判据单列，未做就写"未验"。
