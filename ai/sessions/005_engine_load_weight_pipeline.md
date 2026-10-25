# Session 005 — conv_engine 速度优化：装载裁剪与权重流水

| 字段 | 内容 |
|---|---|
| 会话号 | 005 |
| 日期 | 2026-10-21 |
| 执行 AI（模型 ID） | `group/1101` |
| 执行客户端 / 工具 | `kilo`（CLI，Windows 10 / PowerShell） |
| 角色 | 单人 |
| 任务目标 | 继续 D016 之后的速度优化：定位 `conv_engine` 在板端只有 ~0.056 GOP/s 的原因，并把整网单帧耗时压下来 |
| 上下文投喂 | `docs/accel_interface.md` §7、`src/hls/accel/src/conv_engine.cpp`、`src/ps/host/board_net.py`、`board/logs/board_stream_v5_20261020.md`、`data/results/board_layer_test.json`、`.research/par_tileh1.txt`、`data/models/yolov5n.onnx` |
| **原文位置** | **待导出**：本会话为 CLI 直接进行，`ai/raw/` 的导出脚本只覆盖 `dsh` 客户端；原始对话在本机会话日志中，尚未落成 `ai/raw/2026-10-21_<id>.md` |
| 关联决策 | `D016`（模型/分辨率） |
| 复核方 | 未复核 |

---

## 轮次 1

**输入提示词（逐字）：**

```
现在继续昨天的任务
```

**模型回答要点：**

- 昨天停点：速度优化；`p16/p24/p32` 的 csynth 已证明超预算（DSP 241/353/465，上限 220），`PAR_OUT=16` 不能直接出片。
- 用现有板端数据反推：59 层 PL 卷积 33.35 s（39.09 s 里含 PS 首层 5.75 s），6 个单层用例的读吞吐只有 10–22 MB/s，且与层形状无关 ⇒ 先查装载通路。
- 建模：旧代码恒按 `K_MAX=3` 行、`LB_W_MAX=82` 列装载，596 MB/帧；按 `kh<K` + 运行期列界后降到 77 MB/帧。
- 改动 `conv_engine.cpp`：行守卫、`ncol` 上界、`num_read_outstanding=16/max_read_burst_length=64`。

**我的判断：** 采纳（用户随后批准 csim + csynth）

**理由：** 三个改动都不改数值口径，且 csynth 报告的装载循环本来就是 II=1、迭代延迟 10 拍，先切掉无用装载是最小风险的实验。

---

## 轮次 2

**输入提示词（逐字，选项回复）：**

```
同意，先 csim 再 csynth (Recommended)
```

**模型回答要点：**

- csim：6/6 `CSIM_ALL_OK`（TILE_H=4 与出片配置 TILE_H=1 各一遍）。
- csynth `p8_1_40_ld`：DSP 129（不变）、BRAM 21、LUT 40916、FF 38585；`NUM_READ_OUTSTANDING(16)` 已写进 RTL。
- 代价：LUT 由 35.4K 涨到 40.9K，仍在预算内。

**我的判断：** 采纳

**理由：** 逐位一致 + 资源仍装得下，才允许出片；否则应回到源码改。

---

## 轮次 3

**输入提示词（逐字，选项回复）：**

```
同意，出片并上板实测 (Recommended)
```

**模型回答要点：**

- 导出 `p8_1_40_ld` → 重建 BD → 出 bit（WNS +0.165 ns）→ 上板单帧。
- 实测 **41.4 s → 34.1 s**（conv 39.1→31.9 s）；检测 scores 与旧比特流逐个相同。
- 单层复测：`k1s1_smallw` 7.83→5.93 ms、`k3s1_cin512` 133.45→108.75 ms，聚合 0.0505→0.0622 GOP/s。
- 结论：**输入字节数不是主因**。按每 ci-tile 反推：`k1s1_smallw` 772 拍 = 输入 10 + 权重 ~720 + 卷积 40 ⇒ 权重装载（8×3×3 个未流水的读，每个吃满 DDR 延迟）才是大头。

