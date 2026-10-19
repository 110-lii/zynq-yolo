# build/ —— 可复现构建脚本 + 综合/实现报告

> 上级：[`../README.md`](../README.md)
> 唯一事实源：[`../docs/repo_spec.md`](../docs/repo_spec.md) §2 / §4 / §5

| | |
|---|---|
| 本目录装什么 | **构建真源脚本**（`build/scripts/`）+ Vivado 综合/实现**报告产物**（`build/reports/`） |
| 谁产生 | 手写（脚本）/ Vivado（报告） |
| 进 git | 是；但**报告只保留最新 + baseline**（能由脚本重建的即产物，见 §4） |

## 内容

| 路径 | 说明 | 真源/产物 |
|---|---|---|
| `scripts/` | **构建真源**：创建工程、综合、实现、出比特流、导出报告的 tcl/ps1 脚本 | ✅ 真源（可改，必进 git） |
| `reports/synth/` | 综合报告（利用率、综合日志摘要） | ❌ 产物（会被重建覆盖） |
| `reports/impl/` | 实现报告（place/route、布线拥塞、时序摘要） | ❌ 产物 |
| `reports/timing/` | 时序报告（WNS/TNS/WHS/THS、失败路径） | ❌ 产物 |
| `reports/resource/` | 资源报告（LUT6 / FF / DSP48 / BRAM 占用与上限对比） | ❌ 产物 |
| `reports/drc/` | DRC / 检查报告（含 `[DRC MDRV-1]` 等多驱动类问题记录） | ❌ 产物 |
| `reports_prev_*/` | **报告轮转归档**：形如 `reports_prev_20261019/`（§1 规则 5：`_YYYYMMDD` 后缀） | ❌ 产物（可忽略/清理） |
| `baseline/` | 已固化的基线报告（只读参考，用于「优化前后对比」） | ⚠️ 仅保留基线 |

> 当前磁盘事实：`build/scripts/`、`build/reports/{synth,impl,timing,resource,drc}` **目录已存在但为空**。
> 具体构建脚本尚未创建 —— 见下方 **TODO**，**不要假装已存在**。

## 规则

- **`build/scripts/` 是构建真源**：所有构建动作必须能由这里的脚本**一条命令重建**（§4）。手工在 Vivado GUI 里点出来的步骤，必须以脚本形式回填，否则不可复现 = 不合规。
- **`build/reports/*` 是产物**：不得手工编辑；会被下一次构建覆盖，按 `.gitignore` 大部分排除。
- ### ★ 成功判据：日志令牌 `DONE_OK`（§5）
  **不要用退出码判断成功。** Vivado 的 `vivado.bat` 即使内部 `error` 也可能返回 0。
  - 综合 / 实现 / 出比特流：日志出现 **`DONE_OK`** 才算成功；
  - （JTAG 下载判据是 **`PROGRAM_OK`**，属于 `board/` 阶段的事，见 [`../board/README.md`](../board/README.md)。）
  - 脚本必须在结束时**显式检查并打印** `DONE_OK`，缺失即视为失败。
- **报告轮转约定**：新构建前把上一轮 `reports/` 整体改名为 `reports_prev_<YYYYMMDD>/`；
  仓库内**只保留最新一轮 + `baseline/`**，其余 `reports_prev_*` 可删除（它们能由脚本重建）。
- **禁止入库**（§1 规则 6，见根 `.gitignore`）：`build*.log`、`vivado*.log|jou`、`.Xil/`、`*.dcp`、`__pycache__/`、`.venv/`；
  即 `.bit` / `.rpt` / `.dcp` / `.log` 一律视为可重建产物。
- **数字必须标来源**（§6 纪律）：任何进入 `report/` 的资源/时序数字，必须能在 `build/reports/` 找到出处。
- 命名硬约束（§1）：目录名与文件名只用 `a-z 0-9 _ - .`；版本/日期后缀用 `_YYYYMMDD`；**文件正文可用中文**。
- 本机工具链仅 **Vivado / Vitis / Vitis_HLS 2020.2**（指南推荐 2026.1 或 2025.2 —— 用 2020.2 **须在报告中说明并保证脚本可复现**）；器件 `xc7z020clg400-2`。
- 加速器实现方式、核心算法形态、接口契约等未裁定项：`<待用户裁定：见 docs/repo_spec.md §7>`，构建脚本不得擅自固化。

## 怎么用

```powershell
# 0. 现状核对（当前 script/report 目录均在，脚本尚未写）
Get-ChildItem -Recurse D:\zynq-yolo\build

# 1. 【TODO，脚本尚未创建】轮转上一轮报告
#    TODO: build/scripts/rotate_reports.ps1  -> reports/ 改名为 reports_prev_<YYYYMMDD>/

# 2. 【TODO，脚本尚未创建】一键构建（创建工程 -> 综合 -> 实现 -> 出比特流 -> 导出报告）
#    TODO: build/scripts/build.tcl / build.ps1
#    期望调用形态（示意，以实际脚本为准）：
#    vivado -mode batch -source D:\zynq-yolo\build\scripts\build.tcl -tclargs xc7z020clg400-2

# 3. 判定成功：只认日志令牌
Select-String -Path D:\zynq-yolo\build\reports\*\*.log -Pattern 'DONE_OK'
#    找到 DONE_OK 才算成功；找不到 = 失败，即使进程退出码为 0

# 4. 报告去向：synth/ impl/ timing/ resource/ drc/ 各放对应阶段报告，
#    并把资源/时序关键数字填入 ../docs/repo_spec.md §6 体系与 ../report/
```

> 资源上限（7020，写报告须对照）：53 200 LUT6 / 106 400 FF / **220 DSP48** / **4.9 Mb BRAM** / 1 GB DDR3。
> 出处：`D:\FPGA\oi\CLAUDE.md:210-211`（一手还原见 [`../ref/hardware.md`](../ref/hardware.md) §B）。
> ⚠️ 不要把「≈140 × RAMB36」当成一手事实 —— 该括号在 `oi` 一手出处里查不到，属推算值（见 `ref/hardware.md` §K 第 1 条）。
