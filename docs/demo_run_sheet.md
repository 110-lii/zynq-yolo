# 现场演示操作卡（2026-11-01 排练实测版）

> 这份是**照着做就行**的清单，不是说明文。演练一次实测总耗时 **34 s**（推 45 帧那段），
> 数字都来自 2026-11-01 22:42 的真机排练。
> 相关：[`streaming_quickstart.md`](streaming_quickstart.md)（推流细节）、
> [`report/demo_video_storyboard.md`](../report/demo_video_storyboard.md)（录像分镜）。

---

## 0. 开场前 3 分钟检查（三行命令，全绿才开讲）

```bash
py tools/board_jupyter.py "import subprocess,time;print('BOARD_OK', time.strftime('%H:%M:%S'));print(subprocess.run('systemctl is-active zynq-idle zynq-stream; ls /tmp/hdmi_preview.active 2>/dev/null || echo no_preview_flag',shell=True,capture_output=True,text=True).stdout)"
```

期望：

```
BOARD_OK 22:42:03
active
active
no_preview_flag
```

- 两行 `active` = `zynq-idle`（HDMI 待机白屏）与 `zynq-stream`（5007 等推流）都活着；
- `no_preview_flag` = 当前没人占着 HDMI（屏上应是**纯白**，人眼看一眼确认）；
- 不是这个结果就按 [`streaming_quickstart.md`](streaming_quickstart.md) §5 重装服务，或先断电重启板子。

**素材**（已切好，放本机即可）：

```
D:\yt-dlp\downloads\964262880\clips_5min\clip_04_015min.mp4   ← 比赛画面（主用）
D:\yt-dlp\downloads\964262880\clips_5min\clip_05_020min.mp4   ← 备用
```

> clip_01–03 是片头/演播室，**不要用**（clip_01 第 0 帧还是整幅白场）。

---

## 1. 主镜头：一条命令「PC 推流 → 板上处理 → 结果回上位机」

```bash
py tools/pc_dashboard.py --push "D:/yt-dlp/downloads/964262880/clips_5min/clip_04_015min.mp4" --push-start 60 --push-max-frames 45 --csv build/demo_session.csv
```

开窗口跑（不加 `--headless`），**屏上会依次看到**：

| 时刻（排练实测） | PC 面板顶部「连接」 | 同期 HDMI |
|---|---|---|
| 0–2 s | `推流中 连接板子` | 纯白 |
| 2–10 s | `推流中 25 帧 2.1 MB 3.5 fps` | **带框图**（绿框套球员 + `#id`） |
| 10–30 s | `推流中 等板端落盘` | 仍是带框图（板子还在算） |
| 30–32 s | `推流中 拉回结果 45 行` | 转回**纯白** |
| 32–34 s | `回放中` → `回放结束` | 纯白 |

**旁白要点**（数字都是这台机器跑出来的，别改口）：

- 画面里 **7–10 个球员**被框住，并带稳定 `#id`；
- 「机位」一栏会在 `INVALID_VIEW` / `LIMITED_FIELD_VIEW` 之间跳——这是**机位有效性三态**：
  没标定的转播机位不会硬给球场坐标，宁可空着；
- 板端同一时刻的日志（想投屏就 `journalctl`）：`PIPELINE_OK frames=45 detections=3 med_infer_ms=1243`；
- 端到端 34 s，其中**板子算占 16 s**（45 帧），剩下是等落盘与回放。

> 帧号对得上：`PUSH_FRAME_OFFSET start_frame=1500`，记分牌 00:55 就是视频第 60 秒 + 45 帧。

## 2. 板子侧的证据（可选，10 s）

```bash
py tools/board_jupyter.py "import subprocess;print(subprocess.run('journalctl -u zynq-stream -n 25 --no-pager',shell=True,capture_output=True,text=True).stdout[-1500:])"
```

期望看到这一串（顺序固定）：

