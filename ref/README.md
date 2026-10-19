# ref/ —— 外部文献与技术支持（`D:\zynq-yolo` 版本）

> **本目录是外部文献，不是工程本体。**
> 内容全部是**第三方版权材料**（AMD 赛题指南、正点原子原理图 / 手册 / 教程、OmniVision / onsemi / 芯片原厂 datasheet）。
> **本目录不进 git**（`.gitignore` 已排除 `ref/`），**只作查证、不作提交**。
>
> 来源：`D:\FPGA\oi\ref\`（旧项目，**只读**）+ `D:\zynq-yolo\.research\guide_amd_2026.txt`（本仓库已抽取的指南纯文本）。
> 复制清单（逐文件的源路径 / 字节数 / 源 mtime）见本目录 [`MANIFEST.csv`](MANIFEST.csv)。

---

## 0. 本目录的定位（三条硬规则）

| # | 规则 | 理由 |
|---|---|---|
| 1 | **只进不改**（本目录内的 PDF / 手册 / 原理图不修改、不重写） | 它们是判据的出处；改一个字节，`file:line` 引用就失真 |
| 2 | **不进 git** | ① 二进制一旦提交不可逆（`.git` 历史永久膨胀）② 二进制无法 diff，无版本控制价值 ③ 原件都在 `D:\FPGA\A\` 资料盘，可恢复 ④ 想加回来只需注释掉一行，想删掉要改历史 ⇒ 取保守侧（承旧项目 `ref/README.md:337-344` 的四条判据） |
| 3 | **只作查证，不作提交** | 厂商版权 PDF 不宜随作品公开分发（旧项目 `ref/README.md:320-331`） |

> ⛔ **不要往本目录塞自有产出。** 判断一个文件放哪里，先看「**它参不参与提交**」，而不是「它是什么格式」（旧项目 `ref/README.md:370-373` 的教训）。

---

## 1. 目录树

```
ref/
├── README.md                 # 本文件
├── MANIFEST.csv              # 复制清单 + 只读证明：45 行，见 §2 列说明
├── hardware.md               # ★ 硬件能力清单（逐条 file:line 出处）
├── pitfalls_from_oi.md       # ★ 从旧项目继承的踩坑清单（现象/触发/定位/规避/出处）
├── guide/
│   ├── guide_amd_2026.pdf            # AMD 赛道选题指南（本队所选赛道）
│   └── guide_amd_2026.txt            # 同上，pdftotext 抽取的纯文本（便于全文检索）
├── board/                    # 领航者 ZYNQ 开发板资料（V3.7 底板）
│   ├── board_spec_alientek_leader_zynq7020_v1.2.pdf
│   ├── board_core_schematic_zynq_core_2v5.pdf
│   ├── board_base_schematic_navigator_v3.6.pdf
│   ├── board_pinout_navigator_zynq_io.xlsx
│   ├── board_pinout_navigator_zynq_io_v3.7.xdc     # ★ V3.7 版引脚约束总览
│   ├── board_datasheet_wm8960_v4.4.pdf
│   ├── guide_pynq_alientek_leader_zynq_v3.2.pdf
│   ├── guide_fpga_alientek_leader_zynq_wm8960.pdf
│   ├── guide_vitis_embedded_alientek_zynq_v1.2.pdf
│   └── entry_statement.docx                        # ⚠️ 自有产出，见 §5
├── mt9v034/                  # MT9V034 单色摄像头模块
│   ├── module_schematic_atk_mt9v034_v1.4.pdf
│   ├── datasheet_mt9v034_rev_g.pdf
│   ├── datasheet_mt9v034_register_reference_rev_a.pdf
│   ├── ds_g.txt                                    # Rev.G 抽取的纯文本
│   ├── guide_mt9v034_hdmi_display_experiment.pdf
│   └── guide_mt9v034_tftlcd_display_experiment.pdf
├── ov5640_camera_module/     # 原名【正点原子】OV5640单目摄像头模块
│   ├── ATK-MC5640_module_user_guide_V1.2.pdf
│   ├── schematic/  ATK-MC5640_V1.2.pdf · ATK-MC5640_Lib.zip
│   └── reference/  OmniVision_SCCB_Specification.pdf · OV5640-*（6 份）· ...
└── rgb_lcd_module/           # 原名【正点原子】4.3寸RGBLCD电容触摸屏模块480272、800480
    ├── ATK-MD0430R_module_user_guide_V1.0.pdf
    ├── ATK-MD0430R_module_user_manual_V1.1.pdf
    ├── schematic/  ATK-MD0430R_V1.9.pdf · ATK-MD0430R_Lib.zip
    └── reference/  GT9147/GT917S/GT1151Q 手册 · panel_480272_* · panel_800480_* · ...
