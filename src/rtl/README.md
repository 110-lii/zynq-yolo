# rtl/ —— 手写 RTL（显示/视频/公共模块）

> 上级：[`../README.md`](../README.md)

| | |
|---|---|
| 本目录装什么 | 不属于 HLS 的 RTL：显示读侧、时序、公共模块 |
| 谁产生 | 手写 |
| 进 git | 是 |

## 内容

| 路径 | 说明 |
|---|---|
| `io/hdmi/` | HDMI 状态面板与带框环出 |
| `io/lcd/` | LCD 状态面板与 4bpp 探针 |
| `common/` | 时钟/复位等公共模块 |
| `video/` | 历史视频采集 RTL（D015 后退出主线） |

## 规则

- 同一 bitstream 里不要混用两套时钟相位假设：像素时钟必须由 `clk_wiz`/MMCM 显式产生
- 改任何读侧都要跑 `sim/scripts/run_sim.py` 对应用例

## 怎么用

- `py sim/scripts/run_sim.py` → 期望 `SIM_PASS`
