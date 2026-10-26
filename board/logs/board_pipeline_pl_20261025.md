# 板上 `--pl-track` + SD + PC 推送验证（2026-10-25）

> 板子：192.168.2.99，PYNQ 2.7；overlay：`track_preproc.bit`。
> 运行方式：`sudo` + `PYTHONPATH=/usr/local/share/pynq-venv/lib/python3.8/site-packages`。
> 数字来自板上 stdout 与 `/tmp/sdtest3`、`/tmp/pipeline_match2.json`。

## 1. 命令

```bash
cd /home/xilinx/zynq-yolo
sudo env XILINX_XRT=/usr PYTHONPATH=/usr/local/share/pynq-venv/lib/python3.8/site-packages \
  python3 tools/board_pipeline.py --video /home/xilinx/match_clip.mp4 \
  --max-frames 20 --every 5 --ball-every 2 --pl-track --motion-gate \
  --calib data/calib/field_calib.json --proposal \
  --store-dir /tmp/sdtest3 --out /tmp/pipeline_match2.json
```

## 2. 结果

```text
PL_TRACK enabled
STORE_DIR /tmp/sdtest3
STORE_SUMMARY /tmp/sdtest3/summary.json
STAGES detect_ms=4572 ball_ms=2118 frames=20 pl_ms_med=20.03
PIPELINE_OK frames=20 detections=4 med_infer_ms=992.95 out=/tmp/pipeline_match2.json
```

- `pl_track=True`，每帧 `pl_ms` 约 **20.0 ms**（640×360 输入；720p 基准为 32.39 ms）；
- 20 帧中 4 次检测，检测中位 **992.95 ms**（ncnn，与既有约 1 s 口径一致）；
- proposal 中前段 **3** 个球员、后段 **2** 个球员；机位状态从 LIMITED 切到 INVALID 再回 LIMITED；
- 球在该 match 片段未检出（该片段不是球链路用的近景球片段，符合预期）；
- `ResultStore`：20 行 JSONL + `summary.json`，`counts.frame=20`，`elapsed_s=10.8`。

## 3. SD 文件

```text
/tmp/sdtest3/run_20211119_063252_00.jsonl   20 行
/tmp/sdtest3/summary.json                  frames=20 detections=4 pl_track=true pl_ms_med=20.03
```

## 4. PC 推送

PC（192.168.2.100）运行：

```powershell
python tools\pc_dashboard.py --listen 0.0.0.0:5010 --headless
```

板端：

```bash
python3 tools/result_stream.py --file /tmp/sdtest/run_*.jsonl \
  --host 192.168.2.100 --port 5010 --from-start --rate 0.05
```

结果：

```text
RESULT_STREAM_CONNECTED 192.168.2.100:5010
RESULT_STREAM_OK sent=20
PC_DASHBOARD_OK frames=20
```

## 5. 结论

- **PL 跟踪辅助进入主线可用**：`--pl-track` 真的加载并运行 `track_preproc`，每帧 20 ms；
- **SD 增量存储可用**：JSONL + summary 落盘，20 帧无丢失；
- **PC 接收可用**：板→PC TCP 20 帧，Dashboard headless 收到 20 帧。
- HDMI/LCD 显示仍未通过（见 `board_display_20261025.md`），不要把本条与显示混淆。