# M1 采集链上板记录（2026-10-20）

> 上级：[`README.md`](README.md) ｜ 计划真源：[`../../docs/integration_rtl_plan.md`](../../docs/integration_rtl_plan.md) §6 M1
> 目标（User said, m01475）：**「开始，知道上板测试」** —— 把 M1 采集链一路做到上板，拿到机器可读的通过令牌。

## 0. 结论速览

| 项 | 状态 |
|---|---|
| RTL + TB 仿真（4 个用例 + 整链黄金比对） | ✅ `SIM_PASS 4/4 cases`，`CAPTURE_MATCH_OK crc32=d1c9c45a` |
| BD 集成 + 出片 | ✅ `BD_DONE_OK`（多轮，最新 `WNS=3.809`，含 13 条 `NETCHECK` 连线自查） |
| JTAG 路径（无 OS） | ⛔ 放弃：`fpga` 报 `DONE PIN is not HIGH`，随后 DAP wedge，只能断电 |
| PYNQ 路径（SD 启动） | 🟡 通路已打通到「数据真在流、DMA 真的在读」，卡在**传输不完成 + 采集 FIFO 溢出** |
| 判据令牌 | 尚未拿到 `CAPTURE_BOARD_OK`（为此加了两轮观测位，见 §4） |

## 1. 环境与入口

| | |
|---|---|
| 板卡 | 正点原子领航者 ZYNQ 7020（xc7z020clg400-2），SD 卡启动 PYNQ 2.7 |
| 板 IP | `192.168.2.99`（PC 侧 `192.168.2.100/24` 直连），ssh 22 / Jupyter 9090 |
| 板 Python | 3.8.2、numpy 1.17.4、pynq 2.7（**装在 `/home/xilinx/pynq`，不在 `/usr/lib`**） |
| 部署 | `board/scripts/deploy_capture_pynq.ps1 -BoardHost 192.168.2.99 -Run -TestArgs '--check'` |
| 板上跑 | `board/scripts/run_capture_test.sh` → `sudo -S bash -lc python3 src/ps/host/run_capture_test.py --bitfile board/overlay/capture_chain.bit` |
| 判据 | `CAPTURE_BOARD_OK frame_bytes=1228800 words=307200 crc32=d1c9c45a pad_ok=1` / `CAPTURE_BOARD_FAIL <reason>` |

## 2. 第 1 个坑：PYNQ 不跑我们的 `ps7_init`

第一版 BD 用 PS 的 **FCLK1 = 25 MHz** 做像素时钟（JTAG 路径就是这么写的，能跑）。
PYNQ 路径上必挂：板上现象 `CAPTURE_BOARD_FAIL dma_ioc_timeout dmasr=0x00000000` —— 图样一动不动。

根因：**PYNQ 镜像的 FSBL 用它自己的 PS 配置启动，不是我们 BD 导出的 `ps7_init`**，
所以 `PCW_EN_CLK1_PORT=1` 这类设置在运行时根本不生效，FCLK1 压根没有时钟输出。

处置：BD 改成 `FCLK0(100 MHz) → clk_wiz(MMCM) → clk_out1 = 25 MHz`，`rst_25m/slowest_sync_clk`
接 `clk_wiz_0/clk_out1`、`dcm_locked` 接 `locked`。实测 `clk_wiz.locked=1`。
（后来为了先打通通路又把 v5 改成**全 FCLK0 单时钟**，见 §3。）

## 3. 第 2 个坑（代价最大）：观测把被测对象切断了

现象：图样在跑（`href` 命中 1247/1699）、采集在出数（`cap.m_axis_tvalid` 命中 608/1699）、
`cap.stat_overflow=0`（说明下游 `s_tready` 一直是 1）、但 **`fifo.m_tvalid` 一次都没高过**、
FIFO 写指针从未动过（`dbg_wr_hit=0`）。RD 侧恒空、写侧也不动，看起来像「FIFO 坏了」。

为了看内部状态，当时把 4 根 **AXIS 接口成员脚**直接接到 `xlconcat` 当观测位：
`cap_0/m_axis_tvalid`、`fifo_0/s_tready`、`fifo_0/m_tvalid`、`axi_dma_0/s_axis_s2mm_tready`。
BD 构建日志里其实写着答案，只是被淹没了：

