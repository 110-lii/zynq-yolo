#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""核对 `ai/raw/*.md` 里的**入站发言**与会话原始记录**逐字一致**（原始层的自证）。

为什么需要它（别删）
------------------
`ai/README.md` §1.1 把 `ai/raw/` 定为证据链的根，理由是「手抄必然走样」。
但**导出脚本本身也可能走样**（截断、转义、编码、把注入内容当成用户发言）。
只写一句「已逐字导出」是**断言**，不是**证据**。故本脚本独立地：

    会话原始文件（zstd）→ 独立解压 → 按记录类型取出各类入站发言
                                    ↓ 逐字节比对
    ai/raw/*.md 中对应轮次的代码块正文

**判据（可失败，才叫判据）**：比对结果必须是**逐字节相等**。任一条不等 => 退出码 1 并打印
两边的差异片段。**不允许**「基本一致」「大意相同」这类结论。

核对四类入站发言（与导出器的「轮次」口径一一对应）
------------------------------------------------
| 类别 | 记录里的位置 | 分片里的标题 |
|---|---|---|
| `prompt`（人类提问） | `user/message` + `source.kind=user` + **有 `rpcId`** | `## 轮次 N — 👤 用户`（`<sub>` 不含 `ask_user_question`） |
| `answer`（人类回答） | `tool/result`←`ask_user_question` 的 answers JSON | `## 轮次 N — 👤 用户`（`<sub>` 含 `ask_user_question`） |
| `delegation`（委派提示词） | `user/message` + `kind=user` + **无 `rpcId`** | `## 轮次 N — 📨 委派提示词（父 agent 注入，非人类）` |
| `relay`（团队消息） | `user/message` + `kind=agent-message` | `## 轮次 N — 📨 团队消息（来自 <短ID>，非人类提示词）` |

⚠️ 四类**都必须核**：只核「人类提问」时，8 个子 agent 分片会全部落进「没有人类发言 ⇒ SKIP」，
看起来像核对过了、其实一条没比 —— 这是**最典型的假阳性**（本脚本 2026-10-19 第一版就踩了）。

独立性（刻意的冗余）
------------------
本脚本**不复用** `export_transcript_dsh.py` 的解析/渲染函数：它直接调 `dsh_session_dump.mjs`
解压、自己遍历 JSONL 并匹配 markdown —— 若两个实现同时错，才会给出假 PASS；
若只有一个错（最常见），这里必然报 FAIL。

用法
----
    python ai/tools/verify_raw_match.py                 # 核对全部 ai/raw/*.md
    python ai/tools/verify_raw_match.py --session 8160  # 只核对某个会话（短 ID 前缀）
    python ai/tools/verify_raw_match.py --show          # 顺带打印比对内容的逐字预览
    python ai/tools/verify_raw_match.py --kind prompt   # 只核某一类（prompt/answer/delegation/relay）

退出码：0 = 全部逐字节一致；1 = 有不一致；2 = 环境/路径问题或**一条都没核对**（不是「通过」）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ⚠️ 必须在**任何本地模块 import 之前**关掉 .pyc 生成（本文件当前没有本地 import，
#   但保持与 `export_transcript_dsh.py` / `prompts_index.py` 同一约定，免得日后加 import 时复发）。
#   `tools/check_repo.py` 的 check d) 是扫磁盘的：`ai/tools/__pycache__/` 一出现就 FAIL，
#   而 `.gitignore` 挡不住扫盘检查 ⇒ 从源头消除，不靠"跑完手动删"。
sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = REPO_ROOT / "ai" / "raw"
HELPER = Path(__file__).resolve().parent / "dsh_session_dump.mjs"
SESSION_FILE = "session.v4.jsonl.zstd"

#: 与导出器同一个白名单：只有 `user` 是人类/委派提示词的 kind（其余是 harness 注入）
HUMAN_KINDS = {"user"}
#: 其他 agent / 队友转发 —— 单列渲染，不计入提示词汇总
RELAY_KINDS = {"agent-message"}

#: 分片里「人类提问 / 回答」的标题 —— ⚠️ 两类**共用** `👤 用户` 标题，靠 `<sub>` 区分。
#: 围栏长度是**可变的**（导出器按正文里的反引号连串自动加长，见其 `_code_fence()`），
#: 故这里用 `(`{3,})` + 反向引用配对，**不能**写死三反引号 —— 写死会把长围栏的块整个漏掉。
_PROMPT_RE = re.compile(
    r"^## 轮次 (\d+) — 👤 用户\s*\n\s*<sub>(.*?)</sub>\s*\n\s*(`{3,})text\n(.*?)\n\3[ \t]*$",
    re.MULTILINE | re.DOTALL,
)
#: `<sub>` 含此标记的是「对 ask_user_question 的回答」，**不是**独立提问。
#: ⚠️ 导出器实际渲染「… · 对 `ask_user_question` 的回答（问题 id=…，非独立提问）」，
#:    故这里匹配**工具名** —— 别改成自己臆想的措辞（实测会永远匹配不上，
#:    于是把回答轮误判成提问轮，比对必然假报不一致）。
_ANSWER_MARK = "ask_user_question"

#: 分片里「委派提示词」的标题（子 agent 会话第 1 轮，**也是逐字原文**）
_DELEG_RE = re.compile(
    r"^## 轮次 (\d+) — 📨 委派提示词（父 agent 注入，非人类）\s*\n\s*<sub>(.*?)</sub>\s*\n"
    r"\s*(`{3,})text\n(.*?)\n\3[ \t]*$",
    re.MULTILINE | re.DOTALL,
)
#: 分片里「团队消息」的标题（其它 agent 经 send_message 转发；非人类、非委派）
_RELAY_RE = re.compile(
    r"^## 轮次 (\d+) — 📨 团队消息（来自 `([^`]*)`，非人类提示词）\s*\n\s*<sub>(.*?)</sub>\s*\n"
    r"\s*(`{3,})text\n(.*?)\n\4[ \t]*$",
    re.MULTILINE | re.DOTALL,
)

KIND_LABEL = {"prompt": "人类提问", "answer": "人类回答",
              "delegation": "委派提示词", "relay": "团队消息"}


def find_node() -> str:
    for env in ("DSH_NODE_BIN", "NODE_BIN"):
        v = os.environ.get(env)
        if v and Path(v).is_file():
            return v
    w = shutil.which("node")
    if w:
        return w
    for cand in (r"C:\Program Files\nodejs\node.exe", r"C:\Program Files (x86)\nodejs\node.exe"):
        if Path(cand).is_file():
            return cand
    raise SystemExit("找不到 node（本脚本用 Node 的 zlib 解 zstd，Python 3.13 没有 zstd 模块）")


def sessions_root() -> Path:
    env = os.environ.get("DSH_SESSIONS_DIR")
    if env:
        return Path(env)
    home = os.environ.get("DSH_HOME")
    if home:
        return Path(home) / "sessions"
    return Path.home() / ".dsh" / "sessions"


def decode_session(node: str, zstd_path: Path, tmpdir: Path) -> List[Dict[str, Any]]:
    """调 Node 助手把 zstd 会话解成 JSONL，再逐行 parse；返回记录列表。"""
    out_jsonl = tmpdir / (zstd_path.parent.name + ".jsonl")
    r = subprocess.run([node, str(HELPER), "--dump", str(zstd_path), str(out_jsonl)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit("解压失败（%s）：%s" % (zstd_path.name, (r.stderr or r.stdout).strip()))
    records: List[Dict[str, Any]] = []
    for line in out_jsonl.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            records.append(rec)
    return records


def _texts(blocks: Any) -> str:
    """把 content[] 里的文本块按 `\\n` 拼起来再 strip —— **与导出器 `_user_body()` 同口径**。"""
    if not isinstance(blocks, list):
        return ""
    return "\n".join(str(b["text"]) for b in blocks
                     if isinstance(b, dict) and b.get("text")).strip()


def answer_body(raw: str) -> Optional[str]:
    """把 `ask_user_question` 的回答 JSON 还原成正文 —— **与导出器 `_parse_answer()` 同口径**。

    口径：有 `custom`（用户自己打的字）就用 custom；否则退化成「【选定】…」列表。
    返回 None 表示这段不是回答（解析失败 / 没有 answers）。
    """
    try:
        obj = json.loads(raw)
    except Exception:
        return None
    answers = obj.get("answers")
    if not isinstance(answers, list) or not answers:
        return None
    customs: List[str] = []
    selecteds: List[str] = []
    for a in answers:
        if not isinstance(a, dict):
            continue
        c = a.get("custom")
        if isinstance(c, str) and c.strip():
            customs.append(c.strip())
        s = a.get("selected")
        if isinstance(s, list):
            selecteds.extend([str(x) for x in s if x])
    return "\n".join(customs) if customs else "\n".join("【选定】%s" % x for x in selecteds)


def inbound_from_records(records: List[Dict[str, Any]]) -> List[Tuple[str, str]]:
    """按**记录顺序**取出全部入站发言，返回 [(类别, 正文)]。

    判据全部来自记录自身的字段，**不含**「大概像人话」这类启发式：
      · `user/message` + `kind=user` + **有 `rpcId`** → `prompt`（界面上真的打的字）；
      · `user/message` + `kind=user` + **无 `rpcId`** → `delegation`（spawn 注入的委派提示词）；
      · `user/message` + `kind=agent-message`     → `relay`（其它 agent 转发）；
      · `tool/result`（对应 `ask_user_question`、非 isError）→ `answer`（用户回答，真实原话）。
    ⚠️ 顺序 = 记录顺序 = 导出器 `build_turns()` 的轮次顺序；顺序错会全部错位比对。
    """
    out: List[Tuple[str, str]] = []
    call_names: Dict[str, str] = {}
    for rec in records:
        typ = rec.get("type")
        d = rec.get("data") or {}
        if typ == "tool/call":
            cid = d.get("callId")
            if cid:
                call_names[str(cid)] = str(d.get("name") or "?")
            continue
        if typ == "tool/result":
            m = d.get("message") or {}
            cid = m.get("toolCallId") or (m.get("source") or {}).get("callId")
            if call_names.get(str(cid) or "") != "ask_user_question" or m.get("isError"):
                continue
            raw = "".join(str(b.get("text") or "") for b in (m.get("content") or [])
                          if isinstance(b, dict))
            body = answer_body(raw)
            if body is not None:
                out.append(("answer", body))
            continue
        if typ != "user/message":
            continue
        src = d.get("source") or {}
        kind = str(src.get("kind") or "")
        if kind in HUMAN_KINDS:
            out.append(("prompt" if src.get("rpcId") else "delegation", _texts(d.get("content"))))
        elif kind in RELAY_KINDS:
            out.append(("relay", _texts(d.get("content"))))
        # 其余 kind（agent-instructions / runtime-context / skill-catalog / time-context /
        #   subagent-settled / goal / tool-jobs / …）是 harness 注入或内部通知 → 导出器剔除，
        #   这里同样**不核**（核它们等于把「剔除」判成错误）。
    return out


def _read_text_verbatim(path: Path) -> str:
    """按**原始字节**解码读取，并断言**围栏之外**没有 CR（否则抛错，不静默翻译）。

    ⚠️ 不能用裸 `path.read_text(encoding="utf-8")` 然后声称"逐字节"：它的默认
       `newline=None` 会开**通用换行**，把磁盘上的 `\\r\\n` **翻译成** `\\n`。
       实测反证：某分片磁盘上有 35 个 CRLF，经 `read_text` 读出来 CR 数为 **0**。
       ⇒ 行尾是否符合 `.gitattributes` **不可能**由这种读法发现，必须单独审原始字节
       （`audit_line_endings()`，且在比对之前先跑，见 `main()`）。

    ★ 为什么只查「围栏之外」：围栏里是**逐字原文**（用户在对话里粘贴过 CRLF 的 CSV），
      改写它就等于破坏「逐字节等于原文」这条承诺；而工具自己写的标题/时间戳/围栏
      必须全是 LF。两个口径必须与本脚本的 `audit_line_endings()`、
      `prompts_index.read_text_lf()` **完全一致** —— 否则会出现
      「写出侧保留、读回侧拒绝」的死锁（本仓库真实发生过：`--all` 必失败）。
    """
    b = path.read_bytes()
    n = sum(c + l for (_n, c, l, _p) in _eol_counts(b, path.name))
    if n:
        raise AssertionError(
            "%s 围栏之外含 CR（%d 个），但行尾审计未先拦截 —— 这不是调用顺序错误，"
            "就是结构行真的不合规" % (path.name, n))
    return b.decode("utf-8", errors="replace")


def _eol_counts(b: bytes, name: str = "") -> List[Tuple[str, int, int, int]]:
    """统计一份分片的行尾：[(名字, 结构行 CRLF, 结构行裸 CR, 围栏内 CRLF)]。

    围栏的判定与写出侧同规则：行首（允许前导空白）以 ``` 开头即翻转围栏状态；
    围栏标记行**本身算结构行**（它出现 CR 也是写出点 bug）。
    """
    s_crlf = s_lone = p_crlf = 0
    in_fence = False
    for line in b.split(b"\n"):
        crlf = line.count(b"\r\n")
        lone = line.count(b"\r") - crlf
        if line.lstrip().startswith(b"```"):
            s_crlf += crlf
            s_lone += lone
            in_fence = not in_fence
        elif in_fence:
            p_crlf += crlf
        else:
            s_crlf += crlf
            s_lone += lone
    return [(name, s_crlf, s_lone, p_crlf)]


def audit_line_endings(raw_dir: Path) -> List[Tuple[str, int, int, int]]:
    """审计 `raw_dir` 下每个 `*.md` 的行尾，返回 [(文件名, 结构行 CRLF, 结构行裸 CR, 围栏内 CRLF)]。

    为什么把行尾单独审：`.gitattributes` 声明 `* text=auto eol=lf`，`git add` 会把 CRLF
    规范化成 LF ⇒ 一旦磁盘是 CRLF，「仓库里存档的副本」与「导出器写的文件」就不再逐字节相等，
    而**逐字节比对本身发现不了**（通用换行会把两边都翻译成一样的 \\n）。故必须独立成一条判据。

    ★ 但只有**结构行**的 CR 才作数：围栏内是用户粘贴的 CRLF 原文，它**必须**原样保留。
      把围栏内 CR 也算失败，就会把「忠实的逐字存档」判成缺陷 —— 而写出侧已经采纳了
      「原样保留」的口径（`export_transcript_dsh.py` 只打印提示）。两侧口径不一致时，
      导出器会卡在自己刚写的产物上（`--all` 抛 `RuntimeError: ...含 CR（12 个）`）。
    """
    out: List[Tuple[str, int, int, int]] = []
    for p in sorted(raw_dir.glob("*.md")):
        out.extend(_eol_counts(p.read_bytes(), p.name))
    return out


def inbound_from_md(md_path: Path) -> List[Tuple[int, str, str]]:
    """从 ai/raw 分片里抽出全部入站轮次，返回 [(轮次号, 类别, 正文)]，按轮次号排序。"""
    txt = _read_text_verbatim(md_path)
    rows: List[Tuple[int, str, str]] = []
    for no, _when, _fence, body in _PROMPT_RE.findall(txt):
        kind = "answer" if _ANSWER_MARK in _when else "prompt"
        rows.append((int(no), kind, body))
    for no, _when, _fence, body in _DELEG_RE.findall(txt):
        rows.append((int(no), "delegation", body))
    for no, _who, _when, _fence, body in _RELAY_RE.findall(txt):
        rows.append((int(no), "relay", body))
    rows.sort(key=lambda r: r[0])
    return rows


# ---------------------------------------------------------------------------
# ★ 与导出端同源的密钥脱敏规则（2026-10-19 新增）
#
# 为什么校验器也要脱敏：`ai/raw/` 是导出器**脱敏后**的产物，而本校验器比对的另一端是
# **未脱敏的会话原文**。若只脱敏一侧，任何含密钥的那条必然判为「不一致」——
# 于是「逐字」与「安全」互相打架。正确口径是：
#     **比对的等价类 = 原文经同一套脱敏规则处理后的结果**
# 即：`redact(原文) == 分片正文`。这样既守住了安全（产物无明文密钥），
# 又让「逐字」这句话**仍然是一条可失败、可重跑的判据**（脱敏规则单一且确定 ⇒ 不会掩盖真实差异）。
#
# ⚠️ 规则必须与 `export_transcript_dsh.py` / `prompts_index.py` 里的 `_SECRET_PATTERNS`
#    **逐条一致**；三处不同源，就会出现「导出脱敏了、校验还原不回来」的假 FAIL。
# ---------------------------------------------------------------------------
_SECRET_PATTERNS = [
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "GITHUB_TOKEN"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "GITHUB_PAT"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS_ACCESS_KEY"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "API_KEY_SK"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "SLACK_TOKEN"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b"), "GOOGLE_API_KEY"),
    (re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"), "PRIVATE_KEY_HEADER"),
]


def redact_secrets(text: str) -> str:
    """与导出端**同规则**的脱敏；只用于把「原文」拉到与「分片」同一等价类再比。"""
    if not text:
        return text
    out = text
    for pat, kind in _SECRET_PATTERNS:
        def _sub(m: "re.Match[str]") -> str:
            s = m.group(0)
            return "<REDACTED:%s %s…%s>" % (kind, s[:4], s[-4:] if len(s) > 8 else "")
        out = pat.sub(_sub, out)
    return out


def _first_diff(a: str, b: str, width: int = 60) -> str:
    n = min(len(a), len(b))
    i = next((k for k in range(n) if a[k] != b[k]), n)
    lo = max(0, i - width // 2)
    return ("首个差异在字符 %d（长度 %d vs %d）\n"
            "    原文: %r\n"
            "    分片: %r" % (i, len(a), len(b), a[lo:i + width // 2], b[lo:i + width // 2]))


def main(argv: Optional[List[str]] = None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass

    ap = argparse.ArgumentParser(
        description="核对 ai/raw 的入站发言与会话原文逐字一致",
        allow_abbrev=False)  # 见 export_transcript_dsh.py：缩写会让 --all 静默变成 --all-projects
    ap.add_argument("--raw", default=None, help="raw 目录（默认 <repo>/ai/raw）")
    ap.add_argument("--session", default=None, help="只核对短 ID 以该前缀开头的分片")
    ap.add_argument("--show", action="store_true", help="打印比对内容的前 120 字符预览")
    ap.add_argument("--kind", default=None,
                    choices=sorted(KIND_LABEL), help="只核对某一类入站发言")
    args = ap.parse_args(argv)

    raw_dir = Path(args.raw) if args.raw else RAW_DIR
    if not raw_dir.is_dir():
        print("找不到 raw 目录：%s" % raw_dir, file=sys.stderr)
        return 2

    fragments = sorted(p for p in raw_dir.glob("20*-*.md") if not p.name.startswith("_"))
    if args.session:
        fragments = [p for p in fragments if p.stem.split("_")[-1].startswith(args.session)]
    if not fragments:
        print("没有可核对的分片（%s）" % raw_dir, file=sys.stderr)
        return 2

    # --- 行尾审计（**先跑**，独立判据，不能由下面的逐字比对代替） ---------------
    # `.gitattributes` 声明 `* text=auto eol=lf`。磁盘上若是 CRLF，`git add` 会规范化成
    # LF ⇒ 「仓库里存档的副本」≠「导出器写的文件」，而**逐字比对看不出来**（通用换行把两边
    # 都翻成 \n）。故这里单独扫原始字节，任何一个 CRLF 都算失败。
    # ⚠️ 必须放在比对**之前**：`_read_text_verbatim()` 见到 CR 会直接抛错（那是调用顺序错了），
    #    而 CRLF 文本里的 \r 也会让下面那些以 `\n` 收尾的正则匹配不上 —— 于是症状会表现成
    #    「0 条可核对」而不是「行尾不合规」，把一个明确的失败伪装成"没东西可比"。
    eol_rows = audit_line_endings(raw_dir)
    bad_eol = [(n, c, d) for (n, c, d, _p) in eol_rows if c or d]
    payload_files = [(n, p) for (n, _c, _d, p) in eol_rows if p]
    print("")
    print("行尾审计（.gitattributes 要求 eol=lf；**只算结构行**，围栏内的 CR 是逐字内容）：")
    for name, crlf, lone, pay in eol_rows:
        print("   %-30s 结构行CRLF=%-5d 结构行裸CR=%-3d 围栏内CRLF=%-5d %s"
              % (name, crlf, lone, pay, "OK" if not (crlf or lone) else "← 不合规"))
    print("行尾汇总：%d 个文件，%d 个结构行不合规，合计 %d 个 CRLF / %d 个裸 CR"
          % (len(eol_rows), len(bad_eol),
             sum(c for (_n, c, _d) in bad_eol), sum(d for (_n, _c, d) in bad_eol)))
    if payload_files:
        print("          另有 %d 个分片围栏内共 %d 个 CRLF —— 那是**用户粘贴的 CRLF 原文**，"
              "按逐字存档口径**原样保留**，不算缺陷"
              % (len(payload_files), sum(p for (_n, p) in payload_files)))
    if bad_eol:
        print("VERIFY_EOL_FAIL —— 结构行行尾不合规：git 会把这些 CRLF 规范化成 LF，")
        print("                 于是 clone 后「仓库副本 == 导出文件」的逐字节主张失效。")
        print("                 修法：`python ai/tools/export_transcript_dsh.py` 重导出（脚本已强制 LF）。")
        return 1

    root = sessions_root()
    if not root.is_dir():
        print("找不到会话库根：%s" % root, file=sys.stderr)
        return 2
    node = find_node()

    print("")
    print("verify_raw_match.py —— ai/raw 入站发言 vs 会话原文（逐字节）")
    print("仓库根：%s" % REPO_ROOT)
    print("会话库根：%s" % root)
    if args.kind:
        print("只核类别：%s（%s）" % (args.kind, KIND_LABEL[args.kind]))

    # 会话库 → {short_id: zstd 路径}
    index: Dict[str, Path] = {}
    for ws in sorted(p for p in root.iterdir() if p.is_dir()):
        for sd in sorted(p for p in ws.iterdir() if p.is_dir()):
            f = sd / SESSION_FILE
            if f.is_file():
                index[re.sub(r"^session-", "", sd.name)[:8].lower()] = f

    n_ok = n_bad = n_skip = 0
    redacted_n = 0
    checked = 0
    per_kind: Dict[str, int] = {}
    tmpdir = Path(tempfile.mkdtemp(prefix="dsh_verify_"))
    try:
        for frag in fragments:
            short = frag.stem.split("_")[-1].lower()
            src = index.get(short)
            if src is None:
                print("SKIP %s —— 会话库里找不到短 ID `%s` 的源文件" % (frag.name, short))
                n_skip += 1
                continue
            md_rows = inbound_from_md(frag)
            if args.kind:
                md_rows = [r for r in md_rows if r[1] == args.kind]
            if not md_rows:
                print("SKIP %s —— 分片里没有可核对的入站轮次%s"
                      % (frag.name, "（类别 %s）" % args.kind if args.kind else ""))
                n_skip += 1
                continue
            recs = decode_session(node, src, tmpdir)
            src_rows = inbound_from_records(recs)
            if args.kind:
                src_rows = [r for r in src_rows if r[0] == args.kind]

            print("")
            print("== %s ==" % frag.name)
            print("   源文件：%s（%d 字节）" % (src, src.stat().st_size))
            print("   原文入站 %d 条：%s" % (len(src_rows), _count_str(src_rows, 0)))
            print("   分片入站 %d 条：%s" % (len(md_rows), _count_str(md_rows, 1)))
            if len(src_rows) != len(md_rows):
                print("FAIL 条数不一致（原文 %d vs 分片 %d）—— 不得据该分片作逐字引用"
                      % (len(src_rows), len(md_rows)))
                n_bad += 1
                continue
            for (k_src, body_src), (no_md, k_md, body_md) in zip(src_rows, md_rows):
                checked += 1
                per_kind[k_md] = per_kind.get(k_md, 0) + 1
                # ★ 把「原文」先拉进与「分片」同一等价类（脱敏），再比。
                #   等价关系：redact(原文) == 分片正文。脱敏规则单一确定 ⇒ 不会掩盖真实差异。
                body_src_cmp = redact_secrets(body_src)
                same = (body_src_cmp == body_md) and (k_src == k_md)
                if same and body_src_cmp != body_src:
                    redacted_n += 1
                tag = "OK  " if same else "FAIL"
                print("   %s %s·轮次 %d：%d 字符 vs %d 字符 —— %s"
                      % (tag, KIND_LABEL.get(k_md, k_md), no_md, len(body_src), len(body_md),
                         "逐字节相等" if same else "不相等"))
                if args.show:
                    print("        原文：%r" % body_src[:120])
                    print("        分片：%r" % body_md[:120])
                if same:
                    n_ok += 1
                else:
                    n_bad += 1
                    print("        类别：原文 `%s` vs 分片 `%s`" % (k_src, k_md))
                    print("        " + _first_diff(body_src_cmp, body_md))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)  # 中间文件用完即删（与导出器同规则）

    print("")
    print("分类计数：%s" % ("、".join("%s %d" % (KIND_LABEL[k], v)
                                    for k, v in sorted(per_kind.items())) or "（无）"))
    if redacted_n:
        print("已脱敏后比对：%d 条（原文含密钥字面量，导出端已按同一规则替换为 <REDACTED:…>；"
              "等价关系是「redact(原文) == 分片正文」，不是「原文 == 分片正文」）" % redacted_n)
    print("SUMMARY: %d 条入站发言逐字节相等 / %d 条不一致 / %d 个分片跳过（共核对 %d 条）"
          % (n_ok, n_bad, n_skip, checked))
    if n_bad:
        print("VERIFY_RAW_FAIL")
        return 1
    if not checked:
        print("VERIFY_RAW_EMPTY —— 没有任何可核对的入站发言（**不等于通过**）")
        return 2
    print("VERIFY_RAW_OK")
    return 0


def _count_str(rows: List[Tuple], idx: int) -> str:
    counts: Dict[str, int] = {}
    for r in rows:
        counts[r[idx]] = counts.get(r[idx], 0) + 1
    return "、".join("%s %d" % (KIND_LABEL.get(k, k), v) for k, v in sorted(counts.items())) or "（无）"


if __name__ == "__main__":
    sys.exit(main())
