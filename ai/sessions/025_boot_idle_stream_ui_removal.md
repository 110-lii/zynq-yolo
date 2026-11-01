# Session 025 — 2026-11-01 —— 删掉选色 UI、HDMI 待机白屏、上电即用（D028/D029）、电脑推流

> 承接 [024](024_honesty_audit_and_repack.md)（D015 输入侧上板验等价）。
> 用户本轮原文：「现在 hdmi 让他的空闲时显示白色，把整个固化到板子上，让我可以一上电就可以使用，
> 还有就是电脑软件，我怎么推流」「选颜色的 ui 对识别有用吗，没有就删掉」
> 「现在你我演示视频需要的硬件条件准备好」。

| 字段 | 内容 |
|---|---|
| 会话号 | 025 |
| 日期 | 2026-11-01 |
| 执行 AI（模型 ID） | `mythos-magpie-5487751739`（客户端 `claude`，桌面端 Code 标签页） |
| 执行客户端 / 工具 | Claude Code（桌面端）+ 板端通道 `tools/board_jupyter.py`（Jupyter 9090） |
| 角色 | 单人 |
| 任务目标 | ① 判定并删除「选颜色 UI」；② HDMI 空闲白屏；③ 整链固化为开机自启；④ 给出 PC 推流方法 |
| 上下文投喂 | `AGENTS.md`、`ai/coordination/DECISIONS.md`（D013/D013-a/D027）、`tools/lcd_ui.py`、`tools/board_pipeline.py`、`tools/hdmi_video.py`、`tools/flow_overlay.py`、`docs/board_environment.md` |
| **原文位置** | **待导出**：本会话在 Claude Code 桌面端，仓库仍无 Claude Code 会话导出脚本（同 023/024 的已知缺口，不编造文件名） |
| 关联决策 | **D028**（选色 UI 退线删除）、**D029**（上电即用 + HDMI 白屏）、D013/D013-a、D027 |
| 复核方 | 未复核（白屏仍待人眼） |

---

## 轮次 1 — 「选颜色 UI 对识别有用吗」：按 D013-a 自己的标准判，无用

用户问得很直接。判定不能靠印象，于是回到 **D013-a 的原文判据**：「若某项改动只产出『颜色标签』
而没有提升『球员被识别出来/被正确归类』的指标，则不算完成 D013 的意图」。

逐条核对 `tools/lcd_ui.py` 的实际作用：

| 问题 | 事实 |
|---|---|
| 影响检测吗？ | **不影响**。检测走 ncnn YOLO，球链路走白块候选 + C 分类器；球衣色不参与 |
| 产出是什么？ | 一份 `board/ui_config.json`（10 种球衣色里选 7 项） |
| 这份配置喂给谁？ | `tools/team_assign.py`，给 `players[*].team` 贴 A/B 标签 |
| 有真值吗？ | **没有**。板上跑出 `TEAM_ASSIGN tracks=42 labeled=15`，没有任何人工队别标注可以核 |
| 真按键按过吗？ | 022 只做过主机侧回放 `LCD_UI_SAVED`；**真按键从未完整走通** |

⇒ 拿不出任何「识别指标上涨」的证据，按 D013-a = **无用**，登记 **D028** 并删除
`tools/lcd_ui.py`（连同 `report/figures/lcd_ui_4bpp.png`）。保留：`board/ui_config.json`
仍可手写，`team_assign.py` 仍读它；颜色量测工具作为历史资产保留。

同步改到的文件：`report/design_report.md`（§2.9 段落、图 4b、§7 命令、§8 第 8 条、§10 结论）、
`report/technical_document.md`、`report/submission_checklist.md`、`report/demo_video_storyboard.md`
（**删掉 S8 镜头，总时长 155 s → 139 s**，S9/S10 编号保持不变以免与已录素材文件名冲突）、
`docs/system_status.md`、`docs/flow_overlay.md`、`tools/README.md`、`report/figures/README.md`。

---

## 轮次 2 — HDMI 空闲白屏 + 上电即用（D029）

### 实现

| 文件 | 作用 |
|---|---|
| `tools/board_idle.py` | 加载 `flow_system.bit` → HDMI 写纯白 → **常驻** |
| `tools/install_boot.py` | 推文件、写两个 systemd unit、enable、启动；`--dry-run` 不连板子 |
| `docs/streaming_quickstart.md` | 「电脑怎么推流」的操作手册（含故障表） |

**为什么 idle 进程必须常驻**：HDMI 帧缓存是 PYNQ `allocate()` 的 DDR 缓冲，进程退出缓冲就释放，
扫描输出会读到失效页面（黑屏/花屏）。这一点写在两个文件头里，防止后人「优化」成画一次就退。

**为什么白屏不是敷衍**：`fb_scanout` 已知缺陷是**整行循环旋转**（恒定 `+313` 列）。
纯白既没有内容可旋转，也不承载信息；空闲屏上不该出现任何「像检测结果」的东西。

### 上板实测（板子当次在线）