```
NET_IN_CONNECTED ('192.168.2.100', …)
HDMI_PREVIEW_ON /home/xilinx/zynq-yolo/board/overlay/flow_system.bit
HDMI_PREVIEW_OFF white_restored
PIPELINE_OK frames=45 detections=3 med_infer_ms=…
```

`HDMI_PREVIEW_ON … OFF white_restored` 就是「推流时显示处理后的画面、推完回白」的机器证据。

## 3. 结果文件（给评委看「不是一张图，是数据」）

- `build/pull/net_<时间戳>_00.jsonl`：每帧一行，板上现跑、sha256 校验拉回；
- `build/demo_session.csv`：12 列（帧号/时间/人数/球/出界/球权/机位），Excel 直接开。

```bash
head -5 build/demo_session.csv
```

---

## 4. 如果现场出问题（按顺序降级，别硬撑）

| 现象 | 当场怎么办 |
|---|---|
| 第 0 步不是 `active` / ping 不通 | **断电重启**板子，等 60–90 s 再跑第 0 步；期间先讲架构图 |
| 推流卡在 `推流中` 不动超过 60 s | 看 HDMI 是否已成白屏：成白屏说明板端已处理完，等 `等板端落盘` 那步；连续两次超时就 Ctrl-C，走下行 |
| 板子彻底掉线 | 回放**已经拉回来**的结果：`py tools/pc_dashboard.py --replay build/pull/net_20261030_224217_00.jsonl --video "D:/yt-dlp/downloads/964262880/clips_5min/clip_04_015min.mp4" --video-start 60` —— 画面照出、框对得上（`--video-start 60` 必须带，否则会从片头开始画，框和画面对不上），明确说「这段是刚才板上跑出来的结果」 |
| 评委问「事件呢」 | **照实说**：这条装机的 `zynq-stream` 没开 `--proposal`、也没有匹配标定，所以事件流本来就不会有内容（`events` 恒为空）。出界/进球/球权需要「球 + 球场坐标 + 匹配标定」，本片源 854×480 转播远景三者都不满足。已实测：`--proposal --calib` 的球片段能产出 `events` 对象，但最近球员 3.6 m > 2 m 判据 ⇒ 球权仍不成立（见 `ai/sessions/026` 轮次 7） |
| 想展示**球** | 本片源是 854×480 转播远景，球只有几个像素，**这一路没检出球**（435 帧实测 0 帧）。要展示球请用已有结果回放：`py tools/pc_dashboard.py --replay data/results/ball_air_c03_20261031.jsonl --video data/samples/scenes/c03_t01290.0.mp4 --dump-frame 106`，并说明这是**另一段素材**（1280×720）跑出来的 |

⚠️ **不能说的话**（报告里已撤回或没验过的）：

- 不说「实时」：板端 3.2 fps（开回显）/ 4.4 fps（关回显），检测 1.24 s/次；
- 不说「HDMI 画面已验证」：`HDMI_VISUAL_OK` 已撤回（间歇整行旋转 `+313` 列），只能请评委自己看；
- 不说「球在这段里检出了」：这段没有球；
- 不说「5 分钟片段一口气推完」：7500 帧要 28–39 分钟，演示只用 `--push-max-frames 45`。

---

## 5. 排练记录（2026-11-01 22:42，真机）

```
BOARD_OK 22:42:03 / active / active / no_preview_flag
PUSH_FRAME_OFFSET start_frame=1500
PUSH_DONE frames=45 mb=3.9 fps=5.5
BOARD_RESULT_READY …net_20261030_224217_00.jsonl lines=45 bytes=16210
PULL_OK …sha256=5fde1f820920c7be
REPLAY_DONE 45 frames  →  PC_DASHBOARD_OK frames=45
板端：HDMI_PREVIEW_ON → HDMI_PREVIEW_OFF white_restored →
      STAGES detect_ms=4361 ball_ms=2344 frames=45 →
      PIPELINE_OK frames=45 detections=3 med_infer_ms=1243.3
总耗时 34 s（推流 8 s + 板端处理 16 s + 待落盘/拉回约 5 s + 回放 4.5 s）
```
