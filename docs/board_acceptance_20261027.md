# 上板验收清单（2026-10-27 版）

> 板子 **192.168.2.99** 离线时，下面的每一项都只能做到「本机可验证」为止。
> 本文件是板子恢复后的**执行顺序与判据**；每步都有机器判据令牌 + 需要人眼确认的部分。
> 前置：板子已上电、PYNQ 镜像启动、PC 与板子同网段能互 ping。

## 0. 通板（10 s）

```bash
ping 192.168.2.99
ssh xilinx@192.168.2.99        # 免密不通时用 tools/board_jupyter.py（Jupyter 9090）
```

## 1. 两块屏（D020–D025）

```bash
# 合并版：一块 bit 上两块屏。三级阶梯按 white → chess → panel 走，别跳级
# 两个活体探针可以一起给：--lcd-gpio（像素内容）/ --hdmi-gpio（读口地址顺序）
sudo -E python3 tools/check_display_board.py --which both \
    --bit board/overlay/display_both.bit --pattern white \
    --lcd-gpio 0x41210000 --hdmi-gpio 0x41200000   # ① 信号通路：读回全 0xFFFFFFFF
sudo -E python3 tools/check_display_board.py --which both \
    --bit board/overlay/display_both.bit --pattern chess \
    --lcd-gpio 0x41210000 --hdmi-gpio 0x41200000   # ② 寻址/位序：64×64 棋盘格
sudo -E python3 tools/check_display_board.py --which both \
    --bit board/overlay/display_both.bit --pattern panel \
    --lcd-gpio 0x41210000 --hdmi-gpio 0x41200000   # ③ 状态面板
```
- 机器判据：`DISPLAY_BOTH_BOARD_OK bit=display_both.bit pattern=<级>`（两块 1bpp 帧缓存**逐字**读回一致）
- **2026-10-28 新增 LCD 活体探针**：给 `--lcd-gpio 0x41210000` 后，脚本还会读新挂的
  `dbg_gpio_lcd`（`{de,pixel_data}` / `{v_cnt,h_cnt}`），多出三条机器判据并打一行
  `DISPLAY_BOARD_LIVE OK ... pclk=33.3xxMHz`：
  – 像素时钟在 33.333 MHz ±5%（不用示波器就能认出是不是退回了 25）
  – `h_cnt` 扫到 ≥0.9×1056、`v_cnt` 在递增、`de` 占空 0.60–0.88
  – 采样到的白像素比例与刚写进帧缓存的比例同量级（差 3 倍以上判 FAIL）
  单独验 LCD 一行即可：
  ```bash
  sudo -E python3 tools/check_display_board.py --which lcd \
      --bit board/overlay/display_both.bit --pattern panel --lcd-gpio 0x41210000
  ```
- **2026-10-28 新增 HDMI 活体探针**：给 `--hdmi-gpio 0x41200000` 后读 `dbg_gpio`
  （`bram_addr` / `{de,pixel_ypos}`），打一行 `DISPLAY_BOARD_HDMI_LIVE OK ... fps=59.x`：
  – 地址覆盖到帧尾（`addr_max ≥ 28600`）且行号到 720 —— 证明整场 28,800 字都被扫到
  – `de` 占空 0.60–0.88（1280/1650≈0.776）
  – **用地址推进量反推帧率 60±5**：读口若停住或跳着扫，帧率立刻出窗；
    单个撕裂样本用「越界即丢弃、`Δ>500` 不计入总量」剔除，不影响判据
- **2026-10-28 三级阶梯全部通过**（机器判据）：
  `DISPLAY_BOTH_BOARD_OK pattern=white/chess/panel`；chess 级 LCD `pclk=33.357MHz v_uniq=524`、
  HDMI `fps=59.2 addr_max=28799 ypos_max=720`；见 `ai/sessions/018`、`019`
- **人眼判据**：white 级整屏发亮；chess 级两块屏都是 64×64 棋盘格；panel 级 8 项信息都在、中文不乱码
- 2026-10-27 已补：合并版 BD 补齐 014 的三处修复（`rstb` 接常量 0、两侧 `dcm_locked`、
  LCD 走独立 clk_wiz）。**2026-10-28 更正：那个独立 clk_wiz 必须出 33.333 MHz 而不是 25 MHz** —— 25 MHz 配 1056×525 只有 45.1 Hz，实测表现为「画面能出但几何全错」；见 `ai/sessions/016_lcd_refresh_rate_and_diag.md`），并重出 `display_both.bit`
