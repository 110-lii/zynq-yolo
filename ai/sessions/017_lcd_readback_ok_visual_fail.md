# Session 017 — LCD 双屏画面复测失败：帧缓存读回全对、屏上是乱码；定位到 `lcd_fb_reader` 队列版不再成立

| 字段 | 内容 |
|---|---|
| 日期 | 2026-10-28 |
| 执行 | Kilo（桌面端，接 016 之后） |
| 任务类型 | 上板复测 / 错误定位 / RTL 回退（未出片） |
| 目标 | 板子恢复供电后，把 016 改完的 `display_both.bit` 上板做三级阶梯（white→chess→panel）并接实时状态面板 |
| 上下文 | `tools/check_display_board.py`、`build/scripts/bd_display_both.tcl`、`src/rtl/io/lcd/*`、`src/rtl/io/hdmi/fb_reader.v`、官方 `D:\FPGA\A\...\21_lcd_rgb_colorbar` |

## 结论

**白屏通过（人眼：两块屏整屏亮白）。棋盘格失败**：帧缓存逐字读回 100% 正确，
但屏上是「灰灰的长条分隔 / 大小不一、白到黑渐变的方块」，而且画面会随 BD/重写变化。
**没有一项能写"通过"**；只有 white 级由人眼确认。

## 现场证据（按发生顺序）

| # | 动作 | 机器结果 | 人眼结果 |
|---|---|---|---|
| 1 | 板子上电，ping 恢复、Jupyter 9090 通；板端 `display_both.bit` SHA256 前 16 位与本地入库版一致 | OK | — |
| 2 | `--which both --pattern white` | `DISPLAY_BOTH_BOARD_OK`（HDMI 28,800 字 / LCD 12,000 字，`nonzero` 满） | **两块都整屏白** |
| 3 | `--pattern chess` | 同上 `DISPLAY_BOTH_BOARD_OK` | 两块都是「灰长条」 |
| 4 | 重写 chess（同 bit） | 读回 OK | 仍是灰长条 |
| 5 | 直接 `MMIO` 写 32 px 棋盘 / 竖条纹 / 横条纹 | 读回 `bad=0` | 竖条纹→**看着是竖的**；横条纹→**看着是横的**（行/列映射成立） |
| 6 | 62 px 棋盘 | 读回 `bad=0` | 这次是**规则棋盘格** |
| 7 | 64 px 棋盘（同写法） | 读回 `bad=0` | 灰长条（可复现） |
| 8 | 16 px 棋盘 → 立刻 64 px 棋盘 | 读回 OK | 「有一瞬间是棋盘格，很多，现在又变回之前那样」 |
| 9 | 32 px 棋盘 | 读回 OK | 「长方形的格子，很多，但是颜色从白到黑渐变」 |
| 10 | 左半黑 / 右半白 | 读回 OK | 「左右一点点是灰黑灰白，中间全是横跳」 |
| 11 | 换单屏 `lcd_status.bit`（无 HDMI/跟踪逻辑） | 读回 OK | **彩条**（该 bit 不是 014 验过的版本） |
| 12 | 官方 `21_lcd_rgb_colorbar` 原版 bit（本机新建 `build/scripts/_colorbar_official.tcl`：只读 Verilog + 官方 XDC，`COLORBAR_BIT_OK`，WNS +10.031 / WHS +0.225） | 下载时 PYNQ 报 `unable to find metadata`，手工 `device.download` 之后**板子整机复位掉线**，未能判读 | — |

## 定位

1. **写侧（PS→BRAM Port A）无罪**：每一步都逐字读回，`bad=0`。
2. **行/列映射无罪**：竖条纹出竖的、横条纹出横的。
3. **字内位序/读延迟时序有问题**：`64 px` 与 `62 px` 用的是同一份 RTL、同一次 BD，
   唯一的差别是「一行是不是恰好在字边界切换」——
   对齐到 32 px（字宽）时错，不对齐时反而对 ⇒ 是**每个字里只有部分位是真值**，
   再加上串行输出时看成了渐变/条纹。
