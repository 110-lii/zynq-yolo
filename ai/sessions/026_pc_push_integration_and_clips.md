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

## 未决 / 下一步

1. **人眼**：HDMI 白屏与回显、LCD 面板、PC 面板窗口——仍要人看。
2. 演示视频、授权书、全貌图：只能人工（见 `report/submission_checklist.md`）。
3. `--push` 的 GUI 路径没人眼，第一次演示前先跑一遍窗口版。
4. 若要「实时」，得做推流进板 → 带框图从网口回传的闭环（`video_back.py` 目前只吃板本地视频）。
