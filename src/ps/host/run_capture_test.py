#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_capture_test.py -- 板上 M1 采集链验收（PYNQ 路径，真板跑 + 逐字节比对）。

**只能在板子上跑**（领航者 ZYNQ 7020 / PYNQ 2.7）。PC 上没有 pynq / 连续物理内存，
加载 Overlay 就会失败；那时本脚本打 `CAPTURE_BOARD_FAIL not_on_board` 并退出，
**不会**退化成"PC 上模拟一遍"。

它回答三件事（与 JTAG 路径 `board/scripts/jtag_capture_test.tcl` 回答同一组问题）：
  1. 采集链在真板上能不能把一帧搬进 DDR？      -> DMA 超时/错误位 ⇒ 明确 FAIL
  2. 搬进来的字节和黄金模型一样吗？             -> 逐字节比对 + 首个不符坐标
  3. 连续搬 N 帧多快？                          -> 只在 `--bench` 下测，且**不**
                                                   把 JTAG 路径的 ms 级往返算进去

数据通路（`docs/integration_rtl_plan.md` §1）：
    vid_test_pattern ─DVP─> vid_dvp_capture ─AXIS32─> inf_axis_async_fifo
      ─AXIS─> axi_dma(S2MM) ─> DDR 帧缓冲（本脚本的 numpy 缓冲）

图样公式（与 `vid_test_pattern.v` / `tools/capture_golden.py` 逐字一致）：
    R5 = x & 0x1F ; G6 = y & 0x3F ; B5 = (x + y) & 0x1F
    R8 = {R5,R5[4:2]} ; G8 = {G6,G6[5:4]} ; B8 = {B5,B5[4:2]}
    小端 32 位字 = [B8, G8, R8, 0x00]

用法（板上，仓库根目录）：
    sudo python3 src/ps/host/run_capture_test.py --check
    sudo python3 src/ps/host/run_capture_test.py --bench 20
    sudo python3 src/ps/host/run_capture_test.py --check --json /tmp/capture.json

输出纪律（承 `run_layer_test.py`）：stdout 只打 ASCII 令牌（板子控制台编码不可靠），
中文说明放在本文件与 `board/logs/` 的归档里。

末行令牌：
    CAPTURE_BOARD_OK frame_bytes=<n> words=<n> crc32=<8hex> pad_ok=1
    CAPTURE_BOARD_FAIL <reason>                      任何一环失败
    CAPTURE_BENCH_OK frames=<n> fps=<f> mbps=<f>      吞吐（`--bench`）
