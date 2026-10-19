# 在 Zynq-7020 上搭载 YOLO 的技术调研

> 性质：**技术调研**（侧会话 2026-10-19 应用户要求研究「怎么在 zynq7020 上搭载 YOLO」）。
> 状态：**仍不是裁定**。D004 的**原则**已由用户 2026-10-19 裁定（「把识别判定交给 YOLO 模型」），
> 但**型号 / 输入分辨率 / 类别集合 / 训练来源**这些**参数**仍是 `discussing` —— 本文是这些参数的技术基础，不替用户做选择。
> 纪律：与仓库其余文档相同 —— 一手实测数据与二手说法分开标注，**不编造**。
> 位置沿革（2026-10-19）：本文原落 `.research/`（git 忽略区），D004 原则裁定后由 Lead **迁入本目录入库**；未验证项集中在 §9。
>
> **修订记录（2026-10-19，迁入后一轮独立审计 + Lead 重算）**：修正 **2 处实质性错误**（「INT8 权重 5.6 MB」→ 实为 8.857 MB；
> 「320 分辨率 0.5 GOP / 20 fps」→ 实为 YOLOv3-tiny 3.293 GOP / 3.2 fps，原数字属于另一个模型），
> 新增 **§1.1 可复算的权威量**（参数量 / 体积 / MAC / GOP，与论文反推吻合到 0.1%），
> 并为 2 处口径（RAMB 块数、16% 效率的分母）加了限定。全部细节见 §9。

---

## 0. 结论速览

| 路线 | 在 XC7Z020 上的可行性 | 一句话判定 |
|---|---|---|
| A. Vitis AI DPU（官方 IP） | ❌ **不存在** | DPU IP 只支持 Zynq **UltraScale+**/Versal；DPU-PYNQ 的 21 块支持板**无一块**是 7000 系（§2 有留档，可逐行复核） |
| B. 纯 PS（ARM A9 软件推理） | ✅ 能跑但极慢 | 定点 C 估计 < 1 fps（**估算，本仓库未实测**）；只能当报告里的**基线对照组** |
| C. 全网自定义加速器（ARC2020 风格 RTL） | ✅ 有**同芯片实测**数据 | 416×416 全尺寸 YOLOv3-tiny 实测 **1.88 fps**；降到 224 / 160 分辨率约 6.5 / 12.7 fps（**估算**，见 §3.1） |
| D. 软硬分工（PL 卷积引擎 + PS 前后处理，HLS/RTL） | ✅ **推荐** | 与指南「初级组推荐 PYNQ + HLS/RTL Overlay」完全对口；可复用旧项目摄像头/LCD/OSD 管线 |
| E. FINN 数据流（极端量化 1–8bit） | ⚠️ 官方支持 7020 但装不下 YOLO | Pynq-Z1/Z2 在官方支持表内，但 FINN dataflow 要求权重进片上 BRAM，而 YOLOv3-tiny INT8 权重 **8.857 MB**（§1.1）是片上 BRAM 的 **14.5 倍** ⇒ 放不下 |

---

## 1. 硬件事实（本项目板卡 = 正点原子领航者 ZYNQ 7020，`xc7z020clg400-2`）

> 出处：本仓库 [`../ref/hardware.md`](../ref/hardware.md)（一手逐行引用旧项目 `D:\FPGA\oi` 文档）。

| 资源 | 数量 | 对 YOLO 的含义 |
|---|---|---|
| DSP48E1 | **220** | 乘加吞吐的物理上限；adamgallas 加速器（296 DSP）**超限**，必须折算 |
| BRAM | **4.9 Mb**（= 0.613 MB；按 36 Kb/块折 **139.4 块 RAMB36**） | YOLOv3-tiny 的 INT8 权重 **8.857 MB ≈ 4.9 Mb 的 14.5 倍** ⇒ **绝无可能放片上**，必须走「DDR 权重逐层搬运」（ARC2020 正是这么做的） |
| LUT / FF | 53.2k / 106.4k | 不是瓶颈（同类实现 24–26k LUT） |
| DDR3 | 1 GB，**只挂 PS 侧**，PL 走 AXI HP（64-bit） | 权重/特征图经 AXI HP 往返；Zynq-7000 HP 口原生 64-bit（FINN 源码 `pynq_native_port_width["Pynq-Z2"]=64` 佐证） |
| 摄像头 | DVP OV5640（无 MIPI D-PHY） | 旧项目已有完整采集→LCD 链可复用 |

