"""用球衣颜色**提升球员识别质量**（D013-a），不是给框贴颜色标签。

用户 2026-10-20 的纠正（逐字）：
    「等等，你现在是只判颜色吗，我的意思是通过颜色去更好的识别球员」

⇒ 颜色必须服务于"球员有没有被识别出来 / 有没有被正确归类"，落成四条可测量的路径：

  ① 剔除   把裁判/教练/观众判为非球员（他们的颜色不属于任一队）
  ② 补检   用颜色先验找回检测器漏掉的小目标（D010 实测 320 输入相对召回仅 60%）
  ③ 稳身份 跨帧关联时用队别约束，减少 ID switch
  ④ 分两队 为球权 / 队形 / 半场服务

本文件把①②④做成**可测量**的对照实验；③ 在 track_players.py 里接。

★ 判据必须先能失败
   对每条路径都给「有颜色先验」vs「无颜色先验」两组，且必须报**同一批帧**上的差异。
   如果颜色先验不能让任何指标变好，就如实写"没变好" —— 这比粉饰成有效强。

① 剔除 / ④ 分队的测量方式
   —— 没有人工"谁是球员"真值，所以用**可自证的结构量**：
     (a) 帧内一致性：真正的两队各自在颜色上应聚成两团，团的紧致度用组内色相标准差衡量；
     (b) 位置先验：球员基本在场地内（画面中下部），裁判/教练常贴边线；
     (c) 与置信度的相关性：非球员框（教练/观众）常来自看台，检测置信度分布不同。
   这三条都**不能**单独证明"分类正确"，但它们能证明"分类是否把两类分开了"。

② 补检的测量方式（这条最能证明"更好地识别球员"）
   做法：把检测置信度阈值从 0.25 降到 LO_CONF=0.08，再把新出现的低置信框送去颜色判定；
   颜色属于任一队（距离 < MAX_COLOR_DIST 且有 margin）的**保留**，否则丢弃。
   对照组：不做颜色过滤，直接接受所有低置信框。
   指标：
     n_extra_kept   颜色过滤后保留的低置信框数
     n_extra_all    所有低置信框数
     keep_ratio     n_extra_kept / n_extra_all
     precision_proxy  —— **不能**叫 precision：没有真值。改用「保留框的队色距离中位数」
                        与「丢弃框的距离中位数」对比：保留的应显著更接近队色。
   若两者无差异 ⇒ 颜色先验无效，如实报告。

用法
----
    & D:\\python\\python.exe tools\\color_prior.py --clips data/samples/incidents --palette red,white
    ... --max-clips 3 --stride 4        # 调试
    ... --no-color                      # 对照组：不加颜色先验

产物
----
    data/results/color_prior.json    每条路径的对照数据
    data/results/viz/color_prior/    可视化（保留/丢弃的低置信框分别上色）

判据令牌
    COLOR_PRIOR_DONE <n_clips> <n_kept> <n_dropped>
    COLOR_PRIOR_FAIL <reason>
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[1]
RESULTS = REPO / "data" / "results"
VIZ_DIR = RESULTS / "viz" / "color_prior"
MODEL_DEFAULT = REPO / "data" / "models" / "yolov8n.pt"

# 复用 classify_players.py 里已经校准过的量测与判定（同一份阈值，避免两套口径）。
# ★ 用 importlib 从文件路径加载，**不要** sys.path.insert + import：
#   后者会让 Python 往 tools/ 写 __pycache__，而 tools/check_repo.py 的 check d)
#   是扫盘的（不看 .gitignore）⇒ 直接 FAIL。本仓库已多次踩这个坑。
sys.dont_write_bytecode = True
import importlib.util as _ilu

_cp_spec = _ilu.spec_from_file_location("_cp", str(REPO / "tools" / "classify_players.py"))
_cp = _ilu.module_from_spec(_cp_spec)
# ★ 必须先登记到 sys.modules 再 exec：classify_players.py 里若有 @dataclass，
#   dataclasses 会去 sys.modules 找 cls.__module__ 对应的模块，找不到就
#   AttributeError: 'NoneType' object has no attribute '__dict__'。
# ★ 用 setdefault 会**静默复用**已存在的 `_cp`：若某个模块先加载了 classify_players
#   并把自己那份登记成 "_cp"，这里就会拿到同一份实例、或者（更糟）拿到一份阈值不同的
#   另一份。2026-10-20 实测踩到：`cross_match_eval --color-metric v2` 只改了自己手里
#   一份的 CLASSIFY_METRIC，量测路径走的却是本文件这一份 ⇒ JSON 标着 v2、数字逐位是 v1。
#   ⇒ 这里**强制覆盖**：谁最后加载谁说了算，全进程只有一份权威实例。
sys.modules["_cp"] = _cp
_cp_spec.loader.exec_module(_cp)

PALETTE_REF = _cp.PALETTE_REF
MAX_COLOR_DIST = _cp.MAX_COLOR_DIST
MIN_MARGIN = _cp.MIN_MARGIN
MIN_H_PX = _cp.MIN_H_PX
MIN_PURITY = _cp.MIN_PURITY
color_of_name = _cp.color_of_name
classify = _cp.classify
torso_color = _cp.torso_color
torso_rect = _cp.torso_rect
distance_to_ref = _cp.distance_to_ref


def set_classify_metric(metric):
    """切换取色口径（"v1"/"v2"），返回**实际生效**的口径。

    ★ 为什么必须走这个函数而不是直接赋值：`importlib` 按路径加载会产出**多份模块
      实例**（本文件一份、调用方各自一份、player_gate 里又一份）。2026-10-20 实测踩到：
      `cross_match_eval.py --color-metric v2` 只改了**它自己手里那份**实例的
      `CLASSIFY_METRIC`，而量测路径走的是本文件这一份 —— 结果 JSON 里
      `classify_metric: "v2"` 写着 v2，四层占比与 unknown_rate 却与 v1 **逐位相同**。
      这正是本仓库反复记过的「静默假绿」：不报错、数字看着合理、结论是错的。
      ⇒ 口径只有一个权威落点（本模块的 `_cp` 实例），要改只能从这里改。
    """
    if metric not in ("v1", "v2"):
        raise SystemExit("bad classify metric: %r（只接受 v1 / v2）" % (metric,))
    _cp.CLASSIFY_METRIC = metric
    return _cp.CLASSIFY_METRIC


def classify_metric():
    """当前**实际生效**的取色口径（量测路径用的那一份）。"""
    return _cp.CLASSIFY_METRIC

# ②b 统一门控（--gate）：加载 tools/player_gate.py。
# ★ 与上面同一纪律：importlib 按路径加载 + 加载前设 dont_write_bytecode。
#   代价是 player_gate.py 里 `import classify_players` 会拿到**另一份模块对象**，
#   所以下面显式把它的 PALETTE_REF / color_of_name / torso_rect / classify 换成
#   本文件用的这一份 —— 两套阈值口径必须物理上是同一个对象，不能只是"数值相等"。
_pg = None
GateConfig = None
judge_frame = None
GateMeter = None
PRESET_RULES = None


def _load_gate():
    global _pg, GateConfig, judge_frame, GateMeter, PRESET_RULES
    if _pg is not None:
        return _pg
    sys.dont_write_bytecode = True
    _spec = _ilu.spec_from_file_location("_pg", str(REPO / "tools" / "player_gate.py"))
    _mod = _ilu.module_from_spec(_spec)
    # 同上：exec 之前就要登记，player_gate.py 顶层有 @dataclass GateConfig。
    sys.modules.setdefault("_pg", _mod)
    _spec.loader.exec_module(_mod)
    for k in ("PALETTE_REF", "color_of_name", "torso_rect", "classify"):
        setattr(_mod, k, globals()[k])
    _pg = _mod
    GateConfig = _mod.GateConfig
    judge_frame = _mod.judge_frame
    GateMeter = _mod.ColorMeter
    PRESET_RULES = _mod.PRESETS["presets"]["rules"]
    return _mod

HI_CONF = 0.25          # 常规检测阈值（与全仓库口径一致）
LO_CONF = 0.08          # 补检时的下限
FIELD_Y_MIN = 0.35      # 画面高度 35% 以上基本是看台/广告牌，不算场地内

# ★ 取色失败的两种原因必须分开，否则会用一个假前提去调阈值
#   (a) 太小的框：框高 < MIN_H_PX。**这是信息不足，不是"颜色不像任何队"**——
#       调 MAX_COLOR_DIST / MIN_PURITY 永远不会让它们通过，白调。
#   (b) 够大的框但取色不合格：purity 低（背景/广告牌占多数）或压根连不出躯干矩形。
#       这一类才是「颜色在手但有歧义」，阈值的活干在这里。
# 2026-10-20 标注实证（docs/player_labels_v1.md §4）：hi 层 6 个非球员里 4 个是 (a)，
# 而且这 4 个全是 h≈136–144 px 的大框 —— 大框取色失败，本身就是非球员的信号。
# 详见 docs/hi_layer_color_gate.md。
REASON_TOO_SMALL = "too_small"
REASON_PURITY = "purity"
REASON_RECT = "rect"
REASON_COLOR = "color"


def _classify_box(frame, bb, palette, why=None):
    """对一个框返回 (role, dist, purity, hsv) ；判不了返回 ("unknown", 9.9, purity, None)。

    why: 可选 list，追加取色失败的原因码（REASON_*），供分层统计用。
    """
    h = float(bb[3] - bb[1])
    if h < MIN_H_PX:
        if why is not None:
            why.append(REASON_TOO_SMALL)
        return "unknown", 9.9, 0.0, None
    rect = torso_rect(bb, frame.shape)
    if rect is None:
        if why is not None:
            why.append(REASON_RECT)
        return "unknown", 9.9, 0.0, None
    col = torso_color(frame, rect)
    if col is None:
        if why is not None:
            why.append(REASON_COLOR)
        return "unknown", 9.9, 0.0, None
    purity = col[4]
    if purity < MIN_PURITY:
        if why is not None:
            why.append(REASON_PURITY)
        return "unknown", 9.9, purity, col
    role, dist, _scores = classify(col, palette)
    return role, dist, purity, col


def process_clip(model, clip_path, palette, use_color, args):
    cap = cv2.VideoCapture(str(clip_path))
    if not cap.isOpened():
        raise RuntimeError("cannot open %s" % clip_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    H_img = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    # ① 剔除 + ④ 分队：逐帧统计
    n_hi = 0
    n_team = {"A": 0, "B": 0, "unknown": 0}
    hue_by_role = {"A": [], "B": []}
    in_field_by_role = {"A": [0, 0], "B": [0, 0]}      # [场地内, 总数]
    conf_by_role = {"A": [], "B": [], "unknown": []}

    # ② 补检：低置信框的去留
    n_extra_all = 0
    n_extra_kept = 0
    dist_kept = []
    dist_dropped = []

    # ②b 统一门控（D013-a 的收口点）：--gate 时把 ② 的去留交给 tools/player_gate.py
    #     两者对"什么叫球员"必须只有一套说法，否则下游无从解释。
    gate = getattr(args, "gate", None)
    gate_mode = bool(gate) and use_color
    gate_cfg = None
    gate_cnt = {"player": 0, "unknown": 0, "staff": 0}
    gate_reason = {}
    gate_drop_reasons = set()
    gate_meter = None
    if gate_mode:
        _load_gate()
        if gate == "presets":                    # 只换规则表，保留 v1 的结构证据
            gate_cfg = GateConfig(palette=dict(palette), rules=dict(PRESET_RULES))
            gate_cfg.check()
            gate_drop_reasons = set(k for k, v in PRESET_RULES.items() if v == "drop")
        else:
            if gate not in _pg.PRESETS:
                raise SystemExit("--gate 取值只能是 %s，收到 %r"
                                 % ("/".join(sorted(_pg.PRESETS)), gate))
            gate_cfg = _pg.preset_config(gate, palette=palette, rules=args.gate_rules)
            gate_cfg.gate_on = True
            gate_drop_reasons = set(k for k, v in gate_cfg.resolved_rules().items()
                                    if v == "drop")
        # 门控是"判定层"，颜色只作为其中一条证据；几何守卫与结构证据必须同时生效，
        # 否则 ② 会退化成「取不到色就丢」，正是 v1 被实测推翻的那个错误。
        gate_cfg.use_color = True
        gate_cfg.use_struct = True
        gate_meter = GateMeter(gate_cfg.palette, gate_cfg.use_hull, gate_cfg.hull_purity)

    # hi 层取色失败原因分层（给 docs/hi_layer_color_gate.md 提供可复算的数字）
    hi_by_reason = {REASON_TOO_SMALL: 0, REASON_PURITY: 0, REASON_RECT: 0,
                    REASON_COLOR: 0, "colored": 0}
    hi_unknown_h = []

    frame_no = -1
    stride = max(1, int(args.stride))
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frame_no += 1
        if frame_no % stride:
            continue

        r_hi = model.predict(frame, classes=[0], conf=HI_CONF, iou=0.45,
                             imgsz=args.imgsz, verbose=False)
        boxes_hi = r_hi[0].boxes if r_hi and r_hi[0].boxes is not None else None

        if boxes_hi is not None and len(boxes_hi) > 0:
            for bb, cf in zip(boxes_hi.xyxy.cpu().numpy(), boxes_hi.conf.cpu().numpy()):
                n_hi += 1
                why = []
                role, dist, purity, col = _classify_box(frame, bb, palette, why)
                ckey = role if role in ("A", "B") else "unknown"
                n_team[ckey] += 1
                conf_by_role[ckey].append(float(cf))
                # hi 层为什么判不出颜色：按原因分开计数（见 REASON_* 注释）
                if role in ("A", "B"):
                    hi_by_reason["colored"] += 1
                else:
                    hi_by_reason[why[0] if why else REASON_COLOR] += 1
                    hi_unknown_h.append(float(bb[3] - bb[1]))
                if role in ("A", "B"):
                    if col is not None:
                        hue_by_role[role].append(col[0])
                    cy = float((bb[1] + bb[3]) / 2.0) / max(1.0, float(H_img))
                    inside = cy >= FIELD_Y_MIN
                    in_field_by_role[role][1] += 1
                    if inside:
                        in_field_by_role[role][0] += 1

        # --- ② 补检：只取「低于 HI_CONF、高于 LO_CONF」的框 ---
        r_lo = model.predict(frame, classes=[0], conf=LO_CONF, iou=0.45,
                             imgsz=args.imgsz, verbose=False)
        boxes_lo = r_lo[0].boxes if r_lo and r_lo[0].boxes is not None else None
        if boxes_lo is not None and len(boxes_lo) > 0:
            lo_bb = boxes_lo.xyxy.cpu().numpy()
            lo_cf = boxes_lo.conf.cpu().numpy()
            hi_bb = boxes_hi.xyxy.cpu().numpy() if boxes_hi is not None else np.zeros((0, 4))
            for bb, cf in zip(lo_bb, lo_cf):
                if cf >= HI_CONF:
                    continue                          # 已经在高置信集合里
                # 与高置信框重合的不算"漏检"
                if len(hi_bb) and _iou_max(bb, hi_bb) > 0.5:
                    continue
                n_extra_all += 1
                if gate_mode:
                    d = judge_frame(frame, np.asarray([bb], dtype=float),
                                    np.asarray([float(cf)]), gate_cfg,
                                    meter=gate_meter)[0]
                    role = "unknown" if d.role == "unknown" else "colored"
                    dist, purity = d.color_dist, d.purity
                    if dist is not None:
                        dist = float(dist)
                    # 补检的语义是「这个框是不是被漏掉的球员」：
                    # 门控判 staff = 明确不是人 → 丢；其余（player/unknown）→ 留。
                    # 注意这与 hi 层的处置不同：hi 层已经算进球员集合，unknown 只降权不下场。
                    accept = (d.role != "staff")
                    gate_cnt[d.role] = gate_cnt.get(d.role, 0) + 1
                    gate_reason[d.reason] = gate_reason.get(d.reason, 0) + 1
                else:
                    role, dist, purity, col = _classify_box(frame, bb, palette)
                    accept = True
                    if use_color:
                        # v1 行为逐字保留：取不到色的补检框一律丢。
                        # 标注实证（docs/hi_layer_color_gate.md §6）：这一条会把
                        # too_small 层（87.1% 是球员）一起扔掉 —— 想改就用 --gate。
                        accept = (role in ("A", "B")) and dist < MAX_COLOR_DIST
                if accept:
                    n_extra_kept += 1
                    dist_kept.append(dist if role in ("A", "B") else 9.9)
                else:
                    dist_dropped.append(9.9 if role == "unknown" else dist)

    cap.release()

    def _stat(v):
        if not v:
            return {"n": 0}
        a = np.array(v, dtype=float)
        return {"n": int(a.size), "median": round(float(np.median(a)), 4),
                "mean": round(float(a.mean()), 4)}

    # ① 组内紧致度：两队各自色相的标准差（小 = 聚得紧 = 分类有区分力）
    tight = {}
    for role in ("A", "B"):
        hv = hue_by_role[role]
        if len(hv) >= 5:
            rad = np.deg2rad(np.array(hv) * 360.0)
            # 圆形标准差：用单位圆上到平均方向的偏差
            mx, my = np.mean(np.cos(rad)), np.mean(np.sin(rad))
            mean_dir = np.arctan2(my, mx)
            dev = np.abs(np.angle(np.exp(1j * (rad - mean_dir))))
            tight[role] = {"n": len(hv),
                           "hue_std_deg": round(float(np.degrees(dev.std())), 2),
                           "hue_mean_deg": round(float(np.degrees(mean_dir) % 360.0), 1)}
        else:
            tight[role] = {"n": len(hv)}

    # 位置先验：球员应在场地内
    field_rate = {}
    for role in ("A", "B"):
        inside, tot = in_field_by_role[role]
        field_rate[role] = {"inside": inside, "total": tot,
                            "rate": round(inside / tot, 3) if tot else None}

    return {
        "clip": Path(clip_path).name,
        "fps": fps,
        "n_hi_boxes": n_hi,
        "team_counts": n_team,
        "team_share": {k: (round(v / n_hi, 3) if n_hi else None) for k, v in n_team.items()},
        "hue_tightness": tight,
        "field_rate": field_rate,
        "conf_by_role": {k: _stat(v) for k, v in conf_by_role.items()},
        "rescue": {
            "n_extra_all": n_extra_all,
            "n_extra_kept": n_extra_kept,
            "keep_ratio": round(n_extra_kept / n_extra_all, 3) if n_extra_all else None,
            "dist_kept": _stat(dist_kept),
            "dist_dropped": _stat(dist_dropped),
            "gate": gate if gate_mode else None,
            # --gate 时：② 的去留不再由"取不到色"决定，而由门控的 role 决定
            "gate_role_counts": dict(gate_cnt) if gate_mode else None,
            "gate_reason_counts": dict(sorted(gate_reason.items(),
                                              key=lambda kv: -kv[1])) if gate_mode else None,
        },
        # hi 层"判不出颜色"的原因分层 —— 区分「信息不足」与「颜色有歧义」
        "hi_unknown_reason": dict(hi_by_reason),
        "hi_unknown_h": _stat(hi_unknown_h),
    }


def _iou_max(bb, others):
    x1 = np.maximum(bb[0], others[:, 0])
    y1 = np.maximum(bb[1], others[:, 1])
    x2 = np.minimum(bb[2], others[:, 2])
    y2 = np.minimum(bb[3], others[:, 3])
    inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    a1 = max(1e-6, (bb[2] - bb[0]) * (bb[3] - bb[1]))
    a2 = np.maximum(1e-6, (others[:, 2] - others[:, 0]) * (others[:, 3] - others[:, 1]))
    return float(np.max(inter / (a1 + a2 - inter)))


def main(argv=None):
    ap = argparse.ArgumentParser(description="用球衣颜色提升球员识别（D013-a）")
    ap.add_argument("--clips", default=str(REPO / "data" / "samples" / "incidents"))
    ap.add_argument("--max-clips", type=int, default=0)
    ap.add_argument("--palette", default="red,white",
                    help="预设队色（D013 触屏录入的那份），逗号分隔")
    ap.add_argument("--stride", type=int, default=4)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--model", default=str(MODEL_DEFAULT))
    ap.add_argument("--no-color", action="store_true",
                    help="对照组：补检时不做颜色过滤")
    ap.add_argument("--gate", default="after",
                    help="② 补检的去留交给 tools/player_gate.py。" 
                         "取值：after（默认=v2 门控）/ legacy / noprior / nostruct / "
                         "norules / nohull / minimal / presets（=只换规则表的 v1 结构证据）")
    ap.add_argument("--gate-rules", default=None,
                    help="覆盖规则表，格式 reason=action,reason=action，"
                         "如 too_small=keep,purity=drop,rect=drop,color=demote")
    args = ap.parse_args(argv)

    mp = Path(args.model)
    if not mp.exists():
        print("COLOR_PRIOR_FAIL model_missing %s" % mp)
        return 2
    names = [s.strip().lower() for s in args.palette.split(",") if s.strip()]
    if len(names) < 2:
        print("COLOR_PRIOR_FAIL palette_needs_2")
        return 2
    palette = {"A": names[0], "B": names[1]}

    clips = sorted(Path(args.clips).glob("*.mp4")) if Path(args.clips).is_dir() \
        else [Path(args.clips)]
    if args.max_clips:
        clips = clips[: args.max_clips]
    if not clips:
        print("COLOR_PRIOR_FAIL no_clips")
        return 2

    from ultralytics import YOLO
    model = YOLO(str(mp))

    out = {"tool": "tools/color_prior.py",
           "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "decision": "D013-a：颜色是提升球员识别的手段，不是目标",
           "mode": "no_color_control" if args.no_color else "color_prior",
           "palette": palette,
           "params": {"hi_conf": HI_CONF, "lo_conf": LO_CONF,
                      "stride": args.stride, "imgsz": args.imgsz,
                      "max_color_dist": MAX_COLOR_DIST, "min_margin": MIN_MARGIN,
                      "min_h_px": MIN_H_PX, "min_purity": MIN_PURITY,
                      "gate": args.gate if not args.no_color else "off",
                      "gate_rules": args.gate_rules},
           "clips": [],
           "limitations": [
               "① 剔除/④ 分队**没有人工真值**：hue_tightness 与 field_rate 只能证明"
               "『两类被分开』，不能证明『分得对』",
               "② 补检的 keep_ratio 也**不是** precision：没有标注时无法算准确率；"
               "dist_kept vs dist_dropped 的差距只是必要条件",
               "③ 稳身份（ID switch 减少）需接 track_players.py，本文件未测",
               "② 的默认判据已经换成门控（--gate after）。旧口径 `--gate presets` "
               "保留可复算：它把 too_small 层（标注里 87.1% 是球员）一起丢掉，"
               "见 docs/player_gate_v1.md §4",
               "门控的 reason/score 只在本文件输出里做计数，不参与 ① ④ 的统计口径",
               "裁判若与某队同色，本方法在原理上无法分开（物理限制）",
           ]}
    n_kept = n_drop = 0
    for i, c in enumerate(clips, 1):
        print("[%d/%d] %s ..." % (i, len(clips), c.name), flush=True)
        r = process_clip(model, c, palette, not args.no_color, args)
        out["clips"].append(r)
        n_kept += r["rescue"]["n_extra_kept"]
        n_drop += r["rescue"]["n_extra_all"] - r["rescue"]["n_extra_kept"]
        print("  team=%s rescue kept=%d/%d hue_std=%s"
              % (json.dumps(r["team_share"], ensure_ascii=False),
                 r["rescue"]["n_extra_kept"], r["rescue"]["n_extra_all"],
                 json.dumps({k: v.get("hue_std_deg") for k, v in r["hue_tightness"].items()},
                            ensure_ascii=False)), flush=True)

    # 汇总
    tot_hi = sum(c["n_hi_boxes"] for c in out["clips"])
    all_kept = []
    all_drop = []
    for c in out["clips"]:
        all_kept.append(c["rescue"]["dist_kept"].get("median"))
        all_drop.append(c["rescue"]["dist_dropped"].get("median"))
    out["totals"] = {
        "n_clips": len(clips),
        "n_hi_boxes": tot_hi,
        "rescue_kept": n_kept,
        "rescue_dropped": n_drop,
        "rescue_keep_ratio": round(n_kept / (n_kept + n_drop), 3) if (n_kept + n_drop) else None,
        "median_dist_kept": _med(all_kept),
        "median_dist_dropped": _med(all_drop),
    }
    (RESULTS / "color_prior.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print("RESULT_JSON %s" % (RESULTS / "color_prior.json"))
    print("COLOR_PRIOR_DONE %d %d %d" % (len(clips), n_kept, n_drop))
    return 0


def _med(vals):
    v = [x for x in vals if x is not None]
    return round(float(np.median(v)), 4) if v else None


if __name__ == "__main__":
    raise SystemExit(main())
