# hardware.md —— 领航者 ZYNQ 7020 硬件能力清单（本队板卡实测口径）

> **用途**：写 `report/` / `docs/` 时**从这里取数**。任何进入报告的数字必须能在此文件找到出处。
> **规则**：每一条都带 `文件:行号`；**查不到出处的标 `UNVERIFIED`，不猜数**。
>
> **出处根目录约定**（下文为省篇幅，路径按此缩写）：
>
> | 缩写 | 实际路径 | 性质 |
> |---|---|---|
> | `CLAUDE.md:N` | `D:\FPGA\oi\CLAUDE.md:N` | 旧项目根文档（**只读**） |
> | `README.md:N` | `D:\FPGA\oi\README.md:N` | 旧项目 README（**只读**） |
> | `ref/README.md:N` | `D:\FPGA\oi\ref\README.md:N` | 旧项目 `ref/` 说明（**只读**） |
> | `DECISIONS.md:N` | `D:\FPGA\oi\ai\coordination\DECISIONS.md:N` | 旧项目决策台账（**只读**） |
> | `ai/sessions/X.md:N` | `D:\FPGA\oi\ai\sessions\X.md:N` | 旧项目会话记录（**只读**） |
> | `ai/raw/X.md:N` | `D:\FPGA\oi\ai\raw\X.md:N` | 旧项目会话原文（**只读**） |
> | `docs/repo_spec.md:N` | `D:\zynq-yolo\docs\repo_spec.md:N` | **本仓库**规范（§6 是环境事实表） |
>
> `docs/repo_spec.md` §6（`docs/repo_spec.md:154-171`）是**二手汇总表**；
> 本文件把它**逐条还原到一手出处**，并补上 §6 未覆盖的项。

---

## A. 器件与板卡

| 项 | 值 | 出处 |
|---|---|---|
| 器件型号 | `xc7z020clg400-2` | `CLAUDE.md:8`（器件名写作 `XC7Z020CLG400-2`）；`docs/repo_spec.md:158`（旧项目 5 个 `.xpr` 与全部 `build.tcl` 一致） |
| 开发板 | 正点原子 **领航者 ZYNQ 7020** | `CLAUDE.md:8` |
| 底板版本（本队） | **V3.7**，音频芯片 **WM8960** | `CLAUDE.md:221` |
| 核心板 | **ZYNQ_CORE_2V5** | `CLAUDE.md:221`；`CLAUDE.md:248` |
| 底板原理图 | `领航者ZYNQ底板原理图_V3.6.pdf`（V3.6 版原理图对应 V3.7 底板） | `CLAUDE.md:228`；`ref/README.md:143` |
| 参赛平台写法 | 「正点原子 领航者 ZYNQ（XC7Z020CLG400-2，Zynq-7000 SoC）」 | `ref/board/entry_statement.docx` 正文（**`.docx` 无行号**，仅文件级出处） |

> ⚠️ **一件事说清楚**：`CLAUDE.md:8` 写 `XC7Z020CLG400-2`，`docs/repo_spec.md:158` 写
> `xc7z020clg400-2`（小写 + 短横线）。**二者是同一颗器件的不同书写习惯**，
> 前者是器件丝印/手册写法，后者是 Vivado `PART` 名写法（`CLAUDE.md` 里挑器件也写作 `xc7z020`：`ai/raw/2026-09-20_ed107899.md:2767`）。

---

## B. ★ 资源上限（7020）

**原文**（`CLAUDE.md:210-211`）：

```
53,200 LUT6（≈85,000 LUT4 当量）/ 106,400 FF / 4.9 Mb BRAM / 220 DSP /
1 GB DDR3 @ 34.1 Gbps / 双核 A9 @ 766 MHz
```

