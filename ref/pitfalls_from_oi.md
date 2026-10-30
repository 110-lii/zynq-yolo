# pitfalls_from_oi.md —— 从旧项目 `D:\FPGA\oi` 继承的可复用踩坑清单

> ⚠️ **范围收窄（D007，用户 2026-10-30 裁定）**：与旧项目之间**只复用引脚/电气资料**。
> 本文件记录的**旧项目现象结论一律降级为背景参考，不作为本仓库结论的依据** ——
> 要写进报告或代码，必须由本仓库自己的自测或上板复现一次；复现不出来就不写。
> 不复用旧项目的任何 RTL/Python 源码。

> **抽取原则**：只抽**可复用的坑**（环境、工具链、板卡、编码、判据），**不抄旧项目的业务内容**（球权判定、赛事方案等）。
> **格式**：每条写 **现象 / 触发条件 / 定位方法 / 规避方法 / 出处**。
> **规则**：每一条都带 `文件:行号`；查不到出处的标 `UNVERIFIED`，不编。
>
> **出处根目录约定**（同 [`hardware.md`](hardware.md)）：
>
> | 缩写 | 实际路径 |
> |---|---|
> | `CLAUDE.md:N` | `D:\FPGA\oi\CLAUDE.md:N` |
> | `README.md:N` | `D:\FPGA\oi\README.md:N` |
> | `ref/README.md:N` | `D:\FPGA\oi\ref\README.md:N` |
> | `ai/sessions/X.md:N` | `D:\FPGA\oi\ai\sessions\X.md:N` |
> | `ai/raw/X.md:N` | `D:\FPGA\oi\ai\raw\X.md:N` |
>
> 三类源文件（`CLAUDE.md` / `README.md` / `ai/**`）均位于 `D:\FPGA\oi` 下，**只读**。

> **引注范围声明（重要，避免误读为"无出处"）**：
> 每条坑的**出处统一集中在该条末尾的「**出处**」小节**（这是 task-5 指定的五段式格式：
> 现象 / 触发条件 / 定位方法 / 规避方法 / 出处）。
> 因此「现象 / 触发条件 / 定位方法 / 规避方法」四段**不再逐句重复引注**；
> **凡这四段中出现、而该条末尾「出处」小节未覆盖的事实，一律视为 UNVERIFIED** ——
> 撰写报告时不要把未覆盖的数字或结论写进去。
> 本文件当前**没有**任何一处落在上述"未覆盖"情形（逐条自查确认）。

---

## 索引

> 本表**仅为速查**（一眼看出有哪些坑、怎么规避）；**每条的具体 `文件:行号` 出处见下方对应小节的「出处」段**。
> 索引表本身不重复引注，**不作为引用来源**。

| # | 坑（一句话） | 一句话规避 | 严重度 |
|---|---|---|---|
| P1 | Zynq 的 JTAG 链上**有两个器件**，`[lindex [get_hw_devices] 0]` 拿到的是 PS 的 `arm_dap_0` | **按 PART 名**挑 `xc7z020`，不要用索引 | 🔴 阻塞 |
| P2 | `.bat` 写成 UTF-8 带中文 ⇒ cmd 按 GBK 解码后**整片报错** | `.bat` 保持**纯 ASCII**（+ CRLF） | 🔴 阻塞 |
| P3 | Vivado 内部报错**仍可能 exit 0** ⇒ 退出码不可信 | 判据用日志令牌 **`DONE_OK` / `PROGRAM_OK`** | 🔴 假绿 |
| P4 | 构建脚本**硬编码绝对路径** ⇒ 换克隆路径即失效 | 用 **`[info script]`** 推导路径 | 🟠 可移植性 |
| P5 | 底板 **V3.7 ≠ V3.9**，部分 xdc **同名不同内容** | 用 V3.7 那份，并**用 md5 双指纹秒判** | 🔴 接错引脚 |
| P6 | `.ps1` 写中文、且无 BOM ⇒ PowerShell 5.1 按 GBK 读，**引号被吃掉 → 脚本挂住** | `.ps1` 存成 **UTF-8 with BOM**，或全 ASCII | 🟠 挂死 |
| P7 | Windows 控制台默认 **GBK**，输出 `✅` 等字符会出错 | 脚本输出只用 ASCII 标记 | 🟡 |
| P8 | 厂商例程 `pin.xdc` 引脚表**与本队板卡零重合**，撞号处更危险 | **永远用本队 V3.7 总表**核引脚 | 🔴 静默错位 |
| P9 | `.xdc` 是 **GBK 编码**，直接读中文注释乱码 | 先 `iconv -f GBK -t UTF-8` | 🟡 |
| P10 | 把「某个读取工具失败」当成「PDF 读不了」 | 文本提取用 `pdftotext`，与渲染无关 | 🟡 误判能力 |

