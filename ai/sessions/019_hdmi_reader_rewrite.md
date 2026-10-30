# Session 019 — HDMI 读侧同样重写（与 LCD 同构），1280×720 整场仿真 0 错

| 字段 | 内容 |
|---|---|
| 日期 | 2026-10-28 |
| 执行 | `magpie/group/1101`（Kilo 桌面端，接 018 之后） |
| 任务类型 | RTL 重写 / 仿真验证 / 上板 |
| 目标 | 把 018 在 LCD 侧验证过的「预取移位寄存器」结构用到 HDMI 侧，消掉 017 里「HDMI 棋盘格同样不对」 |
| 上下文 | `src/rtl/io/hdmi/{fb_reader,hdmi_status_top,video_driver}.v`、`ai/sessions/017`、`018`、`tools/bram_fb.py` |

## 结论

**HDMI 侧与 LCD 侧是同一个 bug、同一个修法。** 原 `fb_reader` 用「(字, 位序) 成对寄存」的队列版，
把「地址到数据恰好 1 拍」写死；且 `pixel_data` 是寄存器输出，而 `video_driver` 用
`video_rgb = video_de ? pixel_data : 0` **组合选通** ⇒ 每行第 0 列必然读到消隐期的黑。

重写后：3 种图案 × 整场 1280×720 = 276 万像素，**0 错**；RTL dump 帧与 `pack_1bpp` 重算结果
**0 差异**（`BRAM_FB_FRAME_OK 1280x720`）。

## 与 LCD 的差异（只有分辨率）

| 项 | LCD | HDMI |
|---|---|---|
| 行宽 | 800 px = 25 字 | 1280 px = 40 字 |
| 行数 | 480 | 720 |
| 坐标口径 | `xpos` 1..800、`ypos` 1..480 | `xpos` 1..1280、`ypos` 1..720（本轮用探针实测确认，不靠推测） |
| 每行 DE 拍数 | 800 | 1280 |
| 字索引位宽 | 5 bit（0..24） | 6 bit（0..39） |
| 地址位宽 | 14 bit（12,000 字） | 15 bit（28,800 字） |

坐标口径是**先测后用**：单独写了个只实例化 `video_driver` 的探针（`.scratch/hdmi_probe/`），
打印 `de` 有效期的 `xpos/ypos`，实测 `de cyc=1 xpos=1 ypos=1` 起、每行 1280 拍、
`xpos==0` 在 DE 期间出现 0 次 ⇒ 与 LCD 完全同口径，`-1` 的换算可以直接搬。

## 结构（`src/rtl/io/hdmi/fb_reader.v`）

```
消隐期 (xpos==0)：bram_addr = row_base；sr <= bram_dout（持续装载本行 Word0）
显示期 每位：    sr <= sr >> 1；cur_col[4:0]==0 时把下一字地址发出去
某字最后一拍：   sr <= bram_dout（32 拍前发出，早已稳定）；word_idx++
输出：           pixel_data = active ? (sr[0] ? WHITE : BLACK) : BLACK   ← 组合
```

## 证据链

| # | 证据 | 位置 |
|---|---|---|
| 1 | 坐标口径实测：`de cyc=1 xpos=1 ypos=1`，`XPOS_ZERO_CYCLES=0` | `.scratch/hdmi_probe/probe_tb.v` 输出 |
| 2 | 3 图案整场：64×64 棋盘 / `0xAAAAAAAA` / 单 bit 漂移，各 921,600 px 全过 | `sim/tb/io/hdmi/fb_reader_tb.v`，`FB_READER_TB_PASS 3 patterns / 2764800 px` |
| 3 | 位序接口：RTL dump 帧 vs `pack_1bpp` 重算 **0/921600** | `tools/bram_fb.py --verify-frame --size 1280x720`，`BRAM_FB_FRAME_OK 1280x720` |
| 4 | 全量回归 7/7（新增 HDMI 用例，原 6 个不受影响） | `py sim/scripts/run_sim.py`，`SIM_PASS 7/7 cases` |

