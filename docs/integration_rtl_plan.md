# 整机 RTL 集成计划（D004 档：416×416 输入）

> 状态：**v1 草案（2026-10-20）**。上游契约见 [`accel_interface.md`](accel_interface.md)
> （`conv_engine` 的数值口径已冻结）；输入分辨率取 D004 = **416×416 精度优先档**。
> 本文只写「PL 侧要做什么、为什么这么分、每步怎么验」，**不含实现细节**（实现细节进代码注释）。

---

## 0. 这份文件回答什么

D004 定了 416 之后，整机 PL 不再只是「包一层 `conv_engine`」，而要补齐：

1. 摄像头 → DDR 的采集链路（OV5640 是 DVP，没有 MIPI/HDMI in，见 `architecture.md` §硬件限制）；
2. DDR → HDMI 的显示/叠加链路（检测框要上屏，演示才立得住）；
3. PS 侧逐层调度所需的 DDR / DMA / 寄存器基础设施。

三个问题的答案分别是 §1（数据通路）、§2（分工理由）、§6（验收判据）。

---

## 1. 数据通路

```text
OV5640 DVP（RGB565，640×480@30，pclk 24 MHz）
   │  pclk 域
   ▼
vid_dvp_capture ── AXIS 24bit RGB888（tuser=帧首，tlast=行尾）──┐
   │                                                          │
   ▼                                                          │
inf_axis_async_fifo（pclk → 100 MHz，格雷码指针）              │
   │                                                          │
   ▼                                                          │
AXI DMA（S2MM）── burst 写 ──► DDR 帧缓冲（N 个 slot，带写指针）│
                                   │                          │
                                   ▼  取帧（PS 或 PL）
                     ┌─────────────────────────────┐           │
                     │ PS（PYNQ python）           │           │
                     │  · letterbox 416×416（灰边）│           │
                     │  · 量化成 int8 NCHW         │           │
                     │  · 逐层调 conv_engine       │           │
                     │  · 后处理 → 检测框          │           │
                     │  · 写 OSD 描述符（AXI-Lite） │           │
                     └─────────────────────────────┘           │
                                   │  OSD 描述符表              │
                                   ▼                          │
DDR 帧缓冲 ──► vid_frame_reader（AXI4 MM 读 → AXIS 1280×720）──┘
                                   │
                                   ▼
                        vid_osd_boxes（按描述符画矩形框）
                                   │
                                   ▼
                        vid_hdmi_tx（720p60 时序 + TMDS）
                                   │
                                   ▼
                                 HDMI 显示器
```

**离线评测与现场演示共用同一条 PL 通路**：离线时由 PS 把视频文件的帧写进同一个
DDR 帧缓冲，PL 侧从 `vid_frame_reader` 往后完全一样。这样「离线指标」和「现场演示」
不是两套代码，报告里可以互相印证。

---

## 2. PL / PS 分工（含理由）

| 环节 | 谁做 | 为什么 |
|---|---|---|
| DVP 采集、字节对拼、RGB565→888 | **PL** | 时序由摄像头时钟决定，PS 无法实时跟 |
| letterbox 到 416×416、量化 int8 | **PS**（numpy） | 每帧只做一次 resize+pad，numpy 一遍就完了；放 PL 要额外 line buffer + 定点坐标口径，7020 的 BRAM/DSP 更应该留给卷积 |
| 卷积层 | **PL** `conv_engine` | 唯一有算力收益的部分（契约见 `accel_interface.md`） |
| MaxPool / Concat / 上采样 / DFL | **PS** | 搬运量远大于算术量（`docs/yolo_on_zynq7020_survey.md` §7 实测），放 PL 只会多一圈 DDR 往返 |
| NMS / 阈值 / 后处理 | **PS** | 需要分支与排序，PL 化收益低 |
| OSD 框图元绘制 | **PL**（PS 只写描述符） | 每帧几十个矩形；PL 按扫描线画，零 DDR 开销 |
| HDMI 时序与 TMDS | **PL** | 必须硬时序 |

> **决定 1（缩放放 PS）**：416 只做一次/帧，PS 侧实测成本是几 ms 量级（上板后落实到
> `report/` 的性能表）；换来的是一整块 line buffer 和一套「坐标口径与 numpy 对齐」的
> 调试债——不划算。
>
> **决定 2（OSD 只传描述符）**：PS 每帧只写 ≤32 条 `{x,y,w,h,color}`（AXI-Lite 突发写），
> 不碰像素。这样显示链路带宽与框数量无关。

---

## 3. 模块清单与当前状态

| 模块 | 目录 | 状态 | 判据 |
|---|---|---|---|
| `vid_dvp_capture` | `src/rtl/video/` | ✅ 已实现 + TB 通过 | `VID_DVP_CAPTURE_TB_PASS`（`make sim`） |
| `inf_axis_async_fifo` | `src/rtl/common/` | ⬜ 下一个 | 跨时钟往返、满/空、复位不丢标志 |
| `vid_frame_reader` | `src/rtl/video/` | ⬜ | AXI4 读 burst 波形 + 行/帧计数正确 |
| `vid_osd_boxes` | `src/rtl/video/` | ⬜ | 4 个框逐像素与参考图一致 |
| `vid_hdmi_tx` | `src/rtl/video/` | ⬜ | 彩条 720p60 上屏稳定 10 min |
| `inf_clk_wiz` / `inf_reset_sync` | `src/rtl/common/` | ⬜ | 各域复位释放顺序正确（异步断言、同步释放） |
| BD 集成（PS7 + DMA + conv_engine + 视频链） | `build/scripts/` | ⬜ | 综合/实现日志 `DONE_OK`，JTAG 下载 `PROGRAM_OK` |

