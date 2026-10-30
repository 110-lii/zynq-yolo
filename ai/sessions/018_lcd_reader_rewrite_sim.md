# Session 018 — LCD 读侧重写：定位到「pixel_data 打拍」与「字边界装载时机」，五点图案整场仿真 0 错

| 字段 | 内容 |
|---|---|
| 日期 | 2026-10-28 |
| 执行 | `magpie/group/1101`（Kilo 桌面端，接 017 之后） |
| 任务类型 | RTL 重写 / 仿真验证（未出片、未上板） |
| 目标 | 按 `D:\FPGA\oi` 的 LCD 实现与实测结论重写 `lcd_fb_reader`，把 017 遗留的「读回全对、屏上乱码」在**仿真里**先闭环 |
| 上下文 | `src/rtl/io/lcd/{lcd_fb_reader,lcd_driver,lcd_status_top}.v`、`D:\FPGA\oi\project\rtl\out\out_lcd_rgb.v`、`D:\FPGA\oi\ai\sessions\{056,085,089}`、`tools/bram_fb.py`、`tools/status_display.py` |
| 范围 | **只动 LCD**；HDMI 按要求暂停，`src/rtl/io/hdmi/*` 本轮未改 |

## 结论

**017 的「队列版 vs 移位版」之争不是根因。** 用 iverilog 把整场 480×800 逐像素跑出来之后，
真正的两个错误是：

1. **`pixel_data` 打了一拍。** `lcd_driver` 用 `lcd_rgb = lcd_de ? pixel_data : 0` 组合选通，
   `pixel_data` 必须与 `pixel_xpos` **同拍**有效。原实现 `pixel_data <= wq[xq]/sr[0]`
   是寄存器输出，整幅图比坐标晚一拍 ⇒ **每一行第 0 列读到的是消隐期的黑**。
   LCD 的字宽是 32 px，这个 1 px 的错位又叠加到每个字边界上，屏上就成了「灰长条/渐变」。
2. **字边界的装载时机用错了坐标。** 原实现用 `xe[4:0]==0` 判断「本字第一拍」，
   但 `xe = xpos - 1` 在**同一拍**才更新，装载条件与地址条件互相差一拍 ⇒ 装进 `sr` 的是**下一个字**。
   仿真上表现为「64 px 棋盘错、62 px 反而对」——正好复现 017 的人眼现象。

重写后：**5 种图案 × 整场 480×800 = 192 万像素，0 错**；再把 `status_display` 真实渲染的
8 行面板经 `pack_1bpp` 打进 RTL 读回重建，**与源图逐像素 0 差异**。
BRAM 读延迟改成 1/2/3 拍各跑一场，仍然 0 错 ⇒ 结构不依赖「地址到数据恰好 1 拍」这个假设。

## 新结构（`lcd_fb_reader.v`）

```
消隐期 (xpos==0)：bram_addr = row_base；sr <= bram_dout（持续装载本行 Word0）
显示期 每位：    sr <= sr >> 1；cur_col[4:0]==0 时把「下一字地址」发出去
某字最后一拍：   sr <= bram_dout（32 拍前就发出的地址，早已稳定）；word_idx++
输出：           pixel_data = active ? (sr[0] ? WHITE : BLACK) : BLACK   ← 组合
```

三条与 oi 对齐的口径：

- `pixel_xpos` 有效值 **1..800**、`pixel_ypos` **1..480**（oi `out_lcd_rgb` 与官方 `lcd_driver` 同口径），
  所以列/行都减 1；
- 字内位序 **bit0 = 最左像素**，与 `tools/bram_fb.py` 的 `np.packbits(bitorder="little")` 一致
  （Python 侧单独验过 `bit0→0x00000001`、`bit31→0x80000000`、`bit32→下一字 bit0`）；
- DE 模式：HS/VS 恒高，DE 拉满 800×480（沿用 `lcd_driver`，未改）。

## 证据链

| # | 证据 | 位置 |
|---|---|---|
| 1 | 逐像素定位：`h_cnt=216 de=1 xpos=1`，而 `pixel_data` 仍是消隐期的 BLACK | 仿真波形打印 |
| 2 | 字边界：`cur_col=31` 那一拍才该装载下一字，原实现判 `xe[4:0]==0`（早一拍） | 代码对照 + 仿真 |
| 3 | 5 图案整场：棋盘 64×64 / `0xAAAAAAAA` / `0x55555555` / 1 行横条 / 单 bit 漂移，各 384,000 px 全过 | `sim/tb/io/lcd/lcd_fb_reader_tb.v`，`LCD_FB_READER_TB_PASS 5 patterns / 1920000 px` |
| 4 | 延迟鲁棒：1/2/3 拍 BRAM 延迟各一场全过 | `sim/tb/io/lcd/lcd_fb_reader_latency_tb.v`，`LCD_FB_READER_LATENCY_TB_PASS` |
| 5 | 位序接口：RTL dump 的整场帧与 `pack_1bpp` 重算结果 **0/384000** | `tools/bram_fb.py --verify-frame`，`BRAM_FB_FRAME_OK`（挂 `run_sim.py` 后置校验） |
| 6 | 无警告：`iverilog -Wall` 对三个 LCD 文件零 warning/error | 本机 |
| 7 | 全量回归 6/6 用例通过（含原有 4 个视频/FIFO 用例） | `py sim/scripts/run_sim.py`，`SIM_PASS 6/6 cases` |

## 本轮改动

