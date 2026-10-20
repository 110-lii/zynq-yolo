# 整机 RTL 集成计划（D004 档：416×416 输入）

> 状态：**v2（2026-10-20）**。上游契约见 [`accel_interface.md`](accel_interface.md)（`conv_engine`
> 数值口径已冻结）；输入分辨率取 D004 = **416×416 精度优先档**。
>
> **v2 相对 v1 的改动（重要）**：**删除了 HDMI 上屏与 OSD 框图元支路**。D013 已裁定
> 板载触屏只作**数据输入端**、本项目**不做视频输出**（用户 2026-10-20 指出计划里出现
> `vid_hdmi_tx`/`vid_osd_boxes` 是越权设计）。删掉之后数据通路少一整段，且**不再需要
> 「PL 读回 DDR 再输出」的模块**——检测本来就在 PS 里做，PS 直接读 DDR 帧缓冲即可。

---

## 0. 这份文件回答什么

D004 定了 416 之后，整机 PL 需要补齐什么、为什么这么分、每步怎么验。三个问题的答案
分别是 §1（数据通路）、§2（分工理由）、§6（验收判据）。

**不在本文范围**：触屏 UI 的具体实现（D013 只定了用途；**硬件侧 2026-10-20 已核清** ——
800×480 / GT9147 / DCLK 30 MHz / 40P 引脚见 [`../ref/hardware.md`](../ref/hardware.md) §G，
剩 UI 形态未定 ⇒ **挂起**，见 §7-5）。

---

## 1. 数据通路

```text
OV5640 DVP（RGB565，640×480@30，pclk 24 MHz）
   │  pclk 域
   ▼
vid_dvp_capture ── AXIS 24bit RGB888（tuser=帧首，tlast=行尾）
   │
   ▼
inf_axis_async_fifo（pclk → 100 MHz，格雷码指针）
   │
   ▼
AXI DMA（S2MM）── burst 写 ──► DDR 帧缓冲（N 个 slot + 写指针）
                                   │
                                   │  PS 直接读（PYNQ 零拷贝 numpy），**无需 PL 读回模块**
                                   ▼
                     ┌──────────────────────────────┐
                     │ PS（PYNQ python）             │
                     │  · letterbox 416×416（灰边）   │
                     │  · 量化 int8 NCHW             │
                     │  · 逐层调 conv_engine          │
                     │  · 后处理 → 检测框/轨迹/事件    │
                     │  · 结果经以太网输出给 PC        │
                     └──────────────────────────────┘
```

**显示支路已删除**：检测结果不出现在任何屏幕上，由 PS 经以太网输出（PYNQ 自带能力，
PL 不参与）。因此 PL 侧没有 `vid_frame_reader`、没有 `vid_osd_*`、没有 TMDS/HDMI 时序。

**离线评测与现场演示共用同一条采集通路**：离线时由 PS 把视频文件的帧写进同一个 DDR
帧缓冲，PL 侧从 DMA 往后完全一样。

---

## 2. PL / PS 分工（含理由）

| 环节 | 谁做 | 为什么 |
|---|---|---|
| DVP 采集、字节对拼、RGB565→888 | **PL** | 时序由摄像头时钟决定，PS 无法实时跟 |
| DMA 写 DDR 帧缓冲 | **PL**（Xilinx AXI DMA IP） | S2MM 的突发/对齐/中断都是坑，自写只在必须省 LUT 时才值；7020 的 LUT 要留给卷积 |
| letterbox 到 416×416、量化 int8 | **PS**（numpy） | 每帧只做一次 resize+pad；放 PL 要额外 line buffer + 定点坐标口径，不划算（决定 1） |
| 卷积层 | **PL** `conv_engine` | 唯一有算力收益的部分（契约见 `accel_interface.md`） |
| MaxPool / Concat / 上采样 / DFL | **PS** | 搬运量远大于算术量（`yolo_on_zynq7020_survey.md` §7 实测） |
| NMS / 阈值 / 后处理 / 事件判定 | **PS** | 需要分支与排序，PL 化收益低 |
| 结果输出（以太网 + SD 卡） | **PS** | D014：**两路都做**，结果序列化一次、同心格式写出；PYNQ 现成能力，不需要 PL 参与 |
| 触屏 UI（球衣颜色/半场录入） | ⏸ 挂起 | D013 定了用途、**硬件已核清**（`ref/hardware.md` §G），但 UI 形态未定 ⇒ 无可验收目标 |

---

## 3. 模块清单与当前状态

