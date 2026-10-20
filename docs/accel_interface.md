# 加速器接口契约（conv_engine）

> **本文件是三方共用的唯一真源**：PL 侧 HLS 内核、PC/PS 侧参数导出工具、PYNQ 驱动都必须
> 依照本文件实现。任何一方单方面改动都算违约。
>
> 状态：**v1 草案，已落定数值口径（实测支撑见 §7），寄存器名未冻结**。
> 未定项一律标 `<待实测>`，不得凭空填数。

---

## 0. 为什么需要这份文件

单路跑通的链路是：

```
图像 → PS 预处理 → [逐层: 写参数 → 启动 PL → 等完成 → 取输出] → PS 后处理 → 检测框
                                  ↑
                          PL 只有这一个内核：conv_engine
```

**PL 不保存网络结构**，它是无状态的「一次算一层」的卷积计算器。层序、非卷积算子
（MaxPool/Concat/Resize/Split/DFL）、内存摆放全部由 PS 决定。

因此三件事必须先冻结，否则三方各写各的、集成时必然对不上：

1. **内存布局**（谁按什么顺序摆字节）
2. **定点数值口径**（同样是 int8，怎么算才不会和 PC 上的参考实现差一个 LSB）
3. **参数表内容**（每层要传哪些数给硬件）

---

## 1. 硬件只做一件事

```
acc[c] = Σ_{ci=0..C_IN-1} Σ_{kh,kw}  x_q[ci][h*stride+kh-pad][w*stride+kw-pad] * w_q[c][ci][kh][kw]
         + bias_i32[c]                                    （bias 已含输入零点折叠，见 §3.2）
q_pre  = clamp( round( acc[c] * M[c] * 2^-S[c] ) + zp_pre , 0, 255 )
q_out  = LUT[ q_pre ]                                     （LUT 同时完成激活与输出缩放）
```

**就这些。** 没有 BN、没有残差、没有池化、没有上采样、没有 concat —— 那些都在 PS。

> 为什么池化和 concat 不放 PL：实测（`docs/yolo_on_zynq7020_survey.md` §7）显示它们的
> 数据搬运量远大于算术量，放 PL 只会多一圈 DDR 往返；放 PS 用 numpy 一遍向量化就完了。

---

## 2. 内存布局（NCHW，全部小端）

| 缓冲 | 类型 | 形状 | 说明 |
|---|---|---|---|
| `in`   | `uint8`  | `[C_IN][H_IN][W_IN]` | 激活，**无符号**，行优先连续 |
| `w`    | `int8`   | `[C_OUT][C_IN][K][K]` | 权重，**有符号**；`K*K` 内层按 `kh*K+kw` 展平 |
| `params` | `int32` | `[C_OUT][3]` | 每输出通道 3 个字：`bias_i32`, `M`, `S` |
| `lut`  | `uint8`  | `[256]` | 激活 + 输出缩放合并表 |
| `out`  | `uint8`  | `[C_OUT][H_OUT][W_OUT]` | 输出激活，其中 `H_OUT=(H_IN+2*pad-K)/stride+1` |

`params` 按通道连续摆放：第 `c` 个通道的三个字在 `params[3*c]`、`params[3*c+1]`、`params[3*c+2]`。
**这样 PL 侧一次读 12 字节就能拿到一个输出通道的全部标量**，不会出现同一通道的三个值
分散在三个缓冲里、访存三次的情况。

### 2.1 为什么激活用 uint8 而不是 int8

PC 侧量化验证用的是 **QUInt8 激活 + QInt8 权重**（`tools/int8_sim.py`）。这个组合实测
能被 onnxruntime 正常加载并保持精度（§7），而 QInt8 激活会让 ORT 产出一个自己都拒绝
加载的模型（`output_dtype INT8 does not match y_zero_point type UINT8`）。

**硬件照抄 PC 上已经验证过的口径**，而不是反过来让 PC 迁就硬件。代价是 0：DSP48 支持
无符号×有符号。

### 2.2 边界填充值

零 padding 在 uint8 域里**不是 0，是 `zp_in`**（零点即浮点 0）。PL 内部把越界像素读成
`zp_in`。**不得**用 0 填充 —— 那等于给网络喂了一个偏移量。

---

## 3. 定点数值口径（差一个 LSB 就会逐层累积）

### 3.1 记号

