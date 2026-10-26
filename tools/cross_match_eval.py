"""跨比赛可迁移性评估：把「颜色/门控」的结论从一场球搬到另一场球，看还站不站得住。

问题从哪来
----------
`docs/hi_layer_color_gate.md` 的四层占比（purity 37.1% / color 44.4% /
too_small 14.7% / rect 3.8%）和「按取色失败原因门控」的结论，**全部来自
`migu_5106947698` 这一场比赛**（红/白队服、固定转播机位、640x360 切片）。
换一场球：队服颜色、机位、光照、画质、分辨率全都可能变。

那这些数字**换场还剩多少**？在此之前没人知道 —— 因为没有任何一个工具能
「把同一套量测原样跑到另一场比赛上，并给出与基线的并排对照 + 判据」。
本文件就是那个工具，且**判据先能失败**：漂移超限就报 WARN，样本不足就 FAIL。

输入输出契约（新比赛片段怎么喂进来）
-----------------------------------
**输入（人给的三样东西）**
  1. `--video-dir`  新比赛视频所在目录。两种布局都支持：
       (a) 目录里一个（或几个）大 mp4 —— 用 `--n-clips` + `--clip-len` 在时间轴上均匀切片；
       (b) 目录里一堆小 mp4（事件切片）—— 直接取前 `--n-clips` 个。
     递归扫描，`.mp4/.mkv/.avi/.mov/.ts/.flv/.webm` 都认。
  2. `--palette`  **双方队服颜色，必须由人输入**（对应 D013 的 LCD 触屏录入方案）。
     默认 `red,white`，但**默认值只对旧比赛成立**，新比赛务必显式给。
     可选值 = `classify_players.PALETTE_REF` 的键：red / orange / yellow / green /
     cyan / blue / purple / magenta / white / black。
     ★ 颜色不写死是硬要求：2026-10-20 的两次「全判 white」静默 bug 正是
       色板/量纲搞错造成的（`docs/color_prior_v1.md` §1）。
  3. 可选 `--model / --imgsz / --stride / --clip-len / --n-clips`（默认值与旧基线一致：
     yolov8n / imgsz 640 / stride 8）。

**内部量测（与基线严格同一口径）**
  每个采样帧跑一次 hi 层检测（`classes=[0], conf=HI_CONF, iou=0.45, imgsz`），
  每个框交给 `color_prior._classify_box(frame, bb, palette, why)`（**复用，不重写**），
  得到 `colored` 或四种取色失败原因之一（`color_prior.REASON_*`）。
  ⇒ 与 `tools/hi_color_diag.py` 逐框逐帧等价，所以两边的占比可以并排比。

**输出**
  `data/results/cross_match.json`（**不覆盖** `hi_color_diag.json`）：
  每段 / 每种布局（native、scaled-640）的 reason count 与占比、并与
  `hi_color_diag.json` 的 `reason_share_pct` 并排；外加判据结论。

★ 为什么要跑两种布局（scale-robustness 探针）
---------------------------------------------------------------------------
旧基线是在 **640x360 切片**上跑的。新比赛的原视频很可能是 1280x720 / 1920x1080，
直接喂进去时「框高」这个物理量会整体翻倍或翻三倍 ⇒ `too_small` 占比塌掉、
`purity` 占比改成由「构图密度」决定。**这会把「机位/画面尺度变了」和
「队服颜色变了」两件事混在一起**，而本评估的目的恰恰是把它们分开。
所以同时跑一遍 **缩放到 640 宽** 的重检路径（`_measure_scaled(out_width=640)`），
给判据一个共同的观测尺度；两种布局的 n_hi 与 h 中位并排写进输出：

- 两者接近 ⇒ 画面尺度没差，原生漂移可以放心读；
- 两者差得远 ⇒ 转换主流是**原生路径**（清掉了尺度项），但「原生路径还残留的差」
  才是机位/构图/队色真实造成的差。

判据（先能失败；数字都在代码顶部常量里，改判据就是改常量）
----------------------------------------------------------
| # | 判据 | 阈值 | 不过时 |
|---|---|---|---|
| G1 | 样本量 | `n_hi >= MIN_N_HI`（2000） | **FAIL** `n_hi_too_small <n>` |
| G2 | 单层占比绝对漂移 | `|p_new - p_base| < MAX_ABS_DRIFT_PT`（15 pt） | WARN |
| G3 | 单层占比相对漂移 | `|p_new - p_base| / p_base < MAX_REL_DRIFT`（50%） | WARN（**主导判据**） |
| G4 | 「取色失败率」整体漂移 | `|unknown_rate_new - 0.206| < MAX_UNKNOWN_RATE_DRIFT_PT`（10 pt） | WARN |
| G5 | 门控映射是否还有效 | 反推 `too_small` 层非球员率 θ <= `MAX_TOO_SMALL_THETA`（0.80；旧场 0.129） | WARN `gate_mapping_likely_broken`（**致命**） |
| — | 占比全糊 | `max(rel) < DEGENERATE_REL`（10%） | WARN `degenerate_shares` |

- `max_drift` = max(G3)；有**任何** WARN ⇒ `CROSS_MATCH_OK ... WARN`，只有全绿才是干净 OK。
- WARN 不进退出码（承 `docs/repo_spec.md` §5：以末行令牌为准，不以退出码判成功）；
  只有 FAIL 返回非 0。
- 所有基线都从 `data/results/hi_color_diag.json` **现读**，不手抄 —— 基线一改，
  判据自动跟着改；文件缺失直接 FAIL `baseline_missing`（不允许拿硬编码数字凑）。

用法
----
    # 自检：没有新视频时，用现有 12 段的前 2 段跑一遍，证明管线通（默认 --self-check）
    & D:\\python\\python.exe tools/cross_match_eval.py --self-check

    # 新比赛（大连英博 vs 深圳新鹏城，队色未知 ⇒ 必须人给 palette）
    & D:\\python\\python.exe tools/cross_match_eval.py `
        --video-dir D:\\yt-dlp\\downloads\\migu_968460165 --palette blue,orange

# 整场录像（2h39，混着广告 / 演播室 / 看台特写）：只在**比赛段**内抽窗口
    & D:\\python\\python.exe tools\\cross_match_eval.py `
        --video-dir "<全场 mp4>" --palette blue,white --n-clips 12 `
        --play-windows --max-total-s 0

    # ★ --play-windows 为什么存在：整场转播里草坪占比≈0 的画面（广告 / 演播室 /
    #   看台特写）本来就没有「球员框」，却会被均匀切片算进取色失败原因的**构成**里，
    #   于是漂移分不清是「队色变了」还是「画面内容变了」。判据是草坪像素占比
    #   （默认 20 s 粗扫一档，>=0.25 判为比赛画面，连续 >=120 s 才算一段）。
    #   它是**采样级**判据、不是逐帧真值：JSON 里如实带 step_s / 命中率 / 区间表。

    # 按场校准参考色（先跑 tools/kit_calib.py 得到实测色度，再喂回来做 A/B）
    & D:\\python\\python.exe tools\\kit_calib.py --video "<全场 mp4>" --palette white,blue
    & D:\\python\\python.exe tools\\cross_match_eval.py `
        --video-dir "<全场 mp4>" --palette white,blue `
        --palette-refs data\\results\\kit_calib.json `
        --n-clips 12 --play-windows --max-total-s 0 `
        --out data\\results\\cross_match_calibrated.json
    # ★ --palette-refs **只替换参考色**，不动 MAX_COLOR_DIST / MIN_MARGIN。
    #   否则「校准」和「放宽阈值」两个变量混在一起，A/B 说不清是哪一项起了作用。

产物
----
    data/results/cross_match.json

判据令牌
----
    CROSS_MATCH_OK <n_clips> <n_hi> <max_drift>          # 默认输出；末尾可能追加 WARN(码;码)
    CROSS_MATCH_FAIL <reason>                            # 契约/可读性/样本不足/定位不到视频
    SELF_CHECK_OK pipeline n_clips=.. n_hi=.. max_drift=..   # --self-check 模式下的一行证据（OK 令牌照旧）

★ 自检模式与跨比赛模式**判据不同**，别混读：
  - 跨比赛模式：G1–G5 全部生效（G1 样本不足 ⇒ FAIL；其余 ⇒ WARN）。
  - 自检模式：只证明「管线通」。同一场比赛对自己的基线做漂移检验**没有判别力**
    （测出来的只是抽样噪声），所以自检**不设**占比漂移闸门，`max_drift` 只作为
    「管线确实算出了这个数」的见证打印出来。

诚实边界
--------
- 本工具**没有真值**：它量的是「取色失败原因的构成」，不是「球员判得准不准」。
  层间相对高低仍可读（9 倍量级），但「精确率提升 X%」这类话**跨场不能直接搬**。
- 队名 -> 队服的映射需要人输入（`--palette`），本工具不猜。猜错的后果不是崩溃，
  而是**静默的 `color` 层暴涨** —— 这正是 G2/G3 要抓的东西。
- 锚点以**比赛**为单位，不按样本层层加权：同一场比赛里抽出的事件片段比随机 20s
  窗口更「忙」（人更多、更贴镜头），这是已知的抽样偏差。
"""

