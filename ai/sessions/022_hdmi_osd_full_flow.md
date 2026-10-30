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

主机侧实测：**`FULL_FLOW_TABLE ok=5 skip=1 fail=0` / `FULL_FLOW_OK`**（当天下午加了 team 步后为 ok=6，见 §7.2）（skip 的是 board_clock，
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
| 2 | `py tools/check_lcd4_board.py` | `READBACK_OK bad=0`，每行 `de=800` |
| 3 | `python3 tools/status_display.py --size 800x480 --fb-bram 0x40000000 --once` | `layout=800x480 bpp=4 words=48016` |
| 4 | `python3 tools/lcd_ui.py --keys` → `--timeout 900` | 读到合理空闲电平；按下后 `LCD_UI_COLOR/SELECT` 递增 |
| 5 | 人眼看：tag4 彩条 / 彩色面板 / UI 配色 / HDMI 带框图 | — |
| 6 | `py tools/check_full_flow.py --board` | `FULL_FLOW_OK`（此时 clock 不再是 SKIP） |

## 6. 结论

- 主机侧能验的今天都验了：**fb_scanout 仿真通过、OSD 渲染正确、整体流程 5/5 通过**。
- **凡是需要屏幕的，一条都没有验**：4bpp 上屏、配色、按键、HDMI 环出、双屏。
  这不是「差一点」，是「没做过」——板上线后按 §5 从头走一遍。
- 出片资源/时序没动过，不需要重出 LCD 那一片。

---

## 7. 同日下午续（2026-10-30 11:00 前后）—— 用户："现在做可以完成的事情"

板子仍离线，所以这一轮只做**不依赖屏幕**的活：先把文档的一致性风险清掉，再把
「能落地但一直没接」的队别接进主线。**没有一条新结论建立在看屏上。**

### 7.1 文档一致性（评审第一眼看到的东西）

| 问题 | 处理 | 证据 |
|---|---|---|
| `design_report.md` 把「HDMI 视频环出/OSD」写成**明确不做**（被 D026 推翻） | §1.2 改成第 5 项交付，并注明口径变更 | `report/design_report.md` §1.2 |
| 报告内部三种说法打架：「HDMI 无图像」/「画面已通过（014）」/「均未修复」 | 全文统一 **BRAM_OK / VISUAL_OK 两级判据**；如实写成「HDMI 状态面板 BRAM_OK 通过、VISUAL_OK 待人眼；LCD 4bpp 新片两级都没做」 | 同上 §2.9 / §4.7 / §8 / §9 / §10 |
| 报告缺 D026 | 新增 §2.10（结构/寄存器/两条硬约束/证据边界），补图 7（主机侧渲染，框坐标为演示给定） | 同上 §2.10 |
| `design_report.docx` 比 md 旧 | 重生成（1,231,851 B，含两张新图） | `py tools/md_to_docx.py` |
| `submission_checklist.md` 自相矛盾（§1 已通过 / §7 未修复） | §1、§3.1、§7 全部按两级判据重写；包统计不再抄具体数字 | 同上 |
| `docs/system_status.md` 停在 10-25 | 更新到 10-30：LCD 三轮重做、4bpp、按键、D026、整体流程 | 同上 |
| `docs/architecture.md` 仍写「不做 HDMI/OSD 视频输出」 | 改为 D026 要做 | 同上 |
| `docs/repo_spec.md` 自称唯一事实源却停在 10-19 | §7 加状态同步段（第 2 项已由 D004 裁定，其余四项仍 discussing） | 同上 |
| `DECISIONS.md` 汇总写「decided 14 条」但同句列了 20 条、且没纳入 D026 | 改为 20 条并说明双状态计法 | `ai/coordination/DECISIONS.md` |
| 会话文件名叫 `022_d030_…`，正文从没解释 d030 | 改名 `022_hdmi_osd_full_flow.md`，更新 INDEX/TODAY_REPORT 引用 | 本文件 |
| `report/llm_logs/` 只覆盖到 10-25 | 新增 `05_display_rework_evidence.md`（012–022，含四条被证据推翻的判断） | `report/llm_logs/05` |
| 20+ 个受控子目录没有 README（repo_spec §3 要求） | 补 36 份（内容逐目录手写：装什么/谁产生/进 git/规则/怎么用） | `git show --stat` |

