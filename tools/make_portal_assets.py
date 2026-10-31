#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成赛事门户要求的提交件（PDF 技术文档 / RAR 代码包），并核对大小上限。

门户要求（2026-10-31 截图）::

    演示视频   MP4 / AVC(H.264) / 时长 <= 3 min / <= 300 MB
    技术文档   PDF / <= 50 MB
    全部代码   RAR / <= 200 MB
    承诺及授权书 PDF / <= 50 MB
    作品海报   PDF 或 PPT / <= 50 MB（选填）

本工具只负责两件能自动完成的事：
    1. docx -> PDF（调用本机 Microsoft Word 的 COM 接口，仅 Windows）；
    2. 把出包目录打成 RAR（调用 WinRAR 的 Rar.exe）。

视频、实物照片、签字授权书无法由脚本生成，只做存在性与大小核对。

用法::

    py tools/make_portal_assets.py --pdf
    py tools/make_portal_assets.py --rar dist/zynq-yolo_submission_YYYYMMDD_HHMM
    py tools/make_portal_assets.py --check --video <demo.mp4> --auth <auth.pdf>

RAR 可执行文件查找顺序：`--rar-exe` > 环境变量 `RAR_EXE` > PATH 中的 `rar` / `winrar`。
本机若未装 WinRAR，脚本会打印明确提示，不会假装成功。

日志令牌::

    PORTAL_PDF_OK / PORTAL_PDF_FAIL
    PORTAL_RAR_OK / PORTAL_RAR_FAIL
    PORTAL_CHECK_OK / PORTAL_CHECK_FAIL
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent.parent

MAX_PDF_BYTES = 50 * 1024 * 1024
MAX_RAR_BYTES = 200 * 1024 * 1024
MAX_VIDEO_BYTES = 300 * 1024 * 1024


def force_utf8_stdout():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def human(n):
    return f"{n / 1024 / 1024:.1f} MB"


def find_rar(explicit=None):
    if explicit:
        p = Path(explicit).expanduser()
        return str(p) if p.is_file() else None
    env = os.environ.get("RAR_EXE")
    if env and Path(env).is_file():
        return env
    for name in ("rar", "winrar", "Rar"):
        p = shutil.which(name)
        if p:
            return p
    return None


def docx_to_pdf(src: Path, dst: Path) -> bool:
    """用 Word COM 把 docx 转 PDF。仅 Windows + 已安装 Microsoft Word。"""
    if not src.is_file():
        print(f"PDF_SRC_MISSING {src}")
        return False
    if os.name != "nt":
        print("PDF_FAIL 需要 Windows + Microsoft Word（本机 Word COM）")
        return False

    script = ROOT / "build" / "_docx_to_pdf.ps1"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        "param([string]$Src,[string]$Dst)\n"
        "$ErrorActionPreference = 'Stop'\n"
        "$w = New-Object -ComObject Word.Application\n"
        "$w.Visible = $false\n"
        "try {\n"
        "  $d = $w.Documents.Open($Src, $false, $true)\n"
        "  $d.SaveAs([ref]$Dst, [ref]17)\n"  # 17 = wdFormatPDF
        "  $d.Close($false)\n"
        "} finally {\n"
        "  $w.Quit()\n"
        "}\n",
        encoding="utf-8",
    )
    dst.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
         str(src), str(dst)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", check=False,
    )
    if proc.returncode != 0 or not dst.is_file():
        print(proc.stdout[-2000:])
        print("PORTAL_PDF_FAIL")
        return False
    size = dst.stat().st_size
    if size > MAX_PDF_BYTES:
        print(f"PORTAL_PDF_FAIL {dst} {human(size)} > 50 MB")
        return False
    print(f"PORTAL_PDF_OK {dst} {human(size)} pages~")
    return True


def make_rar(src_dir: Path, out_rar: Path, rar_exe: str) -> bool:
    if not src_dir.is_dir():
        print(f"RAR_SRC_MISSING {src_dir}")
        print("PORTAL_RAR_FAIL")
        return False
    out_rar.parent.mkdir(parents=True, exist_ok=True)
    if out_rar.exists():
        out_rar.unlink()
    # Rar.exe a -r -ep1 <archive> <folder\*>  （-ep1：不含父目录名）
    proc = subprocess.run(
        [rar_exe, "a", "-r", "-ep1", "-idq", str(out_rar), str(src_dir / "*")],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", check=False,
    )
    if proc.returncode != 0 or not out_rar.is_file():
        print(proc.stdout[-2000:])
        print("PORTAL_RAR_FAIL")
        return False
    size = out_rar.stat().st_size
    if size > MAX_RAR_BYTES:
        print(f"PORTAL_RAR_FAIL {out_rar} {human(size)} > 200 MB")
        return False
    print(f"PORTAL_RAR_OK {out_rar} {human(size)}")
    return True


