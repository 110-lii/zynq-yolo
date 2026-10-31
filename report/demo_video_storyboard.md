# 演示视频分镜脚本（2026-10-31 夜）

> 用途：赛事门户「演示视频」上传件（MP4 / H.264 / ≤3 min / ≤300 MB）。
> 演示通道：**板子跑本地片段 + 网口回传带框视频 / PC 面板 / LCD 面板**。
> **HDMI 环出不作演示通道**：它有间歇性整行旋转缺陷，`data/results/hdmi_visual_ok.json`
> 当前为 `HDMI_VISUAL_RETRACTED`，全片不得出现 HDMI 屏画面。
>
> 所有命令在仓库根 `D:\zynq-yolo` 执行。板端没有免密 SSH，统一经 Jupyter：
> `py tools/board_jupyter.py "<Python 代码>"`（板 IP `192.168.2.99`，Jupyter 9090）。
> 下表凡是「板端」命令，实际都通过这一行在 PC 上执行；板内终端命令同时给出，便于核对。
>
> 本脚本只使用仓库里真实存在的入口和真实记录过的结果；没有上板人眼证据的画面，
> 一律标「待上板确认」，不当成已验证。

---

## 1. 总时长分配（合计 155 s，上限 180 s）

| 镜头 | 内容 | 起止 | 时长 |
|---|---|---|---|
| S1 | 片头标题 | 00:00–00:05 | 5 s |
| S2 | 实物全貌 + LCD 赛前界面 | 00:05–00:17 | 12 s |
| S3 | 系统架构与数据流 | 00:17–00:29 | 12 s |
| S4 | 板上跑本地片段（检测 + 球 + 落盘） | 00:29–00:51 | 22 s |
| S5 | 网口回传带框视频（主演示通道） | 00:51–01:21 | 30 s |
| S6 | PC 面板：比赛画面 + 球 + 框 | 01:21–01:41 | 20 s |
| S7 | LCD 状态面板（同一份 JSONL） | 01:41–01:55 | 14 s |
| S8 | 赛前 UI 真按键 | 01:55–02:11 | 16 s |
| S9 | 逐帧 CSV（球坐标与人数） | 02:11–02:23 | 12 s |
| S10 | 指标、边界与收尾 | 02:23–02:35 | 12 s |
| — | 转场/淡入淡出预留 | — | 25 s |
| **合计** | | | **155 s + 25 s = 180 s** |

> 成片控制在 155 s 左右；预留的 25 s 只用于转场和片尾停留，不得靠加长镜头突破 180 s。

---

## 2. 拍摄顺序（与成片顺序不同，照着这个顺序拍）

板子一天能掉三次线，而且「跑过若干次 PL overlay 之后 ncnn 会 SIGTRAP」有前科
（`docs/board_environment.md` §4）。所以**一次开机把板端镜头拍完，且把不依赖 overlay 的
镜头放在前面**：

| 顺序 | 镜头 | 为什么排这里 |
|---|---|---|
| 1 | 准备段 P0–P2 | 上电、查文件、只推缺失件 |
| 2 | S4 板上管线 | 刚开机，ncnn 最干净；`--no-pl` 不需要 overlay |
| 3 | S5 网口回传 | 紧接着用 S4 刚写出的 JSONL，不重跑检测 |
| 4 | 加载 overlay（只加载一次）→ S7 LCD 面板 | 减少 `Overlay()` 次数 |
| 5 | S8 赛前 UI 真按键 | 同一片 overlay |
| 6 | S2 实物 + LCD 界面 | 用 S8 结束后的界面，不再额外加载 |
| 7 | S6 / S9 PC 面板与 CSV | 完全不需要板子，板子掉线也能拍 |
| 8 | S1 / S3 / S10 字幕与静态图 | 后期完成 |

---

## 3. 准备段（不录，开拍前做完）

### P0 上电与通道自检

```bash
py tools/board_jupyter.py "print('BOARD_OK')"
```

预期：打印 `BOARD_OK`。若超时或连接被拒绝：手动断电重启，等 60–90 s 再试；
**不要**在板子离线时继续拍任何板端镜头（AGENTS.md §2）。

### P1 板上文件自检

```bash
py tools/board_jupyter.py "import os;print('MISSING',[p for p in ['/home/xilinx/zynq-yolo/tools/board_pipeline.py','/home/xilinx/zynq-yolo/tools/video_back.py','/home/xilinx/zynq-yolo/tools/status_display.py','/home/xilinx/zynq-yolo/tools/pc_dashboard.py','/home/xilinx/zynq-yolo/tools/lcd_ui.py','/home/xilinx/zynq-yolo/tools/flow_overlay.py','/home/xilinx/zynq-yolo/tools/bram_fb.py','/home/xilinx/zynq-yolo/board/overlay/flow_system.bit','/home/xilinx/zynq-yolo/board/ui_config.json','/home/xilinx/zynq-yolo/data/samples/scenes/c03_t01290.0.mp4','/home/xilinx/ncnn_infer'] if not os.path.exists(p)])"
```

预期：`MISSING []`。板端布局来自 2026-10-31 17:45 的 deploy（`data/results/board_acceptance.json`：
42 个文件 + 16 段片段）。若 `MISSING` 非空，逐个补推：

```bash
py tools/board_jupyter.py --push tools/video_back.py /home/xilinx/zynq-yolo/tools/video_back.py
py tools/board_jupyter.py --push tools/status_display.py /home/xilinx/zynq-yolo/tools/status_display.py
py tools/board_jupyter.py --push tools/lcd_ui.py /home/xilinx/zynq-yolo/tools/lcd_ui.py
py tools/board_jupyter.py --push tools/bram_fb.py /home/xilinx/zynq-yolo/tools/bram_fb.py
py tools/board_jupyter.py --push board/overlay/flow_system.bit /home/xilinx/zynq-yolo/board/overlay/flow_system.bit
py tools/board_jupyter.py --push board/overlay/flow_system.hwh /home/xilinx/zynq-yolo/board/overlay/flow_system.hwh
```

