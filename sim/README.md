# sim/ —— 仿真与验证（testbench、激励向量、回归脚本与结果）

> 上级：[`../README.md`](../README.md)
> 唯一事实源：[`../docs/repo_spec.md`](../docs/repo_spec.md) §2.2 / §4

| | |
|---|---|
| 本目录装什么 | RTL 的 testbench（`sim/tb/`）、激励向量（`sim/vectors/`）、回归脚本与仿真结果入口（`sim/scripts/`） |
| 谁产生 | 手写（仿真器产生的波形/日志为产物） |
| 进 git | 是（仿真真源 `sim/tb`、`sim/scripts` 必须进；波形与临时日志不进） |

## 内容

| 路径 | 说明 |
|---|---|
| `tb/` | **Testbench 真源**。镜像 `src/rtl/` 的子目录结构，见下表 |
| `vectors/` | 激励向量：输入激励文件、期望输出、随机种子记录等，供 tb 读取 |
| `scripts/` | 回归入口脚本（编译 + 仿真 + 结果判定），一条命令跑一轮回归 |
| （`tb/` 下的镜像子目录） | 当前磁盘上 `sim/tb/` 为**空目录**；按下表在新增用例时按需创建 |

### `sim/tb/` 镜像 `src/rtl/` 子目录结构

`sim/` **镜像** `src/rtl/` 的子目录结构；`x.v` 的 testbench 命名为 **`x_tb.v`**。

```text
src/rtl/                      sim/tb/                 （镜像）
├── common/   ─────────────►  common/
├── video/    ─────────────►  video/
├── accel/    ─────────────►  accel/
├── io/       ─────────────►  io/
└── ip/       ─────────────►  （IP 自带仿真模型，一般无需 tb）
```

| `src/rtl/` 下模块（**命名示例，非现有文件**） | 对应 testbench（`sim/tb/` 下同镜像路径） |
|---|---|
| `src/rtl/common/inf_reset_sync.v` | `sim/tb/common/inf_reset_sync_tb.v` |
| `src/rtl/video/vid_dvp_capture.v` | `sim/tb/video/vid_dvp_capture_tb.v` |
| `src/rtl/accel/acc_dma_mover.v` | `sim/tb/accel/acc_dma_mover_tb.v` |
| `src/rtl/io/io_axil_regs.v` | `sim/tb/io/io_axil_regs_tb.v` |

> ⚠️ **当前磁盘事实**：`sim/tb/`、`sim/vectors/`、`sim/scripts/` **均已存在但为空**；上表左右两列的文件**都还不存在**，仅为命名规则示意。

> 命名规则一句话：**`x.v` → `x_tb.v`**，且 tb 在 `sim/tb/` 下的相对路径与源文件在 `src/rtl/` 下的相对路径**逐段相同**。

## 规则

- tb 文件名必须是 `x_tb.v` 形式，**不允许** `tb_x.v` / `test_x.v` / `x_test.v` 等变体。
- tb 必须放在与源模块**逐段相同**的镜像路径下；路径不对视为违反 §2.2。
- 激励向量统一放 `sim/vectors/`，**不要**散落在 `tb/` 或仓库根。
- 回归入口统一放 `sim/scripts/`；脚本必须能**一条命令**跑完并给出可判定的结论。
- **不要用退出码判断成功**（§5）：仿真结论必须落到日志令牌/断言计数上（例如 `TEST_PASS` / `TEST_FAIL` 或 UVM 风格 error count == 0）；本项目综合/实现阶段的令牌为 `DONE_OK`，JTAG 下载为 `PROGRAM_OK`（见 [`../build/README.md`](../build/README.md)、[`../board/README.md`](../board/README.md)）。
- 波形文件（`*.vcd` / `*.wdb` / `*.fsdb`）与仿真日志属**产物**，不进 git；有价值的失败波形如需保留，请转为图片或文本摘要后落盘。
- 命名硬约束（§1）：目录名与文件名只用 `a-z 0-9 _ - .`；禁止中文、空格、括号、全角字符；**文件正文可用中文**。
- 对应 RTL 待裁定项（加速器实现方式、核心算法形态、接口契约数值等）：`<待用户裁定：见 docs/repo_spec.md §7>`。

## 怎么用

```powershell
# 1. 查看镜像结构现状（当前 tb/ vectors/ scripts/ 均存在，尚无文件）
Get-ChildItem -Recurse D:\zynq-yolo\sim

# 2. 为一个 RTL 模块建用例（先建镜像目录，再建 x_tb.v）
New-Item -ItemType Directory -Force D:\zynq-yolo\sim\tb\common
#   -> 新建 D:\zynq-yolo\sim\tb\common\inf_reset_sync_tb.v
#   -> 激励放 D:\zynq-yolo\sim\vectors\

# 3. 跑回归：由 sim/scripts/ 下的入口脚本驱动（脚本尚未创建，见下方 TODO）
#    TODO: sim/scripts/run_regression.ps1 —— 批量编译+仿真+汇总 PASS/FAIL

# 4. 结论记录：本机工具链仅 Vivado / Vitis / Vitis_HLS 2020.2（xsim 随之）；
#    仿真结果摘要写入 ../build/README.md 所述报告体系或 ../report/
```

> 依赖的本机环境事实见 [`../docs/repo_spec.md`](../docs/repo_spec.md) §6：器件 `xc7z020clg400-2`，工具链 **2020.2**。
