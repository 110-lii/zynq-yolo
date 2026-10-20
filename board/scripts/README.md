# board/scripts/README.md

> 上级：[`../README.md`](../README.md)
> 唯一事实源：[`../../docs/repo_spec.md`](../../docs/repo_spec.md) §2 / §4 / §5

| | |
|---|---|
| 本目录装什么 | **上板真源**：把仓库搬运到板子（SCP）、在板上跑验收、把结果带回 PC 的三段式脚本 |
| 谁产生 | 手写 |
| 进 git | 是（`board/scripts/` 是上板真源，必进 git，见 repo_spec §2） |

## 内容

| 脚本 | 跑在哪 | 职责 | 状态 |
|---|---|---|---|
| `deploy_to_board.ps1` | **PC** | 检查本地产物齐全 → 打 tar 包（只带真源 + 数据，不带 `.git`/产物）→ `scp` 到板 → 远程解包到 `/home/xilinx/zynq-yolo` | ✅ 已创建（2026-10-20，**尚未实跑**） |
| `run_board_test.sh` | **板** | 板上跑 `src/ps/host/run_layer_test.py`，stdout 存 `board/logs/`，回传 JSON 里的关键摘要 | ✅ 已创建（2026-10-20，**尚未实跑**） |
| `jtag_layer_test.tcl` | **PC（XSCT）** | **无操作系统路径**：`rst -system` → `ps7_init` → 停 A9 核 → `fpga` 烧 PL → `dow -data` 灌向量 → `mwr` 寄存器 → 轮询 `ap_done` → `mrd` 读回比对。用于 JTAG 模式（PS 不启动 Linux）时验证**字节正确性** | ✅ **已实跑通过**（2026-10-20）：`JTAG_TEST_OK 6 / 6`，0 / 54 784 字节不符，**连续 3 次复现**。证据见 [`../logs/board_jtag_bringup_20261020.md`](../logs/board_jtag_bringup_20261020.md) 与 [`../logs/board_jtag_layer_test_20261020.md`](../logs/board_jtag_layer_test_20261020.md) |
| `jtag_capture_test.tcl` | **PC（XSCT）** | **M1 采集链**无 OS 验收：`ps7_init` → 停核 → `fpga` 烧 `capture_chain.bit` → `ps7_post_config` → 按 `capture_chain_addr.txt` 找 DMA/GPIO 基址 → **先武装 DMA S2MM（DA/LENGTH/DMACR）再放图样（GPIO bit0=1）** → 轮询 `DMASR.IOC` → 查 `GPIO2.bit0`（采集 FIFO 溢出粘滞）→ `mrd -bin` dump DDR → 交 `tools/capture_golden.py --compare` | ⬜ 已创建，**待上板实跑**（判据 `JTAG_CAPTURE_OK` / `JTAG_CAPTURE_FAIL`；与 `sim` 的 `vid_capture_chain` 用**同一份黄金模型**） |
| `stub_jtag_capture_dryrun.tcl` | **PC（XSCT，无板）** | `jtag_capture_test.tcl` 的**假硬件试跑**：把 `mwr/mrd/connect` 换成 Tcl 桩，DMA 立即完成、DDR dump 由黄金 bin 生成（`pass`）或翻一个字节（`corrupt`）。用途是证明**判据能失败**，并让脚本改动不必每次都上板 | ✅ 已跑：`pass` → `JTAG_CAPTURE_OK`；`corrupt` → `JTAG_CAPTURE_FAIL`（并定位到 `(x=360,y=1)`） |
| `deploy_capture_pynq.ps1` | **PC** | **PYNQ 路径的部署入口**（M1 现在走这条）：`scp` `capture_chain.bit/.hwh` → `board/overlay/`、`src/ps/host/run_capture_test.py` → `src/ps/host/`、`run_capture_test.sh` → `board/scripts/`，`-Run` 时接着远程跑后者。参数：`-BoardHost 192.168.2.99 [-Run] [-TestArgs '--check'\|'--bench 20']` | ✅ 已实跑（2026-10-20 多轮）：`DEPLOY_CAPTURE_OK <n> <remote_dir>` / `DEPLOY_CAPTURE_FAIL` |
| `run_capture_test.sh` | **板** | 板上包一层 **`sudo -S bash -lc`** 再跑 `src/ps/host/run_capture_test.py`（`-lc` 不是多余：sudo 重置环境后只有 login shell 才 source 到 `pynq_venv.sh` / `xrt_setup.sh`） | ✅ 已实跑（2026-10-20） |

> ⚠️ 板上/SCP 两个脚本**从未在真实板子上执行过**（截至 2026-10-20）；`jtag_layer_test.tcl` **已实跑通过 6/6 并三次复现**。
> 三个脚本的判据都以**令牌**为准（`DEPLOY_OK` / `BOARD_LAYER_OK` / `JTAG_LAYER_OK`），**不看退出码**。

## 三条路径怎么选