自检：`py tools/check_repo.py` → `SUMMARY: 6 PASS / 0 FAIL / 0 WARN` / `CHECK_REPO_OK`。

### 7.2 队别接入主线（D012/D013 的落点，主机侧可做）

新增 `tools/team_assign.py`：

- 读 `match-config/2`（`board/ui_config.json`，与 `lcd_ui.py` 屏上录入同一份）→
  `{A, B, GK_A, GK_B, REF}` 色板 → 交给 `classify_players.classify()`（默认 v1 口径，门限 0.16 不动）；
- 按 `track_id` **累积投票**：默认 ≥3 票且多数占比 ≥0.6 才出结论，否则 `unknown`（不硬猜）；
- `board_pipeline.py` 新增 `--ui-config` / `--no-team`，默认读 `board/ui_config.json`；
  配置不存在时打印 `TEAM_ASSIGN_OFF` 并**保持旧行为**（回归安全）；
- `result_publisher.build_frame(teams=…)` 写 `players[*].team`；`events.step()` 新增
  `possession_team`（取持球者的队别，无队别信息时为 None）。

判据（全部主机侧，机器可判）：

| 命令 | 令牌 |
|---|---|
| `py tools/team_assign.py --selftest` | `TEAM_ASSIGN_SELFTEST OK`（红/蓝/黄/草坪合成帧；票数不足→unknown；GK 归队） |
| `py tools/result_publisher.py --selftest` | `PUBLISH_SELFTEST OK`（含 teams 给/不给两种路径） |
| `py tools/events.py --selftest` | `EVENT_SELFTEST OK` |
| `py tools/check_team_assign.py --tracks data/results/tracks/inc_01_t01154.9.json --palette A=red,B=white` | `TEAM_EVAL tracks=77 voted=57 labeled=55 A=28 B=27 unknown=22` |
| 同上，inc_06 | `TEAM_EVAL tracks=64 voted=56 labeled=53 A=21 B=32 unknown=11` |
| 反例：`--palette A=purple,B=cyan` | `voted=9/77` + `TEAM_EVAL_WARN low_vote_rate` |
| 整体流程加了 team 步 | `py tools/check_full_flow.py --dry-run` → `FULL_FLOW_TABLE ok=6 skip=1 fail=0`（team 步吃 ui_config 的色板，实测 `labeled=46/77`） |

★ **诚实边界**：没有人工标注的队别真值，所以上面只是「链路通、没塌成单一类」，
**不是准确率**；真实转播里的深色球衣/逆光仍会把 v1 的 0.16 门限顶到 unknown。
板上与真实画面的端到端（`board_pipeline --ui-config`）都还没跑。

### 7.3 仍未做（本轮明确不碰）

1. 所有看屏结论（4bpp 上屏、配色、按键、HDMI 环出、双屏）—— 板子离线；
2. D015 的「PC 经以太网送视频」接收端 —— 协议/端口仍 `discussing`，属功能开发而非「补漏」；
3. D003/D005/D006/D007 四条裁定 —— 需用户拍板，本轮只把状态写准，不替用户定。

### 7.4 离线跟踪链补上单应 + HLS 技能包补检查（同日稍晚）

**`tools/track_players.py --calib`**：此前该文件的轨迹只有像素（`fx/fy` 是**图像**落脚点），
`events.py` 的米制判据在离线链上根本跑不到。现在给了 `--calib`（`field_coords.py` 产出的 `calib.json`）
就对每个点写 `X/Y`（米），输入用**相机补偿后**的 `sx/sy`（与「标定在该机位首帧」的约定一致），
并单独给出 `n_field` 让"没映射上"可见；不给 `--calib` 时行为与之前**逐字节一致**。
映射逻辑抽成 `apply_calib(tracks, Hm)`，主机侧单元验证：
已知仿射（640×360 → 105×68）三点 → `(0,0) / (105,68) / (52.5,34)`，`APPLY_CALIB_OK`。
⚠️ 没跑真实片段（要 YOLO），也没在板上跑；`limitations` 里已如实写明。

