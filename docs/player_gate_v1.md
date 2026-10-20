# 球员判定门控层 v2（D013-a 的收口点）

> 工具：`tools/player_gate.py`　机读结果：`data/results/player_gate.json`（`"version": "v2"`）
> 判据令牌：`PLAYER_GATE_OK <n_kept> <n_dropped> <关键指标>`／失败 `PLAYER_GATE_FAIL <reason>`
> （`docs/repo_spec.md` §5：**认令牌不认退出码**）
> 上游依据：[`hi_layer_color_gate.md`](hi_layer_color_gate.md) §6（第二批 159 框分层判决）、
> [`color_prior_v1.md`](color_prior_v1.md)（颜色作为手段的四种用法）
> 决策：**D013-a**（颜色是用来「更好地识别球员」的**手段**，不是给框染色）

复算口径：12 段 `data/samples/incidents/*.mp4`，stride=8，imgsz=640，hi_conf=0.25，
`classes=[0]` ⇒ **1110 个处理步、12 585 个 hi 框**，单次全量约 100 s。

---

## 0. 这份文档回答什么，以及每个数字能当什么用

`hi_layer_color_gate.md` §6 只用**层间相对高低**说了一件事：hi 层取色失败的四类原因里，
非球员率差 9 倍（purity 97.4% / rect 67.6% / color 43.6% / too_small 12.9%）。
「差 9 倍」到「该怎么门控」中间还有一段：**哪一层丢、哪一层留、哪一层降权，
以及丢/不丢的代价各是多少**。本文件就是那一段，并且**每一组都带可复算的对照**。

一句话结论：**按原因门控在 12 段全量上只丢 98/12 585 框（0.78%）**，
而 v1（取不到色就丢）丢了 834 框、其中 **88.2% 是被误伤的球员**——
「颜色取不到 ⇒ 非球员」这个方向从一开始就是错的。**但**（重要，见 §5.1）
在 purity/rect 两层，标注与本层实测仍然互相矛盾，本轮**没有裁决**。

### 0.1 数字仪表盘：先看这一列

| 记号 | 含义 | 能不能当 precision 用 |
|---|---|---|
| `inside_gap` | 保留框与丢弃框的**场内率差**（pt） | **不能**。位置分布差，不是准确率 |
| `cov_est` | **不再是数字**。令牌里恒为 `cov_est=unreliable`，理由与保守界限见 §5.1(b) | **不能**（`cov_est_reliable=false`） |
| `DEFECT=4` | `gate_defects` 条目数（漏网点/旁证清单） | 不是 bug 数，是**待证伪清单**长度 |
| `CONFLICT=1` | 与标注结论冲突的条目数 | 是证据，不是错误计数 |

### 0.2 ★ 逐框对齐 = 0（本轮才确认的事实）

`data/annot/labels*.csv` 里的 `box_idx` **不是框的身份**：它是
`tools/make_player_labels.py:238` 里 `enumerate(items)` 的**层内下标**，而检测列表按
conf 降序、长度随 conf 门限变化（第一批 conf=0.08，第二批 conf=0.25）。
实测：`labels.csv` 73 行只有 **61** 个唯一 `(clip,frame,box_idx)`；`labels2.csv` 159 行只有 **115** 个。

⇒ 本层**没有一处在标注上拟合**（`alignment_policy.n_boxes_aligned_one_to_one = 0`），
标注只用于事后核对方向与写「待证伪清单」。

写回案件记录（供后来人避坑）：曾有一版把两批标注当成同一批框做了逐框对齐，
得到「总体 N 率 0.58 / 估计 N=1430 / 覆盖率 112%」这类**作废数字**（`over` 判据当时也没拦住）。
现在 `precision_proxy` 只保留一种失效判据——**不可能性**（丢的框比盘面上的框还多）。

> **更正记录（2026-10-20，本轮）**：本文件此前多处写「`cov_est` = 8%」。
> 该数字已**全部作废并删除**，理由见 §5.1(b)：分子分母不同源、rect 层两把尺子差 2.6 倍、
> purity 层还存在**拓扑矛盾**（本 run 保留 802 框 > 按层占比折算的 785 框）。
> 任何把「8%」当覆盖率/精度引用的下游结论都必须一并撤回。
> 令牌字段 `cov_est` 现在**只输出 `unreliable` 这个标签**，不再输出任何百分比。

标注自身的可信度上限（决定一切「标注 vs 实测」的重合度）：第二批 159 框里与第一批
同 `(clip,frame,box_idx)` 的 37 框，一致性 **kappa=0.226**，P/N 真冲突 **10/37=27%**。
⇒ 只信**层间相对高低**（97.4% vs 12.9%，9 倍量级可信），不信绝对值。

---

## 1. 规则表（三件事，不是一件事）

门控不是「规则表」单打独斗，**规则 + 几何守卫 + hull 复核**三层同时生效，缺一层就退化成 v1：

| 层 | 判什么 | 取值 |
|---|---|---|
| ① 原因规则 | 取色失败原因 → 动作 | `too_small` → **keep**（小框看得见人，只是取不到色）<br>`purity` → **drop**<br>`rect` → **drop**<br>`color` → **demote**（离两队都远，但离球员不远） |
| ② 几何守卫 | 位置/尺度是否像球员 | `FIELD_Y_MIN=0.35`；远端人群 `far_frac=5.0` 倍中位框高且 `far_abs_px≥160` ⇒ `far_crowd`（v2 **只 demote，不丢**） |
| ③ hull 复核 | 球衣色像素占其凸包面积的比例 | `HULL_PURITY_MIN=0.60`，且面积占框 ≥ `HULL_MIN_AREA_FRAC=0.25` 才做（否则那是整块背景） |

判据口令（JSON 里 `params.rules` 可核对）：`too_small:keep / purity:drop / rect:drop / color:demote`。

**`MIN_H_PX` / `MIN_PURITY` 的数值语义完全未改**——改的只是「这个原因算不算丢弃理由」。
`LEGACY_RULES`（四条全 drop）保留可复算；默认配置向后兼容（见 §7.2 的逐段验证）。

### 1.1 逐框决策顺序（`_decide`，顺序本身就是结论）

