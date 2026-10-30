# 明天（2026-10-30）的连续作业提示词

> 用法：早上开新会话，把下面 `====` 之间的整段粘贴进去即可。写这份提示词时
> 仓库状态 = 2026-10-29 22:5x（LCD 4bpp+按键 bitstream 正在重出片；板子当时离线）。

---

====

你在 `D:\zynq-yolo`（ZYNQ-7020 足球视频分析）继续干活。**今天我一整天上课，
没法碰电脑、没法回答任何问题、也没法帮你看屏幕。** 所以：

- **不要问我任何问题，不要等确认，不要中途停下来写"要不要继续"**。遇到岔路口就按本提示词
  写的默认值自己定，把判断和理由写进当天的会话记录。
- **不要提交任何破坏性 git 操作**（不许 `reset --hard`、`checkout --`、`clean -fd`、
  不许改写历史、不许 push）。可以本地 `git commit`（每条工作项做完且自检通过后提交一次）。
- 一次任务做不完就做完能做的部分、记 todo、继续下一项，最后在报告里写清楚**哪些是机器验过的、
  哪些必须人眼**。
- 每完成一项，立刻在 `ai/sessions/022_*.md` 里记流水（时间、命令、令牌、结论），
  并在 `ai/sessions/INDEX.md` 登记。**没有证据的事不许写"已验证"。**

## 0. 先做三件事（5 分钟）

1. 读 `AGENTS.md`、`ai/coordination/DECISIONS.md`（重点 D015/D018/D026）、
   `ai/sessions/021_lcd_probe_from_scratch.md`、`docs/hdmi_video_osd.md`。
2. `ping -n 1 192.168.2.99` 看板子在线与否；在线就跑
   `py tools/board_clock.py status`，再 `py tools/board_clock.py sync` 对齐时间。
3. 看 `.scratch/build4.log` 最后 10 行和 `ls -l board/overlay/lcd_probe.bit`：
   昨晚（10-29）在重出 **4bpp + 16 色 + 按键** 的 `lcd_probe.bit`，
   如果 bit 的时间戳还是 10-29 21:18 之前，说明没出成功，按 §1 重出。

## 1. 第一优先级：LCD 4bpp/调色板/按键的 bitstream 出片（最长的一步，先起）

背景：LCD 已经从 1bpp 改成 **4bpp + 16 色调色板**（800×480 → 48,000 图像字 + 16 调色板字，
`0x40000000` 占 192 KB，AXI 段开 256K）；新增按键 `key[0]=L14/key[1]=K16` 挂在
`0x41260000`；480×272 的 BRAM 挪到 `0x40080000`。RTL 已过黄金模型仿真
（800×480 与 480×272 各 1,536,000/522,240 像素、两种 BRAM 延迟 0 错，`SIM_PASS 1/1`）。

**★ 10-29 23:11 这一片已经出成功**：`LCD_PROBE_BD_OK`，WNS **+1.839 ns** / WHS **+0.018 ns**
（0 failing），`board/overlay/lcd_probe.bit` 时间戳 23:11、`.hwh` 23:03，
pclk 实测配置 50 MHz×57.25/3/28.625 = **33.333 MHz**、/106 = **9.001 MHz**。
⇒ **明天第一件事是确认 bit 时间戳还是 23:11 且 hwh 与 bit 同批**；是就直接上板，
**不要再无谓重出一次**（一次 10 分钟起，且这台机器容易 OOM）。

出片命令（**只能一个一个跑，绝不并行**）：

```bash
D:/Xilinx/Vivado/2020.2/bin/vivado.bat -mode batch -source build/scripts/bd_lcd_probe.tcl -log build/vivado/logs/lcd_probe_build.log -journal build/vivado/logs/lcd_probe_build.jou
```

★ 这台机器只有 14 GB 内存，**`-jobs` 必须是 2**（脚本里已改好，别调大）。
今天已经踩过的坑，照做即可：