**`skill/hls_axi_stream_accel/checks/axis_stream_check.py`**：HLS 包的「握手规则检查器」
（纯标准库），吃 TB 用 `$fdisplay` dump 的逐拍 trace，查 R1（`tvalid&&!tready` 期间数据变化）、
R2（握手完成前撤回 `tvalid`）、R3（连续拖欠超门限）、R4（拍数不符）。
自检 `AXIS_CHECK_SELFTEST OK`（1 正例 + 3 负例），文件级测试 `beats=2`、`--expect-beats 5` 正确报 R4。
至此两个技能包都有 `checks/`；`skill/README.md` 清单同步。

### 7.5 打包

`py tools/make_submission.py`：`630 文件 / zip 22.96 MB / SUBMISSION_PACK_OK`，
旧的 08:53 包已删，`dist/` 只留当天一份（若晚上还有改动需再出一次）。

### 7.6 收工：四条裁定的草案 + 最终打包

D003/D005/D006/D007 仍 `discussing`，按 `DECISIONS.md` 的纪律，这几条在裁定前**不能**用来
改 `src/`、`build/`、`board/`。所以本轮只做一件事：把它们摊成
`ai/coordination/DECISION_DRAFTS_20261030.md`（**非绑定**），每条给「现状（带证据）/ 选项 /
建议 / 采纳后要改哪些文件」，让用户一句话就能拍板：D003 建议「算法 HLS + 时序读侧手写 RTL」
（即现状，只是没登记）；D005 建议「现在写内部使用声明、长期换可分发数据集」，并单独标出
**唯一我替用户判断不了的事**（片段的使用权）；D006 建议把已在用的地址/格式冻结成 v1 契约，
改契约 = 新开一条 decision；D007 建议「复用经验、不复用源码，引用要写清哪个文件哪条结论」。

最终包：`dist/zynq-yolo_submission_20261030_1105.zip`（**633 文件 / 22,966,531 B**，
`CHECK_REPO_OK` + `SUBMISSION_PACK_OK`），已核 zip 内含 `--calib` 版 `track_players.py`、
`checks/axis_stream_check.py` 与裁定草案；10:59 那包已删，`dist/` 只留当天一份。

### 7.7 上板（2026-10-30 11:40–12:20，板子短暂在线）

板子掉线多次（含用户手动重启一次），所有上板动作都在线窗口内完成并留了令牌：

| 动作 | 结果 |
|---|---|
| 推送 | `lcd_probe.bit/.hwh`、`lcd_probe.py`、`tag4.bin`、`hdmi_video.bit/.hwh`、`hdmi_video.py`、`check_hdmi_video_board.py`；两端 sha256 一致 |
| LCD 4bpp 读侧自证 | **`READBACK_OK bad=0 checked=10`**，PCLK **33.335 MHz**；重启前后各跑一次，结果一致 |
| LCD 自证图 `tag4` | 用户看屏后确认「对」（斜向 16 色条纹）⇒ **该图 `VISUAL_OK` 通过** |
| HDMI 带框环出 | **`HDMI_VIDEO_BOARD_OK frames=313 under=0`**（5 s 内 305 帧、行欠载 0）⇒ `BRAM_OK` 级通过 |
| 整体流程 | **`FULL_FLOW_OK ok=8 skip=0 fail=0`**（clock/deploy/ui_config/team/store/stream/panel/lcd_ui 全 OK） |
| 赛前 UI 按键 | `LCD_UI_KEYS raw=0x3 key0=1 key1=1`（空闲电平正确）；**真按键仍未按过** |
| 彩色状态面板 | 写屏命令发出时板子掉线（`ConnectionResetError`）⇒ **未确认，不算过** |

**上板暴露的三处「假失败」（已修，提交 `f7245ba`）**：

1. `lcd_probe_host.py deploy` 引用未定义的 `DEPLOY` —— 第一次就在主机侧炸；
2. `lcd_probe_host.py run` / `check_full_flow --board` 把 shell 串丢给 Jupyter 内核，
   `cd X && python3 …` 被 `cd` 魔法当成目录名（`Errno 2`）⇒ 一律改走 `subprocess`；
