#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 **DSH（DeepSeek Harness）** 的本地会话导出为可读的 Markdown（写进 `ai/raw/`）。

为什么需要这个脚本（背景，别删）
--------------------------------
`ai/raw/`（证据链的根）由**各客户端的导出脚本**产生：本仓库当前只有 DSH 一个数据源
（`export_transcript_dsh.py` + `dsh_session_dump.mjs`），旧项目另有 Claude Code /
CodeBuddy / OpenCode / Codex 四个导出器；日后接入新客户端时照同样的结构加即可 ——
**`raw/` 不接受手抄、不接受选择性取证**。

2026-10-18 起本工程用**DSH（DeepSeek Harness）** 承担多 agent 并发工作。
它既不写 Claude 的 JSONL、也不写 CodeBuddy 的 history / OpenCode 的 SQLite / Codex 的 rollout，
而是把会话存成**逐次追加的 zstd 压缩 JSONL**：

    C:/Users/<user>/.dsh/sessions/<工作区键>/<会话 ID>/session.v4.jsonl.zstd
    └─ 工作区键由**本仓库根的实际路径**编码而来，**不写死**：
       `dsh_workspace_key()` 把 `D:\\zynq-yolo` 编码成 `--D-zynq-yolo--`
       （路径分隔符的连续串折成一个 `-`，首尾再补 `--`）。换仓库、换盘符都不用改代码。

若不导出，本轮全部协作记录就会从证据链里整段消失 ——
而赛题指南 3.3.3.1 要求提交「含提示词、模型回答与自我纠错轨迹」的**逐字原文**。
（这正是 Session 108 只能写「原文位置：未导出」的原因，本脚本把这个缺口补掉。）

移植说明（本文件是**外部资产**，别删）
------------------------------------
本脚本并非本仓库原创，而是从**同一用户的旧项目**逐字节移植后**最小改动**：

    来源：`D:\\FPGA\\oi\\ai\\tools\\export_transcript_dsh.py`
          `D:\\FPGA\\oi\\ai\\tools\\dsh_session_dump.mjs`   （配套的 Node 解压助手）
          `D:\\FPGA\\oi\\ai\\tools\\prompts_index.py`      （被本脚本 import，必须同在 ai/tools/）
    移植日期：2026-10-19
    移植方式：`Copy-Item` 逐字节复制（SHA256 一致），只改与旧项目绑定的部分

改动清单（逐处核对「与旧项目绑定」的东西，共 4 处；其余一字未动）：
  1. 文件头工作区键说明：常量 `--D-FPGA-oi--` → 说明它由 `dsh_workspace_key()` 推导；
  2. 新增 `dsh_workspace_key()` / `session_file_name()`：工作区键从**仓库根路径**推导，
     不再依赖任何旧项目路径（旧项目教训：硬编码 `d:/FPGA/oi/...` 使构建脚本不可移植）；
  3. `find_node()` 增加 Windows 常见安装位置回退 `C:\\Program Files\\nodejs\\node.exe`；
  4. `--list` 额外打印「推导出的半成品证据」：仓库根 / 工作区键 / 会话库根 / 默认输出目录，
     让评审能一眼看出脚本认的是哪个仓库，而不是凭旧项目的印象。

⚠️ 之后改本脚本时，请**保留**「工作区键从路径推导」这一条：本仓库的会话库键是
   `--D-zynq-yolo--`，与旧项目 `--D-FPGA-oi--` 不同；写死任一个都会让另一个失效。

解压为什么走 Node（实测，别改回去）
----------------------------------
* Python 3.13.1（本机 `D:\\python\\python.exe`）**没有** `zstandard`；
  标准库 `compression.zstd` 是 3.14+ 才有的，本机 `import compression.zstd` 失败。
* Node **v24.18.0** 自带 `zlib.zstdDecompressSync` / `createZstdDecompress`（实测 `typeof` 为 function）。
⇒ 两段式：`dsh_session_dump.mjs`（Node，纯标准库）解压成临时 JSONL → 本脚本解析。
**不装任何第三方依赖**（红线：禁止 pip/npm install）；中间文件用完即删。

