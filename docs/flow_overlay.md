# flow_system —— 全流程固化 overlay（2026-10-30）

> 把「面板 + 按键 + HDMI 大屏」合进**一个** bitstream。换片不再重建 PS 句柄，
> 赛场上一次加载之后小屏和大屏同时可用。
>
> 机器判据与画面判据仍然分开记：本文只给机器侧的事实，**屏上颜色/版面必须人眼看**。

---

## 1. 为什么合

在这之前整条链路散在三个比特流里：

| bitstream | 提供 | 谁在用 |
|---|---|---|
| `lcd_probe.bit` | LCD 800×480 / 480×272 4bpp 帧缓存、两颗按键、逐行自证抓取 | `status_display.py`、`lcd_ui.py`、`check_lcd4_board.py` |
| `hdmi_video.bit` | HDMI 1280×720 32bpp 扫描输出（S_AXI_HP0 读 DDR） | `hdmi_video.py` |
| `display_both.bit` | HDMI + LCD **同时**（1bpp 文字面板，D023 试验） | 未进主线 |

`display_both` 证明了「同一片 xc7z020 能同时驱动两个显示」是可实现的（当年 LUT 3.92%、
BRAM 48/140）。但它是 1bpp 文字方案，和现行 4bpp 面板 / 32bpp 视频不是一路，所以不能直接
拿来当固化版本 —— 需要在现行 RTL 上重做一次合并。

资源预算（各自单独出片的实测值）：

| | Slice LUT | Block RAM |
|---|---|---|
| `lcd_probe` | 10,119 (19.0%) | 80 / 140 (57.1%) |
| `hdmi_video` | 5,897 (11.1%) | 6 / 140 (4.3%) |
| **相加上限** | ~16,000 (30%) | ~86 / 140 (61%) |

结论：装得下，且余量足。

## 2. 结构

```
                      ┌──────────────── Zynq PS7 ────────────────┐
  sys_clk(U18,50MHz)──┤ (LCD 像素钟源)                            │
                      │  M_AXI_GP0 ──┐        S_AXI_HP0 ──┐        │
                      │  FCLK_CLK0(83.33)                  │        │
                      └──────────────┼─────────────────────┼────────┘
                                     │                     │
                          ┌──────────▼──────────┐   ┌──────▼──────┐
                          │ axi_smc (10 MI)     │   │  hp_smc     │
                          │  9× LCD 从设备      │   │ (AXI4→AXI3) │
                          │  M09 → axi_cc ──────┼──┐└─────────────┘
                          └─────────────────────┘  │        ▲
                                    │              │        │
                          ┌─────────▼───────┐      │   ┌────┴──────────┐
                          │ BRAM 800 / 480  │      │   │ fb_scanout    │
                          │ GPIO×7 + keys   │      │   │ (pclk)        │
                          │ lcd_probe_0     │      │   │ hdmi_video_0  │
                          └─────────────────┘      │   └───────────────┘
                                    ▲              │           ▲
                                    │       ┌──────▼──────┐    │
                             pclk33/pclk9  │ hdmi_smc    │────┘
                             (clk_wiz)     │ + 2×GPIO    │
                                           │ (pclk_h)    │
                                           └─────────────┘
                                              pclk_h = 74.255954 MHz
```

两个 `clk_wiz` 的输入来自**不同源**（LCD 走外部 50 MHz 引脚，HDMI 走 PS 的 FCLK_CLK0），
不违反「同一引脚只能插一个 IBUF」。

### 为什么要 `axi_clock_converter`

HDMI 的 GPIO 必须挂在 pclk（74.25 MHz）域：上一版把它们挂在 83.33 MHz 时，
GPIO → `fb_scanout` 的跨域路径被当成同步路径收敛，**实测 WNS −4.573 ns / 60 个失败端点**。
而 `M_AXI_GP0` 只能有一个从设备分支，所以先在 FCLK 域做 smartconnect，
再由 `M09` 走 `axi_clock_converter` 进 pclk 域（CDC 交给有验证的 IP，不是靠约束掩盖）。

## 3. 地址表（硬件契约，D006 —— **2026-10-30 起 v1 冻结**）

> 下表按用户 2026-10-30 的裁定**冻结为 v1**：改地址、改 4bpp 打包口径（8 像素/字、
> 最低 4 位是最左像素）、改 `WPR=100`、改按键引脚，都必须**新开一条 decision**，
> 不许在代码里悄悄改。`tools/flow_overlay.py` 的 `check_map()` 就是这条纪律的执行者。

