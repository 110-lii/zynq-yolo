# pynq_overlay_bringup —— 通用 PYNQ Overlay 上板体检

> 上级：[`../README.md`](../README.md) · 类型：**通用 PYNQ 技能包（换题目/换板卡仍可用，指南 3.3.5.2 单独加分）**

## 1. 适用范围

- **器件/板卡**：Zynq-7000（本队实测 XC7Z020-CLG400-2，正点原子 领航者 ZYNQ 7020）；
  同系列 7010/7020/7030 的 PYNQ 流程相同，资源数字不同。
- **软件**：PYNQ 2.7（Ubuntu 20.04 / kernel 5.4.0）、Python 3.8+；Vivado/Vitis_HLS 2020.2 导出的
  `.bit` + `.hwh`。
- **接口形态**：AXI-Lite 控制寄存器 + `m_axi` 数据端口（DMA/HP 口）；不覆盖 DPU、UltraScale+、
  PYNQ 3.x 的 `pynq.overlay` 差异。
- **适用任务**：第一次把自研 HLS/RTL IP 加载到板上、需要确认“寄存器能写、DMA 能搬、结果逐位对”。

## 2. 使用方法

### 2.1 最小上板链路

```python
from pynq import Overlay, allocate

ovl = Overlay("xxx.bit")          # 同目录必须有同名 .hwh
print(ovl.ip_dict)                 # 先确认 IP 名、物理地址、mem_id
buf = allocate(shape=(N,), dtype=np.uint8)
buf.flush()                        # CPU 写完 → 设备读之前
# 写 AXI-Lite 参数 → 写 AP_CTRL.start → 轮询 done
buf.invalidate()                   # 设备写完 → CPU 读之前
```

### 2.2 寄存器偏移不要硬编码

从 `.hwh` 的 `ADDRESSBLOCK/REGISTER` 解析 `ADDRESS_OFFSET`，按 `(INTERFACE, NAME)` 建表；
HLS 会把 `in`/`out` 改名成 `in_r`/`out_r`（与关键字冲突），解析时要兼容。参考实现：
`tools/board_detect.py:init_pl`。

### 2.3 64 位指针

`m_axi` 的指针参数是两个 32-bit 寄存器（低/高）。写 `buf.physical_address & 0xFFFFFFFF`
与 `>> 32`；不要假设地址在 32 位内。

### 2.4 上板体检顺序

1. `Overlay()` 是否成功、`ip_dict` 是否包含目标 IP；
2. AXI-Lite 是否有 `s_axi_control` 与 `s_axi_control_r` 两个窗口（漏接 `_r` 会 `KeyError`）；
3. 小缓冲（1 个 tile/1 行）逐位对；
4. 全尺寸 + 计时；
5. 连续跑 ≥10 分钟，记录 `uptime` 与串口，排除整机复位。

`checks/overlay_health.py` 是只读体检脚本：加载 overlay、打印 `ip_dict`/mem_id、
检查控制窗口，不写寄存器。

## 3. 失效条件（可观察现象）

| 条件 | 现象 | 处理 |
|---|---|---|
| `.hwh` 缺失或与 `.bit` 不匹配 | `Overlay()` 报找不到 hardware handoff / IP 名对不上 | 重新导出 `.hwh`，两者必须同一次构建 |
| BD 漏接 `s_axi_control_r`（`NUM_MI` 不足） | 板端 `KeyError: s_axi_control_r` | 在 BD 里显式连接第二个控制窗口 |
| HLS 把 `in/out` 改名 `in_r/out_r` | 按名字取寄存器失败 | 解析 `.hwh`，不要硬编码名字 |
| 忘记 `flush()` / `invalidate()` | 设备读到旧数据 / CPU 读到旧结果，逐位对随机失败 | 写后 flush、读前 invalidate |
| 改了 BD 时钟但 `ps7_init` 固定了 FCLK | 以为 100 MHz、实际 83.33 MHz，时序/吞吐对不上 | 用 PYNQ 运行时设时钟或重做 BOOT.BIN；以实测时钟为准 |
| PYNQ 版本/镜像变化 | `allocate`、`Overlay` API 行为不同 | 固定镜像版本；技能包按 2.7 验证 |
| 板子整机复位（非网络） | `uptime` 只有 1–5 分钟，日志无关机序列 | 查串口/供电/散热；先做双核满载压测复现 |

## 4. 提炼来源

> **范围（D007，2026-10-30 裁定）**：本包只提炼**本仓库自己的**会话记录与实测；
> 旧项目 `D:\FPGA\oi` 的经验**不作为来源**（只允许复用其引脚/电气资料）。

- `ai/sessions/003_conv_engine_bitstream.md`：第一次自研 IP 出片与 Overlay 加载；
- `ai/sessions/004_jtag_bringup.md`：JTAG 无 OS 路径与 PYNQ 路径的分工；
- `ai/sessions/007_pl_mask_preproc.md`：`core_revision` 溢出、`run_ippack.tcl` 必须在 IP 目录内跑；
- `ai/sessions/009_ps_pipeline_pl_preproc.md`：`s_axi_control_r` 漏接、`in_r/out_r` 改名、
  缓存一致性、板子整机复位与 20 分钟压测；
- `board/logs/board_axi_bw_20261022.md`、`board/logs/board_stream_new_20261022.md`：
  两块 overlay 的实际带宽/时序数字。

## 5. 已验证的效果

| 判据 | 结果 | 出处 |
|---|---|---|
| `axi_bw` overlay 加载 + 四模式 | `AXI_BW_OK`，32-bit 读 222.18 MB/s、64-bit 读 444.26 MB/s、拷贝 444.36 MB/s | `board/logs/board_axi_bw_20261022.md` |
| `preproc` overlay 加载 + 逐位 | PL 预处理 18.8 ms，与软件路径结果一致 | `ai/sessions/009_ps_pipeline_pl_preproc.md` |
| `conv_engine` 单层逐位 | `BOARD_LAYER_OK all_6_cases`（6/6 bit-exact） | `board/logs/board_jtag_layer_test_20261020.md` |
| 稳定性 | 20 分钟双核满载，164 轮、约 1148 次推理、无复位 | `ai/sessions/009_ps_pipeline_pl_preproc.md` |

> 判据是**能失败**的：漏接 `_r` 会直接 `KeyError`；缓存没 flush 会逐位对失败；
> 带宽不到 222/444 MB/s 就说明事务粒度/突发没配对。
