# src/ps/host/ —— 板级验收脚本（单层跑 + 逐字节比对 + 实测 GOPS）

> 上级：[`../../README.md`](../../README.md)（`src/`）
> 驱动：[`../pynq_driver/conv_engine.py`](../pynq_driver/conv_engine.py)
> 判据来源：[`../../docs/accel_interface.md`](../../docs/accel_interface.md)、[`../../ref/pitfalls_from_oi.md`](../../ref/pitfalls_from_oi.md) P3 / P7

| | |
|---|---|
| 本目录装什么 | **只能在板上跑**的验收脚本：把 `conv_engine` 真跑一遍，回答"算完了吗 / 算对了吗 / 多快" |
| 谁产生 | 手写 |
| 进 git | 是 |

## 内容

| 文件 | 行数（2026-10-19） | 职责 |
|---|---|---|
| `run_layer_test.py` | 约 400 | 逐用例加载 → `run_once`（重复 N 次计时）→ 与 `out_ref.bin` 逐字节比对 → 写 `data/results/board_layer_test.json` → 打末行令牌 |
| `.gitkeep` | 0 | 占位 |

脚本**不重新生成任何数据**：`in.bin / w.bin / params.bin / lut.bin / out_ref.bin` 全部直接读
`data/layers/cases/<case>/` 下已有的裸小端文件（由 `sim/scripts/make_hls_case.py` 冻结，
已由 HLS C 仿真 `data/results/hls_csim.json` 6/6 逐字节验证过）。

### 三个判据（缺一不可）

| # | 判据 | 失败时的令牌 | 为什么这么定 |
|---|---|---|---|
| 1 | `AP_DONE` 真的置起来（不是轮询到超时还当成功） | `ap_done_timeout`（附 `AP_CTRL` 终值） | "跑完了"≠"跑对了"；超时绝不能算通过 |
| 2 | `out` 与 `out_ref.bin` **逐字节相等** | `mismatch`（附 `bad/total` 与首个不等 `(c,h,w)`） | 定点口径差一点就会整片偏，必须字节级 |
| 3 | `perf_counter()` 量 `ap_start`→`ap_done` 墙钟，`MACs/elapsed` 得 GOPS | ——（成功才有数） | 本仓库此前**没有**本引擎的实测数；`15.30 GOPS` 是探针的数，不能挪用 |

额外两道保险：

- **0xCC 哨兵**：输出缓冲分配后先填 `0xCC`，跑完全 `0xCC` 说明 PL 一个字节都没写（比"全 0 且 ref 也全 0"更能区分"没写"和"算对了"）。
- **启动前 idle 检查**：`AP_CTRL.bit2 != 1` 时直接抛错，不硬启动（避免上一轮没收尾时跑出脏数据）。

## 规则

- **不要把这个脚本拿到 PC 上跑**：没有 `/dev/mem`、没有 `pynq`，加载 Overlay 必失败。此时脚本会打
  `BOARD_LAYER_FAIL not_on_board` 并退出，**不会**退化成"在板上之外模拟一遍"。
- stdout **只打 ASCII 令牌**（板子控制台 GBK，中文/`⇒` 会 `UnicodeEncodeError`）；详细说明写 JSON 与 README。
- 所有写出 `newline="\n"`、`encoding="utf-8"`、JSON `ensure_ascii=True`。
- 顶部 `sys.dont_write_bytecode = True`（防 `__pycache__` 触发 `check_repo.py` check d)）。
- 依赖只有 `numpy` + `pynq`（镜像自带），**禁止 `pip install`**。
- 物理地址一律由驱动从 `.hwh` 解析，脚本不许出现地址字面量。

## 怎么用

### 1. 拷到板上

```powershell
# PC（PowerShell）：把仓库同步到板子（IP 换成实际值）
scp -r D:\zynq-yolo xilinx@192.168.2.99:/home/xilinx/
```

板上必须齐三样：`src/ps/`（驱动 + 本脚本）、`data/layers/cases/`（6 个用例）、
`board/overlay/conv_engine.bit` + `conv_engine.hwh`（**同名同目录**，PYNQ 靠 `.hwh` 建 `ip_dict`）。

### 2. 跑