| 模块 | 目录 | 状态 | 判据 |
|---|---|---|---|
| `vid_dvp_capture` | `src/rtl/video/` | ✅ 已实现 + TB 通过（出口 32 位、高 8 位补零） | `VID_DVP_CAPTURE_TB_PASS`（`make sim`） |
| `vid_test_pattern` | `src/rtl/video/` | ✅ 已实现 + TB 通过（闭式图样，M1 的假摄像头） | `VID_TEST_PATTERN_TB_PASS` |
| `inf_axis_async_fifo` | `src/rtl/common/` | ✅ 已实现 + TB 通过（FWFT、格雷码 CDC、单侧复位；含 6 个变异自检） | `INF_AXIS_ASYNC_FIFO_TB_PASS` |
| 采集链整链（pattern→capture→FIFO，25→100 MHz） | `sim/tb/video/` | ✅ 已实现 + TB 通过 + **黄金模型后置比对** | `VID_CAPTURE_CHAIN_TB_PASS` + `CAPTURE_MATCH_OK` |
| 黄金模型 / 比对器 | `tools/capture_golden.py` | ✅ 已实现（`--out` 生成、`--compare` 比对；自测：改 1 字节必 FAIL） | `CAPTURE_GOLDEN_OK` / `CAPTURE_MATCH_OK` / `CAPTURE_MATCH_FAIL` |
| BD 集成（PS7 + AXI DMA + 采集链） | `build/scripts/bd_capture_chain.tcl` | 🟨 **建设中**：BD 已 validate 通过，综合实现出片中；产物 `board/overlay/capture_chain.{bit,hwh}` + `capture_chain_addr.txt` | 综合/实现日志 `BD_DONE_OK`，下载 `PROGRAM_OK` |
| 上板验收（M1） | `board/scripts/jtag_capture_test.tcl` | 🟨 已写 + 假硬件试跑通过（`stub_jtag_capture_dryrun.tcl`：pass→OK / corrupt→FAIL），**待真板实跑** | `JTAG_CAPTURE_OK` + `CAPTURE_MATCH_OK` |
| 触屏 UI 的 LCD 时序核 + 触摸读取 | — | ⏸ **挂起**（硬件资料已齐：`ref/hardware.md` §G；等 D013 的 UI 形态） | 立项后再定（先定判据再写 RTL） |

**明确不做**（记录以免再被当成待办）：HDMI/TMDS 输出、OSD 框图元、PL 侧帧读回与缩放。

---

## 4. 时钟 / 复位 / 跨时钟域

| 域 | 频率 | 用途 |
|---|---|---|
| `pclk` | 24 MHz（真机：随 OV5640 XVCLK 分频）／**M1 用 25 MHz**（PS `FCLK_CLK1`，假 pclk） | DVP 采集 + 图样发生器 |
| `axi_clk` | 100 MHz（PS `FCLK_CLK0`） | AXI HP0 / DMA / `ctrl_smc` / `data_smc` / FIFO 读侧 |

- 复位：PS `FCLK_RESET0_N`（低有效）分两路进 `proc_sys_reset`（100M / 25M 各一个），
  其 `peripheral_aresetn` 经 `util_vector_logic` **取反**后喂给自研 RTL 的高有效同步复位
  （`cap_0/rst`、`pat_0/rst`、`fifo_0/s_rst`、`fifo_0/m_rst`）——不改被 TB 验证过的复位语义。
- CDC 只允许经 `inf_axis_async_fifo`（AXIS 语义）或 Xilinx IP；**禁止手写打两拍传多比特**。

---

## 5. 带宽（估算，非实测——上板后替换成实测值）

| 项 | 估算 | 口径 |
|---|---|---|
| 采集写 DDR | 640×480×3 B × 30 ≈ **27.6 MB/s** | 计算值 |
| 416×416×3 int8（一层输入） | **519 KB** | 计算值 |
| 整网 DDR 往返总量 | **待实测** | 用 `tools/pynq_net.py` 统计每层尺寸后累加 |
| HP0 可用带宽 | **待实测**（7020 DDR3 32bit@533 理论 1.06 GB/s） | 上板 DMA 基准 |

> 采集只占 27.6 MB/s，不是瓶颈；真正的风险在 `conv_engine` 每层都要把输入从 DDR 读一遍、
> 输出写一遍。这条必须在 M3 实测，不许用理论值写进报告（`repo_spec.md` §5）。

---

## 6. 里程碑与判据（先定「什么算通过」，再动手）

