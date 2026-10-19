#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""conv_engine.py -- PYNQ Overlay 封装类（conv_engine HLS IP 的板级驱动）。

本文件会被拷到板子（领航者 ZYNQ 7020 / PYNQ 2.7）上运行，PC 上 import 会失败
（pynq / xrt / numpy 板上才有），这不是缺陷，是设计分工。

运行环境：PYNQ 2.7（Ubuntu 20.04, kernel 5.4.0, Python 3.6+）。
依赖：pynq >= 2.7, numpy（PYNQ 镜像自带，均无需 pip install）。

== 硬件契约（真源：docs/accel_interface.md §2/§4 + .hwh 实测，勿凭记忆改）==

IP `conv_engine`（MODTYPE=conv_engine，例化名 conv_engine_0）有**两个独立的
AXI-Lite 从口**，因此 PYNQ 的 ip_dict 里它会被拆成两个条目（实测键名，来自
board/overlay/conv_engine.hwh，PYNQ 2.7 hwh_parser.py 的 _parse_ip_dict 逻辑：
同一 INSTANCE 的第二个 MEMRANGE 触发改名）：

    conv_engine_0/s_axi_control     phys 0x40000000  -- 标量参数口（口 A）
    conv_engine_0/s_axi_control_r  phys 0x40010000  -- 指针基址口（口 B）

物理地址**不硬编码**：本类从 overlay.ip_dict 按 mem_id（'s_axi_control' /
's_axi_control_r'）自动查找，键名由 MMIO 侧匹配得到 -- .hwh 换了地址布局也能跑。

口 A 寄存器（32 位访问，偏移以该窗口 0 为基准；= build/ip_repo/...
/xconv_engine_hw.h 的 XCONV_ENGINE_CONTROL_* 定义，与 .hwh 的
ADDRESSBLOCK/REGISTERS 逐项核对一致）：

    0x00 AP_CTRL  : bit0 ap_start, bit1 ap_done, bit2 ap_idle, bit3 ap_ready
    0x04 GIE / 0x08 IER / 0x0c ISR（本驱动用轮询，不使能中断）
    0x10 C_IN     0x18 C_OUT    0x20 H_IN    0x28 W_IN
    0x30 K        0x38 stride   0x40 pad    0x48 zp_in
    0x50 zp_pre   0x58 H_OUT    0x60 W_OUT

口 B 寄存器（5 个 64 位指针，每个写两次：低 32 位 -> off，高 32 位 -> off+4）：

    0x10/0x14 in_r    0x1c/0x20 w    0x28/0x2c params
    0x34/0x38 lut     0x40/0x44 out_r

★ 相邻指针项间隔 12 字节（0x10 -> 0x1c），这是 HLS 的打包方式，不是笔误；
  该偏移同样来自 xconv_engine_hw.h 的 XCONV_ENGINE_CONTROL_R_* 定义。
★ 口 B 的寄存器在 .hwh 里被标记 ACCESS="write-only"，只能用 MMIO 写，
  读回验证（readback）对它无效，属正常现象，不要拿读回做判据。

== 数据通路 ==

m_axi_gmem_r -> PS7 S_AXI_HP0（读 in/w/params/lut）
m_axi_gmem_w -> PS7 S_AXI_HP1（写 out）
（.hwh MEMORYMAP 实测：两条 HP 口均映射 DDR 0x0000_0000..0x3FFF_FFFF，64 位地址。）

== 缓冲区布局（docs/accel_interface.md §2，NCHW，小端）==

    in     uint8  [C_IN][H_IN][W_IN]   激活，无符号
    w      int8   [C_OUT][C_IN][K][K]  权重，有符号
    params int32  [C_OUT][3]           每通道 {bias_i32, M, S}
    lut    uint8  [256]
    out    uint8  [C_OUT][H_OUT][W_OUT]

五个缓冲区与 data/layers/cases/<case>/ 下已有的裸小端文件一一对应：
**驱动直接读这些文件，禁止重新生成**（数据口径由 sim/scripts/make_hls_case.py 冻结）。