| 板子状态 | 用哪个 | 能回答什么 |
|---|---|---|
| SD 启动 PYNQ（Linux 在跑） | `deploy_capture_pynq.ps1` + `run_capture_test.sh`（**M1 采集链现在走这条**） | 采集链字节对不对 ＋ fps/MBps |
| SD 启动 PYNQ、跑旧卷积层验收 | `deploy_to_board.ps1` + `run_board_test.sh` | 卷积层字节对不对 ＋ 多快（GOPS） |
| **JTAG 模式（无 OS）** | `jtag_layer_test.tcl`（层验收，**已 6/6 通过**）、`jtag_capture_test.tcl`（采集链，**未跑**） | 只回答**字节对不对**；吞吐测不了 |
| 裸板无 OS、要看波形 | 见技能包 `vivado-cli-bringup`（ILA 抓波形） | 时序/协议波形 |

### PYNQ 路径实测到的环境硬约束（2026-10-20，全是踩过以后写下的）

1. **必须 `sudo -S bash -lc`**：`sudo` 会重置环境，只有 login shell 才 source 到
   `/etc/profile.d/pynq_venv.sh` 与 `xrt_setup.sh`；否则分别报 `No module named 'pynq'`
   和 `No devices found, is the XRT environment sourced?`。
2. **必须 `cd` 到仓库根**：`run_capture_test.py` 的 `--bitfile` 默认是相对路径，而 ssh
   登录的 cwd 是 `/home/xilinx`，否则 `CAPTURE_BOARD_FAIL bitfile_missing`。
3. **PC 侧免密**：本机只有 OpenSSH（无 sshpass/pscp）。用 `SSH_ASKPASS` +
   `SSH_ASKPASS_REQUIRE=force` + `DISPLAY=localhost:0` 指向一个只输出密码的 `.cmd` 即可
   （脚本里已封装）。Jupyter REST API 写 `~/.ssh` 走不通（XSRF + contents root 限制）。
4. **`.ps1` 必须存成 UTF-8 带 BOM**：PS 5.1 对无 BOM + 中文注释的文件会报出与内容不符的
   语法错（例如 `[switch]$Run,` 处「,后面缺少表达式」）。
5. **改板上 `.py` 的字符串别用 PowerShell `Get-Content | Set-Content`**：PS 5.1 默认按
   ANSI(936) 读，会把 UTF-8 中文读成乱码再写回，**整个文件的注释就毁了**；用 `edit` 工具
   或 `[System.IO.File]::WriteAllText` + `UTF8Encoding($false)`。

## 搬运清单（deploy 脚本实际打包的内容）

| 路径 | 为什么带 | 为什么不带别的 |
|---|---|---|
| `src/ps/` | 驱动 + 验收脚本（运行真源） | 其余 `src/` 板上用不到 |
| `board/overlay/conv_engine.{bit,hwh}` | Overlay 本体 | 出片产物只此一对，必须与当前构建同轮 |
| `data/layers/cases/` | 6 个验收用例（含被 `.gitignore` 排除的 `*.bin`——**从本地磁盘打包**，板上是运行必需件） | `data/` 其余（golden/npz/视频）本轮不需要 |
| `docs/accel_interface.md` | 契约（板上排查时对照 §4 寄存器表） | — |
| `.gitattributes` | 保持 eol=lf 口径 | — |

不打包：`.git/`（板不需要历史）、`build/`（1 GB+ 产物）、`data/models/`（本轮不做整网）、
`data/results/viz/`（14 MB 复核图）。

## 用法（完整三段）

```powershell
# ── PC 侧（Windows，pwsh）──────────────────────────────────────────
# 1) 打包并推到板（默认 xilinx@192.168.1.xx，见脚本参数）
& .\deploy_to_board.ps1 -BoardHost 192.168.1.99 -BoardUser xilinx
#    判据：末行 DEPLOY_OK（tar 体积 + scp 退出码 + 远程解包行数三者齐全才打）

# ── 板上（ssh 或串口）──────────────────────────────────────────────
# 2) 跑验收（sudo 必须：/dev/mem 与 PYNQ 需要根）
ssh xilinx@192.168.1.99
bash /home/xilinx/zynq-yolo/board/scripts/run_board_test.sh
#    判据：末行 BOARD_LAYER_OK all_6_cases <ns> <gops>（run_layer_test.py 的令牌）

# ── PC 侧（收尾）───────────────────────────────────────────────────
# 3) 把板上的 JSON 结果拉回 PC（覆盖 data/results/board_layer_test.json，
#    这一步之后才能 commit 实测数据）
scp xilinx@192.168.1.99:/home/xilinx/zynq-yolo/data/results/board_layer_test.json `
    D:\zynq-yolo\data\results\board_layer_test.json
```

## 规则

- **成功判据是令牌不是退出码**（repo_spec §5）：`DEPLOY_OK` / `BOARD_LAYER_OK` / `PROGRAM_OK`。
- `deploy_to_board.ps1` 里**不硬编码板子 IP/用户名**：默认值只作占位，实跑必须传参或改参数默认。
- 板上脚本只用 bash + python3（PYNQ 2.7 镜像自带），**禁止 pip install**（见 pynq_driver/README）。
- 结果 JSON（`data/results/board_layer_test.json`）被真板覆盖后**必须提交**——它是"实测数字"
  的唯一合法来源；`board/logs/*.log` 是不可重建证据，同样择优入 git（`.gitignore` 已放行 `*.md`，
  原始 `.log` 由脚本同时转存一份 `.md` 摘录）。
- 命名硬约束（repo_spec §1）：文件名纯 ASCII；正文可用中文。
