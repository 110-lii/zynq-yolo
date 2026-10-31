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

### 7.11 板子回来了一趟：overlay 自证过、分层跑表只跑完 1/16（2026-10-30 18:1x–19:0x）

**1. 合并 overlay 的机器判据在板上过了**（`flow_overlay.py --selftest`）：

    FLOW_OVERLAY_LCD  OK mode=PYNQ bad=0/16 tick 132155348→138832280
    FLOW_OVERLAY_HDMI OK frames 564→1056 (+492) under=0 shown=21 bufs=0x38100000,0x38500000
    FLOW_OVERLAY_OK（机器判据；屏上颜色/版面仍要人眼看）

这是 `flow_system.bit` 第一次上板。等级是 `BRAM_OK`，**不是** `VISUAL_OK` —— 屏上还没人看过。
（`board_acceptance.json` 里这一步当时记成 `FAIL`：判据写成了全等比较，而板上打印带括号注释。
已改成前缀匹配。板子本身没问题，是判据的错。）

**2. 部署的教训：60 MB 一晚上传不完，改成按 sha256 增量**
板子 5–15 分钟掉一次，全量部署十几分钟必被截断。现在先一次往返取板端所有文件的
sha256，只补不一样的；这一轮 42 个文件里只传了 6 个，20 秒完事。
工具闭包也不再手写：`run_board_acceptance.py` 用 AST 把 `board_pipeline` 的 import
递归收出来（15 个模块）。之前漏了 `team_assign`，16 段全部 `ModuleNotFoundError` 白跑一轮。

**3. 分层片段上板：先纠正一个误读**
早期每段都报 `det=8`。那是 `--max-det` 的默认值 —— **检测调用次数上限**，不是人数。
改成 `--max-det 0`（不封顶）之后才第一次拿到有意义的量。

**4. 跑完 1/16 就卡死，但这 1 段已经说明了问题**（c01 争抢段，250 帧 / 25 次检测）：

| 阶段 | 数量 |
|---|---|
| 模型原始框 | 274（≈11 / 检测帧） |
| 过草皮门限（框底 7×7 绿色 ≥40%） | 222（81%） |
| 交给下游的跟踪框 | **0.12 / 帧** |
| 球可见率 | 12% |
| 单帧推理 | 961.8 ms（主机 26–44 ms） |

**归因清楚了：不是模型看不见人，是机位有效性判定把坐标掐了**。
16 段跑到 15/16（8 争抢 + 7 普通；n08 因板子崩溃没跑）。普通那 7 段带着新计数器：

    原始框/检测帧 20.4 → 过草皮门限 16.5 → 过 score/尺寸门限 14.9 个目标/帧 → 输出 0.28

门限后 14.9 和主机同段的 17.4 人是同一量级（86%），说明**检测链路本身对得上**。
「输出只剩 0.28」是 `camera_state.assess()` 的设计规则：
没给 `--calib` 就没有单应矩阵，`n_field` 恒 0，于是

    n_det >= 8 且 valid_frac < 0.10  ->  INVALID_VIEW (calibration_mismatch)  ->  rows = []

板上一帧十几个人、又没有标定 ⇒ 每帧都判「标定对不上」⇒ 坐标全清空。
所以要让板端出真正的分层数字，必须带 `--calib`（且标定要对得上这批片段用的机位）。
这一步被板子第三次崩溃打断，**未完成**。

**5. 第三种板子状态：TCP 能连、HTTP 不应答**
第二段开始后 `192.168.2.99:9090` 的 connect 依旧成功，但 `GET /login` 和内核都不回应，
连一条 `print()` 都要等两分钟以上。所以 `board_up()` 只能用来快速排除，不能证明可用。
已给 `scene_suite_board.py` 加 `--clip-timeout`（默认 300 s，实测单段 ~85 s），
超时按板子卡死处理、立刻中止并记 `SCENE_BOARD_FAIL board_wedged`。
观察写进 `docs/board_environment.md` §「第三种状态」。

**6. 剩下的**
- 其余 15 段：板子恢复后 `py tools/scene_suite_board.py --resume --no-push --max-frames 250 --every 10`；
- HDMI 五色卡（`check_hdmi_cards.py`）要人看屏报 `STEP1-RED`…`STEP5-BLACK`；
- `MANIFEST.csv` 的 `human_ok` 列还空着，等 16 段对照图的人眼判断。

### 7.12 晚上续（2026-10-30 20:2x–21:0x）—— HDMI 不出画面被切成两段，归因从 cache 改到配置寄存器

**1. 先把「停在黑卡上」这个自造歧义修掉**
五色卡跑完屏上是黑的，人过去看只会说「HDMI 没画面」，和真故障分不开。
`check_hdmi_cards.py` 加了收尾横幅（白底黑字 `zynq-yolo HDMI 5/5 OK` + 上下彩带），
默认停 30 s 并一直扫着（`HDMI_CARDS_HOLD`）。同一批还修了 `run_board_acceptance.py`
的 `--wait 0`：`while time.time()-t0 < 0` 一次都不进，直接 `SKIP board_offline`，
一个晚上连着两次把在线板子判成离线。

**2. 人眼判据的实际收获（一半过了一半没过）**
- **LCD 过**：用户报「LCD 是彩条」，正是 `flow_overlay.lcd_selftest()` 写的 8 条 100 px 竖彩块
  ⇒ `LCD_VISUAL_OK` 记上；
- **HDMI 没过**：五色卡 + 收尾横幅机器侧 `frames 6105→7916 under=0 elapsed=131s`，
  用户报「始终没画面」。

**3. 切链：用不碰 DDR 的 RTL 彩条把故障挤出显示链路**
载 `hdmi_testpattern.bit`（`pixel_data` 完全由 `xpos/ypos` 组合逻辑算出，没有存储器）
→ **用户报「现在可以，彩条」**。同时读它的调试寄存器：`de=1`、`locked` 20 次采样 16 次为 1
（其余为跨域采样抖动）、`xpos/ypos` 连续变化。
⇒ TMDS、像素时钟、引脚、显示器、线**全是好的**，故障锁定在「DDR 帧缓存 → `fb_scanout`」段。
这条结论把之前「整屏纯白 ⇒ `allocate()` 缓存没刷」的归因降级为**待证**。

**4. 机器判据：DDR 里的像素是对的，读不回来的是配置寄存器**
`build/fb_diag.py` 写完一屏纯色后，用 `/dev/mem`（`MMIO`，绕过缓存）把同一物理地址读回：

    DIAG_RB RED   addr=0x38100000 expect=0xFF0000 match=16/16
    DIAG_RB GREEN addr=0x38500000 expect=0x00FF00 match=16/16
    DIAG_SHOW RED addr_reg=0x02120000 pack=0x00000000

- `match=16/16` ⇒ **DRAM 里确实是我们的像素，`flush()` 不是问题**（这条归因作废）；
- `pack` 读回 **0**（写的是 `0x0D001400`），`addr` 读回的不是 `0x38100000`，
  而且两次读的差值在独立两轮里完全一样（`0x1840000`/6 s）—— 一个「写不进去、自己还在涨」的寄存器，
  最可能是**读到了别的东西**。对照证据：`gpio_hdmi_st`（0x41210000）读回的 `frames` 是
  规规矩矩 60/s、`under=0`，同一条 SMC、同一个 pclk 域，所以不是「读通路全坏」。

**5. 另一条被推翻的判据：`under=0` 不能证明数据对**
`st_under` 是按「每行开始前该行缓存是否就绪」计的，读回全零时它照样是 0。
所以「`frames` 在涨 + `under=0`」只能证明 AXI 握手与行推进在跑，**不构成画面证据**。
这正是「`BRAM_OK` 不能替代 `VISUAL_OK`」的第二个角度：连 `BRAM_OK` 里最像样的那个数，
在数据错的时候也不会报警。

**6. 下一步（已排好的探针）**
`build/hdmi_cfg_probe.py`：写 `0xDEADBEEF` 后立刻读回，并用两个已知好用的 GPIO
（`0x41220000` LCD cfg、`0x41280000` 帧时钟计数）做对照。判据：
- 若对 `0x41200000` 写什么都读不回、对照组正常 ⇒ 问题在地址/读通路，与 cache 无关；
- 若读得回 ⇒ 配置其实进了 PL，回到 `fb_scanout` 内部找（下一步按 014 的老办法，
  用 GPIO 把 `{cfg_en, line_ready, in_range, pixel_data}` 拉出来读）。

### 7.13 两个独立缺陷：先修「每一行都在读第 0 条行缓存」，再修「配置寄存器根本进不去」

夜里这一段把 7.12 留下的两个待证项都做实了，结论是**两处独立故障叠在一起**——
只修一处，画面照样不对。

