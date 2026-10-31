# 演示视频制作方案（门户要求：MP4 / H.264 / ≤3 min / ≤300 MB）

> 状态（2026-10-31）：**尚无演示视频**。本文件给拍摄清单、分镜、剪辑命令与验收判据。
> 参考样片已提供（102 s / 640×432 / H.264），只说明风格：实物开场 → 手部操作特写 →
> 屏幕/界面 → 字幕说明；**不包含**学校、指导教师等信息。
> 输出建议放 `dist/demo_<YYYYMMDD>.mp4`，不随代码包入库。

---

## 1. 门户硬要求

| 项 | 要求 | 验收命令 |
|---|---|---|
| 容器/编码 | MP4 / AVC（H.264） | `ffmpeg -i demo.mp4` 看 `Video: h264` |
| 时长 | ≤ 3 分钟 | `ffmpeg -i demo.mp4` 看 `Duration` |
| 大小 | ≤ 300 MB | 文件属性；建议 < 100 MB |
| 内容 | 不得出现学校名称、指导教师姓名等无关信息 | 逐帧/字幕人工检查 |

---

## 2. 必须补拍的实拍素材（约 10 分钟原始素材）

| # | 镜头 | 时长 | 要点 |
|---|---|---|---|
| 1 | 开发板正面 + 斜 45° 全局 | 15 s | 板卡、HDMI 线、LCD 排线、电源都在画面内；光线均匀 |
| 2 | 上电 + 两屏点亮 | 10 s | HDMI 与 LCD 同时亮；不要拍到桌面杂物/校徽 |
| 3 | LCD 状态面板（8 项） | 8 s | 人数/球/事件/机位/时间等；镜头贴近屏 |
| 4 | 赛前 UI 按键操作 | 12 s | 手指按 `KEY0/KEY1` 切换球衣色并确认，屏幕有反馈 |
| 5 | HDMI 静态六屏 | 18 s | EDGES / HBANDS / VBANDS / GRID / MOTION / MARKERS，每屏约 3 s |
| 6 | HDMI 带框真视频环出 | 15 s | 画面里有球员绿框、球红圈、顶部事件条 |
| 7 | PC Dashboard | 12 s | 球场画布、事件流、证据帧、CSV；屏录即可 |
| 8 | SD 卡 JSONL / summary | 6 s | 文件列表与一行 JSON 特写；可用屏录 |
| 9 | 指标图 | 8 s | `report/figures/optimization.png`、`pc_pitch.png` 做画中画/全屏 |

> 镜头 3–6 若板子稳定，建议一条长镜头拍完再剪，避免反复上下电。
> 若 HDMI 实时叠框回放尚未人眼确认，镜头 6 只拍**静态六屏**，并在字幕写「静态画面验收」。

## 3. 可直接使用的既有素材

| 文件 | 时长/分辨率 | 用途 |
|---|---|---|
| `build/viz/annotated.mp4` | 15.1 s / 1280×720 | 检测框 + 跟踪 ID 的算法效果 |
| `build/viz/annotated_stable.mp4` | 15.1 s / 1280×720 | 稳定版跟踪 |
| `build/ev_inc01_h264.mp4` | 10.0 s / 640×360 | 事件片段（已是 H.264） |
| `build/ball_clip.mp4` | 9.5 s / 1280×720 | 球链路片段 |
| `build/match_clip.mp4` | 15.5 s / 1280×720 | 原始比赛片段 |
| `report/figures/*.png` | — | 框图、指标、界面截图 |

> 这些是 PC 侧离线结果，字幕要写清「PC 侧离线处理」，不要当成板上实时结果。

## 4. 分镜（总长约 2 min 40 s）

| 时间 | 画面 | 字幕/旁白 |
|---|---|---|
| 0:00–0:08 | 标题卡：作品名 + 一句话 | 基于 Zynq-7020 的足球转播视频目标与事件分析系统 |
| 0:08–0:25 | 镜头 1 + 2（实物全局、两屏点亮） | 一块 7020，同时驱动 LCD 状态面板与 HDMI 视频 |
| 0:25–0:45 | 镜头 3 + 4（LCD 面板、赛前 UI） | 赛前选球衣色 → 板上按键确认 → 状态面板常驻 |
| 0:45–1:05 | `annotated.mp4` + `ball_clip.mp4` | 球员检测/跟踪、足球候选与轨迹确认（PC 侧离线） |
| 1:05–1:25 | 镜头 6 + 5（HDMI 环出、静态六屏） | 帧缓存 → fb_scanout → TMDS；六屏几何/颜色逐项验收 |
| 1:25–1:45 | 镜头 7 + 8（Dashboard、SD） | 结果落 SD，TCP 推 PC；球场画布与事件流 |
| 1:45–2:05 | 镜头 9（指标图） | 18.8 ms PL 预处理、0.95 s PS 检测、球确认 80% |
| 2:05–2:30 | 架构图 + 关键取舍 | 为什么不在 PL 跑整网；两级显示判据 |
| 2:30–2:40 | 结尾卡 | 已知边界：视频入口仍为本地文件；实时叠框回放待目验 |

## 5. 剪辑命令（本机 ffmpeg 7.1）

```bash
FF=/d/yt-dlp/ffmpeg/ffmpeg.exe
# 1) 统一到 1280x720 / 30fps / H.264 / yuv420p（无音频或统一 AAC）
"$FF" -i raw_01.mp4 -vf "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,fps=30" \
  -c:v libx264 -preset medium -crf 20 -pix_fmt yuv420p -an clip_01.mp4

# 2) 标题卡（中文字体用系统雅黑）
"$FF" -f lavfi -i color=c=0x0b1f3a:s=1280x720:d=8 -vf \
  "drawtext=fontfile='C\\:/Windows/Fonts/msyh.ttc':text='基于 Zynq-7020 的足球转播视频目标与事件分析系统':fontcolor=white:fontsize=40:x=(w-text_w)/2:y=(h-text_h)/2" \
  -c:v libx264 -crf 20 -pix_fmt yuv420p -an title.mp4

# 3) 拼接（先写 list.txt，每行 file 'clip.mp4'）
"$FF" -f concat -safe 0 -i list.txt -c copy demo_raw.mp4

# 4) 最终验收
"$FF" -i demo_raw.mp4 2>&1 | grep -E "Duration|Video:"
```

> 字幕建议烧录在画面底部（`drawtext` 或剪辑软件），避免外挂字幕平台不识别。
> 背景音乐如使用，必须确认无版权问题；也可以不加。

## 6. 提交前检查

- [ ] `ffmpeg -i` 显示 `Video: h264`，`Duration` ≤ 00:03:00
- [ ] 文件 ≤ 300 MB（建议 ≤100 MB）
- [ ] 全片无学校名称、指导教师姓名、校徽、实验室门牌
- [ ] 板上画面与 PC 离线画面在字幕里区分清楚
- [ ] 不把「机器判据通过」写成「人眼已确认」
