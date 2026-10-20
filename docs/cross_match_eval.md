# 跨比赛可迁移性评估框架（D013-a）

> 结论先说：**现有颜色/门控结论的全部证据只来自 `migu_5106947698` 一场球**。
> 这个工具把「换一场还剩多少」变成一条可复算的命令 + 一组**能失败的判据**，
> 而不是跑完让人自己看数字。
>
> 📌 **本文讲「框架怎么用、判据怎么定、为什么这么定」；实测跑出来是什么、结论是什么，见
> [`cross_match_results.md`](cross_match_results.md)**（两者分工，不要混写）。

工具：`tools/cross_match_eval.py`（配套闸门 `tools/palette_probe.py`）
输出：`data/results/cross_match.json`（**不覆盖** `data/results/hi_color_diag.json`）

---

## 1. 为什么需要这个评估（以及**不是**因为什么）

### 1.1 必要性来自「只验过一场球」这个事实本身

`docs/hi_layer_color_gate.md` 里所有分层占比（purity 37.1% / color 44.4% /
too_small 14.7% / rect 3.8%）、以及「四种取色失败原因非球员率差 9 倍」这条机制，
全部来自 `migu_5106947698` 的 12 段事件切片（640×360 @25fps，红/白队服，固定转播机位）。
**一场球的占比，没有证据能外推到另一场球**——这才是需要跨比赛评估的理由。

### 1.2 ★ 明确不引用 §6.4 的 kappa

`docs/hi_layer_color_gate.md` §6.4 那组一致率数字（kappa ≈ 0.226、加权差 −0.168、
16-of-37）是**按错对齐算出来的**：`data/annot/labels.csv`（73 行）与
`labels2.csv`（159 行）里的 `box_idx` 只是 `tools/make_player_labels.py:238`
处 `enumerate(items)` 的**层内下标**，而 `items` 的长度随 conf 门限变化
（第一批 conf=0.08、第二批 conf=0.25），所以 `(clip, frame, box_idx)` **不是唯一键**
（labels.csv 只有 61 个唯一三元组，labels2.csv 只有 115 个）。按三元组对齐会凑出
37 个「重叠框」，按 `(clip, frame, conf, h_px)` 真正同框只有 5 个、conf 与 h_px
都对得上的只有 1 个。

因此：

- 本框架**不引用** §6.4 的 kappa 作为「标注噪声下界」；
- 也**不据此**推断「现有结论存疑」——那套数字本身不可用，正反都不能当证据；
- 本框架**完全不读** `labels.csv` / `labels2.csv`（只读 `hi_color_diag.json` 的占比，
  且占比是**现读**不是手抄）。标注在本文中只以「层内非球员率」四个**常数**出现（§6），
  没有任何框级对齐，因此 `box_idx` 非唯一这件事对本工具零影响。

### 1.3 一个我们自己实测的、同样说明「一场不够」的数字：单段噪声

旧场 12 段里随便取 3 段，purity 层占比分别是 **48.7% / 41.6% / 33.9%**——
**同一场比赛、同一支球队、同一个机位**，单段之间就摆 ±8 pt。
而旧基线是 12 段的**汇总值**。所以「新比赛抽几段」直接对「旧场 12 段汇总」做对照，
本身就带抽样偏差；这是本框架用 `G1 n_hi ≥ 2000` 卡最小样本量、并要求看
`clips[].reason_share_pct` 逐段分布的原因（§7 诚实边界第 1 条）。

---

## 2. 输入 / 输出契约

### 2.1 输入 1：新比赛视频目录（`--video-dir`）

自动识别两种形态，**不需要你预处理**：

| 目录形态 | 判定 | 抽段方式 |
|---|---|---|
| 一个（或几个）大 mp4 | 目录总时长 > `n_clips × clip_len` | `windowed`：每段视频在时间轴上均匀取窗口，起点按 `(k+0.5)·dur/n` 交错 |
| 一堆小 mp4（事件切片） | 目录总时长 ≤ `n_clips × clip_len` | `single_pass`：每段视频整段跑一遍 |

- 视频后缀：`.mp4 .mkv .avi .mov .ts .flv .webm`；按**解码出的真实时长**排序（不是文件名）。
- 已遇到过的坑：下载器**中间态**（目录里一个 2.6 GB 的正式名 mp4 + 一个 `.ytdl`）
  探测时会给出 9621 s 这种怪时长。判据因此分两级：
  - 目录里有 `*.ytdl` ⇒ 打印 `[skip]` 跳过超长文件，剩下的照跑；
  - 没有 `.ytdl` 但总时长超 `--max-total-s`（默认 1800 s）⇒
    `CROSS_MATCH_FAIL unexpected_video_set ...`（防手滑扫到整个下载目录）；
  - 全部文件都解不出帧 ⇒ `CROSS_MATCH_FAIL no_decodable_video ...`。
- **不要在下 `*.ytdl` 还在时跑真数据**。

### 2.2 输入 2：`--palette`（人工录入，颜色**不写死**）

```
--palette A色,B色        例：--palette red,white（默认）/ red,blue / blue,black
```

- 对应 D013 的 **LCD 输入方案**：队服颜色由人看画面给定，不靠自动识别。
  默认 `red,white` 只对旧比赛成立；**新比赛（大连英博海发 vs 深圳新鹏城）颜色未知，不要假设还是红白**。
- 合法色名来自 `PALETTE_REF`：`red orange yellow green cyan blue purple magenta white black`。
- 给了非法色名 / 两色相同 ⇒ `CROSS_MATCH_FAIL bad_palette ... / palette_duplicate ...`。

### 2.3 输入 3：可选参数（默认值 = 旧基线口径）

| 参数 | 默认 | 说明 |
|---|---|---|
| `--n-clips` | 2 | 抽几段（旧基线用 12）；**自检模式固定 2 段，此参数不生效** |
| `--clip-len` | 20.0 | 每段时长（秒） |
| `--stride` | 8 | 每 N 帧取 1 帧（与基线一致） |
| `--imgsz` | 640 | 检测输入尺寸（与基线一致） |
| `--model` | `data/models/yolov8n.pt` | |
| `--native` / `--no-native` | 开 | 是否额外跑「缩到 640 宽**重新检测**」的对照布局 |
| `--max-total-s` | 1800 | 输入总时长上限（0 = 不限） |
| `--out` | `data/results/cross_match.json` | 输出路径 |
| `--allow-indistinct-palette` | 关 | 色板可分辨性闸门未过时仍继续（JSON 的 `palette_probe` 留痕） |

### 2.4 输出：`data/results/cross_match.json`

顶层字段：`contract / palette_input / palette_probe / params / thresholds /
baseline / clips[] / totals_scaled_640 / verdict_scaled_640 / landscape /
warns[] / fails[] / conclusion[] / caveats[] / elapsed_s`。

其中 `clips[i]` 是**逐段**证据（这是判读的主力，不是汇总值）：

```json
{"clip_id":"inc_01_t01154.9","label":"...","video":"...","start_s":0.0,"dur_s":30.0,
 "n_hi":913,"n_unknown":156,"unknown_rate":0.1709,
 "reason_counts":{"too_small":25,"purity":76,"rect":6,"color":49,"colored":757},
 "reason_share_pct":{"purity":48.72,"color":31.41,"too_small":16.03,"rect":3.85},
 "hi_h_px":{"median":38.0,"p10":..,"p90":..},"landscape":"on_par","elapsed_s":..}
```

`landscape` 用框高分布粗分档（`on_par / tiny / mixed / wide / very_wide /
no_usable_tier / empty`），作用是**给漂移一个机制解释**，不下「是不是球员」的结论。

---

## 3. 两种布局，为什么必须都有

旧基线是**640 宽输入**上量的。新视频若直接把 1280/1920 宽的帧喂进检测器，
框会变大、`too_small` 层自然消失——但这时变化同时来自**尺度变了**和**队色变了**，
两者被混在一起，占比漂移无法归因。

所以默认跑两遍：

- `scaled_640`（表中叫「缩放至 640 宽」）= 把帧缩到 640 宽后**重新检测并重新取色**。
  这是**与旧基线同口径**的那一条，跨场漂移应当以它为主判据。
- `windowed`（表中叫「原生」）= 直接把本片喂进检测器（不缩放），是新比赛的真实输入。
  它和 scaled 的差距，就是「画面尺度变了」这一项。

> 注意：`scaled_640` 是"把帧缩放后重新检测"，**不是**把框坐标除以比例——检测器在
> 不同尺度上给出的框集合本就不同，坐标等比缩放不等价。

---

## 4. 分层口径（与旧基线逐字一致）

逐框走 `tools/color_prior.py::_classify_box(frame, bb, palette, why)`，
`why[0]` 即原因码：