| 符号 | 含义 |
|---|---|
| `sx`, `zx` | 输入激活的 per-tensor scale / zero_point |
| `sw[c]` | 权重的 per-output-channel scale |
| `sp`, `zp_pre` | **激活前**（卷积直出）的 per-tensor scale / zero_point |
| `so`, `zo` | **激活后**（本层最终输出）的 per-tensor scale / zero_point |

### 3.2 bias 的折叠

浮点卷积：`y_f[c] = Σ (x_q-zx)*sx * w_q*sw[c] + b_f[c]`

硬件算的是 `acc[c] = Σ x_q*w_q + bias_i32[c]`，要让它等价于上式：

```
bias_i32[c] = round( b_f[c] / (sx*sw[c]) ) - zx * Σ_{ci,kh,kw} w_q[c][ci][kh][kw]
```

第二项 `Σ w_q` 是权重和，**可以离线算**。折叠之后 PL 只做纯粹的
「无符号 × 有符号 → int32 累加」，**不需要每个像素减零点**。

> ★ 这是本契约里最容易写错的一条。若漏掉 `- zx*Σw`，所有输出会整体偏一个常数量，
> 而由于 `Σw` 每个通道不同，错误表现为**逐通道偏置** —— 看起来像"权重没量化好"，
> 实际是零点没折叠。

### 3.3 requant 乘子

```
M[c] = round( sx * sw[c] / sp * 2^31 )        （int32，Q31 定点）
S[c] = 31 + <使 M[c] 落在 [2^30, 2^31) 的调整量>
```

于是 `acc[c] * M[c] * 2^-S[c] ≈ acc[c] * sx*sw[c]/sp`。

**乘积必须用 64 位中间量**：`acc` 最大约 2^27（最坏 512 通道 × 9 抽头 × 127 × 127），
`M` 约 2^31，直接 32 位相乘会溢出。

**`S[c]` 必须落在 `[1, 62]`**：硬件按 `(acc*M + (1<<(S-1))) >> S` 实现，
`S=0` 时 `1<<(S-1)` 是未定义行为（C++ 里是负数移位），`S>=63` 时 `1<<(S-1)` 超出
int64 有效位。导出侧负责保证；硬件侧对 `S<=0` 做了保护性判断作为第二道防线。

完整算式（**这一条就是硬件的逐位语义，改任何一处都要三处同步**）：

```
prod  = int64(acc[c]) * int64(M[c])            // 有符号 64 位乘积
half  = (S[c] > 0) ? (1 << (S[c]-1)) : 0       // 四舍五入的半个 LSB
shifted = (prod + half) >> S[c]                // 算术右移（对负数向 -inf 取整）
v     = int32(shifted) + zp_pre                // 截断到 32 位后相加（会回绕）
v     = clamp(v, 0, 255)
q_out = LUT[v]
```

> ★ 这里的取整是 **round-half-up（加半个 LSB 再算术右移）**，不是 round-half-even。
> `tools/ref_int8_conv.py`、`sim/scripts/make_hls_case.py`、`conv_engine.cpp`
> 三处必须逐位一致；numpy 的 `>>` 对 int64 也是算术右移，所以三者能对上。

### 3.4 LUT 的生成式（PS 侧）

```python
for q in range(256):
    x_f  = (q - zp_pre) * sp              # 反量化到浮点（激活前）
    y_f  = act(x_f)                       # SiLU / ReLU / 恒等
    q_o  = round(y_f / so) + zo
    LUT[q] = clamp(q_o, 0, 255)
```

`act` 取 `SiLU` 时 `y_f = x_f * sigmoid(x_f)`。YOLOv8 的 SiLU 在 ONNX 里是 `Sigmoid`+`Mul`
两个算子拆开的（见 `data/golden/*.json` 的 `_act_Sigmoid_output_0` / `_act_Mul_output_0`），
这里合并回一个函数。

> **恒等层也必须走 LUT**，不能"跳过 LUT 直接用 q_pre"。原因：`sp ≠ so`（激活前后量程不同），
> 跳过就等于把一个 scale 当成另一个用。

---

## 4. 函数签名（HLS 顶层）