| 资源 | 值 | 出处 |
|---|---|---|
| LUT | 53,200 LUT6（≈85,000 LUT4 当量） | `CLAUDE.md:210` |
| FF | 106,400 | `CLAUDE.md:210` |
| BRAM | 4.9 Mb | `CLAUDE.md:210` |
| DSP48 | **220** | `CLAUDE.md:210`；`CLAUDE.md:206` |
| DDR3 | 1 GB @ 34.1 Gbps | `CLAUDE.md:211` |
| PS CPU | 双核 ARM Cortex-A9 @ **766 MHz** | `CLAUDE.md:211` |

> ⚠️ `docs/repo_spec.md:163` 把 BRAM 写成「**4.9 Mb BRAM（140 × RAMB36）**」。
> **「140 × RAMB36」这个括号在当前一手出处里查不到** —— 旧项目 `CLAUDE.md:210-211` 只写 `4.9 Mb BRAM`。
> 引用时按 `CLAUDE.md:210` 的「4.9 Mb」为准；`140 × RAMB36` 标 **UNVERIFIED**（见 §K）。

---

## C. ★ 外设硬约束（选题与设计时不得违反）

**原文表**（`CLAUDE.md:197-206`，逐行）：

| 约束 | 影响 | 出处 |
|---|---|---|
| **无 MIPI D-PHY** | 摄像头只能用 **DVP**（板载 BANK34 接口支持 **OV7725 / MT9V034 / OV5640**，见 Session 009） | `CLAUDE.md:201` |
| **无 HDMI 输入** | 只有 HDMI 输出（1080P@30），不能做视频采集 / 环出 | `CLAUDE.md:202` |
| **无 GTX/GTH 收发器** | **没有 PCIe、SFP、10G 以太网** | `CLAUDE.md:203` |
| **DDR3 全部挂在 PS 端** | PL 必须经 **AXI HP 端口**访问 DDR3，PL 无独立外部存储 | `CLAUDE.md:204` |
| 板对板连接器 | 上限 **1.5 Gbps** | `CLAUDE.md:205` |
| **DSP 仅 220 个** | **不做大算力 CNN** | `CLAUDE.md:206` |

### ★★ 逐字引用：「DSP only 220 — 不做大算力 CNN」

> **`CLAUDE.md:206` 原文（逐字，含表格管道符）**：
> ```
> | DSP 仅 220 个 | 不做大算力 CNN |
> ```

**这一条的连带影响**（同样有出处，不是推测）：

- 手球判定**只能给"疑似"**，不能做实判 —— `project/doc/topic_statement.md:194`、`project/doc/topic_considerations.md:158`
- 「字面移植把权重跑在板上不现实」：PL 只有 220 DSP；PS 是 A9@766MHz，跑 Transformer 类模型帧率极低 —— `ai/raw/2026-10-09_46acd8b3.md:421`
- 该结论在旧项目被反复引用，属**已定结论**而非待议项：`project/doc/entry_statement.md:439`、`project/doc/module_decomposition.md:158`

