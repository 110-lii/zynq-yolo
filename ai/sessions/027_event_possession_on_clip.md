# Session 027 — 2026-11-01 —— 在一段素材里让「球权」事件真的出来（主机代理复现 + 上位机 `--event`）

> 承接 [026](026_pc_push_integration_and_clips.md)（推流一条命令闭环 + 事件为什么出不来）。
> 用户本轮原文：「现在就在一段素材里体现事件就好，你研究一下怎么弄」。

| 字段 | 内容 |
|---|---|
| 会话号 | 027 |
| 日期 | 2026-11-01 |
| 执行 AI（模型 ID） | `mythos-magpie-5487751739`（客户端 `claude`，桌面端 Code 标签页） |
| 执行客户端 / 工具 | Claude Code（桌面端）+ 板端通道 `tools/board_jupyter.py`（Jupyter 9090） |
| 角色 | 单人 |
| 任务目标 | 让至少一段**真实素材**在事件链上产出事件（球权），并把它做成一条能演示的命令 |
| 上下文投喂 | `AGENTS.md`、`tools/events.py`、`tools/result_publisher.py`（`build_frame`）、`tools/board_pipeline.py`（球/事件段）、`tools/ball_policy.py`、`tools/check_ball_field.py`、`tools/check_board_acceptance.py`、`docs/system_status.md`、`ai/sessions/012` / `026` |
| **原文位置** | **待导出**：本会话在 Claude Code 桌面端，仓库仍无 Claude Code 会话导出脚本（同 023–026 的已知缺口，不编造文件名） |
| 关联决策 | 落实 **D015**（PC 送视频进主线）、**D027**（输入侧协议）；复用 **D029** 的服务边界 |
| 复核方 | 未复核（表格更新于轮次 7 上板验证之后） |

---

## 轮次 1 — 先把判据读准：`possession` 是 3 m，不是 2 m

用户：「现在就在一段素材里体现事件就好，你研究一下怎么弄」。

读 `tools/events.py`：`possession(ball, players, max_r=3.0)`，而 `result_publisher.build_frame`
调 `event_step()` 时**没传** `max_r` ⇒ 实际判据是 **3.0 m**。昨天 026 轮次 7 和几份文档里
写的「2 m 判据」是把 `result_publisher._in_field` 的 2 m 余量看串了。当场改掉 5 处
（`docs/demo_run_sheet.md`、`docs/system_status.md` ×2、`ai/sessions/026` ×2）。
**结论不变**（昨天那次最近 3.6 m，3 m 也过不去），但数字必须对。

## 轮次 2 — 先问「哪类事件在现有素材上最容易成立」

出界/进球只需要球，不需要球员，所以先用 `data/calib/field_calib_ball.json` 把现有片段全扫一遍
（`tools/clip_field_scan.py`，主机侧球链，每帧扫）：

| 片段 | 球场坐标球帧 | 越线事件 | 离边线最近 |
|---|---|---|---|
| `build/ball_clip.mp4`（202 帧） | **56** | 0 | 2.93 m（第 108 帧） |
| `build/match_clip.mp4`（377 帧） | 0 | 0 | — |
| `data/samples/incidents/inc_01…12`（各 750 帧） | 0–4 | 2 次单帧（第 651 / 377 帧）**判为假球**：单帧孤立、坐标贴边、其余帧全无球 | — |

⇒ **出界这条路在现有素材上不通**：唯一有球的中圈机位片段，球最高只到 y≈65 m（线在 68 m）。
那两次「越线」是白块假目标被单应放大出来的，不算。

## 轮次 3 — 换成球员那一侧：球其实在球员脚下

写 `tools/evt_proxy.py`（主机代理）：球链与板上**同一套**（`ball_cv_probe` 候选 +
`build/ball_cls_c.exe` 分类器 + `BallTracker` + `place_ball` + `events.step`），
球员检测降级成主机 yolov8n（板上是 person_v8n25 / ncnn）——**只作预筛**。

