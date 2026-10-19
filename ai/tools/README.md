# ai/tools/ —— `ai/raw/` 原始层的导出与自证脚本

> 上级：[`../README.md`](../README.md)
> 唯一事实源：[`../../docs/repo_spec.md`](../../docs/repo_spec.md) §1（命名）/ §2（目录职责）/ §3（README 模板）
> 依据：指南 3.3.5.3（大模型协作记录为报告必含项）· 指南 3.3.5.5（「大模型协作与技能包」占 15 分）

| | |
|---|---|
| 本目录装什么 | **把 DSH（DeepSeek Harness）会话导出成 `ai/raw/*.md` 逐字原文**的三个脚本 + 一个**独立自证**脚本 |
| 谁产生 | **导入的外部资产**（移植自同一用户的旧项目）+ 一个本仓库新写的核对脚本；**非本轮从零编写** |
| 进 git | 是 —— 脚本是「原始层可重建」的前提，缺了它就只剩手抄 |

**一句话**：`ai/raw/` 里每一份 `.md` 都必须能由本目录的脚本**一条命令重新生成**，
并且能由 `verify_raw_match.py` **逐字节**证明它和会话原始记录一致。
**做不到这两条，`raw/` 就退化成手抄，证据链的根就是假的。**

---

## 内容

| 路径 | 说明 | 来源 |
|---|---|---|
| [`export_transcript_dsh.py`](export_transcript_dsh.py) | **主导出器**：读 DSH 会话 → 写 `ai/raw/YYYY-MM-DD_<短ID>.md`；把人机发言/团队消息/委派提示词分类渲染，并在文件头逐项统计「剔除了什么」 | **移植**（见下「出处与移植」） |
| [`dsh_session_dump.mjs`](dsh_session_dump.mjs) | **Node 解压助手**：`--list` 只解首帧拿会话元数据（快），`--dump` 按 zstd **帧**逐个解压成临时 JSONL（慢但完整）。被上面那个 `.py` 以子进程调用 | **移植**（同上） |
| [`prompts_index.py`](prompts_index.py) | **提示词汇总重建**：从 `ai/raw/` 下**已落盘的全部分片**重建 `_prompts_all.md`。被主导出器 `import`，故必须与它同在 `ai/tools/` | **移植**（同上；主导出器硬依赖它） |
| [`verify_raw_match.py`](verify_raw_match.py) | **逐字自证**：独立解压会话原文，把**四类入站发言**（人类提问 / 人类回答 / 委派提示词 / 团队消息）与 `ai/raw/` 分片里的对应正文**逐字节**比对；**并独立审计行尾是否为 LF**（见「行尾必须是 LF」）。不一致 / 行尾不合规都退出码 1 并打印差异位置 | **本仓库新写**（2026-10-19） |

### 产物（不在本目录，在 `../raw/`）

| 产物 | 说明 |
|---|---|
| `../raw/YYYY-MM-DD_<短ID>.md` | 单个会话某一天的逐字原文（会话跨零点则按天切分，轮次号不重置） |
| `../raw/_prompts_all.md` | 全部会话的用户提示词汇总（按日期分组，**从全部分片重建**，不是某次运行的增量） |

---

## 出处与移植（**如实记录，不据为己有**）

**这是一对已验证可用的外部脚本，不是本仓库原创。** 2026-10-19 从同一用户的旧项目
`D:\FPGA\oi` **逐字节复制**后做**最小改动**：

| 本目录文件 | 来源（只读，未修改源文件） | 复制方式 | 原文件字节数 |
|---|---|---|---|
| `export_transcript_dsh.py` | `D:\FPGA\oi\ai\tools\export_transcript_dsh.py` | `Copy-Item` 后逐处改 **7 处**（见下） | 41 436 |
| `dsh_session_dump.mjs` | `D:\FPGA\oi\ai\tools\dsh_session_dump.mjs` | `Copy-Item` **零改动**（SHA256 与原文件一致） | 9 546 |
| `prompts_index.py` | `D:\FPGA\oi\ai\tools\prompts_index.py` | `Copy-Item` 后改 **4 处**（第 5、6、8 条） | 7 182 |

移植时**逐处核对**了「与旧项目绑定的东西」，其余一字未改：

