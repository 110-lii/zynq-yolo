#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成可提交的干净工程包（含运行必需件 + 清单 + SHA256 + 自检）。

设计原则
--------
1. **以 git 的提交边界为基**：`git ls-files --cached --others --exclude-standard`
   ⇒ 本机 venv、Vivado 运行目录、训练环境不会进包。
2. **显式补运行必需件**：`.bit/.hwh`、ncnn 模型 `.bin/.param/.table`、标定 JSON、
   球分类器权重、`build/reports/` 的资源/时序报告。这些大多被 `.gitignore` 排除或
   属“可重建但评审需要”的证据。
3. **显式排除本机产物**：`build/vivado`、`build/hls*`、`build/tmp*`、`data/train`、
   `data/ball_roi*`、`*.exe`、`.srcs/.gen/.Xil` 等。
4. **出包后在导出目录上跑 `tools/check_repo.py`**，末行必须是 `CHECK_REPO_OK`；
   不通过则不生成 zip（除非 `--allow-check-fail`）。

用法::

    python tools/make_submission.py                 # 导出 + 自检 + zip
    python tools/make_submission.py --no-zip        # 只导出
    python tools/make_submission.py --out D:\\tmp\\pkg
"""
import argparse
import csv
import hashlib
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent.parent

# 本机/过程产物：绝不进包（前缀匹配，POSIX 风格）
EXCLUDE_PREFIXES = (
    ".git/", ".kilo/", ".train/", ".srcs/", ".gen/", ".Xil/", ".dsh-acl-recovery/",
    ".research/", "uploads/", "dist/", "NA/",
    "build/vivado/", "build/venv", "build/hls", "build/tmp", "build/ip_repo",
    "build/ncnn-win/", "build/bw_83mhz/", "build/ce_83mhz_baseline/",
    "build/ball_rois", "build/tracks_", "build/stream/", "build/preview_",
    "build/tflite_", "build/tf_saved_", "build/viz/",
    "build/train_student/", "build/calib_", "build/ila_",
    "build/_", "build/hdmi_",
    "data/train/", "data/ball_roi", "data/ball_roi_full",
    "data/layers/pb160/", "data/golden/pb160/", "data/models/quant_pb160/",
    "data/results/newmatch_frames/", "data/results/viz/",
    "weights/",
)

# 单文件排除（相对路径或后缀）
EXCLUDE_SUFFIXES = (
    ".exe", ".pyc", ".pyo", ".jou", ".str", ".log", ".dcp", ".ltx",
    ".npy", ".onnx", ".tflite", ".pt", ".pth", ".ckpt", ".bak", ".out",
)
EXCLUDE_NAMES = {"usage_statistics_webtalk.html", "usage_statistics_webtalk.xml"}

# 显式补入的运行必需件（相对路径或 glob）
EXTRA_GLOBS = (
    "board/overlay/*.bit",
    "board/overlay/*.hwh",
    "build/models/person_v8n25.bin",
    "build/models/person_v8n25.param",
    "build/models/person_v8n25.table",
    "build/models/ball_roi.npz",
    "build/ball_weights.h",
    "data/calib/*.json",
    "data/models/*.npz",
    "build/reports/**/*.rpt",
    "build/reports/**/*.json",
)

# 这些目录即使已被 git 跟踪，也不该进包（防止历史误 add）
EXCLUDE_TRACKED_DIRS = (
    "build/tmp_", "build/tmp/", ".srcs/", ".gen/", "NA/",
)


def rel_posix(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def is_excluded(rel: str) -> bool:
    if rel in EXCLUDE_NAMES:
        return True
    if any(rel.endswith(s) for s in EXCLUDE_SUFFIXES):
        return True
    if any(rel.startswith(p) for p in EXCLUDE_PREFIXES):
        return True
    if any(rel.startswith(p) for p in EXCLUDE_TRACKED_DIRS):
        return True
    # build 根下的临时图片/二进制
    if rel.startswith("build/") and rel.count("/") == 1:
        name = rel.split("/", 1)[1]
        if name.startswith(("tmp_", "test", "sample", "frame", "f720", "calib_cand",
                            "calib_frame", "calib_check", "calib_list")):
            return True
    return False


def git_paths() -> list[str]:
    proc = subprocess.run(
        ["git", "-c", "core.quotepath=false", "-C", str(ROOT),
         "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if proc.returncode != 0:
        raise SystemExit("git ls-files 失败（必须在 git 仓库内运行）: " +
                         proc.stderr.decode("utf-8", "replace"))
    out = []
    for raw in proc.stdout.split(b"\0"):
        if not raw:
            continue
        rel = raw.decode("utf-8", "replace").replace("\\", "/")
        if rel and (ROOT / rel).is_file() and not is_excluded(rel):
            out.append(rel)
    return sorted(set(out))


def extra_paths() -> list[str]:
    out = []
    for pat in EXTRA_GLOBS:
        for p in ROOT.glob(pat):
            if p.is_file():
                rel = rel_posix(p)
                if not is_excluded(rel):
                    out.append(rel)
    return sorted(set(out))


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "dist"), help="输出目录（默认 dist/）")
    ap.add_argument("--zip", dest="zip", action="store_true", default=True)
    ap.add_argument("--no-zip", dest="zip", action="store_false")
    ap.add_argument("--allow-check-fail", action="store_true")
    args = ap.parse_args(argv)

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    name = f"zynq-yolo_submission_{stamp}"
    out_root = Path(args.out).expanduser().resolve()
    pkg = out_root / name
    if pkg.exists():
        shutil.rmtree(pkg)
    pkg.mkdir(parents=True)

    tracked = git_paths()
    extras = extra_paths()
    all_paths = sorted(set(tracked) | set(extras))

    manifest_rows = []
    total = 0
    for rel in all_paths:
        src = ROOT / rel
        dst = pkg / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        size = src.stat().st_size
        total += size
        manifest_rows.append({
            "path": rel,
            "bytes": size,
            "sha256": sha256_file(src),
            "kind": "extra" if rel in extras else "git",
        })

    # 清单
    manifest = pkg / "SUBMISSION_MANIFEST.csv"
    with manifest.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["path", "bytes", "sha256", "kind"])
        w.writeheader()
        w.writerows(manifest_rows)

    # 说明
    readme = pkg / "SUBMISSION_README.md"
    readme.write_text(
        "# 提交包说明\n\n"
        f"- 生成时间：{datetime.now().isoformat(timespec='seconds')}\n"
        f"- 文件数：{len(manifest_rows)}\n"
        f"- 总字节：{total}\n"
        f"- 来源：`git ls-files --cached --others --exclude-standard` + 显式运行必需件\n\n"
        "## 校验\n\n"
        "```bash\n"
        "python tools/check_repo.py        # 末行应为 CHECK_REPO_OK\n"
        "```\n\n"
        "逐文件校验：`SUBMISSION_MANIFEST.csv` 的 `sha256` 列。\n\n"
        "## 不包含\n\n"
        "- `.train/`、`uploads/`、`.srcs/`、`.gen/`、`build/vivado/`、`build/hls*/`、\n"
        "  `build/tmp*`、`data/train/`、`data/ball_roi*`、`*.exe`、Vivado 运行期日志。\n"
        "- 视频/大规模样本：按 `data/download/` 的脚本 + SHA256 重建。\n",
        encoding="utf-8",
    )

    # 在导出目录上自检（无 .git 时 check_repo 自动退回扫盘）
    check = subprocess.run(
        [sys.executable, str(pkg / "tools" / "check_repo.py"), str(pkg)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", check=False,
    )
    check_txt = check.stdout
    (pkg / "SUBMISSION_CHECK.txt").write_text(check_txt, encoding="utf-8")
    ok = "CHECK_REPO_OK" in check_txt
    print(check_txt)
    print(f"PACKAGE {pkg}")
    print(f"FILES {len(manifest_rows)} BYTES {total} CHECK {'OK' if ok else 'FAIL'}")

    if not ok and not args.allow_check_fail:
        print("SUBMISSION_PACK_FAIL")
        return 1

    if args.zip:
        zpath = out_root / f"{name}.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            for p in sorted(pkg.rglob("*")):
                if p.is_file():
                    z.write(p, p.relative_to(pkg.parent).as_posix())
        print(f"ZIP {zpath} {zpath.stat().st_size} bytes")

    print("SUBMISSION_PACK_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