3. `check_full_flow --board` 的 panel/UI 原来在**主机**上调 `bram_fb`（Windows 无 `os.O_SYNC`）
   ⇒ 改为先推 tools+字体+ui_config 再在板上跑，`deploy` 成为表里的一步。

**判据越界（值得单独记一笔）**：`check_lcd4_board.py` 第一版把 RTL 的抓取语义理解错了 ——
`st/cnt` 在「目标行的上一行」装载那一拍（`hit_load`）更新，且 `cap_row==0` 被 RTL 显式排除、
`cap_row==1` 的装载行是场消隐行（`de_last` 恒 0）。于是第 0/1 行：`cap_row=0` 的寄存器**从没写过**
（读到 0 会被判 BAD，但也可能读到上一行的陈旧值而被判 OK），`cap_row=1` 的 `de` 恒 0。
现在这两行只判 `lb`（内容），并把例外写进脚本文件头与报告 §2.9 —— **假通过比失败更危险**。

### 7.8 赛前 UI 真按键、HDMI 上板画面与「老掉线」的根因（2026-10-30 下午）

**赛前 UI 真按键通过**（板端日志，非回放脚本）：

```
LCD_UI_COLOR item=裁判衣服 value=purple → cyan
LCD_UI_SELECT n=5 item=A 队上半场
LCD_UI_SELECT n=6 item=开始比赛
LCD_UI_SAVED … a=red b=blue gk=green/purple ref=cyan side=left
```

⇒ 按键 → 换色 → 逐项 → 确认保存这条交互链在真屏真键上走通（`tools/lcd_ui.py`）。

**彩色状态面板上屏通过**：第二次上板时 `STATUS_DISPLAY_BRAM … words=48000` + `LCD_CFG_OK`，
用户看屏确认与主机侧参考图一致（橙标题栏、7 行竖色条、中文清晰）。
⚠️ 另有一个真 bug 在此暴露：`lcd_probe` overlay 刚装时 `cfg=0`，选的是 **PL 彩条图案**，
`status_display`/`lcd_ui` 只写帧缓存不切 `cfg` ⇒ 屏上一直是彩条。已修（`6ea9fb0`）。

**HDMI 带框环出机器判据复现**：第二次上板 `HDMI_VIDEO_BOARD_OK frames=314 under=0`。

**「板子老掉线」的根因**（详见 `docs/board_environment.md`）：

- **A 类：网络地址丢失**。改前 `eth0` 跑 DHCP（这条链路没有 DHCP 服务器，dhclient 永远失败、
  每轮重启 `systemd-resolved`），静态 IP 只挂在 `eth0:1` 别名上；任何一次 `ifdown`/`flush`
  都会把地址带走 ⇒ 板子在线但不可达。证据：不可达期间心跳日志仍在逐行写（`up=` 继续涨、`link=up`）。
- **B 类：整机硬复位**。`journalctl` 里每次 boot 都止于 DHCP 重试那一行，**没有 shutdown 序列、
  没有 panic**；`panic=0`、`RuntimeWatchdogUSec=0` ⇒ 不是内核 panic、也不是软看门狗。
  另外 `dhclient.conf` 里有 `reboot 3;`（重试 3 次重启整机），是镜像自带的一颗地雷。
- 板子**没有 RTC**，断电后时间回 1970/2021，日志时间不可信，排查只能按 `boot=` 编号对。
- 处置（可回退，备份 `.bak-20261030`）：`eth0` 改 `inet manual`（地址仍留 `eth0:1`）、
  注释 `reboot 3`、停 dhclient、装 `net_guard.sh` + cron 每分钟兜底、心跳日志加记 IP 与 dhclient 数。

### 7.9 续（10-30 下午）：全白归因、场景分层测试集、全流程 overlay 合并

#### 1) 第三次掉线与「新配置首次生效」

第三次硬复位后重新上电，读回状态：`/etc/network/interfaces.d/eth0` 已是
`iface eth0 inet manual` + `iface eth0:1 inet static`，地址在、`pgrep -c dhclient` = 0、
cron 里 `net_guard.sh` 在跑 ⇒ **网络改动本身是对的，且能在重启后自动生效**。