> 注：上面三条路径也是 `D:\FPGA\oi\` 下的文件（**只读**）。
> `docs/repo_spec.md:167` 引用的「旧项目结论原文」形式为英文 `DSP only 220 — 不做大算力 CNN`；
> **一手出处的实际文字是中文「DSP 仅 220 个 / 不做大算力 CNN」**（`CLAUDE.md:206`）。引用请用中文原文。

---

## D. 主要外设（`CLAUDE.md:215-217`）

**原文**（`CLAUDE.md:215-217`）：

```
DVP 摄像头、HDMI 输出、**双千兆网**（PS: YT8521S + PL: YT8531 RGMII）、USB×4、
CAN、RS485、RS232、**音频 WM8960**（V3.7 底板；V3.9 底板才是 ES8388）、
RGB888 LCD、XADC（1 MSPS）、J3/J4 40pin 扩展口
```

| 外设 | 值 | 出处 |
|---|---|---|
| 摄像头接口 | DVP | `CLAUDE.md:215`；`CLAUDE.md:201` |
| 视频输出 | HDMI 输出，**1080P@30** | `CLAUDE.md:202` |
| 千兆网 ×2 | PS 侧 **YT8521S**；PL 侧 **YT8531**（RGMII） | `CLAUDE.md:215` |
| USB | ×4 | `CLAUDE.md:215` |
| 串行 | CAN / RS485 / RS232 | `CLAUDE.md:216` |
| 音频 | **WM8960**（V3.7）；V3.9 才是 ES8388 | `CLAUDE.md:216` |
| LCD | RGB888 | `CLAUDE.md:217` |
| ADC | XADC，1 MSPS | `CLAUDE.md:217` |
| 扩展口 | J3 / J4，各 40pin | `CLAUDE.md:217` |

**双千兆的工程分工**（决策台账，非硬件手册）：

| 决策 | 内容 | 出处 |
|---|---|---|
| D008 | A 路走 **PL 侧 YT8531（RGMII）**，B 路走 **PS 侧 YT8521S（GEM）**，两物理网口各承载一路 | `DECISIONS.md:22` |
| D012 | 最终定型为**单物理网口直连**（PL 侧 YT8531 RGMII，`192.168.1.10`），单线双端口时分复用 | `DECISIONS.md:26` |

> ⚠️ D008 与 D012 结论不同（双网口分用 → 单网口复用）。**引用时以 D012 为准**，
> 但两者都只是旧项目的**方案选择**，不是板卡的硬约束。

---

## E. ★ 摄像头接口（DVP / SCCB）

| 项 | 值 | 出处 |
|---|---|---|
| 板载接口所在 BANK | **BANK34** | `CLAUDE.md:201` |
| 支持模组 | **OV7725 / MT9V034 / OV5640** | `CLAUDE.md:201` |
| OV5640 与 MT9V034 引脚关系 | **共用同一组 DVP 引脚**，OV5640 多出 4 根 | `ai/raw/2026-09-29_0d19bce2.md:1091` |
| OV5640 多出的 4 根 | `cam_rst_n`(**P14**) / `cam_pwdn`(**V15**) / `cam_scl`(**T10**) / `cam_sda`(**T11**) | `ai/raw/2026-10-06_46acd8b3.md:611`；`ai/raw/2026-09-29_0d19bce2.md:1096` |
| 第二路 DVP（J4）状态 | **已暂停**（D005），板载/J4 相机延后 | `DECISIONS.md:17`；`DECISIONS.md:22` |

**V3.7 xdc 中的板载摄像头引脚**（`ai/raw/2026-09-23_42dd3fe9.md:1777` 原文）：
```
cam_rst_n=P14 cam_pwdn=V15 cam_pclk=W14 cam_vsync=U12 cam_href=T12
```

| 信号 | 引脚 | 出处 |
|---|---|---|
| `cam_pclk` | **W14** | `ai/raw/2026-09-23_42dd3fe9.md:1777` |
| `cam_vsync` | **U12** | `ai/raw/2026-09-23_42dd3fe9.md:1777` |
| `cam_href` | **T12** | `ai/raw/2026-09-23_42dd3fe9.md:1777` |
| `cam_rst_n` | **P14** | `ai/raw/2026-09-23_42dd3fe9.md:1777` |
| `cam_pwdn` | **V15** | `ai/raw/2026-09-23_42dd3fe9.md:1777` |
| `cam_scl` / `cam_sda` | **T10 / T11**（SCCB，经 EMIO） | `ref/README.md:122`；`ai/raw/2026-09-23_42dd3fe9.md:1783` |
| BANK34 侧命名 → 引脚 | `B34_L1_P` = **T11** = `cam_sda`；`B34_L8_P` = **W14** = `cam_pclk` | `ai/raw/2026-09-20_ed107899.md:758`；`ai/raw/2026-09-20_ed107899.md:765` |

> **交叉验证**：`B34_L1_P → T11 = cam_sda`（正是板卡 xdc 的 `emio_sccb_tri_io[1]`），
> 与 Session 022「J4 pin 2 = cam_sda」完全吻合 —— `ai/raw/2026-10-06_46acd8b3.md:1292`。

---

## F. ★★ 引脚硬冲突（两条死线）

### F.1 ⛔ **J4 pin 2 绝对禁用** —— 它是 `cam_sda`，与板载接口**硬并联**

| 项 | 内容 | 出处 |
|---|---|---|
| 现象 | J4 的 **pin 2** 就是 `cam_sda`，与板载摄像头接口**硬并联** | `ai/raw/2026-09-20_ed107899.md:1481` |
| 汇总表条目 | 「★ **J4 pin 2 绝对禁用** —— 它是 `cam_sda`，与板载接口**硬并联**」 | `ai/raw/2026-09-20_ed107899.md:4927` |
| 另一处同一结论 | 「**J4 pin 2 绝对禁用** \| 它是 `cam_sda`，与板载座**硬并联**」 | `ai/raw/2026-10-06_46acd8b3.md:1055` |
| 物理映射 | `B34_L1_P` = **T11** = `cam_sda` | `ai/raw/2026-09-20_ed107899.md:758` |
| 为什么危险 | 飞线接成标准 2×9 摄像头接口时，**pin 2 与板载座并联** ⇒ "随意接"在**物理层**就不成立 | `ai/raw/2026-10-06_46acd8b3.md:1228` |

> **规避**：J4 上做扩展摄像头时，**必须避开 pin 2**；接线按「避开与板载座并联的脚」核对。

### F.2 ⛔ **LCD 与 J3 互斥**（同一批 `B34_L13–L24`）

| 项 | 内容 | 出处 |
|---|---|---|
| LCD 数据总线 | `B34_L13_P/N` … `B34_L24_P/N`（**12 对 = 24 个 IO**）+ `L5` `L7` `L9` `IO0` | `ai/raw/2026-09-20_ed107899.md:1475` |
| J3 扩展口 | `B34_L12_N`、**`L13–L24` 全部**、`L5` `L7` `L9` `L14` + `B35_*` ⇒ 🔴 **与 LCD 是同一批引脚 ⇒ 二者互斥** | `ai/raw/2026-09-20_ed107899.md:1476` |
| 汇总表条目 | 「**LCD 与 J3 互斥**（同一批 `B34_L13–L24`），但**与 J4 的 B13 区零重叠** ⇒ 两摄 + LCD 在引脚上成立」 | `ai/raw/2026-09-20_ed107899.md:4928` |

> **推论（有出处，非我推测）**：LCD 与 J4 的 B13 区**零重叠** ⇒ **两摄 + LCD 在引脚上成立**
> （`ai/raw/2026-09-20_ed107899.md:4928`）。但**用 J3 就不能同时用 LCD**，反之亦然。

---

## G. LCD / 触摸

| 项 | 值 | 出处 |
|---|---|---|
| 接口类型 | **RGB888** | `CLAUDE.md:217` |
| 本队选用型号 | **800×480**（`ATK-MD0430R-800480`） | `ai/sessions/054_ov5640_sccb_and_cfg.md:39`（标注为「用户确认」） |
| 模块支持的两种屏 | 480×272 与 800×480（模组名 `ATK-MD0430R`） | 文件名级出处（`.pdf` 无行号）：`rgb_lcd_module/reference/panel_480272_spec.pdf`、`rgb_lcd_module/reference/panel_800480_spec.pdf`、`rgb_lcd_module/reference/panel_480272_driver_ic_ST7282.pdf`、`rgb_lcd_module/reference/panel_800480_driver_ic_ST7262.pdf`；且旧项目目录原名即 `【正点原子】4.3寸RGBLCD电容触摸屏模块480272、800480`（见 `MANIFEST.csv` 的 `src_absolute` 列） |
| 触摸 IC 备选 | GT9147 / GT917S / GT1151Q（附 FocalTech 应用笔记） | 文件名级出处（`.pdf` 无行号）：`rgb_lcd_module/reference/GT9147_datasheet.pdf`、`GT9147_programming_guide.pdf`、`GT917S_datasheet.pdf`、`GT917S_programming_guide.pdf`、`GT1151Q_datasheet.pdf`、`GT1151Q_programming_guide.pdf`、`Application_Note_for_CTPM_FocalTech.pdf` |
| V3.7 触摸引脚名 | `lcd_scl` `lcd_sda` `ct_rst` `ct_int`（**引脚号与 V3.9 相同，仅端口名不同**） | `CLAUDE.md:240` |

> ⚠️ **LCD 具体时序参数（hsync/vsync 前后沿、porch、像素时钟）本文件不给出数字** ——
> `pdftotext` 不在当前会话 PATH 上，我没有逐行核对 `reference/panel_800480_spec.pdf`，
> 因此**不写猜测值**。标 **UNVERIFIED**，见 §K。
> 需要时序时请自行抽取：`pdftotext -enc UTF-8 rgb_lcd_module/reference/panel_800480_spec.pdf -`

---

## H. MT9V034 摄像头模块要点（7 条，均已核对原文）

出处：旧项目 `ref/README.md:74-80` 的表格（逐条已核对手册原文，推导过程见 `ai/sessions/043_mt9v034_reference_material_and_framerate.md`）：

| # | 事实 | 出处 |
|---|---|---|
| 1 | **全局快门**（快门效率 >99%）、752×480 Wide-VGA、6.0 µm 像元、10-bit 列并行 ADC、27 Mp/s | `ref/README.md:74`（原文标注：Rev.G 首页特性表） |
| 2 | **推荐主时钟 26.66 MHz**；该频率下全分辨率 **60 fps**（总帧时 444,154 主时钟 = 16.66 ms） | `ref/README.md:75`（Rev.G **p.15 Table 4**） |
| 3 | ⚠️ **本模块自带 24.000 MHz 晶振** ⇒ 实测帧率 `444,154 ÷ 24 MHz = 18.51 ms ≈ 54 fps`，**不是 60** | `ref/README.md:76` |
| 4 | ⚠️ **缩窄窗口宽度并不提高帧率** —— 列并行 ADC 决定**最小总行时间 704 列** | `ref/README.md:77`（Rev.G p.15 注） |
| 5 | 提帧率只有**行 binning** 一条路（bin2/bin4 ⇒ 2×/4×）；**列 binning 不提高帧率** | `ref/README.md:78`（Rev.G p.15 注） |
| 6 | **排针只引出 8 根数据线**（标 `D2`–`D9` = 传感器 `DOUT[9:2]`），低 2 位未引出 | `ref/README.md:79` |
| 7 | 输出只有 **10-bit 并行 raw** 或 **8/10-bit 串行 LVDS**，**没有 YUV / RGB565** | `ref/README.md:80` |

> ⚠️ 第 7 条是**最容易踩的坑**：OV7725 / OV5640 教程里的 RGB565 配置代码
> **不能套到 MT9V034 上** —— 它的彩色输出是 **Bayer 原始格式**，需要主机侧去马赛克
> （`ref/README.md:82-83`）。
>
> ⚠️ **厂商例程是给「达芬奇」板跑的，引脚表与本队领航者板不通用**：
> 实测两套引脚号**零重合**，唯一撞号处更危险（`R16` 在厂商表是 `lcd_rgb[0]`，在本队总表是 `lcd_rgb[2]`）
> —— `ref/README.md:109-126`。

---

## I. ★ 底板版本陷阱：V3.7 ≠ V3.9

| 项 | V3.7（本队） | V3.9（勿用） | 出处 |
|---|---|---|---|
| 音频芯片 | **WM8960** | ES8388 | `CLAUDE.md:226`；`ref/README.md:141` |
| 底板原理图 | `..._V3.6.pdf` | `..._V3.9.pdf` | `CLAUDE.md:228` |
| 开发指南 | 三本带 **(WM8960)** 后缀 | 无后缀 | `CLAUDE.md:229` |
| 专用芯片手册 | `WM8960_v4.4.pdf` | — | `CLAUDE.md:231` |
| xdc（转 UTF-8 后）md5 | `1a8959ba581f2d400c24e143756ce408`（157 行） | `d1a27dd259e2bf9c6368a4e5197efec0`（157 行） | `CLAUDE.md:230`；`ref/README.md:160` |
| 原始字节 md5 | `52ab7c6527896c9276e35becc8f2a419` | — | `ref/README.md:159` |

**逐行 `diff`（各 157 行）只有两处差异**（`CLAUDE.md:236`；`ref/README.md:165-167`）：

| 位置 | V3.7 | V3.9 | 出处 |
|---|---|---|---|
| LCD / 触摸 | `lcd_scl` `lcd_sda` `ct_rst` `ct_int` | `touch_scl` `touch_sda` `touch_rst_n` `touch_int`（多 `lcd_rst_n` @ L17） | `CLAUDE.md:240` |
| 音频 I2S | LRC **两条**：`aud_dac_lrc`@**G18** + `aud_adc_lrc`@**L20**（共 6 根） | LRC **一条**：`aud_lrc`@**L20**；**G18 完全未分配** | `CLAUDE.md:241` |

> ⚠️ 准确表述是「**G18 在 V3.9 中被释放**」，不是「两条合成一条」（`CLAUDE.md:243`）。
> 工程含义：WM8960 需要 DAC/ADC 独立帧时钟，ES8388 共用一条 ⇒ **两版音频 IP 核不能直接互用**（`CLAUDE.md:244`）。
>
> ⛔ **`D:\FPGA\NAVIGATOR_ZYNQ_IO.xdc`（资料盘根目录）是 V3.9 版**，必须换成 V3.7 那份
> （`CLAUDE.md:233-234`；`ref/README.md:149-150`）。

**DDR / 时钟 / 复位 / SD / 以太网在 diff 中零差异**，且两版共用同一核心板 `ZYNQ_CORE_2V5`
⇒ V3.9 的 PYNQ 镜像在 V3.7 底板上大概率可直接启动（`CLAUDE.md:247-249`）。
✅ **已于 2026-09-11 实测证实**：V3.9 的 PYNQ 镜像在 V3.7 底板上**首次上板即启动成功**
（Ubuntu 20.04 / 内核 5.4.0）—— `CLAUDE.md:252-253`。
⚠️ 但 xdc 只覆盖 **PL 侧引脚**，PS 侧配置不在其中 ⇒ 最终结论须由**实际烧卡启动**验证（`CLAUDE.md:250`）。

---

## J. 工具链与环境

| 项 | 值 | 出处 |
|---|---|---|
| 本机安装 | **Vivado 2020.2 + Vitis 2020.2**（`D:\Xilinx\`） | `CLAUDE.md:461` |
| 本队实际使用 | Vivado **2020.2**（与正点原子教程一致） | `README.md:149` |
| Vivado 可执行 | `D:/Xilinx/Vivado/2020.2/bin/vivado.bat` | `README.md:161`；`README.md:165` |
| 自带 Python | `D:\Xilinx\Vivado\2020.2\tps\win64\python-3.8.3\python.exe`（**内置 numpy，无需联网**） | `CLAUDE.md:155`；`README.md:146` |
| 指南推荐版本 | Vivado / Vitis **2026.1 或 2025.2**；用其他版本**须在报告中说明并保证脚本可复现** | `README.md:151-153`；`CLAUDE.md:11`；`docs/repo_spec.md:23` |
| 指南原文节号 | 3.3.3.1 | `README.md:151` |

> ⚠️ `docs/repo_spec.md:161` 写「本机仅 Vivado / Vitis / Vitis_HLS 2020.2」——
> **Vitis_HLS 2020.2 这个具体版本我没有在一手出处里找到独立佐证**（`CLAUDE.md:461` 只列 Vivado + Vitis）。
> 引用 Vitis_HLS 版本时标 **UNVERIFIED**。

---

## K. ⚠️ UNVERIFIED / 待核清单（**不要直接把这里的数字写进报告**）

| # | 项（**本表全部条目 = `UNVERIFIED`**） | 为什么标 UNVERIFIED |
|---|---|---|
| 1 | **UNVERIFIED** — BRAM「**140 × RAMB36**」 | 一手出处 `CLAUDE.md:210` 只写 `4.9 Mb BRAM`，未给 RAMB 个数。`docs/repo_spec.md:163` 的括号无一手佐证 |
| 2 | **UNVERIFIED** — **Vitis_HLS 2020.2** | `CLAUDE.md:461` 仅列 Vivado 2020.2 + Vitis 2020.2，未提 Vitis_HLS |
| 3 | **UNVERIFIED** — **LCD 时序参数**（hsync/vsync porch、像素时钟、DE 极性） | `pdftotext` 不在当前会话 PATH，未逐行核对 `rgb_lcd_module/reference/panel_800480_spec.pdf` / `panel_480272_spec.pdf` |
| 4 | **UNVERIFIED** — 480×272 屏是否曾在本队上板 | 只查到本队**选用 800×480**（`ai/sessions/054_ov5640_sccb_and_cfg.md:39`）；480×272 只有规格书在库，无上板记录出处 |
| 5 | **UNVERIFIED** — 触摸 IC 实际是哪一颗（GT9147 / GT917S / GT1151Q） | 三份手册都在库，但**未找到"本队用的是哪颗"的判定出处** |
| 6 | **UNVERIFIED** — DDR3「@ 34.1 Gbps」的测量口径 | `CLAUDE.md:211` 给了数字，但未给测量条件（位宽 × 频率？理论值还是实测？） |
| 7 | **UNVERIFIED** — `entry_statement.docx` 的归属 | 正文是本队作品陈述（自有产出），与 `ref/` 的「外部文献」定位冲突；本目录 `README.md` §5 已请求裁定 |

---

## L. 引用索引（去重，便于回溯）

| 出处 | 本文件用到它的哪些节 |
|---|---|
| `CLAUDE.md:8` | A |
| `CLAUDE.md:11` | J |
| `CLAUDE.md:155` | J |
| `CLAUDE.md:197-206` | C |
| `CLAUDE.md:201-206` | C |
| `CLAUDE.md:210-211` | B, C |
| `CLAUDE.md:215-217` | C, D, G |
| `CLAUDE.md:219-253` | A, G, I |
| `CLAUDE.md:226-245` | I |
| `CLAUDE.md:247-253` | I |
| `CLAUDE.md:461` | J |
| `README.md:146-153` | J |
| `README.md:161-165` | J |
| `ref/README.md:74-83` | H |
| `ref/README.md:109-126` | H |
| `ref/README.md:141-167` | I |
| `ref/README.md:122` | E |
| `DECISIONS.md:17,22,26` | D, E |
| `ai/sessions/054_ov5640_sccb_and_cfg.md:39` | G |
| `ai/raw/2026-09-20_ed107899.md:758,765,1475,1476,1481,4927,4928` | E, F |
| `ai/raw/2026-09-23_42dd3fe9.md:1777,1783` | E |
| `ai/raw/2026-09-29_0d19bce2.md:1091,1096` | E |
| `ai/raw/2026-10-06_46acd8b3.md:611,1055,1228,1292` | E, F |
| `ai/raw/2026-10-09_46acd8b3.md:421` | C |
| `docs/repo_spec.md:154-171` | 全篇（§6 是二手汇总，本文件为其一手还原） |