每条应打印 `BOARD_PUSH <路径> bytes=… sha256=…`，与 `HOST_PUSH … sha256=…` 一致。

### P2 主机侧自检（不接板，先确认回放链路没坏）

```bash
py tools/pc_dashboard.py --selftest
py tools/status_display.py --selftest
py tools/video_back.py --help
```

预期：`PC_DASHBOARD_SELFTEST OK`、`STATUS_DISPLAY_SELFTEST OK`，以及 `video_back.py` 的参数表。
前两条在 `report/submission_checklist.md` §3.1 记为已通过；第三条只用来确认参数名没变。

---

## 4. 分镜

### S1 片头标题（00:00–00:05，5 s）

| 字段 | 内容 |
|---|---|
| 画面内容 | 黑底白字：主标题「ZYNQ-7020 足球赛事视频分析」；副标题「PL 预处理 + PS 检测跟踪 + 球与数据落盘 + 三处显示」。淡入 0.5 s、淡出 0.5 s。 |
| 要执行的确切命令 | 无（后期字幕；不涉及任何板端操作） |
| 预期屏幕结果 | 只有字幕，没有任何实拍画面、校名、教师名、赛事标识。 |
| 支撑证据 | 无（纯字幕，不承载实测结论） |

### S2 实物全貌 + LCD 赛前界面（00:05–00:17，12 s）

| 字段 | 内容 |
|---|---|
| 画面内容 | 板子 45° 俯拍，LCD 与网口同框；LCD 上显示赛前设置界面（7 项）。可拍一下网口指示灯。**HDMI 显示器断电或移出画面**。 |
| 要执行的确切命令 | C2（见下） |
| 预期屏幕结果 | LCD 800×480 显示赛前设置界面：A/B 队球衣色、A/B 守门员色、裁判色、上半场方向、开始比赛，共 7 项；Console 出现 `LCD_UI_DRAW once mode=…`。**本次实拍画面待上板人眼确认**（版式参考 `report/figures/lcd_ui_4bpp.png`，该 PNG 是主机侧渲染，不是屏摄）。 |
| 支撑证据 | `ai/sessions/022_hdmi_osd_full_flow.md` §7.8（真按键链路 `LCD_UI_COLOR` / `LCD_UI_SELECT` / `LCD_UI_SAVED`，用户看屏确认）；`tools/lcd_ui.py` 文件头；`report/figures/lcd_ui_4bpp.png`（仅版式参考） |

```bash
# C2 板端等价：python3 tools/lcd_ui.py --once（只画一次，退出后屏上保持）
py tools/board_jupyter.py "import subprocess;p=subprocess.run(['python3','tools/lcd_ui.py','--once'],cwd='/home/xilinx/zynq-yolo',stdout=subprocess.PIPE,stderr=subprocess.STDOUT,universal_newlines=True);print(p.stdout[-1000:])"
```

### S3 系统架构与数据流（00:17–00:29，12 s）

| 字段 | 内容 |
|---|---|
| 画面内容 | 全屏显示 `report/figures/system_arch.png`；后期用半透明高亮框依次圈出：PL preproc、PS ncnn 检测、球链路、三处输出（SD / PC / LCD）。 |
| 要执行的确切命令 | C3（见下） |
| 预期屏幕结果 | 仓库内系统框图。图上不得叠任何「HDMI 画面已通过」之类结论；HDMI 只作为框图里的一个设计模块出现，旁白明确它今天不参与演示。 |
| 支撑证据 | `report/figures/system_arch.png`；`docs/system_status.md`「架构」；`ai/sessions/022_hdmi_osd_full_flow.md` §7.31（`HDMI_VISUAL_OK` 不成立） |

```bash
# C3 PC：用系统默认看图程序打开框图
py -c "import os;os.startfile(r'report\figures\system_arch.png')"
```

### S4 板上跑本地片段（00:29–00:51，22 s）

| 字段 | 内容 |
|---|---|
| 画面内容 | PC 屏幕录制：终端窗口先出现完整命令行（停 3 s），切板子指示灯/网口 B-roll 过渡，再切回终端显示末尾 `PIPELINE_OK`。左上角叠两行小字：「板端 192.168.2.99 · Jupyter 9090 · 本地片段 c03（250 帧 / 10 s / 25 fps）」和「PS 检测路径（--no-pl）；PL 预处理指标见报告」。板端输出在进程结束后一次性回传，终端中途是静的，靠 B-roll 过渡，不要假装实时滚动。 |
| 要执行的确切命令 | C4-a（板端等价命令）+ C4-b（PC 上实际复制执行的一行） |
| 预期屏幕结果 | 末行 `PIPELINE_OK frames=250 detections=… med_infer_ms=…`；上方出现 `STORE_DIR build/flow`。若板上队别配置生效，还会出现 `TEAM_ASSIGN tracks=… labeled=…`（023 那次是 42/15）。落盘 JSONL 必须是 250 行；023 那次其中 20 行带球（帧 100–114、140–144）。**detections、耗时、带球帧数均以现场为准；只有 `frames=250` 是已记录结论。** |
| 支撑证据 | `ai/sessions/023_pc_video_view_ball_air.md` 轮次 3（`PIPELINE_OK frames=250`；ball 20 帧；人手 42 条轨迹 / 15 条贴队别）；`data/results/ball_air_c03_20261031.jsonl`（250 行，20 行 ball，本机复核） |

