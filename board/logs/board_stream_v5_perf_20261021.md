# YOLOv5n@416 板端速度优化：装载通路与权重流水（2026-10-21）

## 结论

- **端到端 41.4 s → 20.8 s/帧**（1.99x），三次改动都**逐值不改检测结果**。
- 第一轮：输入装载按 `K`/有效列裁剪 + AXI 未完成读 2→16 ⇒ 41.4 → 34.1 s。
- 第二轮：权重装载展平并 `PIPELINE II=1` ⇒ 34.1 → 25.7 s。
- 第三轮：PS 首层 int64 matmul → float32（部分和 < 2^24，逐位等价）⇒ 25.7 → 20.8 s。
- 仍未到 5–8 FPS；当前瓶颈是**每次字节访存的固定开销**（不是总字节数），下一步是 32-bit 打包装载。

## 环境

- 板端 `192.168.2.99`，PYNQ 2.7，FCLK0 83.33 MHz，bit `board/overlay/conv_engine.bit`
- 计划/数据：`data/layers/board_net_plan_v5_fb.json` + `stream_pack_v5_fb/`
- PC：`tools/stream_sender.py --image data/samples/calib_commons/fb_001_match_overcast.jpg --frames 1 --imgsz 416`

## 三次实测（同一帧、同一计划）

| 版本 | total ms | conv ms | 检测 | 备注 |
|---|---|---|---|---|
| 基线 p8_1_40_a0 | 41407.2 | 39093.7 | 7 person + 1 ball | `board_stream_v5_fb.json` |
| v1 装载裁剪 + outstanding=16 | 34149.5 | 31851.2 | scores 完全一致 | `stream_client_v5_fb_ld.json` |
| v2 + 权重流水 | 25733.2 | 23427.2 | scores 完全一致 | `stream_client_v5_fb_wld.json` |
| v3 + PS 首层 float32 累加 | 20798.8 | 18473.7 | scores 完全一致 | `stream_client_v5_fb_f32.json` |

> 检测 scores 三次均为 `0.8319, 0.8241, 0.6640, 0.7208, 0.3160, 0.8435, 0.8908, 0.8201`
> ⇒ 改动只动时序，不动数值。

## 单层复测（REPEAT=1，`board/scripts/run_board_test.sh`）

| case | 基线 ms | v1 ms | v2 ms |
|---|---|---|---|
| k1s1_smallw | 7.83 | 5.93 | 3.62 |
| k1s1_tailcout | 43.43 | 31.68 | 20.62 |
| k3s1_c3_40 | 5.88 | 5.59 | 5.02 |
| k3s1_cin512 | 133.45 | 108.75 | 79.89 |
| k3s1_pad0 | 8.65 | 7.35 | 5.96 |
| k3s2_c16_40 | 22.79 | 21.10 | 17.53 |
| **聚合 GOP/s** | **0.0505** | **0.0622** | **0.0846** |

## 资源与时序

| 配置 | DSP | BRAM18 | LUT | FF | 备注 |
|---|---|---|---|---|---|
| p8_1_40（基线） | 129 | 26 | 35385 | 36476 | HLS csynth |
| p8_1_40_ld（v1） | 129 | 21 | 40916 | 38585 | HLS csynth |
| p8_1_40_wld（v2） | 81 | 21 | 22988 | 22157 | HLS csynth |
| v2 实现后（整板） | 162 | 9.5 | 13076 | 30693 | `build/reports/resource/conv_engine_bd_util.rpt` |

- v2 时序：WNS **+1.254 ns** @ 10 ns（83.33 MHz），`build/reports/timing/conv_engine_bd_timing.rpt`
- v2 csim：6/6 `CSIM_ALL_OK`（TILE_H=1，逐位比对）

## 判据与证据

- `data/results/stream_client_v5_fb_ld.json` / `stream_client_v5_fb_wld.json` — PC 侧收到的 timings + 检测
- `data/results/hls_csim.json`、`data/results/hls_engine.json` — csim 与 csynth 汇总
- `board/logs/board_layer_test_20211118_0633.md` — v1 的单层原始日志；v2 同脚本复测（日志在板上 `board/logs/board_layer_test_<stamp>.log`）

## 下一步

1. **32-bit 打包输入装载**：直接把每行按 4 字节取回再拆，目标把 ~3 拍/字节降到 ~1 拍/字节。
2. **首层 416 去 PS 兜底**（5.75 s，占当前 25.7 s 的 22%）：Focus(2×2 重排) + 3×3 卷积。
3. 再评估 `TILE_W=80/104`（摊薄每 ci 的权重装载）与 `PAR_OUT=16`（当前实现 162 DSP，需先确认上限）。