def check(args):
    ok = True
    rows = []

    def row(name, path, limit, required=True):
        nonlocal ok
        if not path:
            rows.append((name, "—", "未提供", "MISSING" if required else "SKIP"))
            if required:
                ok = False
            return
        p = Path(path).expanduser()
        if not p.is_file():
            rows.append((name, str(p), "文件不存在", "MISSING"))
            if required:
                ok = False
            return
        size = p.stat().st_size
        good = size <= limit
        rows.append((name, str(p), human(size), "OK" if good else f"> {human(limit)}"))
        if not good:
            ok = False

    row("技术文档 PDF", args.pdf or (ROOT / "report" / "technical_document.pdf"),
        MAX_PDF_BYTES, required=False)
    row("全部代码 RAR", args.rar, MAX_RAR_BYTES, required=False)
    row("演示视频", args.video, MAX_VIDEO_BYTES, required=True)
    row("承诺及授权书", args.auth, MAX_PDF_BYTES, required=True)
    row("作品海报（选填）", args.poster, MAX_PDF_BYTES, required=False)
    row("作品全貌图", args.overview, MAX_PDF_BYTES, required=False)

    print("PORTAL_ITEM                  PATH                                         SIZE        STATUS")
    for r in rows:
        print(f"{r[0]:<28} {r[1]:<44} {r[2]:<11} {r[3]}")

    # GitHub 链接：远端 + 未推送提交数
    try:
        remote = subprocess.run(["git", "-C", str(ROOT), "remote", "get-url", "origin"],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, check=False).stdout.strip()
        ahead = subprocess.run(["git", "-C", str(ROOT), "rev-list", "--count", "origin/main..HEAD"],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True, check=False).stdout.strip()
        print(f"GITHUB remote={remote or '—'} ahead_of_origin_main={ahead or '?'}")
        if ahead and ahead != "0":
            print("  ⚠️ 本地有未推送提交，提交前必须 git push")
            ok = False
    except Exception:
        print("GITHUB 无法读取 git 远端")

    print("PORTAL_CHECK_OK" if ok else "PORTAL_CHECK_FAIL")
    return ok


def main(argv=None):
    force_utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", nargs="?", const=str(ROOT / "report" / "technical_document.docx"),
                    help="docx 源（默认 report/technical_document.docx）")
    ap.add_argument("--pdf-out", default=str(ROOT / "report" / "technical_document.pdf"))
    ap.add_argument("--rar", help="要打包的目录（通常 dist/zynq-yolo_submission_<stamp>）")
    ap.add_argument("--rar-out", help="输出 .rar（默认与目录同级、同名）")
    ap.add_argument("--rar-exe", help="Rar.exe 路径（缺省查 RAR_EXE / PATH）")
    ap.add_argument("--check", action="store_true", help="核对门户上传件")
    ap.add_argument("--video")
    ap.add_argument("--auth", help="承诺及使用授权书 PDF")
    ap.add_argument("--poster")
    ap.add_argument("--overview", help="作品全貌图（PDF/图片）")
    args = ap.parse_args(argv)

    if not (args.pdf or args.rar or args.check):
        ap.print_help()
        return 2

    rc = 0
    if args.pdf:
        if not docx_to_pdf(Path(args.pdf), Path(args.pdf_out)):
            rc = 1
    if args.rar:
        rar_exe = find_rar(args.rar_exe)
        if not rar_exe:
            print("PORTAL_RAR_FAIL 未找到 Rar.exe；用 --rar-exe 或环境变量 RAR_EXE 指定")
            rc = 1
        else:
            src = Path(args.rar)
            out = Path(args.rar_out) if args.rar_out else src.with_suffix(".rar")
            if not make_rar(src, out, rar_exe):
                rc = 1
    if args.check and not check(args):
        rc = 1
    return rc


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:
        print(f"PORTAL_ASSETS_FAIL {type(exc).__name__}: {exc}")
        sys.exit(1)