| 步 | 条件 | 输出 | reason |
|---|---|---|---|
| 0 | 取到队色且属 A/B 队 | `player` | `colored_A` / `colored_B` |
| 1 | 框越界 | `staff` | `out_of_frame` |
| 2 | `h/crowd_h ≥ 5.0` 且 `h ≥ 160 px` | `unknown` | `far_crowd` |
| 3 | 关闭颜色先验（消融用） | `unknown` | `no_color_prior` |
| 4 | 取色失败但 hull 纯度达标 | `unknown` | `purity_hull_ok` |
| 5 | 规则表 | `player`/`unknown`/`staff` | 见上表 |
| 6 | 本该丢，但框在场内且 `h ≥ 0.6·crowd_h` | `unknown` | `geo_rescue_<原因>` |
| 7 | 其余 | `staff` | `color_drop_<原因>` |

`player` = 颜色正向证据；`unknown` = **低置信球员（保留、降权），不是丢弃桶**；
`staff` = 判为非球员（下场）。**判定层不丢框**：`judge_frame()` 输出与输入一一对应，
`apply_gate()` 才按 `want=` 切分。

### 1.2 本轮删掉的死规则：`out_of_frame`

v1 有一条「框出画 ⇒ 非球员」。实测 **12 585 框 in_frame=100%**（YOLO 从不返回越界框）
⇒ 纯死代码，已删除。留在这里是因为它是个典型教训：**规则没在数据上验过，就默认它是活的**。

### 1.3 为什么 `far_crowd` 只改动作、几乎不改阈值

inc_05 在 0–600 帧是近景机位（画内 6–10 人，crowd_h≈160–330 px），远处替补/球童 h≈41–58 px
把**中位数**拉低到 136–208。于是正常场上球员 `h/crowd_h` 也能 ≥1.5。
v1 的 `0.60` 其实是把「比例阈值」当成了**倍数**语义，配上「直接丢」必然误伤。

⇒ 阈值改成 5.0× + 绝对下限 160 px，动作改成 `demote`。即便如此它只 demote **12** 框
（conf 中位 0.909、h 中位 269.9 px、场内率 100%）——**这 12 个都是「一个人就占了远高于
人群尺度」的框，大概率是被摄像头拍到的近景球员**，所以它绝不能是 drop。

---

## 2. 八组消融（同一批 12 段、12 585 个 hi 框、1 110 个处理步）

`data/results/player_gate.json` → `ablation`。**判据全部与标注无关**（位置/轨迹/conf/框高）：

| 组 | 保留 | player | unknown | 丢 | 丢弃率 | 保留框场内率 | 丢弃框场内率 | 差 |
|---|---|---|---|---|---|---|---|---|
| **`after`（v2）** | 12 487 | 10 415 | 2 072 | **98** | **0.78%** | 97.57% | 78.57% | **−19.0 pt** |
| `legacy`（取不到色就丢） | 11 964 | 10 034 | 1 930 | 621 | 4.93% | 97.96% | 87.12% | −10.8 pt |
| `v1struct`（结构按 v1） | 12 475 | 10 415 | 2 060 | 110 | 0.87% | 97.57% | 80.91% | −16.7 pt |
| `nohull`（关 hull 复核） | 12 481 | 10 393 | 2 088 | 104 | 0.83% | 97.6% | 79.8% | −17.8 pt |
| `nostruct` | 12 487 | 10 415 | 2 072 | 98 | 0.78% | 97.57% | 78.57% | −19.0 pt |
| `norules`（关规则表） | 12 585 | 10 034 | 2 551 | 0 | 0 | 97.43% | — | — |
| `noprior`（无颜色先验） | 12 585 | 0 | 12 585 | 0 | 0 | 97.43% | — | — |
| `minimal`（只留颜色正向证据） | 12 585 | 10 415 | 2 170 | 0 | 0 | 97.43% | — | — |

读法（三条，别只挑第一条）：

1. **推荐配置只丢 98 框 = 0.78%**。`nostruct` 与 `after` 完全相同 ⇒ 本批素材上
   **结构证据一条都没触发 drop**（`out_of_frame` 是死码，`far_crowd` 只 demote）。
   ⇒ D013-a 的颜色门控实际功效是 **precision / 降权**，不是 recall。
2. `norules` 与 `minimal` 都是 0 丢弃 ⇒ 真正在删框的就只有规则表那一条（`rect=drop`
   与 `purity=drop`），其余全是 `unknown` 降权。
3. `noprior` 把 12 585 框全降成 `unknown`（不丢）⇒ 颜色先验的净贡献是
   **把 10 415 框从 `unknown` 提到 `player`**，即**区分度**，不是筛除率。

---

## 3. v1 → v2：736 个被误伤的球员

`v1_vs_v2`：

| 项 | 数 |
|---|---|
| v1 丢弃 | 834（场内率 90.3%） |
| v2 丢弃 | **98**（场内率 78.6%） |
| **被 v2 救回** | **736（88.2%）** |
| 救回框的场内率 / 框高中位 / conf 中位 | **91.8% / 122.6 px / 0.574** |
| v2 **新增误杀** | **0** |

救回机制分解：`color_demote_color` 442、`color_keep_too_small` 263、`far_crowd` 12、
`geo_rescue_purity` 11、`purity_hull_ok` 6、`colored_A/B` 各 1。

⇒ 两个最大的误伤来源是 **「color 层一律丢」** 与 **「too_small 一律丢」**，合计 **705/736**。
这两个恰好是「颜色取不到」与「人太小」——**都不是非球员的证据**。
人工看图印证：Inc.04 f664 趴/跪地红衣球员在 v1 下被判 `color_demote`/`unknown`（漏判风险）；
Inc.05 f16 站立的**白衣场上球员**在 v1 下被判 `far_crowd`/`staff`（**明确错判**）。

---

## 4. 三组独立于标注的前后对照（A/B/C）

三组都**不使用任何标注**，只依赖检测框自身与视频的时空连续性。

### 4.0 场内/场外怎么定义（先说清楚再报数）

主判据 `FIELD_Y_MIN=0.35`：**框底边 `y_bottom ≥ 0.35·H` 且框未出画** ⇒ 「场内」。

- **为什么是 0.35**：12 段 640×360 俯拍，画面高度 35% 以上是看台/广告牌；
  实测 `ybot/H` 的 **p5=0.39** ⇒ 这条线只切掉最上面约 2% 的框，是「几乎不误杀」的宽松界。