```bash
# C4-a 板端等价命令（在板子终端里会这样跑；实际用 C4-b 经 Jupyter 执行）
cd /home/xilinx/zynq-yolo
python3 tools/board_pipeline.py \
  --video data/samples/scenes/c03_t01290.0.mp4 \
  --every 15 --max-det 0 --no-pl --no-camera-gate --ball-every 5 \
  --bin /home/xilinx/ncnn_infer \
  --param /home/xilinx/zynq-yolo/build/models/person_v8n25.param \
  --model /home/xilinx/zynq-yolo/build/models/person_v8n25.bin \
  --store-dir build/flow --store-prefix c03e \
  --out /home/xilinx/c03e_tracks.json
```

```bash
# C4-b PC 上实际执行这一行（经 Jupyter 通道，末尾打印板端输出）
py tools/board_jupyter.py "import subprocess;p=subprocess.run(['python3','tools/board_pipeline.py','--video','data/samples/scenes/c03_t01290.0.mp4','--every','15','--max-det','0','--no-pl','--no-camera-gate','--ball-every','5','--bin','/home/xilinx/ncnn_infer','--param','/home/xilinx/zynq-yolo/build/models/person_v8n25.param','--model','/home/xilinx/zynq-yolo/build/models/person_v8n25.bin','--store-dir','build/flow','--store-prefix','c03e','--out','/home/xilinx/c03e_tracks.json'],cwd='/home/xilinx/zynq-yolo',stdout=subprocess.PIPE,stderr=subprocess.STDOUT,universal_newlines=True);print(p.stdout[-3000:])"
```

> 现场耗时几十秒到一分多钟（250 帧、每 15 帧一次检测；023 未记录精确耗时），剪辑时用 B-roll 过渡到 22 s。若超时，先看是否卡在
> ncnn（`DETECT_FAIL` / 返回 -5），按「已知风险」处理，不要改参数重跑到证据对不上。
>
> **为什么命令行里有 `--no-pl`**：板上 PL 预处理路径会把框坐标喂错（`ai/sessions/022` §7.30 记录：
> `_is_grass` 36 中只剩 2），所以出演示画面时用 PS 检测路径。PL 预处理本身（18.8 ms/帧、LUT 7%）
> 仍是设计的一部分，但**这一段不演示它**，字幕和旁白都不能让人以为这段画面经过 PL 预处理。

**可选加强（时间够才做，不挤占已定 155 s）**：S4 也可以拍成「视频从 PC 经以太网送进板子」
（D015 输入侧，2026-10-31 已上板验过等价：文件源与网络源均 `frames=250 detections=17`，
带球帧同为 100–114、140–144）。先起板端监听、再在 PC 上送：

```bash
py tools/board_jupyter.py "import subprocess;p=subprocess.run(['python3','tools/board_pipeline.py','--listen','5007','--every','15','--max-det','0','--no-pl','--no-camera-gate','--ball-every','5','--bin','/home/xilinx/ncnn_infer','--param','/home/xilinx/zynq-yolo/build/models/person_v8n25.param','--model','/home/xilinx/zynq-yolo/build/models/person_v8n25.bin','--store-dir','build/flow','--store-prefix','netdemo','--out','/home/xilinx/netdemo.json'],cwd='/home/xilinx/zynq-yolo',stdout=subprocess.PIPE,stderr=subprocess.STDOUT,universal_newlines=True);print(p.stdout[-3000:])"
```

```bash
py tools/video_send.py --video data/samples/scenes/c03_t01290.0.mp4 --host 192.168.2.99 --port 5007 --fps 25
```

> 预期：发送端 `VIDEO_SEND_OK 250 …`，板端 `PIPELINE_OK frames=250`。**别用 `--quality 80`**：
> q80 会让板端多出 10 帧假球（球员白鞋），默认 95 是量出来的。这一段是可选镜头，
> 若不拍，旁白不要提「以太网送视频进板」。

### S5 网口回传带框视频（00:51–01:21，30 s）

| 字段 | 内容 |
|---|---|
| 画面内容 | PC 上 OpenCV 窗口 `board annotated`（1280×720）实时接收；绿框套在球员上，左上角黑条显示 `frame N players M CAM:…`。右下角画中画拍板子 RJ45 网口灯。 |
| 要执行的确切命令 | C5-a（固定 JSONL 文件名）→ C5-b（板端起服务）→ C5-c（PC 收）→ C5-d（回读板端日志） |
| 预期屏幕结果 | 先出现 `VIDEO_BACK_LISTEN port=5006 tracks=250`，PC 连上后 `VIDEO_BACK_CONNECTED`；PC 窗口逐帧出现绿框与 `#id`。板端日志收尾为 `VIDEO_BACK_OK 250 19.61 5.1`（022 实测；本次数值以现场为准，量级应一致）。顶栏 `players` 应与 JSONL 同步：本片用的 c03e JSONL 第 137 帧是 **14** 条 tracks（本机复核；022 另一次回传同一帧记的是 16，说明该数随运行变化，不能写死）。**球的红圈已上板验证**（2026-10-31 22:3x，见下方「已验证」框）：`--tracks` 指向带球 JSONL 后，帧 105 的屏上出现红圈 + `BALL`，位置 `(739, 212)` 与 JSONL 的 `ball` 字段一致。 |
| 支撑证据 | `ai/sessions/022_hdmi_osd_full_flow.md` §7.30（`VIDEO_BACK_OK 250 19.61 5.1`；022 那次 c03 帧 137 = players 16；`build/annotated_c03.mp4` 250 帧；该次回传早于 023 的空中球修复，只证明「框 + 回传链路」）；`tools/video_back.py` 文件头协议；`build/flow_c03e.jsonl`（= `data/results/ball_air_c03_20261031.jsonl`，250 行 / 20 行 ball；f137 = 14 条 tracks） |

```bash
# C5-a 把 S4 刚写出的 JSONL 固定成 c03e_demo.jsonl，后面两条命令都用这个文件名
py tools/board_jupyter.py "import glob,shutil;fs=[x for x in glob.glob('/home/xilinx/zynq-yolo/build/flow/c03e_*.jsonl') if not x.endswith('c03e_demo.jsonl')];f=sorted(fs)[-1];shutil.copy(f,'/home/xilinx/zynq-yolo/build/flow/c03e_demo.jsonl');print('COPIED_TO c03e_demo.jsonl FROM',f)"
```