from __future__ import annotations

import sys

# ★ 不生成 __pycache__：tools/check_repo.py 的 check d) 是**扫盘**的（不看 .gitignore），
#   留下 .pyc 直接 FAIL。置位必须在所有本地模块 import 之前（见 check_repo.py 顶部注释
#   与 docs 里记的那次坑：模块自身体内再置位拦不住自己的 .pyc）。
sys.dont_write_bytecode = True

import argparse
import importlib.util
import json
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools"
RESULTS = REPO / "data" / "results"
BASELINE_JSON = RESULTS / "hi_color_diag.json"
OUT = RESULTS / "cross_match.json"            # ★ 独立文件，不覆盖 hi_color_diag.json
MODEL_DEFAULT = REPO / "data" / "models" / "yolov8n.pt"
SELF_CHECK_DIR = REPO / "data" / "samples" / "incidents"

VIDEO_SUFFIXES = (".mp4", ".mkv", ".avi", ".mov", ".ts", ".flv", ".webm")

# ---------------------------------------------------------------- 判据阈值（真源）
# ★ 判据必须先能失败：全部在这里，改判据=改常量，改常量要在 docs 里同步改并说明为什么。
MIN_N_HI = 2000            # G1 最少 hi 框数。旧基线 12 段是 12 585，约 1/6 就够比占比
MAX_ABS_DRIFT_PT = 15.0    # G2 单层占比绝对漂移上限（百分点）
MAX_REL_DRIFT = 0.50       # G3 单层占比相对漂移上限（50%），任务书要求的「主导判据」
MAX_UNKNOWN_RATE_DRIFT_PT = 10.0   # G4 取色失败率整体漂移上限（百分点）
MAX_TOO_SMALL_THETA = 0.80        # G5 反推的 too_small 层非球员率上限（旧场 12.9%，见 hi_layer_color_gate §6.1）
# G6 草坪伪改进：躯干主体是草的框里，被判成 A/B 的比例上限。
# 为什么是 0.10：草坪绿(OpenCV H≈44-50)到队色窗口的水平距离 ≥ MAX_HUE_DEG_V2(50°) 时
# 只会落在窗口边缘，落进窗口内又不亮也不低饱和的草像素占比很低；实测真草坪圆中位色
# 在 v1/v2 下都是 0/4 判成队色。留 10% 余量给"半草半人"的框边界效应。
MAX_GRASS_ASSIGN_RATE = 0.10
DEGENERATE_REL = 0.10      # 所有层相对漂移都 < 10% ⇒ 这个对照没有判别力
SELF_CHECK_MIN_CLIPS = 2   # 自检至少要跑通两段

# 「比赛窗口」检测（--play-windows）：整场录像里含广告、演播室、看台/回放特写，
# 均匀切片会把这些也算进「取色失败原因构成」，那是画面内容变了、不是队色变了。
# 判据是**草坪像素占比**：足球比赛远景/中景草坪占比很高，广告与演播室≈0。
PLAY_STEP_S = 20.0         # 粗扫步长（2h39 ⇒ 约 477 个采样点）
PLAY_GRASS_MIN = 0.25      # 采样帧草坪占比下限
PLAY_MIN_INTERVAL_S = 120.0  # 只保留 >=2min 的连续段（短于此的多是回放/特写切换）

# 逐框计数口径（顺序固定，输出表按这个顺序排）
REASON_KEYS = ("purity", "color", "too_small", "rect")
COLORED = "colored"

# 缩放到 640 宽再量一遍（与旧基线同一观测尺度），见文件头「scale-robustness 探针」
NATIVE_OUT_WIDTH = 640


def _load(path, name):
    """importlib 加载本地模块（不用 sys.path.insert，避免写 __pycache__）。"""
    sys.dont_write_bytecode = True         # ★ 被 importlib 加载的模块也要在加载**前**置位
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cp = _load(REPO / "tools" / "color_prior.py", "_cm_color_prior")
# CP = 取色口径（classify / _dist_metric / _limit / _margin / CLASSIFY_METRIC）。
# ★ 必须与 palette_probe 用**同一个**模块实例的判断口径；色相颜色由 color_prior
#   re-export（classify 仍是 classify_players 里那一份）。
CP = _load(REPO / "tools" / "classify_players.py", "_cm_classify")

# ---------------------------------------------------------------- 契约校验

CONTRACT_DOC = [
    "输入 1: --video-dir 新比赛视频目录（大 mp4 或一堆小 mp4 都行）",
    "输入 2: --palette 双方队服颜色，人工录入（D013 的 LCD 方案；默认 red,white 只对旧比赛成立）",
    "输入 3: 可选 --model/--imgsz/--stride/--clip-len/--n-clips（默认与旧基线一致）",
    "输出:   data/results/cross_match.json（含并排对照与判据结论，不覆盖 hi_color_diag.json）",
]


def check_contract():
    """启动即自检：复用到的 color_prior 契约是否齐全。返回错误字符串或 None。

    为什么值得单独查：颜色链路上出过两次「不崩溃、只是静默全判 white」的 bug
    （docs/color_prior_v1.md §1）。缺 _classify_box / 原因码错位都属于同一类
    —— 所以宁可在这里 early fail，也不要跑完 40 秒给一份没意义的占比。
    """
    need_fn = ("_classify_box", "color_of_name", "classify", "torso_rect", "torso_color")
    need_const = ("REASON_TOO_SMALL", "REASON_PURITY", "REASON_RECT", "REASON_COLOR",
                  "MIN_H_PX", "MIN_PURITY", "MAX_COLOR_DIST", "MIN_MARGIN",
                  "HI_CONF", "PALETTE_REF")
    missing = [n for n in need_fn if not callable(getattr(cp, n, None))]
    missing += [n for n in need_const if not hasattr(cp, n)]
    if missing:
        return "contract_break color_prior.py 缺: " + ",".join(missing)
    codes = (cp.REASON_TOO_SMALL, cp.REASON_PURITY, cp.REASON_RECT, cp.REASON_COLOR)
    if sorted(codes) != sorted(REASON_KEYS):
        return "contract_break reason_codes_mismatch %s" % (codes,)
    if len(set(codes)) != 4:
        return "contract_break reason_codes_not_distinct"
    return None


def parse_palette(text):
    """'red,white' -> {'A': 'red', 'B': 'white'}。校验色名存在（防二次解析类 bug）。"""
    names = [s.strip().lower() for s in (text or "").split(",") if s.strip()]
    if len(names) != 2:
        return None, "palette_needs_2 (got %d: %s)" % (len(names), names)
    bad = [n for n in names if n not in cp.PALETTE_REF]
    if bad:
        return None, "bad_palette %s (choices: %s)" % (
            ",".join(bad), ",".join(sorted(cp.PALETTE_REF)))
    if names[0] == names[1]:
        return None, "palette_duplicate '%s'" % names[0]
    return {"A": names[0], "B": names[1]}, None


def load_measured_refs(path):
    """读「按场校准」的实测参考色，返回 ({颜色名: 结构}, err)。

    接受的输入（顺序：先看 `measured_refs` 段，再退回把整个文件当映射）：
      1. `tools/kit_calib.py` 的产物 `data/results/kit_calib.json`
         → 取 `measured_refs`（每项 {"kind": "achromatic"|"hue", "s","v","h01"}）；
      2. 直接给映射 `{"white": {"kind": "achromatic", "s":…, "v":…}, …}`。

    ★ 只替换**参考色**，不动任何阈值（MAX_COLOR_DIST / MIN_MARGIN 原样）——
      否则「校准」会变成「顺手放宽判据」，两个变量混在一起就说不清是哪一项起了作用。
    """
    import json as _json
    p = Path(path)
    if not p.exists():
        return None, "palette_refs_missing %s" % p
    try:
        raw = _json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:                       # noqa: BLE001 - 解析失败一律降级为 FAIL
        return None, "palette_refs_unreadable %s: %s" % (p.name, exc)
    refs = raw.get("measured_refs") if isinstance(raw, dict) and "measured_refs" in raw else raw
    if not isinstance(refs, dict) or not refs:
        return None, "palette_refs_empty %s" % p.name
    out = {}
    for name, item in refs.items():
        if not isinstance(item, dict) or "s" not in item or "v" not in item:
            return None, "palette_refs_bad_entry %s=%r" % (name, item)
        out[name] = item
    return {"source": str(p), "refs": out}, None


