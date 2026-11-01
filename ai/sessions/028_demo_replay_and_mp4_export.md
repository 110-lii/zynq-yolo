# Session 028 — 演示段跑通后：PC 录像出片 + 软件/HDMI 循环播放

> 整理层记录。原文层（`ai/raw/`）已于 2026-11-01 从仓库下架，本记录的「原文位置」只在本地可跳。

| 字段 | 内容 |
|---|---|
| 会话号 | 028 |
| 日期 | 2026-11-01 |
| 执行 AI（模型 ID） | 主会话模型（Claude Code，见客户端） |
| 执行客户端 / 工具 | `claude`（Claude Code 桌面端 Code 标签页） |
| 角色 | 单人 |
| 任务目标 | 把 `--demo` 那一段（c05 整段 250 帧）跑完，在 PC 软件与 HDMI 上反复播放，并在电脑上另存一份「处理完的视频」 |
| 上下文投喂 | `tools/pc_dashboard.py`（`--demo` 装配、`replay_worker`、`render_dashboard`）、`tools/hdmi_video.py`（`HdmiVideoOut`/`calibrate_shift`）、`docs/demo_run_sheet.md` §1.5、`docs/streaming_quickstart.md` §2.5、AGENTS.md §2 |
| **原文位置** | 待导出（`ai/raw/` 已下架；本地分片见会话目录） |
| 关联决策 | D031（HDMI 行内平移补正）、D041（文档不写死 commit hash） |
| 复核方 | 未复核（人眼看屏一项未做，见「待办」） |

---

## 轮次 1 — 「把这一段演示的先处理好，然后再软件和hdmi上反复播放」

**输入提示词（逐字）：**

```
现在你把这一段演示的先处理好，然后再软件和hdmi上反复播放
```

**做了什么：**

- 先跑 `py tools/pc_dashboard.py --demo`（板端体检 → 事件管线 5009 → HDMI 回显 + LCD → 推 c05
  整段 250 帧 → 等管线退出 → sha256 拉回 → PC 回放）。
- 板端日志（`/tmp/pc_event_run.log`）确认跑完：
  `HDMI_ALIGN shift=0 residue=0` → `HDMI_PREVIEW_ON` → `LCD_PANEL_OK ×50` →
  `HDMI_PREVIEW_OFF white_restored` → `STAGES detect_ms=77721 ball_ms=89529 frames=250` →
  `PIPELINE_OK frames=250 detections=50`。
- 结果拉回：`build/pull/evt_20261031_022109_00.jsonl`（250 行 / 210,791 字节），
  32 帧球场坐标球、球权 `#32 2.38 m`（f110）`#33 1.93 m`（f130）`#26 1.09 m`（f166–171），
  与 10-31 那两次逐帧一致。
- PC 软件循环播放：`pc_dashboard.py --replay build/pull/evt_20261031_022109_00.jsonl
  --video data/samples/scenes/c05_t01730.0.mp4 --calib data/calib/field_calib_ball.json --loop`。
- HDMI 循环播放：计划在板上起 `tools/hdmi_video.py --replay … --video c05 --calib …
  --fps 25`（`--timeout 0` 默认无限循环）。**未起成，见「待办」。**

**我的判断：** 采纳（PC 侧循环 + 出片），HDMI 侧停在上板前一步。

**理由：** 板子 22:44 起 `ping` 100% 丢包、9090/5007 端口全断，按 AGENTS.md §2
「板子离线时不做任何硬件验证、也不声称已验证」，只能记 todo。

## 轮次 2 — 「现在电脑上也保存一份处理完的视频」

**输入提示词（逐字）：**

```
现在电脑上也保存一份处理完的视频
```

**做了什么：** 给 `pc_dashboard.py` 加 `--dump-video <out.mp4>`（+ `--dump-fps`）：
回放同一份 JSONL，把面板**原样**（左侧带框画面 / 事件流 / 底部统计）逐帧写进 MP4，不按回放节奏等。
产物 `build/demo_c05_processed.mp4`：250 帧、1240×780、25 fps（= 片源速度，10 s）、5.5 MB，
判据令牌 `PC_DASHBOARD_MP4 … frames=250 fps=25.0 bytes=5503849`。抽第 166 帧核对：
红圈套球 + `BALL 53.0, 59.3 m`、事件流三条 `球权`、底栏 `球权: #26 · 距球 1.09 m`。

**我的判断：** 采纳。

**理由：** 录像内容与软件里看到的是同一份渲染函数（`render_dashboard`），
所以「存下来的视频」和「屏上反复播放的画面」不会出现两套观感。

---

## ★ 自我纠错轨迹

| 项目 | 内容 |
|---|---|
| **错在哪**（原始判断） | 用 `py -c "…subprocess.run(['bash','-lc', …])…"` 经 Jupyter 在板上起 HDMI 循环；以为 `bash -lc` 会秒返回 |
| **怎么发现**（触发条件） | 该调用挂住 4 分钟无输出；随后 `GET /api/kernels` 显示内核 `execution_state=busy`，而 `ping` 仍通 ⇒ 是**内核被一条不返回的 `bash -lc` 占住**，不是板子掉线 |
| **证据** | `KERNELS [('00fbd297', 'busy')]`；`POST /api/kernels/<id>/restart` 返回 200 后立刻 `AFTER ['idle']`。对照：同一时刻 `ping 192.168.2.99` 0% 丢包 |
| **怎么改**（修正动作） | 重启内核解卡；改成在核心里直接 `subprocess.Popen([...], stdout=log, stderr=STDOUT, start_new_session=True)`，不经登录 shell |
| **判据**（怎么确认改对了） | 内核回到 `idle`（`AFTER ['idle']`）；下一版启动脚本不再包含 `bash -lc` |
| **根因归纳** | 在 PYNQ 板的内核里**不要走登录 shell**（profile 可能阻塞），后台进程用 `Popen(start_new_session=True)` 直接起 |

> 该修复**没有**在板上复验成——修完的时刻板子已经掉线（`ping` 100% 丢包、9090/5007 均不通）。
> 所以只记「写法改了」，不记「HDMI 循环跑起来了」。

## 待办（板子离线，未验证）

1. 给板子上电，等 60–90 s，跑 §0 体检三行（期望两行 `active` + `no_preview_flag`）。
2. 板上起 HDMI 循环：直接 `Popen(['python3','tools/hdmi_video.py','--replay',
   'build/flow/evt_20261031_022109_00.jsonl','--video','data/samples/scenes/c05_t01730.0.mp4',
   '--calib','data/calib/field_calib_ball.json','--fps','25'])`，先 `touch /tmp/hdmi_preview.active`
   （否则 `zynq-idle` 每 5 s 把屏刷白）。
3. 日志判据：`HDMI_ALIGN shift=… residue=…` 出现且 `residue` 为 0；**屏上观感仍要人眼看**，
   在没看到之前不记 `HDMI_VISUAL_OK`。
4. 收尾：杀掉该进程、删 `/tmp/hdmi_preview.active`，确认屏回纯白。