```cpp
void conv_engine(
    const uint8_t *in,      // [C_IN][H_IN][W_IN]
    const int8_t  *w,       // [C_OUT][C_IN][K][K]
    const int32_t *params,  // [C_OUT][3]  = {bias_i32, M, S}
    const uint8_t *lut,     // [256]
    uint8_t       *out,     // [C_OUT][H_OUT][W_OUT]
    int C_IN, int C_OUT,
    int H_IN, int W_IN,
    int K,                  // 1 或 3（v1 只支持这两种）
    int stride,             // 1 或 2
    int pad,                // 0..1
    int zp_in,              // 0..255
    int zp_pre,             // 0..255
    int H_OUT, int W_OUT);  // ★ 实际实现里补了这两个标量
```

> ★ `H_OUT`/`W_OUT` 是写代码时补上的：内核要按输出尺寸分块，而 `H_OUT` 无法由
> `H_IN/K/stride/pad` 在硬件里"顺带"算出来（要多一路整数除法/取整，且容易和
> PS 侧的后处理口径不一致）。**尺寸一律由 PS 算好传进来**，PL 只做校验，不做推导。
> 架构里 PS 侧算 `H_OUT=(H_IN+2*pad-K)/stride+1`，与 ONNX 的 `ceil_mode=0` 口径一致。

- 5 个 `m_axi` 数据端口 + 1 个 `s_axilite` 控制端口。
- 标量参数走 `s_axilite`，**PYNQ 侧按名字访问**（`ip.register_map.C_IN` 等），
  因此本文件**不冻结字节偏移** —— 名字才是契约。
- 完成判据：`ap_done` 拉高（PYNQ 里表现为 `ip.register_map.CTRL.AP_DONE == 1`）。
- **PL 无状态**：每次调用只算一层，不保存网络结构、不保存上一层的输出地址。
  逐层链接由 PS 侧决定（见 `tools/pynq_net.py`）。

---

## 5. 支持的层范围（v1）

| 项 | 取值范围 | 说明 |
|---|---|---|
| `K` | 1, 3 | YOLOv8n 只有这两种（3×3 占 80.9% MAC，1×1 占 19.1%） |
| `stride` | 1, 2 | YOLOv8n 只有这两种 |
| `pad` | 0, 1 | `K=3` 时 `pad=1`，`K=1` 时 `pad=0` |
| `C_IN` | 3..512 | 最大 512（SPPF 的 `cv2`） |
| `C_OUT` | 16..256 | |
| `H_IN`,`W_IN` | 10..320 | 最大 320×320（@320 输入的首层） |

**不在 v1 范围**：dilation、groups、depthwise、K=2/5/7。YOLOv8n 用不到；
若将来换模型再扩，扩的时候必须同步改本表。

> 📌 **D004 = 416 档的影响（2026-10-20 补记，契约不变）**：416 输入时首层
> `H_IN = W_IN = 416` 超出本表上限，且其输出激活 `16×208×208 = 692 224 B` **超过
> 7020 的 BRAM 总量（≈612 500 B）**。处理办法是 PS 按**行带**把首层拆成同一 `cshape`
> 的多次调用（带 2 行 halo），PL 侧对这个拆分无感知、契约不改。推导与备选方案见
> [`integration_rtl_plan.md`](integration_rtl_plan.md) §7 第 6 条。

> ⚠️ 已核实 YOLOv8n 的 64 个卷积**全部落在上表内**，含一个特例：
> `/model.22/dfl/conv/Conv` 的输入被 reshape 成 `[1,16,4,2100]`（4 个 DFL 分箱 × 2100 个锚点）。
> 它是 `K=1, C_IN=16, C_OUT=1, H=4, W=2100` —— **`W=2100` 超出上表**。
> 处理办法：PS 侧把它 reshape 成 `[1,16,84,25]`（或直接逐锚点算）后再调，
> 或干脆用 numpy 算（MAC 只有 0.1 M，占总量 0.01%）。**优先选后者。**

---

## 6. 分块策略（PL 内部，由实现决定，但对外必须满足 §3 的数值）

实现细节（`PAR_OUT` 输出通道并行度、`TILE` 行分块、行缓冲深度）**不属于契约**，
可以随资源/吞吐调优而变。契约只约束**算出来的数**和**读写哪块内存**。

已经实测过的约束（写进代码注释，别重犯）：

- 累加器必须在**寄存器**里（`ARRAY_PARTITION complete dim=1`）。放进 BRAM 会被
  读-改-写 + Bank 冲突串行化，实测慢 2.8×（v10 教训）。