| 配置 | 球场坐标球 | 球权帧 | 其中落在检测帧 | 最近距离 |
|---|---|---|---|---|
| `--every 25 --ball-every 2`（验收口径） | 52 | 14 | 2 | **1.09 m** |
| `--every 5 --ball-every 2`（演示用） | 52 | 23 | **5** | **1.03 m** |

人工核图（`--dump-frame` 出图）：

- `build/evt_proof_f022.png` / `evt_proof_f025.png`：**红圈正套在白衣球员脚下那颗球上**，
  不是白线、不是广告牌；第 25 帧球员框重新贴回人身上（25 是检测帧）。
- 对照：第 127 帧那次「1.09 m」不采信——那一帧球不在扫描节奏上，用的是上一帧的旧值，
  且落点在画面左缘（`_evt_f127.png` 里红圈不在球上）。

## 轮次 4 — 昨天为什么算出 3.6 m：球帧和检测帧**不相遇**

昨天的 5009 推流跑（`--every 25 --ball-every 2 --max-det 0 --proposal --calib`）里，
球只在第 22–23 帧有球场坐标，而 `--every 25` 的检测帧是 0/25/50…——
**球出现的帧全在两次检测之间**。那些帧的球员坐标是跟踪器外推了 20 多帧的结果（会漂），
再叠加板端 person_v8n25 没框住持球那人，最近的「球员」就落到 3.6 m 外。

⇒ 处置不是放宽 3 m 判据（**不调判据**），而是「让球员坐标是当帧新检出的」：
`--event-every 5` 把检测帧密度提上去，或让球帧落回检测帧。

## 轮次 5 — 做成一条命令：`pc_dashboard.py --event`

装机服务 `zynq-stream` 不带 `--proposal`/`--calib`，而且主路径也不该为一段素材改服务
（`--ball-every 2` 比服务里的 5 慢一倍多）。所以做成「**另起一条**、推完就停」：

```bash
py tools/pc_dashboard.py --event --event-every 5 --push build/ball_clip.mp4 --csv build/demo_events.csv
```

改动（`tools/pc_dashboard.py`）：

| 位置 | 内容 |
|---|---|
| `_event_argv()` | 板端命令：`--listen 5009 --every N --ball-every 2 --max-det 0 --proposal --calib … --no-pl --no-camera-gate --store-dir … --store-prefix evt [--hdmi-preview]`。抽成纯函数是为了能在自检里断言「五个关键参数一个都不能丢」 |
| `board_start_pipeline()` / `board_stop_pipeline()` | 经 Jupyter `Popen` 起管线、写 pid 文件；停的时候 `SIGTERM` + **删 `/tmp/hdmi_preview.active`**——被 SIGTERM 打断的管线走不到自己的收尾分支，不删的话 `zynq-idle` 以为有人占屏，白屏回不来 |
| `_push_pipeline()` | 拆成「起管线 → `_push_body()` → finally 停管线」，异常路径也保证把板端清理掉 |
| 新参数 | `--event / --event-port / --calib / --event-every / --event-ball-every / --event-no-hdmi / --event-prefix` |
| 自检 | 新增第 8、9 组：4 个板端代码模板**格式化后 `compile()`**（补 026 那次 `%d` 没转义的课）、事件命令必含 `--proposal/--calib/--max-det/--ball-every/--listen`、`--max-det` 必须为 `0` |

`PC_DASHBOARD_SELFTEST OK`（主机侧全过）。

## 轮次 6 — 板子掉线：不验证、不声称

22:5x 起把 `ball_clip.mp4` 推到板上（3.69 MB，sha256 校验通过），随后做板端本机跑
`--video build/ball_clip.mp4 --every 25 --ball-every 2 --max-det 0 --proposal --calib …` 时，
Jupyter 连接被重置，紧接 `ping 192.168.2.99` **100% 丢包**。按 `AGENTS.md` §2：
**板子离线时不做任何硬件验证，也不声称「已验证」**。