**1. 人眼二分：整屏一个颜色对、竖条对，横条和文字全丢**
用户先后报了三次画面：「全绿、没有字」「左红右蓝有白条、没有黄条和字」。
把三次现象对到 `fb_scanout` 的地址逻辑上，只剩一个解释：**`disp_line` 卡在 0**，
每一行都去读第 0 条行缓存 —— 于是「第一行是什么」决定整屏：
纯色卡 ⇒ 整屏纯色（看着像成功）；首行有竖条 ⇒ 整屏竖条；首行的横条/文字 ⇒ 全丢。

根因在行推进条件的取值域：
```verilog
// 旧写法
end else if (de && (local_x == width_use - 1))
```
而 `local_x = xpos - 2`，真实 `video_driver` 在 `de` 有效的 1280 拍里给的是
`pixel_xpos = 0…1279`（`data_req` 比 `video_en` 早一拍，`pixel_xpos` 只在 `data_req`
时更新），所以 `local_x` 最大只到 **1277**，`== 1279` **永远不成立**。
顺带一个非常坏的副作用：`ahead = fetch_line - disp_line` 涨到 4 以上就不再取数，
`ready[0]` 恒为 1 ⇒ **`st_under` 永远是 0**。所有机器判据全绿，只有人能发现。

改成数「本行发过多少拍 de」：
```verilog
end else if (de) begin
    if (px_adv + 11'd1 >= width_use) begin px_adv <= 0; disp_line <= disp_line + 1; end
    else                                   px_adv <= px_adv + 1;
end
```

**2. 仿真为什么没抓到：TB 的 `xpos` 是手搓的**
`sim/tb/tb_fb_scanout.v` 原来喂 `xpos <= px + 2`（注释还写着「与 video_driver 一致」），
刚好处处把 `local_x` 顶到 `width_use-1`，把上面那个边角完整绕过去。
改成真实取值域（`xpos = 0…W-1`，期望像素取 `src = max(xpos-2,0)`），并补一条
**行推进断言**（一行 de 走完，`disp_line` 必须 +1）。反向验证（把旧条件贴回去再跑）：

    DISP_LINE line=0 disp_line=0 exp=1
    MISMATCH line=1 xpos=0 src=0 got=000000 exp=010000
    CHECKED=2048 ERRS=1824 UNDER=0 FRAMES=4

注意 `ERRS=1824` 而 `UNDER=0` —— **欠载计数对这个缺陷完全免疫**，这条和 7.12 第 5 条
是同一个教训的两面。恢复修复后 4 帧全对。

**3. 配置寄存器：不是「没生效」，是**两个地址都解到了同一个从设备**

探针一（连续读三次 `st` 与 `cfg[0]`）：

    T0 cfg[0]=0x00020000 st=0x00020000 frames=2
    T1 cfg[0]=0x003E0000 st=0x003E0000 frames=62
    T2 cfg[0]=0x007A0000 st=0x007A0000 frames=122

`frames` 以 60/s 稳定增长 ⇒ **像素域、vs、`hdmi_smc`、`axi_cc` 全是活的**（7.12 里
「是不是复位没释放」的怀疑可以划掉）；但 `cfg[0]` 读回来的就是 `{frames, under}` 本身。

探针二（写魔数 + 四个分支对照）：

    BASE  hdmi_cfg r0=0x58C10000  hdmi_st r0=0x58C10000  lcd_cfg r0=0x00000000
          gpio_keys r0=0x00000003  gpio_t  r0=0x004CB250
    AFTER write 0xDEADBEEF to hdmi_cfg r0 → hdmi_cfg 仍是 0x58C10000
    CTRL  write 0xCAFEBABE to lcd_cfg    → lcd_cfg 读回 0xCAFEBABE

同一条外层总线上的 LCD 配置、按键、帧计数器全都正常 ⇒ 外层 `axi_smc` 与 PS 侧没问题，
**坏在 `0x41200000` 这一支**：写进不去、读回的是状态 GPIO。屏上全黑（人眼确认），
与 `cfg_en` 从未置起来完全一致。

**4. 一个被证伪的假设（记下来，省得下次再试）**
`bd_hdmi_video.tcl`（能读回）与 `bd_flow_system.tcl`（读不回）里是同一个 2 主口
SmartConnect，差别只有 flow_system 多挂了
`__experimental_features__ {disable_low_area_mode 1}`。摘掉后重出片（WNS +3.017 ns，
0 违例）再上板：**`addr_ok=0 pack_ok=0`，屏幕仍然全黑** ⇒ 与这个实验性开关无关。
顺便把内层 SMC 生成的段表挖出来看，`SEG000=0x41200000 / SEG001=0x41210000`、路由位也不同，
**看起来是对的** —— 所以「译码塌陷」只是现象描述，不是寄存器配置能解释的。

**5. 改法：不再赌这条嵌套总线**
`hdmi_video_top.v` 里本来就有 cfg 的 2 拍同步器（注释写着「cfg_* 来自 PS 侧 AXI GPIO
（83.33 MHz），本模块跑 pclk」），XDC 也留了跨域说明的位置。那就顺着这个设计意图走：

- `gpio_hdmi_cfg`（**写侧**，必须可靠）挪到外层 `axi_smc` 的 M09，跑 FCLK 域；
- `gpio_hdmi_st`（只读，实测读通路正常）留在 pclk 域，内层 SMC 降成单主口（M10）；
- `flow_system.xdc` 补上 `clk_fpga_0` 与 `clk_out{1,2}_system_clk_wiz_hdmi_*` 的异步声明
  （两个 HDMI 时钟必须**同一组**：serializer 在 pclk_x5 上采样 pclk 数据，那是真同步路径）。

判据顺序不变：先 `HDMI_CARDS_CFG addr_ok=1 pack_ok=1`，再谈屏上的字。

### 7.14 结构改完、片子出了，人眼这一步被板子卡住（2026-10-30 22:2x）

按 7.13 第 5 条的改法出了新片：

| 项 | 值 |
|---|---|
| `flow_system.bit` | 4,045,674 B，22:24 生成 |
| WNS / TNS | **+3.654 ns / 0.000**（0 个失败端点，97,777 端点） |
| 地址表 | `gpio_hdmi_cfg` 0x41200000、`gpio_hdmi_st` 0x41210000（未变） |
| 结构 | `axi_smc` 11 主口：M09 → cfg（FCLK）；M10 → `axi_cc` → `hdmi_smc`(1 主口) → st（pclk） |

`deploy` 成功（只重传了 bit 和 hwh 两个，其余 sha 一致），**紧接着 `hdmi` 步骤跑到一半
连接被重置**（`WinError 10054`），随后 TCP 都不通了 —— 板子掉了。
按 AGENTS.md：**板子离线不做任何硬件验证，也不声称「已验证」**。

所以现在的状态是干净的「待验」：
- 机器侧已过：仿真（含反向验证）、时序收敛、部署 sha 一致；
- **人眼侧未过**：`HDMI_CARDS_CFG addr_ok/pack_ok` 还没机会跑，五色卡和横幅还没看过。

上电后由 `build/hdmi_watch.py` 自动接手（它每 15 s 探一次内核，活着就
`deploy,hdmi` 一路跑完并停在横幅上）。看到 STEP1-RED … STEP5-BLACK 和收尾横幅，
才记 `HDMI_VISUAL_OK`；看不到就继续查，不写「应该可以」。

顺带记两条当晚同一批的结论，免得以后重算：
- 分层测试集**补齐 16/16**：争抢段 8/8（`build/_contest_rows.json`）+ 普通段 n08
  （补进 `data/results/scene_suite_board.json`；此前这份文件被一次新跑冲成 1 行，
  已从提交里恢复再并入 n08 —— 落盘文件不能手改，但**能重算的合并要重算**）。
  两类合计（每检测帧）：原始框 15.44 / 20.05，过草皮门限 12.30 / 16.25，
  输出 `players` 0.45 / 0.24，球可见率 5.5% / 22.0%，单帧推理 988 / 965 ms。
- **报告里旧的「过 score/尺寸门限 14.9」在任何落盘数据里都查不到出处，已撤掉并加注**；
  16 段补齐后仍然复现不出来，所以不是「样本不够」的问题。

### 7.15 第三处缺陷：HDMI 读主机**根本没有地址空间**（2026-10-30 23:0x–23:4x）

**1. 先证明「不是网表」**
把两片已布线 checkpoint（`flow_system` vs 能出画面的 `hdmi_video`）用
`build/scripts/cmp_hdmi_dcp.tcl` / `cmp_hdmi_dcp2.tcl` 逐原语比对：

    flow_system : OSERDESE2×8  OBUFDS×4   ODDR×(仅 LCD 的 1 个)  OBUF×30(LCD)
    hdmi_video  : OSERDESE2×8  OBUFDS×4   ODDR×0                  OBUF×0
    serializer_{r,g,b,clk}.pclk_x5 / pclk 负载：两边完全一致

HDMI 段的实现结果**逐原语相同**，所以差异不在综合/实现，也不在 RTL。