- 兜底：合并版若仍有单块屏不对，退回 `hdmi_status.bit` / `lcd_status.bit` 分别验（这两块 014 已人眼确认棋盘格）

## 2. LCD 状态面板信息（本轮新增 8 项）

```bash
python3 tools/status_display.py --replay /home/xilinx/out.json --size 800x480 --fb /dev/fb1
```
- 机器判据：无异常退出；无 fbdev 时用 `--png-out` 出 PNG
- **人眼判据**：8 项都在（帧/人数/球/事件/机位/存储/PL/运行），中文不乱码
- 本机已核对过渲染 PNG；上板只差 fbdev/BRAM 通路

## 3. HDMI 视频环出（本轮新增，D026）

```bash
# 3.1 先跑彩条（不依赖任何检测数据）
sudo -E python3 tools/check_hdmi_video_board.py --bit /home/xilinx/hdmi_video.bit --seconds 3
# 3.2 再放带框画面
python3 tools/hdmi_video.py --replay /home/xilinx/run.jsonl --video /home/xilinx/inc_12.mp4 --fps 8
```
- 机器判据：`HDMI_VIDEO_BOARD_OK frames=<n> under=<m>`，`frames` 在涨、`under` 不随帧数线性增长
  （`under` 是行欠载计数，非 0 说明 DDR 读跟不上扫描）
- **人眼判据**：8 色彩条 + 移动白块 → 带框比赛画面（绿框、球红圈、顶部事件条）
- 若 `under` 持续增长：先看 §5 的带宽/时钟问题（HP0 ACLK 与 pclk 必须同源）

## 4. SD 证据帧（本轮新增）

```bash
python3 tools/board_pipeline.py --video /home/xilinx/inc_06.mp4 --every 25 --max-det 0 \
    --ball-every 2 --pl-track --proposal --calib data/calib/field_calib.json \
    --store-dir /home/xilinx/run_out --store-evidence --store-fsync 30 --out /home/xilinx/out.json
python3 tools/result_store.py --selftest          # 板上也跑一遍
```
- 机器判据：`STORE_EVIDENCE written=<n> skipped=<m> errors=0`；`run_out/evidence/*.jpg` 存在且能打开；
  JSONL 里 `type=evidence` 行的 `path` 是**相对路径**，从 store 根能解析到真实文件
- 注意：`out` 证据帧要靠 `events.out_of_play=true` 才会产生，**需要有球越线的片段**；
  没有越线片段时只会出现 `fall` 证据帧（倒地候选本来就是候选，不是检出）

## 5. 板上 → PC 推送 + 上位机

```bash
# 板端
python3 tools/result_stream.py --file /home/xilinx/run_out/run_*.jsonl --host <PC_IP> --port 5005 --follow
# PC 端
py tools\pc_dashboard.py --listen 0.0.0.0:5005 --csv session.csv
```
- 机器判据：PC 端 `PC_DASHBOARD_OK frames=<n>`，CSV 行数与推送一致
- **人眼判据**：球场画布上的点在动、右侧事件流出现条目、证据帧缩略图能显示

## 6. 已知未做 / 不许含糊的点

| 项 | 现状 |
|---|---|
| 出界方向的真实验证 | 仍缺**已标定机位 + 真实越线片段**；12 段 incident 里出界瞬间常切近景，球不在画面 |
| 球衣颜色分队 / 触屏录入 UI | 未实现；球权是「3 m 内离球最近的球员 track_id」，不带队别 |
| 角球 / 球门球分类 | 未实现；事件只有出界方向 + 进球 + 球权 |
| 倒地 | 只给候选（4 帧门槛 4 候选 = 3 真 1 假），**不是检出率** |
| 实时性 | 检测 0.95–1.0 s/帧 ≈ 1 FPS；HDMI 环出是「慢帧快扫」，不是实时视频 |