本轮因此只做到「主机代理 + 人工核图 + 上位机代码与自检」。待办：

1. 板子回来后先跑**板端本机**（不推流）那一条，确认 `events` 里出现 `possession`，落盘归档；
2. 再跑 `pc_dashboard --event` 整条（推流 → 拉回 → 回放），把 JSONL 与面板 PNG 入库；
3. **未解释的差异**：026 的 5009 推流跑与验收用**同一组参数**，却只有 16 帧球场坐标球
   （验收记录是 144）。怀疑与「输入是 JPEG q95 推流 vs 本地文件解码」有关，
   上次 q95 在 c03 上逐帧一致，但这段球更小、更弱。这条要用上面第 1、2 步的 A/B 直接问清楚，
   不要先写结论。

## 轮次 7 — 板子回来了：两条路都上板跑通，球权事件真的出来了

用户：「板子重启好了，继续跑验证」。`BOARD_OK` + `zynq-idle/zynq-stream` 双 `active` 后开跑。

### 7.1 板端本机（文件路径，`build/pull_evt/c05_20261030_234002_00.jsonl`）

```bash
/usr/local/share/pynq-venv/bin/python3 tools/board_pipeline.py \
  --video data/samples/scenes/c05_t01730.0.mp4 \
  --every 5 --ball-every 2 --max-det 0 --proposal \
  --calib data/calib/field_calib_ball.json --no-pl --no-camera-gate \
  --store-dir /home/xilinx/zynq-yolo/build/flow --store-prefix c05
```

`PIPELINE_OK frames=250 detections=50 med_infer_ms=998.75`，板端 230.5 s（3 min 50 s）。
落盘 256 行、**32 帧球场坐标球**、**7 帧球权**（110/111/130/131/**166**/167/169，最近
**1.12 m**）、6 帧 `out_of_play`（170–175，`side=top`）。

### 7.2 推流路径（`pc_dashboard --event`，`build/pull/evt_20261030_232814_00.jsonl`）

```bash
py tools/pc_dashboard.py --event --event-every 5 --push data/samples/scenes/c05_t01730.0.mp4 --csv build/demo_events.csv
```

250 帧 **5 min 04 s**，落盘 250 行、28 帧球场坐标球、**10 帧球权**、0 帧 `out_of_play`；
面板「事件」栏出现**三条** `球权`（`#32 2.38 m` / `#33 1.93 m` / `#26 1.09 m`）。
截短版（`--push-start 4.0 --push-max-frames 80`，板端回 70 行）**1 min 37 s**，
事件与整段那次逐帧一致 ⇒ 演示用截短版。

### 7.3 两条路的逐帧对账：**前 166 帧完全一致，167 帧起推流路径跟错候选**

用 `inv(H)` 把球场坐标投回画面，同一帧上叠两个圆点（`build/c05_evt_f110/130/166/167.png`）：

| 帧 | 文件路径球（球场坐标） | 推流路径球 | 人工核图 |
|---|---|---|---|
| 110 | (52.0, 30.4) | (52.0, 30.3) | 红圈正套在**红衣球员脚下那颗白球**上 ✅ |
| 130 | (49.4, 38.5) | (49.4, 38.4) | 球在草地上、红衣球员迎上来 ✅ |
| 166 | (53.0, 59.3) | (53.0, 59.3) | 球**腾空**在红衣球员身前 ✅ 1.09–1.12 m |
| 167 | (53.2, 60.6) | **(51.6, 58.6)** | 文件那个圈在球上；**推流那个圈落在空草地上**（离裁判更近那一侧）❌ |
| 168–171 | 53.8→57.6（球继续向边线走） | 卡在 (50.x, 57.x) 不动 | 推流路径这一段是**跟丢后的外推** |

所以：

- **推流路径可信的球权帧是 110/111/130/131/166 这 5 帧**（与文件路径逐位相同），
  面板上那三条 `球权` 的前两条和第三条前半段站得住；
