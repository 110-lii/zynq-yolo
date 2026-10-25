# Session 008 — conv_engine 访存口径重写（字节事务 → 32-bit 字）

| 字段 | 内容 |
|---|---|
| 会话号 | 008 |
| 日期 | 2026-10-22 |
| 执行 AI（模型 ID） | `group/1101` |
| 执行客户端 / 工具 | `kilo`（CLI，Windows 10 / PowerShell） |
| 角色 | 单人 |
| 任务目标 | 把 conv_engine 里与 AXI 理论带宽不符的访存写法全部改掉：字节事务、条件读、缺 outstanding、权重散读 |
| 上下文投喂 | `board/logs/board_stream_v5_perf_20261021.md`（20.8 s/帧、读吞吐 10–22 MB/s）、`ai/sessions/005`（772 拍/ci 的分解） |
| **原文位置** | **待导出**（CLI 会话） |
| 关联决策 | 待登记 |
| 复核方 | 未复核 |

## 问题定位（代码级，不靠推断）

`src/hls/accel/src/conv_engine.cpp` 旧版四处与「AXI 满带宽」理论不符：

1. `in`/`w` 是 `ap_uint<8>*`/`ap_int<8>*` ⇒ m_axi 事务粒度 1 字节；
   实测读吞吐 10–22 MB/s，而 32-bit @83.33 MHz 的理论上限是 333 MB/s。
2. 装载循环里 `ok ? in[addr] : zp` 把读地址挂在条件上 ⇒ 无法合并突发。
3. `w` 端口没有 `num_read_outstanding` / `max_read_burst_length`。
4. 权重按 `[co][ci][9]` 散读，每个 ci 要 72 次独立单字节读，实测 ~720 拍/ci。

## 改动

| 位置 | 改法 |
|---|---|
| 顶层签名 | `in`→`ap_uint<32>*`，`w`→`ap_int<32>*`（字节布局不变，PS 侧无需改数据） |
| 输入装载 | 按整字覆盖窗口 `[g0, g0+ncol)`，读地址连续且**无条件**；越界只做数据选择 |
| 权重装载 | K=1 读 1 字；K≥2 读 3 字（12 B）拼 `ap_uint<96>` 后按记录对齐偏移右移 |
| 尾块 | 一律「下标钳位 → 读 → 数据清零」，不再条件读 |
| AXI | 读口 `num_read_outstanding=32`、`max_read_burst_length=64`；写口补 outstanding/burst |

## 验证

| 项目 | 结果 |
|---|---|
| 主机 g++ 编译（`-fsyntax-only`） | 通过，无告警 |
| 逐位比对（TB + 6 个用例，g++ 直跑，等效 csim 数值） | **6/6 TB_CONV_OK**（1600 / 8000 / 25600 / 1600 / 5184 / 12800） |
| csynth（II / LUT / 时序） | **未跑**（需批准） |
| 板端吞吐 | **未测** |

## 预期与遗留

- 只改事务粒度与突发、不改算术：预期单层 772 拍/ci → 约 70–120 拍/ci。
- HLS 调度实测（2026-10-22）：输入装载 `:164`、卷积 `:229`、写回 `:261`、LUT `:97` 全部 **Final II = 1**。
- 周期模型（yolov5n@416，60 层，0.944 GMAC，TILE 1×40、PAR_OUT=8、每 ci = 输入 + 24 字权重 + 40 拍计算）：
  | 权重延迟暴露 | 总周期 | @83.33 MHz | 相对 20.8 s |
  |---|---|---|---|
  | 0 拍 | 158 M | 1.90 s | 11.0× |
  | 40 拍 | 255 M | 3.06 s | 6.8× |
  | 80 拍 | 353 M | 4.23 s | 4.9× |
- 结论：装载与计算串行是剩余差距的主因（每 ci 只有 40 拍在算），宽度改造到此为止，下一步要么 DATAFLOW 重叠，要么拆 bundle/预取权重。
- 遗留 1：`co0` 在最外层，输入 tile 被重复读 `C_OUT/PAR_OUT`（最多 8）次。
- 遗留 2：in/w 共用一个 `gmem_r` bundle（单 HP 口），拆 bundle 可再翻倍（参考 007 掩码 v4 的 3-bundle 做法，54.6→16.1 ms）。
- 遗留 3：装载与计算串行，无重叠。

## 产出

| 路径 | 说明 |
|---|---|
| `src/hls/accel/src/conv_engine.cpp` | 重写访存路径 |
| `src/hls/accel/tb/tb_conv_engine.cpp` | 原型与调用改为 32-bit 字指针 |
| `docs/accel_interface.md` §4 | 签名与对齐约束同步 |
