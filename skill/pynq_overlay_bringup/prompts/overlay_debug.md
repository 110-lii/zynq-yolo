# 提示词模板：PYNQ Overlay 上板排查

> 用法：把 `{...}` 替换成你的实际信息；一次只问一个故障，附上**完整报错 + 最小代码 + `.hwh` 片段**。
> 这些模板来自本队真实排障（`ai/sessions/003/004/007/009`），不是泛泛的“请教我 PYNQ”。

## 模板 1：Overlay 加载失败

```text
环境：PYNQ {2.7}，板卡 {XC7Z020}，Vivado {2020.2}，overlay 文件 {xxx.bit/.hwh} 来自同一次构建。
现象：Overlay("{xxx.bit}") 报错：
{完整 traceback}
我已经确认：
1) .bit 与 .hwh 同名同目录；2) 文件大小 {N}；3) 板端 pynq 版本 {..}。
请按“加载阶段 → IP 识别 → 地址窗口”三段定位，并给出下一步只读检查命令；
不要建议我改寄存器，先确认 overlay 是否真的加载成功。
```

## 模板 2：`KeyError: s_axi_control_r`

```text
HLS IP 导出的 .hwh 里有 {N} 个 ADDRESSBLOCK；Python 侧按 s_axi_control_r 取寄存器时报 KeyError。
ovl.ip_dict 输出：{粘贴}
.hwh 的 ADDRESSBLOCK/INTERFACE 列表：{粘贴}
请判断是 BD 漏接控制窗口、还是 HLS 改名/多窗口命名不同；
给出“从 .hwh 解析寄存器、不硬编码名字”的最小改法。
```

## 模板 3：逐位对随机失败

```text
IP 是 {功能}，输入 {N} 字节，输出 {M} 字节；小尺寸逐位对通过，全尺寸偶尔失败。
代码顺序：allocate → 写输入 → {flush?} → 写参数 → start → 轮询 done → {invalidate?} → 读输出。
请按缓存一致性、64 位指针低/高 32 位、done 轮询条件、DMA 未完成四种可能排序，
并给出一个能区分它们的最小实验（不要一次改多处）。
```

## 模板 4：吞吐与理论不符

```text
实测 {X} MB/s，理论 {Y} MB/s（{位宽} × {频率}）。
HLS 顶层指针类型：{ap_uint<8>* / ap_uint<32>*}；
m_axi 约束：{num_read_outstanding / max_read_burst_length / bundle}。
请先判断瓶颈是“事务粒度、条件读、outstanding、还是 DDR 往返延迟”，
给出一个只改一个变量的对照实验。
```

## 模板 5：板子反复掉线

```text
板子跑 {负载} 时 uptime 只有 {1–5} 分钟；串口日志：{粘贴}；无 panic/Oops/MMC 错误。
请给出“供电/散热/整机复位/网络”四类证据的采集顺序，
以及一个 20 分钟满载压测的最小脚本框架；不要先归因于网络。
```
