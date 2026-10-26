# 跟踪 + HDMI/LCD 合并 overlay（D024）上板验证

> 板子：192.168.2.99；overlay：`board/overlay/track_display.bit/.hwh`。
> 一个 overlay 同时跑 `track_preproc` 和 HDMI/LCD 双屏，避免换 overlay。

## 地址/接口

| 功能 | 地址 | 说明 |
|---|---|---|
| `track_preproc_0/s_axi_control` | `0x40000000` | HLS 控制 + W_IN/H_IN/threshold |
| HDMI 1bpp BRAM | `0x40020000`（128 KB） | 28,800×32-bit |
| LCD 1bpp BRAM | `0x40040000`（64 KB） | 12,000×32-bit |
| track 数据 | HP0–HP3 | in / rgb / prev / mask |

资源/时序：LUT **7,984** / FF **8,835** / DSP 13 / BRAM **51**；
Setup **+2.918 ns**、Hold **+0.036 ns**、PW +4.870 ns，0 failing。

## 上板结果

`track_preproc` 在组合 overlay 上逐字节对拍：

```text
TRACK_BOARD_RGB True
TRACK_BOARD_MASK True
TRACK_BOARD_MS 18.500
TRACK_BOARD_720P_MS 32.11
TRACK_PREPROC_BOARD_OK
```

双屏 BRAM 读回：

```text
IPS ['processing_system7_0', 'track_preproc_0']
HDMI words 28800 probe 1480 exp 0xfc000000 got 0xfc000000 OK
LCD  words 12000 probe 450  exp 0xfc000000 got 0xfc000000 OK
TRACK_DISPLAY_BOARD_OK
```

管线在同一 overlay 上跑通：

```text
PL_TRACK enabled
STORE_SUMMARY /tmp/live/summary.json
STAGES detect_ms=5050 ball_ms=2274 frames=20 pl_ms_med=20.04
PIPELINE_OK frames=20 detections=4 med_infer_ms=1055.95
```

写双屏：

```bash
python3 tools/status_display.py --replay /tmp/live/run_*.jsonl --size 1280x720 \
  --display-bit board/overlay/track_display.bit \
  --fb-bram 0x40020000 --also-lcd-base 0x40040000 --fps 1 --max-frames 3
```

输出 `STATUS_DISPLAY_BRAM 0x40020000` + `STATUS_DISPLAY_LCD_BRAM 0x40040000`。

## 结论

- **跟踪辅助与 HDMI/LCD 双屏可以同时工作**；
- 组合 overlay 上 track 逐位对、双屏读回、20 帧管线均通过；
- 实际两块屏幕画面仍以目视为准；数据链路已通过。