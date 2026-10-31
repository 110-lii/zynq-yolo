# Session 023 — 2026-10-31 —— PC 端「看真比赛」视频视图；球从「永远找不到」到间歇找到；三块屏统一到一份数据

> 承接 [022](022_hdmi_osd_full_flow.md)（HDMI 带框环出、面板提速、`HDMI_VISUAL_OK` 撤回）。
> 用户本轮的诉求逐字：「**加视频视图，我要在 PC 上看真比赛，现在重启了，还有一个问题面板和显示的不一致，球也没找到在哪**」。

| 字段 | 内容 |
|---|---|
| 会话号 | 023 |
| 日期 | 2026-10-31 |
| 执行 AI（模型 ID） | `mythos-magpie-5487751739`（客户端 `claude`，桌面端 Code 标签页） |
| 执行客户端 / 工具 | Claude Code（桌面端），通道：`tools/board_jupyter.py`（Jupyter 9090） |
| 角色 | 单人 |
| 任务目标 | ① PC 面板增加「比赛画面 + 检测框」视图；② 查清「球没找到」；③ LCD / HDMI / PC 三处显示用**同一份**数据 |
| 上下文投喂 | `AGENTS.md`、`ai/coordination/DECISIONS.md`（D010/D011/D012/D017/D018/D026）、`tools/board_pipeline.py`、`tools/ball_cv_probe.py`、`tools/ball_track.py`、`tools/pc_dashboard.py`、`tools/status_display.py`、`src/rtl/io/hdmi/fb_scanout.v` |
| **原文位置** | **待导出**：本会话在 Claude Code 桌面端，`ai/tools/export_transcript_dsh.py` 只覆盖 `dsh` 客户端；本仓库尚无 Claude Code 会话导出脚本（如实记，不编造文件名） |
| 关联决策 | D011（球识别在范围内）、D018（面板 / PC 软件）、D026（HDMI 带框视频） |
| 复核方 | 未复核（屏幕类结论仍待人眼） |

---

## 轮次 1 — 「加视频视图，我要在 PC 上看真比赛」

**输入（逐字）：**

```
加视频视图，我要在 PC 上看真比赛,现在重启了，还有一个问题面板和显示的不一致，球也没找到在哪
```

**做了什么**：`tools/pc_dashboard.py` 增加 `VideoSource`（按帧号取画面，跳帧才 seek）、
`_draw_video_panel()`（画面缩放进左栏 + 绿框 + `#id`）、`--video` 与 `--dump-frame` 参数；
左栏原「105×68 球场俯视图」在给了 `--video` 时改为「比赛画面 · 带框」，
底部的「球权 / 球 / 出界」一行两种视图共用。

**判据**：`PC_DASHBOARD_SELFTEST OK`；`--replay data/results/ball_air_c03_20261031.jsonl --video c03_t01290.0.mp4 --dump-frame 106`
出图见 [`report/figures/pc_dashboard_video_ball.png`](../../report/figures/pc_dashboard_video_ball.png)：
真球上画了红圈 + `BALL`，顶栏「球 可见」、底栏「球：画面 (739, 212) px」。
LCD 面板同帧出图 [`report/figures/lcd_panel_ball_20261031.png`](../../report/figures/lcd_panel_ball_20261031.png)。
本次板上原始输出归档为 [`data/results/ball_air_c03_20261031.jsonl`](../../data/results/ball_air_c03_20261031.jsonl)（250 行）。

### 面板与画面「人数不一致」的根因（已修）

`normalize()` 解析 `tracks` 要求 `len(t) >= 8`（第 6/7 项是球场坐标）。
**没有标定时 board_pipeline 只写 6 项**，于是每一行都被丢掉：视频上画着 13–15 个框，
顶栏写「人数 0」。判据：同一份 JSONL 修复前 `人数 0`、修复后 `人数 13`（帧 149）/`15`（帧 106），
与画面里能数出的框数一致。没有球场坐标时用框中心当**画面坐标**（`space=px`），
俯视图只画 `space=field` 的人。

---

## 轮次 2 — 「球也没找到在哪」：不是球看不见，是它飞在草场外面

**做法**：先不带任何假设，把 c03 片段逐帧全幅扫「小白块」。

**结果（主机侧）**：第 140–158 帧有一个 10×10 px 的白块，从 `(769,182)` 平滑下落到 `(857,235)`，
肉眼放大后确认为球 —— 它落在**广告牌上方的深色背景**里，而 `ball_cv_probe.candidates()` 的
白掩膜是 `white & grass`，草场掩膜之外的整块被抹掉，候选表恒为 0。
这与 D010 记的「球只有 4–12 px」并不矛盾：球看得见，只是**假设球一定在草上**这个前提错了。

**改法**：`candidates(..., air_search=True)` 追加一遍**草场掩膜之外**的小白块搜索，
形状门限更紧（面积 25–420、最长边 ≤28、长宽比 0.6–1.7、圆度 ≥0.55），静态白点靠帧差挡；
`board_pipeline` 默认开启（`--no-ball-air` 可关）。

**板上实测（C 分类器，`ball_cls_c`）**：

