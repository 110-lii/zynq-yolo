# tb/ —— Verilog 测试台

> 上级：[`../../README.md`](../../README.md)

| | |
|---|---|
| 本目录装什么 | 模块级整场仿真 |
| 谁产生 | 手写 |
| 进 git | 是 |

## 内容

| 路径 | 说明 |
|---|---|
| `tb_fb_scanout.v` | D026 读侧：AXI4 读握手 / stride 换行 / 像素一致 |
| `common/` | 公共测试台片段 |
| `io/` | 显示通路 TB |
| `video/` | 视频通路 TB |

## 规则

- 判据写在 TB 里并打印唯一令牌；不要在脚本里猜日志文本

## 怎么用

- `py sim/scripts/run_sim.py --only fb_scanout`
