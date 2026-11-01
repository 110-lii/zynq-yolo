# 电脑推流到板子：怎么用（2026-11-01）

> 对应决策 **D029**（上电即用）与 **D027**（PC → 板的以太网视频入口）。
> 一句话：**板子上电后什么都不用做；在电脑上跑一条 `video_send.py` 就是推流。**

---

## 0. 已验证到哪一步（2026-11-01 实测）

| 判据 | 结果 |
|---|---|
| 服务装好 | `BOOT_SERVICE_STATE active active` |
| 白屏 | `IDLE_OVERLAY_OK` + `IDLE_HDMI_WHITE_OK frames=31 under=0` |
| **真重启后自启** | `reboot` 后 18 s：`IDLE_HDMI_WHITE_OK frames=35 under=0`，两个服务 `active` |
| 重启后推流 | PC 推 20 帧 → 板端 `PIPELINE_OK`、落盘 `net_*_00.jsonl` 20 行 |
| **一条命令闭环**（`pc_dashboard --push`） | 推 45 帧 → `BOARD_RESULT_READY lines=45` → `PULL_OK sha256=…` → 回放 45 帧，画面 7–10 人带框 |
| 白屏**人眼** | ⏳ **未确认** —— 机器只说明配置生效、扫描在跑；屏上是不是白的，要人看一眼 |

> 两个只有上板才会踩到的坑（已写进 `install_boot.py`）：
> PYNQ 的 pynq 在 `/usr/local/share/pynq-venv`，`/usr/bin/python3` 里没有；
> systemd 不读 Jupyter 的环境，必须 `Environment=XILINX_XRT=/usr`，否则 `No Devices Found`。

## 1. 先确认板子已经「开机就绪」

装了 D029 的服务之后，上电约 30–60 s 板子自动完成三件事：

1. 加载 `board/overlay/flow_system.bit`；
2. **HDMI 显示纯白**并一直保持（空闲待机；`zynq-idle` 服务常驻）；
3. 在 **5007/TCP** 等 PC 推流（`zynq-stream` 服务）。

在电脑上验一下（不通就是板子还没起来或掉线了，先上电重启）：

```bash
py tools/board_jupyter.py "import subprocess;print(subprocess.run('systemctl is-active zynq-idle zynq-stream',shell=True,capture_output=True,text=True).stdout)"
```

期望两行 `active`。若第一次还没装过服务，看 §5。

## 2. 推一段视频文件（最常用）

```bash
py tools/video_send.py --video data/samples/scenes/c03_t01290.0.mp4 --host 192.168.2.99 --port 5007 --fps 25
```

- 默认 **JPEG q95**。别改成 80：q80 会让板端多出 10 帧假球（球员白鞋被压缩成类球纹理，见 `ai/sessions/024` 轮次 7）。
- `--fps` 只是**上限**，实际吞吐由板子反压决定：**实测 3.2 fps（开 HDMI 回显）/ 4.4 fps（关回显）**，历史记录另有 2.2–2.3 fps。慢在哪一步见 §2.5 的分解表——**不是网络，也不是「隔几帧才检测一次」**。
- 结尾会打印 `VIDEO_SEND_OK <帧数> <MB> <MB/s> <fps>`。

## 2.5 一条命令走完：推流 → 板端处理 → 拉回 → 看带框画面（演示用这个）

推流和取结果本来要两个终端（`video_send.py` 推、再上板捞 `net_*.jsonl`）。
现在 `pc_dashboard.py --push` 把它们串成一条：

```bash
py tools/pc_dashboard.py --push "D:/yt-dlp/downloads/964262880/clips_5min/clip_04_015min.mp4" --push-start 60 --push-max-frames 45
```

它会依次做四件事，屏上先显示进度、后显示带框画面：

1. 拍一张板端结果目录的「文件→字节数」快照；
2. 推流（`video_send.push_frames`，与命令行**同一份**协议实现），状态条显示 `推流中 25 帧 …`；
3. 轮询板端，等那份 JSONL 的行数稳定下来；
4. 用 sha256 校验着拉回本地 `build/pull/`，然后走普通回放路径。

| 参数 | 作用 |
|---|---|
| `--push <视频>` | 要推的视频；同时作为画面源（帧号才和检测框对得上） |
| `--push-start <秒>` | 从第几秒开始推（跳过片头/中场休息），画面会自动平移帧号 |
| `--push-max-frames <N>` | 只推前 N 帧。**演示必填**：板端 3.2–4.4 fps，5 分钟片段（7500 帧）要推 28–39 分钟 |
| `--push-fps` / `--push-quality` | 同 `video_send.py`；质量别降到 95 以下 |

### 2.5.1 要看**事件**（出界/进球/球权）：加 `--event`

装机服务 `zynq-stream` **不开 `--proposal`、也不带标定**，所以它的 JSONL 里
`events` 恒为空（这是设计，不是坏了：主路径不吃标定，换素材不用重标）。
想看事件就加一个开关，它会**在板上另起一条**带 `--proposal --calib` 的管线（5009 口），
推完自动停掉、清掉 HDMI 占屏标记：

