#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从已落盘的 `ai/raw/*.md` 分片重建「用户提示词汇总」（`_prompts_all.md`）。

为什么是独立模块（别删这段说明）
--------------------------------
`ai/raw/` 有**两个数据源**：Claude Code（`export_transcript.py`）与 CodeBuddy
（`export_transcript_codebuddy.py`）。若汇总文件由**某一端的本次运行**生成，
另一端的提示词就会被整段截掉 —— 实测发生过：2026-10-09 Claude 侧钩子重写本文件时，
把 CodeBuddy 2026-10-06 的 16 条提示词抹掉了（252 → 240 条）。

⇒ 本模块的做法是「**从盘上已落盘的全部分片重建**」，与数据源无关、天然幂等。
两个导出脚本都调用它，**谁后跑都不会截断对方**。

（这条原则与本仓库三次同族翻车一致：共享产物必须「从盘上全部分片重建」，
不能「由本次运行生成」—— 见 `ai/sessions/047` / `048` / `060`。）

用法
----
    from prompts_index import rebuild_prompts_from_raw
    md, n_prompts, n_days = rebuild_prompts_from_raw(raw_dir)

    python ai/tools/prompts_index.py             # 重建 ai/raw/_prompts_all.md
    python ai/tools/prompts_index.py --check     # 只比对，不写；不一致则退出码 1
    python ai/tools/prompts_index.py --out DIR   # 换一个 raw 目录

⚠️ **本脚本原先只能被 import（没有 `__main__`）**，而 `CLAUDE.md` / 交接单里写的
   「`python ai/tools/prompts_index.py`」实际是空跑 —— 2026-10-18 补了 CLI 入口
   （与 `session_index.py` 同风格：默认重建、`--check` 只比对），**重建逻辑一个字未改**。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

# ⚠️ 关掉 .pyc 生成。本文件既被 `export_transcript_dsh.py` import、也能当 CLI 跑，
#   两种用法都会在 `ai/tools/` 下留 `__pycache__/`；而 `tools/check_repo.py` 的
#   check d)「无禁入文件」是**扫磁盘、不看 git** ⇒ 一出现就 FAIL，`.gitignore` 挡不住。
#   ⇒ 从源头消除（调用方 `export_transcript_dsh.py` 也在 import 本模块**之前**设了同一个标志，
#      两层都设：单跑 CLI 的路径由这一行兜住，被 import 的路径由调用方兜住）。
sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[2]


def write_text_lf(path: Path, text: str) -> int:
    """把 `text` 以**显式 LF** 写进 `path`；返回正文里残留的 CR 个数（正常应为 0）。

    为什么不用 `path.write_text(text, encoding="utf-8", newline="\\n")`：
      `newline` 参数是 **Python 3.10 才加进 `Path.write_text`** 的。本仓库 README 声明的
      最低版本是 **Python 3.8+**（`tools/check_repo.py` 只需标准库即可跑）。若用 3.10+ 的
      签名，评审机器上会直接 `TypeError: write_text() got an unexpected keyword argument`。
      `open(..., newline="\\n")` 是 Python 2 起就有的参数 ⇒ **兼容面最大，故选它**。

    为什么必须显式 LF（不要退回默认值）：
      默认 `newline=None` 时，写出的行尾 = `os.linesep` ⇒ Windows 上是 CRLF。而
      `.gitattributes` 声明 `* text=auto eol=lf`，`git add` 会把 CRLF 规范化成 LF，
      于是「仓库里存档的副本」与「导出器写的文件」**不再逐字节相等**，`ai/` 反复主张的
      「逐字节」证据链在 clone 后当场失效（实测：磁盘 139 083 B vs git index 137 619 B，
      差 1 464 B = 1 464 个 CRLF）。

    ⚠️ `newline="\\n"` 只保证**换行符不被翻译**，不会改写正文里已有的 CR。若某条逐字正文
       自带 CRLF，本函数会把它原样写出并把 CR 数返回给调用方去告警 —— **不静默改写正文**，
       因为改写正文才是真正破坏「逐字」的行为。
    """
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return text.count("\r")


# 从 raw 分片里抽「## 轮次 N — 👤 用户」+ 时间戳 + 代码块正文
# ⚠️ 两个导出脚本产出的分片，这两处格式必须保持一致 —— 否则重建会对某一源失效。
# ⚠️ 围栏长度是**可变的**（导出器按正文里的反引号连串自动加长，防正文自带 ``` 提前闭合），
#    故用 `(`{3,})` + 反向引用配对；写死三反引号会**静默漏掉**那些加了长围栏的提示词。
#
# ★ 密钥脱敏规则 —— **必须与 export_transcript_dsh.py 里的 `_SECRET_PATTERNS` 逐条一致**。
#   两处都定义是为了不产生「汇总模块 import 导出模块」的反向依赖；但它们**必须同源同序**，
#   否则「分片已脱敏、汇总没脱敏」会造成 `_prompts_all.md` 仍含明文密钥。
#   （2026-10-19 起因：用户粘贴的 GitHub PAT 被逐字写进 ai/raw/，GitHub push protection
#    以 "repository rule violations" 拒绝推送 ⇒ 脱敏是**安全要求**，不是洁癖。）
_SECRET_PATTERNS = [
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "GITHUB_TOKEN"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "GITHUB_PAT"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS_ACCESS_KEY"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "API_KEY_SK"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "SLACK_TOKEN"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b"), "GOOGLE_API_KEY"),
    (re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"), "PRIVATE_KEY_HEADER"),
]

