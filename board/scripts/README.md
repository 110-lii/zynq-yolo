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

> ⚠️ 板上/SCP 两个脚本**从未在真实板子上执行过**（截至 2026-10-20）；`jtag_layer_test.tcl` **已实跑通过 6/6 并三次复现**。
> 三个脚本的判据都以**令牌**为准（`DEPLOY_OK` / `BOARD_LAYER_OK` / `JTAG_LAYER_OK`），**不看退出码**。

## 三条路径怎么选

| 板子状态 | 用哪个 | 能回答什么 |
|---|---|---|
| SD 启动 PYNQ（Linux 在跑） | `deploy_to_board.ps1` + `run_board_test.sh` | 字节对不对 **＋ 多快（GOPS/fps）** |
| **JTAG 模式（本文件记录的场景）** | `jtag_layer_test.tcl` | 只回答**字节对不对**；吞吐测不了 |
| 裸板无 OS、要看波形 | 见技能包 `vivado-cli-bringup`（ILA 抓波形） | 时序/协议波形 |
| JTAG 模式、要复核字节 | `jtag_layer_test.tcl`（**已 6/6 通过**） | 与 PYNQ 路径交叉验证同一批用例 |

> ⚠️ 两个脚本都**从未在真实板子上执行过**（截至 2026-10-20）。它们按
> [`../../src/ps/pynq_driver/README.md`](../../src/ps/pynq_driver/README.md) 的既有约定
> （仓库放 `/home/xilinx/zynq-yolo`、PYNQ 2.7、`sudo python3`）写成，第一次实跑后按
> 实际情况修偏并**在此处把状态改为"已实跑"**。

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