- 第 167 帧那个 **0.50 m** 是错位球算出来的，**不采信**；168–171 同理。
  按 012 的规矩：机器数字必须配人工核图，单看距离越小越"好"恰恰是错位的特征。
- 为什么两条路在同一帧选了不同候选：**未定位**。可观测的事实是同一帧的 `confidence`
  也不一样（168 帧文件 0.6 / 推流 0.4），与「JPEG q95 重编码后这颗 6–8 px 的小球
  候选分数分布变了」吻合，但**这是推测，不是结论**。

### 7.4 出界那一侧：机器报了，但**不采信**

文件路径报了 170–175 六帧 `out_of_play(side=top)`，球的球场坐标从 (56.8, 69.1) 走到
(58.5, 72.4)——越过了 68 m 的边线。人工核图发现这几帧的球圈**落在白衣球员身上**
（真球在 70–94 px 外），来回跳。**球当时确实出界了**（画面里能看出来），但**板上这一路
给出的坐标不能作为出界证据**。写报告与讲演示时都按「球权已验、出界未验」讲。

### 7.5 两处顺手记下的小毛病

- **已修**：`fmt_ts` 原先按「值 <100 就当秒」猜单位，板端时间戳其实是毫秒，
  于是 CSV 第 2 行（40 ms）显示成 `00:40.00`。现在一律按毫秒格式化，`demo_events.csv` 已重出。
- **未修（记录在案）**：`--dump-frame/--dump-png` 只在 `--replay` 批量路径生效，
  `--push` 那条路不认这两个参数（出图落在最后处理完的那一帧）。现场要用静帧，
  推完拿拉回来的 JSONL 再跑一次 `--replay ... --dump-frame N` 即可。

### 7.6 还留着的证据缺口

026 记录的「202 帧 144 帧坐标球 / 49 帧球权」仍是**不可复现**的孤证：同一段
`ball_clip.mp4` 用验收参数重跑只有 **16 帧坐标球、0 帧球权**（`build/pull_evt/evt_20261030_225312_00.jsonl`，
201 行）。本轮没有再去追这个差，**报告里不把 144/49 当已复现数字引用**，§8 的缺口登记继续有效。

## 轮次 8 — 现场排练暴露的三个问题（用户当场提问），两个当天修掉

用户看排练时问：「不对，画面有变成错位的了，还有就是 lcd 为什么不显示面板，
为什么不使用刚才那个有事件的片段」。

### 8.1 事件模式的画面是**光板**的 —— 已修

`pc_dashboard._draw_video_panel` 只从 `st.raw["tracks"]` 画框，而 `--proposal` 的 JSONL
里根本没有 `tracks`（`result_publisher.build_frame` 只输出 `players` 的**球场坐标**），
所以事件那一镜画面一个框也没有，只有右侧事件流在滚。

修法（纯主机侧，不动板端协议）：`--calib` 给的 3×3 单应求逆，把球员/球的球场坐标
**回投到画面**画圈（圈在脚下），顶栏改成「事件模式（球场坐标回投，圈在脚下）」。
`load_calib_hinv()` / `_field_to_px()` 两个小函数；核图 `build/_evt_f66.png`：
绿圈与球员、红圈与球都对得上。

### 8.2 推 80 帧只拉回 60 行 —— 已修（这是真丢数据）

`ResultStore` 按 `--store-fsync 30` 攒着写盘，而 `board_wait_results` 的判据是
「行数连续两次不变」——推完那一刻正好停在第 60 行的 flush 边界上，于是被当成"写稳了"，
**最后 20 行被丢掉**，第 166 帧那条 1.09 m 的球权就在里面。