_PROMPT_RE = re.compile(
    r"^## 轮次 (\d+) — 👤 用户\s*\n\s*<sub>(.*?)</sub>\s*\n\s*(`{3,})text\n(.*?)\n\3[ \t]*$",
    re.MULTILINE | re.DOTALL,
)
_SESSION_RE = re.compile(r"^# 会话原文 — (\S+)", re.MULTILINE)


def _code_fence(body: str) -> str:
    """围栏长度 = 正文内最长反引号连串 + 1（至少 3）。与导出器 `_code_fence()` 同规则。"""
    longest = 0
    for m in re.finditer(r"`+", body or ""):
        longest = max(longest, len(m.group(0)))
    return "`" * max(3, longest + 1)


def cr_outside_fences(b: bytes) -> int:
    """统计**围栏之外**（即工具自己生成的结构行）的 CR 个数。

    ★ 为什么要区分「结构行」与「逐字正文」：
      - 工具自己写的每一行（标题、`<sub>` 时间戳、`---`、围栏标记）必须是 LF，
        出现 CR 就是写出点的 bug（Windows 上 `Path.write_text` 默认写 CRLF 就是这个坑）；
      - 而**围栏里面是逐字原文**：用户在对话里粘贴过 CRLF 的 CSV，
        把它改写成 LF 就等于破坏「逐字节等于原文」这条唯一的存档承诺。
    历史教训：写出侧已放宽（`export_transcript_dsh.py` 只打印提示、原样保留），
    读回侧却仍一律 `b"\\r" in b` 就报错 ⇒ 导出器**永远卡在自己刚写的产物上**
    （`--all` 必失败，报 `RuntimeError: ...含 CR（12 个）`）。
    两侧口径必须一致：**只查结构行。**
    """
    n = 0
    in_fence = False
    for line in b.split(b"\n"):
        is_fence = line.lstrip().startswith(b"```")
        if is_fence:
            n += line.count(b"\r")   # 围栏标记行本身是结构行
            in_fence = not in_fence
        elif not in_fence:
            n += line.count(b"\r")   # 围栏之外 = 工具生成的结构行
    return n


def read_text_lf(path: Path) -> str:
    """按原始字节读取，**只在围栏之外**拒绝 CR（不静默翻译行尾）。

    为什么不用 `path.read_text(encoding="utf-8")`：默认 `newline=None` 开通用换行，
    会把磁盘上的 `\\r\\n` **翻译成** `\\n`。后果有两个假绿：
      1. `--check` 的比对里，盘上 `_prompts_all.md` 是 CRLF 还是 LF **看不出来**
         （两边都被翻译成一样），于是「一致」通过了、而 git 里其实被规范化过；
      2. 分片正文里的 CRLF 被翻译后再写进汇总，汇总与分片的行尾口径悄悄分叉。
    结构行必须是 LF（`.gitattributes` 的 `* text=auto eol=lf`）；**围栏内的 CR 是逐字内容**，
    不属于本判据的范围（见 `cr_outside_fences()`）。
    """
    b = path.read_bytes()
    n = cr_outside_fences(b)
    if n:
        raise RuntimeError(
            "%s 围栏之外含 CR（%d 个）—— 结构行必须是 LF，说明写出点有 bug；"
            "请重跑 `python ai/tools/export_transcript_dsh.py`"
            % (path.name, n))
    return b.decode("utf-8", errors="replace")


