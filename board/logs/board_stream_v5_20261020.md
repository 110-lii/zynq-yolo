# YOLOv5n @416 板端推流回传验收（2026-10-20）

## 结论

- **PC 推流 → 板端 YOLOv5n@416 整网 → 检测 JSON + output0 回传** 已跑通。
- 真实足球场景 `fb_001_match_overcast.jpg` 板端检出 **7 个 person + 1 个 sports_ball**。
- 板端 output0 与 PC 参考：`cosine = 1.000000`，`max_abs_err = 6.1e-05`（float32 舍入级）。
- 当前 416 首层仍走 PS numpy 兜底（`H_IN=416 > 320` 且 `K=6` 不被引擎支持，实测 5.75 s），因此约 **41.4 s/帧**；这是功能验证，不是性能版。

## 模型

- 经典 **YOLOv5n**，输入 416×416，ONNX `data/models/yolov5n.onnx`
- **1.888 GOP/帧**，60 个 Conv，**全部标准卷积，无 depthwise/group**
- 输出 `output0 = [1, 10647, 85]`；xywh 已是 416 像素坐标，obj/cls 已 sigmoid
- 首层 `3→16, k6s2, 416→208` 同时超出当前引擎 `H_IN<=320` 与 `K∈{1,3}` 限制，暂由 PS NumpyBackend 执行（实测 5.75 s）；其余 59 层在 PL

## 测试 1：zidane.jpg

- 板端：`STREAM_BOARD_OK 1 41315.536`
- PC：`STREAM_OK 1 41976.7 1.000000`
- 检测：2 × person（0.8153 / 0.3135）
- 耗时：conv 39006.9 ms，other 2205.0 ms，total 41315.5 ms
- 对比：`cosine = 1.000000`，`max_abs_err = 9.16e-05`

## 测试 2：fb_001_match_overcast.jpg

- 板端：`STREAM_BOARD_OK 1 41407.236`
- PC：`STREAM_OK 1 42269.4 1.000000`
- 检测：**7 × person + 1 × sports_ball**
  - person scores：0.824, 0.664, 0.721, 0.316, 0.843, 0.891, 0.820
  - sports_ball score：0.832
- 耗时：conv 39093.7 ms，other 2211.6 ms，total 41407.2 ms
- 对比：`cosine = 1.000000`，`max_abs_err = 6.1e-05`

## 证据

- `data/results/board_stream_v5.json` / `data/results/stream_client_v5.json`（zidane）
- `data/results/board_stream_v5_fb.json` / `data/results/stream_client_v5_fb.json`（足球场景）
- `data/layers/board_net_plan_v5.json` + `data/layers/stream_pack_v5/`（zidane 标定）
- `data/layers/board_net_plan_v5_fb.json` + `data/layers/stream_pack_v5_fb/`（足球场景标定）
- `tools/make_board_stream_pack.py` / `tools/stream_sender.py` / `src/ps/host/board_net.py`

## 下一步

1. 把首层 416 改成 PL 行带拆分（2 条 209 行输入带，输出 2×104 行），去掉 PS 兜底。
2. 加速器改为 `PAR_OUT=16` + 行缓冲/双缓冲，目标 **5–10 FPS**。
3. 用足球数据微调 YOLOv5n（2 类），验收 person recall ≥0.90。