- **不让结论依赖单一选择**：同时给 `center`（`cy ≥ 0.35·H`）与 `foot`（`y_bottom ≥ 0.5·H`，更严格）。
- **再加一层不依赖「两列同分布」的检查**：`by_band`（`inside_solid` / `band_ambiguous` ±4/15 px / `outside_solid`）。

### 4.1 对照 A：位置分布 —— 来源 `player_gate.json → compare_A_position`

| 口径 | 保留 n | 保留场内率 | 丢弃 n | 丢弃场内率 | 差 |
|---|---|---|---|---|---|
| **`y_min`=0.35（主判据）** | 12 487 | 97.57% [97.3, 97.8] | 98 | **78.57%** [69.5, 85.5] | **−19.0 pt** |
| `center`=0.35 | 12 487 | 94.95% [94.6, 95.3] | 98 | 71.43% [61.8, 79.4] | −23.5 pt |
| `foot`=0.50 | 12 487 | 77.05% [76.3, 77.8] | 98 | 62.24% [52.4, 71.2] | −14.8 pt |

实心/容差带/场外条带（最干净的一条证据，只做**桶内**比较）：

| band | n | 丢弃数 | 丢弃率 |
|---|---|---|---|
| `inside_solid` | 12 306 | 77 | **0.63%** |
| `band_ambiguous` | 104 | 7 | 6.73% |
| `outside_solid` | 175 | 14 | **8.00%** |

⇒ 越靠场外，丢弃率单调上升（0.63% → 6.73% → 8.00%，**13 倍**）。
二项检验（丢弃框场内率 < 保留框）：`p ≈ 2.6e-14`。
`by_scene`（近景 / 远景 / 稀疏）丢弃率 **5.71% / 0.235% / 8.92%** ⇒ 远景组几乎不碰。

**诚实边界**：
- 两列**不是同一批框**（各配置的丢弃集合并不相同），`inside_gap` 含「集合漂移」成分，
  **不能读成准确率**；只有 `by_band` 的桶内比较是干净的。
- `kept_pl_only`（只把 `player` 当保留）时差只有 **−0.7 pt**（p=0.019）：
  **`unknown` 里有大量场内框**（这正是设计意图），但也说明那 19 pt 很大程度上来自
  `unknown` 桶的构成，而不是「`staff` 稀缺」。
- `legacy` 的差只有 −10.8 pt、v1 只有 −7.6 pt ⇒ **v1 的丢弃集合其实也偏场外**，
  只是它的规模（834）大到「偏场外」根本补不了它的伤害。

### 4.2 对照 B：轨迹碎片化 —— 来源 `compare_B_fragmentation`

口径：同一只**纯几何跟踪器**（`GeoBoxTracker`，相机运动补偿 + IoU 门 + gap 惩罚，
`max_age=3`），**唯一变量是「哪些框进得了跟踪流」**。
指标按 `tools/track_players.py` 的思路取：轨迹总数、长度中位数、短轨迹（<3）、长轨迹（≥25）、
每段碎片化率、ID switch、每 100 步轨迹数。

| 指标 | `after` | `legacy` | `buffer`（第三口径） | Δ(after−legacy) |
|---|---|---|---|---|
| n_tracks | **843** | 787 | 914 | **+56** |
| n_short_lt3 | 199 | 179 | 222 | +20 |
| n_ge_25 | **178** | 167 | 175 | **+11** |
| len_median_of_clips | 10.25 | 9.25 | 9.50 | +1.0 |
| len_mean_of_clips | 15.05 | 15.50 | 14.13 | −0.45 |
| frag_rate_mean | 0.7513 | 0.7531 | 0.7270 | −0.002 |
| id_switch | 242 | 229 | 279 | **+13** |
| tracks_per_100_steps | 74.73 | 69.77 | 81.03 | +4.97 |

**逐段配对符号检验**（同一段、同一批帧、只换进门规则，n=12 段）：

| 指标 | 正/负 | Δ中位 | 双侧 p | 方向 |
|---|---|---|---|---|
| n_tracks | **12/0** | +3.000 | **0.0005** | 更多轨迹 = **更碎** |
| n_ge_25 | **8/0** | +1.000 | **0.0078** | 更多长轨迹 = **更稳** |
| n_short_lt3 | 7/2 | +1.000 | 0.180 | 更碎（不显著） |
| len_median | 2/6 | −0.25 | 0.289 | 更短（不显著） |
| len_mean | 5/7 | −0.318 | 0.774 | 无 |
| frag_rate | 5/7 | −0.0026 | 0.774 | 无 |
| id_switch | 7/4 | +2.0 | 0.549 | 略多（不显著） |

**结论（不挑指标）：B 组是「3 项更碎 / 1 项更稳」，净效应是碎片化略变差。**

- 更碎：`n_tracks` **+56（12/0，p=0.0005，最强的一条）**、`n_short_lt3` +20、`id_switch` +13。
- 更稳：`n_ge_25` **+11（8/0，p=0.0078）**、`len_median` +1.0。
- 机制上也说得通：`after` 保留的框**多 523 个**（12 487 vs 11 964），多出来的里有短命的
  （远处/边缘/一闪而过），轨迹数必然上升；同时 `n_ge_25` 与 `len_median` 都涨，
  说明**真球员的轨迹被保住了、甚至变长了**。两个方向同时成立 ⇒ 只能说「结构变了」，
  **不能说「变好」**。
- 第三口径 `buffer`（把被门控判 `staff` 的框先保留一帧做缓冲，看能否吸收断档）：
  相对 `after` 反而 `n_tracks −71`、`id_switch −37`、`len_median +0.75`
  ⇒ **如果把门控丢的框补回去，碎片化是降低的**，这是「当前丢弃确实造成了一些断档」的旁证。

**诚实边界（B 组最大的坑）**：stride=8 ⇒ 相邻处理帧间隔 0.32 s，`max_age=3` 相当于
**约 1 秒的容忍窗** ⇒ **单帧丢弃会被掩码直接吸收**。B 组只能测到「**连续多帧**丢弃」
造成的碎片化，测不到一次性丢帧。而本门控单帧丢弃平均只有 **0.088 个框**（见 4.3 反事实），
所以 +56 条轨迹**不能归因于那 98 个被丢框本身**，更可能是「保留集合变了 ⇒ 关联竞争格局变了」
的二阶效应。另：自研跟踪器的绝对轨迹数**不能**与 `data/results/player_tracks.json` 的
942 条直接比，只做相对对照。

