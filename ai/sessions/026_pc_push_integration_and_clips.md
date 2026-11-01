# Session 026 — 2026-11-01 —— 推流集成进上位机（一条命令闭环）、整场回放切 10 段

> 承接 [025](025_boot_idle_stream_ui_removal.md)（删选色 UI / HDMI 待机白屏 / 上电即用）。
> 用户本轮原文：「现在把推流也集成到软件里，并且把D:\yt-dlp\downloads\964262880切成5分钟的片段，10段」。

| 字段 | 内容 |
|---|---|
| 会话号 | 026 |
| 日期 | 2026-11-01 |
| 执行 AI（模型 ID） | `mythos-magpie-5487751739`（客户端 `claude`，桌面端 Code 标签页） |
| 执行客户端 / 工具 | Claude Code（桌面端）+ 板端通道 `tools/board_jupyter.py`（Jupyter 9090） |
| 角色 | 单人 |
| 任务目标 | ① `pc_dashboard.py` 里直接推流并取回结果；② 整场回放切 10 段 5 分钟 |
| 上下文投喂 | `AGENTS.md`、`tools/video_send.py`、`tools/pc_dashboard.py`、`tools/board_jupyter.py`、`tools/install_boot.py`、`docs/streaming_quickstart.md` |
| **原文位置** | **待导出**：本会话在 Claude Code 桌面端，仓库仍无 Claude Code 会话导出脚本（同 023–025 的已知缺口，不编造文件名） |
| 关联决策 | 落实 **D015**（PC 送视频进主线）与 **D027**（输入侧协议）；D029（上电即用）的服务直接复用 |
| 复核方 | 未复核（GUI 路径靠 `--headless` 同链路验证，窗口本身未人眼） |

---

## 轮次 1 — 整场回放切 10 段

`D:\yt-dlp\downloads\964262880` 是一个**目录**，里面是
`2026中超第06轮 大连英博海发-河南俱乐部彩陶坊 全场回放 (964262880).mp4`：
2.0 GB、02:16:06.88、854×480、25 fps、h264/aac。本机**没有 ffprobe**，只有
`D:\yt-dlp\ffmpeg\ffmpeg.exe`，所以时长靠解码侧确认，不靠元数据猜。

用 `-ss <起点> -i <源> -t 300 -c copy -avoid_negative_ts make_zero` 切 10 段，落在
`clips_5min/clip_01_000min.mp4` … `clip_10_045min.mp4`（实测时长 05:00.08–05:00.34，每段 73–86 MB）。
`-c copy` 不重编码 ⇒ 不引入新的压缩损失（这点对球链路重要：q80 的假球教训见 024）。

**顺手量到一件影响演示的事**：按 5 s 采样统计草皮像素占比后，只有 **clip_04 起才是比赛画面**，
clip_01–03 是片头/演播室；clip_01 第 0 帧均值 **253**（整幅白场）。
这也解释了 024 里「白帧多出假球」的现象——白场本身就该被排除在外。

---

## 轮次 2 — 把推流搬进上位机

### 为什么要动结构

原来演示要两个终端：`video_send.py` 推完，还得上板把 `net_*.jsonl` 捞回来才能看。
用户要求「集成到软件里」，所以做成一条：

```bash
py tools/pc_dashboard.py --push <视频> --push-start 60 --push-max-frames 45
```

### 改了哪些文件

| 文件 | 改动 |
|---|---|
| `tools/video_send.py` | 推流主体抽成 `push_frames()`（CLI 与上位机**同一份**协议实现）；新增 `--start`；**socket 写超时 5 s → 120 s**；自检加跑一遍 `push_frames` 回环 |
| `tools/board_jupyter.py` | 新增 `run_capture()`（把板端 stdout 收成字符串）与 `configure()`（换地址）；`run()` 行为不变 |
| `tools/pc_dashboard.py` | 新增 `--push/--board/--jupyter/--store-dir/--store-prefix/--push-max-frames/--push-start/--push-fps/--push-quality/--pull-dir`；`push_worker` 串「推 → 等落盘 → 拉回 → 回放」；`VideoSource` 支持帧号偏移；状态条新增 `推流中`；自检新增推流判据用例 |
| `docs/streaming_quickstart.md` | 新增 §2.5「一条命令走完」，写入三个坑 |
| `tools/README.md` | 两行工具说明更新到实测状态 |

进度回报全部走原有队列（`("status", …)` / `("frame", …)`），所以 GUI 与 `--headless`
走的是**同一条渲染路径**，不存在「窗口里一套、截图里另一套」。

---

## 轮次 3 — 四个只有上板才会暴露的坑（★ 自我纠错轨迹）