| # | 位置 | 旧项目写法 | 本仓库改法 | 为什么必须改 |
|---|---|---|---|---|
| 1 | 文件头说明 | 常量式断言「本工程工作区键是 `--D-FPGA-oi--`」 | 改为说明键**由仓库根路径推导** | 硬编码工作区键 = 换仓库即失效 |
| 2 | `dsh_workspace_key()`（新增） | 无此函数 | 由 `REPO_ROOT` 推导（`D:\zynq-yolo` → `--D-zynq-yolo--`） | 承旧项目教训：硬编码 `d:/FPGA/oi/...` 使脚本不可移植 |
| 3 | `find_node()` | 只认两个写死的安装路径 | 追加 `%ProgramFiles%` / `%LOCALAPPDATA%` 回退，顺序仍是「环境变量 → PATH → 常见位置」 | 换机器/换 Node 安装方式仍能找到 node |
| 4 | `main()` | 只打印「数据源：<root>」 | 额外打印**仓库根 / 推导出的工作区键 / 会话文件 / 输出目录**，并把键与实际会话库目录**对照**（命中/未命中都给结论） | 让评审一眼看出脚本认的是哪个仓库，而不是凭印象 |
| 5 | `_code_fence()`（新增，**修 bug**） | 固定用 3 反引号围栏 | 按正文内最长反引号连串自动加长围栏（`export_transcript_dsh.py` / `prompts_index.py` 的读写两侧同步改） | **旧脚本会把「正文里自带 ``` 的消息」提前闭合**：实测一条 15 868 字符的团队消息被截成 1 802 字符，Markdown 看着还正常 —— 这直接破坏「逐字」，是移植后由 `verify_raw_match.py` 抓出来的 |
| 6 | 文件顶部（`import prompts_index` 之前） | 无 | 加 `import sys` + `sys.dont_write_bytecode = True`（三个 `.py` 都加） | **本仓库特有约束**：`check_repo.py` check d) 扫磁盘、`.gitignore` 挡不住，跑一次导出器就多出 `__pycache__/` 把自检打红（旧项目没有这条扫盘检查，故旧脚本不需要）。详见下节 |
| 7 | 三处写出点（`export_transcript_dsh.py` 2 处 + `prompts_index.py` 1 处） | `Path.write_text(md, encoding="utf-8")`（行尾随 `os.linesep` ⇒ Windows 写 CRLF） | 统一走 `write_text_lf()` → `open(..., newline="\n")`，**显式 LF** | `.gitattributes` 声明 `* text=auto eol=lf`，`git add` 会把 CRLF 规范化成 LF ⇒ 仓库里存档的副本与导出文件不再逐字节相等（实测差 **1 464 字节**），「逐字节」证据链在 clone 后当场失效。详见「行尾必须是 LF」一节 |
| 8 | 三处读取点（`prompts_index.py` 的 `rebuild_prompts_from_raw()` 与 `--check`，`verify_raw_match.py` 的分片读取） | `Path.read_text(encoding="utf-8")`（通用换行把 CRLF **翻译**成 LF） | 改 `read_text_lf()` / `_read_text_verbatim()`：按 `read_bytes()` 读，**见到 CR 直接报错要求重导出** | 通用换行会让「磁盘是 CRLF」这件事在比对里**完全不可见**（实测：某分片磁盘 35 个 CRLF，`read_text` 读出 CR 数 **0**）⇒ 行尾不合规却自证通过，是**假绿**。修 7 而不修 8 等于把警报器关掉 |

**`D:\FPGA\oi` 是只读的**：本次只复制，未修改/移动/删除其中任何文件。
**第 5、6、7、8 条是对旧脚本的实质改进**，不只是移植适配；若日后把改动回流旧项目，这四条应一并带回去。

---

## 依赖（**零第三方包**，红线：禁止 `pip install` / `npm install`）

| 依赖 | 版本（本机实测 2026-10-19） | 用途 | 为什么非要它 |
|---|---|---|---|
| Python | **3.13.1**（`D:\python\python.exe`）；**最低要求 3.8+** | 解析 JSONL、渲染 Markdown | 只用标准库。最低版本见下「Python 最低版本」 |
| Node.js | **v24.18.0**（`C:\Program Files\nodejs\node.exe`） | 解压 zstd | **Python 侧没有办法**：本机 `import zstandard` → `ModuleNotFoundError`；标准库 `compression.zstd` 是 3.14+ 才有，本机 `import compression.zstd` 也失败；而 Node 自带 `zlib.zstdDecompressSync`（实测 `typeof` = `function`） |

> ⇒ 两段式：**Node 解压 → Python 解析**。中间 JSONL 落在系统临时目录，用完即删（`shutil.rmtree`）。

### Python 最低版本：**3.8+**（以及为什么不用 `write_text(newline=)`）

行尾改 LF 时本可以写 `path.write_text(md, encoding="utf-8", newline="\n")` —— **没有采用**，理由：

| 候选写法 | 最低 Python | 结论 |
|---|---|---|
| `Path.write_text(md, encoding="utf-8", newline="\n")` | **3.10+**（`newline` 参数是 3.10 才加进 `write_text` 的） | ❌ 评审机器版本未知，会直接 `TypeError: write_text() got an unexpected keyword argument 'newline'` |
| `open(path, "w", encoding="utf-8", newline="\n")` | 2.7 / 3.x 全版本都有该参数 | ✅ **采用**，封装成 `write_text_lf()`（`prompts_index.py`）与 `read_text_lf()` |

**选型理由**：本仓库 `README.md:130` 声明的环境是「Python 3.8+（仅标准库即可跑 `tools/check_repo.py`）」。
为了一个只在写出端用到的便利参数，把整个工具链的门槛从 3.8 抬到 3.10，是拿**可移植性**换**一行代码的短**——不划算。
`open(..., newline="\n")` 语义与 `write_text(newline="\n")` 完全一致（都是「不做换行翻译」），没有损失。

**这条最低版本要求是怎么核的（如实说明核查深度）**：
- ✅ 三个脚本都用 `from __future__ import annotations`（`export_transcript_dsh.py:124`、`prompts_index.py:32`、`verify_raw_match.py:46`），
  故 `str | Path`、`List[str] | None` 这类**新式注解在低版本也不会被求值**；
- ✅ 用 `ast.parse(..., feature_version=3.7/3.8/3.9/3.10)` 四个档位解析三个文件：全部 OK（语法层面 3.7 起可解析）；
- ✅ 逐项排查过高版本才有的运行时 API：无 `:=`、无 `match`、无 `removeprefix/removesuffix`、无 `functools.cache`、
  无 `compression.zstd`、无内建泛型 `list[int]`（全部用 `typing.List` 等）；
- ⚠️ **没有在真实的 3.8 解释器上跑过**（本机只有 3.13.1，且禁止联网装第二个解释器）。
  上面是「静态排除」，不是「实测通过」。若评审机器是 3.8，请先跑一次 `--list` 确认无 ImportError。

> `newline="\n"` 只保证**换行符不被翻译**，**不会改写正文里已有的 CR**。若某条逐字正文自带 CRLF，
> 导出器把它原样写出并打印 `! 正文自带 N 个 CR（已原样保留，未改写）` ——
> **不静默改写正文**，因为改写正文才是真正破坏「逐字」的行为。
> 这种情况下 `verify_raw_match.py` 的行尾审计会报 `VERIFY_EOL_FAIL`（判据是「分片不得含 CR」），
> 由人来决定怎么办，而不是脚本替人决定。

## 失效条件（**什么时候这套脚本会给你错的结果**）

| # | 失效条件 | 现象 | 判据 / 处置 |
|---|---|---|---|
| 1 | **DSH 会话协议升级**（文件名不再是 `session.v4.jsonl.zstd`，或记录结构变了） | `--list` 列出 0 个会话，或导出分片里「人类提示词 0 条」但会话明明聊过 | 会话文件名集中在 `SESSION_FILE` / `session_file_name()` 一处，先改这里；记录结构变了要改 `parse_events()` |
| 2 | **会话库被清空或过期**（平台通常只保留有限天数） | `--list` 正常但历史分片对应的源文件已不存在 | **定期重跑导出**；`verify_raw_match.py` 会对找不到源的分片报 `SKIP`（**SKIP 不是 PASS**） |
| 3 | **会话正在进行**（导出瞬间仍被追加） | 分片文件头出现「⚠️ 读取提示：最后一帧未写完」 | 正常现象，`dsh_session_dump.mjs` 记为 `truncatedTail` 而不报错退出；稍后重跑即补齐 |
| 4 | **换了客户端**（不再用 DSH） | 新会话根本不在 `~/.dsh/sessions` 下 | `--list` 抓不到 ⇒ 必须照同样结构新增该客户端的导出器；**换客户端会静默断链**（原文停更而无人发觉） |
| 5 | **`prompts_index.py` 不在同目录** | 主导出器 `import` 失败，直接报错退出 | 三个文件必须同处 `ai/tools/`；`prompts_index.py` 不是可选的 |
| 6 | **把 `ai/raw/` 当可手改目录** | 分片被手工润色后，`verify_raw_match.py` 报 `VERIFY_RAW_FAIL` | 这正是脚本存在的意义：**手改必被发现**。需要更正时改整理层并注明「原文如此，此处更正」 |
| 7 | 在**非本仓库**位置运行（如把脚本单独拷到别处） | 仓库根被推导成错误路径，导出目录随之跑偏 | 仓库根 = 脚本 `ai/tools/` 往上三级；`--list` 会先打印推理出的「仓库根/工作区键/输出目录」，**先看这 4 行再信结果** |
| 8 | **`ai/raw/` 分片的行尾变成 CRLF**（编辑器另存 / 别的工具重写 / 旧版本脚本写过一次） | `verify_raw_match.py` 报 `VERIFY_EOL_FAIL`；且 `git add` 后仓库副本与磁盘不再逐字节相等 | 判据是「分片字节内不得含 CR」，见下节「行尾必须是 LF」。**处置：重跑导出器**（脚本已强制 LF），**不要**改 `.gitattributes` |

---

## 行尾必须是 LF（**否则「逐字节」在 clone 后就假了**）

### 冲突本身

本仓库 `.gitattributes:13` 声明了兜底规则：

```
* text=auto eol=lf
```

它的意思是：**git 入库时把文本文件的 CRLF 规范化成 LF**。而
`ai/README.md`、`ai/tools/README.md`、`ai/raw/README.md`、`ai/sessions/001_repo_skeleton.md`
都以「分片与被导出的原文**逐字节**一致」作为证据链主张 ——
**这两个设定互相冲突**：只要磁盘上是 CRLF，`git add` 就会把它改写成 LF，
于是「仓库里存档的那份」与「导出器当时写出的那份」**不再逐字节相等**，
而这个不等**没有任何东西会报错**（不会 FAIL、不会警告，`git status` 也是干净的）。

> 这不是推演，是实测（2026-10-19，修复前）：`ai/raw/` 下 10 个文件共 **8 594** 个 CRLF；
> 在临时仓库里 `git add -A` 后，**磁盘 139 083 B vs git index 137 619 B，差 1 464 B**
> （差额正好等于最大那片 `2026-10-19_8160c00c.md` 的 1 464 个 CRLF）。

### 根因

三处写出点都调 `Path.write_text(...)` 而**没给 `newline`**：

| 文件 | 原写法 |
|---|---|
| `ai/tools/export_transcript_dsh.py`（分片） | `dst.write_text(md, encoding="utf-8")` |
| `ai/tools/export_transcript_dsh.py`（汇总） | `(out_dir / "_prompts_all.md").write_text(md, encoding="utf-8")` |
| `ai/tools/prompts_index.py`（汇总） | `dst.write_text(md, encoding="utf-8")` |

`write_text` 的 `newline=None` ⇒ 写出时按 `os.linesep` 翻译 ⇒ **Windows 上就是 CRLF**。
脚本本身没写错什么，它只是默默继承了平台默认值 —— 而这个默认值与本仓库的 `.gitattributes` 相反。

### 修法（两侧都要改，只改写出端等于关掉警报器）

| 侧 | 改法 |
|---|---|
| **写出** | 统一走 `prompts_index.py` 的 `write_text_lf(path, text)`：`open(path, "w", encoding="utf-8", newline="\n")`，返回正文残留 CR 数（非 0 会打印告警但**原样保留**） |
| **读取** | 统一走 `read_text_lf()` / `_read_text_verbatim()`：**按 `read_bytes()` 读**，遇到 CR 直接报错要求重导出 |
| **自证** | `verify_raw_match.py` 新增独立判据「行尾审计」，在**比对之前**扫描每个 `*.md` 的原始字节 |

**为什么读取侧必须一起改（这是本次最容易被漏掉的点）**：
`Path.read_text()` 的默认 `newline=None` 会开**通用换行**，把磁盘上的 `\r\n` **翻译**成 `\n`。
所以只要读侧不修，「磁盘是 CRLF」在比对里**完全不可见** —— 两个实现（导出器与自证器）
各自做了一次翻译，翻译完一样 ⇒ 逐字比对照样绿。实测反证：某分片磁盘上有 **35** 个 CRLF，
经 `read_text` 读出来的 CR 数是 **0**，改用 `read_bytes().decode()` 才数到 **35**。

> ⚠️ 行尾审计**必须排在逐字比对之前**，否则症状会伪装：CRLF 文本里的 `\r` 会让那些以 `\n`
> 收尾的正则匹配不上，于是输出变成「**0 条可核对** / `VERIFY_RAW_EMPTY`」，
> 把一个明确的「行尾不合规」装成「没东西可比」。（实测踩到：首版就是这样报的。）

### 验收判据（两道，都要给，缺一不可）

**判据 A —— 磁盘行尾（可随时自查）**：

```powershell
D:\python\python.exe -c "import pathlib; raw=pathlib.Path(r'D:\zynq-yolo\ai\raw'); tot=0
for p in sorted(raw.glob('*.md')):
    b=p.read_bytes(); c=b.count(b'\r\n'); d=b.count(b'\r')-c; tot+=c+d
    print('%-30s bytes=%7d CRLF=%-4d 裸CR=%-3d %s'%(p.name,len(b),c,d,'OK' if not(c or d) else 'FAIL'))
