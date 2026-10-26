# Session 013 — HDMI/LCD 画面目视验收未通过，并更正 011 的一处过度陈述

## 结论（先给结果）

板上**两块屏都没有画面**：HDMI 只亮背光、LCD 全黑。
机器判据全绿，但**显示这条链路不算通过**。

同时更正 011 的一处记录：那里写的 `DISPLAY_BOARD_OK` 只证明「帧缓存写得进、读得出」，
**从来没有证明画面能显示出来**。原文「读回通过，画面待目视」容易被读成「基本可用」，
实际是「显示功能完全未验证」。本条更正取代 011 中相关表述的效力，原文不删。

## 已排除的（都有实测依据）

| 怀疑项 | 怎么排的 | 依据 |
|---|---|---|
| AXI / 帧缓存 | 写 28800 字、探针值 `0xfc000000` 读回一致 | `DISPLAY_BOARD_OK`（hdmi）/ 12000 字（lcd） |
| TMDS 引脚 | AMD 官方 `xc7z020clg400pkg.txt` 逐条对：G19=`IO_L18P_T2_AD13P_35`、J20=`IO_L17P_T2_AD5P_35`、K19=`IO_L10P_T1_AD11P_35`、J18=`IO_L14P_T2_AD4P_SRCC_35`；对上原理图 B35_L18/L17/L10/L14 | 引脚无一处错配 |
| 外部 50 MHz 晶振引脚 | 资料确认领航者核心板 PL 时钟确在 U18（BANK34） | 引脚未写错 |
| 像素时钟是否在跑 | 换由 PS `FCLK_CLK0`（83.333336 MHz）驱动 clk_wiz 重出片，画面仍无 | 时钟源不是变量 |
| 时序 | Setup +3.938 ns / Hold +0.065 ns；`no_clock=0`；`unconstrained_internal_endpoints=0` | `hdmi_status_bd_timing.rpt` |
| 时钟实际频率 | clk_out1 = 74.256 MHz、clk_out2 = 371.28 MHz | 同上 Clock Summary |

> 排查中被打破的一个假设：**BRAM 读写正常 ≠ PL 时钟正常**。
> BRAM 走 PS 侧 FCLK，像素时钟走 clk_wiz，两者互不相干。这是 011 误判的根源。

## 仍未定位的

物理层三项，本机无法独立判定：

1. **HDMI 线 / 接口**——没有第二根线做对照；
2. **显示器兼容性**——有些屏不认这种非标 TMDS 输出（无 EDID 交互、只有 720p60 一种时序）；
3. **serializer 的时钟布线细节**——`OSERDESE2` 的 5× 时钟（371.25 MHz）只有 10 个端点，
   是否走到专用时钟资源、相位对不对，没有示波器量不了。

## 本轮改动（诊断版，可回退）

| 文件 | 改动 | 意图 |
|---|---|---|
| `build/scripts/bd_hdmi_status.tcl` | `PRIM_IN_FREQ` 50→83.333336；clk_wiz 输入改接 `FCLK_CLK0`（原 `sys_clk` 三行注释保留） | 换时钟源排除外部晶振 |
| `build/scripts/hdmi_status.xdc` | `sys_clk` 的 `PACKAGE_PIN U18` 与 `create_clock` 两行注释 | 顶层已无该端口 |
| 板上 `board/overlay/hdmi_status.bit/.hwh` | 已替换为上述诊断版 | — |

⚠️ 当前板上跑的是**诊断版**（FCLK 供时钟），与仓库原设计不同。回退方法：把 tcl 里三行
取消注释、删掉 FCLK 那一行、`PRIM_IN_FREQ` 改回 50.000，xdc 两行同样恢复，重新出片。

## 纪律教训

- **"读回一致"只能证明数字通路，不能证明任何物理输出。** 显示类验收必须分两级：
  `BRAM_OK`（数字）与 `VISUAL_OK`（人眼），后者没做就不许写"通过"。
- 怀疑顺序应该是：先物理（线/屏），再电气（时钟/引脚），最后 RTL。
  本轮从时钟开始查，多花了两小时，虽然结论正确但顺序偏了。
- 拿引脚这类事实要查**官方 pinout 文件**，不要凭网帖或厂商例程猜；
  本轮 `xc7z020clg400pkg.txt` 一次就把四个引脚全钉死了。

## 产出

| 路径 | 说明 |
|---|---|
| `build/hdmi_diag.log` / `hdmi_diag2.log` | 两次诊断版 Vivado 全流程日志 |
| `build/reports/timing/hdmi_status_bd_timing.rpt` | 诊断版时序（已更新） |
| 本文 | 结论与更正登记 |

## 未决

- 画面为什么不出来，仍未知；下一步按成本排序：换线 → 换显示器 → 实验室上示波器量 TMDS；
- HDMI 与 LCD 同时失效，说明问题在两者共用的部分（PL 时钟或板子供电域），
  单屏问题不可能这么同步——这条线索还没查；
- 011 的报告与 `docs/system_status.md` 里"读回通过，画面待目视"的措辞待统一改成
  「数字通路通过，画面未验证」。