== 使用 ==

    from conv_engine import ConvEngineOverlay

    ovl = ConvEngineOverlay("conv_engine.bit")     # 加载并校验 IP 存在
    res = ovl.run_layer(case_dir)                   # 见 run_layer 的返回 dict

    # 或者更细粒度：
    ovl.load_layer(case_dir)                        # 分配缓冲 + 写指针基址
    ovl.run_once()                                  # ap_start -> 轮询 ap_done
    out = ovl.get_out()                             # 取回输出（copy 到普通 ndarray）

== 编码纪律 ==

- 所有 open() 显式 newline="\n"、encoding="utf-8"（本仓库 .gitattributes eol=lf）。
- 板上 stdout 不打中文/emoji：脚本侧的输出全部是 ASCII 令牌。
- 本文件可以被 tools/check_repo.py 的 check f) AST 解析（纯 UTF-8 无 BOM）。
"""

import os
import re
import sys

# 防止板上运行时生成 __pycache__（tools/check_repo.py 的 check d) 扫盘会判 FAIL）。
# 必须放在所有本地 import 之前。
sys.dont_write_bytecode = True

import time

import numpy as np

try:
    from pynq import allocate
    from pynq import Overlay
except ImportError as exc:  # PC 上没有 pynq 属预期，给出可判读的错误
    raise ImportError(
        "pynq not available: this driver must run ON THE BOARD "
        "(PYNQ 2.7 image, e.g. Navigator ZYNQ 7020). "
        "Run src/ps/host/run_layer_test.py on the board, not on the PC."
    ) from exc

# ---------------------------------------------------------------- 常量（硬件契约）

# PYNQ ip_dict 里两个条目的 mem_id（.hwh MEMORYMAP 的 SLAVEBUSINTERFACE 值）。
MEM_ID_SCALAR = "s_axi_control"     # 口 A：标量参数
MEM_ID_POINTER = "s_axi_control_r"  # 口 B：5 个 m_axi 指针基址

# 口 A 寄存器偏移（32 位访问）。真源：xconv_engine_hw.h + .hwh ADDRESSBLOCK。
REG_AP_CTRL = 0x00
REG_C_IN = 0x10
REG_C_OUT = 0x18
REG_H_IN = 0x20
REG_W_IN = 0x28
REG_K = 0x30
REG_STRIDE = 0x38
REG_PAD = 0x40
REG_ZP_IN = 0x48
REG_ZP_PRE = 0x50
REG_H_OUT = 0x58
REG_W_OUT = 0x60

# 口 B 指针寄存器偏移（每个 64 位：低 32 位写 off，高 32 位写 off+4）。
# 相邻项间隔 12 字节（HLS 打包方式），不是 8，也不是 16 —— 勿"顺手修正"。
PTR_OFFSETS = {
    "in_r": 0x10,
    "w": 0x1C,
    "params": 0x28,
    "lut": 0x34,
    "out_r": 0x40,
}

AP_DONE_BIT = 0x2   # AP_CTRL bit1
AP_IDLE_BIT = 0x4   # AP_CTRL bit2

# 轮询间隔：拉高到 50us 量级，在"快速完成"与"不烧 CPU"之间取个偏快的点。
# 注意 Linux 上 sleep(50e-6) 实际量级是 ~60-100us（调度粒度），因此小用例的
# 计时主要靠下面的紧循环窗口，不能只靠 sleep 轮询。
POLL_INTERVAL_S = 50e-6
# 紧循环窗口：启动后前 1ms 不 sleep，连续读 AP_CTRL（MMIO 读一次 ~1us 量级），
# 让小用例的 ap_start->ap_done 实测不被调度粒度抬虚。
SPIN_WINDOW_S = 1e-3
# 轮询超时：单层最坏 MAC 数量级约 2e8（320x320 首层太极端，测试用例远小于此），
# 真内核按 0.1 GOPS 保守估计也 << 10 s；给 10 s 是为了放住异常情况而不是当正常耗时。
POLL_TIMEOUT_S = 10.0

# 缓冲区总长度上限（防 meta.txt 里的尺寸做缓冲区溢出用）。7020 的 HP 映射到 1GB DDR。
MAX_TENSOR_BYTES = 256 * 1024 * 1024  # 256 MB


# 缓冲区名 -> data/layers/cases/<case>/ 下的裸文件名。
# 注意 in 的**文件名是 in.bin**（不是 in_r.bin）：驱动内部叫 in_r 是为了和
# .hwh/寄存器名（in_r）对齐，磁盘上沿用 sim/scripts/make_hls_case.py 的命名。
BUFFER_FILES = {
    "in_r": "in.bin",
    "w": "w.bin",
    "params": "params.bin",
    "lut": "lut.bin",
}
REF_FILE = "out_ref.bin"


# ---------------------------------------------------------------- meta 解析


def parse_meta(case_dir):
    """解析 data/layers/cases/<case>/meta.txt -> dict。

    格式（docs/accel_interface.md §9.2）：`key=value` 行，`#` 开头为注释。
    全部整数键都在返回 dict 里（int），未知键保留原样（str）。
    """
    path = os.path.join(case_dir, "meta.txt")
    meta = {}
    with open(path, "r", encoding="utf-8", errors="strict", newline="\n") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                raise ValueError(
                    "meta.txt malformed line (expected key=value): %r" % line
                )
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip()
            try:
                meta[key] = int(val)
            except ValueError:
                meta[key] = val
    return meta


def _require_int(meta, key):
    """从 meta dict 取一个必须存在的整数键。"""
    val = meta.get(key)
    if not isinstance(val, int):
        raise ValueError("meta.txt missing or non-integer key: %s" % key)
    return val


def load_case(case_dir):
    """读入一个用例的全部输入文件（meta.txt + 4 个 .bin）。

    返回 (meta, buffers)，buffers 为 dict（键 = 驱动内部缓冲名，与口 B 指针同名）:
        in_r    np.ndarray uint8 [C_IN][H_IN][W_IN]   <- in.bin
        w       np.ndarray int8  [C_OUT][C_IN][K][K]  <- w.bin
        params  np.ndarray int32 [C_OUT][3]           <- params.bin
        lut     np.ndarray uint8 [256]                <- lut.bin
    文件**必须已经存在**（由 sim/scripts/make_hls_case.py 生成），本函数不生成、
    不重算、不校验内容，只校验长度与 meta 声明的几何一致（长度对不上直接报错，
    不做截断/补零这种"看起来能跑"的修复）。
    """
    meta = parse_meta(case_dir)
    c_in = _require_int(meta, "C_IN")
    c_out = _require_int(meta, "C_OUT")
    h_in = _require_int(meta, "H_IN")
    w_in = _require_int(meta, "W_IN")
    k = _require_int(meta, "K")
    h_out = _require_int(meta, "H_OUT")
    w_out = _require_int(meta, "W_OUT")

    shapes = {
        "in_r": (c_in, h_in, w_in),
        "w": (c_out, c_in, k, k),
        "params": (c_out, 3),
        "lut": (256,),
    }
    dtypes = {"in_r": np.uint8, "w": np.int8, "params": np.int32, "lut": np.uint8}

    buffers = {}
    for name, shape in shapes.items():
        path = os.path.join(case_dir, BUFFER_FILES[name])
        dtype = dtypes[name]
        count = 1
        for dim in shape:
            count *= dim
        expected_bytes = count * np.dtype(dtype).itemsize
        actual_bytes = os.path.getsize(path)
        if actual_bytes != expected_bytes:
            raise ValueError(
                "file size mismatch for %s: expected %d bytes "
                "(shape %r dtype %s), got %d" % (
                    path, expected_bytes, shape, np.dtype(dtype).name, actual_bytes
                )
            )
        if expected_bytes > MAX_TENSOR_BYTES:
            raise ValueError(
                "buffer %s too large: %d bytes > limit %d" % (
                    name, expected_bytes, MAX_TENSOR_BYTES
                )
            )
        arr = np.fromfile(path, dtype=dtype)
        if arr.size != count:
            raise ValueError("read %d elements from %s, expected %d" % (
                arr.size, path, count))
        buffers[name] = arr.reshape(shape)
    return meta, buffers


# ---------------------------------------------------------------- Overlay 封装


class ConvEngineOverlay(object):
    """conv_engine HLS IP 的 PYNQ Overlay 封装。

    负责：加载 Overlay、解析 ip_dict、为单层测试分配连续物理内存缓冲、
    写口 A 标量寄存器、写口 B 指针基址寄存器、启动 + 轮询 ap_done、取回输出。

    不负责：层间调度、非卷积算子、任何数值推导（H_OUT/W_OUT 一律由 meta.txt
    提供，PL 只做校验，契约 §4）。
    """

    def __init__(self, bitfile, download=True):
        """加载 Overlay 并绑定两个 AXI-Lite 窗口的 MMIO。

        参数
        ----
        bitfile : str
            .bit 路径（同目录需有同名 .hwh）。
        download : bool
            True 时下载比特流；False 仅绑定（已下载过的场景）。
        """
        self.overlay = Overlay(bitfile, download=download)
        self._find_ip()
        self._c2c_buf = {}
        self._meta = None
        self._bufs = {}

    # ------------------------------------------------------------ IP 查找

    def _find_ip(self):
        """从 ip_dict 按两个 mem_id 找 conv_engine 的两个 AXI-Lite 窗口。

        匹配规则（不硬编码物理地址，键名可变）：
        1. 条目 mem_id 等于目标值；
        2. 条目 type（VLNV，本板实测 'zynq-yolo:hls:conv_engine:1.0'）含
           'conv_engine'（大小写不敏感）。

        PYNQ 2.7 的 ip_dict 在此 IP 上有两个条目，键名是
        '<fullpath>/<mem_id>'（hwh_parser._parse_ip_dict：同一 INSTANCE 的第二个
        MEMRANGE 会触发改名，并把裸键 pop 掉），例如：
            'conv_engine_0/s_axi_control'    mem_id='s_axi_control'
            'conv_engine_0/s_axi_control_r'  mem_id='s_axi_control_r'

        取 IP 对象用 getattr(overlay, key)：Overlay.__getattr__ -> _IPMap.__getattr__
        -> driver(ipdescription)（无 bindto 时即 DefaultIP），得到带 .mmio 的对象
        （键名含 '/' 不影响 —— getattr 是函数调用，不是标识符解析）。

        找不到/找到多个就抛 RuntimeError，异常文本全是 ASCII（板子 stdout 只能
        打 ASCII）。
        """
        ip_dict = self.overlay.ip_dict
        # 全量键名留档，板上出问题时能在异常文本里直接看到实际布局
        self.ip_keys = sorted(ip_dict.keys())

        self.window_keys = {}
        for mem_id in (MEM_ID_SCALAR, MEM_ID_POINTER):
            matches = [
                key for key, info in ip_dict.items()
                if info.get("mem_id") == mem_id
                and "conv_engine" in str(info.get("type", "")).lower()
            ]
            if len(matches) != 1:
                raise RuntimeError(
                    "expected exactly 1 conv_engine window with mem_id=%s, "
                    "found %d (%r); ip_dict keys: %r" % (
                        mem_id, len(matches), matches, self.ip_keys)
                )
            self.window_keys[mem_id] = matches[0]

        self.mmio_scalar = getattr(
            self.overlay, self.window_keys[MEM_ID_SCALAR]).mmio
        self.mmio_pointer = getattr(
            self.overlay, self.window_keys[MEM_ID_POINTER]).mmio
        return self.mmio_scalar, self.mmio_pointer

    def describe(self):
        """返回一行 ASCII 描述（板上第一次跑时打印，便于人工核对地址来源）。

        物理地址来自 .hwh（ip_dict['phys_addr']），本驱动只读取不假设。
        """
        def _addr(mmio):
            addr = getattr(mmio, "base_addr", None)
            if addr is None:
                addr = getattr(mmio, "baseaddr", "unknown")
            if isinstance(addr, int):
                return "0x%08x" % addr
            return str(addr)

        return "conv_engine windows: %s=%s @%s  %s=%s @%s" % (
            MEM_ID_SCALAR, self.window_keys[MEM_ID_SCALAR],
            _addr(self.mmio_scalar),
            MEM_ID_POINTER, self.window_keys[MEM_ID_POINTER],
            _addr(self.mmio_pointer),
        )

    # ------------------------------------------------------------ MMIO 基础

    def _write_reg(self, mmio, offset, value):
        """写一个 32 位寄存器。"""
        mmio.write(offset, value & 0xFFFFFFFF)

    def _read_reg(self, mmio, offset):
        """读一个 32 位寄存器。"""
        return mmio.read(offset)

    def _write_ptr(self, name, phys_addr):
        """把一个 64 位指针基址写进口 B 的对应寄存器（低 32 位 + 高 32 位）。"""
        off = PTR_OFFSETS[name]
        self._write_reg(self.mmio_pointer, off, phys_addr & 0xFFFFFFFF)
        self._write_reg(self.mmio_pointer, off + 4, (phys_addr >> 32) & 0xFFFFFFFF)

    # ------------------------------------------------------------ 层加载

    def load_layer(self, case_dir):
        """为一个用例分配缓冲、灌数据、写指针基址（不动口 A 的几何寄存器）。

        缓冲区用 pynq.allocate(shape=..., dtype=...) 分配连续物理内存，
        数据从 data/layers/cases/<case>/ 下的裸 .bin 文件读入。
        返回 (meta, buffers)，buffers 为普通 ndarray 的 dict（PYNQ buffer 在
        self._c2c_buf 里）。
        """
        meta, buffers = load_case(case_dir)
        self._meta = meta
        self._bufs = buffers

        # 先释放上一层分配的缓冲（pynq.buffer 没有显式 free 时靠 overlay 引用
        # 管理；这里简单重挂，board 内存足够大，不追求精细回收）。
        self._c2c_buf = {}

        for name in ("in_r", "w", "params", "lut"):
            src = buffers[name]
            buf = allocate(shape=src.shape, dtype=src.dtype)
            buf[:] = src
            buf.flush()  # 确保 PS 侧写 DDR 的一致性（不依赖 cache 行为）
            self._c2c_buf[name] = buf
            self._write_ptr(name, buf.physical_address)

        # 输出缓冲（H_OUT/W_OUT 由 meta 提供，不由驱动推导 —— 契约 §4）。
        out_shape = (
            _require_int(meta, "C_OUT"),
            _require_int(meta, "H_OUT"),
            _require_int(meta, "W_OUT"),
        )
        out_buf = allocate(shape=out_shape, dtype=np.uint8)
        # 预填 0xCC：完成后全 0xCC 说明 PL 根本没写输出（比 0 更醒目的哨兵）。
        out_buf[:] = np.full(out_shape, 0xCC, dtype=np.uint8)
        out_buf.flush()
        self._c2c_buf["out_r"] = out_buf
        self._write_ptr("out_r", out_buf.physical_address)

        return meta, buffers

    def write_scalar_regs(self, meta):
        """把 meta.txt 里的几何/量化标量写进口 A（不动 AP_CTRL）。"""
        pairs = (
            (REG_C_IN, "C_IN"), (REG_C_OUT, "C_OUT"),
            (REG_H_IN, "H_IN"), (REG_W_IN, "W_IN"),
            (REG_K, "K"), (REG_STRIDE, "stride"),
            (REG_PAD, "pad"), (REG_ZP_IN, "zp_in"),
            (REG_ZP_PRE, "zp_pre"),
            (REG_H_OUT, "H_OUT"), (REG_W_OUT, "W_OUT"),
        )
        for reg, key in pairs:
            self._write_reg(self.mmio_scalar, reg, _require_int(meta, key))

    # ------------------------------------------------------------ 运行 + 轮询

    def wait_until_done(self, timeout_s=POLL_TIMEOUT_S, t_start=None):
        """轮询 AP_CTRL 直到 ap_done 置起。

        参数
        ----
        timeout_s : float  超时阈值（秒），超时返回 done=False，**不抛异常**
        t_start   : float  计时起点（time.perf_counter() 时基）；None 表示从本
                           函数入口开始计。run_once 传 ap_start 写入前那一刻。

        返回 (done, elapsed_s, ap_ctrl_hex)：
            done        True 时在超时内看到 ap_done==1
            elapsed_s   t_start 到"看到 ap_done"的墙钟秒数（超时则为超时时刻）
            ap_ctrl_hex 超时时 AP_CTRL 的终值（ASCII 十六进制串），正常为 ''

        轮询策略：前 SPIN_WINDOW_S 秒紧循环（不 sleep），之后退到 POLL_INTERVAL_S
        睡眠轮询。ap_done 在 .hwh 里是 READ_ACTION=modify（读清），所以第一次
        读到 bit1=1 就要立刻返回 —— 再读一次它就变 0 了。
        """
        if t_start is None:
            t_start = time.perf_counter()
        deadline = t_start + timeout_s
        spin_until = t_start + SPIN_WINDOW_S
        ctrl_val = 0
        while True:
            ctrl_val = self._read_reg(self.mmio_scalar, REG_AP_CTRL)
            if ctrl_val & AP_DONE_BIT:
                return True, time.perf_counter() - t_start, ""
            now = time.perf_counter()
            if now >= deadline:
                return False, now - t_start, "0x%08x" % ctrl_val
            if now < spin_until:
                continue
            time.sleep(POLL_INTERVAL_S)

    def run_once(self, meta=None):
        """启动一次卷积并阻塞等待 ap_done。

        顺序（对照 HLS 约定，缺一步都可能挂）：
        1. 写口 A 全部标量寄存器；
        2. 复核 IP 处于 idle（防止上一轮没收尾就硬启动）；
        3. 写口 B 5 个指针基址；
        4. 写 AP_CTRL=0x01（ap_start）并记 t0（t0 取在写之前一行）；
        5. 轮询 ap_done 并记 t1 —— 实际计时在 wait_until_done 里做。

        返回 dict（键全部 ASCII）：
            done         bool  是否在超时内看到 ap_done
            elapsed_s    float ap_start 到 ap_done 的墙钟（秒，perf_counter）
            timeout_s    float 使用的超时阈值
            ap_ctrl_final str  超时时 AP_CTRL 的终值（正常时为 ''）
            idle_before  bool  启动前 IP 是否 idle
        """
        if meta is None:
            meta = self._meta
        if meta is None:
            raise RuntimeError("no layer loaded; call load_layer() first")

        # 1. 标量
        self.write_scalar_regs(meta)

        # 2. 启动前 IP 必须 idle（不 idle 说明上一轮没结束，直接报错而不是硬跑）
        ctrl = self._read_reg(self.mmio_scalar, REG_AP_CTRL)
        idle_before = bool(ctrl & AP_IDLE_BIT)
        if not idle_before:
            raise RuntimeError(
                "IP not idle before start (AP_CTRL=0x%08x); "
                "previous run may not have completed" % ctrl
            )

        # 3. 指针基址（load_layer 已写过；再写一遍几乎不耗时，且防中途被改）
        for name, buf in self._c2c_buf.items():
            self._write_ptr(name, buf.physical_address)

        # 4. 启动并计时（t0 必须在写 ap_start 之前取，否则漏掉写寄存器的开销）
        t0 = time.perf_counter()
        self._write_reg(self.mmio_scalar, REG_AP_CTRL, 0x01)

        # 5. 轮询 ap_done（ap_done 读清，读到一次即算完成）
        done, elapsed_s, ap_ctrl_final = self.wait_until_done(t_start=t0)

        return {
            "done": done,
            "elapsed_s": elapsed_s,
            "timeout_s": POLL_TIMEOUT_S,
            "ap_ctrl_final": ap_ctrl_final,
            "idle_before": idle_before,
        }

    # ------------------------------------------------------------ 输出

    def get_out(self):
        """取回输出张量（copy 成普通 ndarray；不 invalidate 不行，板上 PS 侧
        有 DCache，PL 刚写完的 DDR 数据必须先 invalidate 再读）。

        返回 (arr, out_buf)：
        arr     np.ndarray uint8 [C_OUT][H_OUT][W_OUT]，数据副本
        out_buf pynq buffer（调用方需要时可再 invalidate/coherence 操作）
        """
        out_buf = self._c2c_buf["out_r"]
        out_buf.invalidate()  # 让 CPU 丢弃 cache 里的旧数据，从 DDR 重读
        arr = np.array(out_buf)  # 深拷贝，之后的缓冲复用不影响结果
        return arr, out_buf

    # ------------------------------------------------------------ 一站式

    def run_layer(self, case_dir):
        """加载一个用例并完整跑一轮（load_layer + run_once + get_out）。

        返回 dict：
            meta            meta.txt 的 dict
            run             run_once() 的返回 dict
            out             np.ndarray uint8 [C_OUT][H_OUT][W_OUT]
        """
        meta, _src = self.load_layer(case_dir)
        run = self.run_once(meta)
        out, _ = self.get_out()
        return {"meta": meta, "run": run, "out": out}

    def release(self):
        """清空本类持有的 CMA 缓冲引用（析构时会被自动处理，此为显式口）。"""
        self._c2c_buf = {}