print('CRLF+裸CR 合计 =', tot)"
#    期望：每个文件 CRLF=0 裸CR=0，末行「合计 = 0」
```

或直接看自证脚本打印的「行尾审计」段（它已内置同一判据，且**任一 CRLF 即判 `VERIFY_EOL_FAIL`**）。
判据 A 是必要条件，但**它自己证明不了 git 会不会改写** —— 所以还要判据 B。

**判据 B —— git 不再改写（核心，唯一有判别力的判据）**：

在**临时仓库**里比「磁盘字节」与「git index 字节」，两者必须**相等**：

```powershell
$tmp = Join-Path $env:TEMP ("zy_eol_" + [guid]::NewGuid().ToString('N').Substring(0,8))
New-Item -ItemType Directory -Path $tmp | Out-Null
robocopy D:\zynq-yolo $tmp /E /XD ref .research .git /NFL /NDL /NJH /NJS /NP | Out-Null
git -C $tmp init -q; git -C $tmp config core.autocrlf false; git -C $tmp add -A
foreach ($f in Get-ChildItem "$tmp\ai\raw" -File -Filter *.md) {
  $g = git -C $tmp cat-file -s (git -C $tmp rev-parse ":ai/raw/$($f.Name)")
  "{0,-30} disk={1,7} git={2,7}" -f $f.Name, $f.Length, $g
}
Remove-Item -LiteralPath $tmp -Recurse -Force   # 用完即删，别留在工作区
Test-Path $tmp                                  # 期望 False
```

**判据 B 有判别力吗？** —— 有，做过负对照（在 README 定稿后的最终文件上做的）：
把最大一片 `2026-10-19_8160c00c.md` 改回 CRLF 再 `git add`，
立刻从「`disk=268747  git=268747` 相等」变成「`disk=271793  git=268747  差值=3046`」。
也就是说这条判据**能失败**，不是那种「怎么改都绿」的橡皮图章。
（差 3046 B 而不只是 1464 B，因为这次是「把整份文件的 `\n` 全部换成 `\r\n`」，
不是「只在 CRLF 处换回」——两者都足以让判据翻红，判据不需要精确到具体字节数。）

**为什么不用「给 `ai/raw/` 开 `-text`」来消掉差异**：那是**掩盖**。证据文件本来就该是 LF
（它是脚本生成的、平台无关的文本），应当由写出端保证，而不是让 git 去猜、去豁免。
开 `-text` 会让 CRLF 原样入库，于是 Linux 上 checkout 出来是 CRLF 的「逐字原文」——
问题从「被悄悄改写」变成「被原样带到别的平台」，一个都没解决。

### 最低 Python 版本要求

`write_text_lf()` 用的是 `open(..., newline="\n")` 而非 `Path.write_text(newline="\n")`，
因此**不引入 `write_text` 的 3.10+ 门槛**，实际要求与全仓库一致：**Python 3.8+**。
选型理由与核查深度见上文「Python 最低版本」小节。

---

## 规则

- **`../raw/` 下的产物不得手改**（`../README.md` §1.1）。要更正就改整理层，并写明「原文如此，此处更正」。
- **不得编造**：导出器只做「记录 → Markdown」，不做补写；工具结果正文、附件二进制**如实声明为未导出**，绝不摘抄成"用户/模型说过的话"。
- **判定成功不要只看退出码**（承 `../../docs/repo_spec.md` §5）：看末行令牌 ——
  导出看 `完成：写出 N 个分片。`，自证看 **`VERIFY_RAW_OK`**。
- **`SKIP` 不是 `PASS`**：`verify_raw_match.py` 跳过某个分片只说明「分片里没有该类轮次」或「找不到源文件」，**不构成逐字一致的证据**。
- **比对必须覆盖全部四类入站发言**（人类提问 / 人类回答 / 委派提示词 / 团队消息）。只核人类提问会得到一个假的「全绿」：本仓库 8 个子 agent 分片里人类提问数全是 0，那些分片会全部落进 `SKIP`，看起来核对过了、其实一条没比。
- 命名硬约束（§1）：本目录文件名纯 ASCII、小写下划线；**文件正文可用中文**。
- 禁止把运行产物入库：`__pycache__/`、临时 `.jsonl`、临时 `.log`（`.gitignore` 已排除 `__pycache__/` 与 `*.pyc`）。
- 移植来的脚本**改了就要在此表登记**：新增「与旧项目绑定」的改动会导致下次移植冲突。

---

## 为什么脚本不生成 `__pycache__`（**别改成"跑完手动删"**）

**问题不是"脏文件"，是"每次正常使用都会让仓库自检变红"。**

本目录的 Python 入口会 `import` 同目录模块（`export_transcript_dsh.py` →
`from prompts_index import rebuild_prompts_from_raw`），Python 默认会在
`ai/tools/__pycache__/` 下写 `.pyc`。而 `../../tools/check_repo.py` 的
**check d)「无禁入文件」是扫磁盘、不看 `git`**：

| 手段 | 挡得住 `check d)` 吗 | 说明 |
|---|---|---|
| `.gitignore` 里写 `__pycache__/` | ❌ **挡不住** | 它只影响 git 层面是否跟踪；`check_repo.py` 直接遍历文件系统 |
| 跑完 `Remove-Item __pycache__` | ❌ 不稳 | 任何人**只要正常用一次脚本**就复发；靠"记得删"不是工程手段 |
| 放宽 `check_repo.py` 的 check d) | ❌ 掩盖 | 禁入项检查是防护栏，为了让自检变绿而放宽容错 = 拆掉警报器 |
| **`sys.dont_write_bytecode = True`** | ✅ 从源头消除 | 解释器**根本不写** `.pyc`；不依赖人的记性，也不改检查规则 |

所以三个入口脚本的最顶部（**早于任何本地模块 import**）都写成：

```python
import sys
sys.dont_write_bytecode = True

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prompts_index import rebuild_prompts_from_raw, write_text_lf   # ← 必须在这两行之后
```

**顺序是有意义的，别"整理 import"时往上挪**：`dont_write_bytecode` 只有在
**被 import 的模块编译之前**置位才生效。放在 `from prompts_index import ...` 之后就晚了 ——
`prompts_index.pyc` 照样落盘，看起来"加了没用"。

三层兜底：

| 位置 | 兜住哪条路径 |
|---|---|
| `export_transcript_dsh.py` | 主入口（含 `--list`）import `prompts_index` 之前置位 |
| `prompts_index.py` | 单独当 CLI 跑（`--check` / `--out`）的路径 |
| `verify_raw_match.py` | 本文件当前无本地 import，仍同约定 —— 防止日后加 import 时复发 |

**验收判据（可复现，别凭印象）**：
```powershell
Remove-Item -Recurse -Force D:\zynq-yolo\ai\tools\__pycache__ -ErrorAction SilentlyContinue
$env:PYTHONDONTWRITEBYTECODE = ""        # 必须清空！否则测的是环境变量，不是脚本自身
D:\python\python.exe D:\zynq-yolo\ai\tools\export_transcript_dsh.py --list
Test-Path D:\zynq-yolo\ai\tools\__pycache__    # 期望 False
```

> **`$env:PYTHONDONTWRITEBYTECODE` 必须先清空**：它是本条判据唯一的假绿来源 ——
> 若 shell 里残留该变量，即使脚本没写 `sys.dont_write_bytecode`，`Test-Path` 也会是 `False`。

**为什么不用 `python -X pycache_prefix=...` 或 `-B`**：那要求每个调用方都记得加参数，
且 `-B` 也只对当次进程有效；写进脚本才能保证「谁跑、怎么跑都不留」。
（`py_compile` / `python -m compileall` 本身就会写 `.pyc`，做语法检查请用
`python -c "compile(open(p,encoding='utf-8').read(), p, 'exec')"`。）

---

## 怎么用

```powershell
# 0) 前置：确认 node 与 python 在
node -v                                  # 期望 v24.x（自带 zlib.zstdDecompressSync）
D:\python\python.exe -V                  # 期望 3.13.x