**2. 在构建日志里找到 41-1356**
`flow_system_build_20060.backup.log`（= 板上那片 22:24 的 bit）里有 3 次：

    CRITICAL WARNING: [BD 41-1356] Slave segment
      </processing_system7_0/S_AXI_HP0/HP0_DDR_LOWOCM>
      is not assigned into address space </hdmi_video_0/m_axi>

`bd_flow_system.tcl` 把地址全写成显式 `assign_bd_address`，唯独漏了 HP0 的 DDR 段；
`bd_hdmi_video.tcl` 末尾那句**不带参数**的 `assign_bd_address`（自动分配剩余的）正好补上了它
—— 那一版能出画面，原因就在这里。

后果极隐蔽：读请求在互连里解不到从设备、回 DECERR；`fb_scanout` 照样拿到应答，
`frames` 60/s、`under=0`、配置寄存器写读闭环通过，**任何一个机器判据都不报警**，
只有屏上是一个不随帧缓存变化的颜色。

**3. 改法 + 构建期断言**
显式分配 `HP0_DDR_LOWOCM`，并打印地址段、为空直接判构建失败：

    HDMI_M_AXI_SEG /hdmi_video_0/m_axi/SEG_processing_system7_0_HP0_DDR_LOWOCM \
        offset=0x00000000 range=0x40000000

21:5x/22:25 三次构建各 3 次 41-1356 → 新构建 **0 次**。新片 WNS **+3.553 ns**、
WHS +0.019、0 违例（98,311 端点）。

**4. 同批加的两个正交探针（RTL）**
- `st_sum`（`hdmi_video_top` → `gpio_hdmi_st` 通道 2，地址 +0x8）：本帧显示用到的像素
  逐个累加（模 2^32），vs 上升沿锁存 ⇒ 「PL 从 DDR 到底读到了什么」变成机器可判的数；
- `cfg_pack[28]` = 内置彩条：送 TMDS 的是本模块用 de 拍数算出的图案，**不碰 DDR**；
  第 0 段故意做成按行灰阶，用来暴露「每行读第 0 行」这类缺陷。

**5. 上板结果（2026-10-30 23:30–23:35）**
`build/hdmi_ddr_diag.py`：

    DIAG_SUM C1/CR1/RED/GREEN/BLUE/WHITE  —— 与期望值都不是逐位相等，但换颜色就跟着变
    DIAG_SUM_DISTINCT 4 of 4

把累加和反解成「等效累加了几个像素」（`sum = N·word mod 2^32`）：

| 整屏颜色 | 反解 N | 期望 N |
|---|---|---|
| r=1 | 914,087 | 921,600 |
| 红 | 914,246 | 921,600 |
| 绿 | 914,177 | 921,600 |
| 蓝 | 914,335 | 921,600 |
| 白 | 914,262 | 921,600 |

五个独立颜色各自反解出的 N **彼此一致**（921,600 的 99.2%）⇒
**PL 从 DDR 拿到的就是主机写进去的像素值**，DDR 段通了。
差的 0.8%（~7,400 像素）与逐帧 ±3 的抖动说明**累加窗口本身不干净**（vs 沿附近有边界效应），
这一条单独记，不能拿它当逐位判据。

**6. 人眼：彩条（不碰 DDR）通过，帧缓存那一路暴露出行映射问题**
- 彩条：用户报「看到了彩条，最左边是灰阶渐变」⇒ **TMDS、像素时钟、引脚、显示器、
  以及本模块自己的行计数全部正确**。最右重复两段是彩条索引只取了 3 位（1024 px 之后绕回），
  属图案写法，不是缺陷。
- 切回帧缓存：本该是「左上 128×128 白块」，用户报「左边白、其余黑、白里有黑横条、还在闪」。
  结合第 5 条的 N 已证明数据正确 ⇒ 问题在 **`fb_scanout` 的行缓存/行号映射**，
  不在数据、不在 TMDS。`st_under=0` 在这种情况下依然是 0，再次说明它不能当判据。

**7. 中断点**
`build/hdmi_geom_probe.py`（八条横带 40 s + 八条竖带 40 s + 三点标记 90 s，
外加静止画面累加和稳定性 6 连读）已写好，**板子在 23:39 又掉线**（9090/22 端口全不通），
按 AGENTS.md：不做硬件验证、不声称已验证，等上电后重跑。

`HDMI_VISUAL_OK` 仍不记：彩条是「不碰 DDR 的旁路」，帧缓存这一屏人眼还没通过。

---

## 7.16 2026-10-31：板子离线，改用仿真把「行映射」这件事查到底

### 7.16.1 先说不做什么

板子从 23:39 起 9090/22 全超时。按 AGENTS.md，**没有上板就不做任何硬件验证、
不记「已验证」**。这段全部是离线工作：仿真、RTL 修复、期望值口径修正。

### 7.16.2 把板上那组数反解到底（它是机器证据，不是传闻）

BLOCK 那幅图只有两种像素值（0 和 0xFFFFFF），所以累加和可以**逐位反解**出
屏幕上到底出现了几个白像素 —— `M = sum · 0xFFFFFF⁻¹ mod 2³²`：

    BLOCK  got = 0x31FE94CE  ⇒  M = 92,978 个白像素
    正确值（128×128 白块，屏幕口径）      = 16,640 个

92,978 / 720 行 ≈ 129 列/行 ⇒ **屏幕上每一行的左边 128 列都是白的**，
和用户报的「左边白、其余黑」逐字对上。结论收敛成一句：
不是「少数几行错」，是**每一行都读到了靠前的行**。

（这也解释了为什么纯色累加和一直「看起来对」：整屏同色时，任何行映射都给出同一个和。）

### 7.16.3 三个 TB，三个层次

| TB | 覆盖什么 | 之前为什么测不出 |
|---|---|---|
| `tb_fb_scanout` | 64×8，握手 / stride / 像素比对 | 4 拍消隐、零延迟从机 |
| `tb_fb_scanout_frame` | **1280×720 + 370 拍行消隐 + 带延迟 AXI** | 新增 |
| `tb_hdmi_scanout_sys` | **真的 `video_driver`** + 与板上同口径的累加和 | 新增，手搓时序的 TB 都绕开了 `data_req`/`xpos`/`video_en` 互差一拍 |

新的两个都进了 `sim/scripts/run_sim.py` 的 `CASES`，判据是令牌：

    SIM_PASS 11/11 cases

`tb_hdmi_scanout_sys` 逐像素比对 **3,686,400 个像素**（4 帧）零错，
并且帧累加和与主机算式**逐位相等** —— 这是**仿真与实物共用同一把尺子**。

### 7.16.4 仿真抓到的真缺陷（都不是猜的，都有复现路径）

**① 每行最后一列永远是黑**
`in_range = cfg_en && de && ...`。显示侧最后一个像素（`video_de=1`）对应的
`data_req` 已经是 0，于是每行最右一列被门控成黑。
小 TB 是 64 宽、像素比对恰好绕开；系统级 TB 每行抓到**恰好 1 个**错像素（col=1279）。
改法：`in_range` 只保留 `cfg_en && local_x < width_use && line_ready`，
消隐期由 `video_rgb = video_de ? ... : 0` 负责。

**② `ahead` 无符号回绕 ⇒ 取数永久冻结**
`wire [10:0] ahead = fetch_line - disp_line;` + `ahead <= 3`。
取数一旦落后于显示，`ahead` 回绕成 2000 多，条件永远不成立 ⇒ 取数冻结，
屏幕上是那 4 条旧行缓存反复显示，而 `ready[]` 恒 1、`under` 恒 0 —— **所有机器判据都通过**。
复现：把系统级 TB 的从机放慢到 `+LAT=40 +GAP=40`（一行 20 个突发 × 145 拍 = 2900 拍
> 1650 拍/行），旧代码当场冻结在 `fetch_line=8`。
改法：`ahead[12]` 显式判符号位，落后时**放行让它追**，并把落后行数计入诊断。
（顺带踩到自己挖的坑：加 `ahead != 0` 守卫会把帧首 `fetch_line == cur_line == 0`
锁死，整帧一行都取不出来 —— 已删。）

**③ `st_sum` 的口径和屏幕不是同一批像素**
原来累加的是 `data_req` 拍上的 `pixel_data`，比显示早一拍，采到的是
`0,0,0,0,1,…,1276`；屏幕上显示的却是 `0,0,0,1,…,1277`。
于是主机按屏幕口径算的期望值**永远差一点** —— 上板那 0.8% 里至少有一部分是这么来的，
不是「PL 读错了」。
改法：累加改到 **`video_de` + `video_rgb`**（真正送进 TMDS 的那批像素），
主机侧口径 = `[0,0,0] + range(1,1278)`，两边现在逐位相等。

