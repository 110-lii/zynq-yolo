# 板上完整管线实测汇总（2026-10-24）

> 性质：**PC 侧留档的汇总记录**，不是原始串口/JSON 导出。
> 原始 `/tmp/out.json` 未保留（板上临时目录，已随会话结束丢失）；本文件的数字来自
> `docs/system_status.md` 与 `ai/sessions/010_board_pipeline_ball_backfill.md`。
> 已知缺口如实登记；补跑时应把 `--out` 指到持久目录并回传。

## 配置

| 项 | 值 |
|---|---|
| 平台 | 正点原子 领航者 ZYNQ 7020（PYNQ 2.7） |
| 比特流 | `board/overlay/preproc.bit`（PL 预处理） |
| 检测 | ncnn `person_v8n25`（YOLOv8n-0.25 @320，单类 person） |
| 标定 | `data/calib/field_calib.json` |
| 视频 | 202 帧已标定片段（中圈/半场） |
| 命令 | `python3 tools/board_pipeline.py --video <clips.mp4> --every 25 --max-det 0 --ball-every 2 --pl --calib data/calib/field_calib.json --proposal --out /tmp/out.json` |

## 结果

| 指标 | 值 |
|---|---|
| 输入帧 | 202 |
| 输出球场坐标的球帧 | **144** |
| 给出球权的帧 | **49** |
| `events` | 每帧输出；该片段 `out_of_play=false`（球未越线） |
| 球阶段耗时 | ~41 s |
| 总耗时 | ~53 s |
| 已知球确认（另一组 20 球评估，15 px 容差） | 16/20（80%） |
| PL 预处理 | 18.8 ms/帧（WNS +3.724 ns） |

## 已知边界

1. 单应只对已标定机位有效；未标定/INVALID 机位不输出坐标与事件。
2. 球在快速移动/遮挡时确认率下降；`--ball-every 2` 下 5 次丢失只覆盖约 0.4 s，
   backfill 结构改动未落地（`ai/sessions/010`）。
3. 角球/球门球需要最后触球队伍，当前只输出出界侧与最近球员。
4. 原始 `/tmp/out.json` 未回传；复跑时请用持久目录并归档。

## 复现

```bash
# 板端
python3 tools/board_pipeline.py --video <clips.mp4> --every 25 \
  --max-det 0 --ball-every 2 --pl --calib data/calib/field_calib.json \
  --proposal --out /home/xilinx/out.json

# PC 侧取回
scp xilinx@192.168.2.99:/home/xilinx/out.json data/results/board_pipeline_<date>.json
```