```

**规模**：45 个文件，**177,237,389 字节（≈169.0 MiB）**，其中 44 个文件 / 177,178,684 字节来自 `D:\FPGA\oi`
（第 45 个是 `guide/guide_amd_2026.txt`，来自本仓库 `.research/`）。

---

## 2. ★ 只读证明：`D:\FPGA\oi` 未被修改

流程：**复制前**记录每个源文件的 `Length` + `LastWriteTime` → 复制 → 复制完成后**逐文件重读比对**
→ **再独立复验**（第三次读取）。三次快照的结论全部一致，且**证据已固化进本目录的
[`MANIFEST.csv`](MANIFEST.csv)**（不再依赖任何临时文件）：

| MANIFEST.csv 列 | 含义 |
|---|---|
| `dst_relative` | 本仓库内的相对路径 |
| `bytes` | **复制前**记录的源文件字节数 |
| `dst_bytes` | 复制后**目的文件**的实测字节数 |
| `dst_bytes_match` | 二者是否相等（45/45 = `True`） |
| `src_mtime_before` | **复制前**记录的源 `LastWriteTime`（7 位小数） |
| `src_mtime_final` | **最后复验时**重读的源 `LastWriteTime` |
| `src_unchanged` | 两次是否逐 `.Ticks` 相等（45/45 = `True`） |
| `src_absolute` | 源绝对路径（44 条在 `D:\FPGA\oi`，1 条在 `D:\zynq-yolo\.research`） |

**复验结果（逐文件比对 `.Ticks` + `Length`）**：

```
rows_checked            = 45      src_mismatch          = 0
dst_bytes_mismatch      = 0       src_unchanged_true    = 45 / 45
oi_rows                 = 44      oi_bytes              = 177178684 (168.97 MB)
```

> ✅ 结论：**`D:\FPGA\oi` 下 44 个被复制文件的 `Length` 与 `LastWriteTime` 一次都没有变**，
> 本任务对源目录**只做了读操作**（`Get-Item` / `Copy-Item -LiteralPath <src>`）。

抽样证明（`recorded` = 复制前记录，`now` = 复验时实测）：

| 源文件 | recorded | now |
|---|---|---|
| `D:\FPGA\oi\ref\guide\guide_amd_2026.pdf` | len=950952 mtime=2026-09-10 19:37:00.9089508 | len=950952 mtime=2026-09-10 19:37:00.9089508 |
| `D:\FPGA\oi\ref\board\2_【正点原子】领航者ZYNQ之嵌入式Vitis开发指南V1.2.pdf` | len=43402307 mtime=2026-04-14 10:24:12.4466740 | len=43402307 mtime=2026-04-14 10:24:12.4466740 |
| `D:\FPGA\oi\ref\board\board_base_schematic_navigator_v3.6.pdf` | len=936549 mtime=2026-09-11 11:11:59.5098195 | len=936549 mtime=2026-09-11 11:11:59.5098195 |
| `D:\FPGA\oi\ref\board\board_core_schematic_zynq_core_2v5.pdf` | len=2430251 mtime=2026-09-11 11:11:59.4547969 | len=2430251 mtime=2026-09-11 11:11:59.4547969 |

源目录自身 mtime（未变）：

| 目录 | LastWriteTime |
|---|---|
| `D:\FPGA\oi` | 2026-10-19 07:26:54 |
| `D:\FPGA\oi\ref` | 2026-09-29 19:34:42 |
| `D:\FPGA\oi\ref\board` | 2026-09-20 13:02:38 |
| `D:\FPGA\oi\ref\mt9v034` | 2026-09-26 15:32:57 |

> ⚠️ **比对必须用 `.Ticks` 或 `.ToUniversalTime()` 全精度**。
> 第一次复验我用了 `ToString('yyyy-MM-dd HH:mm:ss.fff')`（3 位小数）去比 CSV 里的 7 位小数，
> 结果**全部 45 行假报"已变更"**。这是比对方法的坑，不是源文件被动过。

---

## 3. ★ 文件名 ASCII 化对照（承指南 3.3.5.4）

指南 3.3.5.4 要求**目录名与文件名纯英文**（见 `docs/repo_spec.md:16`、`docs/repo_spec.md:32`）。
源目录里有若干**中文名目录与中文名文件**，复制时**一律重命名**为 ASCII。对照如下，供追溯。
（本表的一手依据 = 本目录 [`MANIFEST.csv`](MANIFEST.csv) 的 `dst_relative` ↔ `src_absolute` 两列，45 行逐条可核。）

### 3.1 目录重命名

| 原目录名（源） | 新目录名（本仓库） |
|---|---|
| `【正点原子】OV5640单目摄像头模块` | `ov5640_camera_module` |
| `【正点原子】OV5640单目摄像头模块资料（新资料）`（源里嵌套两层） | 直接摊平，不再多套一层 |
| `1，原理图` | `schematic` |
| `4，参考资料` | `reference` |
| `【正点原子】4.3寸RGBLCD电容触摸屏模块480272、800480` | `rgb_lcd_module` |
| `【正点原子】4.3寸RGB LCD电容触摸屏模块资料（新资料）` | 直接摊平 |
| `ATK-MD0430R-480272裸屏资料` | 摊平进 `reference/` |
| `ATK-MD0430R-800480裸屏资料` | 摊平进 `reference/` |

### 3.2 中文名 / 含空格括号的文件重命名

| # | 原文件名（源） | 新文件名（本仓库） |
|---|---|---|
| 1 | `ATK-MC5640模块使用说明_V1.2.pdf` | `ATK-MC5640_module_user_guide_V1.2.pdf` |
| 2 | `OV5640_自动对焦照相模组应用指南(DVP_接口)__R2.13C.pdf` | `OV5640_auto_focus_camera_module_application_guide_DVP_R2.13C.pdf` |
| 3 | `OV5640_Camera_Module_Hardware_Application_Notes_R1.0英文手册.pdf` | `OV5640_Camera_Module_Hardware_Application_Notes_R1.0.pdf` |
| 4 | `OmniVision Technologies Seril Camera Control Bus(SCCB) Specification.pdf` | `OmniVision_SCCB_Specification.pdf` |
| 5 | `OV5640_Auto_Focus_Camera_Module_Application_Notes_(with_MIPI_Interface)_R2.15.pdf` | `OV5640_Auto_Focus_Camera_Module_Application_Notes_with_MIPI_Interface_R2.15.pdf` |
| 6 | `ATK-MD0430R模块使用说明_V1.0.pdf` | `ATK-MD0430R_module_user_guide_V1.0.pdf` |
| 7 | `ATK-MD0430R模块用户手册_V1.1.pdf` | `ATK-MD0430R_module_user_manual_V1.1.pdf` |
| 8 | `Application Note for CTPM_FocalTech.pdf` | `Application_Note_for_CTPM_FocalTech.pdf` |
| 9 | `GT1151Q数据手册.pdf` | `GT1151Q_datasheet.pdf` |
| 10 | `GT1151Q编程指南.pdf` | `GT1151Q_programming_guide.pdf` |
| 11 | `GT9147数据手册.pdf` | `GT9147_datasheet.pdf` |
| 12 | `GT9147编程指南.pdf` | `GT9147_programming_guide.pdf` |
| 13 | `GT917S 数据手册.pdf` | `GT917S_datasheet.pdf` |
| 14 | `GT917S编程指南.pdf` | `GT917S_programming_guide.pdf` |
| 15 | `4.3寸480272裸屏规格书.pdf` | `panel_480272_spec.pdf` |
| 16 | `4.3寸480272裸屏驱动IC-ST7282.pdf` | `panel_480272_driver_ic_ST7282.pdf` |
| 17 | `4.3寸800480裸屏规格书.pdf` | `panel_800480_spec.pdf` |
| 18 | `4.3寸800480裸屏驱动IC-ST7262.pdf` | `panel_800480_driver_ic_ST7262.pdf` |
| 19 | `2_【正点原子】领航者ZYNQ之嵌入式Vitis开发指南V1.2.pdf` | `guide_vitis_embedded_alientek_zynq_v1.2.pdf` |

**结论：本目录下没有任何中文名文件被保留。** 外层目录名与文件名的非 ASCII 字符已全部消除；
校验命令：

```powershell
# 应无输出（= 全部 ASCII）
Get-ChildItem -LiteralPath D:\zynq-yolo\ref -Recurse |
  Where-Object { $_.Name -match '[^\x20-\x7E]' }