但掉线没停。`journalctl --list-boots` 显示上一次 boot（12:20:42–12:46:18）仍带着旧的
`inet dhcp` 在跑（ifupdown 只在开机读配置），依旧**没有任何 shutdown 序列**。
这一次掉线发生在启动 HDMI 扫描输出（1280×720×60、32bpp、约 220 MB/s 连续读 DDR）之后几分钟，
所以「`reboot 3;` 是硬复位元凶」这个假设被削弱，供电/硬件嫌疑上升。
下一步定责办法写在 `docs/board_environment.md`：**不跑任何 overlay 空载挂 10 分钟**，
空载也掉就基本可以定成供电。

#### 2) HDMI 全白：机器判据全过，画面是白的

复现到的状态：`frames` 稳定 60 帧/s 增长、`under=0`、帧缓存物理地址 `0x38100000/0x38500000`、
`pack=0x0D001400`（width/stride=1280、en=1，解出来全对）—— **机器侧没有任何一处报错**，
屏上却是纯白。这正是「BRAM_OK 不能替代 VISUAL_OK」的又一个实例。

抓到一条实证：`bufs[*].cacheable = True`。PYNQ 的 `allocate()` 默认可缓存，
CPU 写完的像素只在 cache 里，PL 走 S_AXI_HP0 直接读 DDR ⇒ 读到旧内容。
已在 `tools/hdmi_video.py` 的 `show()` 里加 `Buffer.flush()`（切基址之前刷）。
脚本已推到板上，但**屏上变了没有还没看到**，按纪律记「未验」。
下一步的判定脚本是「红→绿→蓝→白→黑」五张带大字色卡循环，屏上读到哪几个字就能一次分清
「缓存问题已修」和「PL 扫出来的仍是常数」。

#### 3) 场景分层：把测试集从「只有最难的画面」改成两层

原先上板只用 `data/samples/incidents/` 的 12 段 —— 那是 `find_incidents.py` 按
「导播切回放」挑出来的，**只代表最难的一类画面**。新工具 `tools/split_scenes.py` 按
**人员聚集度**切成两类：

    含 50% 检出入的最窄 x 区间宽度 w（归一化）   w 小 = 人挤在一起 = 争抢
                                              w 大 = 阵型铺开 = 普通

第一版判据被两种画面骗了，靠一张接触表当场抓出来：

- **首发名单图**：人脸图标挤成一团，`w` 极小，"争抢度"爆表；
- **球员通道特写**：人也挤在一起，同上。

于是补两道硬门槛：**草皮占比 ≥ 0.35**（排除名单图/通道/看台）、**人物框中位高度 ≤ 0.30 画面高**
（排除特写）。重扫 240 个探针点，剔除 `not_pitch=97` / `closeup=39` / `too_few_players=1`，
剩下 103 个有效点，分出 5 段争抢 + 5 段普通（各 10 s、1280×720）。

产物：`data/samples/scenes/MANIFEST.csv`（入库，含时间/聚集度/哈希）+ 10 个 mp4（不入库）。

过程中还修了一个**名不副实**的数：`n_probe_frames` 一开始全是 1 —— 探针间隔 10 s、窗口 ±5 s，
只框住中心探针自己，「中位数」是假的。改成 ±(half + step/2) 后变成 1~3（仍为 1 的段是真只有
一个有效探针，没有粉饰）。

#### 4) 分层测试表的结论

`tools/check_scene_suite.py` 在主机侧逐段统计（640@0.25）：

| 层 | 段数 | 球检出率 | 聚集度 | 每帧人数 | 单帧推理 |
|---|---|---|---|---|---|
| 争抢 | 5 | **11.6%** | 0.171 | 15.4 | 42.4 ms |
| 普通 | 5 | **19.6%** | 0.294 | 17.5 | 32.5 ms |

方向与判据一致（争抢画面球被挡得更狠），规模小（各 5 段），**不作准确率用**。
> ★ 这一条后来被扩样本推翻，见 §7.10 —— 保留原文以便看出结论是怎么变的。
另有一个值得写进报告的硬事实：**即便是普通场景，球也只有约 1/5 的帧检得到** ——
这就是球门控（`ball_policy` / `ball_gate`）必须存在的原因，不能靠「检测器每帧都能看见球」。