| 里程碑 | 内容 | 通过判据（可失败） |
|---|---|---|
| **M1 采集** | DVP → AXIS → DMA → DDR 帧缓冲 | ① `make sim` 全绿；② 板上连续 1000 帧 `stat_overflow=0`；③ 帧率 30 fps ±1%（帧计数/秒表，不靠感觉）；④ PS 读出的帧与 PC 参考图逐字节相同（≥100 像素抽检） |
| **M2 帧缓冲可用性** | DMA 落 DDR 的格式/对齐/多缓冲 | ① 连续 10 min 无丢帧、无 DMA 溢出中断；② slot 轮转下 PS 拿到的帧序号单调无跳变；③ 静态场景下相邻帧差异仅噪声量级（能证明不是同一帧） |
| **M3 卷积集成** | BD 集成 `conv_engine` + PS 逐层调度 | ① 单层输出与 `csim` 逐字节相同；② 首层 416 行带拆分结果与整图参考逐字节相同（§7-4）；③ 整网单帧输出与 PC onnx 逐层 `cos > 0.999`；④ 端到端单帧耗时落 `report/` |
| **M4 实时性** | 摄像头 → 检测 → 事件/轨迹输出 | ① ≥ N fps（N 由 M3 实测吞吐反推，先写「待定」）；② 连续 10 min 无死机/漂移；③ 结果流格式与离线评测输出同构（同一份后处理代码） |

> **反例先写**：每条判据都必须「能失败」。M1-④ 若用「能出图」当判据，色序接反、字节
> 错位都能通过——所以比字节。M2-③ 若只看「有帧」，DMA 一直传同一帧也要到 10 min。

---

## 7. 未决问题

1. **帧缓冲像素格式**：RGB888（3 B/px）还是 RGB565（2 B/px，PS 侧展开）？后者省 1/3 采集带宽。
2. **letterbox 灰边值**：必须与量化输入口径一致（114？0？`zp_in`？）。PL 不碰，但报告要写清，
   否则「416 精度优先」的分数无法复现。
3. **OV5640 配置**：I²C(SCCB) 序列走 PS（PYNQ 有现成库）验证，出问题再移 PL。
4. **★ 416 与 `conv_engine` 的片上容量冲突（先解，影响 PS 调度）**：现有内核按 320 档设计，
   最坏激活为首层输出 `16×160×160 = 409 600 B`，塞进 7020 的 BRAM（140×36Kb ≈ 612 500 B，
   `src/hls/accel/src/conv_engine.cpp:12`）。换 416 后首层输出 `16×208×208 = 692 224 B`，
   **单块激活就超过整片 BRAM**。三条路：(a) **沿 H 切带**，每带 104 行输出 + 2 行 halo
   （输入带 106 行 ⇒ `16×106×208 = 352 768 B` ✓），PS 调两次、丢弃 halo 拼接，内核已有
   列分块（`TILE_W=40`，`conv_engine.cpp:67`）可复用；(b) 抬 `H_IN/W_IN` 重综合——容量不够，
   **不可行**；(c) 首层移 PS——≈1.5 GOP/帧，不可接受。**倾向 (a)**，验收逐字节比。
5. **触屏 UI 何时做**：硬件侧已无资料缺口（800×480、GT9147、DCLK 30 MHz、40P 引脚全部有手册出处，
   见 [`../ref/hardware.md`](../ref/hardware.md) §G；仅剩屏 ID 位与同步极性需实测），但 **D013 只定了用途**、
   UI 形态（页面与交互）未定 ⇒ 等用户给需求再立项，**不阻塞 M1–M4**。
6. **结果输出形态（已定，D014）**：**以太网推 + 落 SD 卡两条都做**（用户 2026-10-20）。
   设计要求：结果**只序列化一次**，两个 sink **同构写出**（SD 文件格式 = 推送格式），
   以便现场异常时靠 SD 文件事后复核。仍待定的子项：推送协议（TCP/UDP/HTTP）与 SD 上的文件格式/分段策略。

---

## 8. 下一步（按序）

1. ~~`inf_axis_async_fifo` + TB~~ ✅ 完成（2026-10-20，含 6 个变异自检）；
2. ~~BD 集成采集链（DMA 落 DDR）~~ ✅ BD 已 validate，出片与**上板 M1**进行中；
3. BD 集成 `conv_engine` + M3（先单层，再整网 416，含 §7-4 行带拆分）；
4. M4 端到端实时性与结果流（D014 双通道）。

> 顺序理由：先把「每个模块单独可验」打通，最后才做 BD 集成——BD 出问题时的定位成本最高。

---

## 9. 实现记录（2026-10-20，M1 落地时踩到的坑）

> 只记**实测撞到并已修**的，不写猜测。每条都给了报错原文或现象，便于下次一眼认出来。

1. **`axi_dma` 的 M_AXI 与 PS7 `S_AXI_HP0` 协议不同 ⇒ 必须过 SmartConnect**
   报错原文：`[BD 41-1285] The protocols of the interfaces '/axi_dma_0/M_AXI_S2MM'(AXI4)
   and '/processing_system7_0/S_AXI_HP0'(AXI3) are incompatible.`
   ⇒ BD 里加 1×1 `data_smc`（SmartConnect）做 AXI4→AXI3 转换，再连 HP0。