### 4.3 对照 C：conf / h_px 分布 —— 来源 `compare_C_conf_h`

| | n | conf 中位 | h_px 中位 |
|---|---|---|---|
| 保留（player+unknown） | 12 487 | 0.654 | 37.0 |
| 丢弃（staff） | 98 | **0.628** | **96.0** |

- `conf`：Mann-Whitney **p=0.346，方向 dropped_lower**（差异不显著）。
- `h_px`：Mann-Whitney **p=3.6e-13**（丢弃框**明显更高**，中位 96.0 vs 37.0，2.6 倍）。

**这一条是最反直觉的发现**：门控**不是在捡检测器的垃圾**——丢弃框 conf 中位 0.628
与保留框 0.654 几乎一样，`h_px` 还大一倍多。它拒绝的是「检测器很有把握、但躯干颜色
不像任何一队」的**大框**。⇒ **颜色证据是独立于 conf 的信息维度**（conf 分不出来，颜色能）。

高置信大框（`conf≥0.50 且 h≥30`，n=8 930）的丢弃率：

| 配置 | 丢弃数 | 丢弃率 |
|---|---|---|
| **`after`** | 55 | **0.62%** |
| `noprior` | 0 | 0% |
| `legacy` | 221 | 2.47% |
| `v1` | 408 | **4.57%** |

⇒ v1 在高置信大框上丢 4.57%，v2 降到 0.62%（**7.4 倍**改善），且丢弃 ⊆ 颜色失败。
这与第一批标注「hi 层 h≥100 px 精确率仅 57.1%（n=14）」**方向一致**：大框确实是高风险区。

反事实帧计数（纯计数，**不依赖任何场内判据**）：

| | 值 |
|---|---|
| 处理帧数 | 1 110 |
| 平均每帧少 | **0.088** 个框 |
| 至少少一个的帧占比 | 7.66% |
| 单帧最多少 | 2 个 |

---

## 5. 与标注结论的一致 / 冲突

| # | 标注说 | 本层实测 | 判定 |
|---|---|---|---|
| 1 | `too_small` 层 87.1% 是球员（31−4=27/31）⇒ 不该丢 | 规则 `keep`；`after` 在 too_small 层丢弃 **0/381** | **一致** |
| 2 | `purity` 层 97.4% 非球员 ⇒ 该丢 | `after` 在 purity 层只丢 **87/941 = 9.2%**（保留 854，场内率 **100%**） | **冲突** |
| 3 | `rect` 层 67.6% 非球员 ⇒ 该丢 | `after` 在 rect 层只丢 **11/210 = 5.2%**（保留 199，场内率 **100%**） | **冲突** |
| 4 | `color` 层 43.6%，五五开 ⇒ 中性 | 规则 `demote`：**0 框被 drop**，1 017 框降为 `unknown` | **一致** |
| 5 | hi 层「颜色在高置信层完全没生效」 | `colored_A 5 328` + `colored_B 4 706` = **10 034/12 585 = 79.7%** 判成 `player` | **一致（已修）** |
| 6 | 第一批：有颜色先验精确率 85.1%（n=47）vs 71.0%（n=62） | `noprior` 全降 `unknown`；颜色先验净贡献 = 把 10 415 框提为 `player` | 方向一致，量级不可比（±9 pt） |
| 7 | 第一批：hi 层 h≥100 px 精确率仅 57.1%（n=14） | 丢弃框 h 中位 **96.0 px**；高置信大框丢弃率 0.62% | **一致**：大框是高风险区，v2 只对其中「颜色确实不像队」的下手 |
| 8 | `scores2.json` verdicts：purity/color/rect=signal，too_small=noise | 规则表据此设 | 一致，但见 5.1 |

### 5.1 ★ 两处必须写下来的冲突（不许挑指标）

**(a) purity / rect 层的「该丢」在实测里几乎没被执行。**

- purity 层：标注 97.4% 非球员（n=39），`after` 只丢 87/941 = **9.2%**；
  保留的 854 框**场内率 100%**。
- rect 层：标注 67.6% 非球员（n=37），`after` 只丢 11/210 = **5.2%**；
  保留的 199 框场内率也是 **100%**。

两个方向都不能单独当结论：

- **若信标注** ⇒ 本层在 purity/rect 层严重漏网（`gate_defects` 记了 `coverage_gap`）。
- **若信几何** ⇒ 这 854+199 个框全在场内、h 中位也不小，更像**站着的球员**而不是场边人员。

**为什么实测暂时占上风（但不是赢定）**：`geo_rescue` 会把「在场内且尺度正常」的取色失败框
降级成 `unknown` 而非丢弃，实测 `geo_rescue_purity 802`、`geo_rescue_rect 199`。
人工看图支持这一侧（Inc.05 f16 站立白衣球员被 v1 错判为 `staff`）。
但「场内率 100%」本身有偏——**场内有裁判、教练、第四官员、场边工作人员**。

**裁决口径**：本轮**不宣布哪一方对**。理由就是 §0.2 的 kappa=0.226：这批标注连**它自己**
都不自洽，不足以推翻 1 000+ 个「场内且尺度正常」的框；但这也不等于标注错——
**这是一个必须用新数据解决的问题，不是用现有数据能裁决的问题**（见 §8 第 1 条）。

**(b) 覆盖率 `cov_est` 已作废：不再给数字，只给「不可靠」+ 一个保守界限。**

这个量在旧版本里报成 **8.0%**，读起来像「门控只覆盖了 8% 的非球员」。**它不该被这样读**，
所以本轮把它整个撤掉。三条独立理由，任何一条都足以撤掉它：

