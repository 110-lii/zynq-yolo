# data/download/ —— 外部大文件的可复现获取方式

> 上级：[`../README.md`](../README.md) · 仓库规范 [`../../docs/repo_spec.md`](../../docs/repo_spec.md)
>
> **为什么有这个目录**：`../README.md` §「大文件不入 git」规定**单文件 > 10 MB 不得直接提交**。
> 本项目的模型权重（`yolov8n.pt` 6.25 MB、导出后的 `.onnx` 约 12 MB × N 个）正好跨在这条线上，
> 因此**权重不入库，改为「脚本 + 校验值」**。任何人 clone 本仓库后跑一遍本目录的脚本即可得到同样的文件。

---

## 内容

| 脚本 | 取什么 | 校验 |
|---|---|---|
| [`fetch_models.py`](fetch_models.py) | YOLOv8n 预训练权重（`.pt`）→ 导出 `.onnx`（640 / 320） | `SHA256SUMS.txt` |
| [`SHA256SUMS.txt`](SHA256SUMS.txt) | 上述文件的 SHA256（**入库**，用来证明「拉到的就是当初用的」） | 自校验 |

---

## 规则

1. **权重不进 git**：`data/models/*.pt`、`data/models/*.onnx` 已被根 `.gitignore` 排除。
   入库的只有**脚本 + 校验值**，这是 `data/README.md` 说的「替代方案 1」。
2. **校验值必须入库且必须能对上**：脚本下载后自己核 `SHA256SUMS.txt`，
   任何不一致 ⇒ 退出码非 0 并打印实际值（**不静默继续**）。
3. **来源必须可追溯**：脚本里写死 URL 与版本 tag，不写「最新版」。
4. **导出是确定性的参考值，但不保证逐位复现**：ONNX 导出可能随 `torch` / `ultralytics` /
   `onnx` 版本变化而有差异 ⇒ `SHA256SUMS.txt` 记的 `onnx` 校验值**只在脚本声明的版本组合下有效**，
   版本不符时脚本会提示「校验不符但可继续」，并**打印实际哈希**供人判断。

---

## 怎么用

```powershell
cd D:\zynq-yolo
$env:HTTP_PROXY="http://127.0.0.1:7890"; $env:HTTPS_PROXY="http://127.0.0.1:7890"   # 需要代理才能访问 GitHub

# 1) 拉权重 + 导出 onnx + 核对校验值
python data\download\fetch_models.py

# 2) 只核对已有文件（不下载）
python data\download\fetch_models.py --verify-only
```

期望末行：`FETCH_MODELS_OK`；有问题时 `FETCH_MODELS_FAIL`（退出码 1）。

---

## 当前记录（**时点值**，2026-10-19）

| 文件 | 大小 | 说明 |
|---|---|---|
| `data/models/yolov8n.pt` | 6.25 MB | Ultralytics 官方 v8.3.0 资产 |
| `data/models/yolov8n_640.onnx` | ≈12.11 MB | `imgsz=640, opset=12, dynamic=False` |
| `data/models/yolov8n_320.onnx` | ≈12.23 MB | 同上，`imgsz=320` |

> ⚠️ `.onnx` 比 `.pt` 大是因为 ONNX 以 **FP32** 存权重（3 151 904 参数 × 4 B ≈ 12.6 MB）；
> 而**量化后**的 INT8 权重只有 **3.15 MB**（见 `../../docs/yolo_on_zynq7020_survey.md` 的实测表）。
> 进入 PL 的排布要用量化后的体积算，不要用 ONNX 的 FP32 体积。