**④ 显示行号改由 `ypos` 决定**
`local_x == width_use-1` 和「数 de 拍」两种写法都栽过：前者在真机上恒不成立（卡第 0 行），
后者数量对但相位要靠推。`pixel_ypos` 是 `video_driver` 自己的场计数（1 起算），
`cur_line = ypos-1` **由构造保证**是屏幕行号，不依赖任何脉冲计数。
两端都夹住：`ypos==0`（场消隐）或越界一律当 0 —— 否则 `ypos-1` 下溢成大数，
`ahead` 判成「落后」而全速跑飞（这个在仿真里当场复现过一次）。

### 7.16.5 出片

`bd_flow_system.tcl` 未改地址表，只带 RTL 变更重出一版 `flow_system.bit`；
日志 `build/flow_system_build_31002.log`。41-1356 仍为 0 次。

### 7.16.6 上板待办（板子回来第一件事）

`build/hdmi_geom_probe.py` 已按今天的结论改过（读 `0x41290000`、期望值统一成屏幕口径）：

    1. BLOCK / VRAMP —— 两个已知会说话的图案，先拿累加和对表
    2. 八条横带 40 s —— 人眼核「条数是不是 8、顺序对不对、厚度是否均匀」
    3. 八条竖带 40 s —— 单独看列
    4. 静止画面累加和 6 连读 —— 量化帧间漂移
    5. 三个角标 90 s —— 人眼几何锚点

`0x41290000` 三个数是这次的关键判据：

    [10:0]  under_lines   行首没数据（正确 0）
    [21:11] behind_lines  取数落后于显示（正确 0）
    [31:22] last_disp     最后显示到的行号（正确 719）

  · behind > 0            ⇒ 取数被显示甩开，屏幕上是旧行缓存；
  · 两者都 0 且行号 719   ⇒ 显示与取数都在正常推进 ⇒ 错只能在地址（stride/base）
                            或行缓存下标上。

`HDMI_VISUAL_OK` **仍不记**：这一版没有任何上板证据。

### 7.17 补记：两片比特流的 sha256 对不上，但线上的那一片确实带过「数 de 拍」修复

担心「板上跑的是没修行推进的旧片」，用 git 里的比特流直接比对：

    f8985b2 (22:32 提交，22:24 生成)  87b021dcd21fdb28
    cb9c262 (23:40 提交，23:29 部署)  33c52be822cbbcd2   ← 板上跑的就是这片
    d183eca (10-31 00:45)             5f0d37b36dded4f1

三片互不相同 ⇒ 23:30 那轮诊断用的**确实是 HP0 修复之后新出的片**，
该片取自 22:32 之后的工作区，**包含「行推进按 de 拍数计」**。
也就是说「每行都读到第 0 行」不是在旧片上发生的，这条解释不成立。

**BLOCK 的数进一步支持「每行都读第 0 行」**：屏幕口径下每行 130 个白像素 × 720 行
= 93,600，与板上反解出的 92,978 只差 622 个（0.66%），和纯色那组 0.82% 的缺口同量级。
**但 VRAMP 对不上**：若所有行都读第 0 行，VRAMP 的 r 全是 0、累加和应为 0，
实测是 0x3ACB0000。这一条暂时无法解释，与上面的结论并列记录，不上任何结论。

下一轮上板先用 §7.16.6 的探针；新加的「行号写进像素值」那条会在**一个数**里
给出 Σ(显示出来的源行号)：正确 258,840，全读第 0 行是 0，打滑则差额直接折算成行数。

### 7.18 板子「又掉线」的真相：本机网线掉了

10-31 00:5x 再探，仍是 9090/22 全超时。这次先查本机，而不是板子：

    netsh interface show interface
      以太网   Realtek PCIe GbE Family Controller   **已断开连接**
    route print -4
      活动路由里**没有 192.168.2.0/24**，只有 WLAN 192.168.5.5 与默认网关 192.168.5.1

也就是说探测包根本出不了 192.168.2 网段，超时与板子状态无关。
14:0x 之前一直是通的，中间唯一的差别是**这块网口的链路状态**。

已把这条判断固化进 `tools/run_board_acceptance.py::link_diag()`（UDP connect 查路由，
不发包），`wait_for_board` 开头打一行 `HOST_LINK_OK/WRONG/NONE`，
以后「板子离线」不会再被当成一个笼统的超时。

结论：**拍板之前先看本机 `以太网` 是不是「已断开连接」**，插回网线再跑探针。

### 7.19 单行/单列探针：把「哪个源行被显示了几次」量成一个整数

§7.18 之后板子仍离线，但 BLOCK（92,978 个白像素）与 VRAMP（`0x3ACB0000`）两个数
互相矛盾：前者支持「每一行都读靠前的行」，后者连「全读第 0 行」都对不上
（`0x3ACB0000 / 0x10000 = 15051`，而每行贡献 `1280·Σ(r&0xFF)` ⇒ 需 `1280·S ≡ 15051 (mod 65536)`，
左边恒被 256 整除、右边不是，**无解**）。既然解析推不出来，就改成直接量。

新探针：整幅图只有源行 r 是白的，屏幕上显示它几次，累加和里就有几组 1280 个白像素。

    sum = count × 1280 × 0x00FFFFFF  (mod 2^32)

`0x00FFFFFF` 是奇数 ⇒ 与 2^32 互素 ⇒ 乘它在模 2^32 下是**双射**，
`count = (sum · inv) / 1280` 精确可解，不需要任何关于行映射的假设。
列同理，除数是 720。正确值：任何源行都是 1；源列 0 是 3（屏幕列 0/1/2 同一源列）、
源列 1..1277 是 1、1278/1279 是 0。

先在仿真里把这条算式跑通（`tb_hdmi_scanout_sys` 新增 `+rowptr +ROW=n` / `+colptr +COL=n`）：

| 探针 | sum | 反解 count | 期望 |
|---|---|---|---|
| row 0 / 360 / 700 / 719 | `FFFFFB00` | 1 | 1 |
| col 0 | `6FFFF790` | 3 | 3 |
| col 1 / 1277 | `CFFFFD30` | 1 | 1 |
| col 1279 | `00000000` | 0 | 0 |

8/8 通过，逐像素比对同时是 0 错（`CHECKED=1843200 ERRS=0`），说明**同一批像素**
既满足逐点期望、也满足这个反解。

踩到的坑（值得记）：第一版按「`0x00FFFFFF ≡ -1`」把反解写成 `sum = -1280·count`，
行探针**凑巧**全对 —— 因为 `1280 × 2^24 = 5·2^32 ≡ 0`，行探针的差值正好被吃掉；
列探针立刻暴露：col 0 反解出 **1,887,438**。`0x00FFFFFF ≡ -1` 只在模 2^24 下成立。
改成对 `2^32` 做真正的模逆（TB 里穷举 count，主机侧 `pow(w, -1, 2**32)`）后全对。

上板脚本 `build/hdmi_geom_probe.py` 已加上 `DIAG_ROWPTR` / `DIAG_COLPTR`（9 行 + 9 列，
约 10 秒），并给出判定行：每个源行恰好 1 次 = 行映射正确；某行 720 次 = 行号卡死；
0 次 = 该行根本没显示。**本节全部结论来自仿真与纸面，板子上一个像素都还没看。**

### 7.20 不上板也能查的那部分：`flow_overlay.py --check-hwh`

板子等不到，就把「本可以离线发现」的错挡在烧板之前。新增
`py tools/flow_overlay.py --check-hwh --bit board/overlay/flow_system.bit`，
直接解析同名 `.hwh`，逐条核对：

1. **12 个从设备地址**与 `docs/flow_overlay.md` §3 的契约一致（含 `gpio_hdmi_dbg @ 0x41290000`）；
2. **GPIO 方向**：`gpio_hdmi_cfg` 两个通道都是输出、`gpio_hdmi_st` 两通道都是输入、
   `gpio_hdmi_dbg` 是 32 位纯输入 —— 方向配错会让读回**恒 0**，看起来像「PL 没算」；
3. **`HP0_DDR_LOWOCM` 真的分配给了 HDMI 读主机**（`MASTERBUSINTERFACE="m_axi"`，
   覆盖 0x00000000–0x3FFFFFFF）。10-30 夜里那条 `[BD 41-1356]` 漏分配，
   表现是互连 DECERR，而 `under=0`、`frames` 照涨 —— 只有屏上是一个不随内容变的颜色；
4. `st_sum` / `st_dbg` / `st_status` / `cfg_addr` / `cfg_pack` 是否真在网表里；
5. `.bit` 与 `.hwh` 同批（`.hwh` 比 `.bit` 新超过 60 s 就报 `batch:hwh_newer_than_bit`）。

当前结果：`FLOW_OVERLAY_HWH_OK`（顺带证实 `flow_system.bit` 00:38 那片带 `st_dbg`）。
负向测试：拿 `hdmi_video.bit` 跑同一命令 → `FLOW_OVERLAY_HWH_FAIL no_addr:... no_port:...`，
说明它不是永远打 OK 的摆设。