> ⚠️ **踩过的坑（2026-10-31 22:2x 实录）**：`board_pipeline --out /home/xilinx/c03e_tracks.json` 写的是
> **汇总 JSON**（键为 `frames` / `detections` / `ball_pct` …），**不是逐帧 JSONL**。
> 把它传给 `video_back --tracks`，回传画面里**一个框都没有**（程序不报错，只是每帧都是原始视频），
> 很容易误判成「检测没出来」。逐帧结果只在 `--store-dir` 目录下的 `*_00.jsonl` 里 —— 即 C4-a 的
> `build/flow/c03e_*.jsonl`。开拍前先数一下行数：
> `py tools/board_jupyter.py "print(sum(1 for _ in open('/home/xilinx/zynq-yolo/build/flow/c03e_demo.jsonl')))"`
> 应为 `250`；是 `1` 就说明拿错文件了。

```bash
# C5-b 板端等价：python3 tools/video_back.py --serve --video data/samples/scenes/c03_t01290.0.mp4 --tracks build/flow/c03e_demo.jsonl --fps 10 --port 5006
# 后台起服务（不等它结束，立刻回 PC 端收）
py tools/board_jupyter.py "import subprocess;f=open('/home/xilinx/video_back.log','w');subprocess.Popen(['python3','tools/video_back.py','--serve','--video','data/samples/scenes/c03_t01290.0.mp4','--tracks','build/flow/c03e_demo.jsonl','--fps','10','--port','5006'],cwd='/home/xilinx/zynq-yolo',stdout=f,stderr=subprocess.STDOUT,start_new_session=True);print('VIDEO_BACK_STARTED')"
```

```bash
# C5-c PC 端：接收、显示、同时存 MP4 和一张 PNG（约 50 s 发完 250 帧）
py tools/video_back.py --recv --host 192.168.2.99 --port 5006 --out build/demo_annotated_c03.mp4 --png-out build/demo_annotated_c03.png --show
```

```bash
# C5-d 收完后回读板端日志，确认 VIDEO_BACK_OK
py tools/board_jupyter.py "print(open('/home/xilinx/video_back.log').read()[-2000:])"
```

> 回传是约 5 帧/秒的慢放（板端解码+画框+JPEG 的实际上限），旁白要说明「慢放」，
> 不要让观众以为实时 25 fps。PC 防火墙需放行 5006/TCP，且两端在同一网段。

> ### ✅ 已验证（2026-10-31 夜，上板真跑，不是推演）
>
> | 项 | 实测 |
> |---|---|
> | 板端 c03 管线 | `PIPELINE_OK frames=250 detections=17 med_infer_ms=980.9`；`STAGES detect_ms=24532 ball_ms=30782`；`TEAM_ASSIGN tracks=42 labeled=15` |
> | 落盘 JSONL | 250 行，其中 **20 行带球**：帧 **100–114**、**140–144** |
> | 网口回传（match_clip，200 帧） | `VIDEO_BACK_OK 200 21.56 4.9`；屏上 17 个绿框 + `#id` |
> | 网口回传（c03 带球，190 帧） | `VIDEO_BACK_OK 190 19.65 5.0`；**帧 105 屏上有红圈 + `BALL`，位置 (739,212)** |
> | 证据图 | [`figures/video_back_c03_ball_20261031.png`](figures/video_back_c03_ball_20261031.png)（板端 → 网口 → PC，含框与球）；[`figures/video_back_frame106.png`](figures/video_back_frame106.png) |
> | 本机回传样本 | `build/demo_raw/board_c03_ball.mp4`、`build/demo_raw/board_annotated.mp4`（不进提交包，可作 B-roll） |
>
> 本轮同时修掉 `tools/video_back.py` 两个会让「框落在错误的帧上」的缺陷：
> ① 用 `seq[n]`（第 n 行）回退去取框，而 `--every 15` 的 JSONL 里 `frame` 是**源帧号**（0,15,30…），
> 两者相差 15 倍；② 由框内容反推播放位置（`idx = st["frame"]+1`），一错就自我延续。
> 现在只按视频自己的帧计数查表。另加 `--hold`（默认开）：空检测帧沿用上一帧有内容的框
> （不插值、不预测）；要拍「逐帧真检测」证据时用 `--no-hold`。

### S6 PC 面板：比赛画面 + 球 + 框（01:21–01:41，20 s）

| 字段 | 内容 |
|---|---|
| 画面内容 | 前半段：PC Dashboard 窗口回放（左栏比赛画面 + 绿框；右栏事件流为空，本段没有 `events`，不要配「事件识别」旁白）。后半段：切到第 106 帧静帧 `build/demo_frame106.png`，镜头/后期推近红圈 `BALL`。 |
| 要执行的确切命令 | C6-a（先生成 CSV，给 S9 用）→ C6-b（GUI 循环）→ C6-c（第 106 帧静帧） |
| 预期屏幕结果 | 左栏标题「比赛画面 · 带框（转播机位，无需标定）」；第 106 帧 15 个绿框；红圈 + `BALL` 在 (739, 212)；顶栏小标签「连接 回放中 / 帧号 106 / 人数 15 / 球 可见」；底栏「球：画面 (739, 212) px」；机位显示 `INVALID_VIEW`（转播机位未标定，按设计如此）。静帧命令打印 `PC_DASHBOARD_OK frames=107` 与 `PC_DASHBOARD_PNG …`。 |
| 支撑证据 | `ai/sessions/023_pc_video_view_ball_air.md` 轮次 1（`--dump-frame 106` 出图，真球红圈 + `BALL`，顶栏「球 可见」，底栏「球：画面 (739, 212) px」）；`report/figures/pc_dashboard_video_ball.png`；`build/flow_c03e.jsonl` 第 106 帧（15 tracks、ball 739/212/conf 0.60）；`tools/pc_dashboard.py` 的 `_draw_video_panel` / `CsvWriter.FIELDS` |