- 回写循环**禁止** `UNROLL`：搬运不是算力热点，展开会让 HLS re-schedule 到综合跑不完（v6 教训）。
- 输入**不要**整层读入片上：早期层 `16×160×160 = 409 600 B`，而 BRAM 总共约 612 500 B，
  一层就吃掉 67%。必须行缓冲流式。
- ★ **行并行度（`TILE_H`）保持 1**。这不是"省一点"，是决定装不装得下的量级差别：
  `TILE_H=4` 会把行缓冲、requant 副本、地址逻辑**各复制 4 份**，LUT 从 66% 涨到 138%。
  而 `TILE_H=1` **不额外增加 DDR 流量** —— 本设计本来就是"每输出行读 K 行"，
  `TILE_H` 取几冗余倍数都是 K。**并行度只用 `PAR_OUT` 一个旋钮控制，不要叠加 `TILE_H`。**
- ★ **累加器"全展开到寄存器"这个直觉是错的**（本仓库实测推翻自己的假设）：
  以为 `acc` 落 BRAM 后地址多路选择器吃掉 6 448 LUT，于是加 `complete dim=0` 全展开，
  结果 LUT 反而从 35 385 涨到 70 928。**真正的成本是副本数，不是分区方式。**

---

## 7. 资源实测（★ 报告"优化前后对比表"的唯一合法数据源）

### 7.1 读这份表之前必须知道的两件事

1. **两代内核的数字不能混用。** `build/hls_probe/` 是早期**简化探针**（`acc_conv3x3`，
   固定几何、只用来扫并行度旋钮）；`build/hls_engine/` 才是**真内核** `conv_engine`
   （运行期几何 + 行缓冲流式 + per-channel requant + SiLU LUT）。两边的 DSP/LUT 含义不同。
2. ★★ **`csynth.rpt` 顶层那列 `Slack` 恒为 `0.00`，它是"目标值"不是"实测余量"。**
   本项目实测：`hls_probe` 四个配置与 `hls_engine` 五个配置，顶层 Slack **全部**是 `0.00`，
   而真实关键路径差别很大。**真实的 WNS 只能来自 Vivado 实现后 `report_timing` 的结果**，
   并且必须写清时钟约束与工具版本（见 `build/reports/` 的读法）。
   把 `csynth.rpt` 的 0.00 当"时序收敛"写进报告，是可以在评审时被一问就穿的。
   （机理：HLS 调度器把每个 II 的路径排进给定时钟，报了 0.00 只说明"按它的调度估计能塞进 10 ns"；
   布线延迟、跨模块路径、IO 约束它都不管。）

### 7.2 真内核 `conv_engine` 的资源收敛过程（10 ns 约束，`xc7z020clg400-2`）

| 配置 | BRAM18 | DSP | FF | LUT | LUT% | DSP% | FF% | 装得下？ |
|---|---|---|---|---|---|---|---|---|
| `p16_4_40` | 42 | 665 (302%) | 82 494 | 99 715 | 187.4% | 302.3% | 77.5% | ❌❌ |
| `p8_4_40`（`TILE_H=4` 基线） | 26 | 169 | 59 489 | 73 644 | 138.4% | 76.8% | 55.9% | ❌ |
| `p8_4_40_a1`（`ACC_FULL=1`） | 26 | 169 | — | — | — | — | — | ❌ |
| `p8_1_40_a1`（`TILE_H=1`+全展开） | 10 | 129 | 57 471 | 70 928 | 133.3% | 58.6% | 54.0% | ❌ |
| `p8_2_20_a1` | 10 | 145 | 63 640 | 78 126 | 146.9% | 65.9% | 59.8% | ❌ |
| ★ **`p8_1_40_a0`（选定）** | **26** | **129** | **36 476** | **35 385** | **66.5%** | **58.6%** | **34.3%** | ✅ |

上限列：BRAM18 = 280、DSP = 220、FF = 106 400、LUT = 53 200。

**选定 `PAR_OUT=8, TILE_H=1, TILE_W=40`**，理由是它把 LUT 压到 66.5%，
给视频输入 / 帧缓存 / AXI 互联**留出约 33% LUT**。这是能同时装下"加速器 + 采集链路"的实测配置。
（原文写「+ 录像链路」，其中 OSD/显示部分已随 D013「不做视频输出」作废，预算余量保留为余裕。）
`PAR_OUT` 暂时冻结在 8；调大它能提吞吐，但会和视频通路抢 LUT —— 要调必须重跑本表并同时核视频侧预算。