| 地址 | 大小 | 从设备 | 内容 |
|---|---|---|---|
| `0x40000000` | 256K | `bram_ctrl_800` | LCD 800×480 4bpp（48,000 图像字 + 16 调色板字） |
| `0x40080000` | 64K | `bram_ctrl_480` | LCD 480×272 4bpp |
| `0x41200000` | 64K | `gpio_hdmi_cfg` | HDMI ch1=帧缓存物理地址，ch2={en[27],width,stride} |
| `0x41210000` | 64K | `gpio_hdmi_st` | HDMI `{frames[31:16], under[15:0]}` |
| `0x41220000` | 64K | `gpio_cfg` | LCD cfg：`[0]`相位 `[1]`背光关 `[3:2]`图案 `[4]`帧缓存 `[5]`480×272 |
| `0x41230000` | 64K | `gpio_d0` | (lb0, lbmid) |
| `0x41240000` | 64K | `gpio_d1` | (st0, stmid) |
| `0x41250000` | 64K | `gpio_d2` | (fills_cnt, de_last) |
| `0x41260000` | 64K | `gpio_keys` | key[1:0]（PL KEY0=L14 / KEY1=K16） |
| `0x41270000` | 64K | `gpio_id` | **迁移**：原 `0x41200000` |
| `0x41280000` | 64K | `gpio_t` | **迁移**：原 `0x41210000` |
| `0x41290000` | 64K | `gpio_hdmi_dbg` | HDMI 诊断口：由 `cfg_pack[31:29]` 选一路，见下 |

两条迁移是合并的代价：`0x4120/0x4121` 被 HDMI 占用（那两个地址是 `hdmi_video.py`
写死的，改它风险更大）。影响面：

- `tools/status_display.py`、`tools/lcd_ui.py`、`tools/hdmi_video.py`、`tools/bram_fb.py`
  —— **完全不受影响**，用的地址一个没动；
- `tools/lcd_probe.py` —— 本来就按 IP 名从 hwh 解地址（`find_ip`），自动跟随；
- `tools/check_lcd4_board.py` —— 已改成同样按 IP 名解地址（2026-10-30）。

### HDMI 诊断口（读 `0x41210008`，**不要读 `0x41290000`**）

`0x41290000`（`gpio_hdmi_dbg`）实测**读不到**：读它返回的是 `gpio_hdmi_st` 的寄存器
（2026-10-31 16:05 实测：`0x41290000 == 0x41210000 == {frames, under}`，
`0x41290008 == 0x41210008 == 累加和`，改 `cfg_pack[31:29]` 不起作用）。
BD 网表里 `hdmi_video_0/st_dbg → gpio_hdmi_dbg/gpio_io_i` 是连对的，坏在地址解码（`hdmi_smc` M01），
所以诊断量改走**已验证的** `gpio_hdmi_st` 第二通道：

| `cfg_pack[31:29]` | `0x41210008` 读出 | 预期 |
|---|---|---|
| 0（默认） | 整帧像素累加和（老口径，逐位不变） | 主机算式相等的值 |
| 1 | `{描述符队列深度@vs, ar_ptr@vs}` | `0 / 0`（旧口径的 `wr_ptr` 已随描述符队列取消） |
| 2 | 本行第 0 个 `data_req` 拍上的 `rd_idx`（低 13 位） | `≡ 0 (mod 2048)` |
| 3 | 本帧写入拍数 | `921600` |
| 4 | 帧内第一条 AR 地址 − `cfg_addr` | `0` |
| 5 | 帧首「写 row0 第 0 列」那一拍的数据 | ramp 图：值 − 1 = 取数流起点源列（应 0） |
| 6 | 屏幕第 511 列实际送出的像素 | ramp 图：源列 511 = 一一对应，255 = 行内转 1024 |
| 7 | 位置加权和 `Σ(屏幕列号)×像素 mod 2^32` | 主机反解平移量 k，`k = 0` 才对 |