```bash
# C6-a 主机侧快速回放一遍，生成 250 行 CSV（给 S9）
py tools/pc_dashboard.py --replay data/results/ball_air_c03_20261031.jsonl --video data/samples/scenes/c03_t01290.0.mp4 --csv build/demo_session.csv --speed 0 --headless
```

```bash
# C6-b GUI 循环回放（录这一段的前半段）
py tools/pc_dashboard.py --replay data/results/ball_air_c03_20261031.jsonl --video data/samples/scenes/c03_t01290.0.mp4 --loop
```

```bash
# C6-c 第 106 帧静帧（录后半段，正好是球在框里的那一帧）
py tools/pc_dashboard.py --replay data/results/ball_air_c03_20261031.jsonl --video data/samples/scenes/c03_t01290.0.mp4 --dump-png build/demo_frame106.png --dump-frame 106
```

### S7 LCD 状态面板（01:41–01:55，14 s）

| 字段 | 内容 |
|---|---|
| 画面内容 | 镜头对准 LCD（正面或 30–45°），屏上 8 行状态面板；后期在右侧叠小字「同一份 JSONL：c03e_demo」。 |
| 要执行的确切命令 | C7-a（加载 overlay，只加载一次）→ C7-b（写第 106 帧面板） |
| 预期屏幕结果 | LCD 8 行：① 帧 106；② 场上人数 15；③ 球状态「可见 x=739.0 y=212.0 px (space=image) conf=0.60」；⑤ 机位状态 `INVALID_VIEW`；其余行按占位符（以上是 023 那次 c03e 的 f106；本次 S4 重跑后**以现场 `c03e_demo.jsonl` 为准**）。Console 出现 `STATUS_DISPLAY_BRAM 0x40000000 layout=800x480 bpp=4 words=48000 via PYNQ` 与 `LCD_CFG_OK 0x41220000 cfg=0x10 …`。**屏上这一帧的具体内容待上板人眼确认**；`report/figures/lcd_panel_ball_20261031.png` 是主机侧同帧渲染，不等于屏摄。 |
| 支撑证据 | `ai/sessions/023_pc_video_view_ball_air.md` 轮次 3（同一命令 `words=48000 via PYNQ`，9.9 s）；`ai/sessions/022_hdmi_osd_full_flow.md` §7.8（彩色状态面板上屏，用户看屏确认）；`docs/flow_overlay.md` §3（`0x40000000` = LCD 4bpp，`0x41220000` = LCD cfg）；`build/flow_c03e.jsonl` 第 106 帧 |

```bash
# C7-a 板端等价：python3 tools/flow_overlay.py --selftest --skip-hdmi
# 加载 flow_system.bit，只跑 LCD 自检（跳过 HDMI，避免把 HDMI 画面带进现场）
py tools/board_jupyter.py "import subprocess;p=subprocess.run(['python3','tools/flow_overlay.py','--selftest','--skip-hdmi'],cwd='/home/xilinx/zynq-yolo',stdout=subprocess.PIPE,stderr=subprocess.STDOUT,universal_newlines=True);print(p.stdout[-3000:])"
```

```bash
# C7-b 板端等价：python3 tools/status_display.py --replay build/flow/c03e_demo.jsonl --frame 106 --size 800x480 --fb-bram 0x40000000 --lcd-cfg 0x41220000 --once
# 第 106 帧写到 LCD（4bpp + 调色板 + 切帧缓存）
py tools/board_jupyter.py "import subprocess;p=subprocess.run(['python3','tools/status_display.py','--replay','build/flow/c03e_demo.jsonl','--frame','106','--size','800x480','--fb-bram','0x40000000','--lcd-cfg','0x41220000','--once'],cwd='/home/xilinx/zynq-yolo',stdout=subprocess.PIPE,stderr=subprocess.STDOUT,universal_newlines=True);print(p.stdout[-2000:])"
```

### S8 赛前 UI 真按键（01:55–02:11，16 s）

| 字段 | 内容 |
|---|---|
| 画面内容 | 特写：板子上标 KEY0/KEY1 的两颗用户按键（PL `L14` / `K16`）与 LCD 同框。手指按 KEY0 逐项下移，按 KEY1 换颜色；最后一项「开始比赛」按 KEY1 保存。 |
| 要执行的确切命令 | C8（启动交互，120 s 内按完） |
| 预期屏幕结果 | 每按 KEY0 高亮下移到下一项；按 KEY1 在当前项的 10 种球衣色里轮换；最后一项 KEY1 保存，Console 收尾出现 `LCD_UI_SELECT …` / `LCD_UI_COLOR …` / `LCD_UI_SAVED …`（022 实测：`ref=cyan side=left` 等）。按键顺序与颜色以现场为准。 |
| 支撑证据 | `ai/sessions/022_hdmi_osd_full_flow.md` §7.8（真按键日志：`LCD_UI_COLOR item=裁判衣服 value=purple → cyan`、`LCD_UI_SELECT n=5 item=A 队上半场`、`LCD_UI_SELECT n=6 item=开始比赛`、`LCD_UI_SAVED …`）；`tools/lcd_ui.py` 文件头（key0=下一项，key1=换选项/确认） |

```bash
# C8 板端等价：python3 tools/lcd_ui.py --timeout 120
# 启动交互（阻塞 120 s；启动后立刻按板上的 KEY0/KEY1，结束自动打印按键日志）
py tools/board_jupyter.py "import subprocess;p=subprocess.run(['python3','tools/lcd_ui.py','--timeout','120'],cwd='/home/xilinx/zynq-yolo',stdout=subprocess.PIPE,stderr=subprocess.STDOUT,universal_newlines=True);print(p.stdout[-3000:])"
```