```

> 说明：**"未知"字体名/厂商内部命名**不在此列 —— 上述 19 条是**文件名**层面的重命名，
> 文件**正文内容**里的中文一律保持原样（指南只约束文件名，见 `docs/repo_spec.md:33`）。

---

## 4. ★ 底板版本陷阱：V3.7 vs V3.9（**拿错会接错引脚**）

本队板卡是 **V3.7 底板 + ZYNQ_CORE_2V5 核心板**。
资料盘里**同时存在 V3.9 版资料，且部分文件名完全相同、内容不同**（出处：`D:\FPGA\oi\CLAUDE.md:221-222`）。

| | **V3.7（本队，已归档）** | V3.9（**勿用**） |
|---|---|---|
| 音频芯片 | **WM8960** | ES8388 |
| 底板原理图 | `领航者ZYNQ底板原理图_V3.6.pdf` | `领航者ZYNQ底板原理图_V3.9.pdf` |
| 开发指南 | 三本带 **(WM8960)** 后缀 | 无后缀 |
| 专用芯片手册 | `WM8960_v4.4.pdf` | — |

出处：`D:\FPGA\oi\CLAUDE.md:224-231`。

> ⛔ **`D:\FPGA\NAVIGATOR_ZYNQ_IO.xdc`（资料盘根目录那份）是 V3.9 版**，与本队板卡不符 ——
> 出处：`D:\FPGA\oi\CLAUDE.md:233-234`、`D:\FPGA\oi\ref\README.md:149-150`。
> 必须改用本目录的 [`board/board_pinout_navigator_zynq_io_v3.7.xdc`](board/board_pinout_navigator_zynq_io_v3.7.xdc)。

**逐行 `diff` 实测（各 157 行）差异只有两处**（`D:\FPGA\oi\CLAUDE.md:236`、`D:\FPGA\oi\ref\README.md:165-167`）：

| 位置 | V3.7 | V3.9 | 出处 |
|---|---|---|---|
| LCD / 触摸（**引脚号相同，仅端口名不同**） | `lcd_scl` `lcd_sda` `ct_rst` `ct_int` | `touch_scl` `touch_sda` `touch_rst_n` `touch_int`（另多一条 `lcd_rst_n` @ L17） | `CLAUDE.md:240` |
| 音频 I2S（**6 根 vs 5 根**） | LRC **两条**：`aud_dac_lrc`@**G18** + `aud_adc_lrc`@**L20** | LRC **一条**：`aud_lrc`@**L20**；**G18 完全未分配** | `CLAUDE.md:241` |

> ⚠️ 准确表述是「**G18 在 V3.9 中被释放**」，不是「两条合成一条」——
> WM8960 需要 DAC/ADC 独立帧时钟，ES8388 共用一条 ⇒ **两版音频 IP 核不能直接互用**
> （`CLAUDE.md:243-245`）。

**对本仓库的意义**：DDR、时钟、复位、SD、以太网在 diff 中**零差异**，且两版共用同一核心板
⇒ V3.9 的 PYNQ 镜像在 V3.7 底板上大概率可直接启动（`CLAUDE.md:247-249`）。
⚠️ 但 xdc 只覆盖 **PL 侧引脚**，PS 侧配置不在其中 ⇒ 最终结论必须由**实际烧卡启动**验证（`CLAUDE.md:250`）。

---

## 5. ⚠️ `board/entry_statement.docx` —— 本目录里唯一的**自有产出**，需裁定

按 task-5 要求从旧项目 `ref/board/` 一并复制。但复制后核对正文发现：
**它是本队自己的作品陈述，不是第三方材料** ——
正文首段为「争抢场景足球出界判定与球权归属系统 / Out-of-Play Decision and Possession Attribution in Crowded Play」，
并含「全国大学生嵌入式芯片与系统设计竞赛 '2026 FPGA 创新设计赛道 · AMD 3.3 自主选题 · 初级组」、
「硬件平台：正点原子 领航者 ZYNQ（XC7Z020CLG400-2，Zynq-7000 SoC）」等**本队填写内容**。

（出处：`board/entry_statement.docx` 正文；`.docx` 无行号，故只能给到文件级。）

这与旧项目自己的规则冲突 —— 旧项目 `ref/README.md:365-373` 明写「**自有产出勿入本目录**」。

### ✅ Lead 裁定（2026-10-19）：**保持原路径不动，定位改为「旧项目陈述（参考件）」**

| 问题 | 裁定 |
|---|---|
| 是否移出 `ref/`？ | **否**。它是**旧项目**（题目：争抢场景足球出界判定与球权归属）的陈述，**不是本项目的提交物**。 |
| 是否算「提交物不该待在非 git 目录」？ | **不算**。refharvest 的这条担忧前提是「它是我们的提交物」；实际上新项目的陈述**尚未存在**，须待用户裁定题目后**重新撰写**。 |
| 为什么不改名？ | 改名会破坏 [`MANIFEST.csv`](MANIFEST.csv) 的 `dst_*` 列与只读证明的对应关系。文件名歧义由本条裁定消除，不靠改名。 |
| 防误用措施 | ⛔ **禁止**把它当作新项目的参赛陈述提交或直接改名复用；它只能作为**旧项目口径的参考件**。新陈述写好后应落在 `report/`（进 git）。 |

> 结论：`ref/` 的「外部文献」定位**其余部分仍然成立**；本文件是唯一例外，且已被显式登记。

---

## 6. 怎么读这些资料

### 6.1 PDF —— 用 `pdftotext`，中文文本层完好

```bash
pdftotext -enc UTF-8 ref/guide/guide_amd_2026.pdf -            # 全文到 stdout
pdftotext -enc UTF-8 -f 24 -l 26 ref/guide/guide_amd_2026.pdf - # 只抽 24–26 页
```

> ⚠️ **不要因为某个读取工具失败就断定「PDF 读不了」。** 编辑器渲染依赖 `pdftoppm`（可能未装），
> 与**文本提取**是两回事（旧项目 `ref/README.md:267-269`）。

### 6.2 `.xdc` 是 **GBK 编码**，直接读中文注释会乱码

```bash
iconv -f GBK -t UTF-8 ref/board/board_pinout_navigator_zynq_io_v3.7.xdc
```

出处：`D:\FPGA\oi\ref\README.md:271-275`。

### 6.3 `.rar` 厂商例程源码 —— 用 Windows 自带 `tar.exe`（bsdtar）

```bash
/c/Windows/System32/tar.exe -tf "D:\FPGA\A\mt9v034\2，程序源码\1_mt9v034_lcd.rar" | grep '\.v$'
```

> ⚠️ **必须写全 `/c/Windows/System32/tar.exe`**。Git Bash 的 `/usr/bin/tar` 是 GNU tar，**不认 rar**。
> 首次验证 2026-09-26（旧项目 `ref/README.md:95-107`）。

### 6.4 指南关键节位置

| 节 | 内容 | 页码 |
|---|---|---|
| 3.3.1–3.3.2 | 赛道简介、设计任务、初级组推荐方向（PYNQ） | 19 |
| 3.3.3 | 工具限制、板卡与器件限制、开源要求 | 20 |
| 3.3.5 | 提交内容（工程包 / 技能包 / 设计报告 / 推荐目录结构 / 评测权重） | 21–22 |
| 4.2 | Ryzen AI PC 支持申请（具身智能赛道） | 23 |

出处：`D:\FPGA\oi\ref\README.md:279-284`。

---

## 7. 未归档（体积大，按需自行从资料盘取）

以下**不在**本仓库（旧项目 `ref/README.md:49-54`）：

| 内容 | 位置 / 体积 |
|---|---|
| 《嵌入式 Linux 开发指南》(WM8960) | 资料盘，62 MB |
| PYNQ 例程包 `ZYNQ_7020_PYNQ.zip` | `D:\FPGA\A\4_Source_Code\5_PYNQ_Design\`，649 MB |
| **PYNQ 镜像 `ZYNQ-7020-2.7.0.img`** | 8.09 GB —— **不要放进仓库** |
| MT9V034 厂商例程 rar（`1_mt9v034_lcd.rar` 53.5 MB / `2_mt9v034_hdmi.rar` 50.2 MB） | `D:\FPGA\A\mt9v034\2，程序源码\`（旧项目 `ref/README.md:90-93`） |

> ⚠️ `D:\FPGA` 下**除 `oi/` 外一律只读**，不得修改（旧项目 `ref/README.md:316`）。

---

## 8. 配套文档

| 文件 | 作用 |
|---|---|
| [`hardware.md`](hardware.md) | **硬件能力清单** —— 逐条 `file:line` 出处，写报告从这里取数 |
| [`pitfalls_from_oi.md`](pitfalls_from_oi.md) | **从旧项目继承的踩坑清单** —— 现象 / 触发条件 / 定位方法 / 规避方法 / 出处 |
| [`MANIFEST.csv`](MANIFEST.csv) | 复制清单 **＋ 只读证明**：45 行的 `dst_relative` / `bytes` / `dst_bytes` / `src_mtime_before` / `src_mtime_final` / `src_unchanged` / `src_absolute`（列说明见 §2） |

---

## 9. 版权归属

本目录内容均为**第三方版权材料**：
AMD（赛题指南） · 正点原子 ALIENTEK（原理图 / 开发指南 / 教程） · OmniVision（SCCB / OV5640） ·
onsemi / Aptina（MT9V034） · 各触摸与显示驱动 IC 原厂（Goodix GT9147/GT917S/GT1151Q、FocalTech、ST 等）。

本地查证使用无碍；**公开分发前**必须确认每份材料的分发许可，或改为
「只在报告中引用来源链接与文件名」（承旧项目 `ref/README.md:353-361`）。