| 原因码 | 含义 | 判定条件（真源在 `tools/classify_players.py`） |
|---|---|---|
| `colored` | 取到颜色、判进 A/B 队 | `role ∈ {A,B}` |
| `too_small` | 框高 < `MIN_H_PX`(22) —— **信息不足** | `h_px < 22` |
| `rect` | 连不出躯干矩形 | `torso_rect() is None` |
| `color` | 躯干区域取不到色 | `torso_color() is None` |
| `purity` | 躯干区域非草坪像素占比 < `MIN_PURITY`(0.30) | |

`n_unknown = n_hi − colored`；`reason_share_pct[k] = 100·count_k / n_unknown`。
检测用 `classes=[0]`（person）、`conf=HI_CONF`(0.25)、`iou=0.45`、`imgsz=640`。

---

## 5. ★ 判据：占比漂移多少算「不可迁移」

**判据先能失败**（§8 有实测的正/负对照）。所有阈值都在 `tools/cross_match_eval.py`
的真源区，改一处即可复算。

| 码 | 判据 | 阈值 | 级别 | 为什么是这个值 |
|---|---|---|---|---|
| **G1** | 样本量 `n_hi` | `< 2000` ⇒ **FAIL** | FAIL | 旧基线 12 段 12585 框（≈1049/段）。实测 2 段只有 1625 框，占比噪声已到 ±8 pt 级，低于此数得出的结论不可信 |
| **G2** | 单层占比的**绝对**漂移 | `|Δpt| ≥ 15 pt` ⇒ WARN | WARN | 旧场自己的单段波动是 ±8 pt，15 pt ≈ 2×；低于它分不清「换场」与「抽样」 |
| **G3** | 单层占比的**相对**漂移 | `|Δ| / base ≥ 50%` ⇒ WARN（主导层报进 max_drift） | WARN | 小层分母小（rect 3.8%），单看绝对 pt 会漏；50% 是「这一层的构成已经换人了」 |
| **G4** | `unknown_rate` 漂移 | `≥ 10 pt` ⇒ WARN | WARN | 旧基线 0.206；取色整体可用度掉 10 pt 就是链路失效 |
| **G5** | 门控映射是否还成立 | 反推 `too_small` 层内非球员率 `> 0.80` ⇒ WARN `gate_mapping_likely_broken` | WARN（致命） | 见 §6 |
| **D** | 退化检查 | 所有层 `max |rel| < 10%` ⇒ WARN `degenerate_shares` | WARN | 防「占比几乎没动」但其实是没量到东西 |

判读规则（写在代码里，不靠人记）：

1. 任一层触发 **G3（相对 50%）** ⇒ 该层占比**不可迁移**。
2. **G2（绝对 15 pt）** 是兜底：G3 在小基线上容易触发、G2 在大基线上容易触发，两者都报。
3. 两个布局都算，`max_drift` 取**较大者**报进令牌——判据以最坏的那个为准。
4. 令牌形如：

```
CROSS_MATCH_OK <n_clips> <n_hi> <max_drift>            # 无 WARN
CROSS_MATCH_OK <n_clips> <n_hi> <max_drift> WARN(G2;G3;G4)
CROSS_MATCH_FAIL <reason>                              # 例：n_hi_too_small 913<2000
```

> **G3 比 G2 更早触发**（实测负对照里 G3 报了 5 条、G2 只报 2 条）⇒ 相对漂移是主导判据，
> 绝对漂移是兜底。这不是设计时的猜测，是跑出来的。

---

## 6. 门控检验（G5）：层内非球员率能不能从占比反推回来

旧结论里最有价值的一条是「四种取色失败原因，层内非球员率差 9 倍」
（`docs/hi_layer_color_gate.md` §6.1，第二批 159 框）：

| 层 | 层内非球员率 θ |
|---|---|
| `purity` | 0.974 |
| `rect` | 0.676 |
| `color` | 0.436 |
| `too_small` | 0.129 |

本工具把 θ 当**常数**用，做的事是：**只看各层占比，能不能反推出这个 θ 结构**。

- 固定层法：`fixed = Σ_{purity,rect,color} share_k·θ_k`，
  `implied_θ_too_small = (est_nonplayer_rate − fixed) / (share_too_small/100)`
- `est_nonplayer_rate` = 基线 `reason_share_pct` 加权得到（旧基线 0.5997）。
- 判据：`implied_θ_too_small > 0.80` ⇒ `gate_mapping_likely_broken`。
  即「要让总数成立，`too_small` 这一层得有 80% 以上是非球员」——这与旧场实测的
  0.129 差 6 倍以上，说明层的含义在新比赛上可能整体错位了。

> 设计时的两个坑（都已修，留作记录）：
> 1. 第一版把 `est/too_small占比` 直接当 θ，忘了减掉其他三层的贡献 ⇒ **任何输入都报 broken**
>    （连旧场自己 3 段也报）。修好后旧场自检反推值 **0.129**，与旧场 θ 完全一致。
> 2. θ 是**旧比赛**的纯 PyTorch 模型下标注出来的，而 `hi_color_diag.json` 的 `model` 字段
>    是 `yolov8n_calib_int8`。所以 `gate_check.status = consistent` 分不清
>    「层含义没变」和「两个模型在这里给出同样的占比」。这是已知的混淆，见 §7。

---

## 7. 诚实边界（读结论前必须先看）

1. **无真值**：量的是取色失败原因的**构成**，不是「球员判得准不准」。
   要判准不准，必须对新比赛重新标注（而且按 `(clip, frame, conf, h_px)` 对齐，不要用 `box_idx`）。
2. **单段噪声**：旧场 12 段的单段纯度占比是 48.7/41.6/33.9%，±8 pt。
   新比赛抽几段 vs 旧场 12 段汇总，误差来源不对称。
3. **抽样偏差**：旧基线用的是**事件切片**（忙的画面），本工具抽的是**随机 20 s 窗口**。
   前者天然更「忙」、更多非球员/广告牌/远景。
4. **θ 跨场可搬性本身待验**：§6 的四个 θ 来自旧比赛。若层含义变了，G5 报警，但
   目前**无法**在没有新标注的情况下定量判断。
5. **G5 的模型混淆**：见 §6 末尾。
6. **判据是启发式**：G2/G3 的 15 pt / 50% 来自旧场单段噪声的两倍，不是统计检验。
   它保证「明显漂移会被抓到」，不保证「不报就是可迁移」。
7. **色板可分辨性是前置条件**：见 §9。

---

## 8. 自检模式与判据自检（实测）

### 8.1 自检是「管线通」，不是「可迁移」

```
D:\python\python.exe tools/cross_match_eval.py --self-check
```

- 用 `data/samples/incidents` 的**前 2 段**（任务书要求，`--n-clips` 在此模式不生效）。
- **不套用** G1–G5：同一场比赛跟自己对基线做漂移检验**本身没有判别力**
  （测出来只是抽样噪声），若照套 G1 会必然 `n_hi_too_small 1625<2000`，
  把「按任务书只跑 2 段」这个正常状态误报成失败。
- 自检要证的是：数据流、四层计数、并排对照、门控检验、色板闸门、两种布局**全部走过一遍**。
  `--self-check` 时 `--palette` 照常生效（默认 red,white）。

实测输出：

```
[1/2] inc_01_t01154.9@0.0s   n_hi=913   unk=156 share={"purity":48.72,"color":31.41,"too_small":16.03,"rect":3.85} h=38.0
[2/2] inc_02_t01370.6@0.0s   n_hi=712   unk=161 share={"purity":41.61,"color":44.10,"too_small":9.32,"rect":4.97} h=37.3
RESULT_JSON D:\zynq-yolo\data\results\cross_match.json
  -- 原生（windowed） --
     layer          new%    base%    drift       rel status
     purity        45.11    37.10    +8.01     +22% PASS
     color         37.85    44.41    -6.56     -15% PASS
     too_small     12.62    14.68    -2.06     -14% PASS
     rect           4.42     3.81    +0.61     +16% PASS
SELF_CHECK_OK pipeline n_clips=2 n_hi=1625 clips_ok=2 reasons_ok=2/2 gate=consistent scale_probe=ok
CROSS_MATCH_OK 2 1625 0
```

`SELF_CHECK_OK` 这行**故意不放 max_drift**：同一场比赛对自己算出来的漂移只是抽样噪声，
放进自检令牌容易被误读成"可迁移性结论"；它只出现在主令牌的第三位与并排表里，且不套判据。

两点说明：
- 前 2 段是 640×360 的切片，缩到 640 宽是**恒等变换**，所以两种布局数字一致——这是对的，
  不是 bug。
- 表里的 `PASS` 在自检模式下**不代表结论**（同场无判别力），只是判据函数的输出。

### 8.2 判据先能失败 —— 正/负对照（都在旧场素材上跑，可复算）

