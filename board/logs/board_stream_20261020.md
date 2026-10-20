# 板端整网推流回传验收（2026-10-20）

## 结论

- **PC 推帧 → 板端 YOLOv8n@320 整网 → 检测 JSON + output0 回传** 已跑通。
- 连续 3 帧全部通过：`STREAM_BOARD_OK 3 28159.562`
- PC 侧判据：`STREAM_OK 3 29060.5 1.000000`
- 检测结果：2 × `person`，scores `0.68660945` / `0.92165077`
- 板端 output0 与 PC 参考对比：`cosine = 1.000000`，`max_abs_err = 5.96e-08`（float32 舍入级）

## 环境

- 板端：`192.168.2.99`，PYNQ 2.7，Python 3.8.2，numpy 1.17.4，32-bit ARM
- 输入：`data/samples/zidane.jpg`，PC 侧 cv2 resize 到 320×320 RGB，TCP 明文帧发送
- Overlay：`board/overlay/conv_engine.bit`（83.33 MHz）
- 计划：`data/layers/board_net_plan.json`（262 节点 / 64 Conv / 1 层 DFL 走 PS numpy 兜底）

## 逐帧耗时

| frame | det | total ms | conv ms | other ms |
|---|---|---|---|---|
| 0 | 2 | 28200.147 | 26729.509 | 1410.554 |
| 1 | 2 | 28199.261 | 26719.003 | 1421.293 |
| 2 | 2 | 28079.278 | 26669.276 | 1351.032 |

> 当前瓶颈是 63 层卷积的逐层 DDR 往返与 7020 的有限算力；约 **28.2 s/帧**，不是实时。

## 本次上板修掉的问题

1. **numpy 1.17 + 32-bit ARM**：`np.take()` 拒绝 int64 索引的安全转换 → 统一转 `np.intp`；同时补 `sliding_window_view` 兼容实现。
2. **sudo 后 XRT/PYNQ 环境丢失**：`run_board_test.sh` / 流测试启动前显式 source `/etc/profile.d/xrt_setup.sh` 与 `pynq_venv.sh`，并设置 `PYTHONPATH=/home/xilinx`。
3. **CMA 只剩 44 KB**：停止 `jupyter.service`、重启 `pl_server.service` 后恢复到约 35 MB，测试用的 1.12 MB 五缓冲分配成功。

## 证据

- `data/results/board_stream.json` — 板端 3 帧结果与耗时
- `data/results/stream_client.json` — PC 侧发送、接收与参考对比
- `tools/make_board_stream_pack.py` — PC 侧整网测试包/参考生成
- `tools/stream_sender.py` — PC 侧推帧与结果校验
- `src/ps/host/board_net.py` — 板端整网后端（PL 卷积 + PS 非卷积算子）
- `src/ps/host/run_stream_test.py` — 板端 TCP 服务