```bash
py tools/pc_dashboard.py --event --event-every 5 --push data/samples/scenes/c05_t01730.0.mp4 --push-start 4.0 --push-max-frames 80 --csv build/demo_events.csv
```

| 参数 | 说明 |
|---|---|
| `--event` | 起/停板端事件管线（只作用于 `--push`）。**2026-11-01 已上板验过**：250 帧整段 5 min 04 s；上面这条 80 帧截短版约 4 min（事件模式板端 1.1 fps）。推完会**等管线自己退出**再拉结果，否则 `--store-fsync 30` 会截掉尾部不到 30 行 |
| `--calib` | 标定文件，**必须与素材配对**，默认 `data/calib/field_calib_ball.json`（配 `c05_t01730.0.mp4`） |
| `--event-every` | 检测间隔。25 = 验收口径；推演示素材时用 5，球员坐标更新更勤（非检测帧靠跟踪器外推，越拖越偏） |
| `--event-ball-every` | 扫球间隔，默认 2；别再调大，这段 250 帧实测调大就采不到球 |
| `--event-no-hdmi` | 不回显到 HDMI（默认回显，屏上能看到框 + 球）；关掉更快 |
| `--event-lcd N` | 每 N 帧把状态面板写到板载 LCD（默认 5，0=关）。面板与 PC 面板同源，走 `status_display.render` → `bram_fb.write_image4`（800×480 4bpp） |

结果同上：面板「事件」栏会出现 `球权 #id（1.x m）`，CSV 的 `球权ID / 球权距离` 两列有值。
**只有 `data/samples/scenes/c05_t01730.0.mp4`（1280×720 中圈机位）这一路同时满足「球被检出 + 有配对标定」**；
854×480 的转播素材这一路一球未检出，事件必然为空（见 §2.5 的边界说明）。
板端实跑结果（250 帧整段）落盘为 `build/pull/evt_20261030_232814_00.jsonl`：28 帧球场坐标球、
**10 帧球权**，面板上出现三条 `球权`（最近 1.09 m 那条是第 166 帧；0.50 m 那条是球跟丢后的外推，
不采信）。截短版是 `build/pull/evt_20261031_002911_00.jsonl`（80 帧 / 80 行）。
事件模式的 JSONL 只有球场坐标、没有像素框，PC 面板会用 `--calib` 的单应逆矩阵把球员和球
回投成圈（圈标在脚下），顶栏写「事件模式（球场坐标回投）」。

| `--board` / `--jupyter` | 板端推流口与 Jupyter 口（默认 `192.168.2.99:5007`、`:9090`） |
| `--store-dir` / `--store-prefix` | 板端结果目录与文件前缀（与 `zynq-stream` 服务一致） |

实测（2026-11-01，clip_04 第 60 秒起推 45 帧）：

```
PUSH_FRAME_OFFSET start_frame=1500
PUSH_DONE frames=45 mb=3.9 fps=5.5
BOARD_RESULT_READY /home/xilinx/zynq-yolo/build/flow/net_20261030_222520_00.jsonl lines=45 bytes=16210
PULL_OK D:\zynq-yolo\build\pull\net_20261030_222520_00.jsonl lines=45 sha256=5fde1f820920c7be
PIPELINE_OK frames=45 detections=3 med_infer_ms=…
```

三个**只有上板才会暴露**的坑（都已写进代码注释，别「优化」掉）：

- **不能比时间戳**：板子不对时，板端 mtime 停在 10-30，与主机时间没法比；判据改成「文件变长了」。
- **不能只认新建文件**：`zynq-stream` 在**服务启动时**就把 `net_*.jsonl` 建好，推流数据写进这个已有文件。
- **空文件不算结果**：`ResultStore` 是攒着写的，推完那一刻文件可能还是 0 行 0 字节。

### 为什么只有 3–4 fps（实测分解）

「检测每 15 帧才跑一次」不代表就快：**检测只是每帧成本的一部分**。150 帧、同一段视频实测：

| 每帧成本 | 数值 | 说明 |
|---|---|---|
| ncnn 检测 | **≈85 ms/帧** | 1.27 s/次 ÷ 15 帧（`--every 15`，共 10 次 = 12.75 s） |
| 球候选 | **≈60 ms/帧** | `--ball-every 5`，每次约 300 ms（共 9.07 s） |
| 跟踪 + 机位补偿 + JPEG 解码 + 落盘 | ≈82 ms/帧 | 每帧都跑，省不掉 |
| HDMI 回显（画框 + 拷 3.7 MB 帧缓冲） | ≈86 ms/帧 | 只在开 `--hdmi-preview` 时；关掉实测 3.2 → **4.4 fps** |
| **合计** | **313 ms/帧（开回显）/ 227 ms（关）** | ⇒ 3.2 / 4.4 fps |

