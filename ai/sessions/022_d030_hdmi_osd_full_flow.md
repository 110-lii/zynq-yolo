# 022 · 2026-10-30 —— HDMI 带框环出的自证、整体流程一条命令、赛前 UI 升级到 v2

> 承接 [021](021_lcd_probe_from_scratch.md)（LCD 通路从零重做 + 4bpp/调色板/按键出片）。
> 本会话按 [`../docs/next_session_prompt_20261030.md`](../docs/next_session_prompt_20261030.md)
> 的顺序推进：先确认出片 → 再上板 → HDMI → 整体流程 → 提交整理。
> **板子全天离线（07:47 / 08:04 / 08:18 三次 ping 全丢）**，所以凡是需要屏幕的结论一律记为待验。

## 1. 出片状态确认（不重出）

`board/overlay/lcd_probe.bit` 时间戳 **10-29 23:11**、`.hwh` **23:03**，是 021 末尾那一片
（4bpp + 16 色调色板 + 按键），WNS **+1.839 ns** / WHS **+0.018 ns**。
按提示词「是就直接上板、不要再无谓重出」——本会话**没有再跑 Vivado**。

`board/overlay/hdmi_video.bit` **10-27 11:56**、`.hwh` 11:48，均**晚于**其 RTL
（`fb_scanout.v` 09:39、`hdmi_video_top.v` 10:14）与 Tcl（11:38）⇒ 与源码同批，不需要重出。

## 2. HDMI「带框的画面」：主机侧两把尺子（D026）

| 步 | 命令 | 结果 |
|---|---|---|
| 仿真 | `py sim/scripts/run_sim.py --only fb_scanout` | **`SIM_PASS 1/1`**（把 `sim/tb/tb_fb_scanout.v` 注册进 `CASES`，判据 `TB_FB_SCANOUT_OK`：AR/R 握手、stride≠width*4 换行、显示像素==DDR 像素） |
| 打包自检 | `py tools/hdmi_video.py --selftest` | `HDMI_VIDEO_SELFTEST OK`，`pack_roundtrip_dmax=0` |
| 出图 | `py tools/hdmi_video.py --replay … --video inc_01….mp4 --png-out …` | `HDMI_VIDEO_OK 1 0`，见 `.scratch/hdmi_osd_boxes.png` |

出图看什么（人眼，机器判不了）：**4 个绿色球员框带 `#id`、红圈标球、顶部黑底黄字信息条**
（`frame 1200  players 4  POSS #3 1.20m  CAM:fixed`）都在，且框落在真实画面里的人身上。

★ 诚实边界：这张图的**框是合成坐标**（`.scratch/osd_demo.jsonl` 给了像素框），
目的是证明「渲染器会画、位置按输入走」，**不等于**「检测器给的框是对的」。
真实框要等板子上线、跑真管线 JSONL 才谈得上。同理，`hdmi_video.bit` 本身**从未上板**，
`under`（行欠载）计数也还没看过。

## 3. 整体流程一条命令：`tools/check_full_flow.py`（新增）

把已有的件按真实顺序串起来，每步只认**自己的令牌**，最后打一张表：

```
时间对齐(board_clock) → 赛前配置(ui_config v2) → 结果落盘(ResultStore)
  → 板→PC 推流(result_stream ↔ pc_dashboard --listen) → LCD 状态面板 → 赛前 UI
```

主机侧实测：**`FULL_FLOW_TABLE ok=5 skip=1 fail=0` / `FULL_FLOW_OK`**（skip 的是 board_clock，
板子离线）。产物：`build/flow/store/run_*.jsonl` + `summary.json`、`build/flow/status_lcd.png`、
`build/flow/lcd_ui.png`。

踩到的两个坑（都修了）：

1. **`result_stream` 默认从 EOF 开始读**，文件写完再传就一行也发不出去，且连不上会无限重试
   ⇒ 流程里必须带 `--from-start`，并给子进程设超时（`run_mod` 现在把超时变成 FAIL 而不是抛出）。
2. **不要用「试连一下端口」探活**：`pc_dashboard --listen` 会把空连接当成一次会话结束而后退，
   探活反而把要用的服务弄死。改成固定等待 + 检查进程是否还活着。

## 4. 赛前 UI 升级：同一份 schema（D013）

原来只存自定义的 `ui_config.json`（jersey_a/jersey_b…），下游用不上。现在改成
**`zynq-yolo/match-config/2`**（`tools/match_setup.py` 定义、网页前端模拟的同一份）：

- 7 项：A/B 队球衣、A/B 队守门员、裁判衣服、A 队上半场（左/右）、开始比赛；
- 颜色只能从 `classify_players.PALETTE_REF` 的 **10 个名字**里翻（拼错会静默变 unknown）；
  屏上的色块 RGB 由该名字自己的 HSV 参考值换算 ⇒ **屏上看到的色 == 分类器判色的参考色**；
- 确认时用 `match_setup._validate` 校验（两队同色直接 `LCD_UI_FAIL invalid same_team_color`），
  落盘 `board/ui_config.json`；
- 16 色调色板 = 6 个界面色 + 10 个球衣色（正好 16，显式给定，不用自动量化）。

主机侧预览（按调色板量化后还原，等于面板所见）：`board/fb/ui_preview_q.png`。
回放自测：`py tools/lcd_ui.py --preview-only --cfg temp.json --script 0,1,0,0,0,0,0,1`
⇒ `LCD_UI_SAVED … a=red b=green gk=green/yellow ref=black side=left`。

## 5. 待验清单（板子上线后按这个顺序做）

| # | 动作 | 机器判据 |
|---|---|---|
| 1 | `py tools/lcd_probe_host.py deploy`（已把 `--put` 改成新加的 `--push`，并补全 bit/hwh/bram_fb/status_display/lcd_probe/lcd_ui/tag4） | 每个文件两端 sha256 一致 |
| 2 | `py .scratch/verify4_board.py` | `READBACK_OK bad=0`，每行 `de=800` |
| 3 | `python3 tools/status_display.py --size 800x480 --fb-bram 0x40000000 --once` | `layout=800x480 bpp=4 words=48016` |
| 4 | `python3 tools/lcd_ui.py --keys` → `--timeout 900` | 读到合理空闲电平；按下后 `LCD_UI_COLOR/SELECT` 递增 |
| 5 | 人眼看：tag4 彩条 / 彩色面板 / UI 配色 / HDMI 带框图 | — |
| 6 | `py tools/check_full_flow.py --board` | `FULL_FLOW_OK`（此时 clock 不再是 SKIP） |

## 6. 结论

- 主机侧能验的今天都验了：**fb_scanout 仿真通过、OSD 渲染正确、整体流程 5/5 通过**。
- **凡是需要屏幕的，一条都没有验**：4bpp 上屏、配色、按键、HDMI 环出、双屏。
  这不是「差一点」，是「没做过」——板上线后按 §5 从头走一遍。
- 出片资源/时序没动过，不需要重出 LCD 那一片。