| # | 问题 | 证据（本 run，`data/results/player_gate.json → precision_proxy`） |
|---|---|---|
| 1 | 分子分母不同源 | 分母 = 层内 N率 × **另一次运行**（`hi_color_diag.json`）的层占比 × 本 run 框数。两把尺子对同一层的框数估计在 **rect 层差 2.6 倍**、purity 层差 1.1 倍 |
| 2 | 层内 N率自身不可靠 | kappa=0.226、P/N 真冲突 27% ⇒ 只可信层间相对高低（§0.2） |
| 3 | **拓扑矛盾（三层全部）** | `per_stratum[*].topology_ok` 在 purity / rect / color **三层全为 false**：purity 层本 run **保留 802 框** > 按层占比折算的 **785.0 框**；rect 层保留 **199** > 折算 **80.6**；color 层保留 **1 017** > 折算 **939.7**。保留数大于该层总框数是不可能的 ⇒ 「同一层」在两套口径里指的不是同一个集合（本 run 的 `geo_rescue_purity` / `purity_hull_ok` 在 diag 那次被归到了别处） |

**本轮改成只给一个对「门控覆盖得少」最不利的保守界限**（也让覆盖率看起来最大）：

```
分子 = 98      = 门控实际丢掉的框数（本 run 实测，全表唯一硬数）
分母 = 2 116   = max(折算估计量 1 229, 本 run 的 hi-unknown 全量框数)
⇒ 覆盖率下界 4.6%  （bound_kind = rate_vs_hi_unknown_boxes）
```

**这个 4.6% 也不能当结论用**，它只能干一件事：**否定「门控覆盖了大部分非球员」这种说法**。
它的方向性偏差也很清楚——分母越大、算出的比例越小，所以它是「覆盖率有多低」的**上限**。

| 层 | 本 run 实测（丢/留） | 按层占比折算的框数 | 二者的比值 | `topology_ok` | 折算出的非球员估计 |
|---|---|---|---|---|
| purity | 87 / 802 | 785.0 | 1.1× | **false** | 764.9 |
| rect | 11 / 199 | 80.6 | **2.6×** | **false** | 54.5 |
| color | 0 / 1 017 | 939.7 | 1.1× | **false** | 409.6 |
| **合计** | **98 丢** / 2 116 框 | — | — | 3 处违例 | **1 229.0** |

⇒ 唯一能安全说的是：**在标注口径下，本门控的覆盖面是「几分之一」量级，不是「大部分」。**
这与 §2「只丢 0.78%」是同一件事的两面——门控是降权层，不是过滤器。

**JSON 字段口径**（防止再被误用）：`precision_proxy.cov_est_reliable = false`、
`precision_proxy.cov = {bound_lo_pct, bound_denominator, why_unreliable[], strength}`；
`real_recall_est_total` 恒为 `null`，**故意留空**——这个字段只要出现就会被读成 recall。
`coverage_status` 保留（`ok` / `INCONSISTENT_est_below_drops` / `n/a`），但它已不再决定任何被打印的数字。

> 字段修正记录（防止再被引用）：本字段先后错过四次，每次都让覆盖率看起来更漂亮。
> ① 把估计量写成「样本 N 数 / 抽样率」，抽样率≈0.084 时放大 ~12 倍；
> ② 「本 run 的 hi-unknown 框数」只统计了「丢的 + 按 reason 名精确匹配留下的」，
> 而 `geo_rescue_purity` / `purity_hull_ok` 的名字里没有 `purity`，被漏光
> ⇒ 分母缩到 98、分母≈分子、覆盖率被 clamp 成 **100%**；
> ③ 失效判据写成「覆盖率 >100%」，任何小样本子集运行都被静默作废；
> ④ 即使前三处都修好，输出仍是一个**精确到 0.1% 的假精确值**，而它建立在
> 一个拓扑矛盾之上（见上表第 3 行）⇒ 现在彻底不输出百分比。

---

## 6. 阈值敏感性（没有一处是在标注上调出来的）

| 阈值 | 值 | 来源 | 改大/改小的后果 |
|---|---|---|---|
| `FIELD_Y_MIN` | 0.35 | 12 段 `ybot/H` 的 p5=0.39 | 换 `foot`=0.5：两列 77%/62%，差缩到 −14.8 pt（**仍同向**） |
| `HULL_PURITY_MIN` | 0.60 | 球衣色像素占凸包面积比的经验界 | 关掉（`nohull`）：丢 104 而非 98（**+6 框**）⇒ 影响 <0.1% |
| `FAR_PEOPLE_FRAC` | 5.0× | inc_05 近景实测 crowd_h=136–330 | 换回 0.6×（`v1struct`）：丢 110 而非 98 ⇒ 仍只影响 ~12 框 |
| `FAR_ABS_MIN_PX` | 160 | 同上 | 同上 |
| `MIN_H_PX` | 22 | **未改动**（现有常量） | 语义不变，只把 `too_small` 从「丢弃理由」改成 keep |
| `MIN_PURITY` | **未改动** | 现有常量 | 同上 |
| `geo_rescue` 的 `0.6·crowd_h` | 0.6 | **继承 v1 的比例** | 唯一「未独立标定」的阈值，见 §7 第 8 条 |
| `SPARSE/BAND` 容差 | ±4 / ±15 px | 画面高度的 4/15 px（`GateConfig.field_margin_*`） | 只影响 `band` 分类，不影响决策 |

**hull 阈值扫描（0.4/0.5/0.6/0.7/0.8）仍未做**——这是明确缺口，不是已验结论。
现有一条旁证：整条 hull 逻辑关掉只改变 6 框（98→104），所以**扫描的期望收益很小**，
优先级排在 §8 之后。

唯一需要标注意见的是规则表，而它的取值是**离散 3 档**（keep/demote/drop），
不是拟合出来的数值 ⇒ 过拟合风险被限制在「选哪一档」。如果 kappa=0.226 的标注足以定这件事，
那本层就没必要存在了；**它不足以**，所以留了 `--gate-rules` 让下一个人用更好的数据重定这一档。

---

## 7. 诚实边界（逐条，都是会让人被误导的那种）

1. **逐框对齐 = 0**，`box_idx` 不是框身份（§0.2）。任何「精确率/召回率」都不能由本文件直接给出。
2. **标注自身不可靠**：kappa=0.226、P/N 真冲突 27% ⇒ 只用层间相对高低。
3. **对照 A 的两列不是同一批框**，`inside_gap` 含集合漂移；只有 `by_band` 桶内比较是干净的。
4. **对照 B 受 stride=8 影响**：`max_age=3`≈1 s 的容忍窗会吸收单帧丢弃；且 +56 条轨迹
   更可能来自「保留集合变大」的二阶效应，**不能**归因于那 98 个被丢框。
