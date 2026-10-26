# track_preproc 上板验证（2026-10-25）

> 板子：192.168.2.99（PYNQ 2.7 / Linux 5.4.0），overlay：`board/overlay/track_preproc.bit`。
> 运行：`sudo` + `PYTHONPATH=/usr/local/share/pynq-venv/lib/python3.8/site-packages`。
> **判据是逐字节对拍，不是“能加载”**。

## 命令

```bash
cd /home/xilinx/zynq-yolo
PYTHONPATH=/usr/local/share/pynq-venv/lib/python3.8/site-packages \
  python3 tools/check_track_preproc_board.py --repo /home/xilinx/zynq-yolo
```

## 结果

```text
TRACK_BOARD_RGB True
TRACK_BOARD_MASK True
TRACK_BOARD_MS 18.401
TRACK_BOARD_720P_MS 32.39
TRACK_PREPROC_BOARD_OK
```

- RGB 平面与 Python 参考**逐字节一致**；
- 白/运动掩码与 Python 参考**逐字节一致**；
- 64×64 合成帧调用 18.4 ms（含 AXI/启动开销）；
- 1280×720 连续 3 次平均 **32.39 ms/帧**（PL 融合预处理 + 掩码的板端实测）。

## 结论

`track_preproc` **上板逐位对通过**；`--pl-track` 可以进入主线。
注意：32.39 ms 比旧 `preproc` 的 18.8 ms 慢，因为多读一帧 prev + 多写掩码；
这是“把运动掩码放进 PL”的实测代价，报告 §4.7 已记录。