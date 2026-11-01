# checks/ —— 技能包自带检查

> 上级：[`../README.md`](../README.md)

| | |
|---|---|
| 本目录装什么 | 拿到就能跑的验证脚本 |
| 谁产生 | 手写 |
| 进 git | 是 |

## 内容

| 路径 | 需要板？ | 说明 | 判据（末行令牌） |
|---|---|---|---|
| [`overlay_health.py`](overlay_health.py) | **是**（`pynq` 只在板上） | 只读体检：同名 `.hwh` 是否存在 → `Overlay()` 能否加载 → IP 表是否非空 → `s_axi_control` / `s_axi_control_r` 是否成对 | `OVERLAY_HEALTH_OK`；缺 `_r` 时 `OVERLAY_HEALTH_WARN_CONTROL`（退出码 2，`--strict` 判 FAIL） |

## 规则

- 每个脚本都要有 `--help`，参数在陌生机器上可读；
- **退出码必须能失败**：非 0 即不通过，不要在「有问题」时仍返回 0；
- 与另一位队友的 `hls_axi_stream_accel/checks/` 保持同一套约定（`--selftest` / 末行令牌 / 非 0 退出）。

## 怎么用

```bash
python3 checks/overlay_health.py --help
python3 checks/overlay_health.py /path/to/xxx.bit     # 板上跑
```