5. **`cov_est` 已作废**：不再输出任何百分比（`cov_est_reliable=false`），令牌里只打
   `cov_est=unreliable`。唯一保留的数是「保守界限 4.6%」，且它只能用来**否定**
   「门控覆盖了大部分」，不能当覆盖率/精度（§5.1b）。
6. **素材单一**：12 段、同一场比赛、单机位（含近景/远景切换）、红/白队服。
   白队区分度天生弱（`hue_std` 12–18° vs 红色 4.5°）。
7. **裁判若与某队同色，本方法在原理上无法分开**（物理限制，非实现问题）。
8. **`geo_rescue` 的 `0.6·crowd_h` 继承 v1 未独立标定**；近景机位下 crowd_h 被远处小框拉低，
   这条可能偏松（更倾向把取色失败的大框救成 `unknown`）——而它正是 §5.1(a) 争议的关键旋钮。
9. **`too_small` 层的判别力弱**：87.1% 是球员这个结论只来自 n=31。设成 keep 是
   「宁可留着降权」的保守选择，**不是**「已证明它全是球员」。
10. **`DEFECT=4` 不是「4 个 bug」**，是 2 条 `coverage_gap` + 2 条 `label_supported` 的待证伪清单。
11. **本层几乎不丢框**（0.78%）⇒ 它**不会**让下游「球员集合」干净多少；它的价值在
    `unknown` 这一档的**排序/降权**上，**下游必须真的用 `score` 才有意义**。
12. 跨场已有旁证：`cross_match_eval.md` §12 显示新场 `unknown_rate` 20.6%→47.7%
    （色板未按场校准前不宜直接部署）。

---

## 8. 下一步（按优先级）

1. **用第四批跨场标注（`data/annot/labels4.csv`）裁决 purity 层 —— 这是唯一已就位的裁决工具。**
   见 §8.1（含「能裁决什么 / 不能裁决什么」与**标完后跑哪条命令**）。
2. **rect 层跨场样本不足（只有 3 框）⇒ 本批**不能**裁决 rect，需要主场小标注补**。
   见 §8.1 的表格最后一行。
3. **把 `unknown` 的 `score` 接进球权判定**，并测「A/B/C 三组指标随降权策略的变化」——
   `unknown` 占 16.5%（2 072/12 585），有没有用**完全取决于下游读不读 `score`**。
4. **先决定 `geo_rescue` 该不该这么松**（§7 第 8 条）：目前它把 purity/rect 两层 1 001 个
   取色失败框救成 `unknown`，这才让「按原因门控」看起来几乎不删框。
   旋钮的口径要先定，再谈「按原因门控值不值」。
5. **B 组 stride 要降到 1 才能测真碎片化**（当前 0.32 s × `max_age=3` 的容忍窗把要测的东西
   吃掉了）。建议专门跑一段 stride=1 的对照，只看 `id_switch` 与 `len_median`。
6. 跨场：新场先按场校准色板（`tools/kit_calib.py`，见 `cross_match_eval.md` §12.3）再重跑本文件。

### 8.1 第四批（跨场 · 新比赛）能裁决什么、不能裁决什么

**这批是什么**：`data/annot/MANIFEST4.json`，由 `tools/make_newmatch_labels.py --build` 生成
（2026-10-20T14:46）。新比赛（大连英博 vs 深圳新鹏城，白衣/深蓝衣），
12 个 20 s 窗口 × stride=12，候选 5 316 框，每层抽 20 个 ⇒ **83 框**。
`label` 列**全为空**（83/83 未标）⇒ 本节不引用任何来自它的数字。

**它覆盖了哪几层**（`MANIFEST4.json → n_candidates_by_stratum` 与 `counts`，逐字）：

| 层 | 新场候选框数 | 抽样数 | 旧场层内 N率（`labels2.csv`） | 能否裁决旧场结论 |
|---|---|---|---|---|
| `purity` | **968** | **20** | 97.4%（38/39） | ✅ **能**。20 个样本若全是 N，95% 下界 ≈ 0.83 ⇒ 支持旧结论；若 N 率掉到 60% 以下 ⇒ 旧结论在新场**不成立** |
| `color` | 1 055 | 20 | 43.6%（17/39） | ◻ 能测，但旧结论是「五五开」⇒ 本层规则是 demote，影响本来就小 |
| `too_small` | 32 | 20 | 12.9%（4/31） | ◻ 能测；新场候选只有 32 个（旧场约 1 847 个）⇒ **小框在新场几乎不存在**，这本身就是一条重要的跨场差异 |
| `colored`（对照） | 3 258 | 20 | —（旧场判为 player） | ◻ 可作「门控不该丢」的反向对照 |
| **`rect`** | **3** | **3** | 67.6%（25/37） | ❌ **不能裁决**。新场 rect 候选总共只有 3 个框，其中 **2 个是 x2=853.6 的 HUD 右边缘框**（见下） ⇒ n=3 的路子没有判别力 |

**结论**：
- **purity 层（§5.1(a) 争议的一半）本批可以裁决**；
- **rect 层（争议的另一半）本批不能裁决** —— 新场 rect 候选只有 3 个，且 2 个疑似
  **赛前被分诊到 `purity` 层去了**（见下一条）⇒ 需要主场再来一份小标注，或先修分诊顺序。

**一个顺带发现（未证实，只作假设）**：新场 `rect` 层的 3 个候选里，2 个 `x2 = 853.6`
（= 画面右缘，853.6 即帧宽 853.6 px 的 HUD 条左边界的同一列），宽度只有 7.3–8.0 px。
按 `tools/color_prior.py:107-110` 的分诊顺序，`MIN_H_PX → torso_rect → purity → color` 是
**逐层短路**的：落在 HUD/边线上的窄框会先通过 `torso_rect`，然后在 `purity` 层失败，
于是被记成 `purity` 而不是 `rect`。若这个假设成立，旧场 `rect` 层的 67.6% 非球员率里
**可能有一块是 HUD/边线伪框**，那批框本来就该丢，与本门控的争议无关。
**如何证实**：跑一次 `tools/hi_color_diag.py` 的变体，把 `rect` 分诊改到 `purity` **之前**，
看 `rect` 层框数是否显著上升。本文件不下结论——没有这个实验就是在猜。