**它替代不了任何上板判据**，`HDMI_VISUAL_OK` 仍然不记。

### 7.21 上板前的最后一段准备（板子仍未上线）

- **网口的静态地址本来就是对的**：`netsh interface ipv4 show config name="以太网"` 显示
  `192.168.2.100/24`（另有 `192.168.1.102/24`），DHCP 关闭。所以**不需要重配任何东西**，
  缺的只是一根插回去的网线和板子供电 —— 这条比「板子又挂了」重要得多。
- **探针加了「配置到底进没进 PL」这一问**：两个魔数写-读闭环（`out.check_cfg()`）+ 把
  `cfg_pack` 拆成 stride/width/en 逐字段对期望值 + 基址是否指向刚写的那块缓冲。
  理由见 `tools/hdmi_video.py::check_cfg` 的注释：10-30 那次 `frames` 60/s、`under=0`
  全都正常，屏上却什么都没有，根因是配置根本没写进去 —— 所有「扫描在不在跑」的判据
  都不会报警。
- **离线体检**：`py tools/flow_overlay.py --check-hwh` → `FLOW_OVERLAY_HWH_OK`
  （12 个地址、GPIO 方向、HP0 段归属、诊断端口、bit/hwh 同批）。
- **提交包**：`py tools/make_submission.py` → `dist/zynq-yolo_submission_20261031_0132.zip`
  （665 文件 / 104.6 MB，`CHECK_REPO_OK` 6 PASS / 0 FAIL），已删除 10-30 的旧包。

上板那一天只需要两条命令：

    py build/hdmi_geom_probe.py --wait 300        # 先证配置、再证行/列映射
    py tools/flow_overlay.py --selftest           # 机器判据 + LCD 半边

人眼那一步（`HDMI_VISUAL_OK`）只能当场看，**在此之前不写「已验证」**。

### 7.22 上板（12:07–13:40）：取数吞吐修好了，接着暴露出 2 像素的水平错位

板子这次真的连上了（`HOST_LINK_OK src=192.168.2.100`），一天里做了两轮。

**第一轮（旧 bit，12:07）—— 把「13 条黑横条」钉死成吞吐问题**

`py build/hdmi_geom_probe.py` 全套跑完，机器判据给出的是一组**互相印证**的错：

| 探针 | 读数 | 应有 |
|---|---|---|
| `DIAG_CFG_WRITABLE / DECODE` | addr=OK pack=OK, 5120/1280/1 | 配置进得去 |
| `DIAG_ROWCODE` Σ 行号 | 181,248（偏差 −107.77 行） | 258,840 |
| `DIAG_ROWPTR` r=0/1/2/3/360 | 各 1 次 | 1 次 |
| `DIAG_ROWPTR` r=716…719 | **0 次** | 1 次 |
| `DIAG_COLPTR` c=0 / 1 / 1277 | 2 次 / 0 次 + 余 545 / 0 次 + 余 559 | 3 / 1 / 1 |
| `DIAG_SUM BLOCK` | C3FFC73C | FFFFBF00 |

「行号连续但整体偏移、末 4 行完全没显示、每列都只显示了一部分」= 每行取数没在一行时间内追上，
行缓存被回头重写。772 行/（1 帧 720 行）这个量级 ⇒ 每行都欠一点，越到帧末欠得越多 ⇒
**取数吞吐不足**，而不是行号算错。

**修法（RTL）**：`fb_scanout` 的取数引擎重写成「请求游标 / 写入游标解耦 + 最多 4 个读突发在飞」，
`BURST` 64→255。旧写法每行 20 个突发、每个都要等数据全回来才发下一个 ⇒ 每行固定开销
20×(DDR 延迟+间隙)，屏一行只有 1650 pclk，只要延迟超过 18 拍就**永远追不上**。
仿真里用带 AR 队列的从机模型复现：`+LAT=40` 旧引擎 under=180、98% 像素错；新引擎 LAT 8/40/100 全绿。
（这条改动的教训：旧 TB 的从机模型一次只认一个突发，把主设备侧的提前量完全吃掉了，
所以「仿真全绿、上板黑横条」——模型把代价算错了，判据就不可能对。）

**第二轮（12:52 的 bit）—— 机器判据全绿**

| 探针 | 旧 | 新 |
|---|---|---|
| `DIAG_ROWCODE` Σ | 181,248 | **258,840 ✓** |
| `DIAG_ROWPTR` r=719 | 0 次 | **1 次 ✓** |
| `DIAG_COLPTR` c=0 | 2 次 | **3 次 ✓** |
| `DIAG_SUM VRAMP` | MISMATCH | **MATCH ✓** |
| 128×128 白块 | 92,978 白像素 | **16,640（=128×130）✓** |
| `under` | 0 | 0 |

**人眼（用户当场描述）**：黑横条没了、画面不跳了，但

1. 横带「有一小部分有点错位」；
2. 竖带「最右侧的颜色一部分来到了最左边」；
3. 格子屏「竖线是蓝色的，缺 3 条」（注：`(255,0,0)` 在 BGR 里就是蓝，脚本注释写成「红线」是我的错，颜色本身没错）；
4. 运动方块正常；
5. 角标屏「红色被分成两块在最左右边，白色和蓝色不在屏幕最左边」。

**根因：读地址写错了 2**。`video_driver` 的 `pixel_xpos(t) = cnt_h(t) − 259`，`data_req`/`video_en`
有效拍 `cnt_h ∈ [260,1540)` ⇒ **de 拍 C 上 xpos = C+1**（旧注释写的「0…1279」是错的）。
BRAM 读是同步的，`pixel_data(C) = lbuf[addr(C−1)]`，要 `pixel_data(C) = 源列 C` 就必须
`addr(C−1) = C = xpos(C−1)` —— **读地址直接取 xpos**。旧写法 `addr = max(xpos−2, 0)` 让
屏幕列 C 显示源列 `max(C−2,0)`：整幅图右移 2 像素、最左 3 列重复源列 0、源列 1278/1279 永不显示。
机器判据（累加和）**看不出这个**——累加和与顺序无关，只知道某列被显示了几次，不知道显示在哪。
位置只能用眼睛量，这也是这轮唯一一条只能靠人眼的结论。

**改动面**：`fb_scanout` 读地址 + `rd_valid` 与像素同拍寄存；三个会算期望值的主机脚本
（`build/hdmi_geom_probe.py`、`build/hdmi_ddr_diag.py`、`build/hdmi_visual_check.py`）的列口径
从 `[0,0,0]+1…1277` 改成 `0…1279` 一一对应；四个 TB 的期望同步改。全量仿真 `SIM_PASS 12/12`。

**出片**：13:37 新 bit，WNS 4.184 ns / WHS 0.036 ns，时序全部满足；离线 `--check-hwh` → `FLOW_OVERLAY_HWH_OK`。

**上板卡住**：13:41 部署时本机 `以太网` 显示「媒体已断开连接」，只剩 WLAN 192.168.5.5 ⇒ 到
192.168.2.99 的路由走错网段。按 AGENTS.md，**板子离线不做硬件验证、不写「已验证」**。
下次上板第一步就是 `py tools/run_board_acceptance.py --only deploy`，然后
`py build/hdmi_visual_check.py --hold 50`——第一屏 `EDGES` 就是给这个缺陷准备的读数：
源列 0..3 涂白、1276..1279 涂红，**左边缘应纯白、右边缘应纯红**，左边见红 = 回卷，左边见黑 = 少列。

### 7.23 13:50 上板：EDGES 屏对上，人眼确认「全对」——`HDMI_VISUAL_OK` 记下

用户重新上电后（本机 `以太网` 恢复，`HOST_LINK_OK src=192.168.2.100`）部署 13:37 那片，
`build/hdmi_visual_check.py --hold 45` 六屏：

| 屏 | 期望累加和 | 实际 | |
|---|---|---|---|
| EDGES（源列 0..3 白 / 1276..1279 红 / 正中绿方块） | 8DA6F4C0 | 8DA6F4C0 | MATCH |
| HBANDS | FFF8F800 | FFF8F800 | MATCH |
| VBANDS | FFF8F800 | FFF8F800 | MATCH |
| GRID | D2FD2F00 | D2FD2F00 | MATCH |
| MARKERS | 1FDF3E00 | 1FDF3E00 | MATCH |
| under | 0 | 0 | ✓ |

**人眼当场结论：「全对」**（2026-10-31 13:56）。落到 `data/results/hdmi_visual_ok.json`
（记 sha256 `ad628200…104a`、六屏的期望/实际与逐屏描述、以及本结论**不覆盖**什么）。

顺带一条方法论上的更正：上一版 VBANDS / GRID / MARKERS 报 MISMATCH，**不是硬件错，
是期望值按旧口径（`xpos-2`）算的**。同一批数据在改成「列 ↔ 列一一对应」后全对 ——
说明当时屏幕上唯一真实存在的缺陷就是那 2 像素位移，而**累加和对位移是瞎的**
（它只知道某列被显示了几次，不知道显示在哪一列）。位置类的判据只能人眼量，
这条写进 EDGES 屏的设计理由里了。

