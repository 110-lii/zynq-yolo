# src/ps/pynq_driver/ —— conv_engine 的 PYNQ Overlay 封装

> 上级：[`../../README.md`](../../README.md)（`src/`）
> 硬件契约：[`../../docs/accel_interface.md`](../../docs/accel_interface.md) §2 / §3 / §4
> 上位测试：[`../host/run_layer_test.py`](../host/run_layer_test.py)

| | |
|---|---|
| 本目录装什么 | 板子（PYNQ 2.7）上跑的 **Overlay 驱动**：把 `conv_engine` HLS IP 的两个 AXI-Lite 从口包成一个 Python 类，管缓冲分配、寄存器写入、`ap_start`/`ap_done` 握手、输出取回 |
| 谁产生 | 手写（本仓库 `src/` 是设计真源） |
| 进 git | 是 |

## 内容

| 文件 | 行数（2026-10-19） | 职责 |
|---|---|---|
| `conv_engine.py` | 约 590 | `ConvEngineOverlay` 类 + `parse_meta()` / `load_case()`；寄存器偏移常量；`sys.dont_write_bytecode = True` 置顶 |
| `.gitkeep` | 0 | 占位（目录先于文件存在） |

`conv_engine.py` 对外只有四个东西：

| 名字 | 作用 |
|---|---|
| `ConvEngineOverlay(bitfile, download=True)` | 加载 `.bit`（同目录需同名 `.hwh`），按 `mem_id` 从 `overlay.ip_dict` 找到两个窗口，建好两个 `MMIO` |
| `.load_layer(case_dir)` | 按 `meta.txt` 分配 5 个 `pynq.allocate` 连续缓冲，从 `in.bin / w.bin / params.bin / lut.bin` 灌数据，写口 B 的 5 个 64 位指针 |
| `.run_once(meta=None)` | 写口 A 标量 → 查 idle → 再写指针 → `AP_CTRL=0x01` → 轮询 `ap_done`，返回 `{"done", "elapsed_s", "ap_ctrl_final", "idle_before", "timeout_s"}` |
| `.get_out()` | `invalidate()` 后把输出拷成普通 `ndarray`（板上有 DCache，不 invalidate 会读到旧数据） |

辅助：`.describe()` 打一行 ASCII（两个窗口的键名与物理地址），`.run_layer(case_dir)` 一站式，`.release()` 放缓冲。

### 两个 AXI-Lite 窗口（地址一律从 `.hwh` 解析，代码里没有硬编码）

`conv_engine` 有两个从口，PYNQ 2.7 的 `hwh_parser._parse_ip_dict` 会把它们拆成
**两个 `ip_dict` 条目**（同一 `INSTANCE` 的第二个 `MEMRANGE` 触发改名，键名带 `/`）：

| `ip_dict` 键（本板 `.hwh` 实测） | `mem_id` | 物理基址（来自 `.hwh`） | 用途 |
|---|---|---|---|
| `conv_engine_0/s_axi_control` | `s_axi_control` | `0x4000_0000` | 口 A：标量参数 |
| `conv_engine_0/s_axi_control_r` | `s_axi_control_r` | `0x4001_0000` | 口 B：5 个指针基址 |

驱动按 `mem_id` + `type` 含 `conv_engine`（本板 VLNV `zynq-yolo:hls:conv_engine:1.0`）
匹配，键名或基址变了也不影响 —— 换 `.hwh` 不用改代码。

### 口 A `s_axi_control` 寄存器（32 位访问，偏移以窗口 0 为基准）

| 偏移 | 名称 | 说明 |
|---|---|---|
| `0x00` | `AP_CTRL` | bit0 `ap_start`（写 1 启动）、bit1 `ap_done`（**读清**，`.hwh` 里 `READ_ACTION=modify`）、bit2 `ap_idle`、bit3 `ap_ready` |
| `0x04` / `0x08` / `0x0c` | `GIE` / `IER` / `ISR` | 中断相关；本驱动用轮询，**不使能中断** |
| `0x10` | `C_IN` | 输入通道 |
| `0x18` | `C_OUT` | 输出通道 |
| `0x20` | `H_IN` | 输入高 |
| `0x28` | `W_IN` | 输入宽 |
| `0x30` | `K` | 卷积核边长（v1 支持 1 / 3） |
| `0x38` | `stride` | 步长（v1 支持 1 / 2） |
| `0x40` | `pad` | 填充（v1 支持 0 / 1） |
| `0x48` | `zp_in` | 输入零点（边界填充值，**不是 0**） |
| `0x50` | `zp_pre` | 输出零点（`q_pre = clamp(round(acc*M*2^-S)+zp_pre, 0, 255)`） |
| `0x58` | `H_OUT` | 输出高（**PS 算好传入**，PL 不推导） |
| `0x60` | `W_OUT` | 输出宽（同上） |

