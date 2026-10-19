# AMD 官方布局与版本控制建议（查证记录）

> **本文件回答一个问题**：用户要求的「按**官方推荐的架构**」到底有几层官方来源？
> 结论：**两层，不冲突**——
>
> | 层级 | 官方来源 | 管什么 | 落到本仓库哪里 |
> |---|---|---|---|
> | **仓库顶层**（提交物分类） | 竞赛指南 3.3.5.4「推荐目录结构」 | `src/ sim/ build/ board/ data/ skill/ report/` 八个顶层目录 | [`repo_spec.md`](repo_spec.md) §2（**唯一裁决**） |
> | **工程内部**（哪些文件受版本控制） | AMD UG949 / AMD 官方 Revision-Control 教程 | Vivado 工程里什么是**源**、什么是**产物** | 本文件 §3 + 根 [`.gitignore`](../.gitignore) |
>
> ⚠️ **优先级**：竞赛提交口径以指南 3.3.5.4 为准；AMD 建议只在「指南没有规定的地方」补位。
> 本文件**不改变** [`repo_spec.md`](repo_spec.md) 的任何目录职责。

---

## 0. 为什么需要这份文件

旧项目 `D:\FPGA\oi` 存在一个**被掩盖的缺口**：UG949 在整个旧仓库里只作为「引用位」出现（如 `CLAUDE.md:101`），
**从未真正按其内容组织过任何目录**；`srcs/sources_1`、`bd` 只作为 Vivado 生成产物存在。
也就是说，旧仓库从未回答「UG949 说要管哪些源文件，我们是怎么管的」。

本文件把这个缺口显式补上，并**留下查证方法与版本陷阱**，供 `report/` 的「复现说明」一节引用。

---

## 1. 查证方法与其局限（★ 必须随结论一起引用）

| 事实 | 影响 |
|---|---|
| **`docs.amd.com` 是 JavaScript SPA**，任何抓取路径（含 fetch）都读不到正文 | 不能把「读不到」当成「AMD 没有这条建议」；须改用 AMD 静态 PDF |
| UG949 正文是从 AMD **静态 PDF of UG949 v2015.3** 提取的：`https://www.xilinx.com/support/documentation/sw_manuals/xilinx2015_3/ug949-vivado-design-methodology.pdf`（HTTP 200）；当前修订版入口：`https://docs.amd.com/r/en-US/ug949-vivado-design-methodology` | 引文版本 = **v2015.3**，不是 2025.x |
| AMD 官方教程仓库是 **2025.2**：`https://github.com/Xilinx/Vivado-Design-Tutorials/tree/2025.2/General/Revision_Control` | 与 UG949 之间的不一致是**真实存在的**（见 §2 冲突点），不是抄错 |

> **纪律**：本文件每条结论都标了出处与版本；引用时**必须连版本一起引**，否则会误导评审。

---

## 2. AMD 的两套说法（存在真实冲突，不可只引一套）

### 2.1 UG949「Recommended Source Files to Manage」（v2015.3 PDF，逐字要点）

受版本控制的源文件应包含：

| 类别 | 逐字要点 |
|---|---|
| Scripts | run scripts（含 pre/post script）、修改过的 `init.tcl`、`write_project_tcl` 的输出、`.xpr` |
| RTL | 全部 RTL 源码 + testbench/stimulus（含 `.include` 文件） |
| 约束 | 全部 XDC + 约束 Tcl |
| IP | 「all IP directories and output product files **with names intact**」；或 `.xcix` 容器 + `ip_user_files/ip_static_files` + `.coe/.csv/.bmm/.elf` |
| IP Integrator（BD） | 「the **entire** block design location with all of the IP subdirectories, files, and names intact」 |
| HLS | C 源码 + packaged IP + 脚本 |
| System Generator | 整个 module 目录原样保留 |

**最小集警告（逐字）**：IP/BD 的输出产物必须用**同一工具版本**重新生成才能得到一致的副本，且重新生成**耗时显著**。

### 2.2 AMD 官方 Revision-Control 教程列出的方法论（2025.2）

页面正文自称 "five alternative methodologies"，但实际列出 **6 个名字**（原文如此，照录不修正）：

`Foundational` / `Check_In_Generated_Outputs` / `User_Managed_vs_Tool_Managed` / `Manage_IP` / `Package_IP_Outputs_in_One_File` / `IP_Cache_Compile_Time_Speedup`

其中与本仓库相关的是前两个：

- **Foundational（逐字）**：「The recommended flow to use the `write_bd_tcl` and `write_ip_tcl` scripts since they can be sourced from any location, work for project and non-project flows, are a single file...
  **Try to avoid revision control of only the .BD and .XCI files, as these files contain hard coded relative paths for the .gen directory location.**」
