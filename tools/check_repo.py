#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/check_repo.py -- D:\\zynq-yolo 仓库自检（纯标准库，无第三方依赖）。

用法::

    python tools/check_repo.py            # 自动定位仓库根（本脚本的上一级目录）
    python tools/check_repo.py <repo>     # 指定仓库根

退出码：0 = 没有 FAIL；1 = 有 FAIL。
（承 docs/repo_spec.md §5：**不要用退出码判断成功**，以末行日志令牌为准。）

日志令牌（承 §5 纪律）::

    SUMMARY: X PASS / Y FAIL / Z WARN     # 汇总行，总是打印
    末行 CHECK_REPO_OK                    # 无 FAIL 且无 WARN
    末行 CHECK_REPO_WARN                  # 无 FAIL 但有 WARN
    末行 CHECK_REPO_FAIL                  # 有 FAIL

实现的 6 项检查（承 docs/repo_spec.md §1/§2 与指南 3.3.5.4/3.3.5.5）::

    ★ 检查范围 = **提交集**：`git ls-files --cached --others --exclude-standard`
      （即 `git archive` 的边界）。本机 venv、Vivado 运行目录、训练环境等
      **不随包提交**的产物不参与，避免假 FAIL；不是 git 仓库时退回整盘 walk。

    a) 命名规则   : 提交集内目录名/文件名含非 [A-Za-z0-9_.-] 字符 => FAIL
    b) 必需路径   : src sim build board data skill report docs ai tools
                    与根 README.md / LICENSE / .gitignore，缺一 => FAIL
    c) 目录 README: 上述每个目录必须有 README.md，缺一 => FAIL
    d) 禁入文件   : 提交集内 *.jou *.str / .Xil 目录 / __pycache__ 目录 /
                    vivado*.log / 根目录 build*.log，命中 => FAIL
    e) 权重一致性 : report/README.md 必须含指南 3.3.5.5 的 30/20/20/15/15，缺 => WARN（不 FAIL）
    f) Python 语法: 自有 .py（tools/ sim/ src/ ai/tools/）必须能被 AST 解析，命中 => FAIL
                    ★ 补这条的原因见 check_f_py_syntax 的 docstring：曾出现「4 个脚本语法
                    错误、而 a)–e) 全绿」——**门禁绿灯不等于代码能跑**。

设计约束：仓库尚未填满时必须**正常运行**，对缺失项报 FAIL 而不是崩溃。
"""

import sys

# 不生成 __pycache__：即使提交集已按 .gitignore 过滤，运行自检也不应该在仓库里
# 留下 .pyc 垃圾。实测语义（勿想当然）：模块**自身体内**置位拦不住自己的 .pyc
# （CPython 先编译并写缓存，再执行模块体），只有在本模块 import 别的本地模块**之前**
# 置位才有效。所以这段必须放在所有 import 之前。
sys.dont_write_bytecode = True

import os
import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------- 常量（规则真源）

# b) 必需目录（相对仓库根，POSIX 风格）
REQUIRED_DIRS = [
    "src", "sim", "build", "board", "data",
    "skill", "report", "docs", "ai", "tools",
]

# b) 必需根文件
REQUIRED_ROOT_FILES = ["README.md", "LICENSE", ".gitignore"]

# a) 命名检查遍历时必须整棵剪掉的目录（相对仓库根）
SCAN_SKIP_PATHS = {
    ".git",
    ".research",
    "ref",
    "build/reports",
    "board/logs",
}

# a) 命名检查按名字剪掉（出现在任意层级都不扫）
SCAN_SKIP_NAMES = {".venv", "__pycache__"}

# a) 允许的字符集：纯 ASCII 字母/数字/下划线/连字符/点
NAME_ALLOWED = re.compile(r"^[A-Za-z0-9_.-]+$")

# d) 禁入文件
FORBIDDEN_DIR_NAMES = {".Xil", "__pycache__"}
FORBIDDEN_SUFFIXES = (".jou", ".str")

# e) 指南 3.3.5.5 权重：token -> 至少出现的次数
REQUIRED_WEIGHTS = {"30": 1, "20": 2, "15": 2}
WEIGHT_FILE = "report/README.md"

PASS, FAIL, WARN = "PASS", "FAIL", "WARN"


# ---------------------------------------------------------------- 工具

def force_utf8_stdout():
    """Windows 控制台默认可能不是 UTF-8；失败也不能让脚本崩掉。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def walk(root, skip_paths=frozenset(), skip_names=frozenset(), skip_git=True):
    """受控遍历：返回 (rel_dir, dirnames, filenames) 的迭代，并按需剪枝。

    rel_dir 为 POSIX 风格相对路径（仓库根为 "."）。
    剪枝失败的目录被静默跳过，绝不抛出（权限/竞态都不能让自检崩掉）。
    """
    def onerror(_err):
        return  # 忽略无法进入的目录

    for dirpath, dirnames, filenames in os.walk(str(root), topdown=True, onerror=onerror):
        base = Path(dirpath)
        try:
            rel = base.relative_to(root).as_posix()
        except ValueError:
            rel = "."
        keep = []
        for name in sorted(dirnames):
            rel_child = name if rel == "." else rel + "/" + name
            if skip_git and name == ".git":
                continue
            if name in skip_names:
                continue
            if rel_child in skip_paths:
                continue
            keep.append(name)
        # 先交出本层快照（副本），再就地剪枝，避免调用方看到被删减后的列表
        yield rel, list(dirnames), list(filenames), keep
        dirnames[:] = keep