---

## P1 🔴 Zynq 的 JTAG 链上有两个器件：索引 0 是 `arm_dap_0`，不是可编程目标

**现象**
Tcl 里用 `[lindex [get_hw_devices] 0]` 选器件去下载 bitstream，报：

```
ERROR: [Labtoolstcl 44-10] Device arm_dap_0 is not programmable
```

**触发条件**
- 目标器件是 **Zynq-7000 SoC**（PL + PS 在同一 JTAG 链上）。
- 用**索引**（`[lindex ... 0]`）而不是**PART 名**去挑器件。
- 调试链枚举顺序：`arm_dap_0`（PS 的 ARM 调试访问口）在**第 0 位**，`xc7z020_1`（PL 的 TAP）在后。

**定位方法**
把设备链**带属性打出来**（诊断式复跑），一眼就能看出两个器件与各自 `PART`：

```
arm_dap_0  | NAME=arm_dap_0  | PART=arm_dap
xc7z020_1  | NAME=xc7z020_1  | PART=xc7z020
```

**规避方法**
- **按 PART 名挑**：判据是 `PART` 里匹配 `xc7z020` 的那个器件，**不要用固定索引**。
- 下载成功时日志里应看到选中 `xc7z020_1`（而不是 `arm_dap_0`）+ `PROGRAM_OK`。

**出处**
- `ai/raw/2026-09-20_ed107899.md:2703`（汇总表原文：`Device arm_dap_0 is not programmable` / 「Zynq 的 JTAG 链上有 **两个**器件，`[lindex [get_hw_devices] 0]` 拿到的是 PS 的 ARM 调试口。**必须按 PART 名挑 `xc7z020`**」）
- `ai/raw/2026-09-20_ed107899.md:2767`（同一结论的条目式复述）
- `ai/raw/2026-09-20_ed107899.md:2626`（「JTAG 连上了，但**选错了器件**」）
- `ai/raw/2026-09-20_ed107899.md:2633`（`设备链: arm_dap_0 (PS 调试口) + xc7z020_1 (PL) ← 上一版选错了前者`）
- `ai/raw/2026-09-20_ed107899.md:3025`（修复后：`PROGRAM_OK`，器件选中 `xc7z020_1`，不是 `arm_dap_0`）
- `ai/sessions/024_first_end_to_end_bringup.md:155`（原始证据：`ERROR: [Labtoolstcl 44-10] Device arm_dap_0 is not programmable` + 上表那段设备链 dump）
- `ai/raw/2026-09-26_595a243e.md:627`（同样的报错串在另一轮复现）

> **连带坑（同一份证据里的第二条报错）**：`ERROR: [Labtools 27-188] Use refresh_hw_device command, with a valid [debug_nets.ltx] file before running this command.` —— 出处 `ai/sessions/024_first_end_to_end_bringup.md:155`。

---

## P2 🔴 `.bat` 必须是纯 ASCII（中文会被 cmd 按 GBK 解码而报错）

**现象**
`.bat` 批量脚本一跑，屏幕上一片「**不是内部或外部命令**」，连 `REM` 注释行都被当命令执行。

**触发条件**
- `.bat` 文件**以 UTF-8 保存且正文含中文**（可能还带 BOM）。
- 在 Windows `cmd.exe` 下执行。cmd 按**当前代码页（GBK/936）**解码字节 ⇒ UTF-8 的中文字节被逐字节误读，`REM` 行的剩余部分被当成命令。

**定位方法**
① 直接用 `cmd` 跑一次看报错形态（「整片不是内部或外部命令」= 典型编码症状，不是语法错）。
② 检查文件是否含非 ASCII 字节。

