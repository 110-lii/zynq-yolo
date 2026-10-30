#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_team_assign.py —— 在**真实画面**上跑一遍队别投票（不重跑检测）

为什么要它：`team_assign.py --selftest` 只证明「合成色块能投票」。真实画面里
躯干会被草坪/广告牌/阴影污染，管线是否还能给出**有区分度**的队别，必须拿
已有跟踪结果（`data/results/tracks/*.json`）+ 原片再跑一遍。

它**不做准确率声明**：没有人工标注的队别真值，「A/B 各占一半」只能说明链路通、
没有塌成单一类；要谈准确率得先标一批（属未完成项）。

用法：
    py tools/check_team_assign.py --tracks data/results/tracks/inc_01_t01154.9.json \
        --palette A=red,B=white [--points 4] [--min-votes 2]
输出末行：`TEAM_EVAL tracks=N voted=M labeled=K A=a B=b unknown=u` 或 `TEAM_EVAL_FAIL ...`
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cv2                                    # noqa: E402

from team_assign import Assigner              # noqa: E402


def parse_palette(spec):
    pal = {}
    for item in (spec or "").split(","):
        if not item.strip():
            continue
        k, _, v = item.partition("=")
        if not v:
            raise SystemExit("bad --palette item %r（要 A=red,B=white 这种）" % item)
        pal[k.strip()] = v.strip()
    return pal


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--tracks", required=True, help="track_players.py 产出的 tracks JSON")
    ap.add_argument("--palette", default="A=red,B=white",
                    help="队别 -> PALETTE_REF 颜色名（默认旧比赛的红/白）")
    ap.add_argument("--points", type=int, default=4, help="每条轨迹取前几个点投票")
    ap.add_argument("--min-votes", type=int, default=2)
    ap.add_argument("--ratio", type=float, default=0.6)
    args = ap.parse_args(argv)

    d = json.loads(Path(args.tracks).read_text(encoding="utf-8"))
    cap = cv2.VideoCapture(d["clip_path"])
    if not cap.isOpened():
        print("TEAM_EVAL_FAIL cannot open %s" % d["clip_path"])
        return 2

    a = Assigner(parse_palette(args.palette), min_votes=args.min_votes, ratio=args.ratio)
    votes = 0
    for tr in d.get("tracks", []):
        for pt in tr.get("points", [])[:max(1, args.points)]:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(pt["f"]))
            ok, frame = cap.read()
            if not ok:
                continue
            box = (pt["cx"] - pt["w"] / 2.0, pt["cy"] - pt["h"] / 2.0,
                   pt["cx"] + pt["w"] / 2.0, pt["cy"] + pt["h"] / 2.0)
            if a.observe(tr["track_id"], frame, box):
                votes += 1
    cap.release()

    tracks = d.get("tracks", [])
    teams = Counter(a.team(t["track_id"]) for t in tracks)
    st = a.stats()
    known = teams.get("A", 0) + teams.get("B", 0)
    unk = len(tracks) - known
    print("TEAM_EVAL tracks=%d voted=%d labeled=%d A=%d B=%d unknown=%d votes=%d size=%s"
          % (len(tracks), st["tracks_voted"], known, teams.get("A", 0), teams.get("B", 0),
             unk, votes, d.get("size")))
    if known == 0:
        print("TEAM_EVAL_FAIL 没有一条轨迹被判出队别（检查 --palette 是否与球衣一致）")
        return 1
    # 反例守卫：换错色板时「投上票的轨迹数」会明显塌下去（实测 red/white 57/77，
    # purple/cyan 只有 9/77）——票率过低先怀疑色板，而不是相信那几条标签。
    if len(tracks) and st["tracks_voted"] / float(len(tracks)) < 0.20:
        print("TEAM_EVAL_WARN low_vote_rate voted=%d/%d，先核对 --palette 与赛前配置是否一致"
              % (st["tracks_voted"], len(tracks)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