| 帧 | 候选 | 分类器得分 | 结论 |
|---|---|---|---|
| 95 | (824, 240) | 0.945 | 球 |
| 100 | (777, 218) | 0.970 | 球 |
| 105 | (739, 212) | 0.923 | 球（放大核对：白块正在框中心） |
| 110 | (705, 222) | 0.154 | 同一个球，飞进暗背景后掉分 |
| 140 | (769, 182) | 0.849 | 球 |
| 145 | (804, 176) | 0.892 | 球 |

### 顺带挖出的两个既有 bug（都在 `board_pipeline.py`）

1. **`bc` 每帧不清零**：只在扫球帧赋值，非扫球帧把**上一帧的候选**再喂给跟踪器，
   等于同一批候选重复计为命中 —— 板上 JSONL 里 `f105–f109` 同一坐标重复 5 帧就是这么来的。
2. **`ball` 永不清空**：没有确认轨迹时沿用上一次的值，250 帧里 150 帧「有球」，
   坐标还是几分钟前的 —— 面板「球：可见」因此在骗人。现在无轨迹时清空
   （跟踪器自己会外推 `miss ≤ max_miss` 的几帧），并顺手给 `rec` 补上 `ball` / `camera_state`
   —— 以前**只有 `--proposal` 的行才带球**，落盘 JSONL 里根本没有球字段。

另：`ball_track.BallTrack` 增加 `score`（取分类器得分**峰值**），轨迹选择从「比 hits」改成
「先比得分、再比 hits」—— 老的白球衣误检轨迹 hits 常年 20+，新锁上的空中球只有 2–3 次，
按 hits 选永远选不中。并允许**已确认**轨迹用低分候选（`--ball-cls-weak`，默认 0.12）续跟。

**末态（`c03e` 运行）**：250 帧里 20 帧带球坐标，两段 —— `f100–f110`（777→772→739→705 连续下移）
与 `f140–f144`（769,182）。**仍然是断续的**，不是「球检测做完了」：D010 记的物理限制没过，
只是把「恒为 0」变成了「能看见的时候真的给出来」。

---

## 轮次 3 — 三块屏用同一份数据

| 输出 | 命令（板端） | 结果 |
|---|---|---|
| 数据 | `board_pipeline --video c03 --every 15 --max-det 0 --no-pl --no-camera-gate --ball-every 5 --store-dir build/flow --store-prefix c03e` | `PIPELINE_OK frames=250`；ball 20 帧；人手 42 条轨迹 / 15 条贴了队别 |
| LCD | `status_display --replay c03e…jsonl --frame 106 --size 800x480 --fb-bram 0x40000000 --once` | `STATUS_DISPLAY_BRAM … words=48000 via PYNQ`，**9.9 s** |
| HDMI | `hdmi_video --replay c03e…jsonl --video c03 --fps 25 --timeout 30` | `HDMI_VIDEO_OK 375 25.0 status={'frames': 19128, 'under': 0}`，实测 12.5 fps |
| PC | `pc_dashboard --replay c03e…jsonl --video c03 --loop` | 窗口已起；**人眼看屏未做**（我只能看 PNG） |

**踩坑（值得记）**：`status_display` 会把 JSONL 的**每一行**都写一次屏，
250 行 ×（48,016 个字 MMIO 写）≈ 卡住 3 分钟以上，看起来像板子挂了。
现在加了 `--frame N`：出演示面板指定帧号，只写一次（9.9 s）。

## 轮次 4 — HDMI 复查：仍然是移位，不是修好了

用 `build/hdmi_pixel_probe.py` 的板端代码逐像素问「源列 c 落在屏上第几列」：

```
PIX r=300 c=   0 sum=1 px= 313 SHIFT      PIX r=300 c= 640 sum=1 px= 953 SHIFT
PIX r=300 c=1279 sum=1 px= 312 SHIFT      PIX r=  0/1/2/3 c=640 sum=1 px=953 SHIFT
```

- **每条源列都 +313**（1279+313 = 1592 → 回绕到 312），sum=1 表示**没有丢点、没有重复**；
- 行方向正确（r=0/1/2/3/717/718/719 都对）。

⇒ 与 022 §7.31 记的间歇缺陷同型，今天的 k=313。取址逻辑本轮只读未改：
`rd_idx = dbuf*LBUF + xpos`、`wr_idx = d_line[1:0]*LBUF + (d_col + b_i)`，
两边都是「列号即源列」，恒定的整行旋转说明**写侧那一行的起点不是源列 0**，
而不是显示侧读错地址。按 022 的结论，先要一个**多笔在飞**的 AXI 从机 TB 才能在仿真里复现，
没复现之前不出新片。**演示走 PC / 网口，不把 HDMI 当交付通道。**

---

## 未决 / 下一步

1. **球的断续**：`f110` 之后与 `f150` 之后仍会断（分类器掉到 0.1 量级）。
   弱候选续跟只把它从 150 帧拖到 110 帧那一整段；再往前需要「按运动 + 形状」的续跟或更好的 ROI 分类器。
   任何「球帧上涨」都必须按 012 的纪律人工抽查，不能只看计数。
2. **HDMI 整行旋转**：见上，需要多笔在飞的 TB。
3. **人眼终验**：LCD 面板、HDMI 环出、PC 面板三处的**画面**本轮都只有机器判据 + PC 端 PNG；
   上板后要人眼看屏（AGENTS.md 第 5 条）。
4. 提交包 / 报告尚未按本轮改动更新（`report/design_report.md`、`dist/`）。
