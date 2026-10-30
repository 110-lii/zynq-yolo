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