2. **PS7 的 `FCLK_CLK1` 默认不存在**：只写 `PCW_FPGA1_PERIPHERAL_FREQMHZ` 不够，必须同时
   `PCW_EN_CLK1_PORT=1`（且依赖 `PCW_USE_CR_FABRIC=1`），否则 `get_bd_pins .../FCLK_CLK1`
   返回空，`connect_bd_net` 报 `[BD 41-701]`。

3. **`get_bd_intf_pins -of_objects` 只接受单元对象**：把 `"fifo_0"` 当名字传会报
   `[Common 17-161] Invalid option value 'fifo_0' specified for 'of_objects'`；
   要传 `$fifo`（create_bd_cell 的返回值）。

4. **自研 RTL 进 BD 会自动推断 AXIS 接口**（实测：`create_bd_cell -type module -reference
   vid_dvp_capture` 后出现接口引脚 `/cap_0/m_axis`），标量端口仍是普通 pin ⇒ 不需要
   把模块打包成 IP 就能连 `S_AXIS_S2MM`。但**命名必须遵守** `*_tdata/tvalid/tready/tlast/tuser`。

5. **DMA 内存侧位宽会被上游传播覆盖**：`CONFIG.c_m_axi_s2mm_data_width=64` 被
   SmartConnect 按 32 位流传播覆盖，日志 `[BD 41-702] ... ignored`。直接写 32 位，
   不留「以为设了 64」的假象；带宽核算 37 MB/s ≪ 400 MB/s，够用。

6. **DMA 不能吃 24 位流**：AXI DMA 的 stream width 只支持 8/16/32/64/128，
   `axis_dwidth_converter` 也不做 24→32 ⇒ 采集模块出口改成 32 位、高 8 位补零
   （见 `src/rtl/video/vid_dvp_capture.v` 文件头）。

7. **TB 不能靠"停 ready 但 href 挂着"来测反压**：DVP 每拍是 1 个字节，href 不动等于
   继续灌"幽灵字节"，会把字节配对整体错位（本次表现为颜色从第 3 个像素起全错、
   多出 4 个像素）。正确做法是**照常按节奏打像素**，由 FIFO 吸收。

8. **FIFO 指针必须有复位初值**：无复位时 `x` 会顺着格雷码比较传播成恒 `x` 的
   `tvalid/tready`（首次仿真 0 像素）。两侧复位都要清本域指针 + 本域同步链。

9. **`mrd -bin -file` 是本轮的关键工具**：XSCT 支持 `mrd -bin -file <f> <addr> <count>`，
   1.2 MB 一帧可以一次性 dump 成二进制，直接喂 `tools/capture_golden.py --compare`；
   比在 Tcl 里解析文本 mrd 快且不丢格式。

10. **试跑桩（stub）必须与真实结果隔开**：`stub_jtag_capture_dryrun.tcl` 会伪造 dump 与
    结果 JSON，所以 `jtag_capture_test.tcl` 的结果路径支持 `RESULT_JSON` 环境变量覆盖，
    并且桩只写 `build/stub/` 下的临时文件——**不能让一次假跑在 `data/results/` 里留下
    "板上通过"的证据**（本轮实际误写过一个 `data/results/jtag_capture_test.json`，已删；
    桩产物也误落过 `board/scripts/`，已改到 `build/stub/`）。

11. **★ 图样周期性导致的判据盲区（已知、已缓解但不能消除）**：`vid_test_pattern` 的
    `R5=x&0x1F`、`G6=y&0x3F` ⇒ 水平周期 **32 px**、垂直周期 **64 行**。因此
    「整 32 px / 整 64 行」的错位与正确帧**不可区分**。缓解：`tools/capture_golden.py`
    在比对失败时会**枚举这些等价平移**并输出 `CAPTURE_SHIFT_DETECTED offset_px=<d>`——
    把「看不出来」变成「能说出平移了多少」。彻底消除需要把 x/y 高位编进图样（改公式 +
    改 TB + 改黄金模型 + 重出 bit），本轮**未做**，因为整周期错位不是当前路径的实际故障模式。

12. **★ 板级致命状态：`fpga` 失败会把 DAP 弄进 wedge**（2026-10-20 实测）：
    `fpga capture_chain.bit` 报 `fpga configuration failed. DONE PIN is not HIGH`；
    紧接着 DAP 变成 `Cannot open JTAG port: AP transaction error, DAP status 0x30000021`，
    连 `rst -system` 都报 `Cannot reset DAP`，`targets` 里 APU 消失。**软件侧无法恢复**
    （重启 hw_server 无效），只能**断电重启板子**（或按 PS 复位键）。
    脚本已改成：`fpga` 失败 → 降 JTAG 频率到 1.5 MHz + 重做 `rst -system`/`ps7_init` 再试一次，
    仍失败则**立刻** `JTAG_CAPTURE_FAIL` 并提示断电，不再继续往下跑把板子带进更差的状态。