**写入落点已改为描述符 + 帧号驱动**（2026-10-31 17:2x）：每条 AR **发出的那一拍**就把
`{行号, 起始列, 长度, 帧号}` 压进深度 8 的队列（帧号 = vs 计数），R 通道每收一拍都按队首
描述符记账并弹出，但**只有帧号等于当前帧的才写进缓存**。不用「全局拍数 / 1280」推行号，
也不再靠「帧界停取数等回完」——上一帧尾巴上的拍会被它自己的描述符吃干净后丢掉。
帧号同时用来挡跨 vs 的那次 AR 握手：不推进游标，否则新帧会从行中间开始取。
sel1 的上半段现在是队列深度；sel5 读到的「帧首写进 row0 第 0 列的那个字」
**就是该帧第一条 AR 的数据**。

为什么要有 sel6/7：整帧累加和任何重排都看不见（2026-10-31 就是累加和逐位相等、屏上却转了 1024 列），
人眼照片只能给出「整除 128」的结论。sel7 把位置变成一个可反解的数：主机把 1280 个 k 全算一遍即可。
`build/hdmi_phase_probe.py` 一次读完 0–7 并给出 `PHASE_POSSUM` / `候选平移 k`；
`build/hdmi_position_check.py` 是常驻版（读 2/5/6/7，令牌 `HDMI_POSITION_OK|FAIL`）。

## 4. 怎么用

出片（主机，十分钟级）：

```bash
cd /d D:\zynq-yolo && build_flow_system.bat
```

**不上板**就能做的体检（主机，读 `.bit` 同名的 `.hwh`）：

```bash
py tools/flow_overlay.py --check-hwh --bit board/overlay/flow_system.bit
```

它把「硬件契约」逐条对一遍：12 个地址是否与 §3 一致、`gpio_hdmi_cfg` 是否真的输出、
`gpio_hdmi_st`/`gpio_hdmi_dbg` 是否真的输入（方向配错会让读回**恒 0**，看起来像 PL 没算）、
`HP0_DDR_LOWOCM` 是否真的分配给了 HDMI 读主机（漏分配 = 互连 DECERR，而
`under`/`frames`/`frames` 全都正常 —— 2026-10-30 夜里就是这么被骗的）、
`st_sum`/`st_dbg`/`st_status` 是否真在网表里、`.bit` 与 `.hwh` 是否同批。
令牌 `FLOW_OVERLAY_HWH_OK` / `FLOW_OVERLAY_HWH_FAIL <items>`。
**它不替代任何上板判据**，只是把「本可以离线发现」的那类错挡在烧板之前。

板端一条命令自证：

```bash
python3 tools/flow_overlay.py --selftest
```

大屏的**人眼判据**单独一步（机器判据过了也要做 —— 2026-10-30 全白就是这么发现的）：

```bash
python3 tools/check_hdmi_cards.py      # 红/绿/蓝/白/黑五张卡各 12 s，循环 5 分钟
```

它会逐张打印 `HDMI_CARDS_PHASE`。**屏上能依次读到 `STEP1-RED` … `STEP5-BLACK`
才算 HDMI 通路接通**；一直白/一直黑都另有原因（见脚本头部的判定表）。

令牌：

- `FLOW_OVERLAY_LCD OK ...` —— 4bpp 帧缓存写得进读得出 + pclk 计数在走；
- `FLOW_OVERLAY_HDMI OK ...` —— frames 增长且 `under=0`；
- `FLOW_OVERLAY_KEYS raw=0x?` —— 按键电平；
- 末行 `FLOW_OVERLAY_OK`。

## 5. 状态