★ **BRAM 口径**：一手出处只写「4.9 Mb」。**「139.4 块 RAMB36」（旧写「≈140×RAMB36」）是我按 4.9 Mb ÷ 36 Kb 算的推算值**，
原始一手出处未给 RAMB 块数，故已登记在 [`../ref/hardware.md`](../ref/hardware.md) §K UNVERIFIED 清单第 1 条。
算出 139.4（而非整 140）也说明「140」是个**取整后的社区说法**，不是器件常数 —— 引用时写「4.9 Mb」，块数只能写「约 139」并注明推算。

### 1.1 ★ YOLOv3-tiny 的两个关键量（本仓库自行重算，与论文反推吻合到 0.1%）

> 这两个量决定「能不能装下」与「算不算得动」，而调研原文的对应数字有误（见 §9 第 2 条），故在此**给出可复算的权威值**。
> 复算方法：按 darknet `yolov3-tiny.cfg` 的 13 个卷积层逐层累加，
> 每层参数量 = `k²·C_in·C_out`（权重）+ `2·C_out`（BN 的 γ/β）+ `C_out`（卷积偏置）；
> MAC = `k²·C_in·C_out·H_out·W_out`。脚本：`.research/lead_yolo_budget.py`（git 忽略区，可重跑）。

| 量 | 值 | 交叉验证 |
|---|---|---|
| 可训练参数（w+BN+bias） | **8 856 570**（≈8.86 M） | — |
| **INT8 权重体积** | **8.857 MB**（8.446 MiB） | 是板载 BRAM（0.613 MB）的 **14.5 倍** ⇒ 放不下 |
| INT16 / FP32 体积 | 17.713 MB / 35.426 MB | ARC2020 用 16-bit ⇒ 权重 17.7 MB，只能走 DDR |
| **全网 MAC** | **2 782 480 896**（2.7825 GMAC） | — |
| **全网运算量** | **5.5650 GOP/帧**（2 ops/MAC） | 论文反推 `10.45 GOPS × 0.532 s = 5.5594 GOP` ⇒ **比值 1.0010** ✔ |

> ⚠️ 由此**否掉**两种常见说法：① 「YOLOv3-tiny 权重 5.6 MB」—— 见 §9 第 2 条，那是把 GOP 数值错标成 MB；
> ② 「2.8 G MAC ≈ 5.6 GOP」在 @416 **是对的**（正是本表第 4、5 行），但它**不能**顺带推出「权重 5.6 MB」。

**峰值算力**（用于校准预期，非实测）：220 DSP × 2 ops × 时钟 —— **100 MHz ⇒ 44.0 GOP/s；150 MHz ⇒ 66.0 GOP/s；200 MHz ⇒ 88.0 GOP/s**。
⚠️ **「150 MHz」这个时钟值在本仓库没有出处**（`ref/hardware.md` 未记录任何 PL 时钟；全仓库仅本文档自身出现），
它只是**一个示例分母**，不是本项目的时钟计划 ⇒ 引用时必须写成「假设 PL 跑 150 MHz」。

ARC2020 实测持续算力 **10.45 GOPS**，而**分母有三种取法、答案分别是**：
- 若分母 = **220 DSP × 2 × 150 MHz = 66.0 GOP/s**（假设满配 + 假设时钟）⇒ **15.8%**
- 若分母 = **220 DSP × 2 × 100 MHz = 44.0 GOP/s**（用 ARC 实测时钟、满配 DSP）⇒ **23.8%**
- 若分母 = **160 DSP × 2 × 100 MHz = 32.0 GOP/s**（**ARC 自己的配置**，即论文实际用的资源与时钟）⇒ **32.7%**

★ **「约 16%」只在第一种取法下成立，且该取法的时钟无出处。** 三种口径并列，引用时必须写明分母；
**不得只写「效率 16%」** —— 那会把「一半以上的差距来自没有吃满 DSP 与时钟」误读成「架构效率很低」。
**任何 fps 预估都应以 10 GOP/s 量级为实测基数，不要用峰值。**

---

## 2. 路线 A 为什么死了：官方 DPU 与 Zynq-7000

