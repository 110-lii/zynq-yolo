# sim/ —— 仿真与验证（testbench、激励向量、回归脚本与结果）

> 上级：[`../README.md`](../README.md)
> 唯一事实源：[`../docs/repo_spec.md`](../docs/repo_spec.md) §2.2 / §4

| | |
|---|---|
| 本目录装什么 | RTL 的 testbench（`sim/tb/`）、激励向量（`sim/vectors/`）、回归脚本与仿真结果入口（`sim/scripts/`） |
| 谁产生 | 手写（仿真器产生的波形/日志为产物） |
| 进 git | 是（仿真真源 `sim/tb`、`sim/scripts` 必须进；波形与临时日志不进） |

## 内容

| 路径 | 说明 |
|---|---|
| `tb/` | **Testbench 真源**。镜像 `src/rtl/` 的子目录结构，见下表 |
| `vectors/` | 激励向量：输入激励文件、期望输出、随机种子记录等，供 tb 读取 |
| `scripts/` | 回归入口脚本：`run_sim.py` 一条命令编译+仿真+令牌判定 |
| `scripts/make_hls_case.py` | HLS C 仿真用例生成（与 RTL 回归并列） |

### 现有用例

| 用例 | testbench | 被测 RTL | 成功令牌 | 备注 |
|---|---|---|---|---|
| `vid_dvp_capture` | `sim/tb/video/vid_dvp_capture_tb.v` | `src/rtl/video/vid_dvp_capture.v` | `VID_DVP_CAPTURE_TB_PASS` | DVP→AXIS、tuser/tlast、背压、溢出粘滞位 |
| `vid_test_pattern` | `sim/tb/video/vid_test_pattern_tb.v` | `src/rtl/video/vid_test_pattern.v` | `VID_TEST_PATTERN_TB_PASS` | 闭式图样（无摄像头时的上板激励源） |
| `fifo_async` | `sim/tb/common/inf_axis_async_fifo_tb.v` | `src/rtl/common/inf_axis_async_fifo.v` | `INF_AXIS_ASYNC_FIFO_TB_PASS` | 跨时钟 FWFT、随机反压、满/空、单侧复位；附带 6 个变异自检（判据能失败） |
| `vid_capture_chain` | `sim/tb/video/vid_capture_chain_tb.v` | pattern + capture + 异步 FIFO | `VID_CAPTURE_CHAIN_TB_PASS` | **整链**：640×480 一帧、25 MHz→100 MHz 跨域、25% 反压；TB 把收到的字节 dump 成 `build/sim/chain_frame.bin`，再由 `tools/capture_golden.py --compare` 与**上板用的同一份黄金模型**对拍（`run_sim.py` 的后置校验，成功时另打 `CAPTURE_MATCH_OK`） |
| `lcd_fb_reader` | `sim/tb/io/lcd/lcd_fb_reader_tb.v` | `lcd_timing` + `lcd_fb_reader` + `lcd_status_top` | `LCD_FB_READER_TB_PASS` | 5 种正交图案（64×64 棋盘 / 两种 1px 竖条 / 1 行横条 / 单 bit 漂移）各整场 480×800 逐像素比对；末场 dump 成 `build/sim/lcd_frame.txt`，由 `tools/bram_fb.py --verify-frame` 与 `pack_1bpp` 重算结果对拍（Python↔RTL 位序，成功时打 `BRAM_FB_FRAME_OK`） |
| `lcd_fb_reader_latency` | `sim/tb/io/lcd/lcd_fb_reader_latency_tb.v` | 同上 | `LCD_FB_READER_LATENCY_TB_PASS` | 同一 RTL 在 BRAM 读延迟 1/2/3 拍下各跑一整场，证明预取结构不依赖固定读延迟（017 的队列版就栽在这个假设上） |
| `fb_reader` | `sim/tb/io/hdmi/fb_reader_tb.v` | `video_driver` + `fb_reader` | `FB_READER_TB_PASS` | 3 种图案各整场 1280×720 逐像素比对；末场 dump 成 `build/sim/hdmi_frame.txt`，由 `tools/bram_fb.py --verify-frame --size 1280x720` 对拍 Python 位序（`BRAM_FB_FRAME_OK`） |

### `sim/tb/` 镜像 `src/rtl/` 子目录结构

