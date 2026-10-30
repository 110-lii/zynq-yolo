# data/ —— 测试数据与参考结果

> 上级：[`../README.md`](../README.md)
> 唯一事实源：[`../docs/repo_spec.md`](../docs/repo_spec.md) §2 / §4

| | |
|---|---|
| 本目录装什么 | 跑通与评测所需的**数据**：算法参数/权重、标定参数、样本、金标准、结果输出 |
| 谁产生 | 手写（清单/标注）/ 工具产生（下载、转换、推理输出） |
| 进 git | 是（**大文件除外**，见下方规则） |

## 内容

| 路径 | 说明 | 谁产生 | 进 git |
|---|---|---|---|
| `models/` | 模型权重与网络定义（`.pt` / `.onnx` 等）。★ **D004 已裁定走 YOLO**（2026-10-19），故本目录现放 YOLOv8n 权重；**权重本体不入 git**（见下「大文件」） | 外部下载 + 本地导出 | ❌ 权重本体；✅ 目录 `.gitkeep` |
| `calib/` | 标定数据：摄像头/传感器标定参数、色彩与坐标换算系数 | 手写 + 工具产生 | ✅ |
| `samples/` | 测试样本：单帧图片、视频片段、输入激励 | 手写 / 外部采集 | 小样本 ✅；大样本 ❌ |
| `golden/` | ★ **金标准**：ONNX 逐层中间张量。`*_golden.json`（118 KB，**入库**）记 270 个张量的 shape/dtype/统计量/哈希；`*_golden.npz`（320=23 MB / 640=91 MB，**不入库**）存实际数值，由 `tools/make_golden.py` 确定性重建 | 工具产生 | json ✅；npz ❌ |
| `results/` | 结果产物：推理输出、评测指标、对比表数据 | 工具产生 | 择优 ✅（摘要/指标进，原始大文件不进） |
| `download/` | ★ **外部下载脚本 + `SHA256SUMS.txt`**（大文件替代方案 1 的落点，**已实现**：`fetch_models.py`） | 手写 | ✅ **必须入库**（它是可复现的凭据） |

> 当前磁盘事实（2026-10-19）：
> `data/models/` 有 `yolov8n.pt`（6.25 MB）+ `yolov8n_{640,320}.onnx`（各 ≈12 MB）—— **三者都被 `.gitignore` 排除**，
> 靠 [`download/fetch_models.py`](download/fetch_models.py) + [`download/SHA256SUMS.txt`](download/SHA256SUMS.txt) 复现；
> `data/golden/` 有 **两份逐层 golden**：`yolov8n_{320,640}_golden.json`（**入库**，各 118 KB / 270 个张量）
> + `.npz`（**不入库**，320=23 MB / 640=91 MB），由 [`../tools/make_golden.py`](../tools/make_golden.py) 以固定种子重建；
> `data/results/` 有 `probe_yolov8n_*.json`（**几 KB 的指标，入库**，是报告里 MAC/权重的数字来源）；
> `calib/` `samples/` **仍为空**（要等摄像头/数据裁定后才填）。
> ⚠️ **`models/` 这个目录名是骨架沿用的**；方案在 D004 裁定前曾长期未定，现已定为 YOLO。

## 来源与用途（D005，用户 2026-10-30 裁定 = (a) 内部使用声明）

| | |
|---|---|
| 片段来源 | 公开足球赛事转播（`data/samples/incidents/` 的 12 段、`data/samples/scenes/` 的 16 段分层片段） |
| 用途 | **仅用于本课程作业的内部评测与复现**，不用于训练对外发布的模型，不再分发 |
| 是否随包分发 | **否**。`tools/make_submission.py` 只带运行必需件（ncnn 模型、标定、配置），视频与训练集都不进 `dist/` |
| 是否可再分发 | **不宣称可再分发**。本仓库不授予任何人对这些片段的使用权 |
| 使用权的确认 | **用户 2026-10-30 明确确认：本人有权在作业范围内使用这些片段**（记录见 `ai/coordination/DECISIONS.md` D005 与「变更记录」2026-10-30） |
| 长期计划 | 若项目要对外发布，按 D005 的 (b) 分支改用许可明确的可分发数据集，并重做标定与相关指标 —— **不阻塞本次提交** |