网络完全不是瓶颈：150 帧 15 MB 走了 47 s = **0.44 MB/s**，千兆口闲着。
所以要快，顺序是：**关回显（+38%）> 拉大 `--every` > 拉大 `--ball-every`**；
想真正实时得换架构（PL 预处理 + 小窗口），调参救不回来。

关回显：`py tools/install_boot.py --no-hdmi-preview`；恢复：`py tools/install_boot.py`。

## 3. 推电脑摄像头（现场「实拍进板」）

```bash
py tools/video_send.py --camera 0 --host 192.168.2.99 --port 5007 --fps 15
```

⚠️ 摄像头分支**还没在板上联调过**（文件分支已验等价）。第一次用先在电脑上单跑，确认 OpenCV 能开摄像头。

## 4. 推完之后结果在哪

- 板端每处理完一段，落盘一份 JSONL：`/home/xilinx/zynq-yolo/build/flow/net_<时间戳>_00.jsonl`（250 行左右，每 15 帧一次检测）。
- 看服务日志（含 `PIPELINE_OK frames=… detections=…`）：

```bash
py tools/board_jupyter.py "import subprocess;print(subprocess.run('journalctl -u zynq-stream -n 60 --no-pager',shell=True,capture_output=True,text=True).stdout[-4000:])"
```

- **注意**：`board_pipeline --out xxx.json` 写的是**汇总 JSON**，不是逐帧 JSONL。要喂给 `video_back.py --tracks`，用 `build/flow/net_*_00.jsonl`。

## 5. 服务没装 / 想重装

```bash
py tools/install_boot.py --dry-run     # 只看要写什么，不碰板子
py tools/install_boot.py               # 推文件 + 装两个服务 + enable + 立即启动
py tools/install_boot.py --no-stream   # 只装 HDMI 白屏待机
py tools/install_boot.py --uninstall   # 撤掉（文件保留）
```

> 板和 PC 必须同一网段：板 `192.168.2.99`，PC `192.168.2.100`。
> SSH 免密不通是已知事实，所有板端操作都走 `tools/board_jupyter.py`（端口 9090）。

## 6. 推流时 HDMI 上看到什么

**处理后的画面，带框和球。** `board_pipeline --hdmi-preview` 把每帧的检测结果画成带框图，
写进 HDMI 帧缓存（与 D026 同一条 `fb_scanout` 通路）。时间线：

| 时刻 | 屏上 |
|---|---|
| 空闲（没人推流） | **纯白**（`zynq-idle` 每 5 s 刷一次） |
| 推流处理中 | **带框图**：绿框套球员 + `#id`，红圈是球（`HDMI_PREVIEW_ON`） |
| 推完 | **回到纯白**（`HDMI_PREVIEW_OFF white_restored`），然后继续等下一段 |

实测（2026-11-01）：`HDMI_PREVIEW_ON` → `HDMI_PREVIEW_OFF white_restored` →
`PIPELINE_OK frames=30 detections=2 med_infer_ms=997.3`，落盘 30 行。

⚠️ **这块屏的可信度要人眼看**：`fb_scanout` 有已知的**间歇整行循环旋转**缺陷
（恒定 `+313` 列），2026-10-31 的 `HDMI_VISUAL_OK` 就是因此撤回的。
所以：

- 演示里要用它，**先自己看一遍**画面有没有整体错位；
- 它**不能替代** PC 侧回放做验收；
- 觉得画面不可信就关掉回显：`py tools/install_boot.py --no-hdmi-preview`（或把服务里的
  `--hdmi-preview` 去掉），屏上就只剩白屏待机。

不想用 HDMI 的话，另外两条路子仍然可用：

- **PC 侧（最省事）**：`py tools/pc_dashboard.py --push <视频> --push-start <秒> --push-max-frames <N>`，
  推完自动拉回结果并回放带框画面（见 §2.5）。这是**演示主路径**：画面在 PC 上，不受
  HDMI 那个整行旋转缺陷影响。
- **PC 侧（拆开做）**：推流结束后用 `tools/pc_dashboard.py --replay <JSONL>` 看那一段的分析画面；
- **网口回传**：板端另跑 `tools/video_back.py`（走 5006），PC 上 `pc_dashboard --listen` 接。
  这条路已上板验过（`VIDEO_BACK_OK`），但它需要**板本地**的视频源，所以是「板上跑文件 + 回传」，
  不是「PC 推流 + 回传」的闭环。**闭环（推流进板、带框图直接从网口回来）没实现**，
  不要在现场说它是实时的。

## 7. 常见故障

| 现象 | 原因 | 处理 |
|---|---|---|
| `VIDEO_SEND_FAIL connect` | 板子没起、服务没装、端口被占 | 先 `journalctl -u zynq-stream`；再确认 5007 没被别的进程占用 |
| 推流卡住不动 | 板端在跑 ncnn（约 1 s/帧），TCP 反压 | 正常；看 `VIDEO_SEND_PROGRESS` 是否在涨 |
| 板端多出假球 | 用了 `--quality 80` | 用默认 95 |
| 白屏变黑/花 | `zynq-idle` 挂了（缓冲被释放） | `systemctl restart zynq-idle` |
