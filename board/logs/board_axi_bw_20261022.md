# 板端 AXI 带宽标定（axi_bw，100 MHz）

- 日期：2026-10-22
- 平台：领航者 ZYNQ 7020（PYNQ 2.7），FCLK0 = 100 MHz
- 比特流：`board/overlay/axi_bw.bit`（WNS +2.318 ns，LUT 4706 / 8.85%，DSP 0）
- 数据面：`gmem_r32→HP0`、`gmem_r64→HP1`、`gmem_w→HP2`，各 32 outstanding / 64 beat 突发
- 缓冲区：每个 8 MiB，核内 `reps=4`，每模式预热 2 次、计时 5 次取最小

## 结果

| 模式 | 搬运量 | 最好耗时 | 吞吐 |
|---|---:|---:|---:|
| 32-bit 读 | 32 MiB | 0.151022 s | **222.18 MB/s** |
| 64-bit 读 | 32 MiB | 0.075528 s | **444.26 MB/s** |
| 32-bit 写 | 32 MiB | 0.151025 s | **222.18 MB/s** |
| 32-bit 拷贝（读+写并发） | 64 MiB | 0.151024 s | **444.36 MB/s** |

判据令牌：`AXI_BW_OK 4 read32=222.18 read64=444.26 write32=222.18 copy32=444.36`

## 解读

- 32-bit 口理论峰值 400 MB/s（4 B × 100 MHz），实测 **222 MB/s = 55.5%**。
- 64-bit 口理论 800 MB/s，实测 **444 MB/s = 55.5%**，与 32-bit 同比例 ⇒ 限制因素是
  outstanding 数量与 DDR 往返延迟，不是端口位宽。
- 拷贝模式读写各 222 MB/s 同时成立 ⇒ 读口与写口互不争用，聚合 444 MB/s。
- 相对改造前 conv_engine 的 10–22 MB/s，提升 **10–20 倍**，证明"字节事务"才是旧瓶颈。

## 对 conv_engine 的含义

- 单 32-bit 读口实际可用 **222 MB/s**；按周期模型，新引擎整网约 77 MB 流量若在 3 s 内完成，
  只需 ~26 MB/s ⇒ 改写后瓶颈已从"带宽"转为"装载与计算串行 + 每 ci 的 DDR 往返"。
- 下一步收益顺序：装载/计算重叠（ping-pong）> 拆第二个读 bundle > 消除 `co0` 重复读。

## 复现

```bash
bash /home/xilinx/zynq-yolo/board/scripts/run_axi_bw.sh
```

脚本在板端写 `board/logs/board_axi_bw_<时间>.log/.md`；本文件是 PC 侧留档。