**标完之后的裁决命令（可直接复制）**：

```powershell
$env:PYTHONIOENCODING='utf-8'
cd D:\zynq-yolo

# 0) 先确认标完了（未标完时严格失败，不给数字：docs/repo_spec.md §5）
#    把 data/annot/labels4.csv 的 label 列全部填 P / N / U（三选，不判原因）
D:\python\python.exe tools\make_newmatch_labels.py --score
# 期望末行：NEWLABEL_SCORE_OK 83 purity=<..> color=<..> too_small=<..> rect=<..> colored=<..>
# 未标完会打出：NEWLABEL_FAIL not_all_labeled <k>/83

# 1) 看分层 N 率 + Wilson CI + 与旧场的保序检验
#    结果落在 data/results/newmatch_labels.json
#    裁决口径：只看 purity 层的 N_rate_pn 与 n_N
#      ≥ 0.80  ⇒ 旧场 purity 结论跨场成立 ⇒ §5.1(a) 判「本门控漏了 purity 层」，要收紧
#      ≤ 0.50  ⇒ 旧场 purity 结论不跨场成立 ⇒ §5.1(a) 判「标注侧不成立」，保持现状
#      0.50–0.80 ⇒ 没有裁决力，写进 §5.1(a) 说「仍待定」
#    同时读 "order_preserved"：false 说明连层间排序都不能跨场搬。

# 2) 若要把新场也接进本门控的对照（需先按场校准色板，§8 第 6 条）
D:\python\python.exe tools\kit_calib.py --video <新场比赛视频> --palette white,blue
```

**诚实边界（这条最容易忘）**：第四批是**新比赛、两队服色完全不同**（白衣/深蓝 vs 红/白），
取色失败原因的**分布本身可能整体平移**。所以它裁决的是
「**purity 层的『非球员率高』这个方向能不能跨场成立**」，
**不能**把新旧两场的 N 率直接相减、也不能把新场的 N 率代回本文件的 `cov_est`（§5.1b 已作废）。
如果 `order_preserved = false`，那么第二、三批标注建立的分层规则表**整体不可跨场迁移**，
本门控只能作为「单场标定」使用。

---

## 9. 怎么复算

```powershell
$env:PYTHONIOENCODING='utf-8'
cd D:\zynq-yolo

# 全量 + A/B/C 三组对照 + 写机读结果（约 100 s）
D:\python\python.exe tools\player_gate.py --ablate --track --write
# 期望末行：PLAYER_GATE_OK 12487 98 inside_gap=-19.0pt v1_drop=834 ... CONFLICT=1

# 只跑 A/C（不跟踪，快约 2x）
D:\python\python.exe tools\player_gate.py --ablate

# 换规则表（复现 v1 的丢弃口径）
D:\python\python.exe tools\player_gate.py --ablate --gate-rules "too_small=drop,purity=drop,rect=drop,color=demote"

# 把门控接到颜色先验主链路上
D:\python\python.exe tools\color_prior.py --gate after
D:\python\python.exe tools\color_prior.py --gate legacy    # 旧行为对照

# 仓库自检
D:\python\python.exe tools\check_repo.py     # 期望 CHECK_REPO_OK
```

### 9.1 `tools/color_prior.py --gate` 的接入口径（新）

②补检层（低置信补检，`lo_conf=0.08 → hi_conf=0.25`，stride=4，12 段，12 585 个 hi 框）
的 keep/drop，来源 `data/results/color_prior.json → clips[].rescue.gate_matrix`：

| 规则表（`--gate-matrix`） | 补检框 kept | dropped | keep_ratio |
|---|---|---|---|
| `minimal`（只保留颜色正向证据） | 4 720 | 0 | 1.000 |
| **`after`（默认）** | **4 147** | **573** | **0.879** |
| `legacy` = `presets`（取不到色就丢） | 1 643 | 3 077 | 0.348 |

⇒ 同一批 5 320 个补检框，旧规则丢掉 **3 077/4 720 = 65.2%**，新规则丢 **573/4 720 = 12.1%**。
按 reason 拆 `after` 丢的 573：`color_drop_purity 382` + `color_drop_rect 191`；
留下的 4 147 里 `color_keep_too_small 1 691`（**31.8%** 是「人太小」——标注说这层 87% 是球员，
正是 v1 白扔的那一批）、`colored_A 925` + `colored_B 684`、`color_demote_color 813`、
`purity_hull_ok 34`。

**默认行为向后兼容的验证**（这条最容易被忽略）：`--gate after` 下 `team_share` 逐段与
改动前的 12/12 段完全一致（`n_hi_boxes`、`field_rate`、`hue_tightness`、`team_counts` 全一致）
⇒ 新增的 `--gate` **只影响 ②补检的去留，不碰 ①剔除 / ④分队**。
复算：`tools/color_prior.py --gate after --gate-matrix`（约 4 min），末行
`COLOR_PRIOR_DONE 12 4147 573`。

### 9.2 `--gate` 的实现要点（给后来人）

`tools/color_prior.py` 的 `_load_gate()` 通过 importlib 按路径加载本模块，
**显式把 `PALETTE_REF / color_of_name / torso_rect / classify` 换成同一份对象**——
两套阈值口径必须物理上是同一个对象，否则会出现「数值相等但语义不同」的静默分叉。

本轮据此补了两个必踩的坑：

1. **importlib 加载前必须 `sys.modules.setdefault(name, mod)` 再 `exec_module`**，
   否则模块里的 `@dataclass` 会让 `dataclasses` 去 `sys.modules[cls.__module__]` 里取
   `None.__dict__` ⇒ `AttributeError: 'NoneType' object has no attribute '__dict__'`。
2. **队色表只接受颜色名字**。历史上有人把 `color_of_name()` 解析后的 `(h,s,v)` 元组塞进
   `palette`，`classify` 会 `SystemExit`，又被调用处的 `try` 吞掉 ⇒ 静默退化成「全部判 white」
   （`classify_players.py:266` 记过）。现在 `ColorMeter.__init__` 与 `GateConfig.check()`
   都会先挡一次。

---

## 10. 接口（给下游用）

