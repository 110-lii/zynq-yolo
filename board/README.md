# board/ —— 上板工程、运行脚本、实测输出

> 上级：[`../README.md`](../README.md)
> 唯一事实源：[`../docs/repo_spec.md`](../docs/repo_spec.md) §2 / §4 / §5

| | |
|---|---|
| 本目录装什么 | 上板所需的一切：Overlay 产物（`.bit`/`.hwh`）、上板运行脚本、板级日志、实拍图 |
| 谁产生 | 手写（脚本）/ 板级产生（日志、图片） |
| 进 git | 是；**不能由脚本重建的证据必须落盘**；能重建的产物（`.bit`）只保留基线 |

## 内容

| 路径 | 说明 | 真源/产物 |
|---|---|---|
| `overlay/` | Overlay 二进制与硬件握手文件：`*.bit` + `*.hwh`（PYNQ 加载自研 Overlay 用），可含 `.xsa` | ❌ 产物（由 `build/` 重建，只留基线） |
| `scripts/` | **上板真源**：下载/烧写、加载 Overlay、跑用例、抓日志的脚本 | ✅ 真源（可改，必进 git） |
| `logs/` | **板级实测日志**：串口输出、运行日志、实测数字 | ⚠️ **不可重建 ⇒ 必须落盘**（择优进 git 作基线） |
| `images/` | **实拍图**：板卡连接、LCD 显示、示波器/ILA 截图等肉眼证据 | ⚠️ **不可重建 ⇒ 必须落盘** |

> 当前磁盘事实：`board/{overlay,scripts,logs,images}/` **目录均已存在但为空**。上板脚本与实测证据尚未产生。

## 规则

- ### ★ 不可重建的证据必须落盘（§4 判据）
  > **不能由脚本重建的（串口日志、肉眼观察、实拍截图、实测数字）必须落盘。**
  即：`board/logs/` 与 `board/images/` 里的内容**不得**只存在于个人电脑/聊天记录里；一次上板实验结束就要归档。
  相反，能重建的（`.bit` / `.rpt` / `.dcp` / `.log`）由 `.gitignore` 排除，不入 git。
- ### ★ 上板成功判据：日志令牌 `PROGRAM_OK`（§5）
  **不要用退出码判断成功。** JTAG 下载阶段以日志出现 **`PROGRAM_OK`** 为成功判据；
  （综合/实现/出比特流阶段的判据是 **`DONE_OK`**，见 [`../build/README.md`](../build/README.md)。）
- Overlay 加载用 `*.bit` + `*.hwh` 配对，**版本必须与 `build/` 同一轮构建一致**；`.bit` 与 `.hwh` 不匹配会加载失败。
- `board/scripts/` 是上板真源：所有操作必须能由脚本复现；GUI 里点出来的步骤要回填成脚本。
- **实测数字必须标来源**（§6 纪律）：每条写进 `report/` 的实测数据，都要能对应到 `board/logs/` 的具体文件。
- 命名硬约束（§1）：目录名与文件名只用 `a-z 0-9 _ - .`；日期后缀用 `_YYYYMMDD`；**文件正文可用中文**。
- **禁止入库**（§1 规则 6）：`build*.log`、`vivado*.log|jou`、`.Xil/`、`*.dcp`、`__pycache__/`、`.venv/`。
- 本机环境事实（§6）：开发板 **正点原子 领航者 ZYNQ 7020**，底板 **V3.7**（音频 WM8960）+ 核心板 ZYNQ_CORE_2V5；器件 `xc7z020clg400-2`。
  ★ **底板版本陷阱**：**V3.7 ≠ V3.9**（V3.9 音频改 ES8388 且 I2S 引脚不同）；`D:\FPGA\NAVIGATOR_ZYNQ_IO.xdc` 是 **V3.9** 文件，**勿用**。
- 旧项目已验证路径：曾用 SD 启动 PYNQ（Ubuntu 20.04 / kernel 5.4.0）成功加载自研 Overlay（`.bit` + `.hwh`）。
- 软硬件划分边界、AXI 接口契约、核心算法形态等未裁定项：`<待用户裁定：见 docs/repo_spec.md §7>`。

## 怎么用

```powershell
# 0. 现状核对（目录均在，尚无内容）
Get-ChildItem -Recurse D:\zynq-yolo\board

# 1. 从 build/ 取同一轮构建的 Overlay（重新构建后需同步覆盖）
#    Copy-Item D:\zynq-yolo\build\...\*.bit,D:\zynq-yolo\build\...\*.hwh D:\zynq-yolo\board\overlay\

# 2. 【TODO，脚本尚未创建】JTAG 下载并判定
#    TODO: board/scripts/program.ps1 / program.tcl
#    vivado -mode batch -source D:\zynq-yolo\board\scripts\program.tcl
#    判据：日志出现 PROGRAM_OK
Select-String -Path D:\zynq-yolo\board\logs\*.log -Pattern 'PROGRAM_OK'

# 3. 【TODO，脚本尚未创建】PYNQ 侧加载 Overlay 并跑用例
#    TODO: board/scripts/run_on_board.py（配合 ../src/ps/pynq_driver/ 的 Overlay 封装类）
#    python D:\zynq-yolo\board\scripts\run_on_board.py 2>&1 | Tee-Object D:\zynq-yolo\board\logs\run_<YYYYMMDD>.log

# 4. 归档不可重建证据（必做，否则本次上板结论不可追溯）
#    - 串口日志 -> D:\zynq-yolo\board\logs\
#    - 实拍图   -> D:\zynq-yolo\board\images\
```