4. 016 已经把 `lcd_fb_reader.v` 从 014 验证过的**移位版**改成了和 HDMI 一样的
   `(wq, xq)` **队列版**（「BRAM 读延迟咬成固定 1 拍」）。这个假设在 HDMI 侧成立，
   在 LCD 侧（独立 MMCM 33.333 MHz + 行基址位宽改过）**不成立**：
   数据端口的实际延迟变了，队列里成对寄存的位序与数据就错位。
   这就是本轮「读回全对、屏上乱码、且随图案变化」的唯一自洽解释。
5. 单屏 `lcd_status.bit` 直接出彩条 ⇒ 入库的这两个 bit **不是 014 验过画面**的那两个版本
   （014 的修复在 10-26 20:07 才提交，而 `lcd_status.bit` 的 mtime 是 10-26 19:52），
   所以"014 已验证"这句话对入库 bit 不成立，只是会话记录里的结论。

## 本轮改动（未出片、未上板）

| 文件 | 改动 |
|---|---|
| `src/rtl/io/lcd/lcd_fb_reader.v` | 回退到**字内移位版**（014 在屏上验证过的结构）：`bram_addr` 用 `xe[10:5]` 播字、`xe[4:0]==0` 装载 `sr`、`sr[0]` 出像素；`xe = xpos - 2`（014 的正式修法）。地址位宽保持 14 位（25 字/行）。 |
| `tools/check_display_board.py` | ① `find_base` 忽略 `PSDDR`（单屏 bit 的 `mem_dict` 里有它，会被误判成"多个控制器"）；② 新增 `--pattern hstripe\|vstripe\|split`（本轮真正区分"行/列映射"与"字内位序"的图案）；③ 新增 `--hold`（写完停住供人眼判读）；④ 活体探针修掉三个误报：`dbg2` 高位是 `{v_cnt,h_cnt}` 不能 `&0x7FF` 硬切、复位期 `dbg1=0` 要剔除、不再写死「de 全 1」；⑤ 自检补 sticky 用例。本机 `DISPLAY_SELFTEST OK`。 |

> 需要 Vivado 重出 `display_both.bit`（`bd_display_both.tcl`），并**先**用官方
> `colorbar_official.bit` 确认屏/接线（HX 版是 25 MHz / 25 MHz 引脚、DE 模式、`lcd_rgb` 是三态双向）。

## 未完成 / 未验证

| 项 | 状态 | 怎么算过 |
|---|---|---|
| 官方 colorbar bit 上板 | **未能判读**（下载后板子复位掉线） | 屏上是 8 色彩条 ⇒ 屏/线/引脚无问题 |
| 回退后 `display_both.bit` 重出片 | 未出片 | `bd_display_both.tcl` 跑完 + 时序收敛 |
| chess 级人眼判读 | 未过 | 两块屏都是规则 64×64 棋盘格 |
| panel 级 + 实时状态面板 | 未做 | 8 项信息齐全、中文可读 |
| HDMI 侧 | 本轮未单独判读（HDMI 白屏也通过了，棋盘格同样不对） | 单跑 `hdmi_status.bit` 三级阶梯 |

## 纪律

1. **"人眼说白屏通过"也要和"棋盘格通过"分开记**：白屏只证明 DE/时钟/背光/数据在动，
   不证明寻址与位序。本轮正是白屏全绿、棋盘格全错。
2. **判据图案要正交**：竖条纹/横条纹/左右分半分别独立测「列映射」「行映射」「字内位序」，
   比一张棋盘格更快把问题夹到具体一级。
3. **换 BD/时钟后，读侧时序假设要重新验**：同一份 `fb_reader` 结构在 HDMI 上成立，
   换到独立 MMCM 的 LCD 上就不成立。别把"上次验证过的代码"当成"这次也对"。
4. **入库 bit 的 mtime 和会话结论对不上时，先信 mtime**：014 的修复提交时间晚于
   `lcd_status.bit` 的 mtime，本轮因此被单屏 bit 的彩条误导了一轮。