| 场景 | 命令 | 结果 |
|---|---|---|
| **G1 断（样本量）** | `--video-dir data/samples/incidents --n-clips 1` | `CROSS_MATCH_FAIL n_hi_too_small 913<2000`，exit=2 |
| **G1 断（2 段）** | 同上 `--n-clips 2`（= 脚本默认） | `CROSS_MATCH_FAIL n_hi_too_small 1625<2000` |
| **正对照（判据通过）** | `--n-clips 3`，palette red,white | `CROSS_MATCH_OK 3 2659 0.402`，全部层 < 15 pt 且 < 50% |
| **负对照（错色板）** | `--n-clips 3 --palette blue,orange` | `CROSS_MATCH_OK 3 2659 0.834 WARN(G2;G3;G4)`，14 条 WARN |

负对照逐层（这是「判据会不会抓到漂移」的关键证据）：

| 层 | new% | base% | drift | rel |
|---|---|---|---|---|
| purity | 11.97 | 37.10 | **−25.1 pt** | **−68%** |
| color | 81.45 | 44.41 | **+37.0 pt** | **+83%** |
| too_small | 5.03 | 14.68 | −9.65 pt | **−66%** |
| rect | 1.56 | 3.81 | −2.25 pt | **−59%** |

外加 `unknown_rate` 0.640 vs 0.206（**+44.5 pt**）⇒ G4。
把队色换成「完全不对的两个色」之后，四层**全部**触发、未知率翻三倍——
说明判据抓得住「队色换了」这个最核心的迁移失败模式。
（注意：负对照是**故意用错色板**，这正是新比赛最容易发生的操作失误，见 §9。）

---

## 9. 色板可分辨性闸门（`tools/palette_probe.py`）

> **色板先能分辨，再谈迁移。** 这是本框架最重要的一课。

`classify_players.classify()` 只卡 `best < MAX_COLOR_DIST`(0.16) 与
`best−次佳 ≥ MIN_MARGIN`(0.04)，**没有**「跨过 `MAX_COLOR_DIST` 的像素绝不能分到 A/B」
这条约束；而 `distance_to_ref()` 对白/黑用 (1−S, V)、对彩色用色相为主，两组判据不在同一空间。
后果是**静默误判**，不是崩溃。2026-10-20 在新比赛视频上实测（`torso_color` 原样调用、逐像素过 `classify`）：

| 场景 | 结果 |
|---|---|
| 深蓝球衣躯干 × palette `{white,black}` | **39.8%** 像素被判成**白色** |
| 白球衣躯干 × palette `{white,blue}` | **97.0%** 像素被判成**蓝色** |
| 白球衣躯干 × palette `{white,black}` | 49.2% 判白（正常） |
| 深蓝球衣躯干 × palette `{red,blue}` | 9.4% 判蓝（偏低，S 被绿草反射拉高） |

两次误判都**没有报错、没有 unknown**，直接给出错误的分队结果。这属于本仓库
反复踩过的「判据假绿」：程序没崩，但结果毫无意义。

所以主脚本开跑前先跑探针：

- **sibling margin**：两队颜色互相有多像（一方的参考色在另一方判据下的距离），
  `min(d_AB, d_BA) ≥ 0.10` 才算分得开（`MAX_COLOR_DIST` 的 0.625 倍以上，留出 margin 空间）。
- **场景色误判**：草坪（h01≈0.25,S 0.55,V 0.55）、深蓝球衣、黄、紫、**白球衣的真实像素**
  （h01≈0.50, S 0.16, V 0.75）喂给**生产同一个** `classify()`，若被判进 A/B 且
  到该队参考色距离 > `1.5×MAX_COLOR_DIST` ⇒ 假确定性 ⇒ FAIL。
- 未过闸门 ⇒ `CROSS_MATCH_FAIL palette_indistinct ...`（exit=2）；
  显式 `--allow-indistinct-palette` 可放行，结论留在 JSON 的 `palette_probe`。

实测判定（`python tools/palette_probe.py <A> <B>`）：

```
red,white   => PALETTE_PROBE_OK
red,blue    => PALETTE_PROBE_OK
white,blue  => PALETTE_PROBE_OK
red,black   => PALETTE_PROBE_OK
white,black => PALETTE_PROBE_FAIL palette_indistinct sibling A:white|B:black margin=0.034<0.100
```

`white,black` 被挡下是有道理的：白黑两类的判据都落在 (S,V) 平面上，实测白球衣像素
（S≈0.157, V≈0.749）与黑球衣像素（S≈0.329, V≈0.427）都能**同时**挤进两类各自 0.16 的门内，
再叠加 `MIN_MARGIN` 0.04 的重叠区就会判成「两队都像」。

---

## 10. 新比赛到了之后怎么跑

```powershell
$env:PYTHONIOENCODING='utf-8'
cd D:\zynq-yolo

# 0) 确认下载真的完成：目录里不应再有 *.ytdl
Get-ChildItem D:\yt-dlp\downloads\migu_968460165 -Force

# 1) 先看片识色：抽几帧人工看队服颜色（不要假设还是红白）
#    已知（2026-10-20 人工核帧）：大连英博海发 = 白衣，深圳新鹏城 = 深蓝衣，门将浅紫
#    ⇒ 建议 --palette white,blue

# 2) 跑跨场比赛评估
#    ★ 整场录像（2h39）必须加 --play-windows：不加就在整条时间轴上均匀抽窗口，
#      会抽到广告/演播室/看台特写（实测 7453 s 那帧草坪占比 0.00），
#      那些画面本来就没有「球员框」，却会被算进取色失败原因的构成里。
D:\python\python.exe tools\cross_match_eval.py `
  --video-dir "D:\yt-dlp\downloads\migu_968460165\<全场 mp4>" `
  --palette white,blue `
  --n-clips 12 --clip-len 20 --play-windows --max-total-s 0

# 3) 读令牌
#    CROSS_MATCH_OK 12 <n_hi> <max_drift> [WARN(...)]
#    再打开 data\results\cross_match.json 看 clips[] 逐段分布和 conclusion[]
```

`n_clips=12` 是为了与旧基线的 12 段对齐（但**不是**同一类抽样：旧场是事件切片，新场是随机窗口，见 §7.3）。

---

## 11. 已知的下一步（不在本次交付内）

1. **给新比赛做小批量标注**（≥60 框，按 `(clip, frame, conf, h_px)` 对齐），
   才能把「占比」换算成「精确率」——本框架的量是构成，不是准度。
2. 若 G5 报 `gate_mapping_likely_broken`，需要用统一模型重跑 `hi_color_diag.json`
   （消除 §6 的模型混淆），否则分不清是「层含义变了」还是「模型换了」。
3. `classify()` 的「跨过 MAX_COLOR_DIST 绝不能判 A/B」这条约束应当补进
   `tools/classify_players.py`，而不是长期靠探针兜底。

---

## 12. ★ 实测：新比赛（migu_968460165）跑出来的结果

> 这一节是**第一次真的换场**之后的记录。命令、数字、判据全部可复算；
> 结论只有一条，但它是有代价的：**颜色先验不能直接搬到另一场球。**

### 12.1 素材与命令

| 项 | 值 |
|---|---|
| 比赛 | 2026 中超第 28 轮 大连英博海发 3–1 深圳新鹏城 |
| 视频 | `D:\yt-dlp\downloads\migu_968460165\2026中超第28轮全场回放_….mp4`（854×480 @25fps，9554.6 s，1.85 GB） |
| 色板（人工录入） | `white,blue` —— 大连英博海发 **白**、深圳新鹏城 **深蓝**（用户 2026-10-20 确认） |
| 布局 | `--play-windows`：草坪占比粗扫 478 点 → 288 点判为比赛画面 → **21 段**、合计 3680 s（占全片 38.5%）→ 在这 21 段内抽 12 个 20 s 窗口 |

```powershell
D:\python\python.exe tools\cross_match_eval.py `
  --video-dir "D:\yt-dlp\downloads\migu_968460165\<全场 mp4>" `
  --palette white,blue --n-clips 12 --play-windows --max-total-s 0