**DMA 用 Xilinx AXI DMA（Scatter-Gather）IP，不自写**：S2MM 的 out-of-order/对齐/中断
都是坑，自写只在「必须省 LUT」时才值得；7020 的 LUT 要留给卷积。

---

## 4. 时钟 / 复位 / 跨时钟域

| 域 | 频率 | 用途 |
|---|---|---|
| `pclk` | 24 MHz（随 OV5640 XVCLK 分频） | DVP 采集 |
| `axi_clk` | 100 MHz | AXI HP0 / DMA / `conv_engine` 的 `m_axi` |
| `pixel_clk` | 74.25 MHz | 720p60 显示 |

- 复位：PS `fclk_reset0_n` 异步进 PL → `inf_reset_sync` 按域同步释放（异步断言、同步释放）。
- CDC 只允许经 `inf_axis_async_fifo`（AXIS 语义）或 Xilinx IP；**禁止手写打两拍传多比特**。
- DVP 域的 `stat_overflow` 等状态位本身就是电平信号，读侧同步后即可。

---

## 5. 带宽与资源（估算，非实测——上板后替换成实测值）

| 项 | 估算 | 来源/口径 |
|---|---|---|
| 采集写 DDR | 640×480×3 B × 30 = **27.6 MB/s** | 计算值 |
| 显示读 DDR | 1280×720×3 B × 60 = **166 MB/s** | 720p60 RGB888 |
| 416×416×3 int8（一层输入） | **519 KB** | 计算值 |
| 整网 DDR 往返总量 | **待实测** | 用 `tools/pynq_net.py` 统计每层尺寸后累加 |
| HP0 可用带宽 | **待实测**（7020 的 DDR3 32bit@533 理论 1.06 GB/s） | 上板 `devmem`/DMA 基准 |

> 采集+显示合计约 194 MB/s，对 HP 口不算紧；真正的风险在 PS 侧逐层搬运
> （`conv_engine` 每层都要把输入从 DDR 读一遍、输出写一遍）。这条必须在 M3 实测，
> 不许用理论值写进报告（`docs/repo_spec.md` §5：数字必须标来源）。

---

## 6. 里程碑与判据（先定「什么算通过」，再动手）

| 里程碑 | 内容 | 通过判据（可失败） |
|---|---|---|
| **M1 采集** | DVP → AXIS → DMA → DDR 帧缓冲 | ① `make sim` 全绿；② 板上连续 1000 帧 `stat_overflow=0`；③ 帧率 30 fps ±1%（用帧计数/秒表算，不靠感觉）；④ 静态彩条图抽检 ≥100 像素与 PC 参考逐字节相同 |
| **M2 显示** | DDR → HDMI + OSD | ① 彩条 720p60 上屏 10 min 无闪断；② PS 写 4 个框，上屏位置与写入值逐像素一致（截图比对）；③ 满屏移动框无撕裂（双缓冲） |
| **M3 卷积集成** | BD 集成 `conv_engine` + PS 逐层调度 | ① 单层输出与 `csim`（`accel_interface.md` §9 用例）逐字节相同；② 整网 416 单帧输出与 PC onnx 参考逐层 `cos > 0.999`；③ 端到端单帧耗时实测落 `report/` |
| **M4 实时闭环** | 摄像头 → 检测 → OSD 上屏 | ① ≥ N fps（N 由 M3 实测吞吐反推，先写「待定」）；② 连续 10 min 无死机/漂移；③ 演示视频存证 |

> **反例先写**：每条判据都必须「能失败」。例如 M1 的 ④ 若只用「能出图」当判据，
> 色序接反、字节错位都能通过——所以必须逐字节比。

---

## 7. 未决问题（需要裁定或实测）

1. **帧缓冲像素格式**：RGB888（3 B/px）还是 RGB565（2 B/px）？显示端 OSD 需要 888
   才好看，采集端 565 更省带宽——是否在 `vid_frame_reader` 里做 565→888 展开？
2. **letterbox 灰边值**：PS 侧填的灰必须与量化输入口径一致（是 114？还是 0？还是
   `zp_in`？）。PL 不碰这个值，但**报告里要写清楚**，否则「416 精度优先」的分数
   无法复现。
3. **双缓冲/三缓冲**：`vid_frame_reader` 与 PS 写检测结果的撕裂风险，需要几级。
4. **HDMI TX**：用现成 TMDS 开源核（沿用旧项目）还是手写；手写则先出彩条再出图像。
5. **OV5640 配置**：I²C(SCCB) 配置序列走 PS 还是 PL？先按 PS（PYNQ 有现成库）验证，
   出问题再移 PL。

---

## 8. 下一步（按序）

1. `inf_axis_async_fifo` + TB（CDC 往返、满/空、复位）；
2. `vid_frame_reader` + TB（AXI4 读骨架，先用简化宿主模型）；
3. 彩条 + `vid_hdmi_tx`（先上板出彩条，再做 OSD）；
4. BD 集成 `conv_engine`（`accel_interface.md` 已冻结，直接按契约接）；
5. M3 整网，按 §6 判据逐条验收。

> 顺序理由：先把「链路上每个模块单独可验」打通，最后才做 BD 集成——BD 出问题时的
> 定位成本最高，不能把未验证的模块一次性塞进去。