| 步骤 | 结果 |
|---|---|
| 第一次装 | `BOOT_SERVICE_STATE activating active`，idle 服务反复重启 |
| 查日志 | `ModuleNotFoundError: No module named 'pynq'` |
| 定位 | 板上 `sys.executable` = `/usr/local/share/pynq-venv/bin/python3`；系统 `/usr/bin/python3` 里没有 pynq |
| 改完再装 | `No Devices Found, is the XRT environment sourced?` |
| 再定位 | Jupyter 内核的 `XILINX_XRT=/usr`，systemd 不继承 ⇒ unit 里加 `Environment=XILINX_XRT=/usr` |
| 第三装 | `BOOT_SERVICE_STATE active active`；`IDLE_OVERLAY_OK` + `IDLE_HDMI_WHITE_OK frames=31 under=0` |
| **真重启一次** | 18 s 后 `IDLE_HDMI_WHITE_OK frames=35 under=0`，`is-active` 两行均 `active` |
| 重启后推流 | PC 推 20 帧 → 板端 `PIPELINE_OK`，`net_*_00.jsonl` 20 行 |
| 推 40 帧（重启前） | `PIPELINE_OK frames=40 detections=3 med_infer_ms=984.1`，落盘 40 行 |
| 演示文件自检 | `MISSING []`（storyboard P1 的命令，10 个关键文件齐全） |

⚠️ **白屏仍无人眼**：机器只证「配置写进去了、扫描在跑（`frames` 涨、`under=0`）」。
这正是 10-31 `HDMI_VISUAL_OK` 撤回时立下的规矩——机器判据不能代替人眼。

---

## 轮次 3 — 电脑怎么推流

`docs/streaming_quickstart.md` 已写入，核心三条：

```bash
# 1) 板子已就绪？两个服务都该是 active
py tools/board_jupyter.py "import subprocess;print(subprocess.run('systemctl is-active zynq-idle zynq-stream',shell=True,capture_output=True,text=True).stdout)"

# 2) 推一段视频（默认 JPEG q95；别改 80，会多出假球）
py tools/video_send.py --video data/samples/scenes/c03_t01290.0.mp4 --host 192.168.2.99 --port 5007

# 3) 看板端日志与落盘
py tools/board_jupyter.py "import subprocess;print(subprocess.run('journalctl -u zynq-stream -n 40 --no-pager',shell=True,capture_output=True,text=True).stdout[-3000:])"
```

**边界要说清**：推流时 HDMI **仍是白屏**——`board_pipeline` 只做检测与落盘，不把收到的画面送回
HDMI；带框回放（D026）走的是另一条路径且人眼判据已撤回。所以「PC 推流 → 板上看画面 → 回传」
这个闭环**没有实现**，现场不能说它是实时的。

---

---

## 轮次 4 — 推流时把「处理之后的画面」送上 HDMI（用户追问）

用户问：「推流时 HDMI 仍是白屏是什么意思，那怎么显示处理之后的视频」。

原状：`board_pipeline` 只做检测与落盘，从不写 HDMI 帧缓存；空闲白屏由 `zynq-idle` 每 5 s
刷一次，所以推流时屏上不会变。这不是缺陷，是没做。

**做法**（`board_pipeline --hdmi-preview`）：

| 环节 | 实现 |
|---|---|
| 画 | 复用 `hdmi_video.draw_frame(norm_frame(rec))` —— 与网口回传**同一套画法**，不另造一份 |
| 送 | `HdmiVideoOut.show()` 写 DDR + 切缓冲（与 D026 同一条 `fb_scanout` 通路） |
| 不打架 | 起服务时 touch `/tmp/hdmi_preview.active`；`board_idle` 见到它就跳过刷白 |
| 收尾 | 推完写回纯白并删标记 ⇒ 屏上回到待机，不会留着最后一张带框图 |

**中途踩的坑**：第一次改完管线在收尾时崩了 —— `NameError: name 'pl_ms_med' is not defined`，
原因是我插入收尾块时把 `pl_ms_med = ...` 那行吃掉了。修好后重跑干净通过。

**实测**（2026-11-01）：

```
HDMI_PREVIEW_ON /home/xilinx/zynq-yolo/board/overlay/flow_system.bit
HDMI_PREVIEW_OFF white_restored
PIPELINE_OK frames=30 detections=2 med_infer_ms=997.3
```

落盘 `net_*_00.jsonl` 30 行；服务随后自动回到 `NET_IN_LISTEN port=5007`。

**边界**：画面只有机器判据（`frames` 增长、`under=0`、token 顺序对），**没有人眼看**；
且 HDMI 有间歇整行旋转（`+313` 列），所以回显能用来「看着像那么回事」，
**不能**当验收依据 —— 验收仍走 PC 侧 JSONL 回放。要关掉回显：
`py tools/install_boot.py --no-hdmi-preview`。

## 未决 / 下一步

1. **人眼**：HDMI 白屏、LCD 面板、PC 面板——都要看一眼。
2. 演示视频、授权书、全貌图：只能人工。
3. 推流回显已做（轮次 4），但画面未人眼确认；`fb_scanout` 的整行旋转缺陷仍会影响它的可信度。