# 1) 先列会话 —— 永远先跑这一步，别直接导出
D:\python\python.exe D:\zynq-yolo\ai\tools\export_transcript_dsh.py --list
#    期望先打印 4 行可核对的路径推导，其中：
#      仓库根：D:\zynq-yolo
#      工作区键（由仓库根推导）：--D-zynq-yolo-- —— 命中
#    然后列出 9 个会话（1 主 + 8 子 agent），带「本轮 / 非本轮」标记
#    ⚠️ 工作区键「未命中」不一定是错：过滤靠会话记录里的 cwd，键只用于排查

# 2) 真正导出（默认写 <仓库根>\ai\raw\，并重建 _prompts_all.md）
D:\python\python.exe D:\zynq-yolo\ai\tools\export_transcript_dsh.py
#    期望末行：完成：写出 9 个分片。

# 3) 只导出某一个会话（短 ID / 会话 ID / 目录名前缀均可）
D:\python\python.exe D:\zynq-yolo\ai\tools\export_transcript_dsh.py --session 8160

# 4) 逐字自证 + 行尾审计（**导出后必跑**）
D:\python\python.exe D:\zynq-yolo\ai\tools\verify_raw_match.py --show
#    期望末行：VERIFY_RAW_OK
#    ⚠️ 它先打印「行尾审计」段（每个 *.md 的 CRLF / 裸 CR 计数，必须全 0），再逐条比对。
#       行尾不合规 ⇒ 末行 VERIFY_EOL_FAIL（退出 1），处置是重跑第 2 步，不是改 .gitattributes。
#    带 --show 会打印比对内容的前 120 字符，便于人工复看