# => CROSS_MATCH_OK 12 7886 0.371 WARN(G2;G3;G4)
```

### 12.2 数字（并排对照；基线 = 旧场 12 段）

| 布局 | n_hi | unknown_rate | purity% | color% | too_small% | rect% |
|---|---|---|---|---|---|---|
| **基线** `migu_5106947698`（640×360 事件切片） | 12 585 | **20.6%** | 37.10 | 44.41 | 14.68 | 3.81 |
| 新场 · 原生（854×480，h 中位 43.5） | 7 886 | **47.7%** | 37.88 | 60.87 | 1.04 | 0.21 |
| 新场 · 缩放到 640 宽（h 中位 33.5） | 7 862 | **54.4%** | 30.77 | 54.94 | 10.83 | 3.46 |

判据（代码里 G1–G5，`docs/repo_spec.md` §5 的「先能失败」口径）：

- 原生布局：`color` **+16.5 pt**（G2 触发）、`too_small` **−93%**、`rect` **−94%**（G3 触发）、
  `unknown_rate` **+27.1 pt**（G4 触发）。
- 缩放布局：占比四项**全部 PASS**（color +10.5 pt / too_small −3.9 pt / rect −0.35 pt），
  但 `unknown_rate` **+33.8 pt**（G4 仍然触发）。
- 门控检验 G5：`consistent`（too_small 占比 1.0%，反推层内非球员率 0.129 ≤ 0.80）。

⇒ 读法：**「取色失败原因的构成」在缩放尺度上一半能保住（G2/G3 全 PASS），
但「整体取色成功率」跨场塌了近 30 pt（G4 两个布局都触发）。**
前者说明门控的分层逻辑没被推翻；后者说明**颜色这一路本身**换场就退化。

### 12.3 那 30 pt 是什么？—— 用 `tools/kit_calib.py` 量出来

`PALETTE_REF` 的参考色是**按旧场素材校准过一次的**（257 个躯干样本）。新场实测：

| 队服 | `PALETTE_REF` 预置 | 新场实测（229 个躯干样本，h≥40 px） | 差异 |
|---|---|---|---|
| 白（大连英博） | S 0.08 / V 0.95 | **S 0.067 / V 0.935**（n=120） | 基本吻合 |
| 深蓝（深圳鹏城） | H 230° / S 0.70 / V 0.70 | **H 215° / S 0.298 / V 0.435**（n=109） | **饱和度差 0.40、亮度差 0.27** |

球衣不是「标准蓝」而是**深色低饱和藏青**（实测 S≈0.30、V≈0.43），而
`distance_to_ref` 对彩色用 `0.8·Δhue + 0.2·Δs` ⇒ 每像素白拿 0.2×0.40 ≈ **0.08 的常数距离**，
在 0.16 的闸门里就吃掉了一半预算。**同一批像素、只换参考色的结果：**

| 口径 | colored | unknown |
|---|---|---|
| `PALETTE_REF` 预置值 | 61.1% | 38.9% |
| 按本场实测中位数校准 | **74.2%** | **25.8%** |

⇒ `unknown_rate` 的塌陷里，**约 13 pt 可直接归因于「参考色没按这场球校准」**
（38.9% → 25.8%，接近旧场 20.6% 的量级；剩下 5 pt 是机位/构图/光照的真实差异）。

> ⚠️ **不要把 13 pt 当「校准方案已验证」**：`tools/kit_calib.py` 的簇中位数来自
> 「离谁近就归谁」的初判，初判偏差会被继承；它量的是**取色成功率**，不是分队正确率。
> 真实可用的校准必须用**人工确认的像素样本**（这正是 D013 的 LCD 录入方案），
> 本文只把量级量清楚：**色板参考色是「按场」的量，不是全仓库常数。**

### 12.4 对本项目意味着什么（D013 / D013-a）

1. **D013 的「LCD 录入队色」是必需的，不是锦上添花**：不做按场录入+校准，
   颜色这一路在新比赛会退化到 ~50% 取色失败，「用颜色提升球员识别」无从谈起。
2. **门控逻辑本身可以跨场**：缩放布局上四层占比全 PASS、G5 一致 ⇒「按取色失败原因
   分层」这套**判据**没被推翻；塌的是**取色成功率**这一前置环节。
3. **下一步该做的不是调阈值，而是按场校准色板**（`tools/kit_calib.py` 已给出量级与做法）。
   **已做（§12.6）**：接回链路重跑后 unknown_rate 降 9.5/9.4 pt、G2 消失、
   缩放布局四层占比与旧基线最大差 1.59 pt；但 **G4 没有归零**（仍 +17.6/+24.4 pt），
   残留部分是「白色球衣原始像素本身就不满足 (S≤0.30, V≈0.95)」这个 0.16 闸门问题，
   不是校准能修的。

### 12.5 这一节自身的诚实边界

- 新场**没有人工真值**（≥60 框标注尚未做），所以这里只说「取色成功率/构成」，不说准度。
- `--play-windows` 是**采样级**判据（20 s 一档）：窗内仍可能有广告插播，
  命中率 288/478 点已如实写进 `cross_match.json` 的 `play_windows` 段。
- 新旧场的抽样方式不同（旧场＝事件切片，新场＝比赛段内均匀窗口），
  §7.3 的抽样偏差**依然存在**。

### 12.6 ★ A/B：把「按场校准」真的接回链路，看 G4 掉多少

§12.3 只是**换参考色重算同一批像素**（离线口径）。这一节是**真的接回链路重跑**：
`cross_match_eval.py` 新增 `--palette-refs`，只替换参考色、**不动任何阈值**
（`MAX_COLOR_DIST=0.16` / `MIN_MARGIN=0.04` 原样），同一批 12 段窗口、同一模型。

```powershell
D:\python\python.exe tools\cross_match_eval.py `
  --video-dir "<全场 mp4>" --palette white,blue `
  --palette-refs data\results\kit_calib.json `
  --n-clips 12 --play-windows --max-total-s 0 `
  --out data\results\cross_match_calibrated.json
# => CROSS_MATCH_OK 12 7886 0.276 WARN(G3;G4;degenerate_shares)
```

| 指标 | 预置参考色（§12.2） | 按场校准（本节） | Δ |
|---|---|---|---|
| unknown_rate · 原生 | 47.7% | **38.2%** | **−9.5 pt** |
| unknown_rate · 缩放到 640 宽 | 54.4% | **45.0%** | **−9.4 pt** |
| 原生布局 `color` 层占比 | 60.87%（基线 44.41，G2 触发） | **51.10%**（G2 不再触发） | −9.8 pt |
| 原生布局 `max_drift`（G3 主导判据） | 0.371 | **0.276** | −0.095 |
| 缩放布局 `max_drift` | 0.237 | **0.097** | −0.140 |
| 缩放布局四层占比 | 全 PASS（color +10.5 pt） | **全 PASS，且最大偏差 1.59 pt**（purity +0.08 / color +1.14 / too_small −1.59 / rect +0.37） | — |
| 判据 WARN | `G2;G3;G4`（5 条） | `G3;G4;degenerate_shares`（5 条，G2 消失） | — |

**怎么读这三行**：

1. **校准确实有效，但只补回约 1/3**：G4 从 +27.1/+33.8 pt 降到 +17.6/+24.4 pt。
   剩下这 17–24 pt 不是「色板没校准」，而是**白色球衣在 854×480 的原始像素里本来就不是
   「低饱和高亮度」**（`palette_probe.py` 里记的实测：白球衣真实像素 S 0.16–0.33、V 0.75–0.96，
   到 white 参考 (S 0.08, V 0.95) 的距离可达 0.4，远超 0.16 闸门）。
   校准色板**无法**修这一项——那是 0.16 闸门与「白在不同光照下不是一个点」的冲突。
2. **缩放布局的表现最有说服力**：把两边都归到 640 宽同一观测尺度后，
   四层占比与旧基线的最大偏差只有 **1.59 pt**，`max_drift` 0.097。
   ⇒ **「按取色失败原因分层」这套构成关系是跨场可迁移的**；不迁移的是「绝对取色成功率」。
3. **`degenerate_shares` 这条 WARN 是「好消息被工具如实标注」**：它的含义是
   「所有层相对漂移都 < 10% ⇒ 这个对照已经没有判别力」，即缩放布局**测不出漂移**。
   不能读成「判据失败」，也不该拿它当「完全一致」的证据——它是「没差异」的告警。

仍需诚实说明的两点：

- 原生布局的 `G3`（`too_small` −91% / `rect` −93%）是**小层的相对漂移**：
  绝对值只有 1.30% vs 14.68%、0.27% vs 3.81%，而原生布局 h 中位 43.5 px、
  缩放到 640 宽后 33.5 px ⇒ 这层差异主要来自**画面尺度**，不是颜色。
- 校准参考色仍来自 `kit_calib.py` 的「离谁近就归谁」初判（§12.3 的诚实边界不变）。
  真正产品化要用 D013 的 LCD 人工确认像素样本；本 A/B 证明的是**这条路值得走**，
  而不是「校准已完成」。

### 12.7 第四批标注包（新比赛 · 跨场）已生成，等人工标

§12.2/§12.6 量到的都是**取色失败原因的构成**，没有真值。要回答「门控在新场还成不成立」，
必须在新场上人工标一批**同样按原因分层**的框 —— 工具 `tools/make_newmatch_labels.py`：

```powershell
D:\python\python.exe tools\make_newmatch_labels.py --build `
  --video "<全场 mp4>" --palette white,blue `
  --palette-refs data\results\kit_calib.json --slug migu968460165