第一次真跑，界面**静默十分钟**。逐个挖出来：

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 1 | `PUSH_FAIL 板端不可达：not enough arguments for format string` | 板端代码模板用 `%` 拼参数，里面 `print("N %d" % …)` 没转义，外层格式化把它吃掉了 | 模板内一律 `%%d`；并把模板 `compile()` 进自检式检查 |
| 2 | 推流线程抛 `TimeoutError: timed out`，界面永远停在「推流中」 | `create_connection(timeout=5)` 的 5 s 超时会**留在 socket 上**；板端跑 ncnn + 写 SD 时 `sendall` 卡过 5 s 就炸。线程异常没人接，主循环等不到任何状态 | `sock.settimeout(120)`；异常收进 `OK/FAIL` 返回值；`push_worker` 整体兜底成 `PUSH_FAIL` |
| 3 | 拉回一个 **0 行**文件却报 `BOARD_RESULT_READY` | `ResultStore` 是攒着写的：推完那一刻文件已建、内容还在缓冲 | 判据加「行数 > 0」 |
| 4 | 改成「只认推流后新建的文件」后，**一个文件都找不到** | `zynq-stream` 在**服务启动时**就把 `net_*.jsonl` 建好（`STORE_DIR` → `NET_IN_LISTEN` 同一批日志），推流数据写进的是这个**已有**文件 | 改成**快照比增长**：推流前记下每个文件的字节数，取「增长最多且非空」的那个 |

> 第 4 条顺带纠正了一个想当然的前提：**不能拿主机时间去比板端 mtime**。
> 实测板端时钟停在 `2026-10-30`，主机是 `2026-11-01`——比时间戳这条路从一开始就不成立。
> 现在整套判据只剩「文件变长了没有」，与时钟无关。

---

## 轮次 4 — 上板实测

板子在线，两个服务 `active`。用 `clips_5min/clip_04_015min.mp4`（比赛画面）第 60 秒起推 45 帧：

```
STATUS PUSH 开始推送
STATUS PUSH 25 帧 2.1 MB 3.5 fps
PUSH_FRAME_OFFSET start_frame=1500
PUSH_DONE frames=45 mb=3.9 fps=5.5
BOARD_RESULT_READY /home/xilinx/zynq-yolo/build/flow/net_20261030_222520_00.jsonl lines=45 bytes=16210
PULL_OK D:\zynq-yolo\build\pull\net_20261030_222520_00.jsonl lines=45 sha256=5fde1f820920c7be
STATUS REPLAY_START
FRAME 9 players=10 ball=False cam=INVALID_VIEW events={}
FRAME 29 players=7 ball=False cam=LIMITED_FIELD_VIEW events={}
PC_DASHBOARD_OK frames=45
```

板端同一时刻：`PIPELINE_OK frames=45 detections=3 med_infer_ms=…`，`HDMI_PREVIEW_ON/OFF` 正常成对。

出图 `build/push_test.png`：画面是中圈附近的传控，**绿框 ID #1…#9 落在球员身上**，
记分牌 00:55 ⇒ 帧号偏移（`start_frame=1500`）对得上，画面与检测框不错位。

**边界**（现场不能说过头）：

- 板端约 2–3 fps，5 分钟片段整段推要半小时 ⇒ 演示用 `--push-max-frames` 限长；
- 拉回的是**最新一段**结果，不做多段拼接；
- GUI 窗口本身没盯着看过（同一队列/同一渲染，`--headless --dump-png` 验过）；
- 球这一段没判出来（`ball=False`）：中圈远处的球只有几个像素，属已知边界，不是本次改动引入的。

---

## 轮次 5 — 「为什么这么慢」：把 3 fps 拆开算

用户追问两遍：「为什么这么慢」「现在不是隔几帧才识别一次吗，为什么还这么慢」。
不猜，量：同一段（clip_04 第 60 秒起）各推 **150 帧**，只改一个变量。

| 配置 | 墙钟 | 吞吐 | 板端 `STAGES` |
|---|---|---|---|
| 开 `--hdmi-preview`（当前装机） | 47 s | **3.2 fps** | `detect_ms=12752 ball_ms=9066 frames=150 detections=10` |
| 关回显（`install_boot --no-hdmi-preview`） | 34 s | **4.4 fps** | 同上 |

摊到每帧（150 帧）：检测 1.27 s/次 ÷ 15 = **85 ms**；球候选 **60 ms**（`--ball-every 5`，
每次约 300 ms）；跟踪 + 机位补偿 + JPEG 解码 + 落盘 **≈82 ms**；回显再 **+86 ms**。
合计 313 / 227 ms ⇒ 3.2 / 4.4 fps。

**结论**：「每 15 帧检测一次」只摊掉了一部分成本，剩下三项每帧都在跑；
**网络完全不是瓶颈**——150 帧 15 MB 走 47 s = 0.44 MB/s，千兆口闲着。
另外历史记录里 2.2–2.3 fps 与今天的 3.2–4.4 fps 是同一量级，**没有变慢**。