**规避方法**
- **`.bat` 内容保持纯 ASCII**（注释也用英文/ASCII 符号）。
- 行尾用 **CRLF**（`LF` 会让 `cmd.exe` 误解析）。
- 路径写**本机真实盘符**（本机 Xilinx 在 `D:`，不是 `C:`）。
- 校验（纯 ASCII 检）：
  ```
  LC_ALL=C grep -qP '[\x80-\xFF]' <file>.bat && echo "含非 ASCII 字节" || echo "纯 ASCII"
  ```
  出处：`ai/raw/2026-10-11_73639fb1.md:1190`

**出处**
- `ai/raw/2026-10-10_46acd8b3.md:3825`（「**找到重试脚本的 bug，而且是我自己犯的**：`.bat` 写成了 **UTF-8 带中文**，cmd 按 GBK 解码后把 `REM` 行当命令执行 ⇒ 一片"不是内部或外部命令"」）
- `ai/raw/2026-10-10_46acd8b3.md:3827`（「我在写 `run_view.bat` 时特意用过「纯 ASCII .bat」这条纪律，这次却违反了。重写成纯 ASCII」）
- `ai/raw/2026-10-10_46acd8b3.md:3902`（同一事在交接表里再次记录）
- `ai/raw/2026-10-10_46acd8b3.md:1304`（正向做法：「现在写一键脚本。**纯 ASCII 内容**——避免中文 .bat 在不同控制台代码页下的乱码/BOM 问题」）
- `ai/raw/2026-10-10_73639fb1.md:236`（「`build_retry.bat` 现在是纯 ASCII + CRLF + 正确 D: 路径」）
- `ai/raw/2026-10-10_73639fb1.md:267`（**同一条 .bat 上的三个真 bug**：「路径写 `C:\Xilinx` 而本机在 D:、LF 行尾让 cmd.exe 误解析、重试触发词太窄漏了 `slave interpreter`」）
- `ai/raw/2026-10-11_73639fb1.md:1190`（纯 ASCII 检的原始命令）

---

## P3 🔴 Vivado 退出码不可信：内部报错也可能 `exit 0` ⇒ 判据改用日志令牌

**现象**
构建脚本内部 `error` 中止了，**shell 仍报退出码 0**。若 CI / 脚本只看 `$?`，就会把**失败当成功**。

**触发条件**
- 用 `vivado -mode batch -source xxx.tcl` 这类"批处理跑 Tcl"的方式驱动 Vivado。
- 用**进程退出码**做唯一的成功判据。

**定位方法**
不要看退出码，**在日志里找令牌**：
- 造设计成功 ⇒ 日志末行出现 **`DONE_OK`**
- 下载到板子成功 ⇒ 出现 **`PROGRAM_OK`**

**规避方法**
- 判据脚本写成「grep 到 `DONE_OK` / `PROGRAM_OK` 才算通过」，**退出码只作辅助信息**。
- 旧项目 README 里的三步命令本身就是这么标的（`# 成功判据：日志末行 DONE_OK（不是退出码）`）。

**出处**
- `README.md:160`（`# ① 造设计 —— 成功判据：日志末行 DONE_OK（不是退出码）`）
- `README.md:163`（`# ② 下载到板子 —— 成功判据：PROGRAM_OK`）
- `README.md:206-207`（「① 与 ② 都**只看日志里的 `DONE_OK` / `PROGRAM_OK`** —— 构建脚本 `error` 中止时 shell 仍可能报退出码 0（**Session 044/051 的坑**）」）
- 令牌在真实日志中的出现：`ai/raw/2026-09-20_ed107899.md:6050`（`grep -c "DONE_OK" .../build.log`）、`ai/raw/2026-09-20_ed107899.md:6110`（「**`DONE_OK`** —— 端到端跑通了」）
- 同一纪律在报告里也复述为「判据用日志令牌」：`README.md:206`

> ⚠️ **不是 UNVERIFIED**：这是旧项目**实测踩过并写进 README 的纪律**，出处见上。

---

## P4 🟠 构建脚本硬编码绝对路径 ⇒ 换克隆路径即失效

**现象**
脚本在**原来的盘符/目录**下能跑，别人 clone 到别的路径就报「文件找不到」。

**触发条件**
Tcl 构建脚本里**写死** `d:/FPGA/oi/...` 的绝对路径（工程目录、xdc、输出 rpt 等）。

**定位方法**
在 `project/build/` 下 grep 绝对路径：
```
grep -rn 'd:/FPGA/oi' project/build/*/*.tcl
```

