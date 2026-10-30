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

两条迁移是合并的代价：`0x4120/0x4121` 被 HDMI 占用（那两个地址是 `hdmi_video.py`
写死的，改它风险更大）。影响面：

- `tools/status_display.py`、`tools/lcd_ui.py`、`tools/hdmi_video.py`、`tools/bram_fb.py`
  —— **完全不受影响**，用的地址一个没动；
- `tools/lcd_probe.py` —— 本来就按 IP 名从 hwh 解地址（`find_ip`），自动跟随；
- `tools/check_lcd4_board.py` —— 已改成同样按 IP 名解地址（2026-10-30）。

## 4. 怎么用

出片（主机，十分钟级）：

```bash
cd /d D:\zynq-yolo && build_flow_system.bat
```

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
| 地址表与 hwh 对拍 | ✅ 11 个从设备逐条一致（见 §3） |
| 上板 | ❌ **未验**（板子 2026-10-30 反复硬复位，见 `docs/board_environment.md`） |

## 6. 出片记录

每次出片追加一行：时间、结果、WNS/WHS、LUT/BRAM、令牌、失败原因。

| 时间 | 结果 | WNS | LUT | BRAM | 备注 |
|---|---|---|---|---|---|
| 2026-10-30 15:35 | FAIL | — | — | — | `create_bd_design` 报 `couldn't read file "init.tcl": No error` |
| 2026-10-30 16:01 | ✅ `FLOW_SYSTEM_BD_OK` | +3.042 ns（WHS +0.028） | 23,865 (44.9%) | 86/140 (61.4%) | 建图期间 `cd $ipintegrator` 后通过 |

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