### S9 逐帧 CSV（球坐标与人数）（02:11–02:23，12 s）

| 字段 | 内容 |
|---|---|
| 画面内容 | Excel 打开 `build/demo_session.csv`，镜头扫过 12 列表头，停在「球有效=True」的 20 行（帧 100–114、140–144）；画中画放 PC 面板。 |
| 要执行的确切命令 | C9（CSV 已由 C6-a 生成） |
| 预期屏幕结果 | 表头固定 12 列：帧号 / 时间 / 人数 / 球x / 球y / 球有效 / 出界 / 出界侧 / 进球 / 球权ID / 球权距离 / 机位状态；共 250 行数据；`球有效` 为 True 的只有 20 行，集中在 100–114 与 140–144。**出界/进球/球权三列本段为空**：这段没有标定、`board_pipeline` 未开 `--proposal`，JSONL 里没有 `events`。旁白不得说「事件判定结果」。 |
| 支撑证据 | `tools/pc_dashboard.py` `CsvWriter.FIELDS`（12 列）；`report/submission_checklist.md` §3.2（12 列 CSV）；`build/flow_c03e.jsonl`（250 行 / 20 行 ball，本机复核） |

```bash
# C9 用系统默认表格程序打开 CSV
py -c "import os;os.startfile(r'build\demo_session.csv')"
```

### S10 指标、边界与收尾（02:23–02:35，12 s）

| 字段 | 内容 |
|---|---|
| 画面内容 | 结论卡。左列「已实测」：PL 预处理 18.8 ms/帧（LUT 7%）、PS person 0.95–1.0 s/帧、球分类器 P 0.67 / R 0.85（th=0.7，板上 8 ms/候选）、球链路确认 80%（16/20，15 px 内）。右列「边界」：球仍断续、单机位标定、倒地只做候选粗筛（强层 4 个候选 = 3 真 1 假，人工复核，**不作 precision/recall**）、**HDMI 环出不作演示通道**。底部一行「数据与代码可复现；测试片段仅内部使用」。 |
| 要执行的确切命令 | 无（后期字幕卡） |
| 预期屏幕结果 | 只有静态文字。所有数字必须与 `docs/system_status.md` 一致，并在角标注明「历史实测（不同片段/条件）」，不能让人以为都是本片这一段跑出来的；不得相加或换算成「准确率」。 |
| 支撑证据 | `docs/system_status.md`「实测指标」「已知边界」；`ai/sessions/010_board_pipeline_ball_backfill.md`（202 帧板端运行与球链路指标）；`data/results/fall_review.json`（人工复核 3 真 1 假；文件自注「不作 precision/recall」）；`report/submission_checklist.md` §7（开放项）；`data/results/hdmi_visual_ok.json`（`HDMI_VISUAL_RETRACTED`） |

> 证据缺口（必须照实说）：202 帧板端运行的原始 `/tmp/out.json` 未随包归档，指标汇总在
> `docs/system_status.md`。字幕卡只写「报告里有汇总」，不要把它说成「原始数据已随包提供」。

---

## 5. 拍摄清单（需要人做的动作）

### 5.1 开机与板端（开拍前 60 分钟）

- [ ] 板上电（12 V 适配器），网线接 PS 网口，另一端接 PC 同一交换机/直连；PC 网口地址在 `192.168.2.0/24`。
- [ ] `py tools/board_jupyter.py "print('BOARD_OK')"` 通过。
- [ ] P1 文件自检 `MISSING []`；缺什么补推什么。
- [ ] 按第 2 节顺序：先拍 S4，再拍 S5，然后**只加载一次** overlay 拍 S7、S8、S2。
- [ ] 拍 S4 时把终端字体调到 20–22 pt，窗口占屏幕 80% 以上；开头停 3 s 再回车。
- [ ] 拍 S5 时先起板端服务（C5-b），等 1–2 s，再在 PC 上执行 C5-c；窗口标题应为 `board annotated`。
- [ ] 拍 S7 前确认 `FLOW_OVERLAY_LCD OK`、`FLOW_OVERLAY_OK`；若 `LCD` 报 FAIL，先查 overlay 是否加载成功，不要硬拍。
- [ ] 拍 S8 前把手指位置对准 KEY0/KEY1；按下后停 0.5 s 再松开，方便剪。
- [ ] 拍 S2 时确认 LCD 上仍是赛前 UI（S8 结束后屏上保持），不要在拍之前再跑别的写屏命令。

### 5.2 机位与屏

- [ ] 主机位：三脚架，45° 俯拍板子 + LCD + 网口；背景清理，不出现实验室门牌、白板字、姓名贴。
- [ ] **HDMI 显示器断电或整个移出画面**；不要拍 HDMI 线接着的显示器，也不要让它在 LCD 里反光出现。
- [ ] LCD 特写：正面或 30–45°，占画面 70%；手动对焦、锁定曝光；若出现横纹，快门改 1/50 s。
- [ ] 按键特写：KEY0/KEY1 与 LCD 同框，景深覆盖两者。
- [ ] 网口 B-roll：RJ45 指示灯 + 网线，2–3 s，用于 S5 画中画。
- [ ] 补光：柔光从侧面打，避免 LCD 正上方直射反光。

### 5.3 录屏软件设置