# => NEWLABEL_PACK_OK 83 20 20 20 3 20

# 标（网页，键盘 p/n/u；这批约 5 分钟）
D:\python\python.exe tools\label_web.py --port 8765 --batch 4
# 标完：
D:\python\python.exe tools\make_newmatch_labels.py --score
```

候选框分布（12 窗 × stride 12，共 5 316 个框；**与跨场评估逐框等价的分层口径**）：

| 层 | 候选数 | 占比 | 抽了几个 | 旧场同层占比（§12.2 基线） |
|---|---|---|---|---|
| `colored` | 3 258 | 61.3% | 20 | 79.4% |
| `purity` | 968 | 18.2% | 20 | 7.6% |
| `color` | 1 055 | 19.8% | 20 | 9.1% |
| `too_small` | 32 | 0.6% | 20 | 3.0% |
| `rect` | **3** | 0.06% | **3** | 0.8% |

> 口径说明：候选列是「占全部检测框的百分比」；旧场列**换算过**才有可比性——
> 基线的四层占比（37.10 / 44.41 / 14.68 / 3.81）是**在 unknown 里**分的，
> 乘上 unknown_rate 20.6% 才变成「占全部 hi 框」（7.6 / 9.1 / 3.0 / 0.8），
> `colored` 则是 100% − 20.6% = 79.4%。直接拿 37.1% 与本表 18.2% 比是错的。

两件值得记的事：

1. **交叉验证对上了**：候选框里 unknown 合计 2 058/5 316 = **38.7%**，
   与 §12.6 按场校准评估的 38.2% 基本一致（两个工具 stride 不同、实现独立）
   ⇒ 说明 calibration 的结果不是单点偶然。
2. **新场的 `rect` 层几乎没有样本**（候选只有 3 个）：`rect` = 躯干矩形切不出来
   （框太小/太扁）。旧场 rect 层占 0.8%，新场 0.06% —— 这不是抽样不够，而是
   **854×480 的原生画面里框高更大、躯干矩形更容易切出来**。所以第四批**无法**估计
   新场 rect 层的非球员率（n=3），只能给 purity / color / too_small / colored 四层。

`--score` 的判据写在工具里：**层间保序**（旧场 purity > rect > color > too_small，
新场若是 purity > color > too_small 即保序）+ 每层 N 率与 Wilson 95% CI 并排。
未标完时它明确报 `not_all_labeled 0/83` 而**不给数字**（`docs/repo_spec.md` §5）。

### 12.8 取色口径 v2 的实测：unknown 降到 23.7%，但**层的含义变了**

`tools/classify_players.py` 有两套距离口径：**v1**（默认，旧比赛全部结论赖以成立）与
**v2**（2026-10-20 新增：把色相偏离改成"角度配额"并把亮度 V 纳入；门限
`MAX_COLOR_DIST_V2=0.70` 的语义是"允许多大相对色相偏离"，不是把 0.16 调大）。
跨场评估可用 `--color-metric v2` 切换，同一批 12 段窗口、同一模型、同一按场校准色板：

| 配置 | unknown · 原生 | unknown · 缩放 | 原生四层占比（purity / color / too_small / rect） | `max_drift` | 判据 WARN |
|---|---|---|---|---|---|
| v1 + 预置色板（§12.2） | 47.70% | 54.36% | 37.88 / 60.87 / 1.04 / 0.21 | 0.371 | 5 条 `G2;G3;G4` |
| v1 + 按场校准（§12.6） | 38.17% | 44.99% | 47.34 / 51.10 / 1.30 / 0.27 | 0.276 | 5 条 `G3;G4;degenerate` |
| **v2 + 按场校准（本节）** | **23.67%** | **30.60%** | **76.33 / 21.16 / 2.09 / 0.43** | **1.057** | **11 条** `G2;G3;G4` |

`CROSS_MATCH_OK 12 7886 1.057 WARN(G2;G3;G4)`。

**三件事必须一起读，只读第一行会得出相反结论：**

1. **覆盖率确实回来了**：unknown 23.67% 已经很接近旧场基线 20.6%。
   机制是 v2 的窗口把"取到色但离两队都远"的框大量纳入（`color` 层 51.1% → 21.2%）。
2. **但层的含义变了**：v2 把 `color` 层（原本 44% 的 unknown）吃掉之后，
   剩下的 unknown 几乎只剩 `purity` 失败 —— 从 37.1% 涨到 **76.3%**。
   所以 **`max_drift` 从 0.276 炸到 1.057、WARN 从 5 条涨到 11 条**，
   这不是"数字变差了"，而是工具在如实报告：**v1 时代调好的门控规则不能直接套到 v2 上**
   （`purity` 层在 v2 下占 3/4 的 unknown，若仍按"purity → drop"处理，丢弃量会翻几倍）。
3. **G6 草坪伪改进守卫生效**：v2 放宽窗口后最危险的伪改进是"草地被判成球队"。
   实测 97 个"躯干主体是草"的框里，被判成 A/B 的 **0 个**（`grass_assigned_rate=0.0`，PASS）。
   ⇒ 至少这条最明显的捷径被堵住了。

**但 G6 只堵了草**。`palette_probe.py` 的探针色在 v2 下的表现说明风险还在：

| 探针色 | v1 | v2 |
|---|---|---|
| 黄色球衣（裁判/门将常见） | unknown | **判成 A（白队）**，dist 0.56 |
| 紫色球衣（门将） | unknown | **判成 A（白队）**，dist 0.54 |
| 新场白色真实像素（S 0.16 / V 0.75） | unknown | 判成 A，dist 0.40 |

也就是说：**v2 降低 unknown 的代价是"非队色也会被分到某一队"**，
这些分配对不对，**只能靠第四批 83 框标注来判**（§12.7）。
在那之前，v2 **不应**作为默认口径（当前默认仍是 v1，`--color-metric v2` 显式开启）。

> ⚠️ **自身踩坑留档（本轮最值得记的一条）**：`--color-metric v2` 第一版实现是
> `CP.CLASSIFY_METRIC = args.color_metric`，而量测路径走的是 `color_prior` 里
> **另一份** importlib 模块实例 ⇒ 该开关**完全无效**：JSON 里
> `classify_metric: "v2"` 写着 v2，四层占比与 unknown_rate 却与 v1 **逐位相同**
> （38.17% / 47.34 / 51.10 …），不报错、不崩、数字看着合理。
> 修法：口径只留一个权威落点 `color_prior.set_classify_metric()`，并在切换后
> **回读断言**（`cp.classify_metric() != args.color_metric` 直接 `CROSS_MATCH_FAIL`）。
> 教训与 D013 的两次「全判 white」同类：**多份模块实例 + 全局开关 = 静默假绿**。

---

## 13. 分辨率伪影 与 色板过紧（2026-10-20 本轮的两条正式结论）

> # ★ 本轮对外能说的主结论（先读这一段，再看后面所有表）
>
> **v2 让「能判出队色」的比例从 60.4% 提到 78.9%，但黄牌裁判、紫色门将、看台等
> 非队色像素**仍会被误分到某一支队**。因此在第四批标注回来之前：**
>
> 1. **v2 不能当默认口径**（默认仍是 v1，`--color-metric v2` 显式开启）；
> 2. **不能报「精确率提升」** —— 覆盖率 +18.5 pt 的代价是判错从 4 涨到 18（§13.2 标定表）；
> 3. **G6 只堵了草坪一个口子**（护栏见 §13.4），堵不住黄衣/紫衣/被环境色染的白灰。
>
> **Lead 口述数字已更正**：初版这里曾写「60.4% → 78.9%」，经 JSON 复算无法还原
> （52.30/76.33 是 `colored` 字段；81.33/81.33 是"非 color 层失败"上限），
> 以本节数字为准。规则：**Lead 口述数字与 JSON 冲突时一律以 JSON 为准，并在文档注明已更正**。
>
> 数字口径（同一批 12 段 `--play-windows`、n_hi=7886）：v1 有 4124 框判出队色
> （`colored` 4124/7886 = **52.3%**），v2 有 6019 框（**76.3%**）；
> 若按「所有不是 `color` 层失败的框」这一更宽的可用上限算，两口径都是 81.3%
> （6399/7886）——**差额正是 v1 那 1895 个「取到色但离两队都远」的框**，
> v2 把它们纳入了，但纳入得对不对**只能靠标注判**。


本节把 §12 的实测升级成两条**可以独立引用**的结论，并补上 §12 没覆盖的
「判据阈值」「探针修前/修后」「随机窗口对照」。

### 13.1 结论一：`too_small` / `rect` 的占比在**跨分辨率**下不可直接比

新片 854x480，旧片 1280x720（切片 640x360）。两条链路的同一批 12 段：

| 层 | 旧基线（640x360） | 新片 **原生 854x480** | 新片 **缩到 640 宽重检** |
|---|---|---|---|
| `purity` | 37.10% | 37.88%（v1）/ 76.33%（v2） | 30.77% / 54.66% |
| `color` | 44.41% | 60.87% / 21.16% | 54.94% / 19.95% |
| `too_small` | **14.68%** | **1.04%** / 2.09% | **10.83%** / 19.24% |
| `rect` | **3.81%** | **0.21%** / 0.43% | **3.46%** / 6.15% |
| 框高 h 中位 | 37.9 px | **43.5 px** | **33.5 px** |

**机制**：`too_small` 的判据是 `h_px < MIN_H_PX(22)`。新片原生画面里人更高
（h 中位 43.5 vs 旧 37.9），**几乎没人落进 22 px 以下** ⇒ 该层从 14.68% 掉到 1.04%。
这不是"新比赛的人更好认"，是**画面尺度变了**。`rect` 同理（矩形合法性对高度敏感）。
一旦缩到与旧片同宽（640），h 中位回到 33.5，`too_small` 立刻回到 10.83%，与基线 14.68%
的相对漂移降到 −26%（PASS）。

**因此定下的规则（已写进 `caveats` 与令牌）**：

1. 跨分辨率比较时 **`too_small` / `rect` 两层的原生占比不可直接比**，判据必须看
   `scaled_640` 那一列；
2. 但**两套数字都要报**——原生的 `purity` / `color` 才是生产里真正会看到的分布，
   只报缩放列会掩盖"实际部署画面下客户端的真实分层"；
3. 因此 `max_drift` 取两布局的**较大者**：宁可被伪影触发一次 WARN（可解释），
   也不要漏掉真漂移。

> 这条是**工具在如实报告尺度变化**，不是结论变差。若只读原生列就把 `too_small`
> 判成"−93% 不可迁移"，是把分辨率伪影当真漂移——本轮最容易犯的错。

### 13.2 结论二：`blue` 预设色**匹配不上深蓝球衣**（色板过紧）

新场是「大连英博海发 = 白衣 / 深圳新鹏城 = 深藏青」，`--palette white,blue`。
真帧量出的躯干色（`tools/kit_calib.py`，12 起点 × 3 帧，`min_h=40`，共 229 样本）：

| 簇 | n | h01（圆中位） | S | V |
|---|---|---|---|---|
| 白衣（旧场+新场白队） | 120 | 0.250 | 0.067 | 0.935 |
| 深藏青（新场客队） | 109 | **0.598** | **0.298** | **0.435** |

预设 `blue` 的参考色是 `(115, 0.70, 0.70)` ⇒ h01 0.639 / S 0.70 / V 0.70
（`tools/classify_players.py` 的 `PALETTE_REF`）。它描述的是**亮蓝**，不是藏青。

**量化**：深蓝簇 66 个候选像素（`s≥0.15 & h01∈[0.50,0.72]`）到预设 `blue` 的距离
min 0.068 / p10 0.091 / **med 0.128** / p90 0.169 / max 0.210 ⇒ **9/66 超过
`MAX_COLOR_DIST=0.16`**。中位数只有 0.128，**离阈值 0.16 只剩 0.032 的余量**。

**为什么"只把阈值调大"不行**（这是本轮最重要的一条设计判断）：

v1 的彩色分支是 `0.8×(色相环距离/0.5) + 0.2×|ΔS|`，**完全没有亮度项 V**。
`MAX_COLOR_DIST=0.16` 落在色相项上的配额是 `0.16/0.8 = 0.2` ⇒ 等价于
**色相差 ≤ 36°**，且饱和度几乎要完全相等（ΔS 只剩 0.16×0.2=0.032 的预算）。
后果是：

| 现象 | 数值 | 后果 |
|---|---|---|
| 深蓝(V 0.435) 与 亮蓝/V 更高的同色相衣服 | 色相相同 ⇒ **距离相同** | 深色队服和浅色队服分不开 |
| 草反射率高的白衣（V 0.63-0.80） | 与深蓝只差亮度 | 会被判成深蓝 |
| 草坪绿 h01≈0.44 → 预设 `blue` | 距离 = `0.8×(155-115)/180/0.5` = **0.36** | **不能把阈值放到 0.36 以上**，否则全体草地框都会被判成队色 |

也就是说：**阈值往上调一点漏判还多，往上调多了又把草地吃进来**。所以本轮
**没有**动 `MAX_COLOR_DIST`（旧比赛结论零影响），而是新增独立口径
`CLASSIFY_METRIC=v2`（`--color-metric v2`），v2 做三件事：

1. **色相改成角度配额**：`hd = hue_dist×360`，`hd > MAX_HUE_DEG_V2=50` ⇒ 直接不可判
   （而不是被 0.8 权重稀释）；
2. **纳入亮度**：亮而不饱和（`V ≥ 0.65 且 S < 0.35`）⇒ 判为"被环境色染的白/灰"，
   不可判（专门挡草反射白衣被吞成深蓝）；
3. **深色队服的饱和度亏欠补偿**：`ref.V < 0.60` 时加 `0.08×max(0, Sref − S)`。

**白/黑分支在 v2 里与 v1 逐字节相同**——这一条是踩坑换来的：v2 第一版顺手把
white 分支的分母 0.70→0.50、0.25→0.30，结果**口罩**（S 0.60 / V 0.80）距离 0.286→0.26
反而被判成"白队球衣"，v2 判对率一度只有 38.1%（比 v1 的 69.0% 还差）。
结论：**v1 的白/黑分支本来是对的，v2 只该改彩色分支。**

**标定用的粗真值**（197 样本：`S<0.25 & V>0.55` ⇒ white；`h01∈[0.50,0.72] & S≥0.15` ⇒ blue，
119 white + 78 blue）：

| 口径 × 色板 | 判对 | 判错 | unknown |
|---|---|---|---|
| v1 + 预置 `white,blue` | 136/197 = **69.0%** | 4 | 57 |
| v1 + 按场校准 refs | 159/197 = 80.7% | 11 | 27 |
| **v2 + 预置 `white,blue`** | **170/197 = 86.3%** | 18 | 9 |
| **v2 + 按场校准 refs** | **186/197 = 94.4%** | 8 | 3 |

⇒ 只换口径（不校准）**+17.3 pt**；口径+校准合计 **+25.4 pt**。
代价是"判错"从 4 → 18（用精确率换覆盖率），残余 5 个 W→B 误判全是草反射下的
病态白衣：`(0.595,0.216,0.647) (0.582,0.243,0.592) (0.634,0.218,0.631)
(0.617,0.161,0.631) (0.585,0.22,0.635)`——真深蓝 V 0.36-0.62、白衣 V 0.63-0.80，
**只靠亮度分得开**，这是 v2 的结构性上限，不是 bug。

### 13.3 `palette_probe.py`：补上「能判出」方向（修前必失败）

原探针只有 `false_assign`（"判到的队不像它"）一个方向，**从不检查"该判出却没判出"**。
本轮补 `detect_rate()`，探针色全部改成**真帧量出的真实球衣色**（`KIT_PROBES`）：

| 探针 | 颜色 | 内容 | v1 修前 | v1 修后 |
|---|---|---|---|---|
| `white_old` | (0.250,0.067,0.935) | white | 判成 B，OK | OK |
| `blue_new` | (0.598,0.298,0.435) | blue | 判成 B，dist 0.1458（**余量仅 0.0142**） | OK |
| `red_old` | (0.101,0.848,0.819) | red | 色板 `white,blue` ⇒ unknown 0.6534；**色板 `red,white` ⇒ unknown 0.1679** | OK |
| `grass_reflection` | (0.580,0.300,0.700) | white（`detect_only`，只要求不掉 unknown） | **unknown 0.1742 ⇒ FAIL** | OK |

**关键失败记录**：`--palette red,white` × v1 ⇒ `red_old` 判成 unknown，dist 0.1679 > 0.16。
**旧比赛自己的红队，在它自己的色板下，修前代码判不出来。** 这条是"探针必须先能失败"的实证。
修后矩阵：`red,white` v1 FAIL / v2 OK；`white,blue` v1 FAIL（grass_reflection）/ v2 OK；
`red,blue` v1 FAIL / v2 OK；`red,orange` v1 v2 都 FAIL（`red_old` 判成 B=orange，
正是内容判据要堵的）；`white,black` v1 v2 都 FAIL（`palette_indistinct margin 0.034 < 0.100`）。

> **探针自身的三次假绿**（都修了，留档）：①第一版 `need=="any" or role in PALETTE`，
> 后半句恒真 ⇒ 红队色在 `red,white` 下判成"白队"也算 PASS；②第二版 `role == need`
> 但 need 写死 "A"/"B" ⇒ 白队色被判成 B 过不了；③第三版把 role 改成下标 0/1 ⇒
> 球衣色"是不是深蓝"与排成 A/B 无关，判定永远错。最终判据是
> **"判出角色的色板条目颜色名必须等于该探针的颜色名"**。

### 13.4 ★ 判据 G6：草坪框被判成 A/B 的数量（v2 的专属守卫）

放宽口径最危险的伪改进是"草地/看台被判成球队"。新增 `grass_boxes`（躯干主体是草的框）
与 `grass_assigned`（其中被判成 A/B 的）两个计数器：

```
G6: grass_boxes > 0 且 grass_assigned / grass_boxes > MAX_GRASS_ASSIGN_RATE(0.10) ⇒ WARN
    grass_boxes == 0 ⇒ SKIP
