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

## 1.5 第二镜：让评委看到**事件**（球权）

开机服务那条路**不产事件**（没 `--proposal`、没标定）。要看到事件，加 `--event`：
它会在板上另起一条带 `--proposal --calib` 的管线（5009 口），推完自动停、清 HDMI 标记。

### ⭐ 演示就这一条命令（2026-11-01 起）

```bash
py tools/pc_dashboard.py --demo
```

它会依次做：板端体检（两个服务在不在跑）→ 起事件管线（HDMI 回显 + LCD 面板）→
推 `data/samples/scenes/c05_t01730.0.mp4` 整段 **250 帧 = 10 s** → 等管线退出 →
拉回 JSONL → 在 PC 面板上回放。等价的手写版是下面这条，参数已经全部定死在 `--demo` 里：

```bash
py tools/pc_dashboard.py --event --event-every 5 --event-lcd 5 --push data/samples/scenes/c05_t01730.0.mp4 --push-max-frames 250 --csv build/demo_events.csv
```

- **为什么是这段**：`data/samples/scenes/MANIFEST.csv` 里 8 段争抢场景，c05 得分最高
  （score 0.9384，其余 0.85–0.89），而且是**唯一**验出球权事件的一段
  （球被检出 + 配对标定同时成立）。
- **整段 250 帧 = 10 s，实测端到端 8–10 分钟**。时间大头是**推流被板端反压**
  （事件模式板端约 1 fps）。日志停在 `PUSH 等板端算完` 是正常的，别以为卡死。
  赶时间就用 `--push-start 4.0 --push-max-frames 80`，代价是只覆盖 3 s。
- **三块屏同时有东西**：HDMI 大屏（带框画面，**行内平移已在起管线时自动标定并补正**）、
  LCD 小屏（状态面板，每 5 帧刷一次）、PC 面板（事件流 + 球场坐标回投）。


- **素材用 `data/samples/scenes/c05_t01730.0.mp4`**（1280×720 中圈机位，250 帧 / 10 s），
  默认标定 `data/calib/field_calib_ball.json` 就是给它配的。这段里球在**红衣 26 号**脚边
  （第 166 帧前后），另外还有两次近身拼抢（第 110、130 帧）。
- 屏上预期：面板「事件」栏出现三条 `球权`（`#32 2.38 m` / `#33 1.93 m` / `#26 1.09 m`），
  CSV 的 `球权ID / 球权距离` 两列有值，左画面红圈套着球、绿框是球员。
- 时间口径：整段 250 帧在板上算 **5 min 04 s**（`detect 75.5 s + ball 90.8 s`），
  再叠加推流反压与拉回，端到端 **8–10 分钟**。手写截短版（从第 4 秒起 80 帧，
  板端回 **80 行**）约 4 分钟。两版事件逐帧一致 ⇒ 现场**如果时间紧**才用截短版；
- 画面上不再是光板：事件模式只给球场坐标，PC 面板借 `--calib` 的单应逆矩阵把球员和球
  **回投成圈**（圈标在脚下），顶栏会写「事件模式（球场坐标回投）」。
- ⚠️ **第 166 帧之后不要解释成"抢断"**：板端球在 166 帧后跟丢、后面几帧是外推值
  （第 167 帧 0.5 m 的圈在空草地上）。讲到第三条球权就停，或照实说「球跟丢后是外推」。
- ⚠️ **别现场说出界**：整段那次板端确实报了 6 帧 `out_of_play`（第 170–175 帧），
  但人工核图显示是**球轨迹跳到白衣球员身上**（真球在 70–94 px 外），这几帧不采信。
  这一段只讲**球权**。

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
| 评委问「事件呢」 | **照实说**：开机服务那条 `zynq-stream` 没开 `--proposal`、也没有匹配标定，所以事件流本来就不会有内容（`events` 恒为空）。出界/进球/球权需要「球 + 球场坐标 + 匹配标定」，clip_04 这种 854×480 转播远景三者都不满足。**要真给评委看事件**，走上面 §1.5 的 `--event` 那一镜（c05 中圈 720p 素材 + 配对标定 ⇒ 三帧球权，已上板验过） |
| 想展示**球** | 本片源是 854×480 转播远景，球只有几个像素，**这一路没检出球**（435 帧实测 0 帧）。要展示球请用已有结果回放：`py tools/pc_dashboard.py --replay data/results/ball_air_c03_20261031.jsonl --video data/samples/scenes/c03_t01290.0.mp4 --dump-frame 106`，并说明这是**另一段素材**（1280×720）跑出来的 |

⚠️ **不能说的话**（报告里已撤回或没验过的）：

- 不说「实时」：板端 3.2 fps（开回显）/ 4.4 fps（关回显），检测 1.24 s/次；
- 不说「HDMI 画面已验证」：行内平移已定量并**每次起管线自动标定 + 补正**（日志判据 `HDMI_ALIGN shift=… residue=0`），但**屏上观感仍要人眼看**，`HDMI_VISUAL_OK` 不记；
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

### 5.1 `--demo` 整段排练（2026-11-01，真机）

```
BOARD_OK SERVICES active active | PREVIEW_FLAG False
BOARD_PIPELINE_START pid=1013 alive=True
板端：HDMI_ALIGN shift=415 residue=0      ← 行内平移补正，补完复测残余 0
      HDMI_PREVIEW_ON → LCD_PANEL_ON every=5 → LCD_PANEL_OK ×50
PUSH_DONE frames=250 mb=52.4 fps=0.7       ← 约 6 分钟
板端：HDMI_PREVIEW_OFF white_restored → PIPELINE_OK frames=250 detections=50
      STAGES detect_ms=75518 ball_ms=91026 → TEAM_ASSIGN tracks=52 labeled=38
结果：10 帧球权（f110/111 #32 2.38/2.46、f130/131 #33 1.93/1.95、f166–171 #26 1.09/…）
      32 帧球场坐标球、out_of_play 0 帧；与 10-31 那次逐帧一致
```

⚠️ 这次卡了两处、都已修（下一版不会再有）：`PUSH 等板端算完` 的等待原来是 300 s，
而**推完 ≠ 算完**（收尾还压着几十帧 JPEG，板端要再追 3–5 分钟），超时后紧接着拉结果
撞上「还没写完」，报 `板端没有返回数据`。现在等待放宽到 1200 s 且每 60 s 打一行进度，
拉取也带 3 次重试。**现场若看到 `BOARD_PIPELINE_WAIT`，那是板子还在算，不是卡死。**

```