**我的判断：** 部分采纳（保留改动，但推翻“装载字节数主导”的原假设）

**理由：** 输入装载从 246 字节砍到 10 字节，时间只降 1.3x；而权重装载的拍数与实测逐项对得上。

---

## 轮次 4

**输入提示词（逐字，选项回复）：**

```
同意，跑完整一轮 (Recommended)
```

**模型回答要点：**

- 把权重装载展平成 `i = 0..PAR_OUT*K_MAX*K_MAX`，加 `#pragma HLS PIPELINE II=1`；`wbuf` 改一维，计算侧用展开后的常量下标。
- csim 6/6 通过；csynth `p8_1_40_wld`：DSP 81、LUT 22988、FF 22157（权重循环 II=1，72 次迭代 84 拍）。
- 出片后实现：DSP 162（73.6%）、LUT 13076（24.6%）、WNS +1.254 ns。
- 上板：**34.1 s → 25.7 s**（conv 31.9→23.4 s），检测 scores 仍完全一致。

**我的判断：** 采纳（但明确指出仍未达到 5–8 FPS）

**理由：** 1.33x 的实测增益证明权重装载是真实瓶颈之一；但每 ci 仍有 ~460 拍无法用“循环吞吐”解释，说明剩下的是每次字节访存的固定 ~3 拍开销。

---

## 轮次 5

**输入提示词（逐字）：**

```
OK，继续优化，还有就是10s以内是什么意思单帧吗
```

**模型回答要点：**

- 澄清：10 s 指**单帧整网**（416×416 一张图，端到端，含 PS 首层）。
- 先算下一个杠杆：`TILE_W` 40→104 的模型收益只有 1.08x（因为已经流水化后权重只占一小部分），不值得再出片。
- 转向 PS 首层：5.75 s 的根因是 `int8_conv` 用 int64 matmul（numpy 在 A9 上不给 BLAS 用）。改成 float32 累加——taps×255×128×2 < 2^24 时所有部分和都是精确整数，与 int64 逐位等价。
- PC 单测：taps=108/144/288 三个几何 `maxdiff=0`；416 首层 0.112 s → 0.016 s（PC）。
- 上板：**25.73 → 20.80 s**（conv 23.43 → 18.47 s），scores 仍完全一致。

**我的判断：** 采纳

**理由：** 逐位相等有 `np.array_equal` 判据；上板收益 4.93 s 与首层 5.75→0.8 s 的预期完全对上。

---

## ★ 自我纠错轨迹

| 项目 | 内容 |
|---|---|
| **错在哪**（原始判断） | 第一轮认定“装载字节数是主因”，按 596→77 MB/帧 的模型预测整网 8.4 s→0.94 s（约 8x）。 |
| **怎么发现**（触发条件） | 第一版上板只从 41.4 s 降到 34.1 s；单层 `k1s1_smallw` 的输入从 246 字节砍到 10 字节，时间却只从 7.83 ms 降到 5.93 ms。 |
| **证据** | `data/results/stream_client_v5_fb_ld.json`（timings 34149.5 ms）；`board/logs/board_layer_test_20211118_0633.md`（`k1s1_smallw` 5931548 ns）；按 ci-tile 反推的 772 拍与 `csynth` 里权重循环无独立流水条目一致。 |
| **怎么改**（修正动作） | 不再按“总字节”建模，改为按 **ci-tile 的拍数**分解；把权重装载展平并加 `PIPELINE II=1`，同时把 `wbuf` 改一维（不再让 `complete dim=0` 复制结构）。 |
| **判据**（怎么确认改对了） | 单层 `k1s1_smallw` 5.93→3.62 ms、`k1s1_tailcout` 31.68→20.62 ms；整网 34.1→25.7 s；三次检测 scores 完全一致；csynth 中权重循环 `II=1, latency 84 / 72 triples`。 |
| **根因归纳** | HLS 只对**显式标注 PIPELINE 的循环**做流水；展开在 `ci` 循环体里的散装标量读会各自吃满存储延迟。看 `csynth.rpt` 的循环表时，“没有独立循环条目”本身就是“这段没被流水”的信号。 |