- **Check_In_Generated_Outputs**：提交 `<proj>.srcs` + `<proj>.gen` + `<proj>.xpr`（`.xpr` 仅在「用户需要在这个单工程上反复迭代」时才要求入库）；
  顶层用 `add_files`（project 模式）或 `read_bd`/`read_ip`（non-project 模式）重新挂回 BD/IP。
  **官方自陈代价（逐字）**：「Higher number of files and folders in revision control」+「The .srcs and the .gen folder can become out of sync」。

### 2.3 ★ 冲突点（必须显式登记，不能只挑一套引）

| 冲突 | UG949 (v2015.3) | AMD 教程 (2025.2) |
|---|---|---|
| BD / IP 怎么入库 | 整个 BD 目录 + 全部 IP 子目录与产物「names intact」 | **不要**只提交 `.bd`/`.xci`（内含指向 `.gen` 的**硬编码相对路径**）；要么提交 `bd.tcl`/`ip.tcl`（Foundational），要么提交 `.srcs`+`.gen`+`.xpr` |

> 官方**同时**给出两种做法并各自写明优缺点 ⇒ 本仓库必须**明确选一种并写清理由**（见 §3.2），
> 而不是含糊地「照 UG949 办」。

### 2.4 UNVERIFIED（来自一般实践，**AMD 文档中未找到依据**）

| 项目 | 状态 |
|---|---|
| 把 `constrs_1` / `sim_1` / `sources_1` 当作**仓库布局** | ❌ **UNVERIFIED** —— 这三个是 Vivado **project 模式** `<proj>.srcs/` 里的 **fileset 目录名**，没有任何 AMD 文档推荐把它们当仓库目录结构 |
| Vitis-AI 的 `quantize/compile/host/kernel` 四段切分 | ❌ **UNVERIFIED** —— Vitis-AI 3.x 实际用 `files/compile/build/run`，且 `arch` 放在 platform 下；1.4 与 3.5 之间布局有变 |

---

## 3. 本仓库的落点（把官方建议映射到实际目录）

### 3.1 映射表

| 官方要求 | 本仓库落点 | 说明 |
|---|---|---|
| RTL + testbench/stimulus | `src/rtl/**`、`sim/tb/**` | `sim/` **镜像** `src/rtl/` 子目录；`x.v` → `x_tb.v`（见 [`repo_spec.md`](repo_spec.md) §2.2） |
| 约束 XDC / 约束 Tcl | `src/rtl/io/`（引脚约束随子系统） | **`.xdc` 不入 `.gitignore`**（是源，不是产物） |
| HLS C 源码 + 脚本 | `src/hls/accel/{src,include,tb,scripts}/` | HLS 产物 `solution*/`、`*.apcc` 已忽略 |
| Scripts（run scripts、`write_project_tcl` 输出） | `build/scripts/**` | `build/scripts/` = **构建真源**；Vivado 工程由脚本重建 |
| IP | `src/rtl/ip/`（`.xci` 只读） | `.xci`/`.xdc` **入库**（是源） |
| 构建产物报告 | `build/reports/{synth,impl,timing,resource,drc}/` | 只保留**最新 + `baseline*`**（见 §3.2） |
| PYNQ Overlay 交付件 | `board/overlay/*.bit` + `*.hwh` | **故意不忽略**（见 §3.3） |
| PS 侧软件 | `src/ps/{pynq_driver,host}/` | — |

### 3.2 ★ 本仓库的版本控制方法论 = **Foundational 派**

根 [`.gitignore`](../.gitignore) 已经隐含了这个选择，此处把它**显式写成决策**：

| 忽略项 | 含义 |
|---|---|
| `*.cache/`、`*.runs/`、`*.hw/`、`*.gen/`、`*.ip_user_files/`、`*.dcp`、`*.sysdef`、`NA/` | Vivado 工程的**生成目录**全部不入库 ⇒ 即「**由 Tcl 脚本重建工程**」 |
| `*.jou`、`*.log`、`*.str`、`.Xil/`、`webtalk*` | 运行期噪声 |

**理由**（若将来需改为 Check_In_Generated_Outputs，须在此处登记并改 `.gitignore`）：
1. 与 **PYNQ 官方模板**一致（§4）——PYNQ 上游明确忽略一切「由 `.tcl` 源重建」的 Vivado 工程目录；本仓库以 PYNQ 为主框架（指南 3.3.2 初级组推荐）。
2. UG949 自己承认：IP/BD 产物**必须用同一工具版本**才能一致重建；把 `.gen/` 入库会把这一脆弱性带进仓库，且 AMD 自陈 `.srcs` 与 `.gen` 会 **out of sync**。
3. 竞赛要求是「**可复现的构建脚本**」（指南 3.3.5.1 ⑤），脚本才是被考察的交付物。
4. **代价必须写进报告**：评审若在**无网/无法重建 IP** 的环境下验收，需按 §2.2 的 Check_In_Generated_Outputs 方案另行提供 `.srcs`+`.gen`+`.xpr` 打包件。**此代价暂登记为待办，见 §5。**