| 现象 | 处理 |
|---|---|
| `ERROR: [Timing 38-246] Caught exception 'Out of memory'` + 一堆 `Failed run(s)` | 是内存，不是设计错。确认脚本里 `-jobs 2`，重跑 |
| `Failed to remove the directory ...runs. The directory might be in use` | 有僵尸 vivado：`powershell -NoProfile -Command "Get-Process vivado|Stop-Process -Force"`，再 `rm -rf build/vivado/lcd_probe_bd`，重跑 |
| `Failed to load feature 'ipintegrator'` / `invalid command name "bd::utils::dbg"` | 上一进程被杀后的瞬态。确认无 vivado 进程后 `rm -rf build/vivado/lcd_probe_bd` 再跑；连续两次以上才需要怀疑安装 |
| `couldn't read file ".../scripts/xguifrmwork/init.tcl": No error` + `bd_rule:ai_engine` | 同上（10-29 23:00 实测：删项目目录后重跑即好）。判断"安装本身没坏"的办法：用 `.scratch/bd_smoke.tcl` 那样的 5 行脚本另建一个空工程跑 `create_bd_design`，能过就是本工程目录残留，删目录重来 |
| 出片成功 | 日志里有 `write_bitstream Complete`，`board/overlay/lcd_probe.bit` 时间戳是新的，`.hwh` 同批更新 |

判定成功：`ls -l board/overlay/lcd_probe.bit` 时间是今天，且
`grep -c "LCD_PROBE_BD_FAIL" .scratch/build4.log` 为 0。

## 2. 上板验证 LCD（板子在线才做；离线就记 todo 跳到 §3）

板子 IP `192.168.2.99`，**SSH 不通**，一切走 Jupyter：
`py tools/board_jupyter.py "<python>"`（端口 9090、密码 xilinx）。
传文件用 **`py tools/board_jupyter.py --push 本地 远端`**（新加的；旧的 `--put`
只能写 Jupyter 根目录，会 404）。

**2.1 传文件**
```
board/overlay/lcd_probe.bit  -> /home/xilinx/zynq-yolo/board/overlay/lcd_probe.bit
board/overlay/lcd_probe.hwh  -> 同目录
tools/bram_fb.py tools/status_display.py tools/lcd_probe.py tools/lcd_ui.py tools/board_jupyter.py
  -> /home/xilinx/zynq-yolo/tools/
board/fb/tag4.bin -> /home/xilinx/zynq-yolo/board/fb/tag4.bin
```
传完每个文件都打印了两端 sha256，**必须一致**才算传成。

**2.2 4bpp 地址/行映射自证（机器判据）**
```
py .scratch/verify4_board.py
```
这个脚本会：加载 overlay → 写 tag4（第 r 行第 k 字 = `0x11111111*((r+k)&15)`）+
16 个调色板字 → 写 cfg=0x10 → 逐行读 `0x4123/0x4124/0x4125` 的抓取寄存器。
**通过条件：`READBACK_OK bad=0`**，且每行 `de=800`。
> 若 `lb0` 全 0：先查是不是 cfg 没写进去（`Overlay()` 会清零 cfg）；再看是不是
> 调色板没装（PAL_BASE=48000 处的 16 字）。地址口径的坑见 `lcd_probe_core.v` 文件头。

**2.3 状态面板上色**
```
py tools/status_display.py --size 800x480 --fb-bram 0x40000000 --once
```
令牌必须是 `layout=800x480 bpp=4 words=48016`。截图/读回都只能证明「写进去了」；
**颜色对不对必须人眼看**——今天没人，所以记成 todo（见 §5）。

**2.4 赛前 UI**
```
py tools/lcd_ui.py --keys            # 先读一次电平，判断松开时的空闲态
py tools/lcd_ui.py --timeout 900     # 交互模式，按键操作
py tools/lcd_ui.py --script 0,1,0,0,0,1   # 无按键时的回放自测，应打印 LCD_UI_SAVED
```
按键极性未知也没关系：`lcd_ui.py` 以「第一次读到的电平」为空闲态，按下即触发。
确认后会写 `/home/xilinx/zynq-yolo/board/ui_config.json`。**按键真按下去能不能用，
今天没人按**——如果 2.4 的 `--keys` 读到的是合理电平（不是恒定 0 或恒定 3），把它记成
「待人工按键验收」。

## 3. HDMI 输出「带框的画面」（D026，第二优先级）

资产已存在但**没有上板验证记录**（`ai/sessions/INDEX.md` 里搜不到 hdmi_video）：
`src/rtl/io/hdmi/fb_scanout.v`、`hdmi_video_top.v`、`sim/tb/tb_fb_scanout.v`、
`build/scripts/bd_hdmi_video.tcl`、`tools/hdmi_video.py`、`docs/hdmi_video_osd.md`、
`board/overlay/hdmi_video.bit`（时间戳 10-27/28，**来源可疑，先当未验证**）。

