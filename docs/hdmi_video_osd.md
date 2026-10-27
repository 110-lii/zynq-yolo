# HDMI 视频环出（带检测框的画面）—— 设计与验收

> 决策：[`../ai/coordination/DECISIONS.md`](../ai/coordination/DECISIONS.md) **D026**（取代 D018 里「不做视频环出/OSD」）。
> 与状态面板（D020–D025，1bpp 文字）是**并列的两个 HDMI 方案**，不是替代关系：
> 状态面板便宜、能常驻；视频环出占带宽、给人看画面与框。

## 1. 数据通路

```text
PS（tools/hdmi_video.py）
  ├─ 读 JSONL（board_pipeline 输出）→ 归一化 players / ball / events / camera_state
  ├─ 在真实画面上画：球员绿框 + #id、球红圈、顶部黄字事件条
  └─ 打包成 32bpp 字（bit[23:0]=R<<16|G<<8|B，bit31 填充）写进 DDR 帧缓存（双缓冲）

PL（hdmi_video.bit）
  fb_scanout  ── AXI4 读主机 ──► smartconnect ──► S_AXI_HP0（AXI3）──► DDR
     │ 4 条行缓存（BRAM，提前 3 行取）
     ▼
  video_driver（1280x720@30 时序）──► dvi_transmitter_top（TMDS）──► HDMI
```

## 2. 寄存器（AXI GPIO，不再手写 AXI-Lite 从机）

| 地址 | 通道 | 含义 |
|---|---|---|
| `0x41200000` + `0x00` | cfg ch1 | 帧缓存物理地址（字节） |
| `0x41200000` + `0x08` | cfg ch2 | `{en[27], width[26:16], stride[15:0]}`，stride 单位字节 |
| `0x41210000` + `0x00` | status | `{frames[31:16], under[15:0]}`（PL 计数，只读） |

`under` 是**行欠载计数**：某一行开始显示时该行还没取完，就用黑像素顶并 +1。
上板时先看 `under` 是否稳定为 0，再看画面 —— 这是「画面有没有黑行」的机器判据，
但它**不等于**画面正确（正确还要人眼看，见 §4）。

## 3. 两条硬约束（踩过，别改）

1. **HP0 的 ACLK 必须与 pclk（74.25 MHz）同源。** `fb_scanout` 内部没有 AXI 跨时钟桥，
   整条链路单时钟域。带宽核算：1280×720×4 B×30 fps = 110 MB/s，HP 口富余。
2. **Zynq-7000 的 HP 口是 AXI3，我们的主机是 AXI4。** 直连会被 BD 拒绝
   （`BD 41-1285 protocols ... incompatible`），必须经 smartconnect 转换 ——
   与本仓库 `bd_track_preproc.tcl` / `bd_axi_bw.tcl` 的 HP 接线一致。
3. **行缓存必须是一个数组 + 单读口 + 单写口。** 首版写成 4 个独立数组、每拍读 4 路再 4 选 1，
   Vivado 2020.2 推不出 BRAM，综合出 **198,462 个 FDRE**（器件只有 106,775），
   `place_design` 直接 DRC `UTLZ-1` 失败。改成 `lbuf[4*LBUF]`、索引 `行号*LBUF+列` 后
   推断成 ~5 个 BRAM36。
   ⚠️ 索引**不能用位拼接** `{row[1:0], col}`：拼接隐含步长 2^11=2048，
   `LBUF` 取 1536 时落到数组外，写丢、读回 `X`（仿真里表现为整行 XX）。用 `row*LBUF+col`。
4. **配置寄存器（AXI GPIO）必须和像素逻辑同域。** GPIO 挂在 PS 的 83.33 MHz、`fb_scanout`
   在 74.25 MHz 时，这条准静态总线会被当成同步路径收敛：WNS −8.001 ns（166 端点），
   加了两级同步器后仍 −4.573 ns（60 端点，失败在同步器**第一级**）。
   最终处方：`axi_smc` 与两个 GPIO 的 `aclk/aresetn` 全改 `pclk` + `rst_pix`，
   PS 的 `M_AXI_GP0`(83.33) 与 `axi_smc`(74.25) 之间插一个 **`axi_clock_converter`** ——
   跨域只发生在这一个 IP 里。改完 WNS 转正（+5.470 ns）。
   ⚠️ 手工在 XDC 里 `set_clock_groups -asynchronous` 不解决问题：`get_clocks` 在 BD 生成的
   时钟约束之前执行，取不到时钟对象，约束被静默忽略（日志里连 WARN 都没有）。
5. **数组的写端口必须独立、不能带复位。** 把 `lbuf[...] <= ...` 写在带异步复位的 FSM
   `always` 块里，Vivado 报 `Synth 8-3391 Unable to infer a block/distributed RAM ...
   memory pattern not supported`，随后 elaboration 失败。写法：单独一个
   `always @(posedge pclk) if (wr_en) lbuf[wr_idx] <= m_rdata;`，`wr_en` 用组合逻辑。
   改完先跑 `build/scripts/synth_check_hdmi_video.tcl`（约 2 min）确认 `BRAM>=4`，
   再走完整的 BD + impl —— 否则资源问题要到 `place_design` 才暴露。

## 4. 验收状态

| 项 | 判据 | 状态 |
|---|---|---|
| RTL 功能仿真 | `TB_FB_SCANOUT_OK`（2048 像素逐点比对，0 错误、0 欠载） | ✅ `sim/tb/tb_fb_scanout.v` |
| PS 侧出图 | `HDMI_VIDEO_SELFTEST OK`，打包往返 `dmax=0` | ✅ `tools/hdmi_video.py --selftest` |
| 渲染链路预览（**演示框**） | 本机 PNG（比赛画面 + 2 框 + 球圈 + 状态条） | ✅ 人眼已确认 |
| 真实检测叠框预览（**真框**） | 用 `data/results/tracks/inc_12*.json` 的真实检测结果重渲染 | ✅ 人眼已确认：16 框/帧基本贴合，**有漏检（远景/边缘）与误检（观众、工作人员）** |
| 综合实现/时序 | `HDMI_VIDEO_BD_OK`；**WNS +5.470 ns / WHS +0.074 ns，0 失败端点**；LUT 2,335（4.4%）/ BRAM 6 / DSP 0 | ✅ `board/overlay/hdmi_video.bit`（4.05 MB）|
| **上板画面** | HDMI 显示器上看到带框的视频 | ⛔ 待板子恢复（192.168.2.99 当前离线） |

> 纪律：`DISPLAY_BOARD_OK` 这类「帧缓存读回一致」只证明数字通路；
> 视频环出的最终判据是**人眼在显示器上看到带框画面**，且 `under` 不增长。

## 5. 复现

```powershell
# 1) 出片
vivado -mode batch -source build\scripts\bd_hdmi_video.tcl
# 2) 本机预览（不需要板子）
py tools\hdmi_video.py --replay data\results\run_x.jsonl --video data\samples\incidents\inc_12_t06867.9.mp4 --png-out preview.png
# 3) 板上（PYNQ）
python3 tools/hdmi_video.py --replay /home/xilinx/run_x.jsonl --video /home/xilinx/inc_12.mp4 --fps 8
```
