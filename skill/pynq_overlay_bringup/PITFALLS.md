# PITFALLS —— PYNQ Overlay 上板常见错误

每条按「触发条件 / 可观察现象 / 定位步骤 / 规避」写；不是“注意事项”清单，而是可排查的故障树。

## P1. `KeyError: s_axi_control_r`

- **触发**：BD 里只连了一个 `s_axi_control` 窗口，但 HLS IP 有两个（`NUM_MI=2`）。
- **现象**：Python 侧按 `s_axi_control_r` 取寄存器时抛 `KeyError`。
- **定位**：`print(ovl.ip_dict)` 看 IP 的 `mem_id` 与地址段；对照 `.hwh` 的 `ADDRESSBLOCK`。
- **规避**：BD 里显式连接第二个控制窗口；或从 `.hwh` 解析所有 `INTERFACE`，不要假设只有一个。

## P2. HLS 寄存器改名 `in_r/out_r`

- **触发**：顶层参数名是 `in` / `out`，与 HLS/Verilog 关键字冲突。
- **现象**：按 `in` 取寄存器失败，或 `.hwh` 里名字与源码不一致。
- **定位**：读 `.hwh` 的 `REGISTER NAME`，打印实际名字。
- **规避**：按 `.hwh` 解析；或源码里直接命名 `in_data/out_data`。

## P3. 缓存一致性

- **触发**：CPU 写输入后直接启动 IP，或 IP 写完后 CPU 立即读。
- **现象**：逐位对偶尔错、热缓存时对、冷缓存时错。
- **定位**：在启动前/读回前加 `flush()`/`invalidate()` 复测。
- **规避**：写后 `buf.flush()`，读前 `buf.invalidate()`；不要把 `allocate` 的缓冲当普通 numpy 数组。

## P4. 64 位指针只写低 32 位

- **触发**：`m_axi` 指针参数按 64 位地址实现，软件只写了一个 32-bit 寄存器。
- **现象**：IP 访问错误地址、DMA 无输出或系统挂起。
- **定位**：看 HLS 生成的 `*_hw.h`/`.hwh` 是否有 `_1`/`_2` 两个寄存器。
- **规避**：低/高 32 位都写；从 `buf.physical_address` 取，不假设 <4 GB。

## P5. 时钟不是你以为的频率

- **触发**：只改 BD 的 FCLK 约束，但启动 `ps7_init` 固定了时钟。
- **现象**：时序/吞吐与报告不符；83.33 MHz 与 100 MHz 混用。
- **定位**：在板上读 `clk_get_rate` 或 PYNQ 时钟 API；对照 bitstream 构建时的约束。
- **规避**：以板上实测时钟为准；要改就重做 BOOT.BIN 或运行时设时钟。

## P6. 整机复位被误判为网络问题

- **触发**：板子供电/散热不足、或 IP 满载。
- **现象**：`uptime` 只有 1–5 分钟；串口无关机序列；无 panic/Oops。
- **定位**：串口日志 + `uptime` + 双核满载压测。
- **规避**：先排除供电/散热；用 20 分钟满载压测作稳定性判据（本队实测 164 轮无复位）。

## P7. IP packer 的 `core_revision` 溢出

- **触发**：Vivado 2020.2 把 Revision 写成 `YYMMDDHHMM`（如 2610211218 > 2^31）。
- **现象**：IP packer 用 int32 解析失败，导出中断。
- **定位**：看 `run_ippack.tcl` 的 `set Revision` 与导出日志。
- **规避**：把 Revision 改成小整数；并**在 IP 目录内**跑 `run_ippack.tcl`，否则 component.xml 残缺。