```bash
cd /home/xilinx/zynq-yolo
sudo python3 src/ps/host/run_layer_test.py                 # 全部 6 个用例
sudo python3 src/ps/host/run_layer_test.py --case k3s1_c3_40   # 只跑一个
sudo python3 src/ps/host/run_layer_test.py --list          # 列出用例
sudo python3 src/ps/host/run_layer_test.py --repeat 5 --timeout 20 --json /tmp/board.json
sudo python3 src/ps/host/run_layer_test.py --no-download   # 比特流已下载，只绑 MMIO
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--bitfile` | `board/overlay/conv_engine.bit` | Overlay 路径（同目录需同名 `.hwh`） |
| `--cases-dir` | `data/layers/cases` | 用例根目录 |
| `--driver-dir` | `src/ps/pynq_driver` | `conv_engine.py` 所在目录 |
| `--case NAME` | 全部用例 | 可重复给多次 |
| `--repeat N` | `3` | 每用例跑几次；报告取**最小值**（抖动只会让耗时变大） |
| `--timeout S` | `10` | `ap_done` 轮询超时（秒） |
| `--no-download` | 关 | 跳过比特流下载，只绑 MMIO |
| `--json PATH` | `data/results/board_layer_test.json` | 结果落盘路径 |

### 3. 看输出

每个用例一行 `CASE_OK` / `CASE_FAIL`，每次运行一行 `RUN`，失配时一行 `MISMATCH`（含首个不等的 `(c,h,w)` 与两边取值）。
末行固定是一种令牌：

```text
BOARD_LAYER_OK <case> <elapsed_ns> <gops>            # --case 单用例全通过
BOARD_LAYER_OK all_<n>_cases <elapsed_ns> <gops>     # 多用例全通过（聚合：sum(MACs)/sum(min elapsed)）
BOARD_LAYER_FAIL <reason>                            # 任一环失败（reason 是 ASCII、无空格）
```

`reason` 取值：`not_on_board` / `overlay_missing` / `overlay_load_error` / `no_cases_found` /
`case_not_found:...` / `load_error` / `run_error` / `ap_done_timeout` / `compare_error` /
`mismatch` / `partial_fail:<ok>_ok_<fail>_fail`。

### 4. 结果 JSON（`data/results/board_layer_test.json`）

关键字段（每用例一条 `cases[]`）：

| 字段 | 含义 |
|---|---|
| `status` / `reason` | `BOARD_LAYER_OK` 或 `BOARD_LAYER_FAIL` + 原因 |
| `windows` | 两个 AXI-Lite 窗口的 **实际 `ip_dict` 键名与物理地址**（证明地址来自 `.hwh`，不是写死的） |
| `ip_dict_keys` | 板上 `overlay.ip_dict` 的全量键名（换布局时能一眼看出差异） |
| `macs` | `C_OUT*C_IN*K*K*H_OUT*W_OUT` |
| `elapsed_ns_runs[]` | 每次 `run_once` 的 `ap_start`→`ap_done` 墙钟（ns） |
| `elapsed_ns` / `gops` | 取最小值那一趟；`gops = macs / elapsed_ns / 1`（MAC 数 / 秒 / 1e9） |
| `ap_done` / `idle_before` / `sentinel_intact` | 判据 1 与两道保险的观测值 |
| `bytes_total` / `bytes_bad` / `mismatch` | 判据 2：总字节、不等字节数、首个不等的 `{c,h,w,board,ref}` |
| `pc_side_checks` | 仅当文件由 PC 侧逻辑检查写出时存在（**不是板测数据**） |

> `gops` 的口径是 **MAC/秒**（乘加算 1 次），与 `data/results/hls_engine.json` 里 HLS 报告的
> 时钟/延迟口径不同，报告里引用时要写清楚是"实测 MAC/秒"，别和综合报告的数字混着比。

## 已知限制（截至 2026-10-19）

- 本脚本**尚未在真实板子上执行过**：写成本文件时 `board/overlay/conv_engine.bit` 还不存在
  （只有 `.hwh`），本机是 Windows 主机、没有 Zynq 板卡与 PYNQ 环境。
- 因此 `data/results/board_layer_test.json` 当前记录的是 `BOARD_LAYER_FAIL` / `not_on_board`，
  **不含任何 `elapsed_ns` 或 `gops`**。上板跑一次后该文件会被真实数据覆盖。
- 已在 PC 上验证的只有：6 个用例的 `meta.txt` 解析与 4 个输入文件长度校验、逐字节比较器
  （自比 0 不等、改 1 字节能抓到且下标正确）、以及用假 `Overlay`/假 `MMIO` 做的寄存器协议模拟
  （偏移、12 字节间隔、64 位指针高低拆写、11 个标量、`AP_CTRL=0x01`、`invalidate()`、非 idle 拒绝）。
  这些**不能替代**板上验证。