顺序：
1. **仿真先行**：把 `sim/tb/tb_fb_scanout.v` 注册进 `sim/scripts/run_sim.py`（照
   `lcd_probe_core` 的写法加一个 case），跑 `py sim/scripts/run_sim.py`，要 `SIM_PASS`。
2. **不接板也能出图**：`py tools/hdmi_video.py --replay <某场 JSONL> --video <对应视频>
   --png-out .scratch/hdmi_osd.png --max-frames 30`，看图里球员绿框 + #id、球红圈、
   顶部事件条是否都在（这是**唯一**能由机器判断"框画对了"的方式）。
3. 板子在线时：重新出 `hdmi_video.bit`（同 §1 的流程，换 `bd_hdmi_video.tcl`），
   上板后**先看 status 寄存器的 `under` 是否稳定为 0**（行欠载判据），
   再由 `tools/hdmi_video.py` 往 DDR 帧缓存刷帧。
4. 两条硬约束别改（HP0 的 ACLK 必须与 74.25 MHz 同源；Zynq-7000 的 HP 是 AXI3，
   必须过 smartconnect），见 `docs/hdmi_video_osd.md` §3。

时间不够时的取舍：**先出图（第 2 步）→ 再上板**。出不了板就交"主机侧 OSD 图 + 仿真通过 +
上板 todo"，不要写"HDMI 已显示"。

## 4. 整体流程测试（第三优先级）

目标是「一条命令跑完，出对照表」，把已有的件串起来，不要新写大工程：
- 已有：`tools/check_board_acceptance.py`（板上三组参数的球链路验收）、
  `tools/result_store.py`（SD/文件增量存储）、`tools/result_stream.py`（板→PC）、
  `tools/pc_dashboard.py`（PC 端球场视图）、`tools/status_display.py`（LCD/HDMI 面板）、
  `tools/lcd_ui.py`（赛前 UI）、`tools/board_clock.py`（时间对齐）。
- 做一个 `tools/check_full_flow.py`（主机侧也能跑）：顺序 = 时间对齐 → 赛前配置
  （读 `board/ui_config.json`，没有就用默认）→ 跑一段 video/replay → 结果落盘（JSONL+summary）
  → 推 PC → 面板刷新（LCD 4bpp）→ **最后打印一张 PASS/FAIL 表**，每行带证据令牌。
  允许用 `--dry-run` 在主机上跑通（不接板），接板再跑一次。
- 判据只能是「令牌 + 读回」；画面类判据一律写成人眼 todo。

## 5. 提交整理（最后做，且必须留干净状态）

1. 跑一遍能跑的全部自检（至少：`py tools/bram_fb.py --selftest`、
   `py tools/lcd_data.py --selftest`、`py tools/status_display.py --selftest`、
   `py tools/check_display_board.py --selftest`、`py sim/scripts/run_sim.py`），
   把每条令牌抄进会话记录。
2. 写 `ai/sessions/022_<短名>.md`（今天的完整流水 + 未决问题 + 下一步），
   更新 `ai/sessions/INDEX.md`；`docs/` 里受影响的文档（`display_overlay_plan.md`、
   `board_acceptance_20261027.md`、`tools/README.md`）同步真实状态。
3. **人眼验收清单**单独写一节（用户晚上回来照着看）：LCD 4bpp 彩色面板、UI 按键、
   tag4 彩条、HDMI 带框图、双屏同时。每条写「看什么、正常长什么样、不对时先查哪个文件」。
4. `py tools/make_submission.py` 出 `dist/` 包；**旧包删掉只留今天一份**；
   打印包内文件数与大小。
5. 仓库根目录写 `TODAY_REPORT.md`（一页）：做完了什么/没做完什么/卡在哪/我回来第一件事做什么。
6. `git status` 必须干净或只剩明确说明的产物；本地 `git commit`，**不 push**。

## 6. 时间与节奏

- 板子如果一直离线：每 5 分钟 ping 一次，**不要空等**，同时推进 §1 出片、§3 仿真/出图、
  §4 主机侧流程、§5 文档。
- 长时间编译放后台跑，别阻塞；编译期间做别的。
- 每 1 小时在 `ai/sessions/022_*.md` 追加一行「HH:MM 当前状态」，方便我晚上回看。
- 结束时必须留下：① `TODAY_REPORT.md` ② `ai/sessions/022_*.md` ③ 会话记录里
  **明确区分的「机器已验证 / 待人工」两张清单**。

====