def load_palette_probe():
    """加载开跑前的色板可分辨性闸门（tools/palette_probe.py）。

    注意：这个模块内部会 importlib 加载 tools/color_prior.py —— 那是**第二次**
    加载同一个文件。sys.dont_write_bytecode 已在文件最顶部设过，importlib 复用时
    不会再生成 __pycache__；这里再设一次是防御性的（踩过这个坑）。
    """
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("_palette_probe",
                                                  str(TOOLS / "palette_probe.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_baseline(path=BASELINE_JSON, palette=None):
    """读旧比赛基线。占比**现读**，不手抄；返回 (baseline_dict, err)。

    若新跑的 palette 与基线不一致，把基线里的颜色写进 note —— 因为占比漂移
    有一部分可以归因到「队色换了」，这个信息判读时必须有。
    """
    if not path.exists():
        return None, "baseline_missing %s" % path
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:                       # noqa: BLE001 - 任何解析失败都降级为 FAIL
        return None, "baseline_unreadable %s: %s" % (path.name, exc)
    share = raw.get("reason_share_pct") or {}
    miss = [k for k in REASON_KEYS if k not in share]
    if miss:
        return None, "baseline_incomplete 缺 reason_share_pct: %s" % ",".join(miss)
    base = {
        "path": str(path.relative_to(REPO)) if path.is_relative_to(REPO) else str(path),
        "generated": raw.get("generated"),
        "n_clips": raw.get("n_clips"),
        "n_hi": raw.get("n_hi"),
        "n_unknown": raw.get("n_unknown"),
        "unknown_rate": raw.get("unknown_rate"),
        "reason_share_pct": {k: float(share[k]) for k in REASON_KEYS},
        "palette": list((raw.get("params") or {}).get("palette") or []),
        "source_match": "migu_5106947698",
    }
    tot = sum(base["reason_share_pct"].values())
    if abs(tot - 100.0) > 0.5:
        return None, "baseline_share_not_100 %.2f" % tot
    if palette is not None and base["palette"] and palette != base["palette"]:
        base["note"] = ("baseline palette %s != 本次 %s：占比漂移里有一部分可直接归因于队服换了"
                        % (base["palette"], palette))
    return base, None


# ---------------------------------------------------------------- 输入发现 / 切片

def discover_videos(root):
    """递归找出所有视频文件（排除 _scratch 之类的中间目录不做特殊处理，全部计入）。"""
    root = Path(root)
    if root.is_file():
        return [root]
    out = [p for p in root.rglob("*")
           if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES]
    return sorted(out, key=lambda p: str(p).lower())


def probe_video(path):
    """返回 (w, h, fps, n_frames)。打不开返回 None（不抛）。"""
    import cv2
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            return None
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    finally:
        cap.release()
    if w <= 0 or h <= 0:
        return None
    if fps <= 0:
        fps = 25.0
    return w, h, fps, max(0, n)


def plan_clips(video_dir, n_clips, clip_len, max_total_s=0):
    """把「一个/几个大 mp4」或「一堆小 mp4」统一成一份切片计划。

    返回 (plan, layout, err)。plan 元素：
        {"video": Path, "start_s": float, "dur_s": float, "label": str}
    ★ 处置规则（写进输出版本，避免"换场比赛脚本行为变了"这种暗坑）：
      - 整目录总时长 <= n_clips * clip_len  ⇒ single_pass：每段视频从头整段跑一遍
        （事件切片天然就是 ~20-30s，人为每 20s 再切一刀会把锚点事件切丢一半）；
      - 否则 ⇒ windowed：每段视频在时间轴上均匀取 n_clips/n_videos 个窗口。
    """
    vids = discover_videos(video_dir)
    if not vids:
        return [], None, "no_videos %s" % video_dir
    metas = []
    for v in vids:
        pr = probe_video(v)
        if pr is None:
            print("  [skip] 打不开: %s" % v, flush=True)
            continue
        w, h, fps, n = pr
        dur = (n / fps) if n else 0.0
        metas.append({"path": v, "w": w, "h": h, "fps": fps, "n_frames": n, "dur_s": dur})
    if not metas:
        return [], None, ("no_decodable_video 目录里 %d 个视频都解不出帧（下载未完成 / 占位文件？）"
                          "：%s" % (len(vids), "; ".join(v.name for v in vids)))
    total = sum(m["dur_s"] for m in metas if m["dur_s"] > 0)
    if max_total_s and total > max_total_s:        # 防手滑：只打算跑两段却扫到整个下载目录
        # 正常情况（切片或下载器已产出）：文件名里有切片信息 ⇒ 目录整体超长就报错停下，
        # 免得"以为跑的是新比赛、其实跑的是整个下载目录里别的东西"。
        # 下载器中间态（现场目击：dir 里一个 2.6GB 的正式名 mp4 + 一个 .ytdl 状态文件）⇒ 只跳过。
        if any(v.with_name(v.name + ".ytdl").exists() for v in vids):
            print("  [skip] 总时长 %.0fs > --max-total-s %.0fs，目录里有下载中间态(.ytdl)，"
                  "逐个跳过超长文件：%s"
                  % (total, max_total_s,
                     "; ".join("%s(%.0fs)" % (m["path"].name, m["dur_s"]) for m in metas)),
                  flush=True)
            metas = [m for m in metas if m["dur_s"] <= max_total_s * 0.5]
            if not metas:
                return [], None, ("download_in_progress 目录里只有超长文件（%.0fs），"
                                  "等下载完成后再跑" % total)
            total = sum(m["dur_s"] for m in metas if m["dur_s"] > 0)
        else:
            return [], None, ("unexpected_video_set %d 个视频共 %.0fs > --max-total-s %.0fs"
                              "（若这确实是新比赛的全场录像，加大 --max-total-s）"
                              % (len(metas), total, max_total_s))

    layout = "single_pass" if total <= n_clips * clip_len else "windowed"
    plan = []
    if layout == "single_pass":
        for m in metas:
            plan.append({"video": m["path"], "start_s": 0.0,
                         "dur_s": min(m["dur_s"], 1e9) if m["dur_s"] else 0.0,
                         "label": m["path"].stem})
    else:
        per = max(1, int(round(n_clips / float(len(metas)))))
        for m in metas:
            dur = m["dur_s"]
            usable = max(0.0, dur - clip_len)
            for i in range(per):
                start = 0.0 if per == 1 else usable * i / float(per - 1)
                plan.append({"video": m["path"], "start_s": round(start, 2),
                             "dur_s": min(clip_len, dur),
                             "label": "%s@%.1fs" % (m["path"].stem, start)})
    plan = plan[:n_clips] if n_clips > 0 else plan
    for i, p in enumerate(plan, 1):
        p["clip_id"] = i
    return plan, layout, None


# ------------------------------------------------------- 比赛窗口（--play-windows）

def _grass_frac(frame):
    """草坪像素占比。判据只用**占比**，不用颜色分类，避免引入队色依赖。"""
    import cv2
    import numpy as np
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = ((hsv[:, :, 0] >= 35) & (hsv[:, :, 0] <= 85)
            & (hsv[:, :, 1] >= 60) & (hsv[:, :, 2] >= 40))
    return float(np.count_nonzero(mask)) / float(mask.size)


def scan_play_windows(video, step_s=PLAY_STEP_S, grass_min=PLAY_GRASS_MIN,
                      min_interval_s=PLAY_MIN_INTERVAL_S):
    """粗扫整场录像，返回「比赛段」与扫描统计。

    为什么需要它：`plan_clips` 在时间轴上**均匀**取窗口，而整场转播里混着广告
    （实测 7453 s 那帧草坪占比 0.00）、演播室、看台/回放特写。这些画面里
    「球员框」本来就少或根本没有，却会被算进取色失败原因的**构成**里 ⇒ 量到的
    漂移分不清是「队色变了」还是「画面内容变了」。

    返回 {"samples": [(t, frac)], "intervals": [(a, b)], "n_samples": n,
          "n_play": n_play, "total_play_s": s, "step_s":..., "grass_min":...}
    ★ 这是**采样级**判据（20 s 一档），不是逐帧真值：窗内仍可能有广告插播，
      所以结果里要如实带上 step_s 与命中率，别当成"比赛时间精确到秒"。
    """
    import cv2
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        return None
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        dur = (n / fps) if n else 0.0
        samples = []
        t = 0.0
        while t < dur:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ok, frame = cap.read()
            if not ok:
                break
            samples.append((round(t, 1), round(_grass_frac(frame), 4)))
            t += step_s
    finally:
        cap.release()
    if not samples:
        return None
    # 连续「比赛」采样点 → 区间（采样点覆盖 [t, t+step)）
    intervals = []
    run_start = None
    for i, (tt, frac) in enumerate(samples):
        playing = frac >= grass_min
        if playing and run_start is None:
            run_start = tt
        elif not playing and run_start is not None:
            intervals.append((run_start, tt))
            run_start = None
    if run_start is not None:
        intervals.append((run_start, samples[-1][0] + step_s))
    intervals = [(a, b) for a, b in intervals if (b - a) >= min_interval_s]
    return {"samples": samples, "intervals": intervals, "n_samples": len(samples),
            "n_play": sum(1 for _t, f in samples if f >= grass_min),
            "total_play_s": round(sum(b - a for a, b in intervals), 1),
            "step_s": step_s, "grass_min": grass_min,
            "min_interval_s": min_interval_s, "video_s": round(dur, 1)}


def plan_clips_in_windows(windows, video, n_clips, clip_len):
    """把 n_clips 段窗口按**比赛段长度**分配进去（长段多分，短段至少 1 段）。

    与 `plan_clips` 的区别：不给「整场均匀」，而是只在比赛段内部均匀 —— 这是
    「同一场比赛、同一套量测，只换画面内容」这个对照能成立的前提。
    """
    usable = [(a, b) for a, b in (windows or {}).get("intervals", [])
              if (b - a) >= clip_len]
    if not usable:
        return [], "play_windows_too_short"
    total = sum(b - a for a, b in usable)
    alloc = [[a, b, max(1, int(round(n_clips * (b - a) / total)))] for a, b in usable]
    while sum(x[2] for x in alloc) > n_clips:
        i = max(range(len(alloc)), key=lambda j: alloc[j][2])
        if alloc[i][2] <= 1:
            break
        alloc[i][2] -= 1
    while sum(x[2] for x in alloc) < n_clips:
        i = max(range(len(alloc)), key=lambda j: alloc[j][1] - alloc[j][0])
        alloc[i][2] += 1
    plan = []
    for a, b, k in alloc:
        span = (b - a) - clip_len
        for i in range(k):
            start = a if k == 1 else a + span * i / float(k - 1)
            plan.append({"video": Path(video), "start_s": round(start, 2),
                         "dur_s": float(clip_len),
                         "label": "play%04.0f@%05.1fs" % (start, start)})
    plan.sort(key=lambda p: p["start_s"])
    for i, p in enumerate(plan, 1):
        p["clip_id"] = i
        p["label"] = "play%02d@%05.1fs" % (i, p["start_s"])
    return plan[:n_clips], None


# ---------------------------------------------------------------- 量测

def measure(model, plan_item, palette, args):
    """跑一个切片：hi 层检测 + 取色失败原因分层。返回该片段的结果 dict。

    ★ 与 tools/hi_color_diag.py 的循环**逐框等价**（同一 stride、同一 conf、同一
      _classify_box 调用）；唯一不同的是这里把框高/框位留下来，用来解释漂移机制。
    """
    return _measure_scaled(plan_item, palette, model, args, out_width=None)


def scale_frames(plan_item, palette, model, args):
    """原生路径：把每个采样帧缩到 640 宽后**重新检测并重新取色**。

    为什么不是「把框坐标除以 scale」：检测器在不同尺度上会给出**不同的框集合**，
    而基线本来就是「640 宽输入 + 640 宽像素上的框高」，只有重新检测才是同一口径。
    """
    return _measure_scaled(plan_item, palette, model, args, out_width=NATIVE_OUT_WIDTH)


def _measure_scaled(plan_item, palette, model, args, out_width=None):
    """量测内核。out_width=None 表示用原始分辨率（原生布局）。"""
    import cv2
    import numpy as np

    cap = cv2.VideoCapture(str(plan_item["video"]))
    if not cap.isOpened():
        return None
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
    f0 = int(round(plan_item["start_s"] * fps))
    f1 = f0 + int(round(plan_item["dur_s"] * fps)) if plan_item["dur_s"] else 1 << 30
    if f0 > 0:
        cap.set(cv2.CAP_PROP_POS_FRAMES, f0)

    stride = max(1, int(args.stride))
    reasons = {k: 0 for k in REASON_KEYS}
    reasons[COLORED] = 0
    n_hi = 0
    h_px = []
    src_size = None
    started = time.time()

    grass_boxes = 0      # 躯干主体判为草的框数
    grass_assigned = 0   # 其中被判成 A/B 的（伪改进的直接证据）
    idx = f0 - 1
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        idx += 1
        if idx >= f1:
            break
        if idx % stride:
            continue
        if frame is None or frame.size == 0:
            continue
        h0, w0 = frame.shape[:2]
        if src_size is None:
            src_size = (w0, h0)
        if out_width and w0 != out_width:
            nh = int(round(h0 * out_width / float(w0)))
            frame = cv2.resize(frame, (out_width, nh), interpolation=cv2.INTER_AREA)
        r = model.predict(frame, classes=[0], conf=cp.HI_CONF, iou=0.45,
                          imgsz=args.imgsz, verbose=False)
        boxes = r[0].boxes if r and r[0].boxes is not None else None
        if boxes is None or len(boxes) == 0:
            continue
        for bb in boxes.xyxy.cpu().numpy():
            n_hi += 1
            h_px.append(float(bb[3] - bb[1]))
            why = []
            role, _d, _p, _col = cp._classify_box(frame, bb, palette, why)
            # ★ 「草坪框被判成 A/B」统计（2026-10-20）：只看 unknown_rate 会掩盖伪改进 ——
            #   放宽色相窗口能把 unknown 换成一堆"假蓝"。所以每框再问一次
            #   「这个框的躯干主体是不是草」，若**是草又被判成某队**单独计数。
            if _col is not None and CP.grass_like(_col):
                grass_boxes += 1
                if role in ("A", "B"):
                    grass_assigned += 1
            if role in ("A", "B"):
                reasons[COLORED] += 1
            else:
                reasons[why[0] if why else cp.REASON_COLOR] += 1
    cap.release()

    n_unk = n_hi - reasons[COLORED]
    hh = np.array(h_px, dtype=float) if h_px else np.zeros(0)
    return {
        "clip_id": plan_item["clip_id"],
        "label": plan_item["label"],
        "video": Path(plan_item["video"]).name,
        "start_s": round(plan_item["start_s"], 2),
        "dur_s": round(plan_item["dur_s"], 2),
        "src_size": list(src_size) if src_size else None,
        "n_hi": n_hi,
        "n_unknown": n_unk,
        "unknown_rate": round(n_unk / n_hi, 4) if n_hi else None,
        "reason_counts": dict(reasons),
        "reason_share_pct": {k: (round(100.0 * reasons[k] / n_unk, 2) if n_unk else None)
                             for k in REASON_KEYS},
        "hi_h_px": {
            "median": round(float(np.percentile(hh, 50)), 1) if hh.size else None,
            "p10": round(float(np.percentile(hh, 10)), 1) if hh.size else None,
            "p90": round(float(np.percentile(hh, 90)), 1) if hh.size else None,
        },
        "grass_boxes": grass_boxes,
        "grass_assigned": grass_assigned,
        "elapsed_s": round(time.time() - started, 1),
        "landscape": classify_landscape(hh),
    }


def classify_landscape(h_arr):
    """用框高分布描述「这次抽到的画面长什么样」，给漂移一个机制解释。

    只做**粗分档**（看 `hi_h_px` 的三分位与中位数），不下"是不是球员"的结论：
      no_usable_tier —— p10 与 p90 都在 100 px 以上，画面里**基本没有 "usable" 那一档**
                        （近景/长焦/特写），颜色链路在本片段上的输入本身就换了
      mixed   —— 有小有大，跨度超过一个数量级
      on_par  —— 中位数落在 20-70 px，与旧比赛（unknown 中位数 37 px）同一量级
      wide    —— 中位数 70-160 px，整体比旧比赛大一档（很可能没缩放，直接喂原分辨率）
      tiny    —— 中位数 < 20 px，远机位/低码率
    """
    if h_arr is None or len(h_arr) == 0:
        return "empty"
    p10 = float(np_percentile(h_arr, 10))
    p90 = float(np_percentile(h_arr, 90))
    med = float(np_percentile(h_arr, 50))
    if p10 >= 100:
        return "no_usable_tier"     # p10/p90 都在 100px 以上 ⇒ 画面里根本没有 "usable" 那一档
    if med < 20:
        return "tiny"
    if p90 / max(1.0, p10) >= 10:
        return "mixed"
    if med <= 70:
        return "on_par"
    if med <= 160:
        return "wide"
    return "very_wide"


def np_percentile(a, q):
    import numpy as np
    return np.percentile(a, q)


def aggregate(items, key="reason_counts"):
    """把若干片段的结果累加成一个总表。"""
    counts = {k: 0 for k in REASON_KEYS + (COLORED,)}
    n_hi = 0
    h_px = []
    for it in items:
        if it is None:
            continue
        n_hi += it["n_hi"]
        if it.get("hi_h_px", {}).get("median") is not None:
            h_px.append(it["hi_h_px"]["median"])
        for k in counts:
            counts[k] += int(it[key].get(k, 0))
    n_unk = n_hi - counts[COLORED]
    # ★ 草坪伪改进：v2 放宽色相窗口后，检测器在草地上给出的 person 框会被赋个队色，
    #   unknown_rate 因此"变好"。所以每次汇总都带这两个数，判据 G6 用它。
    grass_boxes = sum(int(it.get("grass_boxes") or 0) for it in items if it)
    grass_assigned = sum(int(it.get("grass_assigned") or 0) for it in items if it)
    return {
        "n_clips": len([it for it in items if it]),
        "n_hi": n_hi,
        "n_unknown": n_unk,
        "unknown_rate": round(n_unk / n_hi, 4) if n_hi else None,
        "grass_boxes": grass_boxes,
        "grass_assigned": grass_assigned,
        "grass_assigned_rate": (round(grass_assigned / float(grass_boxes), 4)
                                if grass_boxes else None),
        "reason_counts": counts,
        "reason_share_pct": {k: (round(100.0 * counts[k] / n_unk, 2) if n_unk else None)
                             for k in REASON_KEYS},
        "hi_h_px": {"median": round(float(sorted(h_px)[len(h_px) // 2]), 1) if h_px else None},
    }


# ---------------------------------------------------------------- 判据

def judge(new_tbl, baseline):
    """对照基线给判据。返回 (verdict_dict, reasons, warns)。

    ★ 判据先能失败：样本不足直接 FAIL；占比漂移超限报 WARN。
    """
    reasons, warns = [], []
    n_hi = int(new_tbl.get("n_hi") or 0)
    share = new_tbl.get("reason_share_pct") or {}
    base = baseline["reason_share_pct"]

    gate = evaluate_gate(share, base)

    # G1 样本量：算占比的门槛（没到门槛就不判漂移，但并排对照与门控检验照样给出）
    if n_hi < MIN_N_HI:
        reasons.append("n_hi_too_small %d<%d" % (n_hi, MIN_N_HI))
        return ({"verdict": "FAIL", "gate_fail": "G1", "max_drift": None,
                 "max_abs_drift_pt": None, "n_hi": n_hi,
                 "table": who_table(share, base), "gate_check": gate,
                 "grass_false_assign": {"grass_boxes": new_tbl.get("grass_boxes") or 0,
                                        "grass_assigned": new_tbl.get("grass_assigned") or 0,
                                        "grass_assigned_rate": None,
                                        "status": "SKIP"}},
                reasons, warns)

    rows = who_table(share, base)
    rel_all = [r["drift"] / r["base_pct"] for r in rows.values() if r["base_pct"] > 0]
    max_rel = round(max(rel_all), 4) if rel_all else None
    max_abs = round(max(abs(r["drift"]) for r in rows.values()), 2)

    # G2 / G3 单层占比漂移
    for k, r in rows.items():
        if r["base_pct"] <= 0:
            continue
        if abs(r["drift"]) >= MAX_ABS_DRIFT_PT:
            warns.append("G2 abs_drift_%s %+.1fpt>=%.0f" % (k, r["drift"], MAX_ABS_DRIFT_PT))
        if abs(r["drift"]) / r["base_pct"] >= MAX_REL_DRIFT:
            warns.append("G3 rel_drift_%s %+.0f%%>=%.0f%%"
                         % (k, 100.0 * r["drift"] / r["base_pct"], 100.0 * MAX_REL_DRIFT))

    # G4 整体取色失败率
    ur = new_tbl.get("unknown_rate")
    base_ur = baseline.get("unknown_rate")
    if ur is not None and base_ur is not None:
        dr = 100.0 * (ur - base_ur)
        if abs(dr) >= MAX_UNKNOWN_RATE_DRIFT_PT:
            warns.append("G4 unknown_rate_drift %+.1fpt>=%.0f" % (dr, MAX_UNKNOWN_RATE_DRIFT_PT))

    # G5 门控映射是否还有效（这是唯一"致命"的 WARN：结论本身失效，不只是数字漂移）
    if gate.get("status") == "broken":
        warns.append("G5 gate_mapping_likely_broken implied_too_small_theta=%.2f>%.2f"
                     % (gate.get("implied_too_small_theta") or 0.0, MAX_TOO_SMALL_THETA))

    # G6 伪改进：色相窗口放宽后，"unknown 变少"可能来自**草/看台框被赋队色**。
    #    ★ 为什么必须有这一条（lead 2026-10-20 明确要求）：v1→v2 的 unknown_rate 从
    #    0.477 掉到约 0.2，如果不同时看"草坪框被判成 A/B 的数量"，报告会报喜不报忧。
    #    实测新比赛一帧 854x480 里，检测器在草地上给的 person 框有 7-8 个，真球员 4-10 个
    #    ⇒ 全部赋错色时能把 unknown 压掉一小半。
    gb = new_tbl.get("grass_boxes") or 0
    ga = new_tbl.get("grass_assigned") or 0
    grass = {"grass_boxes": gb, "grass_assigned": ga,
             "grass_assigned_rate": (round(ga / float(gb), 4) if gb else None),
             "status": "PASS"}
    if gb:
        if ga / float(gb) > MAX_GRASS_ASSIGN_RATE:
            grass["status"] = "WARN"
            warns.append("G6 grass_false_assign %d/%d=%.1f%%>%.0f%%"
                         % (ga, gb, 100.0 * ga / gb, 100.0 * MAX_GRASS_ASSIGN_RATE))
    else:
        grass["status"] = "SKIP"          # 没采到草坪框（全特写/室内）⇒ 不判

    # 退化检查：漂移全都小到没有判别力
    if max_rel is not None and max_rel < DEGENERATE_REL:
        warns.append("degenerate_shares max_rel_drift=%.1f%%<%.0f%%"
                     % (100.0 * max_rel, 100.0 * DEGENERATE_REL))

    return ({"verdict": "FAIL" if reasons else ("WARN" if warns else "OK"),
             "max_drift": max_rel, "max_abs_drift_pt": max_abs,
             "n_hi": n_hi, "table": rows, "gate_check": gate,
             "grass_false_assign": grass},
            reasons, warns)


def who_table(new_share, base_share):
    """并排对照表：每层 new / base / drift（百分点）/ rel_drift。"""
    out = {}
    for k in REASON_KEYS:
        n = new_share.get(k)
        b = base_share.get(k)
        if n is None or b is None:
            continue
        d = round(float(n) - float(b), 2)
        out[k] = {
            "new_pct": round(float(n), 2),
            "base_pct": round(float(b), 2),
            "drift": d,                                # 百分点
            "rel_drift": (round(d / b, 4) if b else None),
            "status": ("PASS" if (abs(d) < MAX_ABS_DRIFT_PT
                                  and (not b or abs(d) / b < MAX_REL_DRIFT))
                       else "WARN"),
        }
    return out


# hi_layer_color_gate.md §6.1 的第二批标注结果（真源是 data/annot/labels2.csv，
# 由 tools/hi_unknown_analysis.py 复算）。这里只嵌**点估计**，不做统计推断。
LABELS2_NONPLAYER_RATE = {"purity": 0.974, "rect": 0.676, "color": 0.436, "too_small": 0.129}
# 层内非球员率 θ_k 的**新占比加权**版本，用来做「门控映射是否失效」的检验。
# 先用 {purity, rect, color} 三层的 θ 推出新比赛的「每框非球员率」λ≈0.335，
# 再反推 too_small 的真实 θ = 0.647/0.172 ≈ 3.8（>1 就是不可能 ⇒ 映射失效）。
REF_LAYERS = ("purity", "rect", "color")
MAX_TOO_SMALL_THETA = 0.80   # 旧场 0.129；新场推出来 > 0.80 即判「too_small 不再是球员」「方向反了」


def evaluate_gate(new_share, base_share=None):
    """门控结论「取色失败原因 ⇒ 非球员」在新比赛里还成不成立？

    做法：新比赛的 unknown 框按四层占比重分配，用第二批标注的层内非球员率 θ_k
    （purity .974 / rect .676 / color .436 / too_small .129）算两种值：

      1. est_nonplayer_rate  = Σ share_k × θ_k        —— 新比赛 unknown 池的整体非球员率
      2. implied_too_small_theta = (est − Σ_{Ref} share_k θ_k) / share_too_small
                                                        —— 用新占比反推的 too_small 层 θ

    判据 H_FIXED：implied_too_small_theta > 0.80 ⇒ 门控方向反了（旧结论「too_small
    基本是球员、必须保留」在新比赛不成立），WARN gate_mapping_likely_broken。

    ★ 诚实边界：θ_k 是从旧比赛标注搬过来的，θ 本身能不能跨场搬是本次要问的问题之一。
      所以本检验只抓**明显失效**（θ 被顶到不可能的值），不报精度，也不能当精度用。
    """
    def layers(share):
        return {k: (float(share.get(k) or 0.0)) for k in REASON_KEYS}

    new_l = layers(new_share)
    base_l = layers(base_share) if base_share else None

    def est(l):
        tot = sum(l.values())
        if tot <= 0:
            return None
        return sum(l[k] / 100.0 * LABELS2_NONPLAYER_RATE[k] for k in REASON_KEYS)

    new_rate = est(new_l)
    base_rate = est(base_l) if base_l else None
    out = {"basis": "docs/hi_layer_color_gate.md §6.1 层内非球员率（点估计）",
           "layer_nonplayer_rate": LABELS2_NONPLAYER_RATE,
           "thresholds": {"max_too_small_theta": MAX_TOO_SMALL_THETA,
                          "ref_layers": list(REF_LAYERS)},
           "new_est_nonplayer_rate": (round(new_rate, 4) if new_rate is not None else None),
           "base_est_nonplayer_rate": (round(base_rate, 4) if base_rate is not None else None)}

    ts = new_l.get("too_small", 0.0)
    if ts <= 0.5:
        out["status"] = "new_layer_empty"
        out["note"] = "新比赛里 too_small 层几乎为空，门控无从检验"
        return out
    if new_rate is None or base_rate is None:
        out["status"] = None
        out["note"] = "占比缺失，无法检验"
        return out
    fixed = sum(new_l[k] / 100.0 * LABELS2_NONPLAYER_RATE[k] for k in REF_LAYERS)
    implied = (new_rate - fixed) / (ts / 100.0)
    out["too_small_pct"] = round(ts, 2)
    out["implied_too_small_theta"] = round(implied, 4)
    out["shift_pt"] = round(100.0 * (new_rate - base_rate), 2)
    if implied > MAX_TOO_SMALL_THETA:
        out["status"] = "broken"
        out["note"] = ("反推 too_small 层非球员率 %.2f > %.2f：旧结论「too_small 基本是球员」"
                       "在新比赛不成立，门控方向需重订" % (implied, MAX_TOO_SMALL_THETA))
    else:
        out["status"] = "consistent"
        out["note"] = ("反推 too_small 层非球员率 %.2f ≤ %.2f，与旧场 0.129 的定性一致"
                       % (implied, MAX_TOO_SMALL_THETA))
    return out


def clamp_token(val, fmt="%.2f"):
    """令牌里不许出现 null：缺失统一写成 0，并在 JSON 里保留 null 原样。"""
    if val is None:
        return "0"
    return fmt % val


# ---------------------------------------------------------------- 主流程

def main(argv=None):
    ap = argparse.ArgumentParser(
        description="跨比赛可迁移性评估（颜色/门控结论换场还剩多少）")
    ap.add_argument("--video-dir", default="",
                    help="新比赛视频目录（大 mp4 或一堆小 mp4 均可）；留空 = 自检模式")
    ap.add_argument("--palette", default="red,white",
                    help="双方队服颜色（人工录入，D013 LCD 方案）。默认只对旧比赛成立")
    ap.add_argument("--palette-refs", default="",
                    help="按场校准：给 tools/kit_calib.py 的 kit_calib.json"
                         "（或 {颜色名:{kind,s,v[,h01]}} 映射），只替换参考色、不动阈值")
    ap.add_argument("--color-metric", default=None, choices=["v1", "v2"],
                    help="取色距离口径：v1（默认，旧比赛结论逐字节不变）/ "
                         "v2（跨场：色相角度配额 + 亮而不饱和守卫 + 草坪守卫，"
                         "见 tools/classify_players.py 的 distance_to_ref_v2）")
    ap.add_argument("--self-check", action="store_true",
                    help="用 data/samples/incidents 的前 2 段自检（无 --video-dir 时默认开启）")
    ap.add_argument("--n-clips", type=int, default=2,
                    help="抽几段（旧基线用的 12）；自检固定 %d" % SELF_CHECK_MIN_CLIPS)
    ap.add_argument("--clip-len", type=float, default=20.0, help="每段时长（秒）")
    ap.add_argument("--stride", type=int, default=8, help="每 N 帧取 1 帧（与基线一致）")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--model", default=str(MODEL_DEFAULT))
    ap.add_argument("--native", dest="native", action="store_true", default=True,
                    help="同时跑「缩放到 640 宽再量一遍」的原生路径（默认开）")
    ap.add_argument("--no-native", dest="native", action="store_false")
    ap.add_argument("--allow-indistinct-palette", action="store_true",
                    help="color palette gate fails -> still run (recorded in JSON)")
    ap.add_argument("--max-total-s", type=float, default=1800.0,
                    help="输入视频总时长上限（防手滑扫到整个下载目录，0=不限）")
    ap.add_argument("--play-windows", action="store_true",
                    help="整场录像模式：先按草坪占比检出比赛段，只在比赛段内抽窗口"
                         "（广告/演播室/看台特写不算；--n-clips 为总窗口数）")
    ap.add_argument("--play-step", type=float, default=PLAY_STEP_S,
                    help="比赛窗口粗扫步长（秒，默认 %.0f）" % PLAY_STEP_S)
    ap.add_argument("--play-grass-min", type=float, default=PLAY_GRASS_MIN,
                    help="判「比赛画面」的草坪像素占比下限（默认 %.2f）" % PLAY_GRASS_MIN)
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)

    t0 = time.time()
    out_path = Path(args.out)
    # ★ 自检结果**不写** canonical 输出：原来 --self-check 会覆盖 cross_match.json，
    #   把「新比赛的真结果」冲成「同一场比赛跟自己对基线」（2026-10-20 实际踩到）。
    #   自检是管线证据，跨场比赛结果才是结论证据，两者必须分文件。
    if (args.self_check or not args.video_dir) and out_path == Path(OUT):
        out_path = RESULTS / "cross_match_selfcheck.json"

    # ---- 契约自检（early fail，别等跑完 40 秒再发现口径不对）----
    err = check_contract()
    if err:
        print("CROSS_MATCH_FAIL %s" % err)
        return 2

    palette, err = parse_palette(args.palette)
    if err:
        print("CROSS_MATCH_FAIL %s" % err)
        return 2

    # ---- 按场校准（可选）：只换参考色，不动阈值 ----
    # 为什么要它：PALETTE_REF 是按旧场素材标定的常数；新场深蓝球衣实测 S=0.298/V=0.435
    # 而预置 blue 是 S=0.70/V=0.70，差 0.40 的饱和度会在 0.16 闸门里直接吃掉一半预算。
    refs_info, palette_measure = None, palette
    if args.palette_refs:
        refs_info, err = load_measured_refs(args.palette_refs)
        if err:
            print("CROSS_MATCH_FAIL %s" % err)
            return 2
        missing = [n for n in (palette["A"], palette["B"]) if n not in refs_info["refs"]]
        if missing:
            print("CROSS_MATCH_FAIL palette_refs_missing_colour %s（文件里有 %s）"
                  % (",".join(missing), ",".join(sorted(refs_info["refs"]))))
            return 2
        palette_measure = {role: refs_info["refs"][name] for role, name in palette.items()}
        refs_info["applied"] = {"A": palette["A"], "B": palette["B"]}
        refs_info["note"] = ("按场校准：只替换参考色，MAX_COLOR_DIST / MIN_MARGIN 未改动")
        print("  按场校准参考色来自 %s（%s）"
              % (refs_info["source"],
                 "; ".join("%s=%s" % (r, json.dumps(refs_info["refs"][n], ensure_ascii=False))
                           for r, n in palette.items())))

    baseline, err = load_baseline(BASELINE_JSON, [palette["A"], palette["B"]])
    if err:
        print("CROSS_MATCH_FAIL %s" % err)
        return 2

    # ---- 取色口径：v1（默认）/ v2 ----
    # ★ 为什么要做成**可切换**而不是直接改默认：v1 是旧比赛全部结论（低置信层精确率
    #   95.7%、hi_color_diag 的四层占比）赖以成立的口径。改默认 = 旧结论失去基线。
    #   所以两套并存，报告里两套数字都出（见 docs/cross_match_eval.md §12）。
    if args.color_metric:
        # ★ 必须改**量测路径实际使用的**那一份 classify_players 实例。
        #   直接写 `CP.CLASSIFY_METRIC = ...` 只改到本文件自己手里那份（importlib 按
        #   路径加载 ⇒ 多份实例），量测仍走 color_prior 的那份 v1 —— 实测后果是
        #   JSON 记着 v2、四层占比与 unknown 却与 v1 逐位相同（静默假绿）。
        cp_metric = cp.set_classify_metric(args.color_metric)
        CP.CLASSIFY_METRIC = cp_metric        # 让探针与本文件读同一个口径
        if cp.classify_metric() != args.color_metric:
            print("CROSS_MATCH_FAIL color_metric_not_applied %s!=%s"
                  % (cp.classify_metric(), args.color_metric))
            return 2
    print("  取色口径 CLASSIFY_METRIC=%s（limit=%.3f margin=%.3f）"
          % (cp.classify_metric(), CP._limit(), CP._margin()))

    # ---- 色板可分辨性闸门：色板先能分辨，再谈迁移 ----
    # 理由见 tools/palette_probe.py 文档串：classify() 缺"跨过 MAX_COLOR_DIST 的像素
    # 绝不判 A/B"这条约束，误判是静默的（实测白队 97% 像素判成蓝色、零报错）。
    # ★ 2026-10-20 补上**漏判方向**：只查误判时，深蓝球衣在预置 blue 下整队判
    #   unknown（dist 0.146-0.180 vs 门限 0.16）探针仍报 PASS —— 判据只能判出绿。
    probe = None
    try:
        pp = load_palette_probe()
        probe = pp.evaluate(palette, cp.classify_metric())
    except Exception as exc:                       # noqa: BLE001 - 探针坏了不该拖垮主流程
        probe = {"ok": False, "problems": ["palette_probe_unavailable %s" % exc]}
    for _name, _v in (probe.get("detect") or {}).items():
        print("  probe detect %-18s expect=%-3s -> %-8s dist=%.3f %s"
              % (_name, _v["expect"], _v["role"], _v["dist"], _v["status"]))
    if not probe["ok"] and not args.allow_indistinct_palette:
        print("CROSS_MATCH_FAIL %s" % probe["problems"][0])
        return 2
    if not probe["ok"]:
        print("  [warn] 色板可分辨性闸门未过，--allow-indistinct-palette 显式放行：%s"
              % probe["problems"][0])

    self_check = args.self_check or not args.video_dir
    video_dir = Path(args.video_dir) if args.video_dir else SELF_CHECK_DIR
    # 自检固定跑前 2 段（任务书要求）；--n-clips 只在跨比赛模式下生效，避免"说 3 段跑 2 段"
    n_clips = SELF_CHECK_MIN_CLIPS if self_check else args.n_clips
    if not video_dir.exists():
        print("CROSS_MATCH_FAIL video_dir_missing %s" % video_dir)
        return 2
    if not Path(args.model).exists():
        print("CROSS_MATCH_FAIL model_missing %s" % args.model)
        return 2

    print("跨比赛可迁移性评估  mode=%s  dir=%s  palette=%s"
          % ("self_check" if self_check else "cross_match", video_dir, palette))
    for line in CONTRACT_DOC:
        print("  contract | %s" % line)

    plan, layout, windows = [], None, None
    if args.play_windows and not self_check:
        vids = discover_videos(video_dir)
        if len(vids) != 1:
            print("CROSS_MATCH_FAIL play_windows_needs_single_video %d" % len(vids))
            return 2
        print("  -- 比赛窗口粗扫（step=%.0fs grass>=%.2f）--" % (args.play_step,
                                                                args.play_grass_min))
        windows = scan_play_windows(vids[0], step_s=args.play_step,
                                   grass_min=args.play_grass_min)
        if windows is None:
            print("CROSS_MATCH_FAIL play_windows_scan_failed %s" % vids[0])
            return 2
        print("     采样 %d 点 / %d 点判为比赛画面 / 比赛段 %d 段 / 合计 %.0fs（视频 %.0fs）"
              % (windows["n_samples"], windows["n_play"], len(windows["intervals"]),
                 windows["total_play_s"], windows["video_s"]))
        plan, err = plan_clips_in_windows(windows, vids[0], n_clips, args.clip_len)
        if err:
            print("CROSS_MATCH_FAIL %s" % err)
            return 2
        layout = "play_windows"
    else:
        plan, layout, err = plan_clips(video_dir, n_clips, args.clip_len, args.max_total_s)
        if err:
            print("CROSS_MATCH_FAIL %s" % err)
            return 2
    print("  layout=%s  n_plan=%d  n_clips=%d" % (layout, len(plan), n_clips))

    from ultralytics import YOLO
    model = YOLO(args.model)

    measured = []
    for i, item in enumerate(plan, 1):
        r = measure(model, item, palette_measure, args)
        if r is None:
            print("  [%d/%d] %s 打不开，跳过" % (i, len(plan), item["label"]))
            continue
        measured.append(r)
        print("  [%d/%d] %-34s n_hi=%-5d unk=%-5d share=%s h=%s"
              % (i, len(plan), item["label"], r["n_hi"], r["n_unknown"],
                 json.dumps(r["reason_share_pct"], ensure_ascii=False),
                 r["hi_h_px"]["median"]), flush=True)
    if not measured:
        print("CROSS_MATCH_FAIL no_measurable_clip")
        return 2

    scaled = []
    if args.native:
        print("  -- 原生路径（缩放到 %d 宽后重新检测/取色）--" % NATIVE_OUT_WIDTH)
        for i, item in enumerate(plan, 1):
            r = scale_frames(item, palette_measure, model, args)
            if r is None:
                continue
            scaled.append(r)
            print("     [%d/%d] %-34s n_hi=%-5d unk=%-5d share=%s h=%s"
                  % (i, len(plan), item["label"], r["n_hi"], r["n_unknown"],
                     json.dumps(r["reason_share_pct"], ensure_ascii=False),
                     r["hi_h_px"]["median"]), flush=True)

    tbl_windowed = aggregate(measured)          # 直接喂本片 → 唯一有判别力的布局
    tbl_scaled = aggregate(scaled) if scaled else None   # 缩到 640 宽重测 → 旧基线的口径

    verdict_native, reasons, warns = judge(tbl_windowed, baseline)
    verdict_scaled, _r_s, warns_s = (None, [], [])
    if tbl_scaled and (self_check or tbl_scaled["n_hi"] >= MIN_N_HI):
        verdict_scaled, _r_s, warns_s = judge(tbl_scaled, baseline)
        verdict_scaled["label"] = "scaled_640"

    if self_check:
        # 自检：跨场闸门一律不算（同场对基线没有判别力），只查管线通不通，并打印见证数字
        pipe = cross_match_self_check({
            "clips": measured, "totals_native": tbl_windowed,
            "totals_scaled_640": tbl_scaled, "verdict_native": verdict_native})
        payload = assemble(args, palette, baseline, layout, self_check, measured, probe,
                           tbl_windowed, tbl_scaled, verdict_native, verdict_scaled,
                           [], pipe, t0, out_path, windows=windows, refs=refs_info)
        _write(out_path, payload)
        print("RESULT_JSON %s" % out_path)
        if isinstance(pipe, list):
            print("CROSS_MATCH_FAIL %s" % pipe[0])
            return 2
        print_self_check_log((verdict_native, verdict_scaled, layout))
        print("SELF_CHECK_OK %s" % pipe)
        md = max_drift_of(verdict_native, verdict_scaled)
        print("CROSS_MATCH_OK %d %d %s" % (len(measured), tbl_windowed["n_hi"],
                                           clamp_token(md, "%.3f")))
        return 0

    if reasons:          # 判据失败也要落盘：失败现场是最该留证据的东西
        payload = assemble(args, palette, baseline, layout, self_check, measured, probe,
                           tbl_windowed, tbl_scaled, verdict_native, verdict_scaled,
                           warns + warns_s, reasons, t0, out_path, windows=windows,
                           refs=refs_info)
        _write(out_path, payload)
        print("RESULT_JSON %s" % out_path)
        print("CROSS_MATCH_FAIL %s" % reasons[0])
        return 2

    # 两个布局都算一遍，取**更大的** max_drift 报进令牌（判据以最坏的那个为准）
    max_drift = max_drift_of(verdict_native, verdict_scaled)
    all_warns = warns + warns_s
    payload = assemble(args, palette, baseline, layout, self_check, measured, probe,
                       tbl_windowed, tbl_scaled, verdict_native, verdict_scaled,
                       all_warns, reasons, t0, out_path, windows=windows, refs=refs_info)
    _write(out_path, payload)
    print("RESULT_JSON %s" % out_path)

    if reasons:
        print("CROSS_MATCH_FAIL %s" % reasons[0])
        return 2
    print_table("原生（%s）" % layout, verdict_native["table"])
    if verdict_scaled and verdict_scaled.get("table"):
        print_table("缩放至 %d 宽" % NATIVE_OUT_WIDTH, verdict_scaled["table"])
    for w in all_warns:
        print("  WARN %s" % w)
    for c in payload["conclusion"]:
        print("  => %s" % c)

    token = "CROSS_MATCH_OK %d %d %s" % (
        len(measured), tbl_windowed["n_hi"], clamp_token(max_drift, "%.3f"))
    codes = []
    for w in all_warns:
        code = w.split(" ")[0]
        if code not in codes:
            codes.append(code)
    if codes:
        token += " WARN(%s)" % ";".join(codes)
    print(token)
    return 0


def max_drift_of(*verdicts):
    """两种布局里取**更大的** max_drift（判据以最坏的那个为准）；都没数就是 None。"""
    vals = [v["max_drift"] for v in verdicts
            if v and v.get("max_drift") is not None]
    return round(max(vals), 4) if vals else None


def cross_match_self_check(payload):
    """自检模式的判据（与跨比赛判据**不同**，别混在一起读）。

    自检要证明的是「管线通」：数据流、四层计数、并排对照、门控检验、两种布局
    全部走过一遍且各段都量到了。**不是**证明「可迁移」——同一场比赛跟自己对
    基线做漂移检验，本身没有判别力（测出来只是抽样噪声），所以这里**不设**
    占比漂移闸门；跨比赛的 G1–G5 一律不套用。
    """
    clips = payload.get("clips") or []
    tbl = payload.get("totals_native") or {}
    probs = []
    if len(clips) < SELF_CHECK_MIN_CLIPS:
        probs.append("pipeline n_clips=%d<%d" % (len(clips), SELF_CHECK_MIN_CLIPS))
    if (tbl.get("n_hi") or 0) <= 0:
        probs.append("pipeline n_hi=0")
    for c in clips:
        if (c.get("n_hi") or 0) <= 0:
            probs.append("pipeline clip_no_detection %s" % c.get("label"))
            break
    if not (payload.get("verdict_native") or {}).get("table"):
        probs.append("pipeline compare_table_empty")
    if not (payload.get("verdict_native") or {}).get("gate_check"):
        probs.append("pipeline gate_check_not_run")
    if payload.get("totals_scaled_640") is None:
        probs.append("pipeline scale_probe_not_run")
    if probs:
        return probs
    # 管线通了：打印见证数字。**注意** max_drift 不进这一行 —— 同一场比赛对基线算出来的
    # 漂移没有判别力（只是抽样噪声），放进令牌里容易被误读成"可迁移性结论"，它只出现在
    # 主令牌 `CROSS_MATCH_OK <n_clips> <n_hi> <max_drift>` 与并排表里，且不套判据。
    gc = (payload.get("verdict_native") or {}).get("gate_check") or {}
    evidence = ("pipeline n_clips=%d n_hi=%d clips_ok=%d reasons_ok=%d/%d "
                "gate=%s scale_probe=%s"
                % (len(clips), tbl["n_hi"], len(clips),
                   sum(1 for c in clips
                       if all(k in (c.get("reason_counts") or {}) for k in REASON_KEYS)),
                   len(clips), gc.get("status") or "-",
                   "ok" if payload.get("totals_scaled_640") is not None else "skipped"))
    return evidence


def assemble(args, palette, baseline, layout, self_check, clips, probe,
             tbl_native, tbl_scaled, verdict_native, verdict_scaled, warns, reasons,
             t0, out_path, windows=None, refs=None):
    native_land = aggregate_landscape(clips)
    out = {
        "tool": "tools/cross_match_eval.py",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "mode": "self_check" if self_check else "cross_match",
        "why": "现有颜色/门控结论全部来自 migu_5106947698 一场球；本工具量「换一场还剩多少」",
        "contract": CONTRACT_DOC,
        "palette_input": {"palette": palette, "given_by": "human",
                          "note": "队服颜色必须人工录入（D013 LCD 方案），默认 red,white 只对旧比赛成立"},
        "measured_refs": refs,
        "params": {"n_clips": len(clips), "clip_len": args.clip_len, "stride": args.stride,
                   "imgsz": args.imgsz, "model": Path(args.model).name,
                   "hi_conf": cp.HI_CONF, "min_h_px": cp.MIN_H_PX,
                   "min_purity": cp.MIN_PURITY, "max_color_dist": CP._limit(),
                   "min_margin": CP._margin(), "layout": layout,
                    "classify_metric": cp.classify_metric(),
                   "native_width": NATIVE_OUT_WIDTH if tbl_scaled else None,
                   "input_dir": str(getattr(args, "video_dir", "") or SELF_CHECK_DIR)},
        "thresholds": {"MIN_N_HI": MIN_N_HI, "MAX_ABS_DRIFT_PT": MAX_ABS_DRIFT_PT,
                       "MAX_REL_DRIFT": MAX_REL_DRIFT,
                       "MAX_UNKNOWN_RATE_DRIFT_PT": MAX_UNKNOWN_RATE_DRIFT_PT,
                       "MAX_TOO_SMALL_THETA": MAX_TOO_SMALL_THETA,
                       "MAX_GRASS_ASSIGN_RATE": MAX_GRASS_ASSIGN_RATE,
                       "DEGENERATE_REL": DEGENERATE_REL},
        "palette_probe": probe,
        "baseline": baseline,
        "clips": clips,
        "totals_native": tbl_native,
        "verdict_native": verdict_native,
        "totals_scaled_640": tbl_scaled,
        "verdict_scaled_640": verdict_scaled,
        "play_windows": (None if windows is None else {
            "method": "grass_frac_scan",
            "step_s": windows["step_s"], "grass_min": windows["grass_min"],
            "min_interval_s": windows["min_interval_s"],
            "video_s": windows["video_s"], "n_samples": windows["n_samples"],
            "n_play_samples": windows["n_play"],
            "n_intervals": len(windows["intervals"]),
            "total_play_s": windows["total_play_s"],
            "intervals": [[a, b] for a, b in windows["intervals"]],
            "note": "采样级判据（非逐帧真值）：窗内仍可能有广告插播，"
                    "命中率见 n_play_samples/n_samples",
        }),
        "landscape": native_land,
        "warns": warns,
        "fails": reasons,
        "elapsed_s": round(time.time() - t0, 1),
        "output_path": str(out_path),
        "conclusion": [],
        "caveats": [
            "无真值：量的是取色失败原因的**构成**，不是「球员判得准不准」",
            "层内非球员率取自旧比赛标注（docs/hi_layer_color_gate.md §6.1），跨场能否搬本身是待验问题",
            "锚点以比赛为单位：事件切片比随机 20s 窗口更「忙」，抽样偏差已知",
            "两种布局（原生 / 缩放到 640 宽）都要看：原生布局混入了「画面尺度变了」这个因素",
            "跨分辨率比较时 too_small / rect 两层的占比**不可直接比**：它们由框高与画面"
            "宽度共同决定（MIN_H_PX=22 是绝对像素），换分辨率后必须看 scale 到 640 那一列",
            "v2 放宽色相窗口会牺牲一部分「精确率」换「覆盖率」：误判率从 2.0% 升到 9.1%"
            "（197 个真值样本里 4→18），是否值得取决于下游怎么用（见 docs §12.6）",
        ],
    }
    out["conclusion"] = _conclusions(out, tbl_native, tbl_scaled, verdict_native, warns)
    return out


def aggregate_landscape(clips):
    import numpy as np
    med = [c["hi_h_px"]["median"] for c in clips if c["hi_h_px"]["median"] is not None]
    kinds = {}
    for c in clips:
        kinds[c["landscape"]] = kinds.get(c["landscape"], 0) + 1
    return {"per_clip": [{"label": c["label"], "landscape": c["landscape"],
                          "median_h_px": c["hi_h_px"]["median"]} for c in clips],
            "median_h_px": round(float(np.median(med)), 1) if med else None,
            "kinds": kinds}


def _conclusions(out, tbl_native, tbl_scaled, verdict_native, warns):
    """把结论写成一句话（数字全部取自上面的表，不手填）。"""
    cs = []
    sh = (tbl_native or {}).get("reason_share_pct") or {}
    base = out["baseline"]["reason_share_pct"]
    if sh:
        worst = max(REASON_KEYS, key=lambda k: abs((sh.get(k) or 0) - base[k]))
        cs.append("最大单层漂移在 %s：%.1f%% -> %.1f%%（基线 %s）"
                  % (worst, base[worst], sh.get(worst) or 0.0, out["baseline"]["source_match"]))
    if tbl_scaled:
        cs.append("原生布局 n_hi=%d（h 中位 %s），缩放至 %d 宽后 n_hi=%d（h 中位 %s）：两者差距就是"
                  "「画面尺度变了」这一项"
                  % (tbl_native["n_hi"], out["landscape"]["median_h_px"], NATIVE_OUT_WIDTH,
                     tbl_scaled["n_hi"], tbl_scaled["hi_h_px"]["median"]))
    gate = verdict_native.get("gate_check") or {}
    if gate.get("status"):
        cs.append("门控检验：%s（too_small 占比 %.1f%%，反推层内非球员率 %s，上限 %.2f；"
                  "旧基线加权 %s）"
                  % (gate["status"], gate.get("too_small_pct") or 0.0,
                     gate.get("implied_too_small_theta"), MAX_TOO_SMALL_THETA,
                     gate.get("base_est_nonplayer_rate")))
    if warns:
        cs.append("判据报 %d 条 WARN：%s" % (len(warns), "；".join(warns[:4])))
    elif out.get("mode") == "self_check":
        cs.append("自检模式：不套用跨场判据（同场对基线没有判别力），只证明管线通")
    else:
        cs.append("全部判据通过：占比漂移均 < %.0f pt 且 < %.0f%%" % (MAX_ABS_DRIFT_PT,
                                                                  100 * MAX_REL_DRIFT))
    return cs


def print_self_check_log(log):
    """自检模式的每层并排对照（不套判据，只摆数）。

    单独一个函数是为了让"自检 vs 跨比赛"的区别在代码里也看得见：
    同一个 print_table 被两种模式共用，但自检模式**不会**因此把 PASS/WARN
    当成结论 —— 同场对基线没有判别力（见 cross_match_self_check 文档串）。
    """
    if not log:
        return
    v, tbl_scaled, layout = log
    print_table("原生（%s）" % layout, v["table"])
    if tbl_scaled is not None:
        print_table("缩放至 %d 宽" % NATIVE_OUT_WIDTH, tbl_scaled["table"])


def print_table(title, rows):
    print("  -- %s --" % title)
    print("     %-10s %8s %8s %8s %9s %s" % ("layer", "new%", "base%", "drift", "rel", "status"))
    for k, r in rows.items():
        print("     %-10s %8.2f %8.2f %+8.2f %8s %s"
              % (k, r["new_pct"], r["base_pct"], r["drift"],
                 ("%+.0f%%" % (100 * r["rel_drift"])) if r["rel_drift"] is not None else "-",
                 r["status"]))


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                    encoding="utf-8", newline="\n")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:                        # noqa: BLE001 - 兜底也要留令牌
        print("CROSS_MATCH_FAIL uncaught %s: %s" % (type(exc).__name__, exc))
        raise SystemExit(2)