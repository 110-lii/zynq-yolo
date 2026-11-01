"""主机代理复现「板上 --proposal --calib」的事件链：球链用与板上同一套
（ball_cv_probe 候选 + build/ball_cls_c.exe 分类器 + BallTracker + place_ball），
球员检测降级成主机 YOLOv8n（板上是 person_v8n25 / ncnn）。

用途：板子离线时先回答「这段素材在 --every 25 --ball-every 2 --max-det 0 下会不会出事件」。
**结论只作预筛**，板上真跑才算验证（AGENTS.md §2）。

用法：py build/_evt_host_proxy.py --video build/ball_clip.mp4 \
          --calib data/calib/field_calib_ball.json --every 25 --ball-every 2
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))
os.chdir(REPO)

import cv2          # noqa: E402
import numpy as np  # noqa: E402

from ball_cv_probe import candidates as ball_cands   # noqa: E402
from ball_policy import place_ball, resolve_max_miss  # noqa: E402
from ball_track import BallTracker                   # noqa: E402
from board_track import MultiTracker                 # noqa: E402
from field_coords import box_to_field, inside_hull   # noqa: E402
from result_publisher import build_frame             # noqa: E402

CLS = os.path.join(REPO, "build", "ball_cls_c.exe")


def cls_scores(patches):
    if not patches:
        return []
    arr = np.stack([cv2.cvtColor(cv2.resize(p, (32, 32), interpolation=cv2.INTER_AREA),
                                 cv2.COLOR_BGR2RGB) for p in patches]).astype(np.uint8)
    fd, raw = tempfile.mkstemp(suffix=".raw")
    os.close(fd)
    try:
        arr.tofile(raw)
        r = subprocess.run([CLS, raw, str(len(patches))], capture_output=True, text=True)
    finally:
        os.unlink(raw)
    return [float(l.split()[2]) for l in r.stdout.splitlines() if l.startswith("SCORE")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--calib", default="data/calib/field_calib_ball.json")
    ap.add_argument("--every", type=int, default=25)
    ap.add_argument("--ball-every", type=int, default=2)
    ap.add_argument("--max-det", type=int, default=0)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--ball-cls-th", type=float, default=0.50)
    ap.add_argument("--model", default="data/models/yolov8n.pt")
    ap.add_argument("--out", default="")
    ap.add_argument("--dump-frame", type=int, default=-1,
                    help="把这个帧号画出来（球红圈 + 球员框），供人眼核对")
    ap.add_argument("--dump-png", default="")
    a = ap.parse_args()

    calib = json.load(open(a.calib, encoding="utf-8"))
    H = calib["H"]
    hull = calib.get("image")

    from ultralytics import YOLO
    yolo = YOLO(a.model)

    cap = cv2.VideoCapture(a.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    tr = MultiTracker(iou_th=0.3, max_missed=max(a.every * 2, 10), min_hits=2)
    ball_tr = BallTracker(min_hits=2, max_miss=resolve_max_miss(None, fps), min_speed=3.0)

    hits = {}
    n_det = 0
    ball = None
    n_frames = n_ball_field = n_poss = n_oop = n_goal = n_poss_det = 0
    n_ball_scan = n_ball_hit = 0
    best_poss = None
    ev_rows = []
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        is_det = (idx % a.every == 0) and (a.max_det == 0 or n_det < a.max_det)
        if is_det:
            res = yolo.predict(frame, imgsz=640, conf=a.conf, verbose=False,
                               classes=[0])[0]
            dets = []
            for b in res.boxes:
                x1, y1, x2, y2 = [float(v) for v in b.xyxy[0]]
                dets.append([x1, y1, x2, y2, float(b.conf[0])])
            tracks = tr.step(dets, 0.0, 0.0, dt=a.every)
            n_det += 1
        else:
            tracks = tr.predict(0.0, 0.0)

        rows = []
        for t in tracks:
            row = [t[0], round(t[1], 1), round(t[2], 1), round(t[3], 1), round(t[4], 1),
                   round(t[5], 3)]
            if H is not None:
                u, v = (t[1] + t[3]) * 0.5, t[4]
                if hull is None or inside_hull(hull, u, v):
                    f = box_to_field(H, t[1:5])
                    row += [None, None] if f is None else [round(f[0], 2), round(f[1], 2)]
                else:
                    row += [None, None]
            rows.append(row)
        if is_det:
            for t in tracks:
                hits[t[0]] = hits.get(t[0], 0) + 1
        h = frame.shape[0]
        rows = [r for r in rows if r[5] >= 0.30 and (r[4] - r[2]) >= max(16.0, 0.02 * h)]
        rows.sort(key=lambda r: (r[5], hits.get(r[0], 1)), reverse=True)
        rows = rows[:25]

        _do_ball = is_det if a.ball_every <= 0 else (idx % max(1, a.ball_every) == 0)
        img_xy = None
        if _do_ball:
            bc = []
            cs = ball_cands(frame, None)
            pat, sel = [], []
            for c in cs:
                x, y = int(c["x"]), int(c["y"])
                p = frame[max(0, y - 32):y + 32, max(0, x - 32):x + 32]
                if p.shape[0] == 64 and p.shape[1] == 64:
                    pat.append(p)
                    sel.append(c)
            if pat:
                for c, sc in zip(sel, cls_scores(pat)):
                    if sc >= a.ball_cls_th:
                        bc.append(dict(c, cls=sc))
            bt = ball_tr.step(bc, dt=a.ball_every)
            n_ball_scan += 1
            if any(t.miss == 0 for t in bt):
                n_ball_hit += 1
            if bt:
                b = max(bt, key=lambda t: (round(t.score, 2), t.hits))
                conf = round(min(1.0, b.hits / 5.0), 2)
                img_xy = (b.x, b.y)
                f_xy = box_to_field(H, [b.x - 1, b.y - 1, b.x + 1, b.y + 1]) if H else None
                ball = place_ball(f_xy, (b.x, b.y), conf,
                                  is_predicted=(b.miss > 0),
                                  allow_predict_past_boundary=False)
            else:
                ball = None

        n_frames += 1
        f = build_frame(int(round(idx * 1000.0 / fps)), rows, hits=hits,
                        camera_valid=True, ball=ball)
        ev = f.get("events") or {}
        if idx == a.dump_frame:
            vis = frame.copy()
            for r in rows:
                cv2.rectangle(vis, (int(r[1]), int(r[2])), (int(r[3]), int(r[4])),
                              (0, 255, 0), 2)
                if len(r) > 7 and r[6] is not None:
                    cv2.putText(vis, "#%d %.0f,%.0f" % (r[0], r[6], r[7]),
                                (int(r[1]), int(r[2]) - 4), cv2.FONT_HERSHEY_SIMPLEX,
                                0.45, (0, 255, 0), 1, cv2.LINE_AA)
            if img_xy:
                cv2.circle(vis, (int(img_xy[0]), int(img_xy[1])), 10, (0, 0, 255), 2)
            txt = "frame %d  %s" % (idx, json.dumps(ev, ensure_ascii=False))
            cv2.putText(vis, txt, (12, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                        (0, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(vis, txt, (12, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                        (255, 255, 255), 1, cv2.LINE_AA)
            if a.dump_png:
                os.makedirs(os.path.dirname(a.dump_png) or ".", exist_ok=True)
                cv2.imwrite(a.dump_png, vis)
                print("EVT_PROXY_DUMP %s" % a.dump_png)
        if ball and ball.get("space") == "field":
            n_ball_field += 1
        if ev.get("possession") is not None:
            n_poss += 1
            if is_det:
                n_poss_det += 1
            if best_poss is None or ev["possession_dist"] < best_poss["dist"]:
                best_poss = {"frame": idx, "dist": ev["possession_dist"],
                             "pid": ev["possession"], "ball": (ball["x"], ball["y"])}
            ev_rows.append({"frame": idx, "possession": ev["possession"],
                            "dist": ev["possession_dist"], "ball": (ball["x"], ball["y"])})
        if ev.get("out_of_play") or ev.get("goal"):
            n_oop += 1 if ev.get("out_of_play") else 0
            n_goal += 1 if ev.get("goal") else 0
            ev_rows.append({"frame": idx, "out_of_play": ev.get("out_of_play"),
                            "goal": ev.get("goal"), "side": ev.get("out_side"),
                            "ball": (ball.get("x"), ball.get("y"))})
        idx += 1

    cap.release()
    res = {"video": a.video, "calib": a.calib, "every": a.every,
           "ball_every": a.ball_every, "max_det": a.max_det,
           "frames": n_frames, "ball_scans": n_ball_scan, "ball_scans_hit": n_ball_hit,
           "ball_field_frames": n_ball_field, "possession_frames": n_poss, "possession_on_det_frames": n_poss_det,
           "out_of_play_frames": n_oop, "goal_frames": n_goal,
           "best_possession": best_poss, "events": ev_rows[:40],
           "note": "主机代理：球员检测为 yolov8n（板上是 person_v8n25），只作预筛"}
    print("EVT_PROXY frames=%d ball_scan=%d hit=%d ball_field=%d possession=%d (det=%d) oop=%d goal=%d"
          % (n_frames, n_ball_scan, n_ball_hit, n_ball_field, n_poss, n_poss_det, n_oop, n_goal))
    print("EVT_PROXY_BEST %s" % json.dumps(best_poss, ensure_ascii=False))
    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        json.dump(res, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("EVT_PROXY_OUT %s" % a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