> ⚠️ **证据强度声明（2026-10-19 审计后收紧，务必先读）**：本节只有**第 1 条**有本地一手留档
> （`.research/dpu_pynq_readme.md` = DPU-PYNQ master README 原文，可逐字复核）。
> **第 2、3 条与 §4/§6 中的多处断言，本地无留档、无法复核**（当时读过、但没保存原文；且本机 `web_search` 已因
> HTTP 401 失效、`raw.githubusercontent.com` 等域名 fetch 全部失败 ⇒ 事后也补不回来）。
> ⇒ **本节标题里的「一手核查」只对第 1 条成立**；其余标 `⚠️ 本地无出处`，**报告引用前必须自行复核或降级表述**。
> 审计列出的无留档项共 7 组，见 §9 第 12 条。

- ✅ **有本地留档**：**DPU-PYNQ**（<https://github.com/Xilinx/DPU-PYNQ>，**已归档**，README 首行 `Repository Archived`）：支持表 **21 块板**（`dpu_pynq_readme.md:16-36` 逐行可数）**全部是 UltraScale+/RFSoC/Versal**（KR260、KV260、Pynq-ZU、RFSoC2x2/4x2、Ultra96v1/v2、ZCU102/104/106/111/208/216/1285、Genesys ZU-5EV、T1 Telco RFSoC/MPSoc、TySOM-3A-ZU19EG、TySOM-3-ZU7EV、UltraZed-EG、ZUBoard-1CG），最小 B800/B1600 架构也只给 Ultra96/ZUBoard。README 原文（`:6`）明确「can be ported onto **PYNQ-enabled Zynq Ultrascale+** boards」。
- ⚠️ **本地无出处**：历史回溯到 **v1.2.0（2020-12，与 Vivado 2020.2 同代）**：目标板仅 Ultra96 / ZCU104 / ZCU111 —— 同时代也没有 7000 系。**（当时读过该 tag 的 README，但 `.research/` 未留档 ⇒ 不可复核）**
- ⚠️ **本地无出处**：**Vitis AI** 各版（1.3 / 2.0 / 2.5）的 DPU（B512/B1024/B2304/B3136/B4096）均为 UltraScale+ MPSoC 及以上；`.xmodel` 编译目标里没有 7000 系架构。**（同上）**
- 结论：**「7020 + Vitis AI DPU + model zoo 的 .xmodel」这条路在本芯片上不存在**。任何声称「DPU 跑在 7020」的教程，实际板子要么是 UltraScale+（如 KV260/ZCU104），要么是更古老的 Deephi 时代方案（早已随收购消失）。
  ★ 该结论**不依赖上面两条无留档项**：仅凭第 1 条的 21 板表 + README 的「Ultrascale+」限定即可成立。
- 常见混淆源：KV260 名字里没有「UltraScale」但它是 K26 = Zynq **UltraScale+** MPSoC —— 与 Zynq-7000 完全两代。

---

## 3. 同芯片唯一可靠实测锚点：ARC2020 论文（Yu & Bouganis, Imperial College）

论文：*A Parameterisable FPGA-Tailored Architecture for YOLOv3-Tiny*, ARC 2020, LNCS 12083。
仓库（含论文 PDF 与 thesis）：<https://github.com/Yu-Zhewen/Tiny_YOLO_v3_ZYNQ>
本地留档：`.research/arc2020_yolov3tiny_zynq.pdf` + 抽取文本 `.research/arc2020_paper.txt`（**`.research/` 是 git 忽略区，不入库**）。
⚠️ 因此本文只给**上游 URL**，不给指向 `.research/` 的链接 —— clone 后那条路径不存在（同 `ref/` 的处理口径）。

**实测平台就是 XC7Z020（Zedboard，512 MB DDR3）——与我们同芯片**，其数字可直接当预期锚点：

| 指标 | 数值（论文实测原文） |
|---|---|
| 模型 | YOLOv3-tiny，416×416，**16-bit 定点**（无再训练） |
| 单帧延迟 | **532 ms**（板上实测）= **1.88 fps** |
| 持续算力 | 10.45 GOPS |
| 资源 | 160 DSP · 185 BRAM18 · 25.9k LUT · 46.7k FF（7020 内放得下） |
| 功耗 | 3.36 W |
| 精度损失 | COCO val5k mAP 30.9% vs 33.4%（16bit 定点，−2.5pp） |
| 时钟 | PL 100 MHz / PS 666.7 MHz |
| 方法 | 解析式时延/资源模型做设计空间探索（DSE），层批处理（layer batching）+ DDR 权重流，**不假设片上能放下权重** |
| 对比 | 论文称比同芯片 ARM-only 快 290×；对比表**实测延迟**：Zynq-7035 实现 52ms、XCZU3EG 63ms、ZCU102 25ms、VC707 9.15ms（`arc2020_paper.txt:676-681`） |
| 对比表的资源列（**该实现用了多少**） | Zynq-7035 = **409 DSP** / 787 BRAM18 / 47k LUT / 40k FF / 7.518 W；XCZU3EG = 71.04 GOPS；ZCU102 = 377 DSP / 610.93 GOPS；VC707 = 168 DSP（`arc2020_paper.txt:646-687`） |