def rebuild_prompts_from_raw(raw_dir: Path) -> Tuple[str, int, int]:
    """把 raw_dir 下全部 `YYYY-MM-DD_*.md` 里的用户提示词汇总成一份。

    返回 (markdown, 提示词条数, 天数)。
    """
    # 日期 → [(会话短id, 轮次, 时间, 正文)]
    by_date: Dict[str, List[Tuple[str, int, str, str]]] = {}
    for p in sorted(raw_dir.glob("20*-*.md")):
        date = p.name[:10]
        txt = read_text_lf(p)
        m = _SESSION_RE.search(txt)
        sid = (m.group(1)[:8] if m else p.stem.split("_")[-1])
        for turn_no, when, _fence, body in _PROMPT_RE.findall(txt):
            by_date.setdefault(date, []).append(
                (sid, int(turn_no), when.strip(), body)
            )

    L: List[str] = []
    L.append("# 用户提示词汇总（逐字原文）")
    L.append("")
    L.append("> ⚠️ **本文件由 `ai/tools/prompts_index.py` 自动导出，请勿手工编辑。**")
    L.append("> 按**日期**分组、组内按时间顺序，汇总全部会话中的用户输入，**逐字原文**。")
    L.append("> 赛题指南 3.3.3.1 明确要求提交提示词，故单独成文便于评审查阅。")
    L.append("")
    L.append("> 📌 本文件**从 `raw/` 下已落盘的分片重建**（而非从某一端的原始记录生成）——")
    L.append("> 因为 `raw/` 有**两个来源**（Claude Code 与 CodeBuddy），谁重跑谁覆盖，")
    L.append("> 只会把另一份截断。")
    L.append("")
    n_total = sum(len(v) for v in by_date.values())
    L.append(f"**合计 {n_total} 条提示词，跨 {len(by_date)} 天。**")
    L.append("")
    L.append("> 「轮次」编号与 `raw/` 中该会话分片的编号一致，可互相对照。")
    L.append("")
    L.append("---")
    L.append("")

    p_no = 0
    for date in sorted(by_date):
        L.append(f"## {date}")
        L.append("")
        groups: Dict[str, List[Tuple[int, str, str]]] = {}
        for sid, tno, when, body in by_date[date]:
            groups.setdefault(sid, []).append((tno, when, body))
        for sid, rows in groups.items():
            nos = [r[0] for r in rows]
            L.append(f"### 会话 `{sid}`（轮次 {min(nos)}–{max(nos)}） — {len(rows)} 条")
            L.append("")
            for tno, when, body in rows:
                p_no += 1
                L.append(f"#### P{p_no}")
                L.append("")
                L.append(f"<sub>{when}　·　会话内轮次 {tno}</sub>")
                L.append("")
                fence = _code_fence(body)
                L.append(fence + "text")
                L.append(body)
                L.append(fence)
                L.append("")
    # ★ 脱敏：本文件是**汇总层**，与分片必须口径一致。
    #   分片已在导出端脱敏；这里再兜一次，防止「分片是旧的、汇总却重算了」这类时序漏洞。
    #   （`redact_secrets` 定义在 export_transcript_dsh.py；此处按同规则内联，避免反向依赖。）
    md = "\n".join(L)
    for _pat, _kind in _SECRET_PATTERNS:
        def _sub(m):
            s = m.group(0)
            return "<REDACTED:%s %s…%s>" % (_kind, s[:4], s[-4:] if len(s) > 8 else "")
        md = _pat.sub(_sub, md)
    return md, n_total, len(by_date)


# ---------------------------------------------------------------------------
# CLI（2026-10-18 补：此前本文件只能被 import，命令行调用是空跑）
# ---------------------------------------------------------------------------
def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="从 ai/raw/ 全部分片重建 _prompts_all.md（逐字提示词汇总）",
        allow_abbrev=False)  # 见 export_transcript_dsh.py 的说明：缩写会让 --all 静默变成 --all-projects
    ap.add_argument("--out", default=None, help="raw 目录（默认 <repo>/ai/raw）")
    ap.add_argument("--check", action="store_true",
                    help="只比对，不写；与盘上文件不一致则退出码 1")
    args = ap.parse_args(argv)

    raw_dir = Path(args.out) if args.out else (ROOT / "ai" / "raw")
    if not raw_dir.is_dir():
        print("找不到 raw 目录：%s" % raw_dir, file=sys.stderr)
        return 2

    try:
        md, n_prompts, n_days = rebuild_prompts_from_raw(raw_dir)
    except RuntimeError as e:  # 分片含 CR ⇒ 行尾不合规，**不静默翻译**，明确报错退出
        print("FAIL %s" % e, file=sys.stderr)
        return 1
    dst = raw_dir / "_prompts_all.md"

    if args.check:
        # ⚠️ 这里也必须按原始字节读：用 `read_text` 会把盘上的 CRLF 翻译成 LF，
        #    于是「盘上是 CRLF、期望是 LF」这种不一致**检查不出来**（假绿）。
        if dst.is_file():
            try:
                cur = read_text_lf(dst)
            except RuntimeError as e:
                print("FAIL %s" % e, file=sys.stderr)
                return 1
        else:
            cur = ""
        if cur != md:
            print("_prompts_all.md 与 %d 个分片不一致（%d 条提示词）—— 重跑 `python ai/tools/prompts_index.py`"
                  % (len(list(raw_dir.glob("20*-*.md"))), n_prompts))
            return 1
        print("_prompts_all.md 与 %d 个分片一致（%d 条提示词，跨 %d 天）"
              % (len(list(raw_dir.glob("20*-*.md"))), n_prompts, n_days))
        return 0

    # ★ 必须与各导出器**逐字节一致**，而「一致」的定义是**显式 LF**（见 `write_text_lf`）。
    #   旧写法 `Path.write_text(md, encoding="utf-8")` 不给 newline ⇒ 行尾走 os.linesep
    #   ⇒ Windows 上写出 CRLF，与 `.gitattributes` 的 `* text=auto eol=lf` 冲突，
    #   clone 后 git 把 CRLF 规范化成 LF ⇒ 「逐字节」主张当场失效（实测差 1464 字节）。
    write_text_lf(dst, md)
    print("已重建 %s（%d 条提示词，跨 %d 天）" % (dst, n_prompts, n_days))
    return 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass
    sys.exit(main())
