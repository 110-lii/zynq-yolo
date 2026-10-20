# Session 004 — JTAG 模式（无操作系统）下的首次真机上板尝试

> 整理层记录。原始层：本会话（2026-10-20）尚未导出到 `ai/raw/`，
> 故「原文位置」如实标注为**待导出**；板级证据已独立落盘
> [`../../board/logs/board_jtag_bringup_20261020.md`](../../board/logs/board_jtag_bringup_20261020.md)。

| 字段 | 内容 |
|---|---|
| 会话号 | 004 |
| 日期 | 2026-10-20 |
| 执行 AI（模型 ID） | `group/1101`（客户端 `dsh`） |
| 角色 | Lead（本会话无子代理） |
| 任务目标 | 板子处于 JTAG 模式、人不在板边时，仍要拿到 conv_engine 的真机证据 |
| 上下文投喂 | `docs/accel_interface.md` §4/§7.4、`src/ps/pynq_driver/conv_engine.py`（寄存器偏移）、BD 工程生成的 `ps7_init.tcl`、技能包 `vivado-cli-bringup` |
| **原文位置** | `ai/raw/` **待导出**（`ai/tools/export_transcript_dsh.py` 下次运行后补） |
| 关联决策 | 无新增决策；D003/D006 的「事实进展」再加一条真机证据（状态仍 `discussing`） |
| 复核方 | 未复核（令牌与日志见 board/logs/） |

---

## 轮次 1 — 路径选择

**用户输入（逐字）：**

```
现在板子是jtag模式，现在我在外面没办法调整，但是pynq的镜像就在sd卡里
```

**判断：** 放弃 ssh/PYNQ 路径（PS 不从 SD 启动），改走「不开操作系统、纯 JTAG 驱动」路线：
`ps7_init → fpga → DAP 读写 AXI/DDR`。理由：`vivado-cli-bringup` 技能明确覆盖「裸板 + JTAG、无系统」场景；
且 `ps7_init.tcl` 已由 BD 工程生成，具备不跑 FSBL 就初始化 DDR 的条件。

## 轮次 2 — 实跑与四轮纠错

见下方纠错轨迹。**最终拿到 `JTAG_TEST_OK 6 / 6`（54 784 字节 0 不符，连续 3 次复现）** —— 四次「像硬件坏了」的现象全部是测量侧问题。

---

## ★ 自我纠错轨迹

| # | 错在哪（当时的原话/原做法） | 怎么发现 | 证据 | 怎么改 | 判据 |
|---|---|---|---|---|---|
| 1 | 以为 `mwr/mrd` 可直接访问 `0x4000_0000` 的 PL 侧寄存器 | 首次运行报 `Blocked address 0x40000000. PL AXI slave ports access is not allowed. This address has not beed added to the memory map` | 该报错原文（skill 陷阱 #6 的同款） | 用 XSCT 的 `memmap -addr ... -size ...` 先登记区域 | 登记后 `mrd 0x40000000` 返回 `00000004`（idle） |
| 2 | 把 `mrd` 输出当成 `0xADDR: 0xVAL` 解析 | `block_read_short got=0 ref=0 want=6400` —— 正则一条都没匹配上 | 实测打印格式为 `40000000:   00000004`（**无 `0x` 前缀**） | 正则改为 `^([0-9A-Fa-f]+):\s+([0-9A-Fa-f]+)$` | 同一命令解析出 6400/6400 字 |
| 3 | 以为 `ps7_init` 之后 CPU 是停着的 | `targets` 实测 `ARM Cortex-A9 MPCore #0 (Running)`、`#1 (Running)` —— 两个核在跑 DDR 里的垃圾数据，既抢内存又让 DAP 反复报 `AHB AP transaction error, DAP status 0x30000021` | `targets` 输出；以及 DAP 报错与「输出部分错误」同时出现 | `ps7_init` 后 `targets 2/3; stop` 把两核按停（`(Suspended)`） | 核状态变为 Suspended；此后 DAP 报错显著减少 |
| 4 | 把「启动后第一次读到 ap_done」当成「内核已算完可以读」 | 内存竞态：`polls=1` 即判 done 并立刻块读，得到 `got=0xCC...`（哨兵未覆盖）；延迟 3 s 后同一位置仍是错值，但**两次运行之间只差 1 字** | `RUN1_VS_RUN2_DIFF_WORDS=1`；`bad=3096/25600` vs 延迟后改善 | 去掉启动前的 `ap_done` 预读（预读与 `ap_start` 采样竞争），并在 done 后加稳定期 | 差异从「全量错」收敛为「局部 1 字抖动」——说明之前的巨量错误主要是读取窗口问题 |

> **本会话最重要的方法论收获**：三次「看起来像硬件算错」的现象，前两次都是**测量方式错**
> （访问许可 / 输出格式 / 读取时机 / CPU 抢内存）。判定「硬件算错」之前，必须先用
> 对照实验把测量路径本身排除掉。

---

## 产出文件

| 路径 | 说明 |
|---|---|
| `board/scripts/jtag_layer_test.tcl` | 新增：纯 JTAG 单层验收脚本（含上述四轮纠错的注释） |
| `board/logs/board_jtag_bringup_20261020.md` | 新增：本轮板级证据、弯路档案、未决问题 |
| `board/logs/board_jtag_layer_test_20261020.md` | 新增：`JTAG_TEST_OK 6/6` 的完整 stdout（不可重建证据） |
| `data/results/jtag_layer_test.json` | 新增：机读结果（6 用例、0 字节不符；**刻意不含耗时**） |
| `board/scripts/README.md` | 更新：登记 JTAG 脚本与「已实跑通过 6/6」状态 |
| `board/README.md` | 更新：上板路径增加 JTAG 分支 |

## 未决问题

1. ✅ ~~`JTAG_LAYER_OK` 未拿到~~ —— **已解决**：`rst -system` + 按停 A9 核后，
   6 用例 54 784 字节 **0 不符**，连续 3 次复现。
2. **吞吐仍无实测**：JTAG 路径刻意不测 GOPS（往返毫秒级）；需 SD 启动 PYNQ 后用
   `src/ps/host/run_layer_test.py`。这是**唯一**还缺的上板数据。
3. **整网未上板**：`tools/pynq_net.py` 的 PL 后端仍是骨架，64 层逐层链接未在板上验证。
4. **「板子掉了」是误判**：一度 `targets` 全空，但 Windows 设备树里 Digilent
   `C3059298ABCD` 在位、重扫后链上器件都在 ⇒ 是 hw_server 状态问题，不是掉电。
5. 本轮原文尚未导出到 `ai/raw/`。

## 结论与沉淀

- **可用结论**：`conv_engine.bit` 在真实 XC7Z020 上，6 个边界用例（含 `C_IN=512`、
  `C_OUT=20` 尾块、`pad=0`、`stride=2`、`K=1` 窄列）输出与独立 numpy 金标准**逐字节相等**，
  合计 54 784 字节 0 不符，三次复现。**这是本项目第一次真机功能验证。**
  但**吞吐仍为 `null`**，不得据此外推出 fps。
- **可沉淀**：`→ ../../skill/`（建议）「板级测量路径排除法」：访问许可（`memmap`）→ 输出格式
  （`mrd` 无 `0x` 前缀）→ CPU 抢占（`ps7_init` 不停核）→ 平台复位状态（`rst -system`）
  → 才轮到怀疑设计；再加一条「判断板子掉电前先查 USB 设备树与 hw_server 实例」。
  本轮四次假故障（含一次误判掉电）全部属于测量/工具侧，真故障数 0。