> 上表里 `p8_4_40_a1` 的空格不是漏填：那次跑的是 `TILE_H=4`+全展开，与 `p8_4_40` 差异极小，
> 未单独记录完整数字。**不要凭印象补数。**

**★ `TILE_H=1` 下把 `PAR_OUT` 扫到 16/24/32 的结果（这是"为什么冻结在 8"的直接证据）**：

| 配置 | BRAM18 | DSP | FF | LUT | LUT% | DSP% | 装得下？ |
|---|---|---|---|---|---|---|---|
| `p8_1_40` | 26 | 129 | 36 476 | 35 385 | 66.5% | 58.6% | ✅ |
| `p16_1_40` | 42 | 241 | 56 314 | 53 676 | 100.9% | 109.5% | ❌ |
| `p24_1_40` | 58 | 353 | 76 655 | 71 318 | 134.1% | 160.5% | ❌ |
| `p32_1_40` | 74 | 465 | 95 980 | 88 478 | 166.3% | 211.4% | ❌ |

（来源：`build/hls_engine/logs/csynth_p{8,16,24,32}_1_40.log` 的 `CSYNTH_DONE` 行。）

`TILE_H=1` 时 DSP 与 `PAR_OUT` 严格线性：**每 +8 个 `PAR_OUT` 多 112 个 DSP**（14 DSP/`PAR_OUT`）。
`TILE_H=4` 时只需 6 DSP/`PAR_OUT`（`p8`=43、`p16`=91、`p24`=139、`p32`=187）——
**同一个旋钮在两代结构上的代价差一倍多，所以"探针扫出来的 15.30 GOPS 能直接搬到引擎上"是错的**
（见 §7.1 第 1 条与 §7.3）。`TILE_H=1` 之下 `PAR_OUT` 只要超过 8，LUT 就先在 100% 附近撞墙。

⚠️ 这两组数字（`TILE_H=4` 与 `TILE_H=1`）**不能并列比较 DSP**：`TILE_H=4` 的乘法器被
`PAR_OUT × 9` 抽头摊薄后由 HLS 复用，`TILE_H=1` 的结构不同。要比较必须在同一 `TILE_H` 下扫。

### 7.3 早期探针 `acc_conv3x3` 的并行度扫描（保留作参考）

测例 `C_IN=64, C_OUT=128, H=W=40, K=3`（≈ YOLOv8n 的 `model.8`），10 ns 时钟：

| `PAR_OUT` | DSP | LUT% | 周期 | 有效算力 | vs ARC2020 同芯片 |
|---|---|---|---|---|---|
| 8  | 43  | 58% | 4 979 771 | 4.74 GOPS | 0.45× |
| 16 | 91  | 68% | 2 688 051 | 8.78 GOPS | 0.84× |
| 24 | 139 | 78% | 2 151 849 | 10.96 GOPS | 1.05× |
| 32 | 187 | 87% | 1 542 167 | 15.30 GOPS | 1.46× |

（对照基准：ARC2020 在**同一颗** XC7Z020 上 10.45 GOPS / 160 DSP = 0.0653 GOPS/DSP。
本探针 `PAR_OUT=8` 达 0.1102 GOPS/DSP，**每 DSP 效率是它 1.7 倍**；
意思是瓶颈不在结构效率，而在"只用了多少 DSP"。）

### 7.4 实现（P&R）实测：HLS 的 Fmax 估计**不准**，真实约 88.5 MHz

这一节的数据来自 `build/reports/timing/conv_engine_bd_timing.rpt`（Vivado 2020.2，
`xc7z020clg400-2`，整条 BD：PS7 + SmartConnect + conv_engine + proc_sys_reset）。

