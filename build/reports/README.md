# build/reports/ —— 综合与实现报告（产物区）

> 上级目录约定见 [`../README.md`](../README.md)；唯一事实源 [`../../docs/repo_spec.md`](../../docs/repo_spec.md) §2 / §4。

## 本目录装什么 / 谁产生 / 进 git

| 子目录 | 装什么 | 谁产生 | 进 git |
|---|---|---|---|
| `synth/` | 综合报告：`*_utilization_synth.rpt`、综合日志摘录 | Vivado（`build/scripts/` 驱动） | ❌ 只留最新 |
| `impl/` | 实现报告：`*_utilization_impl.rpt`、`*_routed.rpt`、DRC 摘要 | Vivado | ❌ 只留最新 |
| `timing/` | 时序报告：`*_timing_summary_routed.rpt`（**WNS / TNS / WHS** 出处） | Vivado | ❌ 只留最新 |
| `resource/` | 资源汇总（LUT / FF / DSP / BRAM 的最终数字） | Vivado + `tools/collect_metrics.py` | ❌ 只留最新 |
| `drc/` | DRC / 布线拥塞检查输出 | Vivado | ❌ 只留最新 |
| `baseline_<YYYYMMDD>/` | **写进设计报告的基准数字快照**（每次关键优化后打一份） | 手动复制 | ✅ **必须留** |

## 规则

1. **本目录下除 `baseline_*/` 与 `README.md` 外全部不入 git**（`.gitignore` 已排除）。
   理由：这些都是能由 `build/scripts/` 一条命令重建的**产物**，入库只会制造"数字对不上是哪一版"的歧义。
2. **`baseline_<YYYYMMDD>/` 是例外且必须提交** —— 它是 `report/` 里「优化前后对比表」每个数字的**出处**。
   打基线时**整目录复制**，不要只挑一个 rpt：`baseline` 要能独立支撑一张对比表。
3. **产物里的每个数字进报告前，必须写明它来自哪个 `baseline_/` 或哪次构建的日志令牌**（承 `docs/repo_spec.md` §6「数字必须标来源」）。
4. **判断构建成功看日志令牌 `DONE_OK`，不看退出码**（承 §5；Vivado 内部报错也可能 exit 0）。
5. `.gitkeep` 是**空目录占位**：git 不跟踪空目录，没有它 clone 后本目录的 5 个子目录会全部消失。
   **不要删除** `.gitkeep`，也不要在里面写内容。

## 怎么用

```
# 出综合与实现报告（脚本见 build/scripts/*.tcl，运行后自动导出）
mingw32-make bit

# 关键优化后打一份基线，供 report/ 对比表引用
#   cp -r build/reports/{synth,impl,timing,resource} build/reports/baseline_20261019/
```

> ⚠️ 本目录的 `README.md` 与 `baseline_*/` 是**提交件**；其余是**产物**。
> 混淆二者的后果是评审拿到的数字无法复核。