测完已把服务改回开回显（用户此前明确要求推流时 HDMI 显示处理后的画面），
并在 `docs/streaming_quickstart.md` §2.5 写明「要快的顺序：关回显 > 拉大 `--every` >
拉大 `--ball-every`；想真正实时得换架构」。

## 轮次 6 — 排练一遍演示（用户：「现在模拟一遍」）

在板子在线状态下照现场顺序真跑了一次（22:42），总耗时 **34 s**：

```
BOARD_OK 22:42:03 / active / active / no_preview_flag
PUSH_FRAME_OFFSET start_frame=1500
PUSH_DONE frames=45 mb=3.9 fps=5.5
BOARD_RESULT_READY …net_20261030_224217_00.jsonl lines=45 bytes=16210
PULL_OK …sha256=5fde1f820920c7be      （与下午那次逐字节相同 ⇒ 同一输入可复现）
板端：HDMI_PREVIEW_ON → HDMI_PREVIEW_OFF white_restored →
      STAGES detect_ms=4361 ball_ms=2344 frames=45 →
      PIPELINE_OK frames=45 detections=3 med_infer_ms=1243.3
```

排练暴露并当场补上了一个洞：**降级方案里回放同一份 JSONL 会画错画面**。
推流用了 `--push-start 60`（板端 `frame=0` = 视频第 1500 帧），而 `--replay` 从第 0 帧画，
框就和画面对不上。于是给 `pc_dashboard` 加了 `--video-start <秒>`（换算成 `VideoSource.start`），
并实测降级命令：`--replay build/pull/net_20261030_224217_00.jsonl --video clip_04 --video-start 60`
→ 记分牌 00:54，框落在球员身上。

另外记一条**现场必须知道**的事实：这批 854×480 转播素材在板上**一球未检出**
（5 段共 435 帧，`ball.valid` 全为 False），所以演示词不能吹球；要展示球得回放已有的
c03（1280×720）结果，并说明是另一段素材。

产物：`docs/demo_run_sheet.md`（开场检查、逐秒预期、板端证据、四档降级、不能说的话）。

## 轮次 7 — 「是不是什么事件都检测不出来」：三层原因，逐层实测

用户问得很准：排练里事件流一直是空的。查下来不是一件事，是三层叠在一起：

| 层 | 事实 | 怎么证的 |
|---|---|---|
| ① **装机服务根本不跑事件代码** | `zynq-stream` 的 ExecStart 没有 `--proposal`，而 `events` 只在 `--proposal` 分支里算 | 拉回的 `net_*.jsonl` 每行只有 `frame/detected/tracks/camera_state`，**没有 `events` 键** |
| ② **服务也没有标定** | 出界/进球/球权都基于球场坐标 | `journalctl` 里从没出现过 `STORE_DIR` 之外的 `--calib`；`camera_state` 恒为 `LIMITED_FIELD_VIEW` |
| ③ **有标定也未必有事件** | 用 `--proposal --calib data/calib/field_calib_ball.json` 推 `build/ball_clip.mp4` 202 帧：16 帧球场坐标球、`VALID_FIELD_VIEW`、**`events` 对象正常产出**；但最近球员 **7.12 m（最小 3.6 m）> 3 m 判据** ⇒ `possession=None`、`out_of_play=False` | `build/pull_evt/evt_20261030_225312_00.jsonl`（已入库） |

另测了「标定错配」的样子：拿 c03 的标定去推 854×480 转播画面，球投影到 y≈93 m（场地只有 68 m），
系统按设计退回画面坐标 —— 这说明**换素材必须重新标定**，不能借用。

**证据链缺口（如实记）**：报告里「202 帧 144 坐标球 / 49 帧球权」那次是
`--calib field_calib.json --proposal` 跑的，`/tmp/out.json` 没归档；仓库里现存的
`data/results/*.jsonl` **没有一份带 `events`**。今天这份 `pull_evt` 是唯一在库的、带 `events` 的板上输出。

**处置**：不为了演示去调 `possession` 的 3 m 判据；演示词按 `docs/demo_run_sheet.md`
新增的「评委问事件呢」一行照实说。

## 未决 / 下一步

1. **人眼**：HDMI 白屏与回显、LCD 面板、PC 面板窗口——仍要人看。
2. 演示视频、授权书、全貌图：只能人工（见 `report/submission_checklist.md`）。
3. `--push` 的 GUI 路径没人眼，第一次演示前先跑一遍窗口版。
4. 若要「实时」，得做推流进板 → 带框图从网口回传的闭环（`video_back.py` 目前只吃板本地视频）。