★★ **一处必须分清的口径（2026-10-19 审计抓出并修正）**：本文原写「论文称上一代工作在 **Zynq-7035（900 DSP，比 7020 大 4 倍）**上也只有 52 ms」——
**这是错的**。「900」是 **Zynq-7035 的器件容量**，**不是论文里的数**；论文表 2 那一列写的是 **409**（**该实现占用**的 DSP 数），
论文全文检索 `\b900\b` **零命中**。⇒ 现已拆分表述：
- **论文给的**：Zynq-7035 那个实现用 **409 DSP**、延迟 **52 ms**；
- **器件规格给的**：Zynq-7035 容量 **900 DSP** ≈ 7020 容量 220 的 **4.1 倍**（这不是论文的数据，属器件事实）。
★ 同族陷阱：**「容量」与「占用」不能并置相除** —— 7020 的 **220 是容量**、ARC 在同一颗芯片上**只用了 160**；
把「220 容量」与「160 占用」混着算利用率，就会得出错误的效率结论（详见 §1.1 末尾的三分母口径）。

★ **论文自己放弃了 8-bit**：它用「权重损失度量 Weight loss」比较 8-bit 与 16-bit，
某些层的 8-bit 权重损失高达 **70.91%**（`arc2020_paper.txt:571`），故最终选 **16-bit**（`:552-553`）。
⇒ 「换 INT8 就能快一倍」在本项目里**是一个待实测的假设，不是已知结论**。

### 3.1 用这个锚点推算「缩了之后」的预期

YOLOv3-tiny @416 = **2.7825 G MAC = 5.5650 GOP/帧**（本仓库重算，见 §1.1；与论文反推比值 1.0010）。
按实测 10.45 GOPS 线性外推（**估算，须标注为估算**）：

| 配置 | 单帧 GOP | 预估延迟 | 预估 fps | 与本仓库面积线性重算的比对 |
|---|---|---|---|---|
| 416×416（论文实测） | 5.565 | 532 ms | **1.88（实测）** | 5.565 / 532.0 ms ✔ 自洽 |
| 224×224 | ≈1.614 | ≈154 ms | ≈6.5 | 1.614 / 154.2 ms ✔ 一致 |
| 160×160 | ≈0.823 | ≈79 ms | ≈12.7 | 0.823 / 78.7 ms ✔ 一致 |
| **320×320 的 YOLOv3-tiny** | ≈**3.293** | ≈**315 ms** | ≈**3.2** | ⚠️ **旧表写 0.5 GOP / 48 ms / 20 fps —— 错，见下** |
| YOLO-Fastest 级模型 @320（公开数据 ≈0.2–0.3 GMAC，**需自行复测**） | ≈0.5–0.6 | ≈48–57 ms | ≈18–21 | 这是**另一个模型**，不能与上面四行并列比较 |

★★ **旧表第 4 行是一处实质性错误（2026-10-19 修正）**：原文把「0.5 GOP / ≈48 ms / ≈20+ fps」与前三行并列在「YOLOv3-tiny」名下，
但按面积线性（成本 ∝ res²）从 416 锚点推，**320×320 的 YOLOv3-tiny 应为 3.29 GOP / ≈315 ms / ≈3.2 fps** —— 与 0.5 GOP 差 **6.6 倍**。
那行数字只可能属于**另一个更小的模型**（如 YOLO-Fastest 级）。现拆成两行并标明模型不同，避免「320 分辨率能跑 20 fps」的误读。
⇒ 结论：**想在 320 分辨率跑 20 fps，只能换模型（YOLO-Fastest 级），不能靠改分辨率。**

⚠️ **本表除第 1 行外全是线性外推，不是实测**。若换 INT8（论文是 16bit）并吃满 220 DSP（论文只用 160），再有一定上浮空间；但**在报告里承诺 >25 fps@416 是不诚实的**，锚点数据不支持。

---

## 4. 可参考的开源实现（按可信度排序）