> ⚠️ **本仓库尚未产生任何 BD**。一旦引入 Block Design，**必须**同时提交 `write_bd_tcl` 产出的 `*.tcl`；
> 仅提交 `.bd` 属 UG949 与 AMD 教程**共同反对**的做法。

### 3.3 与 PYNQ 官方模板的**唯一一处故意偏离**

PYNQ 上游 `.gitignore` 忽略 `*.bit *.hwh *.xsa *.pdi *.dcp *.ncr`（「Raw overlay files」）。
**本仓库故意不忽略 `*.bit` 与 `*.hwh`**：

- 指南 3.3.5.1 ④ 要求提交「**上板工程、运行脚本、实测输出**」；④ 的交付物就是 Overlay 本身。
- 指南 3.3.5.1 ① 要求「完整工程文件」。
- 根 `.gitignore:57` 已写死理由注释：「`*.bit` 与 `*.hwh` 是交付件，**故意不在此列**」。

**风险登记**：`.bit` 是由 `build/scripts/` 重建的产物，入库违反「真源 vs 产物」纪律（[`repo_spec.md`](repo_spec.md) §4）。
**取舍**：竞赛评审需要能直接加载的 Overlay ⇒ 交付必要性 > 纯粹性。**若 `.bit` 体积成为问题，改用 Git LFS 而非删除。**

---

## 4. PYNQ 官方模板（逐字，作为本仓库 `.gitignore`/`.gitattributes` 的对标基准）

- `.gitignore`：`https://github.com/Xilinx/PYNQ/blob/master/.gitignore`
  忽略：`*.log`、`*.jou`、`*.vitis_hls_log*.xml`；「Raw overlay files」(`*.bit *.hwh *.xsa *.pdi *.dcp *.ncr`)；
  「Vivado project directories, regenerated from the .tcl sources」（如 `boards/*/base/base/`）；`.Xil/`、`*.pb`、`hd_visual/`、`build/`、`__pycache__/`、`*.so`、`GNUmakefile`、`user.props`。
- `.gitattributes`：`https://github.com/Xilinx/PYNQ/blob/master/.gitattributes`
  `* text eol=lf`；CRLF for `*.vcxproj/*.props/*.project/*.cproject/*.inf`；binary for `images/*.mp4`；
  `*.wav binary -crlf`、`*.elf binary -crlf`、`*.bin binary -crlf`、`*.bit binary -crlf`、`*.a binary -crlf`、`*.bsp binary`；`pynq/__init__.py ident`。
- PYNQ 板级 base overlay 布局（`boards/ZCU104/base/`）：`base.tcl`、`build_bitstream.tcl`、`build_ip.tcl`、`check_timing.tcl`、`makefile`、`base.py`、`__init__.py`、`notebooks/`、`vivado/`
  ⇒ **提交 Tcl，不提交生成的 Vivado 工程**。这正是 §3.2 所选 Foundational 派的上游依据。
- Overlay 交付形态 = `.bit` + `.hwh`（上游两者都被 gitignore）。
- 纯 Overlay 示例仓库：`https://github.com/Xilinx/PYNQ-ComputerVision` → `overlays/{README.md, buildUnitOverlays.py, <overlay dir>}`。
- 文档：`https://pynq.readthedocs.io/en/latest/pynq_overlays.html`

---

## 5. 待办（本文件产生的、尚未关闭的事项）

| # | 事项 | 触发条件 | 状态 |
|---|---|---|---|
| A1 | 一旦引入 Block Design，须提交 `write_bd_tcl` 产出的 `*.tcl` | 首次建 BD 时 | ⬜ 未发生（本仓库尚无 BD） |
| A2 | 登记「评审需离线验收」时的 `.srcs`+`.gen`+`.xpr` 打包件方案 | 若报告需声明离线可重建 | ⬜ 待用户裁定 |
| A3 | 若 `.bit` 体积过大，改 Git LFS（**不是**删除） | 首次提交 `.bit` 时 | ⬜ 未发生 |
| A4 | 引用本文件任何结论时**必须连版本一起引**（UG949 v2015.3 / 教程 2025.2） | 写 `report/` 时 | ⬜ 纪律，长期有效 |

---

## 6. 相关文档

| 想知道 | 去哪 |
|---|---|
| 仓库顶层目录职责（**唯一裁决**） | [`repo_spec.md`](repo_spec.md) §2 |
| 哪些文件不该进 git、为什么 | 根 [`.gitignore`](../.gitignore)（每块都带理由注释）+ 本文件 §3.2 |
| 系统架构（软硬件划分、接口契约） | [`architecture.md`](architecture.md)（骨架，待用户裁定题目） |
| 硬件与工具链硬约束 | [`repo_spec.md`](repo_spec.md) §6 + [`../ref/hardware.md`](../ref/hardware.md) |
| 决策状态（哪些已定、哪些在讨论） | [`../ai/coordination/DECISIONS.md`](../ai/coordination/DECISIONS.md) |
