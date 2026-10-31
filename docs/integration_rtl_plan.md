# 整机 RTL 集成计划（D004 档：416×416 输入）—— **历史方案，已被 D017 取代**

> ⛔ **本文是历史方案，不是当前主线。** 2026-10-23 实测后，D017 已把主线改为
> **PL `preproc`（320×320）+ PS ncnn YOLOv8n-0.25**；`conv_engine` 整网实测
> 9.63 s/帧（≈0.10 FPS），退出主线，仅保留逐层 bit-exact（6/6）与 AXI/带宽/时序验证价值。
> 当前架构与指标见 [`system_status.md`](system_status.md) 与
> [`../report/design_report.md`](../report/design_report.md)。本文以下内容**原文保留**，
> 用于记录 416 路线的设计、容量冲突与放弃理由。
>
> 状态：**v3（2026-10-20）**。上游契约见 [`accel_interface.md`](accel_interface.md)（`conv_engine`
> 数值口径已冻结）；输入分辨率取 D004 = **416×416 精度优先档**。
>
> **v3 相对 v2 的改动（重要）**：**取消板载摄像头采集**。D015 已裁定视频由 **PC 经以太网送入**，
> 主线不再使用 OV5640 / DVP / SCCB，也不再以 DVP→AXIS→DMA 作为输入通路。数据通路的起点改为
> **PS 侧网络接收**（端口与协议仍待定，见 §7-1），帧落 DDR 后的处理与 v2 相同。
>
> **v2 相对 v1 的改动**：**删除了 HDMI 上屏与 OSD 框图元支路**。D013 已裁定
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
PC（视频文件 / 实时推流）
   │  以太网（端口与协议待定，见 §7-1）
   ▼
PS（PYNQ Linux，网络接收 + 帧重组）
   │  写 DDR 帧缓冲（N 个 slot + 写指针）
   ▼
PS 预处理：letterbox 416×416（灰边）+ 量化 int8 NCHW
   │
   ▼
PL `conv_engine`（逐层卷积；输入/输出缓冲都在 DDR，经 AXI HP 访问）
   │
   ▼
PS 后处理 → 检测框 / 球员轨迹 / 队别 / 事件
   │  结果只序列化一次（D014）
   ├──► 以太网推给 PC
   └──► 落 SD 卡（与推送格式同构）