**规避方法**
- 用 **`[info script]`** 推导脚本自身位置，再相对推导工程目录 / xdc / 输出目录。
- 旧项目 README 明确点名：**现有 5 个 Tcl 硬编码了绝对路径** ——
  `make_prj` / `impl` / `impl_dbg` / `impl_diag` / `ila_capture`；
  而**另外两条链**（`ov5640_lcd/`、`mt9v034_udp/`）用 `[info script]` 推导，**可移植**。
- 新仓库的构建脚本**从 `mt9v034_udp/build.tcl` 的写法抄**，不要照抄 `dual_cam/`。

**出处**
- `README.md:196-198`（原文：「现有 5 个 Tcl 里 `make_prj/impl/impl_dbg/impl_diag/ila_capture` **硬编码了绝对路径** `d:/FPGA/oi/...` ⇒ 换克隆路径即失效（另两条链用 `[info script]` 推导，可移植）。**新顶层 `field_judge` 的构建脚本从 `mt9v034_udp/build.tcl` 的写法抄**，不要照抄 `dual_cam/`」）

---

## P5 🔴 底板版本混用陷阱：V3.7 ≠ V3.9（音频 WM8960 vs ES8388、I2S 引脚不同）

**现象**
从资料盘随手拿一份 `NAVIGATOR_ZYNQ_IO.xdc` 就约束引脚 —— 文件**能编译过**，但**上板接错引脚**；音频 IP 核**不能直接互用**。

**触发条件**
- 资料盘里**同时存在** V3.7 与 V3.9 两套领航者资料，**部分文件名完全相同、内容不同**。
- 只按文件名认资料，不做版本指纹核对。
- ⛔ 尤其危险：`D:\FPGA\NAVIGATOR_ZYNQ_IO.xdc`（资料盘**根目录**那份）是 **V3.9** 版。

**定位方法**
**用 md5 双指纹秒判**，不必逐行看（两种指纹不可混用，因为 `.xdc` 是 GBK）：

| 指纹类型 | V3.7（本队） | V3.9（勿用） | 复现命令 |
|---|---|---|---|
| **原始字节** md5 | `52ab7c6527896c9276e35becc8f2a419` | — | `md5sum 文件` |
| **转 UTF-8 后** md5 | `1a8959ba581f2d400c24e143756ce408`（157 行） | `d1a27dd259e2bf9c6368a4e5197efec0`（157 行） | `iconv -f GBK -t UTF-8 文件 \| md5sum` |

**实测差异只有两处**（`diff` 逐行比对，各 157 行，其余 150+ 行完全一致）：

| 位置 | V3.7 | V3.9 |
|---|---|---|
| LCD / 触摸（**引脚号相同，仅端口名不同**） | `lcd_scl` `lcd_sda` `ct_rst` `ct_int` | `touch_scl` `touch_sda` `touch_rst_n` `touch_int`（多 `lcd_rst_n` @ L17） |
| 音频 I2S（**6 根 vs 5 根**） | `aud_mclk`E19 / `aud_bclk`M18 / `aud_dacdat`G17 / `aud_adcdat`M17；LRC **两条**：`aud_dac_lrc`@**G18** + `aud_adc_lrc`@**L20** | 同上四根；LRC **一条**：`aud_lrc`@**L20**；**G18 完全未分配** |

**规避方法**
- 板卡是 **V3.7 底板 + ZYNQ_CORE_2V5 核心板**（`CLAUDE.md:221`）⇒ 一律用 V3.7 那套资料。
- 精确表述是「**G18 在 V3.9 中被释放**」，不是「两条合成一条」；WM8960 需要 DAC/ADC 独立帧时钟，ES8388 共用一条 ⇒ **两版音频 IP 核不能直接互用**。
- 边界：**xdc 只覆盖 PL 侧引脚**，PS 侧配置不在其中 ⇒ 最终结论必须由**实际烧卡启动**验证。