至此「画面对不对」这一级第一次有了人眼证据；`HDMI_VISUAL_OK` 记录生效。

### 7.24 固化 overlay 的机器侧收尾：`FLOW_OVERLAY_OK`，外加修好一个真 bug

在**同一片**已人眼确认的 bit 上跑 `flow_overlay.py --selftest`（板上 `python3`）：

    FLOW_OVERLAY_LCD   OK mode=PYNQ bad=0/16 tick 133696195→140374381 cnt=3285648160
    FLOW_OVERLAY_HDMI  OK frames 390→767 (+377) under=0 shown=16 bufs=0x38100000,0x38500000
    FLOW_OVERLAY_KEYS  raw=0x3 key0=1 key1=1
    FLOW_OVERLAY_OK

第一次跑是 `FLOW_OVERLAY_LCD FAIL exception NameError: name 'lcd_selftest' is not defined` ——
`cd07598`（加 `--check-hwh`）那次重构把 `def lcd_selftest():` **这一行**删掉了，函数体
还留在模块级，成了孤儿代码：语法能过、导入能过，只有真跑到 LCD 那一半才炸。
补回一行即可。这条正是「机器判据要真跑一遍」的价值：`--check-hwh` 离线查的是地址表，
它查不出「某个测试函数根本没定义」。

### 7.25 同一片上的「真视频跑一遍」：`HDMI_VIDEO_OK 25 12.0 ... frames=6825 under=0`

    python3 tools/hdmi_video.py --video data/samples/scenes/n05_t01580.0.mp4 --fps 12 --timeout 20
    HDMI_VIDEO_OK 25 12.0 status={'frames': 6825, 'under': 0}

PS 侧解 25 帧真转播画面按 12 fps 写进 DDR 帧缓存（双缓冲 + flush），PL 侧 720p60 扫描输出，
全程 `under=0`。这条走的是**和产品完全相同**的一条路（PS 打包 → DDR → HP0 → 行缓存 → TMDS），
与 §7.23 的静态图案人眼结论互为补充：

| 判据 | 级别 | 结论 |
|---|---|---|
| 六屏静态图案（几何/颜色/无撕裂） | **人眼** | ✅ `HDMI_VISUAL_OK` |
| LCD 回读 + pclk 计数 / HDMI frames / 按键 | 机器 | ✅ `FLOW_OVERLAY_OK` |
| 真视频 25 帧经固化 overlay 环出 | 机器 | ✅ `HDMI_VIDEO_OK`，`under=0` |
| 真视频的**人眼**结论 | 人眼 | ⏳ 未做（人当时不在屏前），不记 |

按项目口径，最后一行不算过，也不拿前三行去顶它。

### 7.26 用户第二次报「还是不对」之后：把「屏与源的位置映射」从人眼照片变成机器读数

§7.23 的人眼「全对」只覆盖了当时那六屏。随后用户报

> 「现在真视频又错位了，和刚才一样，最右侧的现在在最左侧」
> 「不贴边 …… 竖线的顺序是红线白线，绿线 ……」

同一片 13:37 bit 上，`hdmi_align_probe` 请用户念 10 条数字带，用户读出 **8 9 0 1 2 3 4 5 6 7**
并附照片。照片用 cv2 按「每 128 px 一条带、按颜色归类」量了一遍：屏第 c 列 = 源第
(c+1024) mod 1280 列，即**每行整体右移 256 像素**（1024 与 256 在这张图上等价），
三条细锚线（源 320/640/960）落在屏 576/896/1216，也正好 +256。

这一轮不再靠猜，先把它机器侧能查的全查了：

| 检查 | 工具 | 读数 | 结论 |
|---|---|---|---|
| 帧缓存里到底是什么 | `hdmi_shear_probe` | `SHEAR_DDR OK badpx=0` | PS 写侧没问题 |
| 屏上像素是不是源帧的一个排列 | 同上 | `SHEAR_ROT MATCH` | 与「整行循环移位」一致 |
| 是不是**帧级线性错位** | 同上 | `SHEAR_LIN` 只有平凡的 k=0 | 不是整体偏移 |
| 是不是**取数跨行进位**（每行多读 1024 字、把下一行开头卷进来） | 新增 `hdmi_tail_probe` | `TAIL_VERDICT ROT`（两张图：12 横带 + 720 行互不相同） | 行内重排，取数没越界 |
| 不碰 DDR 的对照组 | 内置彩条 | 左端是灰阶带、贴左边缘 | 时序/TMDS 段是好的 |

`hdmi_tail_probe` 的设计理由值得记下来，因为它是本轮唯一能**把两种「长得一样」的机制
分开**的机器判据：

* 「整行循环移位」是**行内排列**，整帧和不变，也读不到帧缓存之外；
* 「取下 1024 字」是**跨行**的，最后一行的尾部会读到帧缓存之外那 4 KB，
  **那 4 KB 的内容会真实改变 `st_sum`**；
* 所以把帧缓存后 4 KB 用 MMIO 直接读出来（不猜），代进两种模型的期望值里逐一比对即可。
  第二张图用「720 行互不相同的颜色」，任何跨行错位都会改求和，把巧合概率压到 0。

于是结论收敛到一句：**屏上每行是「本行数据的一个循环移位」**，错位发生在
DDR→行缓存→`pixel_data` 之内，而这一路此前没有任何能看见「第几列」的出口。

下一步（本轮正在做）：把 4 个相位量 + **屏幕第 0/512/1024 列实际送出的像素**
放进 `st_dbg`（`cfg_pack[31:29]` 选，见 `fb_scanout.v` 端口注释），重出一片诊断 bit，
用 `build/hdmi_phase_probe.py` 一次读完。判据：

    sel1 {wr_ptr@vs, ar_ptr@vs} = 0/0      帧末两条游标归零
    sel2 rd_idx(本行第 0 个 de 拍) ≡ 0 (mod 2048)
    sel3 beats                  = 921600
    sel4 首条 AR − cfg_addr     = 0
    sel5 帧首写入 row0 第 0 列那一拍的数据（值 − 1 = 取数流的起点源列）
    sel6/7 = 屏幕第 511 / 1023 列送出的源列号（ramp 图：值 − 1）

`sel5/6/7` 是关键：它把「取数流从源列几开始」「屏上是源列几」变成机器能读的数，
不再需要「请用户念数字」——这是本轮方法论上真正的补课。

采样游标也有讲究：屏幕列一律用**本行第几个 `data_req` 拍**（`de_x`）来数，
不用 `disp_x / xpos`。因为 xpos 本身就是要验的量，拿它当游标时，一旦它真偏了 256 列，
`disp_x == 0` 整行一次都不成立，读数会静默变成 0 —— 看着像「没采到样」。
`de_x` 每行恒 1280 拍，与 xpos 无关；又因为 `pixel_data` 是同步 BRAM 读出（早一拍），
`de_x == X` 采到的正是**屏幕第 X−1 列**，脚本里按 511/1023 解读。

### 7.27 诊断位出片后第一次读：**`0x41290000` 根本读不到** —— 之前读到的「719/0/0」是假的

诊断版 bit（15:57 出片，`FLOW_SYSTEM_BD_OK`，12/12 仿真 PASS）上板后，`build/hdmi_phase_probe.py`
读回的第一屏就是不对劲的：8 个选择器读出来的值**只随选择器线性变大**，而且低 16 位恒 0。

```
PHASE sel0 00E80000   sel1 01000000   sel2 01180000 ... sel7 01900000     ← 每读一次 +0x180000
PHASE sel1_dec wr_ptr_vs=0 ar_ptr_vs=0 frames=400 under=0
```

`0x180000 = 24 << 16`，而 0.4 s × 60 fps = **24 帧**。换个说法：bit[31:16] 就是 `frames`。
三连测定死（同一片 bit）：

| 读 | 值 | 含义 |
|---|---|---|
| `0x41210000` | `00C00000` | `{frames=0xC0, under=0}` |
| `0x41290000` | `00C00000` | 同上 —— **同一条 st_status** |
| `0x41210008` | `F0E9F000` | `st_sum`（纯 0x7F7F7F 帧，主机算式 `0x7F7F7F×921600` 逐位相等） |
| `0x41290008` | `F0E9F000` | 同上 —— **同一条 st_sum** |

写 `cfg_pack[31:29] = 5`（选「帧首写入数据」）后 `0x41290000` 照旧是 `frames`：选哪一路都读不到。
而这次出片的 `system.bd` 里 `hdmi_video_0/st_dbg → gpio_hdmi_dbg/gpio_io_i` **是连对的**，
地址表也是对的（hwh：`gpio_hdmi_dbg` 64K @ `0x41290000`）。所以坏在**地址解码**这一段
（`hdmi_smc` 的 M01 没有应答，读事务落在了 M00 的 `gpio_hdmi_st` 上），不是 RTL 连线。

