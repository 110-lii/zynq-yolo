#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""team_assign.py —— 把「球衣颜色 → 队别」接进管线（D012/D013 的落点）

为什么单独成一个模块
--------------------
`classify_players.py` 是**逐帧分类器**（躯干取色 + 到预置色板的距离），
`board_pipeline.py` 是**逐帧管线**。两边需要的粘合剂是「同一个 track 在多帧里
投出一队」——单帧判色会因遮挡、光照、广告牌而抖，必须**按 track 累积投票**。
本模块只做这件事，不改分类器阈值，也不改管线别的行为。

单位与口径（全部沿用既有实现，不新造）
------------------------------------
- 输入配置：`zynq-yolo/match-config/2`（`tools/lcd_ui.py` 屏上录入、`tools/match_setup.py` 校验）
  ⇒ `teams.A/B.{kit_color,keeper_color}`、`referee.kit_color`；
- 取色：`classify_players.torso_rect()` + `torso_color()`（已含草坪/广告牌抑制）；
- 判色：`classify_players.classify()`（默认 v1 口径，距离门限 0.16）；
- 投票：同一 `track_id` 累计，达到 `min_votes` 才给结论；多数票不到 `ratio` 则仍 unknown。
  **宁可 unknown 也不硬猜** —— 与 classify_players 的既有纪律一致。

用法（模块内）
--------------
    a = Assigner.from_file("board/ui_config.json")
    a.observe(tid, frame_bgr, (x1, y1, x2, y2))     # 每检测帧调用
    a.team(tid)                                     # -> "A" / "B" / None

自检：python3 tools/team_assign.py --selftest
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from classify_players import classify, torso_color, torso_rect  # noqa: E402

SCHEMA = "zynq-yolo/match-config/2"

# role -> 是否属于某队（守门员归队；裁判不属于任何队）
ROLE_TO_TEAM = {"A": "A", "B": "B", "GK_A": "A", "GK_B": "B", "REF": None}


def palette_from_config(cfg):
    """match-config/2 -> classify_players.classify() 的 palette（role -> 颜色名）。"""
    teams = (cfg or {}).get("teams") or {}
    pal = {}
    for t in ("A", "B"):
        entry = teams.get(t) or {}
        if entry.get("kit_color"):
            pal[t] = entry["kit_color"]
        if entry.get("keeper_color"):
            pal["GK_" + t] = entry["keeper_color"]
    ref = ((cfg or {}).get("referee") or {}).get("kit_color")
    if ref:
        pal["REF"] = ref
    return pal


class Assigner:
    """按 track 累积球衣颜色投票，给出队别。"""

    def __init__(self, palette, min_votes=3, ratio=0.6, metric=None):
        self.palette = dict(palette or {})
        self.min_votes = int(min_votes)
        self.ratio = float(ratio)
        self.metric = metric
        self._votes = {}          # track_id -> {role: n}
        self._n = {}              # track_id -> 总有效票数

    # ---------------------------------------------------------------- 构造
    @classmethod
    def from_file(cls, path, **kw):
        """配置不存在或 schema 不符 -> 返回 None（调用方据此保持旧行为）。"""
        try:
            cfg = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:
            return None
        if cfg.get("schema") != SCHEMA:
            return None
        pal = palette_from_config(cfg)
        if not pal:
            return None
        return cls(pal, **kw)

    # ---------------------------------------------------------------- 观测
    def observe(self, track_id, bgr, box):
        """对一帧的一个框取躯干色并投票；返回本帧判出的 role（或 None）。"""
        if not self.palette:
            return None
        rect = torso_rect(box, bgr.shape)
        if rect is None:
            return None
        col = torso_color(bgr, rect)
        if col is None:
            return None
        purity = col[4] if len(col) > 4 else 1.0
        if purity < 0.25:            # 框里大部分是背景：这票不可信
            return None
        role, _dist, _scores = classify(col[:3], self.palette, metric=self.metric)
        if role == "unknown" or role not in self.palette:
            return None
        d = self._votes.setdefault(int(track_id), {})
        d[role] = d.get(role, 0) + 1
        self._n[int(track_id)] = self._n.get(int(track_id), 0) + 1
        return role

    # ---------------------------------------------------------------- 结论
    def label(self, track_id):
        tid = int(track_id)
        n = self._n.get(tid, 0)
        if n < self.min_votes:
            return None
        d = self._votes.get(tid) or {}
        if not d:
            return None
        role, k = max(d.items(), key=lambda kv: kv[1])
        if k / float(n) < self.ratio:
            return None
        return role

    def team(self, track_id):
        return ROLE_TO_TEAM.get(self.label(track_id))

    def teams(self):
        out = {}
        for tid in self._votes:
            t = self.team(tid)
            if t:
                out[tid] = t
        return out

    def stats(self):
        return {"tracks_voted": len(self._votes),
                "tracks_labeled": len(self.teams())}


# ---------------------------------------------------------------- 自检
def _selftest():
    import numpy as np

    ok = True

    def _img(bgr, box):
        img = np.zeros((240, 320, 3), np.uint8)
        img[:, :] = (60, 140, 60)                       # 草坪绿背景
        x1, y1, x2, y2 = box
        img[y1:y2, x1:x2] = bgr
        return img

    cfg = {"schema": SCHEMA,
           "teams": {"A": {"kit_color": "red", "keeper_color": "yellow"},
                     "B": {"kit_color": "blue", "keeper_color": "green"}},
           "referee": {"kit_color": "black"}}
    pal = palette_from_config(cfg)
    ok &= (set(pal) == {"A", "B", "GK_A", "GK_B", "REF"})

    a = Assigner(pal, min_votes=3)
    box_red, box_blue = (40, 60, 100, 220), (180, 60, 240, 220)
    img_red = _img((0, 0, 200), box_red)                # BGR 纯红
    img_blue = _img((200, 0, 0), box_blue)              # BGR 纯蓝
    for _ in range(4):
        a.observe(1, img_red, box_red)
        a.observe(2, img_blue, box_blue)
    ok &= (a.label(1) == "A" and a.team(1) == "A")
    ok &= (a.label(2) == "B" and a.team(2) == "B")

    # 票数不够 -> unknown（不硬猜）
    b = Assigner(pal, min_votes=5)
    for _ in range(2):
        b.observe(9, img_red, box_red)
    ok &= (b.label(9) is None and b.team(9) is None)

    # 纯草坪框 -> 不投票
    c = Assigner(pal, min_votes=1)
    img_grass = _img((60, 140, 60), (40, 60, 100, 220))
    ok &= (c.observe(3, img_grass, (40, 60, 100, 220)) is None)

    # 守门员角色归队（黄色 = GK_A ⇒ team A，绝不是 B）
    gk = Assigner({"GK_A": "yellow"}, min_votes=1)
    img_y = _img((0, 220, 220), box_red)                # BGR 黄
    for _ in range(2):
        gk.observe(5, img_y, box_red)
    ok &= (gk.label(5) == "GK_A" and gk.team(5) == "A")

    print("TEAM_ASSIGN_SELFTEST", "OK" if ok else "FAIL",
          json.dumps(a.stats(), ensure_ascii=False))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--show-config", metavar="JSON",
                    help="打印从 match-config/2 解析出的色板")
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest()
    if args.show_config:
        cfg = json.loads(Path(args.show_config).read_text(encoding="utf-8"))
        print(json.dumps(palette_from_config(cfg), ensure_ascii=False, indent=1))
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