```

实测 12 段：原生 **0 / 97**（0.0%）、缩放 **0 / 90**（0.0%），PASS。
**为什么 10%**：草坪像素本身到两队色距离都远（0.36 以上），但**检测器在草坪上给出的
person 框**（远景里每帧 7-8 个）只要被判到队色就是纯假阳性；旧场 `purity` 层内非球员率
是 0.974，10% 意味着"至少 90% 的草框必须不掉进队色"，比现状 100% 留了余量、又远比"随便"严。

**但 G6 只堵了草**：v2 下黄色（裁判/门将）判成 A、紫色（门将）判成 A、
真白像素 dist 0.40 也判成 A ⇒ **"非队色被分到某一队"这条风险 G6 管不到**，
只能靠第四批 83 框标注判（§12.7）。所以在标注回来之前，**v2 只作为可选项，默认仍是 v1**。

### 13.5 修前 / 修后完整对照（同一 mp4、同一模型、`white,blue`）

**A. 比赛窗口模式（`--play-windows`，12 段，n_hi 7886）**

| 指标 | v1（修前） | v2（修后） | 变化 |
|---|---|---|---|
| `unknown_rate` · 原生 | 47.70% | **25.03%** | **−22.7 pt** |
| `unknown_rate` · 缩放 | 54.36% | 31.57% | −22.8 pt |
| `purity` · 原生 | 37.88% | 72.19% | +34.3 pt |
| `color` · 原生 | 60.87% | 25.43% | −35.4 pt |
| `too_small` · 原生 | 1.04% | 1.98% | +0.9 pt |
| `rect` · 原生 | 0.21% | 0.41% | +0.2 pt |
| G6 草框被判队色 · 原生 | 0 / 97 | 0 / 97 | 无 |
| `max_drift` / WARN 条数 | 0.371 / 5 | 0.946 / 9 | 层含义变了（§12.8） |

**B. 随机窗口模式（12 段，n_hi 4712；`data/results/cross_match_random_v2.json`）**

| 指标 | v1 | v2 | 变化 |
|---|---|---|---|
| `unknown_rate` · 原生 | 56.60% | **28.74%** | **−27.9 pt** |
| `purity` · 原生 | 30.78% | 60.64% | +29.9 pt |
| `color` · 原生 | 61.83% | 24.82% | −37.0 pt |
| `too_small` · 原生 | 6.90% | 13.59% | +6.7 pt |
| `rect` · 原生 | 0.49% | 0.96% | +0.5 pt |
| G6 · 原生 | 0 / 32 | 0 / 32 | 无 |
| `max_drift` / WARN | 0.392 / 6 | 1.022 / 8 | — |

**A 与 B 必须分开读**：随机窗口里混着特写与看台（h 中位 86.5，@8667.9 s 单帧 h 中位 181），
`too_small` 掉到 6.90% 同样是尺度伪影（§13.1），**不要拿随机窗口那一列论证"更好"**。
`--play-windows` 才是与旧基线同口径的那一列。

**③ 外层 precision 代理（无真值，只能代理）**：旧场"颜色层精确率 95.7%"的前提是
unknown 只有 20.6%；新场 v1 的 unknown 是 47.7%（相当一部分是深蓝匹配不上 `blue`），
v2 降到 25.0%（接近旧场水平）。**但 v2 的 18 个 W→B 误判说明"判出来的更不纯"** ⇒
precision 代理与 coverage 反方向变化，**在第四批标注回来之前不能报"精确率提升"**。
这两列只报告、不下结论，是本轮的诚实边界。

### 13.6 ★ `color_metric_not_applied` 判据的**失败验证**（防第二层假绿）

§12.8 记录的静默假绿（JSON 写 v2、实际跑 v1）修法里加了一条硬 FAIL：

```python
cp_metric = cp.set_classify_metric(args.color_metric)
CP.CLASSIFY_METRIC = cp_metric
if cp.classify_metric() != args.color_metric:
    print("CROSS_MATCH_FAIL color_metric_not_applied %s!=%s" % (...))
    return 2