#### 这条事实的意义

1. **此前所有「读 `st_dbg` 得到 719/0/0」的结论都是假的** —— 读到的其实是 `{frames, under}`。
   §7.22 里基于这组读数做的推断（行号/欠载判断）必须全部作废、重读。
2. 方法论上，这是一次**同一个错误的第二次**：§7.26 的教训是「累加和对顺序瞎」，
   这次的教训是「**诊断量不能只挂在一条没被验证过的读回总线上**」。
   判定一个诊断口可靠，必须先看它**随时间变化的方式**对不对得上（这次就是靠「+24/0.4s」
   一眼看出来的）。

#### 修法（本轮出片 bvqobwuh3）

不去动地址解码（那要重排 BD，风险大、收益只在读回），而是**把诊断量挪到已经验证过的读回路径上**：

- `cfg_pack[31:29] == 000` → `0x41210008` 仍是**整帧累加和**（老口径逐位不变，仿真 TB 用的就是这一路）；
- `cfg_pack[31:29] != 000` → `0x41210008` 出 `st_dbg` 选中的那一路诊断；
- 顺带加了一味**位置加权和** `sel = 111`：`Σ (屏幕列号) × 像素 mod 2^32`。
  整帧累加和对重排盲，而把列号乘进去之后，「第 c 列显示源第几列」就变成一个数：
  主机把 1280 个平移量 k 全算一遍，**反解 k**。这是「位置」这件事第一次有了**一个数就能判**的口径，
  而且不依赖任何人眼/照片。

`0x41290000` 那一路保留在网表里（不删，留给以后修地址解码），文档里标注为**不可读**。


### 7.28 写入落点改「描述符 + 帧号」驱动（2026-10-31 17:00–18:0x）

#### 出发点：这一轮第一次拿到「位置」的机器读数

§7.27 把诊断口挪到 `0x41210008` 之后，第一次能读到「屏上某个位置显示的到底是源里哪个像素」：
`build/hdmi_map_probe.py` 用「每个像素自带行列号」的图，一次读清三件事。

- `sel4`（帧内第一条 AR 地址 − cfg_addr）= 0、`sel1`（帧末游标）= 0/0、`sel3`（本帧写入拍数）= 921600、
  `sel2`（行首读地址）= 0 ⇒ **地址对、拍数对、没丢没重**。
- 但 `sel5`（帧首写进 row0 第 0 列的那个字）在旧片上读到的是源 **(行719, 列897)**；
  `sel6`（屏第 511 列的像素）读到源列 255，而且它来自哪一行**每帧都在跳**。
- ⇒ 错的是「这一拍落到行缓存哪个位置」。旧写法用「全局拍数 / 1280」推行号，等于假设
  R 通道是一条**首拍对齐的连续整帧流**。相位一偏，整帧被切歪：行跨行混、列整体旋转，
  而累加和（与顺序无关）、`under`、`frames` 全都不报警 —— 这就是「怎么查都没毛病」的原因。

#### 第一版修法（出片 `build/flow_system_build7`）：AR 描述符队列

每条 AR 握手时把 `{行号, 起始列, 长度}` 压进深度 4 的队列，R 通道每收一拍按
「队首描述符 + 突发内偏移」落点；弹队列按**拍数**而不是 `m_rlast`（AXI3 从机可能拆包回）。
仿真 12/12（含 `tb_fb_scanout_frame` 逐像素、`tb_hdmi_scanout_sys` 端到端）。

上板读回（同一片 bit，两次运行）：

| 图 | sel5（帧首写入 row0col0） | sel6（屏 511 列） | sel7 反解 k |
|---|---|---|---|
| map 图 | (行0, 列0) ✅ | — | — |
| ramp 图 | 源列 **1243**（= −37） | 源列 **474**（= 511−37） | **1243** |

两次运行结果不同 ⇒ 相位不是固定值。ramp 这次自洽地指向「元素 j 落到列 j+37」：
**帧首那 37 拍是上一帧尾巴的数据，被新帧第一个描述符吃掉了**。
清队解决不了：数据什么时候回由从机决定，跟我们的队列状态无关。

#### 第二版修法（出片 `build/flow_system_build8`）：描述符带帧号，帧界零等待

- 每条突发在**发出那一拍**就把 `{行号, 起始列, 长度, 帧号}` 入队（帧号 = vs 计数，队列深度 8）；
- R 通道每收一拍都按队首描述符记账并弹出，但**只有帧号等于当前帧的才写进缓存**：
  上一帧的残留拍被它自己的描述符吃干净、悄悄丢掉，不污染新帧，也不需要停下取数等它们；
- 于是 `flush_pending` 整套（帧界停取数、等回完、再归零）**删除**，帧界零等待，只把游标归零；
- 跨 vs 的那次 AR 握手**不推进游标**（判据用帧号 `ar_frame_q == frame_id`，不用 `vs_rise`）。

`ar_frame_q` 这一条是仿真当场抓出来的：只拦与 vs**同拍**的握手不够 —— 握手晚一两拍回来
同样属于老帧，而 vs 分支已经把游标归零，两个非阻塞赋值谁在后面谁生效,
新帧第一次取数会从行中间开始。`tb_fb_scanout` 报 `ERRS=32`、`got=xxxxxx`：
**行 0 的头 32 列整段没写进去**（人眼就是左上角一条黑带）。改成帧号判据后 12/12 全过。

#### 待做

- 上板（`flow_system_build8`）读 `sel5/sel6/sel7`：期望 `(行0,列0)` / 源列 511 / `k = 0`；
- 人眼看屏（验收标准：机器读数与人眼都要过）。

### 7.29 用户第三次报「还是那样」：把「一个像素落在屏上第几列」变成可测的数

#### 先说一个自己踩的坑：`sel0` 不是诊断通道

`hdmi_video_top` 里 `cfg_pack[31:29] == 0` 走的是**老口径整帧累加和**，
`{disp, behind, under}` 那一路只在 `st_dbg` 里、没有对外选择器。用 `sel0` 去读诊断位，
读出来 `disp=43 / behind=1936`（每帧只有 720 条有效行，1936 根本不可能），
这是**同一个错误的第三次**：拿「看起来像诊断的寄存器」当诊断用。
本节的实测全部只用 `sel5/sel6/sel7`（`st_dbg` 与位置加权和）。

#### 新仪器：一个像素、一个值 —— `build/hdmi_pixel_probe.py`

把源 (r,c) 单点写 1、其余全黑，然后读两路：

- `sel0`（整帧累加和）= 这个源像素在屏上**出现了几次**；
- `sel7`（位置加权和 Σ(屏列+1)×像素）− 1 = 它落在**第几列**（值恒为 1 时无歧义）。

上板结果（第 300 行，列 0..1216 步长 64 + 末列）：

```
c=0 → 屏列 425   c=64 → 489   c=128 → 553  …  c=832 → 1259
c=896 → 43       c=960 → 105  …              c=1279 → 424      （run A）
```

- **`sum` 恒等于 1**：不丢、不重、不是「线性流错位」；
- 屏列 = (c + 425) mod 1280：**整行是一个常数循环移位**；
- 换一次进程再测，同一个探针给出 **399**（不是 425）⇒ **移多少每次上电都不一样**；
- 行方向不受这个探针影响（加权和只看列），所以另做一个。

#### 行方向：把行号写进像素值

整幅图每一行填「行号+1」（列方向无所谓），读 `sel6`（屏幕最后一条有效行、第 511 列）：

```
ROWMAP px512_val=592 -> 屏第 719 行显示的是源第 591 行   （行偏 128）
ROWMAP sum 与理论值**逐位相等**（d=0）
```

再补一刀：只点亮**一条**源行、其余全黑，扫 24 个行号读 `sel6`，**全是 0**
—— 那次运行里屏幕最后一行整行被门控成黑（`line_ready=0`）。

#### 结论（这才是「修不好」的真相）

1. 数据一条不少、拍数一拍不差（`sel3` 恒 921600、`sel4`=0、`sel1`=0/0），
   但屏上内容同时在**行、列两个方向**错位，错多少**每次上电不同**（列 425/399/423，行 128）；
2. 行缓存只有 4 槽、`ready[]` 只按「行号低 2 位」索引 ⇒ **任何 4 的整数倍的行滞后都是隐形的**
   （128 正好是 4 的倍数），`under`/`frames`/`beats` 全部照常；
3. 反过来也成立：取数落后时 `can_fetch = behind | ahead<=3` **放行**取数去写「正在显示的那一槽」，
   于是屏上是「新帧的某几列 + 旧帧的其余列」，前沿就在取数游标处 —— 列偏移 425 就是那个前沿。
4. 因此这不是某一处地址算错，而是**取数与扫描输出之间缺一个「行号」握手**。