| 项目 | 芯片 | 量化 | 资源/性能 | 可信度与借鉴点 |
|---|---|---|---|---|
| [Yu-Zhewen/Tiny_YOLO_v3_ZYNQ](https://github.com/Yu-Zhewen/Tiny_YOLO_v3_ZYNQ)（ARC2020） | **xc7z020**（Zedboard） | 16bit | 532ms / 160 DSP | ★★★ 论文+实测+DSE 框架；裸机、无摄像头、图像编译期写死头文件 |
| [adamgallas/fpga_accelerator_yolov3tiny](https://github.com/adamgallas/fpga_accelerator_yolov3tiny)（2021 Digilent 杯一等奖） | ZU-3EG | INT8 | <50ms；24k LUT / 40 BRAM36 / **296 DSP** | ★★★ 通用卷积加速器（1×1/3×3 conv、查表激活、maxpool），PS 侧调度；**DSP 超 7020 上限，需折算或 LUT 乘法**。作者明示可移植、参考 Angel-Eye 架构 |
| [huanggeli/yolov3tiny-ZYNQ7000](https://github.com/huanggeli/yolov3tiny-ZYNQ7000) | xc7z035（900 DSP） | — | 实时显示闭环 | ★★ adamgallas 的 Zynq-7000 移植 + 双目 OV5642 + HDMI 显示回路；证明该架构在 7000 系跑通，但 7Z035 资源比 7020 大，不能照抄 |
| [a2307588073-arch/zynq-yolov3-tiny-accelerator](https://github.com/a2307588073-arch/zynq-yolov3-tiny-accelerator) | xc7z100 | INT8 8-lane | — | ★★ 54 个 Verilog 文件、AXI4-Stream 数据面 + AXI4-Lite 控制、VDMA、OV5640→HDMI 裸机闭环；许可证梳理规范；**同样是更大芯片** |
| [HuiminKIM/YOLOv2_ZYBOZ720](https://github.com/HuiminKIM/YOLOv2_ZYBOZ720)（Sensors 2025 论文） | **xc7z020**（ZYBO Z7-20） | INT16 | **≈12 s/帧**，非实时 | ★★★ 同芯片诚实反面锚点：纯 RTL 小并行度做 YOLOv2 会慢成什么样；也说明**并行度/DSE 决定生死** |
| [lzxlearner/zynq7010-yolo-accelerator](https://github.com/lzxlearner/zynq7010-yolo-accelerator) | xc7z010（80 DSP） | 定点 | 未给数据 | ★ 无性能证据、无 bit 流验证记录，仅结构示意；**7010 只有 80 DSP，全 YOLO 存疑** |
| [FINN](https://github.com/Xilinx/finn) / [finn-examples](https://github.com/Xilinx/finn-examples) | **Pynq-Z1/Z2 = xc7z020clg400-1**（官方支持表内） | 1–8bit | 仅 CNV/MLP 类小网络有预编译例 | ★★★ 工具链真实可用但**装不下 YOLO**（见 §6）；对 7020 的价值是「官方证明 7020 可跑量化 NN dataflow」+ AXI HP 64-bit 等板级事实 |

另有零星「正点原子 7020 板跑 YOLO」的尝试仓（如 zweng233/zynqforYOLO，0★），无验证数据，不构成证据。

---

## 5. 路线 B：纯 PS 基线的定位

双 A9 @766 MHz + NEON，定点优化 C 实现 YOLOv3-tiny@416 量级在**数秒/帧**（ARC 论文的 ARM 对照被加速器打出 290×；即便认为其 ARM 实现保守，数量级不会低于 1 s/帧）。价值：
1. 报告「优化前后对比表」的第 1 行基线（指南 3.3.5.3 第 4 项明确要求优化过程对比）；
2. 软件金标准（本机 `onnxruntime 1.31.0` 可直接做 PC 侧 golden model，本机无 `torch`/`onnx`，训练与量化时需补装或换机器）。

---

## 6. 路线 E：FINN 为什么不适合（但值得知道）

- FINN 官方 `pynq_part_map` 明确含 `Pynq-Z2 → xc7z020clg400-1`（源码 `src/finn/util/basic.py`，v0.8.1）——**芯片被支持**。
- 但 FINN 的 dataflow 架构把**权重烘焙进片上 BRAM**；而板载 BRAM 只有 **4.9 Mb ≈ 0.61 MB**，
  而 YOLOv3-tiny 的权重（哪怕 INT8）是 **MB 量级**（具体数值见 §9 第 2 条，本文未找到可靠一手出处）⇒ 全网 dataflow 放不下。
  finn-examples 在 Pynq-Z1 上只有 CNV/MLP 这类小网络。
- 工具链现实：FINN 只支持 Docker 内运行（Linux 主机），当前版本要求 Vivado 2024.2，v0.8.1 时代匹配 2020.x——若未来要做 1–4bit 极限量化的小分类头，可以再评估；对「YOLO 检测」不是正路。

---

## 7. 推荐路线 D 的具体形态（与比赛指南逐条对表）

**形态**：PS（PYNQ, Python）负责 预处理(letterbox/量化打包) + 层调度 + 后处理(decode/NMS)；PL 负责 卷积/池化/激活 的**通用量化卷积引擎**（Vitis HLS 2020.2 生成 AXI-Stream IP，或移植 adamgallas 类 RTL 并把并行度折算到 ≤220 DSP），权重与特征图经 AXI DMA + AXI HP 在 DDR 之间流。

| 指南要求（权重） | 本路线怎么吃分 |
|---|---|
| 创新性与应用价值 30 | 沿用旧项目「球场判罚」叙事：YOLO 检测球/人 + 已有 OSD/LCD/回包管线 = 完整应用闭环，而非孤立 demo |
| 性能与资源优化 20 | 有 ARC2020 锚点 + 自测基线（ARM vs PL），优化对比表有真实数据可填；DSE/折叠因子本身就是「优化过程」叙事 |
| 功能正确性与设计完整性 20 | 金标准 = PC onnxruntime 输出 vs 板上输出逐张比对（旧项目已有「实测输出与参考结果比对」习惯） |
| 大模型协作与技能包 15 | 已有 ai/ 三层记录体系；本调研 + 后续移植踩坑天然是 skill 素材（如「7020 上 DPU 不可行的排查记录」「DSP 折叠因子扫描脚本」） |
| 文档质量与可复现性 15 | Vivado 2020.2 batch 脚本 + `DONE_OK` 判据体系已立 |

**与本机工具链的匹配**：Vivado/Vitis/Vitis_HLS **2020.2** 全套可用（HLS 生成卷积 IP 正是其用途）；adamgallas 用 2021.1、ARC 用 2019.1，RTL 跨版本移植可行；PYNQ 镜像（Ubuntu 20.04/kernel 5.4）在领航者板上已被旧项目验证可加载自研 Overlay。

### 7.1 风险与前置死线（上板前就要定）

1. **DSP 折算**：8-lane×INT8 需 296 DSP（adamgallas）→ 7020 须降到 ~6 lane 或部分乘法进 LUT；带宽-算力平衡要用小脚本先扫（ARC 的解析模型思路）。
2. **BRAM 预算**：4.9 Mb 要同时装 输入行缓冲 + 输出 ping-pong + 激活查找表；416×416 全图特征图必爆 ⇒ **逐层分块（tiling）是必做**，不是可选项。
3. **输入分辨率即产品定义**：若题目裁定要求「看清 416 分辨率细节」，性能分就只能按 **~1.9 fps（实测）** 写；
   若接受 **224 ⇒ ≈6.5 fps / 160 ⇒ ≈12.7 fps**（均**估算**），或换 **YOLO-Fastest 级模型**（§3.1 末行，未复测），才可能更高。
   ⚠️ **不要写「6–25 fps」** —— 按 §3.1 的表，**YOLOv3-tiny 自己在任何分辨率下都到不了 25 fps**；
   20+ fps 那个量级**必须换模型**才有依据。**这个取舍必须由用户裁定后写进 D004**。
4. **训练/量化工具链在 PC 侧**：本机 Python 3.13 有 onnxruntime 无 torch/onnx；量化（INT8 PTQ/QAT）需装 torch 或另找训练机——属于环境缺口，应提前补。
5. **后处理不算小**：13×13+26×26 两头 decode + NMS 放 PS；旧项目经验（S4 择优、field-frame 门）表明**后处理逻辑才是这类系统真正的正确性风险区**。

### 7.2 建议的验证阶梯（每级都有机器可读判据，沿用 `DONE_OK` 风格）

1. PC golden：onnxruntime 跑选定模型，逐层输出存 `data/golden/`（判据：逐张 diff < ε）。
2. 量化一致性：INT8 量化模型 vs 浮点模型 mAP/逐层误差（判据：mAP 掉幅 < 阈值）。
3. 单层 HLS：一个 3×3 conv 在 PL 上与 golden 逐元素比对（判据：`CONV_LAYER_OK`）。
4. 全网组装：逐层调度跑通静态图（判据：`E2E_OK` + 与 golden IoU > 阈值）。
5. 实时闭环：接 OV5640 采集 → 检测 → OSD → LCD（判据：`PROGRAM_OK` + 实测 fps 落 `build/reports/`）。

---

## 8. 本次调研的信息源清单（全部 2026-10-19 读取）

- DPU-PYNQ master README（归档声明 + 21 板支持表全 UltraScale+）：<https://github.com/Xilinx/DPU-PYNQ>
- DPU-PYNQ v1.4.0 / v1.2.0 README（历史板表同样无 7000 系）：<https://github.com/Xilinx/DPU-PYNQ/tree/v1.4.0> · <https://github.com/Xilinx/DPU-PYNQ/tree/v1.2.0>
- Vitis AI v1.3 / v2.0 / v2.5 README：edge 目标均为 MPSoC/Alveo：<https://github.com/Xilinx/Vitis-AI>
- ARC2020 论文 *A Parameterisable FPGA-Tailored Architecture for YOLOv3-Tiny*（ARC 2020, LNCS 12083）与代码仓 <https://github.com/Yu-Zhewen/Tiny_YOLO_v3_ZYNQ>；本地留档 `.research/arc2020_yolov3tiny_zynq.pdf` + `.research/arc2020_paper.txt`（git 忽略区，不入库）
- adamgallas 加速器 README（296 DSP、<50ms、INT8、可移植说明）：<https://github.com/adamgallas/fpga_accelerator_yolov3tiny>
- huanggeli ZYNQ7000 移植（7Z035、注意 DSP 型号 DSPE1 适配提示）：<https://github.com/huanggeli/yolov3tiny-ZYNQ7000>
- INT8 8-lane xc7z100 实现（AXI-S 数据面 + AXI-Lite 控制面结构可借鉴）：<https://github.com/a2307588073-arch/zynq-yolov3-tiny-accelerator>
- YOLOv2@ZYBO-Z720（同芯片 7020 的 12s/帧诚实数据 + Sensors 2025 论文 DOI 10.3390/s25206359）：<https://github.com/HuiminKIM/YOLOv2_ZYBOZ720>
- FINN 仓库与 `pynq_part_map` 源码（Pynq-Z1/Z2=xc7z020clg400-1、HP 口 64-bit）：<https://github.com/Xilinx/finn>
- finn-examples README（Pynq-Z1 仅有 CNV/MLP 类小网络）：<https://github.com/Xilinx/finn-examples>
- 本仓库 [`../ref/hardware.md`](../ref/hardware.md)（板卡/资源/引脚一手事实）与 [`repo_spec.md`](repo_spec.md) §6（本机环境事实）。

---

## 9. 未验证项与数字口径（**引用本文任何数字前先读这一节**）

> 状态：**已过一轮独立审计（`SURVEY_AUDIT_OK`，57 行核验表：✅33 / ⚠️19 / ❌1 / 🔶4）+ Lead 重算（2026-10-19）**。
> **修正 3 处实质性错误**（第 2、9、13 条：5.6 MB 单位串用；320 分辨率混入别的模型；Zynq-7035 的 900/409 口径）、
> **加 6 处口径限定**（第 3、10、11、12、13、14 条）。**本节的每一条都指向一个具体位置**，不是泛泛的免责声明。
>
> ★ **审计最有价值的一句话**：ARC2020 的 **12 项数字一个都没抄错**、DPU 21 板表也数得准 ——
> 问题全在**单位串用、出处归属、口径混用**，属「数据搬运正确、口径管理失守」。
> 这类错误比编造数字**更容易被放过**，因为它读起来完全通顺。

1. §3.1 表中**除 416×416 那一行外，全部是线性外推估算**，不是实测。
2. ✅ **已修正（原文错）——「INT8 权重 ≈5.6 MB」**：该数字与「5.6 GOP/帧」同值，是**把 GOP 数值错标成 MB**。
   本仓库自行按 darknet cfg 逐层重算（§1.1）得 **可训练参数 8 856 570 ⇒ INT8 = 8.857 MB**（INT16 = 17.713 MB），
   且该重算的 GOP（5.5650/帧）与论文反推（5.5594）**比值 1.0010** ⇒ 参数表可信。
   ⚠️ 「放不进片上」的结论**方向不变**，且更强：8.857 MB 是板载 BRAM（0.613 MB）的 **14.5 倍**。
3. ⚠️ **口径**：`≈140×RAMB36 / 280×RAMB18` 是**推算**，一手出处未给 RAMB 块数（见 §1 注与 [`../ref/hardware.md`](../ref/hardware.md) §K 第 1 条）。
   按 4.9 Mb ÷ 36 Kb 实算得 **139.4** 块 ⇒ 「140」是取整说法，不是器件常数。
4. YOLO-Fastest 的 0.2–0.3 GMAC 是**社区公开数据，未复测**；§3.1 已把它与 YOLOv3-tiny 的 320 行**拆开**，不可混比。
5. **INT8 相对 16bit 在同 DSP 数下的加速比未实测**；且 ARC2020 明确报告了 8-bit 在若干层的权重损失高达 70.91%（故它选了 16-bit）。
6. adamgallas 的 RTL 向 2020.2 / 7020 的**移植工作量未评估**。
7. §4 表中各开源实现的性能数字**均来自其各自 README/论文，本队未复现**。
8. **本机联网核查受限**：`web_search` 工具在 2026-10-19 实测返回 **HTTP 401 Authentication Fails**，
   故 §2/§4 的外部事实只能凭**当时读取的原文留档**（`.research/dpu_pynq_readme.md`、`.research/arc2020_paper.txt`）作为证据，
   未能当场二次复核 ⇒ 报告引用这些外部结论时，应注明「本队未在本机复现该外部核查」。
9. ✅ **已修正（原文错）——§3.1 的「320 分辨率 ≈0.5 GOP / ≈20 fps」**：按面积线性从 416 锚点推，
   **320×320 的 YOLOv3-tiny 应为 3.293 GOP / ≈315 ms / ≈3.2 fps**（差 6.6 倍）。该行数字只可能属于**更小的模型**。
   ⇒ 已拆成两行并标明「另一个模型」；**「320 分辨率跑 20 fps」是误读，不能靠改分辨率达成，只能换模型**。
10. ⚠️ **口径（三分母）**：「ARC2020 实测算力仅为峰值 16%」这句话**只在分母取 220 DSP × 2 × 150 MHz = 66 GOP/s 时成立**，
    **而「150 MHz」在本仓库没有出处**（`ref/hardware.md` 未记录任何 PL 时钟；全仓库该值只出现在本文档）。
    另两种取法：**220 DSP × 2 × 100 MHz = 44 GOP/s ⇒ 23.8%**；**ARC 自己的 160 DSP × 2 × 100 MHz = 32 GOP/s ⇒ 32.7%**。
    引用时必须写明分母与时钟来源，否则「16%」会误导成「架构效率很低」，而其实一半以上的差距来自**没有吃满 DSP 与时钟**。
11. `.research/lead_yolo_budget.py` 是**可重跑**的重算脚本（§1.1 数字由它产出），但位于 git 忽略区；
    评审要复算时按 §1.1 里的公式自行实现即可（参数 = `k²·C_in·C_out + 2·C_out + C_out`）。
12. ⚠️ **本地无留档的外部断言（7 组，审计逐条列出）** —— 这些是「当时读过原文、但 `.research/` 没存」的：
    ① DPU-PYNQ **v1.2.0** 的板表；② **Vitis AI 各版** DPU 架构清单；③ FINN `pynq_part_map`（v0.8.1）；
    ④ FINN「要求 Vivado 2024.2」；⑤ adamgallas 的 **296 DSP / <50ms**；⑥ HuiminKIM 的 **12 s/帧**；
    ⑦「ARC 用 Vivado 2019.1」（论文 `:82` 只说 Vivado HLS，**全文无版本号**）。
    另有 `xc7z010 80 DSP`、`296 DSP` 同类。⇒ **引用这些时须自行复核或降级为「有资料称」**，不得写成「本队实测/一手核查」。
    ★ 这条与 `docs/repo_spec.md` §6「数字必须标来源」**同源**：没有留档就等于没有出处。
13. ⚠️ **已加唯一引用的两处器件事实**：Zynq-7035 容量 **900 DSP**（≈7020 的 4.1 倍）**来自器件规格，不是论文**；
    论文表里的 **409** 是**该实现的占用**。两者不可混用（详见 §3 的「口径」段）。
14. ⚠️ **参数口径 0.05% 差异（不影响结论）**：本仓库重算得 **8 856 570 参数 / 8.857 MB**，
    独立审计重算得 **8 852 366 / 8.852 MB** —— 差异来自**BN 仿射参数（γ/β）是否计入**。
    **引用时必须注明算法口径**（本文档取「w + BN 的 γ/β + 卷积 bias」）。