```

**判据自己必须先能失败**，否则它只是第二层假绿。验证分两步：

**(a) 单实例层验证**（importlib 加载两份 `classify_players`，复现 bug 状态）：

```
A) 修好状态 set_classify_metric(v2):
    cp.classify_metric() = v2
    classify(量测路径) = ('B', 0.2944, ...) | 断言会 PASS? True
B) 复现旧 bug（只改本文件那份实例）:
    CP.CLASSIFY_METRIC = v2 | cp.classify_metric() = v1
    classify(量测路径) = ('B', 0.1458, ...)   <- 仍是 v1
    断言会 FAIL? True
```

注意 `0.2944`（v2）与 `0.1458`（v1）是**同一个深蓝球衣色**在两个口径下的距离——
v1 下它离门限 0.16 只剩 0.0142，这就是 §13.2 那个"差一点就全 unknown"的余量。

**(b) 端到端注入验证**（把 `cross_match_eval.py` 里那一行换回 bug 写法，
其余不动，直接跑脚本）：

```
$ python tools/cross_match_eval.py --self-check --color-metric v2
CROSS_MATCH_FAIL color_metric_not_applied v1!=v2
exit=2
```

撤掉注入、恢复原文件后重跑：

```
SELF_CHECK_OK pipeline n_clips=2 n_hi=1625 clips_ok=2 reasons_ok=2/2 gate=consistent scale_probe=ok
CROSS_MATCH_OK 2 1625 0
```

⇒ **判据在 bug 状态下确实 FAIL（exit 2），在修好状态下确实 PASS。** 这条判据是有效的。

### 13.7 ⚠️ `max_drift` / WARN 条数在 v1 与 v2 之间**不可直接比**

| | v1 | v2 | 能不能比 |
|---|---|---|---|
| `max_drift` | 0.371 | 0.946 / 1.057 | **不能** |
| WARN 条数 | 5 | 9 / 11 | **不能** |

原因：判据 G2/G3 是「各层占比相对旧基线的漂移」，而 **v2 把 `color` 层的框大量
搬进了 `colored`**（实测 v1 `color` 层 2290 框 → v2 只剩 395 框，其中 1895 框变成判出队色）。
`purity` / `too_small` / `rect` 三层**逐框数量完全没变**（1425 / 39 / 8）。
分层的**分母意义变了**（`color` 层从 29.0% 缩到 5.0%），同一套阈值自然会报出更多 WARN。

> ❗ **不要**把 `0.371 → 0.946` 读成「新比赛更不可迁移」。
> 正确读法是：**v2 改变了层的定义，所以 v1 时代的漂移阈值不能拿来评价 v2**。
> 这也是 §12.8 说的"层含义变了"的定量版本。
> 要评价 v2 是否更可迁移，必须重标 G2/G3 的基线，或直接用第四批标注的真值算
> precision/recall —— 而后者还没标。

### 13.8 `data/results/` 下本任务的产物索引（没有索引的证据等于没有证据）

`sha256` 一列只给前 16 位，用途是**丢了能确认是不是同一份**；全量可用
`python -c "import hashlib,pathlib;print(hashlib.sha256(pathlib.Path(r'<file>').read_bytes()).hexdigest())"` 复算。

| 文件 | 命令 | 口径 | 时点 | sha256(前16) |
|---|---|---|---|---|
| `_cm_play_v1.json` | `--play-windows --n-clips 12 --palette white,blue --color-metric v1 --allow-indistinct-palette` | **v1** | 15:10 | `4f2b9017f90e642e` |
| `_cm_play_v1.log` | 同上 | v1 | 15:10 | `4a7fc6b86fc21493` |
| `_cm_rand_v1.json` | 同上但去掉 `--play-windows`，加 `--max-total-s 0` | v1 | 15:12 | `c5d613b3efef5cb2` |
| `_cm_rand_v1.log` | 同上 | v1 | 15:12 | `4d79ce6ed507e108` |
| `cross_match_play_v2.json` | `--play-windows --n-clips 12 --color-metric v2`（无校准） | v2 | 15:04 | `c0f0060ed07e1fae` |
| `cross_match_v2.json` | `--play-windows --n-clips 12 --color-metric v2 --palette-refs data/results/kit_calib.json` | **v2+校准** | 15:22 | `d544b617b0d2a9ee` |
| `cross_match_random_v2.json` | `--n-clips 12 --color-metric v2 --max-total-s 0` | v2 | 15:14 | `f2daad5880df455d` |
| `_cm_rand_v2.log` | 同上 | v2 | 15:14 | `1f2ec49b5fededed` |
| `_cm_play_v2_calib.log` | 对应 `cross_match_v2.json` | v2 | 15:22 | `a8d803bd4e46cf35` |
| `cross_match_v2_STALE_buggy.json` | 旧 `cross_match_v2.json`（commit cda6427）改名留档 | 字段标注错 | 15:02 | `738bd802566cb219` |
| `cross_match_v2_preset.json` | `--play-windows --n-clips 12 --color-metric v2`（预置色板、无校准） | v2 | 15:26 | `52bd67f08fe30fc1` |
| `_kit_new.json` | `tools/kit_calib.py` 采样 229 个躯干色 | — | 14:32 | `c2ae9622292ea929` |
| `kit_calib.json` | 同上聚合为按场参考色 | — | 14:02 | `45f1190242f64783` |

各文件说明：

- **`_cm_play_v1.json` / `.log`**：修前基准（比赛窗口）。原 `cross_match_play_v1.json`
  在并发清理中被删、**已丢失**，以本文件+日志为准（同命令、同参数重建）。
- **`_cm_rand_v1.json` / `.log`**：修前基准（随机窗口）。
- **`cross_match_play_v2.json` / `cross_match_v2_preset.json`**：同一配置（预置色板、无校准）先后各跑一次（**Lead 独立复现，用于验证可复现性**），四层占比逐位相同（72.19 / 25.43 / 1.98 / 0.41，unk 0.2503），互为复现证据。
- **`cross_match_v2.json`**：§12.8 主表用的就是这份。§12.8 表格里"v2 + 按场校准"一行即此。
  已加 `"default_recommended": false` 与 `"blocked_by"`（见 §13.9）。
- **`cross_match_v2_STALE_buggy.json`**：**数字是对的，只有 `max_color_dist` 字段标错**
  （写 0.16，真 v2 应为 0.70）。其四层占比（76.33 / 21.16 / 2.09 / 0.43）与新跑的
  真 v2 **逐框一致**。留作「字段标注错」样本，**不要引用它的 `max_color_dist`**。
- `_cm_*` 文件**不要挪位置**（索引表按路径点名）。

> ⚠️ `cross_match_v2_STALE_buggy.json` 与 `cross_match_play_v2.json` 在本轮被并发清理
> 误删过两次，已从 git 与日志恢复。后续清理 `data/results/` 前**先看这张表**。
> 已从 git 与日志恢复。后续谁再清理 `data/results/`，请先看这张表。

### 13.9 机读的「不能当默认」标记

`data/results/cross_match_v2.json` 顶层带两个字段，防止以后有人看到 76.3% 就直接拿去当默认：

```json
"default_recommended": false,
"blocked_by": "第四批标注未标完（data/annot/labels4.csv label 列全空）"
```

判据：**只有当第四批标注标完、并用真值算出 v2 vs v1 的 precision/recall 之后**，
才允许把这个字段改成 `true`。覆盖率（52.3% → 76.3%）单独不构成放行理由（§13 开头主结论）。

---

## 14. 如何验证一条「开关判据」真能失败

本仓库最顽固的假绿形态是**开关没接上**，而不是阈值调错。已发生过的实例：

- 颜色全判 `white`（阈值不动，模块实例是另一份）；
- `--gate-rules` 看着生效、实际走的是默认分支；
- `box_idx` 错位对齐（`tools/make_player_labels.py:238` 的 `box_idx` 只是 stratum 内
  `enumerate` 下标，不是框身份，两批标注按三元组根本对不上）。

共同点：**不报错、不崩、数字看着合理**。所以凡是「加了开关 ⇒ 断言开关生效」这类判据，
它自己必须先被证明**会失败**，否则就是第二层假绿。

### 14.1 三步法（可复制）

**第 1 步：单实例层复现 bug 状态。** 绕开脚本，直接拿模块确认「bug 写法确实改不动量测路径」。

```powershell
$env:PYTHONIOENCODING='utf-8'
@'
import sys; sys.dont_write_bytecode = True
import importlib.util
from pathlib import Path
REPO = Path(r"D:\zynq-yolo")
def load(path, modname):
    spec = importlib.util.spec_from_file_location(modname, str(REPO / "tools" / path))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