| 项 | 值 | 出处 |
|---|---|---|
| 约束时钟 | `clk_fpga_0` 周期 10.000 ns（= 100 MHz，首次尝试） | 汇总表 |
| **WNS** | **−1.247 ns** | 同上 |
| TNS / 失败端点 | −94.074 ns / 226（总 77 469） | 同上 |
| WHS / 保持余量 | +0.036 ns（保持无违规） | 同上 |
| 结论行 | `Timing constraints are not met.` | 同上 |
| 最差路径数据延迟 | 10.952 ns（logic 5.501 / route 5.451，布线占 49.8%） | 同上 |
| 最差路径逻辑级数 | 22（CARRY4=16, LUT6=2, LUT3=2, LUT2=1, LUT4=1） | 同上 |
| 时钟路径偏斜 / 不确定度 | −0.200 ns / 0.154 ns | 同上 |
| **反推真实 Fmax** | **≈ 1/(10 + 1.247 − 0.354) ≈ 88.5 MHz** | 本文件按上式算得，非工具输出 |

最差路径的起止点（逐字）：
`Source: system_i/conv_engine_0/inst/mul_64s_32s_64_5_1_U14/conv_engine_mul_64s_32s_64_5_1_Multiplier_3_U/buff0_reg[-1111111105]__1/C`
→ `Destination: system_i/conv_engine_0/inst/p_mid1_reg_26830_reg[45]/D`，
全部落在 §3.3 的 requant 段（`conv_engine.cpp` 的 `ap_int<48> a48 * b48 → ap_int<64> prod`
→ `+half → >>pm_shift` → `+zp_pre` → 查 LUT）。

★ **必须记住的教训：HLS `csynth` 报的 `Estimated Fmax` 与实现后的真实 Fmax 差 55%**
（`csynth` 说 136.99 MHz，Vivado 布线后 88.5 MHz）。`csynth` 不管布线延迟，
`route` 占了最差路径的 49.8%。**报告里引用 Fmax 必须注明是"HLS 估计"还是"实现后实测"**，
两者不能混用——这与 §7.3 的"探针数字不能当引擎数字"是同一类错误。

**当时的处置（记录决定与代价，不记录"以后再说"）**：
把 FCLK0 从 100 MHz 降到 **83.333333 MHz**（1000/12，Zynq 标准分频点），
给最差路径留约 +0.75 ns 余量、保住 83% 算力。
理由：先把单路端到端跑通、拿到**实测周期数与逐位正确性**，再谈提频。
要真正提回 100 MHz，正确做法是**重构 requant 的 48×48 乘法**（拆成 18×18 DSP 组合，
或让乘法跨 2 拍流水），**不是把时钟硬拉高**。

**降到 83.33 MHz 后的复跑结果（已实测，`build/reports/timing/conv_engine_bd_timing.rpt` 重生成）**：

| 项 | 100 MHz（首次） | **83.33 MHz（当前）** |
|---|---|---|
| 约束周期 | 10.000 ns | **12.000 ns** |
| WNS | −1.247 ns ❌ | **+0.025 ns ✅** |
| TNS / 失败端点 | −94.074 ns / 226 | **0.000 ns / 0** |
| WHS / 失败端点 | +0.036 ns / 0 | +0.014 ns / 0 |
| 结论行 | `Timing constraints are not met.` | **`All user specified timing constraints are met.`** |
| 端点总数 | 77 469 | 77 491 |

⇒ 时序**已收敛**，`board/overlay/conv_engine.bit`（4 045 674 B）与
`conv_engine.hwh`（285 760 B）即基于该配置产出，可上板。
⚠️ 但 **+0.025 ns 是"刚好过线"**：它是同一配置的确定性结果（布局布线固定），
可复现；只是**没有任何温度/电压裕量论证**。报告里引用必须连这句一起写。

### 7.5 吞吐：目前**没有**可信的整网周期数

- `csynth.rpt` 的 `Latency (cycles)` 是按 `LOOP_TRIPCOUNT` 上限算的**界**，不是实测。
  探针因为几何全固定（64/128/40 写死），那个界恰好等于真实值；
  而 `conv_engine` 的几何是运行期参数，`TRIPCOUNT` 只能给区间 ⇒ 印出来的
  `1.7e10` / `5.1e10` 这类数字**没有物理意义，禁止引用**。
- 要拿到可信周期数，只有两条路：`cosim_design` 跑 RTL 协同仿真，或上板用 AXI Timer 实测。
- 在此之前，fps 只能按 `tools/fps_feasibility.py` 的"GOPS 反推"口径给出**估算区间**，
  并必须在报告里标明是估算。

---

## 8. 变更纪律

1. 改本文件的任何数值口径（§2 布局、§3 公式），必须同步改三处实现并重跑
   `tools/ref_int8_conv.py` 的逐位比对。