def count_token(text, token):
    """统计 token 作为独立数字出现的次数（避免 2026 里的 20 被误计）。"""
    return len(re.findall(r"(?<![0-9])" + re.escape(token) + r"(?![0-9])", text))


def read_text(path):
    """尽力读取文本；读不了返回 None（不抛异常）。"""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def git_submission_paths(root):
    """返回「会进入提交包」的相对路径集合（POSIX 风格，只含磁盘上存在的项）。

    集合 = `git ls-files --cached`（已跟踪）+ `--others --exclude-standard`
    （未被 .gitignore 排除的新文件）。这是 `git archive` 的边界，也是本自检的
    检查边界：本机 venv、Vivado 运行目录、训练环境等不随包提交的产物，不应该
    制造假 FAIL；而任何**会被提交**的新文件仍必须过命名/禁入检查。

    不是 git 仓库或 git 不可用时返回 None，调用方退回整盘 walk。
    """
    try:
        proc = subprocess.run(
            [
                "git", "-c", "core.quotepath=false", "-C", str(root),
                "ls-files", "-z", "--cached", "--others", "--exclude-standard",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None

    paths = set()
    for raw in proc.stdout.split(b"\0"):
        if not raw:
            continue
        rel = raw.decode("utf-8", "replace").replace("\\", "/")
        try:
            exists = (root / rel).exists()
        except OSError:
            exists = False
        if exists:
            paths.add(rel)
    return paths


# ---------------------------------------------------------------- 检查项

def check_a_naming(root):
    """a) 目录名/文件名必须纯 ASCII：[A-Za-z0-9_.-]。

    检查范围 = 提交集（git 跟踪 + 未忽略的新文件）；不是 git 仓库时退回扫盘。
    本机 venv/训练环境等不随包提交的目录不参与，避免假 FAIL。
    """
    bad = set()
    paths = git_submission_paths(root)
    if paths is not None:
        for rel in paths:
            parts = rel.split("/")
            for i, name in enumerate(parts):
                if NAME_ALLOWED.match(name):
                    continue
                head = "/".join(parts[: i + 1])
                is_dir = (i < len(parts) - 1) or (root / rel).is_dir()
                bad.add(head + ("/" if is_dir else ""))
    else:
        for rel, _dirnames, filenames, _keep in walk(
            root, skip_paths=SCAN_SKIP_PATHS, skip_names=SCAN_SKIP_NAMES
        ):
            prefix = "" if rel == "." else rel + "/"
            # 目录名：keep 是尚未剪枝的子目录（含被跳过的），需重新枚举原始 dirnames
            for name in sorted(_dirnames):
                rel_child = name if rel == "." else rel + "/" + name
                if name == ".git" or name in SCAN_SKIP_NAMES or rel_child in SCAN_SKIP_PATHS:
                    continue
                if not NAME_ALLOWED.match(name):
                    bad.add(prefix + name + "/")
            for name in sorted(filenames):
                if not NAME_ALLOWED.match(name):
                    bad.add(prefix + name)
    bad = sorted(bad)
    if not bad:
        return PASS, "命名规则：未发现中文/空格/特殊字符的目录名或文件名", []
    detail = ["发现 %d 处违规命名（只允许 [A-Za-z0-9_.-]）:" % len(bad)]
    detail += ["    - " + item for item in bad]
    return FAIL, "命名规则：发现 %d 处非 ASCII / 特殊字符命名" % len(bad), detail


def check_b_required_paths(root):
    """b) 必需目录与必需根文件必须存在。"""
    missing = []
    for name in REQUIRED_DIRS:
        if not (root / name).is_dir():
            missing.append("缺目录: %s/" % name)
    for name in REQUIRED_ROOT_FILES:
        if not (root / name).is_file():
            missing.append("缺根文件: %s" % name)
    if not missing:
        return PASS, "必需路径：%d 个目录 + %d 个根文件齐全" % (
            len(REQUIRED_DIRS), len(REQUIRED_ROOT_FILES)), []
    return FAIL, "必需路径：缺 %d 项" % len(missing), missing


def check_c_dir_readmes(root):
    """c) 每个必需目录必须有 README.md。"""
    missing = []
    for name in REQUIRED_DIRS:
        target = root / name
        if not target.is_dir():
            missing.append("%s/ 目录不存在，无法检查 README.md" % name)
        elif not (target / "README.md").is_file():
            missing.append("缺 README.md: %s/README.md" % name)
    if not missing:
        return PASS, "目录 README：%d 个必需目录均有 README.md" % len(REQUIRED_DIRS), []
    return FAIL, "目录 README：缺 %d 份" % len(missing), missing


def check_d_forbidden(root):
    """d) 提交集内不得有 .jou/.str/.Xil/__pycache__/vivado*.log/根 build*.log。

    检查范围与 a) 相同：git 提交集；不是 git 仓库时退回整盘 walk。
    已被 .gitignore 排除的本机产物不会随包提交，不计入 FAIL。
    """
    hits = set()
    paths = git_submission_paths(root)
    if paths is not None:
        for rel in paths:
            parts = rel.split("/")
            for i, name in enumerate(parts):
                if name in FORBIDDEN_DIR_NAMES:
                    hits.add("禁入目录: %s/" % "/".join(parts[: i + 1]))
            name = parts[-1]
            low = name.lower()
            if low.endswith(FORBIDDEN_SUFFIXES):
                hits.add("禁入文件: %s" % rel)
            elif low.startswith("vivado") and low.endswith(".log"):
                hits.add("禁入日志: %s" % rel)
            elif len(parts) == 1 and low.startswith("build") and low.endswith(".log"):
                hits.add("禁入根日志: %s" % rel)
    else:
        for rel, _dirnames, filenames, _keep in walk(root):
            prefix = "" if rel == "." else rel + "/"
            for name in sorted(_dirnames):
                if name in FORBIDDEN_DIR_NAMES:
                    hits.add("禁入目录: %s%s/" % (prefix, name))
            for name in sorted(filenames):
                low = name.lower()
                if low.endswith(FORBIDDEN_SUFFIXES):
                    hits.add("禁入文件: %s%s" % (prefix, name))
                elif low.startswith("vivado") and low.endswith(".log"):
                    hits.add("禁入日志: %s%s" % (prefix, name))
                elif rel == "." and low.startswith("build") and low.endswith(".log"):
                    hits.add("禁入根日志: %s%s" % (prefix, name))
    hits = sorted(hits)
    if not hits:
        return PASS, "禁入文件：未发现 .jou/.str/.Xil/__pycache__/vivado*.log/根 build*.log", []
    detail = ["命中 %d 处禁入项（应被 .gitignore 排除或删除）:" % len(hits)]
    detail += ["    - " + item for item in hits]
    return FAIL, "禁入文件：命中 %d 处" % len(hits), detail


def check_e_weights(root):
    """e) report/README.md 必须含指南 3.3.5.5 的 30/20/20/15/15（缺 => WARN）。"""
    target = root / WEIGHT_FILE
    if not target.is_file():
        return WARN, "权重表：%s 不存在，无法核对（指南 3.3.5.5）" % WEIGHT_FILE, []
    text = read_text(target)
    if text is None:
        return WARN, "权重表：%s 读取失败，无法核对" % WEIGHT_FILE, []
    missing = []
    for token, need in sorted(REQUIRED_WEIGHTS.items(), key=lambda kv: int(kv[0]), reverse=True):
        have = count_token(text, token)
        if have < need:
            missing.append("缺权重 %s（需要 >= %d 次，实际 %d 次）" % (token, need, have))
    if not missing:
        return PASS, "权重表：%s 含 30/20/20/15/15 五项权重（指南 3.3.5.5）" % WEIGHT_FILE, []
    return WARN, "权重表：%s 权重不全（%d 项缺失）" % (WEIGHT_FILE, len(missing)), missing


def check_f_py_syntax(root):
    """f) 本仓库自己的 .py 必须能被 CPython 解析（只做 compile，不写 .pyc）。

    为什么需要这一条：2026-10-19 一次批量编辑把 `sys.dont_write_bytecode` 插到了
    `from __future__ import annotations` **之前**，4 个 tools/ 脚本直接语法错误，
    而当时的 check a)–e) 全部报 PASS —— 因为没有任何一条检查会去读源码内容。
    **门禁绿灯不等于代码能跑**，所以补上这条。

    只扫自有源码目录（不含 ref/ 第三方资料、不含 build/ 生成物）。
    """
    import ast as _ast

    src_roots = [root / p for p in ("tools", "sim", "src", "ai/tools")]
    bad = []
    n = 0
    for sr in src_roots:
        if not sr.is_dir():
            continue
        for p in sorted(sr.rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            n += 1
            try:
                _ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
            except SyntaxError as e:
                bad.append("%s:%s: %s" % (p.relative_to(root).as_posix(), e.lineno, e.msg))
            except UnicodeDecodeError as e:
                bad.append("%s: 不是 UTF-8（%s）" % (p.relative_to(root).as_posix(), e))
    if not bad:
        return PASS, "Python 语法：%d 个自有 .py 全部可解析" % n, []
    detail = ["%d 个文件语法错误:" % len(bad)] + ["    - " + b for b in bad]
    return FAIL, "Python 语法：%d 个文件解析失败" % len(bad), detail


CHECKS = [
    ("a", "命名规则（纯 ASCII: [A-Za-z0-9_.-]）", check_a_naming),
    ("b", "必需目录 / 必需根文件存在", check_b_required_paths),
    ("c", "每个必需目录有 README.md", check_c_dir_readmes),
    ("d", "无禁入文件（.jou/.str/.Xil/__pycache__/vivado*.log/build*.log）", check_d_forbidden),
    ("e", "指南 3.3.5.5 权重一致性（report/README.md）", check_e_weights),
    ("f", "自有 .py 语法可解析（AST parse，不写 .pyc）", check_f_py_syntax),
]


# ---------------------------------------------------------------- 主流程

def main(argv):
    force_utf8_stdout()

    if len(argv) > 1:
        root = Path(argv[1]).expanduser().resolve()
    else:
        root = Path(__file__).resolve().parent.parent

    print("check_repo.py -- 仓库自检（纯标准库）")
    print("仓库根: %s" % root)
    print("规则真源: docs/repo_spec.md §1/§2（命名与职责）、指南 3.3.5.3/3.3.5.4/3.3.5.5")

    if not root.is_dir():
        print("")
        print("FAIL <仓库根不存在>")
        print("    - 传入的路径不是目录: %s" % root)
        print("")
        print("SUMMARY: 0 PASS / 1 FAIL / 0 WARN")
        print("CHECK_REPO_FAIL")
        return 1

    n_pass = n_fail = n_warn = 0
    for tag, title, func in CHECKS:
        print("")
        print("== check %s) %s ==" % (tag, title))
        try:
            status, message, details = func(root)
        except Exception as exc:  # 任何意外都降级为 FAIL，绝不让自检崩溃
            status, message, details = FAIL, "检查自身异常: %s: %s" % (type(exc).__name__, exc), []
        print("%s %s" % (status, message))
        for line in details:
            print(line)
        if status == PASS:
            n_pass += 1
        elif status == FAIL:
            n_fail += 1
        else:
            n_warn += 1

    print("")
    print("SUMMARY: %d PASS / %d FAIL / %d WARN" % (n_pass, n_fail, n_warn))
    if n_fail:
        print("CHECK_REPO_FAIL")
        return 1
    if n_warn:
        print("CHECK_REPO_WARN")
        return 0
    print("CHECK_REPO_OK")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except SystemExit:
        raise
    except Exception as exc:  # 兜底：任何异常都必须留下可判读的令牌
        print("FAIL <check_repo.py 未捕获异常>: %s: %s" % (type(exc).__name__, exc))
        print("SUMMARY: 0 PASS / 1 FAIL / 0 WARN")
        print("CHECK_REPO_FAIL")
        sys.exit(1)