**出处**
- `CLAUDE.md:219`（节标题「★ 板卡版本差异（V3.7 vs V3.9，已用 xdc 逐行 diff 核实）」）
- `CLAUDE.md:221-222`（V3.7 底板 + ZYNQ_CORE_2V5；「两套的部分文件名相同（如 `NAVIGATOR_ZYNQ_IO.xdc`）但内容不同，**拿错会接错引脚**」）
- `CLAUDE.md:224-231`（版本对照表 + 双指纹）
- `CLAUDE.md:233-234`（⛔ 根目录那份是 V3.9，必须换）
- `CLAUDE.md:236`、`CLAUDE.md:240-241`（差异只有两处 + 具体引脚）
- `CLAUDE.md:243-245`（G18 被释放的准确表述 + IP 核不能互用）
- `CLAUDE.md:250`（xdc 边界：PS 侧不在其中）
- `ref/README.md:134-167`（同结论的另一份长文；`ref/README.md:159-160` 双指纹；`ref/README.md:165-167` 差异两处）
- `ref/README.md:149-150`（⛔ 资料盘根目录那份是 V3.9）

---

## P6 🟠 `.ps1` 写中文且无 BOM ⇒ PowerShell 5.1 按 GBK 读，引号被吃掉 → 脚本挂住

**现象**
PowerShell 脚本**不报错也不退出**，像卡死在等输入。

**触发条件**
- 本机 `pwsh` 工具实际跑的是 **Windows PowerShell 5.1**（不是 pwsh 7）。
- `.ps1` 文件**无 BOM** 且正文含中文 ⇒ PS 5.1 按 **GBK/ANSI** 解码 ⇒ 中文字节把**引号吃掉** ⇒ 语法不完整 ⇒ 一直等输入。

**定位方法**
- 症状是「**挂住**」而不是「报语法错」——这一点本身就指向编码。
- 检查文件是否有 BOM（UTF-8 BOM 前三字节 `239,187,191`）。

**规避方法**
- `.ps1` 存成 **UTF-8 with BOM**（`New-Object System.Text.UTF8Encoding($true)`），或**全 ASCII**。
- 同理：脚本里**不要用中文做正则/字符范围**（命令行传递时编码可能被改写 ⇒ 测出假结果）。

**出处**
- `ai/raw/2026-09-23_42dd3fe9.md:356`（原文：「找到卡住的原因了：**`.ps1` 里写了中文**——Windows PowerShell 5.1 无 BOM 时按 GBK 读脚本，中文把引号吃掉 → 语法不完整 → PowerShell 一直等输入 ⇒ **挂住**。改成纯 ASCII」）
- `ai/raw/2026-09-11_10e145a7.md:361`（同源教训：「刚才那段统计脚本里**直接写了中文字符做正则范围**，而命令行经 Windows 传递时编码可能被改写 —— 那 0 是**我测错了**，不是 PDF 读不了。用纯 ASCII 的码点区间重新测」）

---

## P7 🟡 Windows 控制台默认 GBK：输出 `✅` 等字符会出错

**现象**
脚本本身逻辑正确，但一打印 `✅` / `❌` 之类字符就报错或乱码。

**触发条件**
Windows 控制台默认代码页为 GBK，无法输出这些字符。

**定位方法**
把输出字符与报错位置对上，就能确认是输出编码而非逻辑问题。

**规避方法**
脚本的**输出标记只用 ASCII**（例如 `OK` / `FAIL` / `DONE_OK`）。

**出处**
- `ai/raw/2026-09-10_f9dbaef7.md:2091`（原文：「发现一个真实问题：Windows 控制台默认 GBK 编码，无法输出 `✅` 等字符。修复它。」）
- 同族纪律：`CLAUDE.md` 判据令牌 `DONE_OK` / `PROGRAM_OK` 本身就是纯 ASCII（`README.md:160`、`README.md:163`）

---

## P8 🔴 厂商例程 `pin.xdc` 引脚表与本队板卡**零重合**，撞号处更危险

**现象**
照抄厂商例程的引脚约束，**不报错**，但上板后图像/显示**错位**。

**触发条件**
- 厂商例程是给**「达芬奇」开发板**跑的，教程原文自己就写了。
- 直接把它的 `constrs_1/new/pin.xdc` 搬到本队领航者板。

**定位方法**
逐信号比对厂商表 vs 本队 V3.7 总表。实测两套**引脚号零重合**：