cp = load("color_prior.py", "_bk_cp")         # 量测路径实际用的那一份
CP = load("classify_players.py", "_bk_cls")   # 调用方手里那一份
col = (0.598, 0.298, 0.435); pal = {"A": "white", "B": "blue"}   # 真帧深蓝球衣色
print("修好:", cp.set_classify_metric("v2"), cp.classify(col, pal))
cp.set_classify_metric("v1"); CP.CLASSIFY_METRIC = "v2"          # 故意退回 bug 写法
print("bug :", CP.CLASSIFY_METRIC, cp.classify_metric(), cp.classify(col, pal))
'@ | Set-Content -Encoding utf8 _bugcheck.py
D:\python\python.exe -B _bugcheck.py; Remove-Item _bugcheck.py
```

实测输出（`cp.classify_metric()` 停在 `v1`、距离停在 `0.1458`，即证明 bug 写法无效）：

```text
修好: v2 ('B', 0.2944, {'A': 0.5, 'B': 0.2944})
bug : v2 v1 ('B', 0.1458, {'A': 0.5, 'B': 0.1458})
```

**第 2 步：端到端注入 bug 写法。** 把判据依赖的那行改回 bug 形态，直接跑脚本。

```powershell
$env:PYTHONIOENCODING='utf-8'
Copy-Item tools/cross_match_eval.py _cme_backup.py -Force
@'
import sys; sys.dont_write_bytecode = True
from pathlib import Path
p = Path("tools/cross_match_eval.py"); s = p.read_text(encoding="utf-8")
old = "        cp_metric = cp.set_classify_metric(args.color_metric)\n        CP.CLASSIFY_METRIC = cp_metric"
new = "        CP.CLASSIFY_METRIC = args.color_metric   # BUG-INJECTED\n        cp_metric = args.color_metric"
assert old in s, "anchor missing"
p.write_text(s.replace(old, new, 1), encoding="utf-8")
'@ | Set-Content -Encoding utf8 _inj.py
D:\python\python.exe -B _inj.py; Remove-Item _inj.py
D:\python\python.exe -B tools/cross_match_eval.py --self-check --color-metric v2
Write-Host "exit=$LASTEXITCODE"
```

期望（**这就是判据真能失败的证据**）：

```text
CROSS_MATCH_FAIL color_metric_not_applied v1!=v2
exit=2
```

**第 3 步：撤掉注入，确认恢复。** 判据不能「永远 FAIL」。

```powershell
Copy-Item _cme_backup.py tools/cross_match_eval.py -Force; Remove-Item _cme_backup.py
D:\python\python.exe -B tools/cross_match_eval.py --self-check --color-metric v2
```

期望：

```text
SELF_CHECK_OK pipeline n_clips=2 n_hi=1625 clips_ok=2 reasons_ok=2/2 gate=consistent scale_probe=ok
CROSS_MATCH_OK 2 1625 0
```

### 14.2 三步都过了才算数

| 步骤 | 证明了什么 | 没做会怎样 |
|---|---|---|
| 1 单实例复现 | bug 写法**确实**改不动量测路径（不是想当然） | 判据可能防的是不存在的问题 |
| 2 端到端注入 | 判据在真实入口处**确实**会 FAIL | 判据可能写了但走不到（死代码 / 分支被跳过） |
| 3 撤掉恢复 | 判据**不是**恒 FAIL | 一个恒 FAIL 的判据同样没用（会被 `--allow-*` 绕过后失效） |

### 14.3 复用清单

这套方法对下面这些判据同样适用（凡「开关 ⇒ 断言」都该这么验）：

- `--color-metric` → `cp.classify_metric() != args.color_metric`（已按本节验过）
- `--palette-refs` → 校准 refs 是否真的进了 `classify`
  —— **目前还没有这条断言，是已知缺口**（本轮只靠 JSON 里的 `measured_refs` 字段间接确认）
- `--gate` / `--gate-rules` → `player_gate` 实际用了哪套规则
- `--play-windows` → 采样窗口是否真的落在草坪占比 ≥ 阈值的区间
- `--allow-indistinct-palette` → 放行后 `palette_probe` 的结论是否仍被如实写进 JSON