```
WARNING: [BD 41-1271] The connection to the pin: /cap_0/m_axis_tvalid has been overridden by the user.
         This pin will not be connected as a part of the interface connection <m_axis>.
WARNING: [BD 41-759]  The following pins are not connected / tied to 0 ...
```

**BD 会把这些脚从接口网里摘掉**，另一侧悬空输入被 tie 成 0 ⇒ `fifo_0/s_tvalid` 恒 0 ⇒
写指针永不动。**观测动作本身把数据通路切断了**，而源侧 tvalid 照样翻转，于是看起来像 FIFO 故障。

正确做法（已固化）：
- 需要看接口内部状态，就在 RTL 里**导出专用 `dbg_*` 标量端口**（`inf_axis_async_fifo` 现在有
  `dbg_wfull/dbg_rempty/dbg_wr_hit/dbg_rd_hit/dbg_m_tready/dbg_rd_cnt[7:0]`），
  在 BD 里只连这些**非接口**标量脚；
- `m_tvalid = ~rempty`、`s_tready = ~wfull` 这类可由已有位推出的，不重复接。

## 4. 第 3 个坑：DMA simple 模式要的是「一帧一个 tlast」

数据通路接通后（v6，单时钟），板上读到：`cap_tvalid` 有活动、FIFO 写指针在动、DMA 开始收数，
但 `DMASR=0x00005011`（Halted + Int_Err + IOC + Err_Irq）。
用探针量长度：`LENGTH=1024` 时 `DMASR` 里 `Int_Err` 立刻置起 ⇒ 换算成 **640 个字**。

原因：`vid_dvp_capture` 原来 **每行末** 就打一拍 `tlast`（EOL）。而 `axi_dma` 在
**Scatter/Gather 关闭的 simple 模式**下，一个 packet 就是一次传输：**`tlast` 晚到没事，
早到就是 `Int_Err`**（PG021 p.30 表 2-7）。所以第 640 个字（第一行末）就把传输打断了。

处置：`vid_dvp_capture` 改成 **只在帧末（EOF）打 `tlast`**，一个 packet = 一整帧。
代价：行边界不再从流里体现（不需要——分辨率固定，PS 侧按 `FRAME_BYTES` 切片）。
TB 同步改（帧内不再期望逐行 tlast），`make sim` 仍 4/4 PASS。

## 5. 第 4 个坑：BD 的接口成员被「按名字自动对位」，而且是静默的

改完 tlast 后仍然失败，这次是 `CAPTURE_BOARD_FAIL capture_fifo_overflowed_during_transfer`。
逐条比对 `.hwh` 的 `<MODULE>/<PORT>/<CONNECTION>` 才发现两张错配：

| 实际连线 | 期望 |
|---|---|
| `axi_dma_0/s_axis_s2mm_tkeep` ← **`fifo_0/m_tvalid`** | ← 常量 `0xF` |
| `axi_dma_0/s_axis_s2mm_tlast` ← **`fifo_0/m_tuser`** | ← `fifo_0/m_tlast` |

原因：`fifo_0` 是 `create_bd_cell -type module -reference` 进来的自研 RTL，BD 给它自动推断出
`s`/`m` 两个 AXIS 接口；在和 DMA 的 `S_AXIS_S2MM` 连接口时，**成员是按名字/顺序对位的**，
`m_tlast`/`m_tuser` 位置错开一格就成了上面这样，而且**不报错**。

处置（已固化进 `build/scripts/bd_capture_chain.tcl`）：
1. 离散连成员脚（`connect_bd_net` 逐根），`tkeep` 由 `xlconstant(0xF)` 驱动；
2. 出片前跑一轮 **`NETCHECK`**：对每条关键连接断言「两个脚在同一张 net 上」
   （`get_bd_nets -of_objects [get_bd_pins ...]` 比对），任一不符就 `BD_FAIL netcheck`，
   实测能立刻抓到 `fifo_0_m_tlast ↔ axi_dma_0/s_axis_s2mm_tlast` 不在同一网。
3. 读 `.hwh` 时**只读 `<MODULE>` 段**：同一个文件里还有 `<BUSINTERFACE>` 的 `<PORTMAP>`
   （接口逻辑名→物理名），按 `PORT` 全局 grep 会把 PORTMAP 里的条目当成真实连线。

改完后 `hwh` 复核：`m_tuser` 未连接、`m_tlast → s_axis_s2mm_tlast`、`tkeep ← tkeep_one/dout`。