```

**摄像头支路已删除（D015）**：本项目不使用板载 OV5640/DVP/SCCB，也不使用
`vid_dvp_capture` → `inf_axis_async_fifo` → `AXI DMA` 作为输入通路。该链路的 RTL / BD /
仿真**保留为历史与回归资产**，但**不再出现在主线数据通路里**，也不再是 M1 的验收对象。

**显示支路已删除（D013）**：检测结果不出现在任何屏幕上，由 PS 经以太网输出（PYNQ 自带能力，
PL 不参与）。因此 PL 侧没有 `vid_frame_reader`、没有 `vid_osd_*`、没有 TMDS/HDMI 时序。

**离线评测与现场演示共用同一条输入通路**：两者都由 PC 经以太网把帧送给板端，区别只在
PC 侧是读文件还是实时推流；板端从「网络接收」往后完全一样。

---

## 2. PL / PS 分工（含理由）

| 环节 | 谁做 | 为什么 |
|---|---|---|
| 视频接收、帧重组、写 DDR 帧缓冲 | **PS**（PYNQ Linux 网络栈） | 网络协议栈与文件系统都是 PS 现成能力；PL 侧自写 MAC/UDP 会吃掉本该留给卷积的 LUT |
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
| **网络视频接收** | `tools/board_pipeline.py::NetFrameSource` + `tools/video_send.py` | ✅ **已实现并上板验等价**（D027：PS GEM + TCP + JPEG + `ZYVI`，端口 5007） | 同段 c03 文件源/网络源均 `frames=250 detections=17`、带球帧集合相同；**不为逐字节一致**（JPEG 有损，默认 q95） |
| `conv_engine` | `src/hls/accel/` | ✅ 已实现 + csim 通过 + 出片（83.33 MHz 时序收敛） | `CSIM_ALL_OK` / `BOARD_LAYER_OK` |
| `vid_dvp_capture` | `src/rtl/video/` | ⛔ **退出主线**（D015；保留为历史资产） | 原判据 `VID_DVP_CAPTURE_TB_PASS` 仅用于回归 |
| `vid_test_pattern` | `src/rtl/video/` | ⛔ **退出主线**（D015；保留为历史资产） | 原判据 `VID_TEST_PATTERN_TB_PASS` 仅用于回归 |
| `inf_axis_async_fifo` | `src/rtl/common/` | ⛔ **退出主线**（D015；保留为 CDC 参考实现） | 原判据 `INF_AXIS_ASYNC_FIFO_TB_PASS` 仅用于回归 |
| 采集链整链 / 黄金模型 / 采集 BD / JTAG 采集验收 | `sim/tb/video/`、`tools/capture_golden.py`、`build/scripts/bd_capture_chain.tcl`、`board/scripts/jtag_capture_test.tcl` | ⛔ **退出主线**（D015；历史记录见 `board/logs/board_capture_m1_20261020.md`） | 原判据 `VID_CAPTURE_CHAIN_TB_PASS` / `CAPTURE_MATCH_OK` / `BD_DONE_OK` / `JTAG_CAPTURE_OK` 仅用于回归 |
| 触屏 UI 的 LCD 时序核 + 触摸读取 | — | ⏸ **挂起**（硬件资料已齐：`ref/hardware.md` §G；等 D013 的 UI 形态） | 立项后再定（先定判据再写 RTL） |

**明确不做**（记录以免再被当成待办）：板载摄像头采集（D015）、HDMI/TMDS 输出、OSD 框图元、PL 侧帧读回与缩放。

---

## 4. 时钟 / 复位 / 跨时钟域

| 域 | 频率 | 用途 |
|---|---|---|
| `axi_clk` | **83.333333 MHz**（PS `FCLK_CLK0`，1000/12） | `conv_engine`、AXI HP、SmartConnect、寄存器访问 |

- 主线只有 **PS 提供的 `axi_clk` 一个时钟域**；摄像头 `pclk` 域随 D015 退出主线。
- 复位：PS `FCLK_RESET0_N`（低有效）经 `proc_sys_reset` 产生 `peripheral_aresetn`；
  自研 RTL 若需高有效复位，用 `util_vector_logic` 取反，不改被 TB 验证过的复位语义。
- **主线不需要 PL 侧 CDC**：网络接收在 PS，`conv_engine` 与 PS 同处 AXI 时钟域。
  历史采集链用的 `inf_axis_async_fifo` 仅作参考实现保留；若将来改走 PL 侧 RGMII，
  跨域仍只允许经异步 FIFO 或 Xilinx IP，**禁止手写打两拍传多比特**。

---

## 5. 带宽（估算，非实测——上板后替换成实测值）

| 项 | 估算 | 口径 |
|---|---|---|
| 网络视频入板（未压缩 RGB888） | 640×480@30 ≈ **27.6 MB/s**；1280×720@30 ≈ **82.9 MB/s** | 计算值；**以 PC 实际推流规格为准** |
| 网络视频入板（压缩码流） | 取决于编码器与码率 | 需 PS 解码，CPU 成本待实测 |
| 416×416×3 int8（一层输入） | **519 KB** | 计算值 |
| 整网 DDR 往返总量 | **待实测** | 用 `tools/pynq_net.py` 统计每层尺寸后累加 |
| HP0 可用带宽 | **待实测**（7020 DDR3 32bit@533 理论 1.06 GB/s） | 上板实测 |

> 网络接收本身不是主要瓶颈；真正的风险在 `conv_engine` 每层都要把输入从 DDR 读一遍、
> 输出写一遍。若 PC 送的是压缩码流，PS 解码还会额外吃 CPU。两条都必须在 M3 实测，
> 不许用理论值写进报告（`repo_spec.md` §5）。

---

## 6. 里程碑与判据（先定「什么算通过」，再动手）

| 里程碑 | 内容 | 通过判据（可失败） |
|---|---|---|
| **M1 视频入板** | PC → 以太网 → PS 网络接收 → DDR 帧缓冲 | ① PC 连续发送已知测试片段；② 板端收到的帧数与发送帧数一致、无丢帧；③ 抽检帧与 PC 参考帧逐字节相同；④ 分辨率/帧率/码率写入日志（不靠感觉） |
| **M2 帧缓冲可用性** | 接收落 DDR 的格式/对齐/多缓冲 | ① 连续 10 min 无丢帧、无接收缓冲溢出；② slot 轮转下 PS 拿到的帧序号单调无跳变；③ 静态场景下相邻帧差异仅噪声量级（能证明不是同一帧） |
| **M3 卷积集成** | BD 集成 `conv_engine` + PS 逐层调度 | ① 单层输出与 `csim` 逐字节相同；② 首层 416 行带拆分结果与整图参考逐字节相同（§7-4）；③ 整网单帧输出与 PC onnx 逐层 `cos > 0.999`；④ 端到端单帧耗时落 `report/` |
| **M4 实时性** | PC 视频 → 检测 → 事件/轨迹输出 | ① ≥ N fps（N 由 M3 实测吞吐反推，先写「待定」）；② 连续 10 min 无死机/漂移；③ 结果流格式与离线评测输出同构（同一份后处理代码） |

> **反例先写**：每条判据都必须「能失败」。M1-③ 若用「能出图」当判据，色序接反、字节
> 错位都能通过——所以比字节。M2-③ 若只看「有帧」，一直收同一帧也要到 10 min。

---

## 7. 未决问题

1. ~~**★ 网络入口（D015 的未定子项）**~~ → **已由 D027 落定（2026-10-31）**：
   - **网口**：**PS 侧 GEM**（Linux 网络栈现成，不动 PL、不出新片）；PL RGMII 方案不采用。
   - **协议**：**TCP**（与输出半条 `video_back.py` 同构）+ **JPEG 帧**（默认 q95）。
   - **帧边界与同步**：`magic b"ZYVI"` + 循环 `[u32 len][JPEG]`，`len=0` 结束；靠 TCP 流顺序，
     不做时间戳。**仍未做**：丢帧检测/重同步的显式机制（目前靠 TCP 保证不丢）。
   - 实现与上板判据见 `docs/system_status.md`「视频入口」一条、`ai/sessions/024` 轮次 7。
2. **帧缓冲像素格式**：PC 送 RGB888（3 B/px）、RGB565（2 B/px，PS 侧展开）还是压缩码流？
   前两者省去解码；压缩码流省网络带宽但吃 PS CPU。**由 PC 侧推流规格决定**。
3. **letterbox 灰边值**：必须与量化输入口径一致（114？0？`zp_in`？）。PL 不碰，但报告要写清，
   否则「416 精度优先」的分数无法复现。
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

1. ⬜ **待用户裁定**：网络入口选型（PS GEM vs PL RGMII）、协议与帧格式（§7-1）；
2. 按裁定实现 PC 侧推流脚本 + PS 侧接收/帧重组，拿到 M1 判据；
3. BD 集成 `conv_engine` + M3（先单层，再整网 416，含 §7-4 行带拆分）；
4. M4 端到端实时性与结果流（D014 双通道）。

> 顺序理由：输入源改了，先把「帧能可靠进 DDR」重新打通，再谈卷积与端到端；
> 摄像头采集链的历史经验（CDC/DMA/BD）保留在 §9，但不再作为主线验收对象。

---

## 9. 实现记录（2026-10-20，M1 摄像头链路踩到的坑 —— D015 后为历史资产）

> ⚠️ 本节记录的是**已退出主线的摄像头采集链**（DVP/DMA）经验，保留原因是 CDC、BD 连线、
> PYNQ 时钟源、观测隔离这些教训对任何后续 PL 集成仍然适用。**不要把它当成当前输入通路的实现说明。**
>
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

13. **★ PYNQ 路径与 JTAG 路径的时钟差异（2026-10-20 实测）**：
    JTAG 路径自己跑 BD 导出的 `ps7_init.tcl`，所以 `FCLK0=100 / FCLK1=25` 都按我们的 BD；
    **PYNQ 路径下 PS7 时钟来自镜像自带的 FSBL**，我们的 PS7 配置不会在 `Overlay()` 时生效。
    实测：FCLK1 派生的 pclk 域整块不工作（图样无输出、DMA 永远等数据，`DMASR=0` 到超时）。
    处置：把 pclk 改成 **FCLK0 → clk_wiz(MMCM) → 25 MHz**，这样只要 FCLK0 在（PYNQ 必有，
    AXI 就靠它）采集链就能跑。代价：多一个 MMCM。
    教训：**凡是「PS 提供的时钟」都要问一句「谁初始化 PS」** —— JTAG 与 Linux 是两套源头。

14. **PYNQ 侧环境的三个硬约束（都实测过）**：
    ① `pynq` 包装在 `/home/xilinx/pynq`，`sudo` 重置环境后 root 看不到它 ⇒ 必须
       `sudo -S bash -lc '...'`（login shell 会 source `/etc/profile.d/pynq_venv.sh` +
       `xrt_setup.sh`），否则报 `No module named 'pynq'`；
    ② 只 `sudo` 不 source XRT 会报 `No devices found, is the XRT environment sourced?`；
    ③ 板上 `python3` 为 3.8.2 / pynq 2.7.0 / numpy 1.17.4；在板上跑多行代码要 **scp 脚本**，
       别用 `ssh "... -c '...'"`（PowerShell 引号会把代码搞坏，本轮踩过一次，
       报的 `SyntaxError` 是引号造成的，不是 Python 版本问题）。

15. **上板调试的可观测性设计（本轮最有用的一条）**：PYNQ 路径没法像 JTAG 那样随手读内部信号，
    所以在 BD 里把关键内部结点引到一个 **32 位 xlconcat → AXI GPIO 通道 2 输入**，PS 侧
    忙读几万次统计命中：
      bit0 cap.stat_overflow / bit1 复位实况 / bit2 pat.vsync_n / bit3 pat.href /
      bit7 fifo.s_rst / bit8 fifo.m_rst / bit9 fifo.wfull / bit10 fifo.rempty /
      bit11 fifo.dbg_wr_hit / bit12 fifo.dbg_rd_hit / bit14 恒 1 的 sanity 位
    **必须忙读**：vsync 低电平只有 4 个 pclk（160 ns），FIFO 读侧在 DMA 全速消费时只有
    零星几拍有效——0.5 ms 间隔的采样会系统性漏掉（实测 0.2 s / 319 次全漏，
    改成忙读（MMIO ~3 us/次，0.05 s 采 1699 次）才看到 tvalid 命中 608 次）。

16. **★★ 观测会把被测对象切断：AXIS 接口成员脚不能单独接出去（2026-10-20，代价两次出片）**
    这是本轮最贵的一课。把 `cap_0/m_axis_tvalid`、`fifo_0/s_tready`、`fifo_0/m_tvalid`、
    `axi_dma_0/s_axis_s2mm_tready` 四根**接口成员脚**接进 debug concat 之后，BD 报：
    ```
    WARNING: [BD 41-1271] The connection to the pin: /cap_0/m_axis_tvalid has been
             overridden by the user. This pin will not be connected as a part of the
             interface connection: cap_0_m_axis
    CRITICAL WARNING: [BD 41-759] These pins are tied-off to all 0's ... /fifo_0/s_tvalid
    ```
    也就是：**观测动作把引脚从接口网里摘掉了**，FIFO 的 `s_tvalid` 变成悬空输入被 tie 成 0。
    硅上表现极具误导性——源侧 `cap.m_axis_tvalid` 照样在翻转（读到 37% 命中）、
    `s_tready` 恒 1、`wfull` 恒 0，但**写指针一次都没动**（`dbg_wr_hit=0`）、`rempty` 恒 1、
    DMA 永远等不到数据。看上去像「跨时钟 FIFO 在硅上不工作」，实际是测量把对象切断了。
    这条现象在逻辑上是**不可能**的（tvalid & tready ⇒ 必写），所以只要把三条观测放在一起
    验一遍自洽性，就该怀疑测量本身。
    **规矩**：
      a) debug tap 只接**非接口**的标量脚（`stat_overflow` / `vsync_n` / `href` / 常量）；
      b) 接口内部状态（tvalid/tready/full/empty）一律由 RTL **自己导出专用 `dbg_*` 输出**；
      c) 已知恒定的观测位放一个**恒 1 / 恒 0 的 sanity 位**，先证明观测通路活着，再信被测位；
      d) 构造完 BD 后**必看 `[BD 41-759]` / `[BD 41-1271]`**——它们的出现说明有脚被摘掉或 tie 0
         而不是连接正确。