## 本轮改动

| 文件 | 改动 |
|---|---|
| `src/rtl/io/hdmi/fb_reader.v` | 重写为预取移位寄存器 + 组合 `pixel_data`（与 `lcd_fb_reader` 同构，40 字/行、720 行、15 位地址） |
| `sim/tb/io/hdmi/fb_reader_tb.v` | 新增：3 种图案整场 1280×720 逐像素比对，末场 dump 到 `build/sim/hdmi_frame.txt` |
| `tools/bram_fb.py` | `gold_bits/verify_frame` 泛化到任意 32 像素对齐分辨率；`--verify-frame` 增 `--size` |
| `sim/scripts/run_sim.py` | `CASES` 增 `fb_reader`（挂 `--verify-frame --size 1280x720` 后置校验） |

## 上板结果（2026-10-28 15:1x，含 LCD+HDMI 两块屏的新 `display_both.bit`）

出片：`write_bitstream Complete!`、`REBUILD_OK`，板端 bit SHA256 前 16 位 `1d86b7d97eaf0ae4`
与本机一致（下载走 PC 上的 `http.server`，`board_jupyter.py --put` 不能写
`/home/xilinx` 之外，Jupyter 根目录限制 —— 记一笔，省得下次再试）。

| 级别 | HDMI 机器判据 | LCD 机器判据 |
|---|---|---|
| white | 28,800 字逐字读回 `bad=0` | 12,000 字读回 `bad=0` |
| chess | 读回 `bad=0`；`HDMI_LIVE OK addr_max=28799 ypos_max=720 de=0.753 fps=59.2` | 读回 `bad=0`；`LIVE OK pclk=33.357MHz h=[0,1054] v uniq=524 white=0.5169` |
| panel | 读回 `bad=0`；`HDMI_LIVE OK addr_max=28795 de=0.741 fps=59.3` | 读回 `bad=0`；`LIVE OK pclk=33.357MHz white=0.0343` |

`DISPLAY_BOTH_BOARD_OK bit=display_both.bit pattern=panel screens=hdmi,lcd`。

HDMI 侧「fps=59.2」是这次新加的判据：用 `bram_addr` 的推进量除以 28,800 反推帧率，
所以「读口停住 / 只扫一段 / 跳着扫」都会立刻出窗；单次撕裂用「越界丢弃 + `Δ>500` 不计入」
剔除（首版把撕裂计入总量，得到虚高的 62.5，已改）。

> **人眼终验仍未做**：机器判据只证明「缓存内容对、扫描范围对、扫描顺序与速率对」，
> 不证明「屏上看到的就是规则棋盘/可读面板」。这一条必须人看屏。

## 未完成 / 未验证

| 项 | 状态 | 怎么算过 |
|---|---|---|
| 出片 | **已做** | `write_bitstream Complete!` |
| HDMI 上板 white/chess/panel | **已过**（机器） | `DISPLAY_BOTH_BOARD_OK` + `HDMI_LIVE` 三条判据 |
| LCD 上板 white/chess/panel | **已过**（机器） | `DISPLAY_BOTH_BOARD_OK` + `LIVE` 四条判据 |
| 人眼终验 | **待看屏** | 显示器/屏上规则 64×64 棋盘；面板 8 行可读、中文不乱码 |

## 纪律

1. **同一 bug 在两块屏上不会只出现一次。** 018 把 LCD 修好后，必须回头检查 HDMI 是否同构 ——
   017 已经写了「HDMI 棋盘格同样不对」，这不是新问题，是同一处漏改。
2. **坐标口径先测后用。** 不假设 HDMI 与 LCD 的 `xpos` 同一起点；用一个 30 行的最小探针
   打出 `de/xpos/ypos` 实测值再写 RTL（成本 < 1 分钟）。
3. **改 RTL 前不要动正在跑的出片。** 本轮把 HDMI 修改先写在 `.scratch/` 并用同一份 RTL 仿真验证，
   LCD 那次出片跑完才换进 `src/`，避免 Vivado 综合到一半读到被改的源文件。