会话记录格式（本机逐条实测，非猜测）
----------------------------------
每行一条 JSON 记录，顶层 `{type, seq, time, data, …}`，`time` 为毫秒时间戳：

    session                 首条，含 id / cwd / createdAt / origin / parentSession / delegationDepth
    system/message          system prompt（**不是用户发言**）
    user/message            data.content[]（type ∈ text / file）、data.source.kind
    assistant/message       data.message.content[]（type ∈ text / reasoning / tool-call）、turn / step
    tool/call               data.callId / name / arguments
    tool/result             data.message.toolCallId / content / isError
    request/context         data.provider / model（会话用的模型）
    session/title           标题（不是发言）
    其余（turn/start、step/*、job/*、agent/inbox/spliced、team/*、approval/*、command/*…）均为内部状态

★ 用户发言 vs harness 注入（本会话索引最关键的一条）
----------------------------------------------------
`user/message` 的 `data.source.kind` 实测有 11 种取值，**只有 `user` 是人类提示词**：

    user                 人类真的打的字（含"继续"这类真人触发）
    agent-message        其他 agent / 队友经 `send_message` 转发 → **单列、不计轮次**
    agent-instructions   项目 CLAUDE.md 等指令注入        → 剔除
    runtime-context      每次请求重发的运行上下文快照     → 剔除
    skill-catalog        技能目录                         → 剔除
    time-context         时间戳上下文                     → 剔除
    subagent-settled     后台子 agent 结束通知            → 剔除
    tool-jobs / repeat-tool-reminder / model-selection / user-approval → 剔除

⚠️ 这正是 Session 017-e 的教训（不得把 harness 以 user 角色注入的续跑语当成「用户提示词」）：
   若只按 `role=user` 判，会把 CLAUDE.md 全文、运行上下文、技能目录都当成用户提示词写进
   `_prompts_all.md`，那份汇总立刻失真。故本脚本**按 source.kind 白名单**判定，并在文件头
   逐个 kind 计数（评审可据此核对「剔除了什么」）。

⚠️ 区分「人类提示词」与「spawn 注入的委派提示词」靠的是 `source.rpcId`，不是 `origin`：
   · 人类消息（界面/IM 里真的打的字）**带 `source.rpcId`**；
   · `spawn_teammate` 注入的委派提示词只有 `{"kind":"user"}`，**没有 rpcId**
     （实测 2026-10-18：本机 16 个会话里 9 条无 rpcId 的，全是子 agent 会话 seq=10 的委派提示词）。
   ⇒ 无 rpcId 的渲染成 `## 轮次 N — 📨 委派提示词（父 agent 注入）`，**不计入** `_prompts_all.md`。
   ⚠️ 不能改用 `origin == "subagent"` 判 —— 有子 agent 会话后来被人类接着聊
   （`session-cef1dfbf` 的 3 条人类消息就是这种情况，按 origin 判会把人类的话标成"委派"）。

⚠️ 用户在界面上对 `ask_user_question` 的回答**不在** `user/message` 里，而在对应
   `tool/result` 的 `content[0].text`（JSON：`{"answers":[{"id","selected","custom"}]}`）。
   那是**真实的用户原话** ⇒ 本脚本把它单独渲染成一个 `## 轮次 N — 👤 用户` 轮次，
   并在 `<sub>` 里注明来源是「回答问题」，避免被误读成独立提示词。

工具调用 / 结果的导出口径
------------------------
与另四个导出器一致：**工具调用折叠为单行**（超 160 字符以 ` …` 结尾），
**工具结果正文不导出**（体积可达 MB 级）——一律在文件头「未导出」一行如实声明，
需要原文时按文件头给出的路径取 `session.v4.jsonl.zstd`。**不做任何编造性补写。**

用法
----
    python export_transcript_dsh.py                  # 导出本工程的全部 DSH 会话
    python export_transcript_dsh.py --list           # 只列出会话，不写文件
    python export_transcript_dsh.py --session 53ad   # 只导出指定会话（id / 目录名 / 短 ID 前缀）
    python export_transcript_dsh.py --all-projects   # 不按工作目录过滤
    python export_transcript_dsh.py --no-prompts     # 不重建 _prompts_all.md

路径默认值**全部从脚本自身位置推导**，没有一处写死：
  · 仓库根     = `<本脚本>/../../../`（即 `ai/tools/` 往上三级）；
  · 默认输出   = `<仓库根>/ai/raw/`；
  · 会话库根   = `DSH_SESSIONS_DIR` > `DSH_HOME/sessions` > `~/.dsh/sessions`；
  · 会话文件   = 会话库根下的 `session.v4.jsonl.zstd`（`session_file_name()`）；
  · 工作区键   = `dsh_workspace_key(<仓库根>)`（仅用于打印与排查，过滤用的是会话里的 `cwd`）。

⚠️ `_prompts_all.md` 由 `prompts_index.rebuild_prompts_from_raw()` 从**已落盘的全部分片**重建，
   与数据源无关、天然幂等 —— 多个导出脚本共用它，谁后跑都不会截断其他来源。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ⚠️⚠️ 必须在**任何本地模块 import 之前**关掉 .pyc 生成，别把这两行往下挪。
#   本脚本 `import prompts_index`（同目录）会让 Python 在 `ai/tools/` 下生成 `__pycache__/`，
#   而 `tools/check_repo.py` 的 check d)「无禁入文件」是**扫磁盘、不看 git** ⇒ 立刻 FAIL。
#   `.gitignore` 只挡 git 层面的噪声，挡不住扫盘检查；靠"跑完手动删"必然复发。
#   ⇒ 从源头消除。详见 `ai/tools/README.md`「为什么脚本不生成 __pycache__」。
sys.dont_write_bytecode = True

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prompts_index import rebuild_prompts_from_raw, write_text_lf  # noqa: E402

CLIENT_LABEL = "DSH (DeepSeek Harness)"
SESSION_FILE = "session.v4.jsonl.zstd"
HELPER = Path(__file__).resolve().parent / "dsh_session_dump.mjs"
#: 仓库根 = 本脚本 `ai/tools/xxx.py` 往上三级（**从脚本位置推导，不写死盘符/项目名**）
REPO_ROOT = Path(__file__).resolve().parents[2]

#: 只有这一种 source.kind 是人类提示词（实测全量枚举，见文件头）
HUMAN_KINDS = {"user"}
#: 其他 agent / 队友转发 —— 单列渲染，不计轮次
RELAY_KINDS = {"agent-message"}


# ---------------------------------------------------------------------------
# 定位：node / 会话库
# ---------------------------------------------------------------------------
def find_node() -> str:
    """找 Node 可执行文件（解压 zstd 用；顺序：环境变量 → PATH → 常见安装位置）。"""
    for env in ("DSH_NODE_BIN", "NODE_BIN"):
        v = os.environ.get(env)
        if v and Path(v).is_file():
            return v
    w = shutil.which("node")
    if w:
        return w
    for cand in (
        r"C:\Program Files\nodejs\node.exe",
        r"C:\Program Files (x86)\nodejs\node.exe",
        str(Path(os.environ.get("ProgramFiles") or r"C:\Program Files") / "nodejs" / "node.exe"),
        str(Path(os.environ.get("LOCALAPPDATA") or "") / "Programs" / "nodejs" / "node.exe"),
    ):
        if cand and Path(cand).is_file():
            return cand
    raise SystemExit(
        "找不到 node —— 本脚本用 Node 的 zlib 解 zstd（Python 3.13 没有 zstd 模块）。\n"
        "  可用环境变量 DSH_NODE_BIN 指定，或把 node 加进 PATH。"
    )


def session_file_name() -> str:
    """会话文件名（DSH 的磁盘布局常量；集中一处，便于日后协议升级时替换）。"""
    return SESSION_FILE


def dsh_workspace_key(path: str | Path) -> str:
    """把一个工作目录编码成 DSH 的**会话库工作区键**（只用于打印 / 排查）。

    实测编码规则（`D:\\zynq-yolo` → `--D-zynq-yolo--`）：
      · 把路径分隔符（反斜杠、正斜杠、冒号）的**连续串整体**折成一个 `-`
        （⚠️ 不能逐个字符各换一个 `-`：`D:\\x` 会变成 `D--x`，键多一个 `-` 就永远匹配不上）；
      · 首尾各补 `--`；**不改大小写**（实测键保留盘符大写：`--D-zynq-yolo--`）。

    ⚠️ 过滤会话**不靠**这个键，靠会话记录里的 `cwd`（`_norm_dir` 比较）——
       因为键的编码不是协议承诺，`cwd` 才是；且非 ASCII 路径的键编码**未实测**。
       此函数供 `--list` 打印、与 `check_workspace_key()` 的人工排查使用。
    """
    s = str(path).strip().rstrip("\\/")
    s = re.sub(r"[\\/:]+", "-", s)
    return "--%s--" % s


def session_dirs_under(root: Path) -> List[str]:
    """列出会话库根下**真实存在**的工作区键目录名（打印/排查用，不解压任何会话）。"""
    try:
        return sorted(p.name for p in root.iterdir() if p.is_dir())
    except OSError:
        return []


def check_workspace_key(root: Path, key: str) -> Tuple[str, bool]:
    """核对推导出的工作区键是否真在会话库里；返回 (人读结论, 是否命中)。

    ⚠️ 会话库根既可能是 `<root>/<工作区键>/<会话 ID>/`，也可能被直接指向某个
       `<工作区键>` 目录 —— 后者由 `dsh_session_dump.mjs` 的 `findSessionDirs()` 支持。
       故这里只在**根下确实存在工作区键目录**时才报不一致，绝不据此退出。
    """
    dirs = session_dirs_under(root)
    if key in dirs:
        return "命中（会话库根下存在该键目录）", True
    if any((root / d).is_dir() and (root / d / SESSION_FILE).is_file() for d in dirs):
        return "会话库根直接指向单个工作区键目录（无需再分层）", True
    return ("未命中：根下现有键目录为 %s —— 过滤靠会话里的 `cwd`，此键仅供排查"
            % ("、".join("`%s`" % d for d in dirs) or "（无）")), False


def sessions_root() -> Path:
    """DSH 会话库根目录：DSH_SESSIONS_DIR > DSH_HOME/sessions > ~/.dsh/sessions。"""
    env = os.environ.get("DSH_SESSIONS_DIR")
    if env:
        return Path(env)
    home = os.environ.get("DSH_HOME")
    if home:
        return Path(home) / "sessions"
    return Path.home() / ".dsh" / "sessions"


def _norm_dir(p: str) -> str:
    return str(p).replace("\\", "/").rstrip("/").lower()


# ---------------------------------------------------------------------------
# Node 助手：列表 / 解压
# ---------------------------------------------------------------------------
def _run_helper(node: str, args: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [node, str(HELPER)] + args,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def list_sessions(node: str, root: Path) -> List[Dict[str, Any]]:
    r = _run_helper(node, ["--list", str(root)])
    if r.returncode != 0:
        raise SystemExit("解压助手 --list 失败：%s" % (r.stderr or r.stdout).strip())
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError as e:
        raise SystemExit("解压助手 --list 输出不是 JSON：%s" % e)


def dump_jsonl(node: str, src: Path, dst: Path) -> Dict[str, Any]:
    r = _run_helper(node, ["--dump", str(src), str(dst)])
    if r.returncode != 0:
        raise SystemExit("解压助手 --dump 失败（%s）：%s" % (src.name, (r.stderr or r.stdout).strip()))
    line = (r.stdout or "").strip().splitlines()
    try:
        return json.loads(line[-1]) if line else {}
    except json.JSONDecodeError:
        return {}


# ---------------------------------------------------------------------------
# 短 ID（文件名用）
# ---------------------------------------------------------------------------
_UUID_PREFIX_RE = re.compile(r"^(?:session-)?([0-9a-fA-F]{8})-[0-9a-fA-F]{4}-")


def short_id(session_id: str) -> str:
    """文件名里的 8 字符短 ID。

    · 纯 UUID / `session-<uuid>` → 取前 8 位（与另四个导出器一致）；
    · `im:<provider>_<token>:dm:<ts>:<token>@im.wechat` 这类 IM 派生 id → **不能**取前 8 位，
      因为同一 provider 的多个 IM 会话前缀完全相同（实测 3 个微信会话都从 `im:weixin_…` 起），
      取前 8 位会在 `ai/raw/` 里互相撞名。故用 sha1 前 5 位，前缀 `im_`，共 8 字符。
    短 ID 只用于文件名与 H1（供 `prompts_index` 分组），完整 id 在文件头表格里。
    """
    m = _UUID_PREFIX_RE.match(session_id)
    if m:
        return m.group(1).lower()
    tag = "im" if session_id.lower().startswith("im:") else "dsh"
    return "%s_%s" % (tag, hashlib.sha1(session_id.encode("utf-8")).hexdigest()[:5])


# ---------------------------------------------------------------------------
# 解析：记录 → 事件
# ---------------------------------------------------------------------------
def _blocks(container: Any) -> List[Dict[str, Any]]:
    if not isinstance(container, dict):
        return []
    c = container.get("content")
    if not isinstance(c, list):
        return []
    return [b for b in c if isinstance(b, dict)]


def _clip(s: str, n: int = 160) -> str:
    s = s.replace("\n", " ").strip()
    return s[:n] + (" …" if len(s) > n else "")


def tool_summary(name: str, args: Any) -> str:
    """把一次工具调用压成一行 —— 保留「做过什么」，不淹没正文（与另四个导出器同口径）。"""
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            return "`%s` %s" % (name, _clip(args))
    if isinstance(args, dict):
        for key in ("file_path", "path", "filePath", "file"):
            if isinstance(args.get(key), str) and args[key]:
                return "`%s` %s" % (name, args[key])
        if isinstance(args.get("command"), str) and args["command"]:
            return "`%s` %s" % (name, _clip(args["command"]))
        for key in ("pattern", "query", "url", "task_id", "session_id", "target",
                    "subject", "description", "name", "content", "prompt", "id"):
            if args.get(key):
                return "`%s` %s" % (name, _clip(str(args[key])))
        if args:
            return "`%s` (%s)" % (name, ", ".join(list(args)[:3]))
    return "`%s`" % name


def _code_fence(body: str) -> str:
    """按正文里最长的一串反引号，算一个**足够长**的围栏（至少 3 个)。

    ⚠️ 这是「逐字」的必要条件，不是美化：正文里若自带一行 ```，用固定三反引号围栏会让
       Markdown 提前闭合，剩余内容掉到围栏外面 —— 肉眼看不出、字数却少了三个数量级。
       围栏取「正文内最长反引号连串长度 + 1」，保证正文里的任何反引号串都闭合不了它。
    """
    longest = 0
    for m in re.finditer(r"`+", body or ""):
        longest = max(longest, len(m.group(0)))
    return "`" * max(3, longest + 1)


def _user_body(d: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]]]:
    """把一条 user/message 拆成「文本正文」与「附件清单」。

    ⚠️ 附件（`content[].type ∈ {"file", "image"}`）只有 attachmentId / name / bytes / mediaType，
       **正文未导出**（二进制在 DSH 的附件库里，不在会话文件里）；这里**不**把附件名混进提示词
       正文（否则 `_prompts_all.md` 会出现用户没打过的字），而是单列清单渲染在提示词块**之外**。
    """
    texts: List[str] = []
    files: List[Dict[str, Any]] = []
    for b in _blocks(d):
        bt = b.get("type")
        if bt in ("file", "image"):
            att = b.get("attachment") or {}
            files.append({"kind": bt, "name": att.get("name") or bt,
                          "bytes": att.get("bytes"), "media": att.get("mediaType")})
        elif b.get("text"):
            texts.append(str(b["text"]))
    return "\n".join(texts).strip(), files


def _parse_answer(raw: str, ts: Optional[int]) -> Optional[Dict[str, Any]]:
    """把 `ask_user_question` 的 tool/result 正文（JSON）还原成用户回答。"""
    try:
        obj = json.loads(raw)
    except Exception:
        return None
    answers = obj.get("answers")
    if not isinstance(answers, list) or not answers:
        return None
    customs: List[str] = []
    selecteds: List[str] = []
    ids: List[str] = []
    for a in answers:
        if not isinstance(a, dict):
            continue
        if a.get("id"):
            ids.append(str(a["id"]))
        c = a.get("custom")
        if isinstance(c, str) and c.strip():
            customs.append(c.strip())
        s = a.get("selected")
        if isinstance(s, list):
            selecteds.extend([str(x) for x in s if x])
    body = "\n".join(customs) if customs else "\n".join("【选定】%s" % x for x in selecteds)
    return {"kind": "answer", "text": body, "files": [], "ts": ts,
            "extra": {"raw": raw, "ids": ids, "selected": selecteds, "custom": customs}}


def parse_events(records: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """记录流 → 按时间顺序的事件流（assistant 步 / 用户提示 / 团队消息 / 用户回答）。"""
    stats: Dict[str, Any] = {
        "n_prompt": 0, "n_delegation": 0, "n_answer": 0, "n_relay": 0,
        "n_assistant_records": 0, "n_tool_calls": 0,
        "n_tool_call_records": 0, "n_tool_result_records": 0,
        "skipped_kinds": {}, "skipped_records": {},
    }
    call_names: Dict[str, str] = {}
    events: List[Dict[str, Any]] = []

    def bump(d: Dict[str, int], k: str) -> None:
        d[k] = d.get(k, 0) + 1

    for rec in records:
        typ = rec.get("type")
        d = rec.get("data") or {}
        ts = rec.get("time")

        if typ == "tool/call":
            cid = d.get("callId")
            if cid:
                call_names[str(cid)] = str(d.get("name") or "?")
            stats["n_tool_call_records"] += 1
            continue

        if typ == "tool/result":
            stats["n_tool_result_records"] += 1
            m = d.get("message") or {}
            cid = m.get("toolCallId") or (m.get("source") or {}).get("callId")
            name = call_names.get(str(cid) or "")
            if name == "ask_user_question" and not m.get("isError"):
                raw = "".join(str(b.get("text") or "") for b in _blocks(m))
                ev = _parse_answer(raw, ts)
                if ev:
                    events.append(ev)
                    stats["n_answer"] += 1
            continue

        if typ == "assistant/message":
            m = d.get("message") or {}
            stats["n_assistant_records"] += 1
            rec_no = stats["n_assistant_records"]
            for b in _blocks(m):
                bt = b.get("type")
                if bt == "text" and b.get("text"):
                    events.append({"kind": "text", "text": str(b["text"]), "ts": ts, "rec": rec_no})
                elif bt == "reasoning" and b.get("text"):
                    events.append({"kind": "reasoning", "text": str(b["text"]), "ts": ts, "rec": rec_no})
                elif bt == "tool-call":
                    cid = b.get("id")
                    nm = str(b.get("name") or "?")
                    if cid:
                        call_names.setdefault(str(cid), nm)
                    stats["n_tool_calls"] += 1
                    events.append({"kind": "tool", "text": tool_summary(nm, b.get("arguments")),
                                   "ts": ts, "rec": rec_no})
            continue

        if typ == "user/message":
            src = d.get("source") or {}
            kind = str(src.get("kind") or "<none>")
            body, files = _user_body(d)
            sender = src.get("senderSessionId")
            if kind in HUMAN_KINDS:
                # ★ 人类消息带 `source.rpcId`（界面上真的是人打的字）；
                #   spawn 注入的**委派提示词**只有 `{"kind":"user"}`，**没有 rpcId**
                #   （实测：本机 16 个会话里 9 条无 rpcId 的全是 seq=10 的 spawn 提示词）。
                #   不能只按 origin=="subagent" 判 —— 有子 agent 会话后来被人类接着聊
                #   （session-cef1dfbf 的 3 条人类消息就是这种情况）。
                if src.get("rpcId"):
                    stats["n_prompt"] += 1
                    events.append({"kind": "prompt", "text": body, "files": files, "ts": ts,
                                   "sender": sender})
                else:
                    stats["n_delegation"] += 1
                    events.append({"kind": "delegation", "text": body, "files": files, "ts": ts,
                                   "sender": sender})
            elif kind in RELAY_KINDS:
                stats["n_relay"] += 1
                events.append({"kind": "relay", "text": body, "files": files, "ts": ts, "sender": sender})
            else:
                bump(stats["skipped_kinds"], kind)
            continue

        # 其余：system/message、agent/inbox/spliced、turn/*、step/*、job/*、team/*、
        #       approval/*、command/*、session/title… 均为内部状态或注入正文，不进正文
        bump(stats["skipped_records"], str(typ))

    return events, stats


# ---------------------------------------------------------------------------
# 事件 → 轮次
# ---------------------------------------------------------------------------
def build_turns(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把事件流合并成「轮次」：每条**入站消息**（人类提示 / 父 agent 委派 / 用户回答 /
    团队转发）开一个新轮次，其后的 assistant 步按时间顺序挂在同一轮次里（轮次号全局连续）。"""
    turns: List[Dict[str, Any]] = []
    n = 0

    def new_turn(kind: str, text: str, ts: Optional[int], files, extra, sender) -> Dict[str, Any]:
        nonlocal n
        n += 1
        t = {"no": n, "user_kind": kind, "user_text": text, "user_ts": ts,
             "files": files or [], "extra": extra or {}, "sender": sender,
             "items": [], "recs": set(), "ts": ts, "ts_end": ts}
        turns.append(t)
        return t

    for ev in events:
        k = ev["kind"]
        if k == "relay":
            # 团队消息是一次**入站消息**（结构上与用户提示同级），故单独开一个轮次，
            # 但标题不是「👤 用户」⇒ 不会进 `_prompts_all.md`。
            new_turn("relay", ev.get("text") or "", ev.get("ts"),
                     ev.get("files"), None, ev.get("sender"))
            continue
        if k in ("prompt", "delegation", "answer"):
            new_turn(k, ev.get("text") or "", ev.get("ts"),
                     ev.get("files"), ev.get("extra"), ev.get("sender"))
            continue
        if not turns:
            new_turn("none", "", ev.get("ts"), None, None, None)
        t = turns[-1]
        t["items"].append((k, ev.get("text") or ""))
        if ev.get("rec"):
            t["recs"].add(ev["rec"])
        if ev.get("ts"):
            t["ts_end"] = ev["ts"]
            if t["ts"] is None:
                t["ts"] = ev["ts"]
    return turns


# ---------------------------------------------------------------------------
# 分天 / 渲染（格式与另四个导出脚本一致，否则 prompts_index 重建会漏）
# ---------------------------------------------------------------------------
def _fmt_ms(ms: Optional[int]) -> str:
    if not ms:
        return "—"
    try:
        return datetime.fromtimestamp(ms / 1000.0).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError, OverflowError):
        return str(ms)


def group_turns_by_date(turns: List[Dict[str, Any]]) -> List[Tuple[str, List[Dict[str, Any]]]]:
    """按「轮次发生的自然日」（本地时区）切分；跨零点会话拆多份，轮次号不重置。"""
    groups: List[Tuple[str, List[Dict[str, Any]]]] = []
    for t in turns:
        ms = t["user_ts"] if (t["user_kind"] != "none" and t["user_ts"]) else t["ts"]
        day = _fmt_ms(ms)[:10] or "unknown"
        if groups and groups[-1][0] == day:
            groups[-1][1].append(t)
        else:
            groups.append((day, [t]))
    return groups


def _turn_range(day: List[Dict[str, Any]]) -> Tuple[int, int]:
    nos = [t["no"] for t in day]
    return (min(nos), max(nos)) if nos else (0, 0)


def _day_filename(date: str, sid: str) -> str:
    return "%s_%s.md" % (date, sid)


def _who(sender: Any) -> str:
    """把 senderSessionId 压成可读短名：IM 会话给渠道名，子 agent 给短 ID。"""
    if not sender:
        return "—"
    s = str(sender)
    if s.startswith("im:"):
        # im:qq_9cd980efc1f8:dm:… / im:weixin_a50403359ef9:…@im.wechat
        return s.split(":")[1].split("_")[0]
    return s[:8]


def _skipped_summary(kinds: Dict[str, int], records: Dict[str, int]) -> str:
    """把剔除项压成一行（表格单元格里不能有竖线，故用顿号）。"""
    parts = []
    if kinds:
        parts.append("注入 user 消息 " + "、".join(
            "`%s` %d" % (k, v) for k, v in sorted(kinds.items(), key=lambda kv: -kv[1])))
    if records:
        top = sorted(records.items(), key=lambda kv: -kv[1])[:6]
        rest = sum(v for _, v in sorted(records.items(), key=lambda kv: -kv[1])[6:])
        s = "、".join("`%s` %d" % (k, v) for k, v in top)
        if rest:
            s += "、其余 %d" % rest
        parts.append("内部状态记录 " + s)
    return "；".join(parts) if parts else "无"


def render_session(day_turns: List[Dict[str, Any]], meta: Dict[str, Any],
                   day: str, all_days: List[Tuple[str, List[Dict[str, Any]]]],
                   stats: Dict[str, Any]) -> str:
    sid = meta["short_id"]
    multi = len(all_days) > 1
    idx = [d for d, _ in all_days].index(day) + 1
    no_lo, no_hi = _turn_range(day_turns)
    n_human = sum(1 for t in day_turns if t["user_kind"] == "prompt")
    n_deleg = sum(1 for t in day_turns if t["user_kind"] == "delegation")
    n_ans = sum(1 for t in day_turns if t["user_kind"] == "answer")
    n_relay = sum(1 for t in day_turns if t["user_kind"] == "relay")
    n_tools = sum(1 for t in day_turns for k, _ in t["items"] if k == "tool")
    n_steps = len({r for t in day_turns for r in t.get("recs", ())})
    t_lo = _fmt_ms(next((t["ts"] for t in day_turns if t["ts"]), None))
    t_hi = _fmt_ms(next((t["ts_end"] or t["ts"] for t in reversed(day_turns)
                         if t["ts_end"] or t["ts"]), None))
    total_turns = sum(len(ts) for _, ts in all_days)
    others = ["`%s`（轮次 %d–%d）" % (_day_filename(d, sid), _turn_range(ts)[0], _turn_range(ts)[1])
              for d, ts in all_days if d != day]

    if meta["origin"] == "subagent":
        kind_row = "子 agent 会话（`origin=subagent`，委派深度 %d，父会话 `%s`）" % (
            meta.get("delegationDepth") or 0, meta.get("parentSession") or "—")
    else:
        kind_row = "主会话（`origin=primary`）"

    L: List[str] = []
    L.append("# 会话原文 — %s" % sid)
    L.append("")
    L.append("> ⚠️ **本文件由 `export_transcript_dsh.py`（DSH 客户端）自动导出，请勿手工编辑。**")
    L.append("> 用户提示词为**逐字原文**，未做任何改写、润色或截断。")
    L.append("> 客户端 DSH（DeepSeek Harness），源文件 `%s`（zstd 多帧，Node 标准库解压）。"
             % SESSION_FILE)
    if meta["origin"] == "subagent" and stats["n_delegation"]:
        L.append("> 📨 本会话是**子 agent 会话**：其中的「委派提示词」轮次是 spawn 时父 agent 写的，"
                 "按规则**不计入** `_prompts_all.md`（那份汇总只收人类提示词）。")
    L.append("")
    if multi:
        L.append("> 📌 **本会话跨 %d 天，已按天切分。**本文件是 **%s** 那一天的片段（第 %d/%d 天）。"
                 % (len(all_days), day, idx, len(all_days)))
        L.append("> 其余分片：%s。" % "、".join(others))
        L.append("> 轮次编号**沿用会话内全局序号、不重排**，故本文件从「轮次 %d」开始（而非 1）——"
                 " 这是刻意的，便于与其它分片对照。" % no_lo)
        L.append("")
    L.append("| 字段 | 值 |")
    L.append("|---|---|")
    L.append("| 会话 ID | `%s` |" % meta["session_id"])
    L.append("| 短 ID（文件名） | `%s` |" % sid)
    L.append("| 会话类型 | %s |" % kind_row)
    L.append("| 客户端 | %s · 协议 `session.v4.jsonl` |" % CLIENT_LABEL)
    L.append("| 轮次口径 | 一个轮次 = 一条**入站消息**（人类提示词 / 父 agent 委派 / 用户回答 / 团队转发）；"
             "`system/message`、注入上下文与内部状态**不占轮次** |")
    if meta.get("model"):
        L.append("| 模型 | `%s` |" % meta["model"])
    L.append("| 工作目录 | `%s` |" % (meta.get("cwd") or ""))
    if multi:
        L.append("| 本文件日期 | **%s**（本会话第 %d/%d 天） |" % (day, idx, len(all_days)))
        L.append("| 本文件轮次 | 轮次 %d–%d |" % (no_lo, no_hi))
        L.append("| 本文件时间 | %s → %s |" % (t_lo, t_hi))
        L.append("| 会话整体时间 | %s → %s（共 %d 轮，跨 %d 天） |"
                 % (_fmt_ms(meta.get("start")), _fmt_ms(meta.get("end")), total_turns, len(all_days)))
        L.append("| 同一会话的其他分片 | %s |" % "、".join(others))
    else:
        L.append("| 开始时间 | %s |" % _fmt_ms(meta.get("start")))
        L.append("| 结束时间 | %s |" % _fmt_ms(meta.get("end")))
    L.append("| 本文件人类提示词 | %d 条%s |"
             % (n_human, "（会话合计 %d 条）" % stats["n_prompt"] if multi else ""))
    if n_deleg or stats["n_delegation"]:
        L.append("| 委派提示词（父 agent） | %d 条%s |"
                 % (n_deleg, "（会话合计 %d 条）" % stats["n_delegation"] if multi else ""))
    L.append("| 用户回答（ask_user_question） | %d 条%s |"
             % (n_ans, "（会话合计 %d 条）" % stats["n_answer"] if multi else ""))
    L.append("| 团队消息（agent 转发，非人类） | %d 条 |" % n_relay)
    L.append("| 助手步（assistant/message） | %d 步%s |"
             % (n_steps, "（会话合计 %d 步）" % stats["n_assistant_records"] if multi else ""))
    L.append("| 工具调用 | %d 次（折叠为单行）%s |"
             % (n_tools, "，会话合计 %d 次" % stats["n_tool_calls"] if multi else ""))
    L.append("| 已剔除 | %s%s |"
             % (_skipped_summary(stats["skipped_kinds"], stats["skipped_records"]),
                "（会话整体）" if multi else ""))
    L.append("| 未导出 | 工具结果正文（`ask_user_question` 的用户回答除外）、工具调用参数全文"
             "（折叠单行，超 160 字符以 ` …` 结尾）、`system/message` 与注入上下文正文、附件二进制 |")
    if meta.get("truncated_tail"):
        L.append("| ⚠️ 读取提示 | 导出时源文件正在追加，**最后一帧未写完**，该帧内容未导出 |")
    L.append("")
    L.append("---")
    L.append("")

    for t in day_turns:
        k = t["user_kind"]
        if k == "prompt":
            L.append("## 轮次 %d — 👤 用户" % t["no"])
            L.append("")
            L.append("<sub>%s</sub>" % _fmt_ms(t["user_ts"]))
            L.append("")
        elif k == "answer":
            L.append("## 轮次 %d — 👤 用户" % t["no"])
            L.append("")
            ids = t["extra"].get("ids") or []
            L.append("<sub>%s · 对 `ask_user_question` 的回答（问题 id=%s，非独立提问）</sub>"
                     % (_fmt_ms(t["user_ts"]), ", ".join(ids) or "—"))
            L.append("")
        elif k == "delegation":
            L.append("## 轮次 %d — 📨 委派提示词（父 agent 注入，非人类）" % t["no"])
            L.append("")
            L.append("<sub>%s</sub>" % _fmt_ms(t["user_ts"]))
            L.append("")
        elif k == "relay":
            L.append("## 轮次 %d — 📨 团队消息（来自 `%s`，非人类提示词）"
                     % (t["no"], _who(t.get("sender"))))
            L.append("")
            L.append("<sub>%s</sub>" % _fmt_ms(t["user_ts"]))
            L.append("")

        if k != "none":
            # ⚠️ 围栏长度必须**大于正文里自带的最长 ``` 行**，否则正文里的 ``` 会把围栏提前闭合，
            #    后面的内容就被挤到代码块外面（实测：一条 15868 字符的团队消息被截成 1802 字符，
            #    而 markdown 看起来仍然「正常」—— 只有逐字节回比才发现）。逐字的前提是围栏够长。
            body = t["user_text"] or ("（本条消息无文本正文，只有附件）" if t["files"] else "（空）")
            fence = _code_fence(body)
            L.append(fence + "text")
            L.append(body)
            L.append(fence)
            L.append("")
            if t["files"]:
                L.append("<sub>附件 %d 个（二进制正文未导出）：%s</sub>"
                         % (len(t["files"]),
                            "、".join("%s `%s`（%s 字节）"
                                     % ("图片" if f.get("kind") == "image" else "文件",
                                        f["name"], f.get("bytes"))
                                     for f in t["files"])))
                L.append("")
            if k == "answer" and t["extra"].get("raw"):
                L.append("<details><summary>原始回答 JSON 与选项（逐字）</summary>")
                L.append("")
                L.append("```json")
                L.append(t["extra"]["raw"])
                L.append("```")
                L.append("")
                L.append("</details>")
                L.append("")

        # ---- 该轮次的模型回答 ----
        reasonings: List[str] = []
        tools: List[str] = []
        has_answer = False
        for kind, _ in t["items"]:
            if kind in ("text", "reasoning", "tool"):
                has_answer = True
                break

        if has_answer:
            L.append("### 🤖 模型回答")
            L.append("")
            span = _fmt_ms(t["ts"]) if t["ts"] else _fmt_ms(t["user_ts"])
            if t["ts_end"] and t["ts_end"] != t["ts"]:
                span += " → %s" % _fmt_ms(t["ts_end"])
            L.append("<sub>%s</sub>" % span)
            L.append("")

            # 正文按发生顺序输出；思考与工具调用折叠到轮末
            for kind, payload in t["items"]:
                if kind == "text":
                    L.append(payload)
                    L.append("")
                elif kind == "reasoning":
                    reasonings.append(payload)
                elif kind == "tool":
                    tools.append(payload)

            if reasonings:
                L.append("<details><summary>思考过程（reasoning）%d 段，逐字</summary>" % len(reasonings))
                L.append("")
                for r in reasonings:
                    L.append(r)
                    L.append("")
                L.append("</details>")
                L.append("")
            if tools:
                L.append("<details><summary>本轮调用工具 %d 次（折叠为单行，完整参数见原文）</summary>"
                         % len(tools))
                L.append("")
                for i, s in enumerate(tools, 1):
                    L.append("%d. %s" % (i, s))
                L.append("")
                L.append("</details>")
                L.append("")
            L.append("---")
            L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def _force_utf8() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass


def _read_jsonl(path: Path) -> Tuple[List[Dict[str, Any]], int]:
    records: List[Dict[str, Any]] = []
    bad = 0
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            if isinstance(rec, dict):
                records.append(rec)
    return records, bad


def _round_flags(info: List[Dict[str, Any]], current: str,
                 explicit_lead: str) -> Tuple[Dict[str, bool], str]:
    """标出「本轮 / 非本轮」会话，供 `--list` 检索用。

    口径（写死在代码里，避免每次凭印象划范围）：
      · **本轮** = ① 当前会话本身（`DSH_SESSION_ID`）；② 它的父会话（本轮 Lead）；
        ③ 全部 `origin=subagent` 的子 agent 会话（本轮的派生物）。
      · **非本轮** = 同工作区里其他真人会话（别的聊天线程）。它们**照样导出**
        （原始层不接受选择性取证），只是清单里标出来，方便日后检索。
    """
    lead = explicit_lead
    if not lead and current:
        for s in info:
            if str(s.get("sessionId") or s.get("dir")) == current:
                lead = str(s.get("parentSession") or "")
                break
    flags: Dict[str, bool] = {}
    for s in info:
        sid = str(s.get("sessionId") or s.get("dir"))
        flags[sid] = bool((current and sid == current)
                          or (lead and sid == lead)
                          or s.get("origin") == "subagent")
    return flags, lead


def main(argv: Optional[List[str]] = None) -> int:
    _force_utf8()
    ap = argparse.ArgumentParser(description="把 DSH（DeepSeek Harness）会话导出为 ai/raw/*.md")
    ap.add_argument("--out", default=None, help="输出目录（默认 <repo>/ai/raw）")
    ap.add_argument("--list", action="store_true",
                    help="只列出会话，不写文件（含 本轮/非本轮 标记、角色 label、首句）")
    ap.add_argument("--all-projects", action="store_true", help="不按工作目录过滤")
    ap.add_argument("--session", default=None, help="只导出 id / 目录名 / 短 ID 以该前缀开头的会话")
    ap.add_argument("--no-prompts", action="store_true", help="不重建 _prompts_all.md")
    ap.add_argument("--sessions-dir", default=None, help="DSH 会话库根（默认 ~/.dsh/sessions）")
    ap.add_argument("--workspace-key", default=None,
                    help="会话库工作区键（默认由本仓库根路径推导，仅用于打印/排查）")
    ap.add_argument("--round-lead", default=None,
                    help="指定本轮 Lead 会话 ID（默认取当前会话的 parentSession）")
    args = ap.parse_args(argv)

    repo = REPO_ROOT                       # ← 从脚本位置推导，不写死盘符/项目名
    key = args.workspace_key or dsh_workspace_key(repo)
    out_dir = Path(args.out) if args.out else (repo / "ai" / "raw")
    root = Path(args.sessions_dir) if args.sessions_dir else sessions_root()
    if not root.is_dir():
        print("找不到 DSH 会话库：%s" % root, file=sys.stderr)
        return 2
    node = find_node()
    key_note, key_hit = check_workspace_key(root, key)
    print("仓库根：%s" % repo)
    print("工作区键（由仓库根推导）：%s —— %s" % (key, key_note))
    print("数据源（会话库根）：%s" % root)
    print("会话文件：%s" % session_file_name())
    if not args.list:
        print("输出目录：%s" % out_dir)

    info = list_sessions(node, root)
    want = _norm_dir(str(repo))
    selected: List[Dict[str, Any]] = []
    for s in info:
        if s.get("error"):
            print("  ! %s 读取失败：%s" % (str(s.get("dir"))[:16], s["error"]))
            continue
        if not args.all_projects and _norm_dir(s.get("cwd") or "") != want:
            continue
        if args.session:
            raw_sid = str(s.get("sessionId") or "")
            short = short_id(raw_sid or str(s.get("dir")))
            if not (str(s.get("dir", "")).startswith(args.session)
                    or raw_sid.startswith(args.session)
                    or short.startswith(args.session)):
                continue
        selected.append(s)
    selected.sort(key=lambda s: s.get("createdAt") or 0)

    if not selected:
        print("没有匹配的 DSH 会话（试试 --all-projects 或 --session）。")
        return 1

    if args.list:
        current = str(os.environ.get("DSH_SESSION_ID") or "")
        flags, lead = _round_flags(info, current, args.round_lead)
        print("  短ID       创建时间             类型      归属    角色 label / 首句")
        if lead:
            print("  （本轮 Lead = `%s`；本轮 = 当前会话 ∪ 其父会话 ∪ 全部子 agent 会话）" % lead)
        for s in selected:
            raw_sid = str(s.get("sessionId") or s.get("dir"))
            mark = "本轮" if flags.get(raw_sid) else "非本轮"
            desc = s.get("label") or s.get("firstPrompt") or ""
            print("  %-10s %s  %-8s %-6s %s"
                  % (short_id(raw_sid), _fmt_ms(s.get("createdAt")),
                     s.get("origin") or "?", mark, _clip(desc, 90)))
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    tmpdir = Path(tempfile.mkdtemp(prefix="dsh_export_"))
    try:
        for s in selected:
            raw_sid = str(s.get("sessionId") or s.get("dir"))
            short = short_id(raw_sid)
            src = Path(s["file"])
            tmp_jsonl = tmpdir / (short + ".jsonl")
            dump_info = dump_jsonl(node, src, tmp_jsonl)
            records, bad = _read_jsonl(tmp_jsonl)
            if not records:
                print("  · %s 无记录，跳过" % short)
                continue

            origin = str(s.get("origin") or "primary")
            events, stats = parse_events(records)
            turns = build_turns(events)
            if not turns:
                print("  · %s 无发言内容，跳过" % short)
                continue

            times = [r.get("time") for r in records if r.get("time")]
            model = ""
            for r in records:
                if r.get("type") == "request/context":
                    d = r.get("data") or {}
                    if d.get("provider") and d.get("model"):
                        model = "%s/%s" % (d["provider"], d["model"])
                        break
            if not model:
                for r in records:
                    if r.get("type") == "model/selection":
                        d = r.get("data") or {}
                        model = "%s/%s" % (d.get("provider") or "?", d.get("model") or "?")
                        break

            meta = {
                "session_id": raw_sid,
                "short_id": short,
                "origin": origin,
                "parentSession": s.get("parentSession"),
                "delegationDepth": s.get("delegationDepth"),
                "cwd": s.get("cwd") or "",
                "model": model,
                "start": min(times) if times else s.get("createdAt"),
                "end": max(times) if times else None,
                "truncated_tail": bool(dump_info.get("truncatedTail")),
            }
            if bad:
                print("  ! %s 有 %d 行 JSON 解析失败（已跳过，未编造）" % (short, bad))

            all_days = group_turns_by_date(turns)
            for day, day_turns in all_days:
                md = render_session(day_turns, meta, day, all_days, stats)
                dst = out_dir / _day_filename(day, short)
                # 显式 LF：`write_text` 默认按 os.linesep 写 CRLF，与 `.gitattributes`
                # 的 `* text=auto eol=lf` 冲突 ⇒ clone 后「逐字节」主张失效。
                # 用 `write_text_lf()`（`open(..., newline="\n")`）而非 `write_text(newline=)`，
                # 因为后者是 Python 3.10+ 才有的签名，本仓库最低要求 3.8+。
                n_cr = write_text_lf(dst, md)
                if n_cr:
                    print("  ! %s 正文自带 %d 个 CR（逐字原文含 CRLF，已原样保留，未改写）"
                          % (dst.name, n_cr))
                lo, hi = _turn_range(day_turns)
                print("  ✓ %s  (轮次 %d–%d，人类提示 %d 条)"
                      % (dst.name, lo, hi, sum(1 for t in day_turns if t["user_kind"] == "prompt")))
                written += 1
            if dump_info.get("truncatedTail"):
                print("  ! %s 源文件末帧未写完，该帧未导出（会话仍在进行）" % short)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)  # 中间文件用完即删

    if not args.no_prompts:
        md, n_prompts, n_days = rebuild_prompts_from_raw(out_dir)
        n_cr = write_text_lf(out_dir / "_prompts_all.md", md)  # 同样必须显式 LF（见上）
        if n_cr:
            print("  ! _prompts_all.md 正文自带 %d 个 CR（已原样保留）" % n_cr)
        print("  ✓ _prompts_all.md  (合计 %d 条提示词，跨 %d 天)" % (n_prompts, n_days))

    print("完成：写出 %d 个分片。" % written)
    return 0


if __name__ == "__main__":
    sys.exit(main())
