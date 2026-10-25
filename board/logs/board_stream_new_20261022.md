# 板端整网实测：conv_engine 32-bit 字访存版（2026-10-22）

- 比特流：`board/overlay/conv_engine.bit`（HLS LUT 39,372；实现 WNS **+1.588 ns** @ 83.33 MHz）
- 输入：416×416，yolov5n（60 个 Conv）
- 单层验收：`BOARD_LAYER_OK all_6_cases`，聚合 **0.1156 GOP/s**（旧 0.0846）
- 整网：`FRAME_OK 0 det=2 total_ms=9633.9`

## 耗时分解

| 项 | 新版本 | 旧版本（20.8 s 基线） | 提升 |
|---|---:|---:|---:|
| conv | **8.13 s** | ~18.5 s | 2.27× |
| 其他（PS 首层/非卷积/后处理） | **1.45 s** | ~2.3 s | 1.6× |
| 合计 | **9.63 s** | 20.8 s | **2.16×** |

## 与周期模型对比

模型（TILE 1×40、PAR_OUT=8、w_lat 0/40/80）预测 conv 1.90/3.06/4.23 s，
实测 8.13 s = 677 M 周期，比最悲观档还高约 1.9 倍。

## 差距定位（按证据）

1. **权重装载退回为每 ci 72 次字读**。为让 HLS 能完成 RTL 生成，K≥2 路径从
   "3 字 + 96-bit 移位器" 退回 "扁平 72 次字读 + 32-bit 移位"。逻辑量降下来了，
   但每次 ci 多出约 48 次事务、且每个字节各自往返。
2. 32-bit 口实测上限 222 MB/s（`board_axi_bw_20261022.md`），但这里是**散读**，
   拿不到突发效率。
3. 输入解包（4 字节/拍）没有成为瓶颈：`VITIS_LOOP_164_12` II=1。

## 下一步（按收益排序）

1. **权重改 12 字节对齐记录**（主机侧补齐 + 内核 3 个对齐字读、静态取字节）：
   权重事务 72 → 24/ci，且无动态移位器，逻辑量小于当前版本，预计 conv 8.1 → 4–5 s。
2. 拆 `w` 到第二个读口（222 → 444 MB/s）。
3. 装载/计算 ping-pong 重叠。
4. 消除 `co0` 重复读（最多 8×）。

## 复现

```bash
# 板端监听
python3 src/ps/host/run_stream_test.py --frames 1 --out data/results/stream_client_new.json
# PC 侧推帧
python tools/stream_sender.py --host 192.168.2.99 --frames 1
```