| 文件 | 改动 |
|---|---|
| `src/rtl/io/lcd/lcd_fb_reader.v` | 重写：预取移位寄存器 + 组合 `pixel_data`；消隐期持续装载 Word0；字边界按 `cur_col[4:0]==31` 装载、下一字地址提前 32 拍发出 |
| `sim/tb/io/lcd/lcd_fb_reader_tb.v` | 新增：5 种正交图案整场 480×800 逐像素比对，末场 dump 到 `build/sim/lcd_frame.txt` |
| `sim/tb/io/lcd/lcd_fb_reader_latency_tb.v` | 新增：同一 RTL 在 1/2/3 拍 BRAM 延迟下的等价性 |
| `tools/bram_fb.py` | 新增 `--verify-frame`：把 RTL dump 的帧与 `pack_1bpp` 重算结果对拍（Python↔RTL 位序唯一接口），并入 `--selftest` |
| `sim/scripts/run_sim.py` | `CASES` 增 2 条（`lcd_fb_reader` 挂后置校验、`lcd_fb_reader_latency`） |

`lcd_driver.v`、`lcd_status_top.v`、BD 脚本、`clk_wiz_lcd`（33.333 MHz）**未改**。

## 仿真踩到并修掉的坑（判据本身写错过两次）

1. **第一版 TB 在 `posedge` 前采样**，把「时钟沿后一拍」的数据当成当前像素，报出 30 个假错；
   改成 `while(!lcd_de) @(posedge clk); #1;` 后才与硬件口径一致。
2. **期望值用 `wire exp_rgb = exp_bit ? ... : ...`**，而 `exp_bit` 在 task 里阻塞赋值 ——
   同一时间步内连续赋值还没传播，比较用的是**上一轮**的期望值，整幅图看起来「错位一格」。
   改成在 task 内直接算 `exp_pix` 后归零。
   ⇒ 教训与 oi `085` 同源：**判据自己也要先被反证一次**，否则会把 TB 的 bug 当成 RTL 的 bug 去改。

## 上板结果（2026-10-28 15:0x，新 `display_both.bit`）

出片：`REBUILD_OK`，`write_bitstream Complete!`，`All user specified timing constraints are met`；
板端 bit 的 SHA256 前 16 位 `74489793a8a95e61` 与本机一致。

| 级别 | 机器判据 | 结果 |
|---|---|---|
| white | 12000 字逐字读回 `bad=0`（nonzero=12000） | `DISPLAY_BOARD_OK` |
| chess | 读回 `bad=0`；活体探针 `de=0.821 h=[0,1054] v=[0,524] uniq=525 white=0.5261 pclk=33.357MHz` | `DISPLAY_BOARD_OK` |
| panel | 读回 `bad=0`；活体探针 `de=0.810 h=[1,1052] v=[0,524] uniq=525 white=0.0362 pclk=33.357MHz` | `DISPLAY_BOARD_OK` |

**`v_uniq=525` 是关键证据**：探针在 0.5 s 内看到的行号覆盖了整整 525 行（`v_total`），
而不是 017 那种「只有部分行有内容」；`h` 覆盖到 1054（`h_total=1056`），
`white` 比例与图案自洽（棋盘 ≈0.5、面板 ≈0.036）。像素时钟 33.357 MHz 与 016 的实测一致。

**顺手修掉一个探针 bug**：`probe_lcd_live` 原来对 `dbg2`（`{v_cnt,h_cnt}` 计数器）也要求
「连读两次取相同值」，而 `h_cnt` 每拍都变 ⇒ 实测 `SAME=0`、`d` 列表恒空，报
`h_max=-1 / v_not_advancing`。改为「解码后 `h<h_total && v<v_total` 才收，越界当撕裂丢弃」，
并在测频时同样跳过撕裂样本（否则 pclk 会被虚高到 34.5 MHz）。

> **人眼终验仍未做**：本轮只证明帧缓存内容、扫描范围与扫描顺序正确；
> 「屏上看到的是不是规则棋盘格/可读面板」必须由人看屏确认，不写「通过」。

## 未完成 / 未验证

| 项 | 状态 | 怎么算过 |
|---|---|---|
| `display_both.bit` 重出片 | **已做**（含本修复） | `write_bitstream Complete!` + 时序满足 |
| LCD 上板 chess 级（机器） | **已过** | `DISPLAY_BOARD_OK` + `v_uniq=525` + `pclk=33.357 MHz` |
| LCD 上板 panel 级（机器） | **已过** | 同上，`white=0.0362` |
| 人眼终验 | **未做** | 看屏：规则 64×64 棋盘；8 行中文面板可读、位置正确 |
| HDMI 侧 | 已用同一结构重写并仿真通过（见 `019`），待本次出片后上板 | `--which hdmi` 三级阶梯 + 人眼 |

## 纪律

1. **屏上「乱码」先做逐像素仿真，不要先在板上试图案。** 本轮 5 图案 × 整场跑完只用了几秒，
   比重新出片（十分钟级）+ 上板判读快两个数量级，且能直接指出是「第 0 列」还是「字边界」。
2. **`pixel_data` 与坐标必须同拍**：凡是驱动 `lcd_de ? pixel_data : 0` 这类组合选通的，
   输出端不能再打拍；要打拍就连坐标一起打。
3. **字边界判据要用「当前像素的列号」，不要用「下一拍的列号」。** 差一拍在 32 px 对齐时才暴露，
   不对齐时看着正常 —— 这正是 017 里 62 px 对、64 px 错的原因。