修法：事件模式是我们自己 `Popen` 起的管线，改成**等进程退出**（`board_wait_pipeline_exit`，
读 pidfile 查 `/proc/<pid>`）再拉。复跑：`BOARD_PIPELINE_EXITED` → `lines=80`，
三条球权（f10/11 2.38 m、f30/31 1.93 m、f66 1.09 m）全在。归档
`build/pull/evt_20261031_002911_00.jsonl`。

代价是时间：截短版从「1 min 37 s」变成**约 4 min**——之前那 1 分 37 秒是**拉早了**换来的。

### 8.3 LCD 不亮：是 D028 删的，不是坏了

板上现在只有两个服务（`zynq-idle` 白屏待机、`zynq-stream` 等推流），
`tools/board_pipeline.py` 里**没有任何 LCD 代码**——`tools/lcd_ui.py` 于 2026-11-01 按
**D028**（用户原话「选颜色的ui对识别有用吗，没有就删掉」）删除。所以演示时小屏没有面板显示，
这是当时的决定，不是这次改坏的。要恢复得重开一条决策并重做界面，本轮没动。

### 8.4 HDMI 画面「错位」：已知间歇缺陷，未修

`fb_scanout` 的**间歇整行循环移位**（恒定 `+313` 列）2026-10-31 就登记过，
`HDMI_VISUAL_OK` 因此撤回；这次的观感与它一致。**演示不要依赖 HDMI**：
`--event-no-hdmi` 让它保持白屏待机，画面以 PC 窗口为准。

## 轮次 9 — 板子恢复后的整段验证：LCD 面板 + 250 帧（10 s）跑通

用户：「板子重启好了，继续跑」。第一次起来后**跑到一半又掉线**（`BOARD_PIPELINE_START pid=770`
后约 1 分钟 Jupyter 断、ping 全丢）。可疑点：LCD 初始化里调了一次 `Overlay(flow_system.bit)`，
而 `board_idle` 开机已加载并保持这片比特流，**重复下比特流会清掉 PL 里 HDMI/DMA 的状态**。
改成只走 MMIO 不再加载（`1acbac2`）。**这是怀疑不是证据**——当天板子在没开 LCD 时也掉过三次。

第二次上电后按「先短后长」走：

| 步骤 | 结果 |
|---|---|
| 12 帧短跑（`--lcd-preview 5`） | `LCD_PANEL_ON every=5` → `LCD_PANEL_OK frame=0`；`PIPELINE_OK frames=12`，板子没掉 |
| BRAM 回读 | `LCD_CFG=0x10`；`0x40000000` 起 8 个字 `0x44444444/0x99999999/0xdddddddd/…/0xffffffff` 全部非零 |
| **整段 250 帧 = 10 s**（`pc_dashboard --event --event-every 5 --event-no-hdmi --push-max-frames 250`） | 端到端 **约 8 分钟**；板端 `PIPELINE_OK frames=250 detections=50`，`STAGES detect_ms=75494 ball_ms=90800`；面板刷新 **50 次**（`LCD_PANEL_OK frame=0/45/95/145/195/245`） |
| 拉回 | `BOARD_PIPELINE_EXITED` → `lines=250` → `PULL_OK sha256=e7281e92991ffc4b`（`build/pull/evt_20261031_001959_00.jsonl`） |

整段事件（与之前 80 帧那次逐帧一致）：

- 球权 10 帧：`f110/111 #32 2.38/2.46 m`、`f130/131 #33 1.93/1.95 m`、
  `f166–171 #26 1.09/0.50/1.36/1.87/2.19/2.15 m`（**167 帧起球跟丢后是外推，不采信**）；
- `out_of_play` 0 帧（这条推流路径本来就没报，出界仍未验）；
- 28 帧球场坐标球。

⚠️ **仍未由人确认**：小屏上到底有没有出现面板（机器只证明「数据写进了帧缓存、cfg 生效」，
按 2026-10-31 `HDMI_VISUAL_OK` 撤回的同一纪律，不能代替人眼）；整段时长从之前估的 5 分钟
更正为**约 8 分钟**（实测），现场排练要按这个排。