2. 新增支持的算子（dilation/groups/…）必须同步扩 §5 的表。
3. `PAR_OUT`、`TILE` 这类**实现参数**不进契约，改动不需要改本文件。

---

## 9. 测试向量格式（csim 用，机器可读）

PL 侧的正确性判据是「和一份**独立实现**逐位相等」，不是「看起来差不多」。
量化链路没有"近似对"，差 1 就是 requant 或 bias 折叠写错了。

### 9.1 用例目录

由 `sim/scripts/make_hls_case.py` 生成到 `data/layers/cases/<case>/`：

| 文件 | dtype | 形状 | 内容 |
|---|---|---|---|
| `meta.txt` | ASCII 文本 | — | `key=value` 行，见 9.2 |
| `in.bin` | uint8 | `[C_IN][H_IN][W_IN]` | 输入激活 |
| `w.bin` | int8 | `[C_OUT][C_IN][K][K]` | 权重 |
| `params.bin` | int32 | `[C_OUT][3]` | `{bias_i32, M, S}` |
| `lut.bin` | uint8 | `[256]` | 激活表 |
| `out_ref.bin` | uint8 | `[C_OUT][H_OUT][W_OUT]` | **独立 numpy 实现**的输出 |

全部为**小端、无头部、纯裸数组**（`np.ndarray.tofile` 默认行为）。

### 9.2 `meta.txt` 的键

`name C_IN C_OUT H_IN W_IN K stride pad zp_in zp_pre H_OUT W_OUT`。
`#` 开头的行是注释。C 侧 TB 用 `fopen` + `atol` 解析，**保持纯 ASCII**。

### 9.3 为什么 `out_ref` 不是 TB 自己算的

TB 如果和 RTL 用同一份逻辑算期望值，两边一起错也全绿 —— 比对就没有判别力。
因此 `out_ref` 由 `sim/scripts/make_hls_case.py` **按本文件 §1/§3 独立重写一遍**
（numpy，显式模拟 int32 回绕、64 位乘积、算术右移、LUT），与 RTL 只共享**文字契约**。

`tools/ref_int8_conv.py`（真实量化参数 + 真实层）是**第三份**实现，用来抓
"两份实现 + 文字契约同时读错"这种最难发现的情况。

### 9.4 用例选择的原则

覆盖**边界**而不是"多生成几个"：

| 用例 | 覆盖什么 |
|---|---|
| `k3s1_c3_40` | 最常见的 3×3/s1/p1（YOLOv8n 里 39 层是这个形状） |
| `k1s1_tailcout` | 1×1；且 `C_OUT=20` 不是 `PAR_OUT(8)` 的整数倍 ⇒ 尾块补齐分支 |
| `k3s2_c16_40` | stride=2 降采样（`model.1/3/5/7`）；行/列索引都要乘 stride |
| `k1s1_smallw` | `W_OUT=10` 远小于列分块宽度 ⇒ `wo_abs<W_OUT` 守卫 |
| `k3s1_cin512` | `C_IN=512` 上限（`model.9` 是 512 入）⇒ ci 长循环不溢出 |
| `k3s1_pad0` | `K=3` 且 `pad=0`（输出比输入小 2）⇒ 无 padding 边界 |

### 9.5 `M`/`S` 的取值不是随便取的

`sim/scripts/make_hls_case.py` **由 `acc` 的实际量级反推 `M`/`S`**，让输出真的铺满
`0..255`（只留少量钳位验证 clamp 分支）。第一版随便取 `M∈[2^30,2^31)`、`S∈[25,40]`，
结果 `v` 全部撞到 0/255 的钳位，于是"算错了"和"算对了"输出一模一样 ——
失配率只有 40%，差点把"bias 漏加"这个真 bug 误判成"钳位正常"。

> ★ 这是本仓库第二条"判据必须先能失败"的实例：**一个测试如果在有 bug 时也能通过，
> 它就不是测试。**

### 9.6 跑法

```bash
python sim/scripts/make_hls_case.py --all
python src/hls/accel/scripts/run_csim.py --par 8
```

判据三层，缺一不可：Tcl 层 `CSIM_DONE <tag> OK`、TB 层 `TB_CONV_OK <case> <n>`、
汇总层 `CSIM_ALL_OK <n>`。**`csim_design` 成功只说明"跑完了"，不等于"算对了"。**