`sim/` **镜像** `src/rtl/` 的子目录结构；`x.v` 的 testbench 命名为 **`x_tb.v`**。

```text
src/rtl/                      sim/tb/                 （镜像）
├── common/   ─────────────►  common/
├── video/    ─────────────►  video/
├── accel/    ─────────────►  accel/
├── io/       ─────────────►  io/
└── ip/       ─────────────►  （IP 自带仿真模型，一般无需 tb）
```

| `src/rtl/` 下模块（**命名示例，非现有文件**） | 对应 testbench（`sim/tb/` 下同镜像路径） |
|---|---|
| `src/rtl/common/inf_reset_sync.v` | `sim/tb/common/inf_reset_sync_tb.v` |
| `src/rtl/video/vid_dvp_capture.v` | `sim/tb/video/vid_dvp_capture_tb.v` |
| `src/rtl/accel/acc_dma_mover.v` | `sim/tb/accel/acc_dma_mover_tb.v` |
| `src/rtl/io/io_axil_regs.v` | `sim/tb/io/io_axil_regs_tb.v` |

> 命名规则一句话：**`x.v` → `x_tb.v`**，且 tb 在 `sim/tb/` 下的相对路径与源文件在 `src/rtl/` 下的相对路径**逐段相同**。

## 规则

- tb 文件名必须是 `x_tb.v` 形式，**不允许** `tb_x.v` / `test_x.v` / `x_test.v` 等变体。
- tb 必须放在与源模块**逐段相同**的镜像路径下；路径不对视为违反 §2.2。
- 激励向量统一放 `sim/vectors/`，**不要**散落在 `tb/` 或仓库根。
- 回归入口统一放 `sim/scripts/`；脚本必须能**一条命令**跑完并给出可判定的结论。
- **不要用退出码判断成功**（§5）：仿真结论必须落到日志令牌/断言计数上（例如 `TEST_PASS` / `TEST_FAIL` 或 UVM 风格 error count == 0）；本项目综合/实现阶段的令牌为 `DONE_OK`，JTAG 下载为 `PROGRAM_OK`（见 [`../build/README.md`](../build/README.md)、[`../board/README.md`](../board/README.md)）。
- 波形文件（`*.vcd` / `*.wdb` / `*.fsdb`）与仿真日志属**产物**，不进 git；有价值的失败波形如需保留，请转为图片或文本摘要后落盘。
- 命名硬约束（§1）：目录名与文件名只用 `a-z 0-9 _ - .`；禁止中文、空格、括号、全角字符；**文件正文可用中文**。
- 对应 RTL 待裁定项（加速器实现方式、核心算法形态、接口契约数值等）：`<待用户裁定：见 docs/repo_spec.md §7>`。

## 怎么用

```powershell
# 1. 全部用例回归（令牌判定：看 SIM_PASS / SIM_FAIL，不看退出码）
& D:\python\python.exe sim\scripts\run_sim.py
mingw32-make sim                     # 等价（Makefile 转发）

# 2. 单个用例 / 打开逐拍打印
& D:\python\python.exe sim\scripts\run_sim.py --only vid_dvp_capture
& D:\python\python.exe sim\scripts\run_sim.py --only vid_dvp_capture --trace

# 3. 列出用例表
& D:\python\python.exe sim\scripts\run_sim.py --list
```

引擎选择：`auto` 先找 iverilog（本机 `D:\iverilog\bin`，快速回归），没有就退到
Vivado 2020.2 的 `xvlog/xelab/xsim`。**官方工具链是 Vivado xsim**；iverilog 只是
本机快检，RTL 统一按 Verilog-2001 可综合子集写。产物（`.vvp`/日志）只落
`build/sim/`，不进 git。

**后置校验**：`run_sim.py` 的 `CASES` 第 6 项可以挂一个「拿 TB 产物再打第二把尺子」的
校验。采集链就靠它把仿真 dump 直接交给上板用的同一个比对器
（`tools/capture_golden.py`），所以「仿真过」与「板上过」用的是**同一份黄金模型**，
不是两套各自为政的判断。跑通时日志里会多一行
`POST vid_capture_chain tools/capture_golden.py -> CAPTURE_MATCH_OK`。

> 依赖的本机环境事实见 [`../docs/repo_spec.md`](../docs/repo_spec.md) §6：器件 `xc7z020clg400-2`，工具链 **2020.2**。
