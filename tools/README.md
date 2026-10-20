# tools/ —— 仓库校验、文档生成、度量采集脚本

> 上级：[`../README.md`](../README.md)
> 唯一事实源：[`../docs/repo_spec.md`](../docs/repo_spec.md) §1 / §6（对应指南 3.3.5.2 校验脚本类技能）

| | |
|---|---|
| 本目录装什么 | **工具脚本**：仓库合规自检、度量采集、报告渲染 |
| 谁产生 | 手写 |
| 进 git | 是 |

## 内容

> ⚠️ 本节原写「`tools/` 为空目录、脚本一个都没创建」——**早已过时**（2026-10-20 更正）。
> 现状：**35 个自有 `.py`**（`tools/check_repo.py` 的 check f 实测口径）已落地。
> 下表只列关键脚本；完整清单看目录。状态列**如实**标注，**不要假装已存在/已运行**。

| 路径 | 说明 | 状态 |
|---|---|---|
| `check_repo.py` | **仓库自检**：命名纯 ASCII / 必需目录 / 每目录 README / 禁入文件 / 权重表 / py 语法 | ✅ 已创建（令牌 `CHECK_REPO_OK`） |
| `collect_metrics.py` | **资源/时序度量采集**：解析 `build/reports/*` 产指标表 | **TODO（未创建）** |
| `md_to_docx.py` | **报告渲染**：把 `report/` 下 Markdown 渲染为提交用文档 | **TODO（未创建）** |
| `ball_visibility_sweep.py` | **球可见性扫描**（D010 范围收缩依据之一） | ✅ 已创建 |
| `ball_cv.py` | **传统 CV 球定位可行性原型**：结论 `no_work`（人眼 2/10，判据假绿分析） | ✅ 已创建（结果 `data/results/ball_cv.json`） |
| `pitch_geometry.py` | **白线反解球场几何可行性**：n=10 n_pass=0（D010 范围外 b 的依据） | ✅ 已创建 |
| `person_scale_check.py` | **人可见性 vs 输入分辨率**（D010 收窄后「人看得见」判据） | ✅ 已创建 |
| `int8_sim.py` / `pynq_net.py` | INT8 量化验证 / 整网 PC 编排（后端可插拔，PL 后端待接） | ✅ 已创建 |
| `make_calib_from_video.py` / `find_incidents.py` / `log_incident_decisions.py` / `make_incident_review.py` / `conf_calibration.py` / `draw_detections.py` / `eval_calib_set.py` / `class_coverage.py` / `fetch_calib_real.py` / `onnx_probe.py` / `fps_feasibility.py` / `design_tradeoffs.py` / `export_layers.py` / `make_golden.py` / `ref_int8_conv.py` | 数据集 / 标定 / 评测 / 金标准 / 契约参考实现族 | ✅ 已创建 |
| （其他脚本） | 需要时新增；新增后必须在上表登记并补 `README` 说明 | — |

## 规则

- **状态必须诚实**：未创建的脚本标 `TODO`，禁止在 README 或报告中声称其已存在/已运行。
- 脚本一律可**一条命令**运行，且**不以退出码单独判定成功**（§5）：应打印明确结论/令牌（如 `CHECK_OK` / `CHECK_FAIL`）与失败明细。
  > 本项目既有判据令牌：构建 `DONE_OK`、上板 `PROGRAM_OK`。工具脚本可沿用同名风格。
- 脚本的判据来源必须是 [`../docs/repo_spec.md`](../docs/repo_spec.md)，**不得在脚本里另立一套规则**；规范更新时同步改脚本。
- `collect_metrics.py` 产出的数字必须**标来源文件路径**（§6 纪律：数字必须标来源）。
- 命名硬约束（§1）：脚本名与目录名只用 `a-z 0-9 _ - .`；日期后缀用 `_YYYYMMDD`；**文件正文可用中文**。
- 禁止把工具运行产物（`__pycache__/`、临时 `.log`）提交入库（§1 规则 6）。
- 本目录脚本属「校验脚本类技能」，可被 `skill/` 引用；若沉淀为技能包，`skill/` 下每项须含 `README.md`（适用范围/使用方法/失效条件，指南 3.3.5.2）。
- 窗口/环境：Windows + `pwsh`；本机 Python 为 `D:\python\python.exe` **3.13.1**（第三方库清单见 [`../docs/repo_spec.md`](../docs/repo_spec.md) §6 环境事实表）；本机工具链仅 Vivado / Vitis / Vitis_HLS **2020.2**。
- **本目录脚本必须零第三方依赖或用纯标准库**（`check_repo.py` 即纯标准库：`os` / `re` / `sys` / `pathlib`），以保证评审机器上 `python tools/check_repo.py` 直接可跑。

## 怎么用

```powershell
# 1. ✅ 仓库自检（纯标准库，无第三方依赖）
& D:\python\python.exe D:\zynq-yolo\tools\check_repo.py
#    期望末行：SUMMARY: 6 PASS / 0 FAIL / 0 WARN  +  结论令牌 CHECK_REPO_OK
#    退出码 0 = 无 FAIL；有 FAIL 时为 1。也可在仓库根直接 `mingw32-make check`。

# 2. 【TODO，未创建】采集资源/时序度量
#    python D:\zynq-yolo\tools\collect_metrics.py --reports D:\zynq-yolo\build\reports
#    期望：输出指标表（含来源文件路径），可直接粘进 ../report/

# 3. 【TODO，未创建】渲染报告
#    python D:\zynq-yolo\tools\md_to_docx.py --src D:\zynq-yolo\report --out D:\zynq-yolo\report\

# 4. 建议节奏：每次改动 src/ 或 build/ 后跑一次 check_repo.py，
#    每轮构建后跑一次 collect_metrics.py（见 ../build/README.md 的报告轮转约定）
```

## 现状

| 脚本 | 状态 | 判据令牌 |
|---|---|---|
| `check_repo.py` | ✅ 已实现（6 项检查：命名 / 必需目录与根文件 / 每目录 README / 禁入文件 / 权重表 / py 语法） | `CHECK_REPO_OK` |
| `collect_metrics.py` | ⬜ TODO | —— |
| `md_to_docx.py` | ⬜ TODO | —— |
| `ball_visibility_sweep.py` | ✅ 已实现（分辨率 × 切图 × 模型 × 置信度四组扫描 + 基线/阴性/阳性对照） | `SWEEP_DONE <n>` / `SWEEP_FAIL <原因>` |
| `ball_cv.py` | ✅ 已实现（运动残差 + 外观先验 + 时空关联；含 null/static/shuffle/synthetic/ablate 五类对照） | `BALL_CV_DONE`（结果 JSON 内 `verdict_class`） |
| `pitch_geometry.py` | ✅ 已实现（白线掩膜覆盖率判据 + 阴性/阳性对照） | `PITCH_GEOM_*` |
| `int8_sim.py` / `pynq_net.py` | ✅ 已实现 | `PYNQ_NET_OK` / `PYNQ_NET_FAIL` 等 |