- [ ] 软件：OBS Studio（或同等）；画布与输出统一 **1920×1080**，**30 fps**。
- [ ] 编码：**H.264**（x264 或 NVENC），**CBR 8000 kbps**，关键帧间隔 2 s，MP4。
- [ ] 音频：48 kHz，可静音录制（旁白后期配）；不要录系统提示音。
- [ ] 不加摄像头叠加、不加软件水印、不加计时器。
- [ ] Windows 显示缩放 100%；浏览器/Excel 缩放 125%；终端深色主题。
- [ ] 每个镜头单独一个文件：`S01.mp4` … `S10.mp4`；每条开头留 3 s 静止。
- [ ] **不要**用 4K 录完再压到 720p；PC Dashboard 窗口是 1240×780，720p 会裁掉底部，所以 PC 面板镜头必须 1080p，全片统一 1080p。
- [ ] 若一定要交 720p：只有纯板端/网口回传镜头可以 720p，PC 面板与静帧仍按 1080p 录后缩，不能直接裁。

### 5.4 剪辑与导出

- [ ] 时间线 1920×1080 / 30p；按第 1 节时长表，总长 155 s 左右。
- [ ] 导出 H.264 High Profile、8 Mbps、AAC 128 kbps、MP4、faststart；目标 ≤300 MB（155 s × 8 Mbps ≈ 155 MB）。
- [ ] 静态图（S3/S10）每张停留 ≥5 s，字号 ≥40 px（1080p）。
- [ ] 转场只用硬切/淡入淡出；不加花哨特效、不加背景音乐盖过旁白（音乐 -20 dB）。
- [ ] 导出后跑第 8 节的自检命令。

---

## 6. 不能出现的东西

1. **学校名称、校徽、学院名、指导教师姓名、学号、实验室门牌、白板上的姓名/课程名**。
   拍摄前清场；后期逐帧检查。
2. **其他比赛标识**：转播台标、比分牌、赞助商广告、队徽/队名。c03 片段左上角有比分牌，
   剪辑时用同色块或模糊遮住（固定在画面左上约 320×90 区域），且遮罩不得盖住球员框和球。
   若遮不干净，换一段没有台标的片段重跑 S4（要重跑管线，成本高，尽量一次遮好）。
3. **任何 HDMI 环出画面**：包括显示器亮着的一角、反光、录屏里的 HDMI 预览窗口。
   `HDMI_VISUAL_RETRACTED` 已记录间歇性整行旋转，拍进去就是自曝缺陷。
4. **声称 HDMI 已修好/已通过的结论**：不得出现 `HDMI_VISUAL_OK`、不得说「大屏画面正常」。
5. **未经上板验证的结论**：
   - 不得把 `report/figures/hdmi_osd_boxes.png` 当检测结果（该图是主机侧合成坐标渲染，
     仓库 `report/figures/README.md` 已注明「不能作为检测结果」）；
   - 不得把 `report/figures/lcd_ui_4bpp.png`、`report/figures/lcd_panel_ball_20261031.png` 当屏摄；
   - 不得说「球检测已完成」——实际是 250 帧里 20 帧，且断续；
   - 不得把 CSV 里空白的出界/进球/球权列说成「事件判定通过」——c03 没有标定，这段没有 `events`；
   - 不得说「板→PC 实时推送已上板验证」——`result_stream` 只有本地回环记录；
     本片用的是 `video_back` 回传，不要混说；
   - 不得说「队别已在真实画面验证」——只有主机侧自检与非准确率统计；
   - 不得把候选数当检出数，不得把 `--max-det` 当人数。
6. **调大 `--ball-lost-sec` 后的球帧数**：该开关已否决（新增 38 帧人工抽查全非球），
   不得作为成果展示。
7. **参考样片/其他作品画面**：`D:\xiazai\…mp4` 只是风格参考，不得剪进成片。

---

## 7. 旁白稿（中文，按镜头分段）

> 语速按每分钟约 220 字控制；每段都只说画面里能看到、仓库里能查到的东西。

**S1（5 s）**
这是我们在 ZYNQ-7020 上做的足球赛事视频分析系统。

**S2（12 s）**
先看实物。ZYNQ-7020 板子，小屏是 800×480 的 LCD，显示赛前设置界面。演示视频先放在板上，结果从网口回传。

**S3（12 s）**
PL 做像素预处理，PS 跑检测和跟踪。演示走 PS 检测；结果只序列化一次，给存储、PC、LCD，HDMI 不在演示内。

**S4（22 s）**
现在在板子上跑一段 10 秒的本地片段。每 15 帧做一次检测，球单独按 5 帧的节奏扫描；最后一行 `PIPELINE_OK` 说明 250 帧全部跑完。

**S5（30 s）**
检测结果不经过 HDMI，而是从网口回传。板子把带框画面压成 JPEG 发出来，PC 实时接收；绿框是球员，编号是跟踪 ID。板端推理比较慢，这里是大约 5 帧每秒的慢放。

**S6（20 s）**
这是 PC 面板回放，用的是板上那份 JSONL。第 106 帧，红圈是球，坐标 739、212 像素，画面里有 15 个球员框。机位显示 `INVALID_VIEW`，因为转播机位没标定，坐标是画面像素。

**S7（14 s）**
同一份数据也写到 LCD 上。8 行状态里，第 3 行就是球：可见，坐标 739、212 像素；第 5 行是机位状态。

**S8（16 s）**
赛前用板上的两颗按键设置球衣颜色：KEY0 换下一项，KEY1 换颜色，最后一项确认保存。设置会写进板上的 `board/ui_config.json`。

**S9（12 s）**
每一帧落成 CSV，固定 12 列；事件三列这段为空。球有效的行只有 20 行，集中在第 100 到 114 帧和第 140 到 144 帧。

**S10（12 s）**
指标和边界都留在报告里：球还是断续的，倒地只做到候选粗筛，HDMI 环出今天不作为演示通道。

---

## 8. 导出后自检

```bash
# 时长 / 分辨率 / 帧率 / 帧数 / 文件大小（OpenCV 读，不依赖 ffprobe）
py -c "import os,cv2;c=cv2.VideoCapture(r'build/demo_final.mp4');print('size_MB=%.1f'%(os.path.getsize(r'build/demo_final.mp4')/1e6),'WxH=%dx%d'%(int(c.get(3)),int(c.get(4))),'fps=%.2f'%c.get(5),'frames=%d'%c.get(7))"
```