---

## 产出文件

| 路径 | 说明 |
|---|---|
| `src/hls/accel/src/conv_engine.cpp` | 修改：`kh<K` 行守卫、运行期 `ncol`、`num_read_outstanding=16/max_read_burst_length=64`、权重装载展平并流水、`wbuf` 一维化 |
| `board/overlay/conv_engine.bit` / `.hwh` | 重新出片（v2，`p8_1_40_wld` 等价配置） |
| `tools/pynq_net.py` | 修改：`int8_conv` 增加 float32 精确累加快速路径（仅 `2·taps·255·128 < 2^24` 时启用） |
| `board/logs/board_stream_v5_perf_20261021.md` | 新增：四次实测与资源/时序证据 |
| `data/results/stream_client_v5_fb_ld.json` / `..._wld.json` / `..._f32.json` | 新增：PC 侧三轮实测结果 |
| `data/results/hls_csim.json` / `hls_engine.json` | 更新：csim/csynth 汇总 |
| `ai/sessions/005_engine_load_weight_pipeline.md` | 新增：本记录 |

## 未决问题

- 每 ci-tile 仍有约 460 拍无法由循环吞吐解释，怀疑是**每次 8-bit 访问固定 ~3 拍**；需用 32-bit 打包装载验证。
- PS 首层已降到 ~0.8 s（float32 精确累加），Focus + 3×3 拆分的优先级随之下降。
- `PAR_OUT=16` 在 v2 资源下是否可行未重测（v2 实现已用 162/220 DSP）。
- 板端 `board_net.py` 未记录逐层耗时，定位只能靠单层用例反推；后续可在结果 JSON 里落 `slowest_convs`。

## 参考文档出处

| 出处 | 用在哪 |
|---|---|
| `docs/accel_interface.md` §7 | 资源/时序与出片配置的既有结论 |
| `build/reports/timing/conv_engine_bd_timing.rpt` | WNS +1.254 ns @ 83.33 MHz |
| `build/reports/resource/conv_engine_bd_util.rpt` | DSP 162 / LUT 13076 / BRAM 9.5 |
| `build/hls_engine/eng_p8_1_40_wld/.../conv_engine_csynth.rpt` | 权重循环 II=1、72 次迭代 84 拍 |
| `data/models/yolov5n.onnx` | 逐层形状与 MAC 统计（1.888 GOP/帧） |

## 结论与沉淀

**可用结论（带数字）：**

- 板端整网 **41.4 → 20.8 s/帧（1.99x）**，数值逐位不变。
- 装载裁剪（596→77 MB/帧）+ 16 个未完成读：1.21x。
- 权重装载流水：再 1.33x；单层 `k1s1_smallw` 3.62 ms，聚合 0.0846 GOP/s。
- v2 HLS 资源：DSP 81 / LUT 22988；实现后 DSP 162（73.6%）/ LUT 13076（24.6%），WNS +1.254 ns。
- PS 首层改用 float32 精确累加：5.75 → ~0.8 s（int64 matmul 在 A9 上不走 BLAS）。
- 当前瓶颈从“总字节数”转为“每次字节访存的固定拍数”。

**可沉淀为技能包的内容：**

- [ ] → `../../skill/hls-perf/`：HLS 性能排查清单 —— ①先看 `csynth.rpt` 循环表里**有没有独立条目**（没有 = 未流水）；②按 ci-tile 拍数分解而不是总字节；③`ARRAY_PARTITION` 维度写错会静默降级；④展开在循环体里的散装标量读是隐藏的串行点。

**关联记录：** Session 003 / Session 004 / `D016`