> 纪律：**不把「能从网上拿到」当成「可以分发」**。上面这一节是声明，不是许可；
> 任何人想把这些片段的副本放进公开仓库、演示视频或附件，都要先改这条 decision。

## 规则

- ### ★ 大文件不入 git
  单个大文件（算法权重、原始视频、大图集等）**不得直接提交**。判定线由团队在根 `.gitignore` 中固化；
  实践建议：单文件 > 10 MB（或总量 > 100 MB）即视为大文件，必须走下列替代方案之一。
- ### 替代方案（**已选定方案 1**，2026-10-19）
  1. **✅ 采用：外部下载脚本** —— 已落地为 [`download/fetch_models.py`](download/fetch_models.py) +
     [`download/SHA256SUMS.txt`](download/SHA256SUMS.txt)（URL 钉版本、下载后核 SHA256、
     严格项不符即 exit 1；导出项哈希随工具版本变化时**打印实际值并继续**，不假装可逐字节复现）。
  2. **未采用：Git LFS** —— 理由：Gitee/GitHub 的 LFS 配额与评审端是否装 `git-lfs` 都不可控，
     而「脚本 + 校验值」在纯 `git clone` 的机器上也能复现。**若日后再需要，须在此登记并改根 `.gitignore`。**
- **可复现性底线**：任何下载来的数据，必须记录**来源 URL + 版本/日期 + 校验值**，否则 `report/` 无法复现。
  ★ 本项目的做法：URL 写死在脚本里（含版本 tag），哈希写进 `SHA256SUMS.txt`（**入库**），
  并用 `scripts/../tools/onnx_probe.py` 把「权重 → MAC / 体积」这一步也变成可重跑的测量。
- **数字必须标来源**（§6 纪律）：`results/` 里的任何指标，都要能追溯到生成它的脚本与输入样本。
- **不要用退出码判断成功**（§5）：数据转换/评测脚本的结论要落到日志令牌或显式比对结果上。
- 命名硬约束（§1）：目录名与文件名只用 `a-z 0-9 _ - .`；日期后缀用 `_YYYYMMDD`；**文件正文可用中文**。
- **禁止入库**（§1 规则 6）：`__pycache__/`、`.venv/`、`build*.log`、`vivado*.log|jou`、`.Xil/`、`*.dcp`。
- 本机环境事实（§6）：DDR3 仅 **1 GB**（7020，DDR3 挂在 PS 侧，PL 须走 AXI HP）；旧项目结论**中文一手原文**（`D:\FPGA\oi\CLAUDE.md:206`，逐字含表格管道符）`| DSP 仅 220 个 | 不做大算力 CNN |` ——
  数据规模与算法体量必须与该上限相容。（英文「DSP only 220」是转述，非一手原文。）

## 怎么用

```powershell
# 0. 现状核对
Get-ChildItem -Recurse D:\zynq-yolo\data

# 1. ✅ 已实现：拉权重 + 导出 onnx + 核 SHA256（需要代理访问 GitHub）
$env:HTTP_PROXY="http://127.0.0.1:7890"; $env:HTTPS_PROXY="http://127.0.0.1:7890"
python data\download\fetch_models.py
#    期望末行 FETCH_MODELS_OK；严格项（.pt）哈希不符会 exit 1

# 2. ✅ 已实现：量出模型的计算量与权重体积（报告里 MAC / 权重的数字来源）
python tools\onnx_probe.py --model data\models\yolov8n_640.onnx --json data\results\probe_yolov8n_640.json
#    期望末行 PROBE_OK

# 3. 放入小样本/标定/金标准（可直接进 git）
#    Copy-Item <样本> D:\zynq-yolo\data\samples\
#    Copy-Item <期望输出> D:\zynq-yolo\data\golden\

# 4. 跑评测 -> 结果落 results/
#    评测脚本见 ../src/ps/host/ 与 ../tools/collect_metrics.py（TODO：尚未创建）

# 5. 只把「指标摘要 / 对比表 / 金标准」提交入库，原始大文件保持本地
```