#### 决定：先走用户给的退路（网口回传），HDMI 作为待办

用户 2026-10-31 明确授权：「不行就从网口回传带框的视频」。新增
`tools/video_back.py`：板端把带框帧压成 JPEG 走 TCP 回传，PC 端收下来存 MP4 + 抽帧 PNG，
协议见文件头。HDMI 修法（下次出片一起做，不再单独试）：

- 行缓存每槽加一个**行号标签**，写数据时连同槽号写入；显示侧只认 `tag == cur_line`，否则黑并计数；
- 取数落后时**跳过**已显示过的行（`ar_line = cur_line + 1` 重新对准），不再一路爬着追；
- `MAX_OUT` 4→8、`BURST` 64→128（512 字节对齐，不跨 4 KB）。

### 7.30 2026-10-31 18:20–19:30：行号握手落地 + 上板 28/28 全部落位

§7.29 决定的三件事一次做完，只出**一片** bit（AGENTS.md：综合十分钟级，不连着试）。

#### 改了什么（`src/rtl/io/hdmi/fb_scanout.v`）

| 问题 | 改法 |
|---|---|
| `ready[]` 按 `line & 3` 索引，128 行滞后隐形 | 每槽加 11 位**行号标签** `ltag0..3`，写完一行连同行号写入；显示侧 `line_ready = (ltag_c == cur_line)` |
| 落后时取数会写「正在显示的槽」 | `skip_now = behind`：落后即刻把游标**对准当前显示行**（`ar_line = cur_line, ar_ptr = 0`），不再一路爬着追 |
| 突发跨 4 KB（AXI4 违规） | `BURST 255 → 128`（512 字节，行基址 5120=512×10 天然对齐，永不跨界） |
| 在飞不足 | `MAX_OUT 4 → 8` |
| 队列深度不是 2 的幂 | `QDEP 12 → 16`（4 位指针按 16 回绕；QDEP=12 时索引 12..15 越界，`d_len=x` 把取数冻死 —— 仿真里踩到过，见下） |

#### 仿真

- 名义四例全过：`fb_scanout` / `fb_scanout_frame` / `hdmi_scanout_sys` / `hdmi_video_top`；
- 另做了「慢从机」压力例（`LAT=60, GAP=20`）：**79% 的行未就绪、屏上大面积黑**。
  查明这个 TB 的从机是**单笔在飞**（`m_arready` 在整笔突发期间为 0），144 拍才出 64 拍数据
  = 0.44 拍/周期，而显示侧要 0.78 拍/周期 —— 这是**带宽根本不够**的模型，不是本设计的缺陷模型。
  行为上它给出的是「黑」而不是「错位」，正是标签门想要的安全降级。**该压力例不能作为通过判据**，
  已在 TB 头注释外记下；要真和板子对齐得做多笔在飞的从机模型（待办）。

#### 上板（新 bit `1397ce6813f9`，时序全收敛）

`build/hdmi_pixel_probe.py` 一个像素一个像素地问，新片 **28/28 全部 `OK`**：

```
PIX r=300 c=   0 sum=1 px=   0 OK
PIX r=300 c= 128 sum=1 px= 128 OK
...
PIX r=300 c=1279 sum=1 px=1279 OK
PIX r=  0 c= 640 sum=1 px= 640 OK      ← 行首/行尾
PIX r=719 c= 640 sum=1 px= 640 OK
```

同一片仪器在旧片上读的是 `px = (c + 425) mod 1280`（§7.29），现在是**恒等映射**：
每个源像素恰好出现一次，落在同名屏幕列。**列方向已由机器证明修好。**

#### 但「行」这台仪器证明不了

`possum` 里只有屏幕列号，行置换对任何整帧标量都是不变量（Σ、possum、拍数全都瞎）。
`r=719 c=640 OK` 只说明「这个点在屏上第 640 列、出现一次」，**不能**说明它落在第 719 行。
行方向只能靠人眼看屏 —— 与 AGENTS.md 的验收口径一致。为此用真片子环出：

```
HDMI_VIDEO_OK 559 25.0 status={'frames': 10174, 'under': 0} achieved=12.4     # n03 普通场景
```

#### 顺带：带框回传视频从「有框位」变成「真有框」

- 板端 `--no-pl`（PL 预处理路径把框坐标喂错，`_is_grass` 36 中只剩 2）；
- `--max-det 8` 会**限死检测帧数**（150 帧只检 8 帧），要 `--max-det 0`；
- `--no-camera-gate`（新增）：没有 calib 时 `valid_frac=0` 会被判 `INVALID_VIEW` 并**清空全部坐标**
  —— 对「不输出错坐标」是对的，对出演示视频是错的，因此给一个显式开关；
- `video_back.py --tracks` 要 **JSONL**（`build/flow/*.jsonl`），给 summary `.json` 会静默退化成不发框。

结果：`n03` 150/150 帧、`c03` 146/150 帧带框；回传 `VIDEO_BACK_OK 250 19.61 5.1`。

#### 收尾时又抓到一处「静默不出框」

回传和环出都调用 `hdmi_video.norm_frame()`，它只认 `players`（球场坐标），
而 `board_pipeline` 落盘的 JSONL 用的是 `tracks`（`[id,x1,y1,x2,y2,score]`，像素坐标）。
于是**两个通道都一个框都没画**，表头还印着 `players 0` —— 看起来像检测全灭，实际是归一化函数漏了一个口径。
已让 `norm_frame` 在无 `players` 时按 `tracks` 转（box 直接用像素框），自检 `HDMI_VIDEO_SELFTEST OK`。
重跑结果：`c03` 帧 137 = **players 16**、`n03` 帧 137 = **players 20**，框都落在人身上
（`build/annotated_c03.mp4`、`build/annotated_n03.mp4`，各 250 帧）。

### 7.31 2026-10-31 20:2x：**纠正 §7.30 的结论** —— 缺陷是间歇的，不是修好了

§7.30 写「列方向已由机器证明修好」是**错判**。同一片 bit（`1397ce68…`）：

| 时间 | 仪器/人 | 结果 |
|---|---|---|
| 19:27 | `hdmi_pixel_probe` | 28/28 `OK`，`px == c` |
| 19:50 前后 | 用户看屏 | 「现在可以了」 |
| **20:23** | 同一仪器 | 28/28 `SHIFT`：c=0→437、c=640→1077、c=1279→440 |
| 20:24 | 用户看屏 | 「HDMI 又变成之前那样了」 |

**结论**：行号标签挡住了「显示错行」，但没挡住「整行内容被旋转 k 列」。
k 随每次运行变化（425/399/437/440/441，也出现过 0）⇒ 它依赖取数与扫描之间的相位，
不是某个固定地址算错。`HDMI_VISUAL_OK` **不成立**，证据文件已改成 `HDMI_VISUAL_RETRACTED`。

为什么仿真一直绿：四个 TB 的 AXI 从机都是**单笔在飞**（`m_arready` 在整个突发期间为 0），
而板上是 8 笔在飞、128 拍突发。**相位类缺陷在单笔模型里根本不存在** —— 这是这次最大的教训，
比缺陷本身更值得记：仪器的模型不对，「全过」只是安慰。

下一步（未做，需重开一片）：
1. 写一个多笔在飞的从机 TB（AR 可连续握手、R 按序回、可配延迟抖动），先在仿真里复现 k≠0；
2. 重点查 vs 边沿：队列里残留突发、`ar_ptr` 残值、新帧首行的 `d_col` 是否保证为 0；
3. 复现之前，演示一律走网口回传（`tools/video_back.py`，已跑通两段片子）。

### 7.32 面板刷新慢的根因（已修，未上板验证 —— 板子在 20:26 掉线）

用户问「为什么面板刷新的这么慢」。实测板端：

| 动作 | 实测 |
|---|---|
| `HdmiVideoOut.show()`（空帧，仅打包+写 DDR+切缓冲） | 23.3 ms/帧 |
| 整条回放（解码+缩放+画框+打包） | ≈80 ms/帧 ⇒ **12 fps 上限** |
| `bram_fb.write_image4`（4bpp 量化+写屏） | **3637 ms/帧** ⇒ 面板 3.6 s 才刷一次 |

根因在 `pack_4bpp` 的最近色落位：`rgb[:,:,None,:] - pal[None,None,:,:]` 会当场开一个
480×800×16×3 的 int32 数组（**73 MB**），A9 上光分配+遍历就要几秒。
改成「先对图像取唯一色，再对唯一色做 16 次向量比较」：**逐色精确**（与旧实现逐像素比对 0 失配，
调色板原色仍精确映射到自己），随机图 480×800 从 3636 ms → **80 ms**，
界面面板（颜色少）更快。`BRAM_FB_SELFTEST OK`。
**待办**：板子回来后推 `tools/bram_fb.py` 上板复测，并把 `--fps` 调回 2–5。