| 信号 | 厂商 `pin.xdc`（达芬奇） | 本队总表（V3.7） |
|---|---|---|
| `sys_clk` | **R4** | **U18** |
| `sys_rst_n` | **U2** | **N16** |
| `pclk` | `cmos_pclk` **F14** | `cam_pclk` **W14** |
| `vsync` / `href` | `cmos_vsync` E16 / `cmos_href` B15 | `cam_vsync` U12 / `cam_href` T12 |
| `data[0]`…`data[7]` | C14, B13, C13, D15, D14, E14, E13, F13 | R14, U13, V13, U15, U14, W13, V12, Y14 |
| SCCB `scl`/`sda` | D16 / B16 | T10 / T11（经 EMIO） |
| `lcd_rgb[0]` | **R16** | **W18** |

**规避方法**
- ⛔ **唯一撞号的一处最危险**：`R16` 在厂商表里是 `lcd_rgb[0]`，在本队总表里是 `lcd_rgb[2]` ——
  **同一个引脚号、不同信号**。照抄厂商表**不会报错**，只会在板上错位。
- **永远以本队 V3.7 总表为准**：`ref/board/board_pinout_navigator_zynq_io_v3.7.xdc`。
- 接口形态可借鉴、**代码不能直接搬**（厂商前端是 DVP 式 `cmos_frame_*`，不是 AXI4-Stream）。

**出处**
- `ref/README.md:109-111`（厂商例程是给达芬奇板跑的；教程原文引用）
- `ref/README.md:112-113`（实测两套引脚号零重合，比对对象写明）
- `ref/README.md:115-123`（上表的逐行出处）
- `ref/README.md:125-126`（⛔ `R16` 撞号）
- `ref/README.md:128-130`（接口形态可借鉴、代码不能直接搬）

---

## P9 🟡 `.xdc` 是 GBK 编码，直接读中文注释会乱码

**现象**
打开 `.xdc` 看到注释乱码，误以为文件坏了。

**触发条件**
`.xdc` 文件为 **GBK 编码 + CRLF**（旧项目 `.gitattributes:8` 明写这条）。

**规避方法**
先转码再读：
```
iconv -f GBK -t UTF-8 ref/board/board_pinout_navigator_zynq_io_v3.7.xdc
```
> ⚠️ 与 P5 关联：**md5 比对时两边都要走 `iconv`**，否则会得到"内容不同"的**假结论**。

**出处**
- `ref/README.md:271-275`（`.xdc` 是 GBK 编码 + 转码命令）
- `ref/README.md:162-163`（「两种指纹不可混用：本文件的 `.xdc` 是 GBK……与资料盘比对时，**两边都要走 `iconv`**，否则会得到"内容不同"的假结论」）

---

## P10 🟡 「某个读取工具失败」≠「PDF 读不了」：文本提取与渲染是两回事

**现象**
PDF 在某个工具里打不开/渲染失败，于是下结论「这份 PDF 读不了」，绕道去找别的资料。

**触发条件**
编辑器的 PDF **渲染**依赖 `pdftoppm`（可能未装）；而**文本提取**依赖 `pdftotext`，两条路径互不相干。

**定位方法**
直接用 `pdftotext` 抽文本，成功即说明"读得了"：
```
pdftotext -enc UTF-8 ref/guide/guide_amd_2026.pdf -
pdftotext -enc UTF-8 -f 24 -l 26 ref/guide/guide_amd_2026.pdf -   # 只抽某几页
```

**规避方法**
把「渲染能力」与「文本提取能力」分开判断，**不要因为一个工具失败就否定整类能力**。

**出处**
- `ref/README.md:252-269`（`pdftotext` 用法 + 「⚠️ 不要因为某个读取工具失败就断定「PDF 读不了」。编辑器的 PDF 渲染依赖 `pdftoppm`（未装），但这与**文本提取**是两回事」）
- 该教训的原始会话：`ai/sessions/002_environment_capability_misjudgment.md`（由 `ref/README.md:269` 指向）

---

## 附：本清单**未使用**的旧项目内容（有意排除）

以下旧项目内容**不属"可复用的坑"**，故未抄入（避免把旧项目的业务结论带进新仓库）：

- 球权判定 / 出界判据 / 手球降级等**业务规则**；
- 具体赛事方案与机位前提的历次改版（`DECISIONS.md` 的 D001/D004/D008-D013 等）；
- 各次上板的具体回传包数、CRC 统计等**一次性观测数据**。

若日后需要，可直接到 `D:\FPGA\oi` 对应文件查（**只读**），
或参考本目录 [`hardware.md`](hardware.md) §L 的引用索引。
