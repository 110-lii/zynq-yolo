# 电脑推流到板子：怎么用（2026-11-01）

> 对应决策 **D029**（上电即用）与 **D027**（PC → 板的以太网视频入口）。
> 一句话：**板子上电后什么都不用做；在电脑上跑一条 `video_send.py` 就是推流。**

---

## 0. 已验证到哪一步（2026-11-01 实测）

| 判据 | 结果 |
|---|---|
| 服务装好 | `BOOT_SERVICE_STATE active active` |
| 白屏 | `IDLE_OVERLAY_OK` + `IDLE_HDMI_WHITE_OK frames=31 under=0` |
| **真重启后自启** | `reboot` 后 18 s：`IDLE_HDMI_WHITE_OK frames=35 under=0`，两个服务 `active` |
| 重启后推流 | PC 推 20 帧 → 板端 `PIPELINE_OK`、落盘 `net_*_00.jsonl` 20 行 |
| 白屏**人眼** | ⏳ **未确认** —— 机器只说明配置生效、扫描在跑；屏上是不是白的，要人看一眼 |

> 两个只有上板才会踩到的坑（已写进 `install_boot.py`）：
> PYNQ 的 pynq 在 `/usr/local/share/pynq-venv`，`/usr/bin/python3` 里没有；
> systemd 不读 Jupyter 的环境，必须 `Environment=XILINX_XRT=/usr`，否则 `No Devices Found`。

## 1. 先确认板子已经「开机就绪」

装了 D029 的服务之后，上电约 30–60 s 板子自动完成三件事：

1. 加载 `board/overlay/flow_system.bit`；
2. **HDMI 显示纯白**并一直保持（空闲待机；`zynq-idle` 服务常驻）；
3. 在 **5007/TCP** 等 PC 推流（`zynq-stream` 服务）。

在电脑上验一下（不通就是板子还没起来或掉线了，先上电重启）：

```bash
py tools/board_jupyter.py "import subprocess;print(subprocess.run('systemctl is-active zynq-idle zynq-stream',shell=True,capture_output=True,text=True).stdout)"
```

期望两行 `active`。若第一次还没装过服务，看 §5。

## 2. 推一段视频文件（最常用）

```bash
py tools/video_send.py --video data/samples/scenes/c03_t01290.0.mp4 --host 192.168.2.99 --port 5007 --fps 25
```

- 默认 **JPEG q95**。别改成 80：q80 会让板端多出 10 帧假球（球员白鞋被压缩成类球纹理，见 `ai/sessions/024` 轮次 7）。
- `--fps` 只是**上限**：板端 ncnn 约 0.98 s/帧，实际吞吐由板子反压决定（实测 2.2–2.3 fps）。
- 结尾会打印 `VIDEO_SEND_OK <帧数> <MB> <MB/s> <fps>`。

## 3. 推电脑摄像头（现场「实拍进板」）

```bash
py tools/video_send.py --camera 0 --host 192.168.2.99 --port 5007 --fps 15
```

⚠️ 摄像头分支**还没在板上联调过**（文件分支已验等价）。第一次用先在电脑上单跑，确认 OpenCV 能开摄像头。

## 4. 推完之后结果在哪

- 板端每处理完一段，落盘一份 JSONL：`/home/xilinx/zynq-yolo/build/flow/net_<时间戳>_00.jsonl`（250 行左右，每 15 帧一次检测）。
- 看服务日志（含 `PIPELINE_OK frames=… detections=…`）：

```bash
py tools/board_jupyter.py "import subprocess;print(subprocess.run('journalctl -u zynq-stream -n 60 --no-pager',shell=True,capture_output=True,text=True).stdout[-4000:])"
```

- **注意**：`board_pipeline --out xxx.json` 写的是**汇总 JSON**，不是逐帧 JSONL。要喂给 `video_back.py --tracks`，用 `build/flow/net_*_00.jsonl`。

## 5. 服务没装 / 想重装

```bash
py tools/install_boot.py --dry-run     # 只看要写什么，不碰板子
py tools/install_boot.py               # 推文件 + 装两个服务 + enable + 立即启动
py tools/install_boot.py --no-stream   # 只装 HDMI 白屏待机
py tools/install_boot.py --uninstall   # 撤掉（文件保留）
```

> 板和 PC 必须同一网段：板 `192.168.2.99`，PC `192.168.2.100`。
> SSH 免密不通是已知事实，所有板端操作都走 `tools/board_jupyter.py`（端口 9090）。

## 6. 推流时 HDMI 上看到什么

**还是白色。** 当前 `board_pipeline` 只做检测与落盘，不把收到的画面送到 HDMI；
HDMI 的带框回放（D026）走的是另一条 `hdmi_video.py` 路径，且该通路的人眼判据已撤回
（间歇整行旋转 `+313` 列），**演示与验收都不用它**。

要「边推边看」，现在有两条现成路子：

- **PC 侧**：推流结束后用 `tools/pc_dashboard.py --replay <JSONL>` 看那一段的分析画面；
- **网口回传**：板端另跑 `tools/video_back.py`（走 5006），PC 上 `pc_dashboard --listen` 接。
  这条路已上板验过（`VIDEO_BACK_OK`），但它需要**板本地**的视频源，所以是「板上跑文件 + 回传」，
  不是「PC 推流 + 回传」的闭环。**闭环目前没实现**，不要在现场说它是实时的。

## 7. 常见故障

| 现象 | 原因 | 处理 |
|---|---|---|
| `VIDEO_SEND_FAIL connect` | 板子没起、服务没装、端口被占 | 先 `journalctl -u zynq-stream`；再确认 5007 没被别的进程占用 |
| 推流卡住不动 | 板端在跑 ncnn（约 1 s/帧），TCP 反压 | 正常；看 `VIDEO_SEND_PROGRESS` 是否在涨 |
| 板端多出假球 | 用了 `--quality 80` | 用默认 95 |
| 白屏变黑/花 | `zynq-idle` 挂了（缓冲被释放） | `systemctl restart zynq-idle` |
