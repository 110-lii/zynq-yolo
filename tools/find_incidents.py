"""从整场转播里找出「值得人来判一次」的候选时刻，并切成人能看的片段。

为什么要这个
------------
判罚系统的最终验收要问的不是「检测框准不准」，而是「判得对不对」。后者需要
**人对真实事件的裁定**作为金标准 —— 这是只有人能提供的东西，模型给不了，
也不能靠模型自己生成（自己出的题自己答，等于没有对照）。

但一场 142 分钟的比赛不能让人从头看一遍。观察到的广播规律是：
**有争议/关键事件后导播会切回放**，回放表现为**短时间内的密集镜头切换**。
所以用 ffmpeg 的 scene 检测找镜头切换，再把「窗口内切换次数多」的时段
排成候选，交给人工裁定。

产物分两层（和仓库既有纪律一致）：
  * `data/samples/incidents/MANIFEST.csv`  —— **入库**，只记时间与哈希，可复现；
  * `data/samples/incidents/*.mp4|*.jpg`   —— **不入库**（第三方转播内容）。

用法
----
    # 1) 扫全片镜头切换（约 2-5 分钟，可后台跑）
    python tools/find_incidents.py --scan
    # 2) 聚类出候选窗口
    python tools/find_incidents.py --plan --top 12
    # 3) 切片 + 抽静帧 + 写给人填的 MANIFEST
    python tools/find_incidents.py --extract
"""
import argparse
import csv
import json
import re
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True

REPO = Path(__file__).resolve().parents[1]

VIDEO = Path(r"D:\yt-dlp\downloads\migu_5106947698\migu_5106947698.mp4")
FFMPEG = Path(r"D:\yt-dlp\ffmpeg\ffmpeg.exe")
OUT = REPO / "data" / "samples" / "incidents"
SCAN_JSON = OUT / "_cuts_scan.json"

PTS_RE = re.compile(r"pts_time:([0-9.]+)")

# MANIFEST 列。`decision` 之后的三列是**留给人填的**，脚本只写表头与空白。
COLS = ["incident_id", "t_center_s", "window_start_s", "window_end_s", "n_cuts",
        "clip_file", "still_before", "still_at", "still_after",
        "decision", "confidence", "notes"]


def run(cmd):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return p.returncode, p.stdout.decode("utf-8", "replace")


def frame_end(cuts, args):
    """素材末端的保守估计 = 最后一个切换点。

    本机没有 ffprobe（只有 `D:\\yt-dlp\\ffmpeg\\ffmpeg.exe`），拿不到精确总时长；
    而这里只需要「窗口别越过素材末尾」，用最后一个切换点当界足够。
    """
    return cuts[-1] if cuts else 0.0


def cmd_scan(args):
    OUT.mkdir(parents=True, exist_ok=True)
    if not VIDEO.exists():
        print("FIND_INCIDENTS_FAIL 找不到视频 %s" % VIDEO)
        return 1
    print("扫全片镜头切换（scene > %.2f），源 %s" % (args.scene_thr, VIDEO.name))
    # -an: 不解音频；-f null: 不写盘。pts_time 就是**绝对**时间（没有 -ss）
    cmd = [str(FFMPEG), "-hide_banner", "-i", str(VIDEO),
           "-filter:v", "select='gt(scene,%.3f)',showinfo" % args.scene_thr,
           "-an", "-f", "null", "-"]
    rc, txt = run(cmd)
    cuts = [float(m) for m in PTS_RE.findall(txt)]
    cuts = sorted(set(round(c, 2) for c in cuts))
    print("  切换点 %d 个，rc=%d" % (len(cuts), rc))
    if not cuts:
        print("FIND_INCIDENTS_FAIL scene 检测没拿到任何切换点（检查 ffmpeg 与阈值）")
        return 1
    with open(SCAN_JSON, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"video": str(VIDEO), "scene_thr": args.scene_thr,
                            "n_cuts": len(cuts), "cuts": cuts}, indent=2))
    print("  切换点 -> %s" % SCAN_JSON.relative_to(REPO))
    span = cuts[-1] - cuts[0]
    print("  时间跨度 %.1f s，平均 %.1f s 一个切换" % (span, span / max(1, len(cuts))))
    print("SCAN_OK %d" % len(cuts))
    return 0