```python
import player_gate as pg          # tools/ 在 sys.path 上时

cfg = pg.GateConfig()                                              # 默认 = 推荐配置
cfg = pg.preset_config("after", palette={"A": "red", "B": "white"})
cfg.check()                                                        # 非法配置抛 ValueError

# 单帧：**不丢框**，输出与输入同序
decs = pg.judge_frame(frame_bgr, boxes_xyxy_Nx4, confs_N, cfg)      # -> list[GateDecision]
d.role          # "player" | "unknown" | "staff"
d.reason        # colored_A / colored_B / color_drop_purity / geo_rescue_purity / ...
d.score         # 0..1，给下游排序/降权
d.inside_field, d.h_px, d.conf, d.hull_ratio, d.evidence

# 批处理：按 want 切分
kept, dropped, every = pg.apply_gate(frames, cfg, want=("player", "unknown"))
```

- `judge_frame` 只接受 `(N,4)` 框 + `(N,)` conf；传老式 `[(box, conf), ...]` 会**明确报错**
  （曾经从 `np.asarray` 里冒出一个看不懂的 `inhomogeneous shape` ValueError）。
- `PRESETS` / `preset_config()` 是 `tools/color_prior.py --gate` 与本文件对照实验的
  **共用配置源**（8 条：`after` / `legacy` / `noprior` / `nostruct` / `norules` / `nohull` /
  `v1struct` / `minimal`，外加旧别名 `presets`），不存在「脚本里一套、实验里另一套」。
- `--gate-rules` 与 `cfg_base` 现在真正进入核心统计（早先有一个 bug 让它「在对照表上
  看着生效、在核心统计里完全不生效」，是典型的**静默假绿**，已修）。
- 下游读数据时**只认 `role` + `score`**，不要自己再调一遍 `torso_rect`/`classify`——
  两套阈值口径分叉过一次（`classify_players.py:266`），修起来很贵。

### 10.1 ★ 一个会悄悄吃掉规则表的加载坑（给后来人）

`tools/color_prior.py --gate presets` 走的是**旧代码路径**：它直接
`GateConfig(palette=..., rules=dict(PRESET_RULES))`，而 `GateConfig` 的**字段默认值**是
`LEGACY_RULES`（四条全 drop）。所以 `presets` 与 `legacy` 在数值上是同一条规则——
这不是 bug，是「旧口径」的正确含义，写在这里免得有人以为 `presets` 应该更宽松。

`--gate-rules` 是**唯一**能改规则表的入口，且它同时作用于 `PRESETS` 名与 `presets` 别名；
带它跑时，`--ablate` 表里 `legacy` / `v1struct` 两行会被 `cfg_base` 覆盖成同一套规则
（这两行的对照意义随之失效）。**要复现 `legacy` 必须不带 `--gate-rules`。**

---

## 11. ★ 踩坑：`git diff` 会因为 CRLF 规范化说反话，要信 sha256

**症状**（2026-10-20 实际踩到）：一个文件在编辑器里被改过，`git status` 却**不显示 `M`**；
换一个文件，`git diff HEAD --stat` 报出「+58/−30」，而工作区字节数与 HEAD 一模一样。
两种现象同时出现，跟着 `git diff` 判断「有没有改」一定会判错。

**机制**（两条独立成因，都在本仓库实测过）：

1. `git cat-file -p HEAD:<file>` 拿到的 **原字节** 与工作区文件的字节数可能**恰好相等**，
   但经过 clean filter 后 blob hash 不同 —— 反之亦然。
   本仓库 `core.autocrlf = false`、`core.eol` 未设 ⇒ 落地时按 `.gitattributes` / 平台默认
   做 CRLF↔LF 转换，**committed blob（LF 域）与 working tree（CRLF 域）不同域**。
2. `git status` / `git diff` 走 **stat 缓存 + clean filter** 两条路径，二者对「文件变没变」
   的判定标准不同（mtime/size 命中缓存时直接判「没变」，可文件内容已经变了）。
   ⇒ **`git diff` 是启发式，不是哈希比对。**

**结论：凡是「这份文件到底等于哪个版本」的判断，一律走 sha256，不走 `git diff`。**
可直接复制的核对命令（Windows PowerShell，本仓库路径）：

```powershell
$env:PYTHONIOENCODING='utf-8'
cd D:\zynq-yolo

# 一次比对「工作区 vs HEAD」的全部交付物（CRLF 规范化后逐字节比）
& D:\python\python.exe -c @'
import subprocess, hashlib
from pathlib import Path
for p in ('tools/player_gate.py','tools/color_prior.py',
          'docs/player_gate_v1.md','data/results/player_gate.json'):
    r = subprocess.run(['git','cat-file','-p','HEAD:'+p], capture_output=True)
    if r.returncode != 0:
        print('%-32s NOT IN HEAD' % p); continue
    hb, wb = r.stdout, Path(p).read_bytes()
    n = lambda b: hashlib.sha256(b.replace(b'\r\n', b'\n')).hexdigest()[:16]
    print('%-32s HEAD=%-8s WT=%-8s SAME=%s' % (p, n(hb), n(wb), n(hb) == n(wb)))
'@

# 辅助：哪些文件真的 dirty（注意：这里会漏报，只作参考）
git diff-files --name-only
```

判定规则（照抄即可）：

- sha256 **相等** ⇒ 工作区就是 HEAD 的版本，提交里已经是终态，不用管 `git diff` 说什么。
- sha256 **不等** 而 `git diff --stat` 只显示一两行 ⇒ 多半是行尾/编码差异，
  用 `git diff --stat --ignore-cr-at-eol` 复核；若仍不等，才是真内容差异。
- 「我以为改过、但 sha256 相等」 ⇒ 改动可能被别的进程/工具覆盖回去了，
  **别信自己的记忆，信哈希**。

**这条纪律的来历**：本轮 Lead 在我还在写的中途按顺序提交了 5 个 commit，
事后要确认「工作区版本是不是终态」。`git diff` 给出的信号自相矛盾（见上「症状」），
最后是靠 `git cat-file -p` 原始字节 + 规范化 sha256 才确定：
`tools/player_gate.py` 与 `data/results/player_gate.json` 已在终态，
`docs/player_gate_v1.md` 与 `tools/color_prior.py` 仍是中间态。
**如果当时信了 `git diff`，就会把两份中间态当成终态交付。**