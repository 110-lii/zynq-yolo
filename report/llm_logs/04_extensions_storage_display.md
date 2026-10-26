# 04 · 提交前扩展：PL 跟踪辅助、SD 存储、PC 软件、HDMI/LCD 面板

> 原始出处：本次会话（2026-10-25，客户端 `kilo`，模型 `magpie/group/1101`）；
> 代码落点与自检命令见文末。对应设计报告 §2.9、§4.7、§8 第 7–9 条。

## 1. 用户指令（逐字）

```
现在还是那个问题，你把所有的算法都放在了ps里，现在你在pl里放一些，跟踪的之类的，
还有就是完善一下，比方说sd卡存储，hdmi输出，lcd显示数据，电脑软件等等
```

拆成四件可验收的事：

1. **PL 里放跟踪相关算法**：不能只把 `preproc` 算作“PL 有算法”；
2. **SD 卡存储**：板上结果要能增量落盘、崩溃可恢复；
3. **HDMI/LCD 显示数据**：不是视频环出，而是把人数/球/事件显示出来；
4. **电脑软件**：接收/回放板端结果，画球场与事件。

## 2. 模型方案与关键取舍

### 2.1 为什么不是“在 PL 里实现 SORT”

SORT 的核心是**帧间数据关联 + ID 管理 + 异常分支**（IoU、Hungarian、丢失计数），
不是规则像素流水线；在 PL 里做完整 SORT 的收益很低、验证成本很高。可落地的是
**把跟踪最贵的像素级输入搬到 PL**：

- 原 `preproc`（BGR→320 RGB）与 `mask_preproc`（白/运动掩码）是两个 IP；
- PYNQ 同时只能加载一个 overlay ⇒ 两者不能同时用；
- 融合成 **`track_preproc`**：一次调用同时输出 RGB 平面 + 320×320 掩码，
  4 个 `m_axi` 接 HP0–HP3，PS 用掩码做“无运动的新轨迹”门控。

这是“PL 参与跟踪”，而不是把 SORT 硬塞进 PL；报告里也按这个口径写。

### 2.2 SD / PC / 显示

- **SD**：JSONL 追加 + `fsync` + 轮转 + `summary.json`，崩溃最多丢最后 N 行；
- **PC**：板端 `result_stream` 按行 TCP 推送，PC `pc_dashboard` 接收/回放，
  画 105×68 球场，支持 CSV 与 headless；
- **HDMI**：`status_display` 渲染 1280×720，`bram_fb` 打包 1bpp 写 BRAM
  （28,800×32，0x40000000），PL `fb_reader` + vendor TMDS 输出；已出片
  `hdmi_status.bit`；根因是 AXI BRAM 双端口 Port A=写/Port B=读，改单端口后
**DISPLAY_BOARD_OK**（读回一致），画面待目视。
- **LCD**：`src/rtl/io/lcd/`（1bpp BRAM 12,000×32 + vendor `lcd_driver`）已出片
  `lcd_status.bit`（Setup +4.283 ns）；板上同样 **DISPLAY_BOARD_OK**，画面待目视。
- **板上管线**：`PIPELINE_OK frames=20 detections=4 med_infer_ms=992.95`，
  proposal 3→2 名球员，`pl_ms=20.03 ms`；SD 20 行 + summary；PC 收到 20 帧。

## 3. 自我纠错轨迹

| # | 错在哪 | 怎么发现 | 怎么改 | 判据 |
|---|---|---|---|---|
| 1 | `track_preproc` 测试台的 argc/argv 下标写错（usage 6 个参数却判 `<8`） | 编译通过但运行只打印 USAGE | 改成 `<7`、`argv[5]/argv[6]` | `TRACK_TB_OK` + `TRACK_PREPROC_REF OK` |
| 2 | PC Dashboard 把第 0 帧显示成 `-1` | 合成 JSONL 回放第一行 `FRAME -1` | `frame=0` 是 falsy，改成显式 `is None` 判断 | 回放首行 `FRAME 0` |
| 3 | headless 回放在队列空时死循环 | 回放结束后进程不退出 | 用 `while not (replay and stop)` + `continue`，去掉 `except` 里的 `break` | `PC_DASHBOARD_OK frames=3` |
| 4 | 以为“状态面板能写 fb”就等于 HDMI 已支持 | 读硬件资料：HDMI/LCD 接 PL，PYNQ 镜像不一定注册 fbdev | 明确区分“渲染软件”与“显示 overlay”，在报告 §8 登记缺口 | `STATUS_DISPLAY_NO_FB` 会显式打印，而不是静默假绿 |
| 5 | 把 AXI BRAM Controller 当单端口用：只接 Port A，Port B 悬空 | 板上写入后读回恒为 `0x8`；查 v4.1 PG：双端口模式 **Port A=写、Port B=读** | 改 `C_SINGLE_PORT_BRAM=1`：AXI 用 Port A 读写，显示用 BRAM Port B | `DISPLAY_BOARD_OK`（HDMI 28,800 字、LCD 12,000 字读回一致） |

## 4. 验证与产物

| 项 | 命令 | 结果 |
|---|---|---|
| PL 算法对拍 | `python tools/check_track_preproc.py` | `TRACK_PREPROC_REF OK`（RGB/MASK 逐字节一致） |
| SD | `python tools/result_store.py --selftest` | `RESULT_STORE_SELFTEST OK` |
| PC | `python tools/pc_dashboard.py --selftest` + 本地回放 | `PC_DASHBOARD_SELFTEST OK` / `frames=3` |
| 板→PC | `result_stream` 本地 TCP 回环 | `RESULT_STREAM_OK sent=2` |
| 面板 | `python tools/status_display.py --selftest` | `STATUS_DISPLAY_SELFTEST OK` |
| HDMI 帧缓存 | `python tools/bram_fb.py --selftest` | `BRAM_FB_SELFTEST OK`（28,800 words） |
| HDMI overlay | `bd_hdmi_status.tcl` | `HDMI_STATUS_BD_OK`；Setup +3.938 ns；**DISPLAY_BOARD_OK**（画面待目视） |
| LCD overlay | `bd_lcd_status.tcl` | `LCD_STATUS_BD_OK`；Setup +4.283 ns；**DISPLAY_BOARD_OK**（画面待目视） |
| PL 跟踪上板 | `check_track_preproc_board.py` | `TRACK_PREPROC_BOARD_OK`；720p 32.39 ms |
| 板上管线 | `board_pipeline.py --pl-track`，20 帧 | `PIPELINE_OK`；4 次检测、992.95 ms；3→2 球员；pl_ms 20.03 |
| SD 板上 | 同一次运行 | 20 行 JSONL + summary |
| 板→PC | `result_stream` → `pc_dashboard` | `sent=20`、`PC_DASHBOARD_OK frames=20` |
| 仓库 | `python tools/check_repo.py` | `CHECK_REPO_OK` |

**已完成/未完成**：`track_preproc` 已完成 HLS csynth + BD 出片（LUT 7,374 / WNS +2.457 ns），
**上板逐位对通过**（`TRACK_PREPROC_BOARD_OK`，720p 32.39 ms）；板上管线、SD、PC 推送也通过。
**HDMI/LCD overlay 均已出片**（Setup +3.938/+4.283 ns）；根因是 AXI BRAM 双端口
Port A=写/Port B=读，改单端口后 **`DISPLAY_BOARD_OK`**（读回一致），实际画面待目视。
