# 跨比赛可迁移性评估框架（D013-a）

> 结论先说：**现有颜色/门控结论的全部证据只来自 `migu_5106947698` 一场球**。
> 这个工具把「换一场还剩多少」变成一条可复算的命令 + 一组**能失败的判据**，
> 而不是跑完让人自己看数字。

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