判据：`WxH=1920x1080`、`fps=30.00`、`frames≈4650`（155 s）、`size_MB<300`。
时长再除以 30 应 ≤180 s；若不满足，先删转场预留，不要删证据镜头。

---

## 9. 证据索引（本脚本引用的仓库文件）

| 文件 | 用途 |
|---|---|
| `ai/sessions/023_pc_video_view_ball_air.md` | S4/S5/S6/S7：`PIPELINE_OK frames=250`、ball 20 帧、`--dump-frame 106`、LCD 9.9 s |
| `ai/sessions/022_hdmi_osd_full_flow.md` | S5/S7/S8：`VIDEO_BACK_OK 250 19.61 5.1`、c03 137 帧 players 16、真按键日志、彩色面板人眼确认、`tag4` 人眼确认 |
| `ai/sessions/021_lcd_probe_from_scratch.md` | LCD 4bpp 重做、读回判据与历史 |
| `ai/sessions/010_board_pipeline_ball_backfill.md` | 202 帧板端运行与球链路指标 |
| `data/results/ball_air_c03_20261031.jsonl` | c03 板上原始输出（250 行，20 行 ball），与 `build/flow_c03e.jsonl` 逐字节一致 |
| `data/results/fall_review.json` | 倒地人工复核（3 真 1 假；文件自注不作 precision/recall） |
| `data/results/hdmi_visual_ok.json` | `HDMI_VISUAL_RETRACTED`，HDMI 不作演示通道的依据 |
| `data/results/board_acceptance.json` | 2026-10-31 17:45 deploy：42 个文件 + 16 段片段 |
| `docs/system_status.md` | S3/S10 的指标与边界 |
| `docs/board_environment.md` | 掉线/半死/ncnn SIGTRAP 的处置 |
| `docs/flow_overlay.md` | LCD 4bpp / LCD cfg 地址表 |
| `report/submission_checklist.md` | 门户格式要求（§0）与开放项（§7） |
| `report/figures/system_arch.png` | S3 框图 |
| `report/figures/pc_dashboard_video_ball.png` | S6 第 106 帧参考出图（PC 侧渲染，非屏摄） |
| `report/figures/lcd_panel_ball_20261031.png` | S7 同帧参考出图（PC 侧渲染，非屏摄） |
| `report/figures/lcd_ui_4bpp.png` | S2 版式参考（主机侧渲染，非实拍） |
| `report/figures/hdmi_osd_boxes.png` | **反面清单**：主机侧合成坐标渲染，不得当检测结果 |
| `build/annotated_c03.mp4` | S5 掉线时的替代素材（022 回传产物，250 帧） |
| `build/annotated_n03.mp4` | 备用回传素材（另一段，250 帧） |

---

## 10. 已知风险与补拍

| 风险 | 影响的镜头 | 现场征兆 | 处理 / 补拍 |
|---|---|---|---|
| 板子掉线（一天约三次） | S2、S4、S5、S7、S8 | `board_jupyter` 超时、连接拒绝、ping 不通 | 手动断电重启，等 60–90 s，`print('BOARD_OK')` 通过后只补拍缺的镜头；**不要**在离线状态声称已验证 |
| Jupyter 半死（TCP 能连、HTTP 不应答） | S4–S8 | 一条 `print` 等 2 分钟以上 | 断电重启；不重试、不换参数猜；先拍 S6/S9 等无板镜头 |
| ncnn 返回 -5（SIGTRAP） | S4 | `DETECT_FAIL`、`ncnn_infer` 无输出 | 重启后只跑一次；仍失败则 S4 改为展示已有 JSONL 的头部并标注「此前板端运行产物」，S5 用 `build/annotated_c03.mp4` 补 |
| 回传连不上/花屏 | S5 | `VIDEO_BACK_FAIL`、PC 收不到 | 查防火墙 5006/TCP、同网段、先 serve 后 recv；重试一次。替代：播放 `build/annotated_c03.mp4`（022 回传产物，250 帧；早于空中球修复，**没有球红圈**，字幕要注明） |
| S4 输出与 023 不完全一致 | S4、S5、S7 | 帧数不是 250、f106 无球 | 帧数必须是 250；若 f106 无球，先列出实际带球帧再改 `--frame`：`py tools/board_jupyter.py "import json;rows=[json.loads(l) for l in open('/home/xilinx/zynq-yolo/build/flow/c03e_demo.jsonl')];print([r['frame'] for r in rows if r.get('ball')])"` |
| LCD 反光/频闪/拍不清 | S2、S7、S8 | 屏上有条纹、过曝 | 调角度、快门 1/50 s、侧面柔光；不要用闪光灯直打 |
| HDMI 缺陷误入画面 | 全片 | 显示器亮着、反光里出现错位画面 | 开拍前拔 HDMI 或关显示器；后期逐帧检查 |
| 合规遮罩没盖干净 | S5、S6、S9 | 比分牌/台标仍可见 | 固定区域遮罩；若遮罩影响球员框，换片段重跑（成本高，尽量一次遮好） |
| 成片超 300 MB | 全片 | 自检命令 `size_MB` > 300 | 码率降到 6 Mbps（约 116 MB / 155 s），分辨率保持 1080p；不要靠删证据镜头压时长 |
| 原始素材丢失 | 全片 | 只剩成片 | 保留 `build/demo_raw/S01…S10` 与 `build/demo_final.mp4`，不要覆盖；提交包只放成片 |

> 板子离线时的最小成片：S1、S3、S6、S9、S10 可以照常完成；S2、S4、S5、S7、S8 记为
> 「待补拍」。**绝不能用主机侧渲染图冒充实拍屏摄**；若当天补不齐，宁可少一个镜头，
> 也不要把「待上板确认」的内容说成已验证。