### 口 B `s_axi_control_r` 寄存器（5 个 64 位指针）

| 低 32 位偏移 | 指针 | 指向 |
|---|---|---|
| `0x10`（高 `0x14`） | `in_r` | `uint8 [C_IN][H_IN][W_IN]` |
| `0x1c`（高 `0x20`） | `w` | `int8 [C_OUT][C_IN][K][K]` |
| `0x28`（高 `0x2c`） | `params` | `int32 [C_OUT][3]` = `{bias_i32, M, S}` |
| `0x34`（高 `0x38`） | `lut` | `uint8 [256]` |
| `0x40`（高 `0x44`） | `out_r` | `uint8 [C_OUT][H_OUT][W_OUT]` |

★ 相邻项间隔 **12 字节**（`0x10 → 0x1c`），这是 HLS 打包方式，**不是笔误**，不要"顺手改成 8 或 16"。
★ 这 5 个寄存器在 `.hwh` 里是 `ACCESS="write-only"`：**读回无效**，不要拿读回值当判据。

## 规则

- **地址只能从 `.hwh` / `ip_dict` 解析**，禁止在代码里写 `0x40000000` 之类字面量（换板/换布局即失效）。
- **不生成、不重算数据**：`in.bin / w.bin / params.bin / lut.bin / out_ref.bin` 由 `sim/scripts/make_hls_case.py` 冻结，驱动只读文件、只校验长度。
- 文件名纯 ASCII；文件正文 UTF-8 无 BOM；所有 `open()` 显式 `newline="\n"`（`.gitattributes` 是 `eol=lf`）。
- 本文件顶部 `sys.dont_write_bytecode = True`，且必须在任何本地 import 之前：板上跑一次不许留 `__pycache__/`（`tools/check_repo.py` 的 check d) 会扫盘判 FAIL）。
- 板上 stdout **只打 ASCII**：控制台是 GBK，`print()` 里出现中文或 `⇒` 会 `UnicodeEncodeError`。中文说明写进 README / JSON。
- 这个模块 **在 PC 上 import 会直接抛 ImportError**（没有 `pynq`）。这是设计分工，不是缺陷：它只能在板上跑。
- 缓冲区用 `pynq.allocate(shape=..., dtype=...)`（CMA 连续物理内存），**不要**用 `np.zeros` —— PL 拿不到虚拟地址。
- 依赖只有 `pynq` / `numpy`（镜像自带），**禁止 `pip install`**。

## 怎么用

### 板上（推荐直接跑 host 脚本）

```bash
# 仓库根目录，板上
sudo python3 src/ps/host/run_layer_test.py --case k3s1_c3_40
```

### 手动调驱动（板上 Python / Jupyter）

```python
import sys
sys.path.insert(0, "/home/xilinx/zynq-yolo/src/ps/pynq_driver")
from conv_engine import ConvEngineOverlay

ovl = ConvEngineOverlay("/home/xilinx/zynq-yolo/board/overlay/conv_engine.bit")
print(ovl.describe())            # ASCII：两个窗口的键名 + 物理地址

res = ovl.run_layer("/home/xilinx/zynq-yolo/data/layers/cases/k3s1_c3_40")
print(res["run"])                # {"done": True, "elapsed_s": ..., ...}
print(res["out"].shape)          # (16, 40, 40)

# 细粒度：同一个层重复跑（不重复分配）
ovl.load_layer("/home/xilinx/zynq-yolo/data/layers/cases/k3s1_c3_40")
for _ in range(3):
    print(ovl.run_once())
out, _buf = ovl.get_out()
```

### PC 侧（只能验语法，不能跑）

```powershell
& D:\python\python.exe -c "compile(open(r'D:\zynq-yolo\src\ps\pynq_driver\conv_engine.py',encoding='utf-8').read(), 'conv_engine.py', 'exec'); print('SYNTAX_OK')"
```

> 不要用 `py_compile` / `compileall`：它们会写 `.pyc`，触发 `check_repo.py` 的 check d)。

### 已知限制（截至 2026-10-19，未上板）

- `board/overlay/conv_engine.bit` 在本文件写成时**尚不存在**（只有 `.hwh`），所以本驱动**从未在真实板子上执行过**；`run_once()` 的寄存器时序、`ap_done` 轮询、`invalidate()` 一致性都只在 PC 上用假 `Overlay`/假 `MMIO` 做过协议级模拟（见 [`../../data/results/board_layer_test.json`](../../data/results/board_layer_test.json) 的 `pc_side_checks`）。
- 因此：**没有实测 `elapsed_ns`，没有实测 GOPS**。任何"应该能跑"的说法都不成立，等真板数据。