# 5) 只重建提示词汇总（不改分片）
D:\python\python.exe D:\zynq-yolo\ai\tools\prompts_index.py
D:\python\python.exe D:\zynq-yolo\ai\tools\prompts_index.py --check   # 只比对，不一致退出码 1

# 6) 提交前：仓库自检（不得因本目录新文件引入命名违规）
D:\python\python.exe D:\zynq-yolo\tools\check_repo.py
#    期望末行：CHECK_REPO_OK
```

常用开关（`export_transcript_dsh.py`）：

| 开关 | 作用 |
|---|---|
| `--list` | 只列会话，不写文件 |
| `--session <前缀>` | 只导出匹配的会话（短 ID / 会话 ID / 目录名） |
| `--all-projects` | 不按工作目录过滤（会导出**所有**工作区，慎用） |
| `--out <目录>` | 换输出目录 |
| `--sessions-dir <目录>` | 换会话库根（默认 `DSH_SESSIONS_DIR` > `DSH_HOME/sessions` > `~/.dsh/sessions`） |
| `--workspace-key <键>` | 显式指定工作区键（仅影响打印/排查，不影响过滤） |
| `--no-prompts` | 不重建 `_prompts_all.md` |

常用开关（`verify_raw_match.py`）：

| 开关 | 作用 |
|---|---|
| `--session <前缀>` | 只核对匹配的会话分片 |
| `--kind <类别>` | 只核某一类：`prompt` / `answer` / `delegation` / `relay` |
| `--show` | 打印比对内容的逐字预览（前 120 字符） |
| `--raw <目录>` | 换 raw 目录 |

## 实测结果（**时点值** = 2026-10-19 11:09–11:10，行尾修复后的最后一次完整验证）

> ⚠️ **下表数字是时点快照，不是恒定值**：主会话仍在进行，每次重跑导出后条数/字节数都会变。
> 引用这些数字时必须带「时点」二字，别当成仓库常量。

| 项 | 结果（时点值，2026-10-19 11:09–11:10） |
|---|---|
| 导出 | 9 个分片 + `_prompts_all.md`，末行 `完成：写出 9 个分片。` |
| 逐字自证 | **28 条入站发言逐字节相等 / 0 条不一致 / 0 个分片跳过**，末行 `VERIFY_RAW_OK` |
| 分类计数 | 人类提问 4、人类回答 1、委派提示词 8、团队消息 15 |
| **行尾（新增判据）** | 11 个 `*.md` 全部 `CRLF=0 裸CR=0`，合计 **0**；`verify_raw_match.py` 末行 `VERIFY_RAW_OK`（含行尾审计段） |
| **git 不再改写（核心）** | 临时仓库 `git add -A` 后 **磁盘 1 139 194 B == git index 1 139 194 B，差值 0，11/11 文件 EQUAL**；负对照（把最大一片改回 CRLF）从「disk=268747 git=268747 相等」变成 `disk=271793 差值=3046` ⇒ 判据有判别力 |
| 汇总一致性 | `prompts_index.py --check` → `_prompts_all.md 与 9 个分片一致（5 条提示词，跨 1 天）` |
| 仓库自检 | `check_repo.py` → `SUMMARY: 5 PASS / 0 FAIL / 0 WARN` / `CHECK_REPO_OK` |
| **无 `__pycache__` 污染** | 跑完 `--list` / 导出 / 自证后 `Test-Path ai\tools\__pycache__` → **False**（`$env:PYTHONDONTWRITEBYTECODE` 已清空，非环境变量假绿；全树 `__pycache__`/`*.pyc` 扫描无输出） |

> **这 28 条比「4 条人类提示词」多得多，是本目录刻意要的**：子 agent 的委派提示词和团队消息同样是
> 「人/父 agent 写下的原话」，一旦被截断或被当成用户发言，`report/llm_logs/` 的引用就会失真。
> **条数会随会话推进增长**（主会话还在跑）；每次重跑导出后 `verify` 的条数变化属正常，
> 若出现「条数不一致」那是分片落后于原文，重跑导出即可 —— **不要靠手改分片消掉它**。
> （本次实测就撞到一次：导出后收到新的 task 消息，原文变 15 条团队消息而分片是 14 条，
> 重跑导出后立即恢复 0 条不一致。**这是正常产物刷新，不是缺陷**。）

---

## 与三层体系的关系

```
DSH 会话库（~/.dsh/sessions/--<工作区键>--/*/session.v4.jsonl.zstd）
        │  dsh_session_dump.mjs（Node，按 zstd 帧解压）
        │  export_transcript_dsh.py（Python，分类渲染）
        ▼
   ai/raw/*.md          ← 原始层：逐字，不可手改（本目录脚本生成）
        │  verify_raw_match.py  ── 逐字节回比会话原文（本目录自证）
        ▼
   ai/sessions/*.md     ← 整理层：按「一件事」整理，含自我纠错轨迹
```

> **`report/llm_logs/` 里的每一条结论都必须能指回整理层，整理层再指回 `ai/raw/` 的原文位置。**
> 本目录的脚本是这条链**唯一可重复的那一环**：链断了，`raw/` 就是无根记录。

---

## 事故一：`--all` 被 argparse 静默当成 `--all-projects`（**范围越界**）

**现象**：`ai/raw/` 从 9 个分片涨到 **63 个分片 / 59 个唯一会话 id**，
而本工作区 `--D-zynq-yolo--` 下**只有 19 个会话**。
多出来的是 `--D-FPGA-oi--`（旧项目 26 个会话）、`--D-weixin-bot--`（1）、
`--C-Users-L-Documents-deepseek-harness-default-workspace--`（7）、
`--D-~7535~79D13~73ED--`（14）等**别的项目、别的用途**的会话**全文**。
当时它们还是 untracked —— 差一步就会被 `git add -A` 提交进**公开仓库**。

**根因**：`argparse` 默认允许**选项前缀缩写**。`export_transcript_dsh.py` 里只有
`--all-projects`（含义是"不按工作目录过滤"），于是 `--all` 被唯一前缀匹配成它。
最小复现（已实测）：

```python
import argparse
p = argparse.ArgumentParser()
p.add_argument('--all-projects', action='store_true')
p.add_argument('--out', default=None)
print(p.parse_args(['--all']).all_projects)   # -> True，且没有任何警告
```

**为什么危险**：`--all` 是**看起来完全合理**的写法，写的人以为在说"全都导出"，
实际语义是"跨工作区都导出"。错的方向恰好是**扩大范围**，而范围一大就静默、不报错。

**修法**：三个脚本的 `ArgumentParser(...)` 全部加 **`allow_abbrev=False`**。
关掉之后拼错/写短的选项会**报错退出**，而不是变成另一个选项。
（`export_transcript_dsh.py`、`prompts_index.py`、`verify_raw_match.py` 均已加。）

**验收判据（可复现）**：导出后
`(Get-ChildItem ai/raw/2026-*.md | ForEach-Object { ($_.BaseName -split '_')[-1] } | Sort-Object -Unique).Count`
必须等于 `--D-zynq-yolo--` 下的会话目录数；且 `ai/raw/` 里 grep 不到 `D:\FPGA\oi`。
**只清 `_prompts_all.md` 是不够的**：越界的分片文件必须删掉，然后再跑
`prompts_index.py --out ai/raw` 重建汇总（否则汇总仍是从脏集合算出来的）。

## 事故二：行尾判据把**围栏内的逐字 CRLF** 也判成缺陷（**导出器卡死在自己产物上**）

**现象**：`python ai/tools/export_transcript_dsh.py --all`（当时还没加 `allow_abbrev=False`）
在**写完所有分片之后**抛错：

```
RuntimeError: 2026-10-19_8160c00c.md 含 CR（12 个）—— 分片必须是 LF。请重跑 …
```

**根因**：写出侧与读回侧**口径不一致**。

| 侧 | 文件 | 口径 |
|---|---|---|
| 写 | `export_transcript_dsh.py` | 已放宽：`write_text_lf()` 返回 CR 数，**只打印提示**，「逐字原文含 CRLF，已原样保留，未改写」 |
| 读 | `prompts_index.py: read_text_lf()` | 仍旧严格：`if b"\r" in b: raise` |

而 `--all` 的最后一步是 `rebuild_prompts_from_raw()`，它**读回刚写的分片** ⇒ 必失败。
于是导出器**永远卡在自己的产物上**。

**那 12 个 CR 是什么**：用户在对话里**粘贴的 CRLF 的 CSV**（12 条判罚裁定）。
它在分片里的位置是**围栏代码块内部**，是**逐字原文**。把它改写成 LF，
就等于破坏原始层唯一的承诺：「逐字节等于原文」。
实测确认：`2026-10-19_8160c00c.md` CR=12 / CRLF=12，全部在 `incident_id,decision,…` 那个围栏里。

**修法**：把判据从「文件里有没有 CR」改成「**围栏之外**有没有 CR」。
工具自己生成的每一行（标题、`<sub>` 时间戳、`---`、围栏标记行）必须是 LF；
围栏内部是别人给的字节，原样保留。三处实现口径必须一致：

- `prompts_index.py: cr_outside_fences()` / `read_text_lf()`
- `verify_raw_match.py: _eol_counts()` / `audit_line_endings()` / `_read_text_verbatim()`
- `export_transcript_dsh.py` 的写出侧（原样保留 + 提示）

`verify_raw_match.py` 的输出现在分两栏：`结构行CRLF` / `结构行裸CR`（判据，必须全 0）
与 `围栏内CRLF`（仅提示，不算缺陷）。

**验收判据**：`VERIFY_RAW_OK`，且汇总里「结构行不合规 = 0」。
当前实测：`行尾汇总：21 个文件，0 个结构行不合规` +
`另有…围栏内共 24 个 CRLF —— 那是用户粘贴的 CRLF 原文，按逐字存档口径原样保留，不算缺陷`。

**教训（可迁移）**：一条判据放宽了，**必须同时检查它的对偶**——
这里"写"与"读"就是一对。只放宽一侧，症状会伪装成"另一侧的 bug"
（报错指向 `prompts_index.py`，而根因是两侧口径分叉）。