| 项 | 状态 |
|---|---|
| 构建脚本 `build/scripts/bd_flow_system.tcl` | ✅ 出片成功（见 §6） |
| 约束 `build/scripts/flow_system.xdc` | = `lcd_probe.xdc` + HDMI TMDS 四根 P 脚，无冲突 |
| 综合/实现 | ✅ `FLOW_SYSTEM_BD_OK`，WNS **+3.042 ns** / WHS **+0.028 ns**，0 失败端点 |
| 地址表与 hwh 对拍 | ✅ 12 个从设备逐条一致（含 `gpio_hdmi_dbg @ 0x41290000`）；现由 `--check-hwh` 离线复核 |
| 2026-10-31 重出片 | ✅ WNS **+3.937 ns** / WHS +0.036，0 失败端点（98,452 个终点），0 条 `[BD 41-1356]` |
| 2026-10-31 13:37 再出片 | ✅ WNS **+4.184 ns** / WHS +0.036，0 失败端点（98,531 个终点）；取数引擎重构 + 读地址改回 `xpos` |
| 上板（机器判据） | ✅ 2026-10-31 `hdmi_geom_probe.py`：行号 Σ=258,840 ✓、每个源行恰好 1 次（含 719）✓、源列 0 三次 ✓、VRAMP 累加和 MATCH ✓、`under=0`。LCD 侧 10-30 的 `bad=0/16` 仍成立 |
| 上板（人眼） | ⚠️ **`HDMI_VISUAL_OK` 已撤回（2026-10-31 20:23）**。13:56 用户当场确认「全对」（EDGES 屏左边缘纯白、右边缘纯红、绿方块居中；8 条横带/竖带顺序厚度正确、无黑横条、不跳动；45 横 + 80 竖线一条不少；运动方块无撕裂），当时记录为 `HDMI_VISUAL_OK`；**但 20:23 在同一片 bit 上复测又出现整行循环移位**（`px = c + 437` / `+441`），今日逐像素探针定量为恒定 **`+313` 列**（21 源列 × 7 行一致，`sum=1` 无丢点无重复，行映射正确）。⇒ 间歇性缺陷，证据文件 `data/results/hdmi_visual_ok.json` 现为 `HDMI_VISUAL_RETRACTED` |

## 6. 出片记录

每次出片追加一行：时间、结果、WNS/WHS、LUT/BRAM、令牌、失败原因。

| 时间 | 结果 | WNS | LUT | BRAM | 备注 |
|---|---|---|---|---|---|
| 2026-10-30 15:35 | FAIL | — | — | — | `create_bd_design` 报 `couldn't read file "init.tcl": No error` |
| 2026-10-30 16:01 | ✅ `FLOW_SYSTEM_BD_OK` | +3.042 ns（WHS +0.028） | 23,865 (44.9%) | 86/140 (61.4%) | 建图期间 `cd $ipintegrator` 后通过 |
| 2026-10-31 00:38 | ✅ 出片（含 dbg GPIO） | +3.937 ns（WHS +0.036） | — | — | HDMI 行号改取 `ypos-1`、末列修复、`ahead` 回绕修复、`st_dbg @ 0x41290000`；**未上板** |
| 2026-10-31 12:52 | ✅ 出片 | +3.845 ns（WHS +0.036） | — | — | 取数引擎改为请求/写入游标解耦 + 4 突发在飞，`BURST` 64→255；**上板**：黑横条消失、画面不跳 |
| 2026-10-31 13:37 | ✅ 出片 | +4.184 ns（WHS +0.036） | — | — | 读地址由 `max(xpos-2,0)` 改回 `xpos`（屏幕列 C ↔ 源列 C）；**13:50 上板、13:56 人眼「全对」**（当时记 `HDMI_VISUAL_OK`，**20:23 撤回**） |
| — | ❌ 无新片 | — | — | — | **20:23 同片复测出现整行循环移位**；今日探针定量恒定 `+313` 列。取址逻辑未改：问题在写侧该行起点不是源列 0；复现需「多笔在飞」的 AXI 从机 TB，**仿真复现前不出片** |

资源明细（2026-10-30 16:01 出片）：Slice Registers 31,255 (29.4%)、DSP 2、MMCM 2/4、
Bonded IOB 41/125。

> LUT 44.9% 比 §1 里「两个单独设计相加≈30%」高不少 —— 差在合并引入的
> `axi_smc`(10 MI) + `axi_clock_converter` + `hdmi_smc` + `hp_smc` 四个互连 IP。
> 仍在 50% 红线以内，但**再往里加东西前要先算互连的账**，不能按单体相加估。

## 7. 已知风险（上板前先读）

1. **两个 pixel 域的复位**：`rst_pix_h` 的 `dcm_locked` 取 `clk_wiz_hdmi/locked`，
   `rst_pix33`/`rst_pix9` 取 `clk_wiz/locked`。两个 MMCM 是独立锁定的；
   如果只接一块屏（比如没插 HDMI 线），另一个时钟仍在跑，不影响面板。
2. **HDMI 帧缓存在 DDR**：`allocate()` 出来的 buffer 是 cacheable，**必须 `flush()`
   再切基址**（2026-10-30 全白事故）。`flow_overlay.py` 用的是修过的 `HdmiVideoOut`。
3. **地址迁移的残留**：仓库外如果有脚本写死 `0x41200000 = gpio_id`，会打到 HDMI cfg 上。
   仓库内已全部改成按 IP 名解地址。