def cmd_plan(args):
    if not SCAN_JSON.exists():
        print("FIND_INCIDENTS_FAIL 先跑 --scan")
        return 1
    with open(SCAN_JSON, encoding="utf-8") as f:
        cuts = json.load(f)["cuts"]
    win = float(args.window)
    # 滑动窗口统计切换密度：回放段会在 20-40 s 内出现多次切换
    skip_before = float(args.skip_before)
    span_end = frame_end(cuts, args)
    cands = []
    for c in cuts:
        if c < skip_before or (span_end and c > span_end):
            continue
        near = [x for x in cuts if c <= x < c + win]
        if len(near) >= args.min_cuts:
            cands.append({"t": c, "n": len(near), "end": max(near)})
    # 合并重叠候选（同一段回放会被每个切换点各命中一次）
    cands.sort(key=lambda d: d["t"])
    merged = []
    for d in cands:
        if merged and d["t"] <= merged[-1]["end"] + win:
            merged[-1]["end"] = max(merged[-1]["end"], d["end"])
            merged[-1]["n"] = max(merged[-1]["n"], d["n"])
        else:
            merged.append({"t": d["t"], "end": d["end"], "n": d["n"]})
    merged.sort(key=lambda d: -d["n"])
    top = merged[:args.top]
    top.sort(key=lambda d: d["t"])
    print("窗口 %.0f s 内切换 >= %d 次的候选段：%d 个（取前 %d）"
          % (win, args.min_cuts, len(merged), len(top)))
    for i, d in enumerate(top, 1):
        print("  #%02d  %7.1f s (第 %5.1f 分钟)  窗口内切换 %2d 次  段长 %.0f s"
              % (i, d["t"], d["t"] / 60.0, d["n"], d["end"] - d["t"]))
    with open(SCAN_JSON, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"video": str(VIDEO), "scene_thr": args.scene_thr,
                            "n_cuts": len(cuts), "cuts": cuts,
                            "plan": top}, indent=2))
    print("PLAN_OK %d" % len(top))
    return 0


def cmd_extract(args):
    if not SCAN_JSON.exists():
        print("FIND_INCIDENTS_FAIL 先跑 --scan 和 --plan")
        return 1
    with open(SCAN_JSON, encoding="utf-8") as f:
        plan = json.load(f).get("plan") or []
    if not plan:
        print("FIND_INCIDENTS_FAIL 没有 plan，先跑 --plan")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    pre, post = float(args.pre), float(args.post)
    rows = []
    for i, d in enumerate(plan, 1):
        iid = "inc_%02d" % i
        tc = float(d["t"])
        start = max(0.0, tc - pre)
        dur = pre + post
        clip = OUT / ("%s_t%07.1f.mp4" % (iid, tc))
        # 片段压到 640x360，够人看清且体积小（不入库，但别浪费磁盘）
        rc, out = run([str(FFMPEG), "-hide_banner", "-y", "-ss", "%.2f" % start,
                       "-i", str(VIDEO), "-t", "%.2f" % dur,
                       "-vf", "scale=640:-2", "-c:v", "libx264", "-preset", "veryfast",
                       "-crf", "26", "-an", str(clip)])
        if rc != 0 or not clip.exists():
            print("  跳过 %s：ffmpeg rc=%d" % (iid, rc))
            continue
        stills = {}
        for tag, off in (("before", -1.0), ("at", 0.0), ("after", 1.0)):
            p = OUT / ("%s_%s.jpg" % (iid, tag))
            run([str(FFMPEG), "-hide_banner", "-y", "-ss", "%.2f" % max(0.0, tc + off),
                 "-i", str(VIDEO), "-frames:v", "1", "-vf", "scale=1280:-2",
                 "-q:v", "3", str(p)])
            stills[tag] = p.name if p.exists() else ""
        rows.append({
            "incident_id": iid, "t_center_s": "%.1f" % tc,
            "window_start_s": "%.1f" % start, "window_end_s": "%.1f" % (start + dur),
            "n_cuts": d["n"], "clip_file": clip.name,
            "still_before": stills["before"], "still_at": stills["at"],
            "still_after": stills["after"],
            "decision": "", "confidence": "", "notes": "",
        })
        print("  %s  t=%.1f s  片长 %.0f s  -> %s" % (iid, tc, dur, clip.name))
    man = OUT / "MANIFEST.csv"
    with open(man, "w", encoding="utf-8", newline="\n") as f:
        # ★ `csv` 模块默认 lineterminator="\r\n"，与 .gitattributes 的 eol=lf 冲突
        #   （本仓库已因同类原因踩过 3 次）。必须显式改成 "\n"。
        w = csv.DictWriter(f, fieldnames=COLS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print("")
    print("清单 -> %s（%d 条，decision/confidence/notes 留空待人填）"
          % (man.relative_to(REPO), len(rows)))
    print("EXTRACT_OK %d" % len(rows))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--extract", action="store_true")
    ap.add_argument("--scene-thr", type=float, default=0.15)
    ap.add_argument("--window", type=float, default=30.0)
    ap.add_argument("--min-cuts", type=int, default=12)
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--skip-before", type=float, default=90.0,
                    help="跳过片头（实测第 0.8 s 处有 82 次切换 = 字幕动画，不是事件）")
    ap.add_argument("--pre", type=float, default=14.0)
    ap.add_argument("--post", type=float, default=6.0)
    args = ap.parse_args(argv)
    if args.scan:
        return cmd_scan(args)
    if args.plan:
        return cmd_plan(args)
    if args.extract:
        return cmd_extract(args)
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