#### 5) 全流程 overlay：一次加载，小屏大屏同时可用

新出片脚本 `build/scripts/bd_flow_system.tcl`，把 `lcd_probe` 与 `hdmi_video` 合并进
`flow_system.bit`：两个 `clk_wiz`（LCD 走外部 50 MHz、HDMI 走 PS FCLK_CLK0，输入源不同，
不违反「一引脚一个 IBUF」）、`axi_smc`(10 MI) + `axi_cc`(FCLK→pclk) + `hdmi_smc` + `hp_smc`。

地址只动了两条：`gpio_id` `0x41200000→0x41270000`、`gpio_t` `0x41210000→0x41280000`
（`0x4120/0x4121` 是 `hdmi_video.py` 写死的，不能动）。`status_display` / `lcd_ui` /
`hdmi_video` / `bram_fb` 一个地址都不用改；`lcd_probe.py` 本来就按 IP 名解地址；
`check_lcd4_board.py` 已改成同样按 IP 名解（否则会读到 HDMI 的寄存器上）。

第一次出片挂在 `create_bd_design` → `couldn't read file "init.tcl": No error` ——
仓库里记过的老坑，但这次连绝对路径 `source` init.tcl 都读不到，说明「预加载」不够；
改成**建图期间把 cwd 切到 `$ipintegrator`** 才过。细节与出片记录见 `docs/flow_overlay.md`。

### 7.10 分层样本扩到 8+8 —— 上一条结论被自己推翻（2026-10-30 16:2x）

§7.9 第 4) 条记的是「争抢 11.6% vs 普通 19.6%，方向与判据一致」。样本扩到各 8 段
（`--reuse` 复用 `_probe.json`，不重扫全场）后，同一条链路给出 **15.8% vs 20.2%**：

| 层 | 段数 | 球检出率 | 聚集度 | 每帧人数 |
|---|---|---|---|---|
| 争抢 | 5（旧） | 11.6% | 0.171 | 15.4 |
| 普通 | 5（旧） | 19.6% | 0.294 | 17.5 |
| 争抢 | **8（新）** | **15.8%** | 0.159 | 14.4 |
| 普通 | **8（新）** | **20.2%** | 0.298 | 17.0 |

做了置换检验（各 8 个片段值，20 万次重排）：

- 球检出率差 4.5 个百分点，**p = 0.61 —— 分不开**；
- 聚集度差 0.139，p = 0.0002，两类完全不重叠（争抢最大 0.206 < 普通最小 0.274）。

**所以要说清两件事**：

1. 聚集度分得开是**构造性质**（标签就是按聚集度定义的），不能拿来当「争抢更难」的证据；
2. 「争抢画面球更难检出」**在 8+8 下不成立**，5+5 时的 8 个百分点是小样本巧合。
   逐段球检出率跨 0%–52%，说明它主要受「这一段球有没有被人挡住」支配，而不是阵型宽窄。

保留这条推翻记录而不是删掉旧数字：报告里同时写「先得到的数」和「后来推翻它的数」，
比只写站得住的数更能说明测试集是怎么被用起来的。已同步改 `report/design_report.md` §2.11、
`docs/system_status.md`、`data/samples/scenes/README.md`、`tools/README.md`。

配套新增 `tools/scene_sheet.py`：把 16 段各取中点帧拼成一张对照图（上排争抢、下排普通），
`SCENE_SHEET_OK ... cells=16 size=1600x900`。`human_ok` 那一列只能靠人看这张图填。

另：板子从 15:5x 起彻底不在网里。**16:12 对 `192.168.2.0/24` 全段扫了 9090/22/80，
254 个地址一个不通** ⇒ 不是「地址变了」（A 类），是整机没上电（B 类）。
按 AGENTS.md 不做任何硬件验证；改成挂 `tools/run_board_acceptance.py --wait 2700`
在后台等它上线，通了就自动 deploy → overlay 自证 → HDMI 五色卡 → 分层片段，
每步落盘到 `data/results/board_acceptance.json`。**尚未跑出任何板端结论。**