## 6. 第 5 个坑（当前卡点）：数据在流、FIFO 却一直是满的，DMA 既不完成也不报错

最新一轮 `--check`：

```
DBG_BITS parked=0x4406 (overflow=0 rstn=1 vsync_n=1 href=0 s_rst=0 m_rst=0 wfull=1 rempty=0 wr_hit=1 rd_hit=1 sanity=1)
DMASR_ARMED 0x00000000 (halted=0 idle=0 ... ioc=0 err_irq=0)
DBG_SEEN running samples=164 ... href=116 fifo_notempty=148 fifo_ready=17 wfull_hits=147 rempty_hits=16 wr_hit_hits=148 rd_hit_hits=148
CAPTURE_BOARD_FAIL capture_fifo_overflowed_during_transfer (gpio2 bit0=1 inside the window while IOC=0)
```

读法：
- `wfull` 命中 147/164、`rempty` 命中 16/164 ⇒ 读侧**在**动（一阵阵地抽干），不是死锁；
- 传输窗口内 `stat_overflow` 置起且 `IOC` 未到 ⇒ 真的掉了像素（`tlast` 会早到，必然 `Int_Err`）；
- 20 ms 窗口（≈ 0.8 帧）不够判「到底完成了没有」，而 300 ms 的探针测出 `VERDICT NO_PROGRESS`
  —— 那支探针自己也有毛病（先把图样停在空 FIFO 上再去武装 DMA，举证无效，已作废）。

为此加了第 4 组观测位（**决定「DMA 到底拉不拉」**）：

| 位 | 含义 |
|---|---|
| bit15 | `fifo.m_tready` 直通（下游拉不拉） |
| bit16..23 | `fifo` 读侧成功握手计数低 8 位（实际走掉几拍） |

并给 `--check` 加了新判据 `downstream_not_pulling`（FIFO 有数据但 `m_tready` 恒 0 且 `rd_cnt` 不变）。
**下一步**就是用这一版重出片、重跑，拿到「拉不拉 + 走几拍」的硬数字。

## 7. 工程事故与规矩

1. **本地 `.py` 被 PowerShell 改坏了**：用 `Get-Content -Raw | -replace | Set-Content -Encoding utf8`
   改 `src/ps/host/run_capture_test.py`，PS 5.1 默认按 ANSI(936) 读 UTF-8 ⇒ 全文中文变乱码，
   写回时又加了 BOM。**救回方式**：板上还留着上一轮 `scp` 上去的干净副本，从板子拉回来、
   再用 `edit` 工具重做改动。教训：改源码一律用 `edit`；必须写文件时用
   `[System.IO.File]::WriteAllText` + `UTF8Encoding($false)`。
2. **假证据一律落 `build/`**：桩脚本产物只许进 `build/stub/`，不许进 `data/results/` 或 `board/`。
3. **PS 死机的规律与恢复**：以前上板测试后十几分钟 ping 全断；`xsct connect` 能看到两个 A9
   在 Running，`targets -set -filter {name =~ "APU*"}; rst -system` 可远程重启 PS（45 s 恢复），
   不必断电。现已在 `run_capture_test.py` 的 `finally` 里先停图样再 `stop_recv()`，避免
   「DMA 武装着等数据 + IOC 中断常开」这种状态长时间挂着。
4. **判据必须先能失败**：`capture_golden.py --compare` 改 1 字节就会 `CAPTURE_MATCH_FAIL` 并定位
   `(x=360,y=1)`；BD 的 `NETCHECK` 也先在人为错连上验过会报错，之后才敢信它绿。

## 8. 复现步骤（当前版本）

```powershell
# PC 侧：重建 BD + 出片（约 8–12 min）
& "D:\Xilinx\Vivado\2020.2\bin\vivado.bat" -mode batch -source build\scripts\bd_capture_chain.tcl
# 判据：末行 BD_DONE_OK ... WNS=<正数>，且日志里有 NETCHECK n checks, 0 bad

# 部署 + 上板验收
& .\board\scripts\deploy_capture_pynq.ps1 -BoardHost 192.168.2.99 -Run -TestArgs '--check'
# 判据：CAPTURE_BOARD_OK frame_bytes=1228800 words=307200 crc32=d1c9c45a pad_ok=1

# 仿真回归（随时可跑，不需要板子）
& D:\python\python.exe sim\scripts\run_sim.py      # 期望 SIM_PASS 4/4 cases
```