"""

import sys

# 必须在任何本地 import 之前：板上禁止生成 __pycache__
# （tools/check_repo.py 的 check d) 会扫 __pycache__ 判 FAIL）。
sys.dont_write_bytecode = True

import argparse
import json
import os
import time
import zlib

import numpy as np

# ---- 帧参数：必须与 BD 里 vid_test_pattern / vid_dvp_capture 的参数一致 ----
H_ACTIVE = 640
V_ACTIVE = 480
PIXEL_BYTES = 4
FRAME_BYTES = H_ACTIVE * V_ACTIVE * PIXEL_BYTES

# DMA S2MM 寄存器（PG021；MM2S 半侧在 BD 里未例化，S2MM 偏移不变）
REG_DMACR = 0x30
REG_DMASR = 0x34
REG_DA = 0x48
REG_DA_MSB = 0x4C
REG_LENGTH = 0x58

DMACR_RS = 0x1
DMACR_IOC_IRQ = 0x1000
DMACR_ERR_IRQ = 0x4000
DMASR_IOC = 0x1000
DMASR_ERR_IRQ = 0x4000
DMASR_ERR_MASK = 0x70            # bit4/5/6 = Int_Err / Slv_Err / Dec_Err

# AXI GPIO：通道 1 = 输出（bit0 = 图样 run），通道 2 = 输入（bit0 = 采集溢出粘滞）
GPIO_CH1_DATA = 0x00
GPIO_CH2_DATA = 0x08

POLL_MAX = 4000                  # 4000 x 100 us = 0.4 s（一帧 ~35 ms，余量充足）
POLL_STEP_S = 100e-6


def tok(line):
    print(line, flush=True)


def fail(reason):
    tok("CAPTURE_BOARD_FAIL %s" % reason)
    sys.exit(1)


def expected_frame():
    """按图样公式生成期望的 32 位字（uint32，小端存储对应 DDR 字节序）。"""
    x = np.arange(H_ACTIVE, dtype=np.uint32)
    y = np.arange(V_ACTIVE, dtype=np.uint32)
    r5 = (x & 0x1F).astype(np.uint32)
    b5 = ((x[None, :] + y[:, None]) & 0x1F).astype(np.uint32)
    g6 = (y & 0x3F).astype(np.uint32)

    r8 = ((r5 << 3) | (r5 >> 2)).astype(np.uint32)[None, :].repeat(V_ACTIVE, axis=0)
    g8 = ((g6 << 2) | (g6 >> 4)).astype(np.uint32)[:, None].repeat(H_ACTIVE, axis=1)
    b8 = ((b5 << 3) | (b5 >> 2)).astype(np.uint32)

    # 小端字节序：[B8, G8, R8, 0x00] ⇔ uint32 = R8<<16 | G8<<8 | B8
    words = (r8 << 16) | (g8 << 8) | b8
    return words.reshape(-1).astype("<u4")


def describe_dmasr(sr):
    """把 S2MM_DMASR 的关键位翻译成可读文本（PG021 p.30 表 2-7）。"""
    return ("halted=%d idle=%d sg_incld=%d dma_int_err=%d dma_slv_err=%d dma_dec_err=%d "
            "sg_int_err=%d sg_slv_err=%d sg_dec_err=%d ioc_irq=%d dly_irq=%d err_irq=%d"
            % (sr & 1, (sr >> 1) & 1, (sr >> 3) & 1, (sr >> 4) & 1, (sr >> 5) & 1,
               (sr >> 6) & 1, (sr >> 8) & 1, (sr >> 9) & 1, (sr >> 10) & 1,
               (sr >> 12) & 1, (sr >> 13) & 1, (sr >> 14) & 1))


class CaptureChain(object):
    """capture_chain Overlay 的最小封装：DMA(S2MM) + GPIO 两个 AXI-Lite 窗口。

    物理地址**不硬编码**：从 overlay.ip_dict 按 mem_id 找（`S_AXI_LITE` / `S_AXI`），
    这样改了 BD 地址也不用改脚本（承 `conv_engine.py` 的做法）。
    """

    def __init__(self, bitfile, download=True):
        try:
            from pynq import Overlay
        except Exception as exc:                  # noqa: BLE001 - 不在板上的正常路径
            fail("not_on_board (import pynq failed: %s)" % exc)
        self.overlay = Overlay(bitfile, download=download)
        self.dma = self._find("S_AXI_LITE")
        self.gpio = self._find("S_AXI")

    def _find(self, mem_id):
        for key in sorted(self.overlay.ip_dict.keys()):
            d = self.overlay.ip_dict[key]
            if d.get("mem_id") != mem_id:
                continue
            ip = getattr(self.overlay, key.split("/")[0])
            return ip
        fail("overlay_missing_memid %s (ip_dict keys: %s)"
             % (mem_id, ",".join(sorted(self.overlay.ip_dict.keys()))))

    # ---- DMA ----
    def arm_recv(self, buf):
        from pynq import allocate  # noqa: F401  (文档：buf 来自 pynq.allocate)
        self.dma.mmio.write(REG_DA, buf.physical_address & 0xFFFFFFFF)
        self.dma.mmio.write(REG_DA_MSB, (buf.physical_address >> 32) & 0xFFFFFFFF)
        self.dma.mmio.write(REG_LENGTH, FRAME_BYTES)
        # W1C 错误位 + IOC，避免上一次残留粘滞位冒充成功
        self.dma.mmio.write(REG_DMASR, DMASR_IOC | DMASR_ERR_IRQ | DMASR_ERR_MASK)
        self.dma.mmio.write(REG_DMACR, DMACR_RS | DMACR_IOC_IRQ | DMACR_ERR_IRQ)

    def stop_recv(self):
        """停 S2MM（RS=0）。

        ★ 为什么失败路径也必须停：DMA 武装着（RS=1）而数据永远不来时，它会在总线上
          长时间空等，并且 IOC 中断使能一直开着；上板实测出现过板子跑十几分钟后
          **整个 PS 卡死**（网络 ping 不通、只能 JTAG `rst -system` 救回）。
          先停掉再退出，把「失败」局限在这一次测试里。
        """
        try:
            self.dma.mmio.write(REG_DMACR, 0)
        except Exception:                     # noqa: BLE001 - 收尾失败不该掩盖测试结论
            pass

    def wait_recv(self):
        for _ in range(POLL_MAX):
            sr = self.dma.mmio.read(REG_DMASR)
            if sr & DMASR_ERR_MASK or sr & DMASR_ERR_IRQ:
                fail("dma_error dmasr=0x%08X" % sr)
            if sr & DMASR_IOC:
                return sr
            time.sleep(POLL_STEP_S)
        fail("dma_ioc_timeout dmasr=0x%08X" % self.dma.mmio.read(REG_DMASR))

    # ---- GPIO ----
    def pattern_run(self, on):
        self.gpio.mmio.write(GPIO_CH1_DATA, 1 if on else 0)

    def capture_overflow(self):
        return self.gpio.mmio.read(GPIO_CH2_DATA) & 0x1

    def debug_bits(self):
        """通道 2 的观测位（BD 里由 xlconcat 拼的）：

        bit0  cap.stat_overflow（粘滞）
        bit1  rst_100m.peripheral_aresetn（1 = 复位已释放）
        bit2  pat.vsync_n       （低有效；持续为 1 说明图样没在场消隐）
        bit3  pat.href          （行有效）
        bit7  fifo.s_rst        （写侧复位实况）
        bit8  fifo.m_rst        （读侧复位实况）
        bit9  fifo.wfull  → s_tready = ~bit9
        bit10 fifo.rempty → m_tvalid = ~bit10
        bit11 fifo.dbg_wr_hit（写指针动过 ⇒ 真的写进去了数据）
        bit12 fifo.dbg_rd_hit（读指针动过 ⇒ DMA 真的吃掉过数据）
        bit14 恒 1 的 sanity 位（读不到 1 ⇒ 观测通路本身坏了，别信下面的位）
        bit15 fifo.m_tready 直通（**下游到底拉不拉**）
        bit16..23 fifo 读侧成功握手计数低 8 位（**实际走掉几拍**）

        ★ 这里刻意**没有** cap.m_axis_tvalid / fifo.s_tready / fifo.m_tvalid /
          dma.s_axis_tready：把 AXIS 接口成员脚单独接到 concat 会让 BD 把这些脚
          从接口网里摘掉（[BD 41-1271]），另一侧悬空输入被 tie 0（[BD 41-759]），
          结果观测本身把数据通路切断了 —— 2026-10-20 就是这么演了一出
          「FIFO 写指针永不动」的假故障。接口内部状态一律由 RTL 的 dbg_* 端口导出。
        """
        return self.gpio.mmio.read(GPIO_CH2_DATA) & 0xFFFFFF


def describe_bits(bits):
    return ("overflow=%d rstn=%d vsync_n=%d href=%d s_rst=%d m_rst=%d "
            "wfull=%d rempty=%d wr_hit=%d rd_hit=%d sanity=%d m_tready=%d rd_cnt=%d"
            % (bits & 1, (bits >> 1) & 1, (bits >> 2) & 1, (bits >> 3) & 1,
               (bits >> 7) & 1, (bits >> 8) & 1,
               (bits >> 9) & 1, (bits >> 10) & 1, (bits >> 11) & 1,
               (bits >> 12) & 1, (bits >> 14) & 1,
               (bits >> 15) & 1, (bits >> 16) & 0xFF))


def poll_bits(chain, duration_s=0.2, step_s=0.0):
    """在一段时间内高频采样观测位，返回每种「事件是否出现过」以及命中次数。

    为什么不能只读一次：vsync 低电平只有 4 个 pclk（25 MHz 下 160 ns），
    而一帧 33.6 ms ⇒ 随机读一次几乎必然读到 1；同理 FIFO 读侧在 DMA 全速消费时
    只会零星地有效几拍。必须**连续采样**并统计命中次数。
    默认 step_s=0（忙读，MMIO 一次 ~2–5 µs）。
    """
    seen = {"overflow": 0, "rstn": 1, "vsync_low": 0, "href": 0,
            "fifo_notempty": 0, "fifo_ready": 0,
            "n_rstn": 0, "n_href": 0, "n_s_rst": 0, "n_m_rst": 0,
            "n_wfull": 0, "n_rempty": 0, "n_wr_hit": 0, "n_rd_hit": 0,
            "n_notempty": 0, "n_ready": 0, "n_m_tready": 0,
            "rd_cnt_first": None, "rd_cnt_last": None, "rd_cnt_uniq": 0}
    n = 0
    t_end = time.time() + duration_s
    while time.time() < t_end:
        b = chain.debug_bits()
        n += 1
        if b & 1:
            seen["overflow"] = 1
        if (b >> 1) & 1:
            seen["n_rstn"] += 1
        else:
            seen["rstn"] = 0
        if ((b >> 2) & 1) == 0:
            seen["vsync_low"] = 1
        if (b >> 3) & 1:
            seen["href"] = 1
            seen["n_href"] += 1
        if (b >> 7) & 1:
            seen["n_s_rst"] += 1
        if (b >> 8) & 1:
            seen["n_m_rst"] += 1
        if (b >> 9) & 1:
            seen["n_wfull"] += 1
        else:
            seen["fifo_ready"] = 1
            seen["n_ready"] += 1
        if (b >> 10) & 1:
            seen["n_rempty"] += 1
        else:
            seen["fifo_notempty"] = 1
            seen["n_notempty"] += 1
        if (b >> 11) & 1:
            seen["n_wr_hit"] += 1
        if (b >> 12) & 1:
            seen["n_rd_hit"] += 1
        if (b >> 15) & 1:
            seen["n_m_tready"] += 1
        rc = (b >> 16) & 0xFF
        if seen["rd_cnt_first"] is None:
            seen["rd_cnt_first"] = rc
        if rc != seen["rd_cnt_last"]:
            seen["rd_cnt_uniq"] += 1
        seen["rd_cnt_last"] = rc
        if step_s:
            time.sleep(step_s)
    seen["samples"] = n
    return seen


def fmt_seen(seen):
    return ("samples=%d rstn=%d overflow=%d vsync_low_seen=%d "
            "hits href=%d fifo_notempty=%d fifo_ready=%d "
            "s_rst_hits=%d m_rst_hits=%d wfull_hits=%d rempty_hits=%d "
            "wr_hit_hits=%d rd_hit_hits=%d m_tready_hits=%d "
            "rd_cnt[%s->%s] rd_cnt_changes=%d"
            % (seen["samples"], seen["rstn"], seen["overflow"], seen["vsync_low"],
               seen["n_href"], seen["n_notempty"], seen["n_ready"],
               seen["n_s_rst"], seen["n_m_rst"],
               seen["n_wfull"], seen["n_rempty"], seen["n_wr_hit"],
               seen["n_rd_hit"], seen["n_m_tready"],
               seen["rd_cnt_first"], seen["rd_cnt_last"], seen["rd_cnt_uniq"]))


def cmd_check(chain, args):
    from pynq import allocate

    # ★ 直接按 uint32 分配：一帧 = 307 200 个字。这样比对不用 np.frombuffer(buf)，
    #   避免依赖 Buffer 的 buffer-protocol 细节（PYNQ 2.7 上是 buffer.view(dtype) 可用，
    #   但按字分配更直观、也少一次类型转换）。
    words = FRAME_BYTES // 4
    buf = allocate(shape=(words,), dtype=np.uint32)
    try:
        chain.pattern_run(False)
        bits0 = chain.debug_bits()
        tok("DBG_BITS parked=0x%06X (%s)" % (bits0, describe_bits(bits0)))
        # ★ 先验观测通路，再验被测对象：bit14 是 BD 里一个恒 1 的常量。
        #   读不到 1 说明 gpio2 的高位根本没接对/没读对，这时下面的位都没有诊断价值。
        if not (bits0 & 0x4000):
            fail("dbg_sanity_bit_not_1 (gpio2 bit14 is a hardwired 1 in BD; got 0x%04X) "
                 "-- observation path is broken, do not trust bits 0..13" % bits0)
        if not (bits0 & 0x2):
            fail("domain_held_in_reset (aresetn=0; dbg=0x%04X)" % bits0)

        chain.arm_recv(buf)
        sr_arm = chain.dma.mmio.read(REG_DMASR)
        tok("DMASR_ARMED 0x%08X (%s)" % (sr_arm, describe_dmasr(sr_arm)))
        chain.pattern_run(True)
        # 忙采 20 ms（≈ 0.8 帧；MMIO 一次 ~2–5 µs，能采上万次）：FIFO 读侧在 DMA 全速
        # 消费时只有零星几拍有效，0.5 ms 间隔的慢采样会系统性漏掉它。
        # ★ 窗口刻意短于「一帧 24.6 ms」：传输完成之后再采，采到的 overflow 是
        #   「DMA 停了、FIFO 被后一帧填满」的正常结果，不是丢像素的证据。
        seen = poll_bits(chain, duration_s=args.window_ms / 1000.0)
        tok("DBG_SEEN running %s" % fmt_seen(seen))
        sr_mid = chain.dma.mmio.read(REG_DMASR)
        tok("DMASR_WINDOW 0x%08X (%s)" % (sr_mid, describe_dmasr(sr_mid)))
        if sr_mid & (DMASR_ERR_MASK | DMASR_ERR_IRQ):
            fail("dma_error_in_window dmasr=0x%08X (%s)"
                 % (sr_mid, describe_dmasr(sr_mid)))
        if not seen["rstn"]:
            fail("domain_reset_asserted_during_run")
        if seen["overflow"] and not (sr_mid & DMASR_IOC):
            # IOC 还没到就先溢出 = 传输期间真的丢了像素；IOC 已到则是「DMA 停在帧尾、
            # 图样继续出像素把 FIFO 填满」的正常尾现象（见下面 INFO 行）。
            fail("capture_fifo_overflowed_during_transfer (gpio2 bit0=1 inside the "
                 "window while IOC=0) -- pixels were dropped before the DMA finished")
        if not seen["href"]:
            fail("pattern_not_running (窗口内没见到 href=1：run 没到图样，或图样被复位)"
                 " -- 先查 GPIO bit0 -> xlslice -> pat/run 这条链")
        if not seen["fifo_notempty"] and not seen["n_wr_hit"]:
            fail("fifo_never_written (窗口内 rempty 恒 1 且 wr_hit 恒 0：FIFO 一个字节都没收到。"
                 " 先查 cap_0/m_axis -> fifo_0 的接口连接有没有被 debug tap 之类的操作摘掉)")
        if not seen["fifo_notempty"]:
            fail("fifo_read_side_silent (写指针动过 %d 次，但读侧 rempty 从没低过：查 m_clk/m_rst "
                 "与 DMA 侧 tready)" % seen["n_wr_hit"])
        # 数据在 FIFO 里、但下游一次都没拉：问题在 DMA/AXI 侧，不在采集侧。
        if not seen["n_m_tready"] and not seen["rd_cnt_uniq"]:
            fail("downstream_not_pulling (FIFO 有数据 rempty=0，但窗口内 m_tready 恒 0 且 "
                 "rd_cnt 一次没变：DMA 没在拉 S2MM 流。查 DMA 是否真在跑、其 M_AXI 通道"
                 "（HP0）是否被 PS 使能/有时钟)")
        sr = chain.wait_recv()
        chain.pattern_run(False)             # 停图样：别让下一帧覆盖缓冲
        bits2 = chain.debug_bits()
        tok("DBG_BITS done=0x%06X (%s)" % (bits2, describe_bits(bits2)))
        tok("CAPTURE_DMA_OK dmasr=0x%08X" % sr)

        # ★ 传输完成后的 overflow 一定是 1：DMA 停在帧尾不再收数，图样还在出像素，
        #   16 深采集 FIFO 在 ~1.3 µs 内就满。这是**正常结果**，不是丢像素 ——
        #   「传输期间没丢」已经由上面 20 ms 窗口 + 下面的逐字比对共同证明。
        tok("INFO overflow_after_done=%d (expected 1: FIFO fills once the DMA stops; "
            "not evidence of loss during transfer)" % chain.capture_overflow())

        buf.invalidate()                     # DMA 写过内存，CPU cache 必须先失效
        got = np.asarray(buf)
        exp = expected_frame()
        if got.size != exp.size:
            fail("size_mismatch got=%d expect=%d" % (got.size, exp.size))
        diff = np.nonzero(got != exp)[0]
        if diff.size:
            i = int(diff[0])
            x, y = i % H_ACTIVE, i // H_ACTIVE
            tok("FIRST_DIFF word=%d x=%d y=%d got=0x%08X expect=0x%08X"
                % (i, x, y, int(got[i]), int(exp[i])))
            fail("byte_mismatch %d/%d" % (diff.size, exp.size))

        # 高 8 位必须恒 0：既验证 32 位补零（AXI DMA 不支持 24 位流），也顺带
        # 锁住字节序——若 DDR 里落的是错位字节，高位会先炸。
        pad = np.nonzero((got >> 24) != 0)[0]
        if pad.size:
            i = int(pad[0])
            fail("pad_byte_not_zero word=%d x=%d y=%d got=0x%08X"
                 % (i, i % H_ACTIVE, i // H_ACTIVE, int(got[i])))

        crc = zlib.crc32(exp.tobytes()) & 0xFFFFFFFF
        tok("CAPTURE_BOARD_OK frame_bytes=%d words=%d crc32=%08x pad_ok=1"
            % (FRAME_BYTES, exp.size, crc))
    finally:
        try:
            chain.pattern_run(False)
        except Exception:                     # noqa: BLE001
            pass
        chain.stop_recv()
        try:
            buf.freebuffer()
        except Exception:                     # noqa: BLE001 - 释放失败不该掩盖测试结论
            pass


def cmd_bench(chain, args):
    from pynq import allocate

    n = max(1, int(args.bench))
    words = FRAME_BYTES // 4
    buf = allocate(shape=(words,), dtype=np.uint32)
    try:
        chain.pattern_run(False)
        # 预热一帧：让图样从场首开始，并把 DMA 通路走通（不计入统计）
        chain.arm_recv(buf); chain.pattern_run(True); chain.wait_recv()

        t0 = time.perf_counter()
        for _ in range(n):
            chain.arm_recv(buf)
            chain.wait_recv()
        dt = time.perf_counter() - t0
        chain.pattern_run(False)
    finally:
        try:
            buf.freebuffer()
        except Exception:                     # noqa: BLE001
            pass

    fps = n / dt if dt > 0 else 0.0
    mbps = (n * FRAME_BYTES) / dt / 1e6 if dt > 0 else 0.0
    tok("CAPTURE_BENCH_OK frames=%d fps=%.2f mbps=%.2f" % (n, fps, mbps))
    tok("# note: 背靠背 arm 时 DMA 停在帧尾而图样自由跑，下一帧的传输起点不一定落在"
        "帧首（FIFO 里残留的像素会先被搬走）——这里测的是**采集+DMA 通路的吞吐**，"
        "不是帧对齐；帧对齐与内容正确性由 --check 保证。")
    tok("# note: fps here is bounded by the test-pattern pclk (FCLK1=25 MHz -> "
        "2 pclk/pixel, so a 640x480 frame is ~24.6 ms), NOT by the accelerator; "
        "it measures the capture+DMA path, not YOLO throughput")


def main(argv=None):
    ap = argparse.ArgumentParser(description="board-side M1 capture chain acceptance (PYNQ)")
    ap.add_argument("--bitfile", default="board/overlay/capture_chain.bit")
    ap.add_argument("--check", action="store_true", help="one frame, byte-exact check")
    ap.add_argument("--bench", type=int, default=0, metavar="N",
                    help="time N consecutive frames (capture+DMA path throughput)")
    ap.add_argument("--json", default="", help="write machine-readable result here")
    ap.add_argument("--window-ms", type=int, default=6,
                    help="observation window right after arming (ms); keep it shorter than one "
                         "active frame (640x480 @25MHz pclk ≈ 12.3 ms) so that 'FIFO full after "
                         "the DMA stopped' is not mistaken for pixel loss")
    args = ap.parse_args(argv)

    if not os.path.isfile(args.bitfile):
        fail("bitfile_missing %s" % args.bitfile)
    if not (args.check or args.bench):
        ap.print_help()
        return 2

    chain = CaptureChain(args.bitfile)

    if args.check:
        cmd_check(chain, args)
    if args.bench:
        cmd_bench(chain, args)

    if args.json:
        try:
            with open(args.json, "w", newline="\n") as fh:
                json.dump({"tool": "src/ps/host/run_capture_test.py",
                           "frame_bytes": FRAME_BYTES,
                           "check": bool(args.check), "bench_frames": args.bench,
                           "status": "OK"}, fh, indent=1)
            tok("RESULT_JSON %s" % args.json)
        except Exception as exc:              # noqa: BLE001 - 写不了 JSON 不该推翻测试结论
            tok("WARN could not write json: %s" % exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())