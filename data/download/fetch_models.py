"""拉取 YOLOv8n 权重、导出 ONNX，并用 SHA256 证明「拉到的就是当初用的」。

为什么要有这个脚本（而不是把权重直接提交）：
    `data/README.md` 规定**单文件 > 10 MB 不进 git**。本项目的 `.onnx` 恰好 12 MB 量级，
    所以走「替代方案 1：外部下载脚本 + 校验值」。入库的只有本文件与 `SHA256SUMS.txt`。

为什么校验值要分「严格」与「非严格」两类：
    · `.pt`（下载所得）⇒ **严格**：字节必须一致，否则说明源变了或被篡改。
    · `.onnx`（本地导出所得）⇒ **非严格**：ONNX 导出随 torch / ultralytics / onnx 版本变化，
      同一份 `.pt` 在不同版本下导出的字节可能不同。**不假装它可逐字节复现**，
      而是记下「当时用的版本组合 + 当时的哈希」，版本不符时**打印实际值并继续**，让人自己判断。

判据：
    末行 `FETCH_MODELS_OK`（退出码 0）或 `FETCH_MODELS_FAIL`（退出码 1）。
    本脚本可在无网络时用 `--verify-only` 单独核已有文件。

用法：
    python data/download/fetch_models.py                 # 下载 + 导出 + 校验
    python data/download/fetch_models.py --verify-only    # 只校验
    python data/download/fetch_models.py --write-sums     # 重写 SHA256SUMS.txt（改版本时才用）
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[2]
MODELS = ROOT / "data" / "models"
SUMS = Path(__file__).resolve().parent / "SHA256SUMS.txt"

# ★ 版本钉死，不写「最新版」——「可复现」的前提是来源确定
WEIGHT_URL = "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8n.pt"
WEIGHT_NAME = "yolov8n.pt"
EXPORT_SIZES = (640, 320)
OPSET = 12

# 导出产物是非严格项（见模块 docstring）
STRICT = {WEIGHT_NAME}


def sha256_of(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_sums() -> dict:
    d = {}
    if SUMS.is_file():
        for line in SUMS.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 2:
                d[parts[-1]] = parts[0]
    return d


def write_sums(files: dict) -> None:
    lines = [
        "# SHA256SUMS —— 由 data/download/fetch_models.py --write-sums 生成，勿手改",
        "#",
        "# 严格项（.pt，下载所得）：字节必须一致。",
        "# 非严格项（.onnx，本地导出）：随 torch/ultralytics/onnx 版本变化，仅供对照。",
        "",
    ]
    for name in sorted(files):
        lines.append("%s  %s" % (files[name], name))
    SUMS.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def download(url: str, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    print("  下载 %s" % url)
    tmp = dst.with_suffix(dst.suffix + ".part")
    with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if total:
                print("\r    %5.1f%%  %.1f/%.1f MB" % (got * 100.0 / total, got / 1e6, total / 1e6), end="")
    print("")
    tmp.replace(dst)


def export_onnx() -> None:
    try:
        from ultralytics import YOLO
    except ImportError:
        print("  ⚠ 未安装 ultralytics，跳过导出（只校验 .pt）")
        return
    import shutil

    m = YOLO(str(MODELS / WEIGHT_NAME))
    for imgsz in EXPORT_SIZES:
        dst = MODELS / ("yolov8n_%d.onnx" % imgsz)
        if dst.is_file():
            print("  已存在，跳过导出：%s" % dst.name)
            continue
        print("  导出 imgsz=%d opset=%d ..." % (imgsz, OPSET))
        p = m.export(format="onnx", imgsz=imgsz, opset=OPSET, simplify=False, dynamic=False)
        if Path(p).resolve() != dst.resolve():
            shutil.move(p, dst)
        print("    -> %s (%.2f MB)" % (dst.name, dst.stat().st_size / 1e6))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="拉取模型并校验 SHA256")
    ap.add_argument("--verify-only", action="store_true", help="不下载，只校验已有文件")
    ap.add_argument("--write-sums", action="store_true", help="重写 SHA256SUMS.txt")
    args = ap.parse_args(argv)

    MODELS.mkdir(parents=True, exist_ok=True)
    expect = load_sums()

    if not args.verify_only:
        w = MODELS / WEIGHT_NAME
        if w.is_file():
            print("  已存在，跳过下载：%s" % w.name)
        else:
            download(WEIGHT_URL, w)
        export_onnx()

    # ---- 校验 ----
    targets = [WEIGHT_NAME] + ["yolov8n_%d.onnx" % s for s in EXPORT_SIZES]
    present = {n: (MODELS / n) for n in targets if (MODELS / n).is_file()}

    if args.write_sums:
        write_sums({n: sha256_of(p) for n, p in present.items()})
        print("  已写 %s（%d 项）" % (SUMS, len(present)))
        print("FETCH_MODELS_OK")
        return 0

    if not expect:
        print("  ⚠ 没有 SHA256SUMS.txt —— 只能报告实际哈希，无法判定")
        for n, p in present.items():
            print("    %s  %s" % (sha256_of(p), n))
        print("FETCH_MODELS_OK")
        return 0

    bad_strict = 0
    soft_diff = 0
    for n, p in present.items():
        act = sha256_of(p)
        exp = expect.get(n)
        strict = n in STRICT
        if exp is None:
            print("  ?  %-22s 无期望值（新文件？跑 --write-sums）  实=%s" % (n, act[:16]))
        elif act == exp:
            print("  OK %-22s sha256=%s" % (n, act[:16]))
        elif strict:
            print("  FAIL %-22s 严格项不符！期望=%s 实际=%s" % (n, exp[:16], act[:16]))
            bad_strict += 1
        else:
            print("  DIFF %-22s 非严格项（导出工具版本可能不同）  期望=%s 实际=%s"
                  % (n, exp[:16], act[:16]))
            soft_diff += 1

    missing = [n for n in targets if n not in present]
    if missing:
        print("  ⚠ 缺失：%s" % ", ".join(missing))

    print("")
    if bad_strict:
        print("FETCH_MODELS_FAIL —— %d 个严格项校验失败" % bad_strict)
        return 1
    if soft_diff:
        print("FETCH_MODELS_OK —— 严格项全对；%d 个导出项哈希不同（版本差异，已如实列出）" % soft_diff)
        return 0
    print("FETCH_MODELS_OK")
    return 0


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    sys.exit(main())
