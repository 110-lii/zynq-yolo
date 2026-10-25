"""board_pipeline.py —— 板上：视频 -> 稀疏检测(ncnn) -> SORT 跟踪 -> 每帧输出

检测每 N 帧跑一次（默认 15 帧 ≈ 1.1 s @14fps），其余帧用跟踪器外推。
输出 JSON：每帧的 tracks（id + 原图坐标框），以及检测次数/耗时统计。

用法（板上）：
    python3 tools/board_pipeline.py --video /home/xilinx/inc.mp4 --every 15 --max-det 8 \
        --out /home/xilinx/tracks.json
"""
import argparse
import json
import os
import tempfile
import time
import statistics
import subprocess
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, "tools")
from board_track import MultiTracker  # noqa: E402
from field_coords import box_to_field, inside_hull  # noqa: E402
from result_publisher import build_frame  # noqa: E402
from camera_state import assess as cam_assess  # noqa: E402
from ball_cv_probe import candidates as ball_cands  # noqa: E402
from ball_track import BallTracker  # noqa: E402
from board_detect import detect as bd_detect, init_pl  # noqa: E402
from ball_cls_np import BallClassifier  # noqa: E402


BALL_CLS_BIN = "/home/xilinx/ball_cls_c"


def cls_scores(patches):
    """批量调 C 分类器（32x32），返回概率列表。"""
    if not patches:
        return []
    arr = np.stack([cv2.cvtColor(cv2.resize(p, (32, 32), interpolation=cv2.INTER_AREA),
                                 cv2.COLOR_BGR2RGB) for p in patches]).astype(np.uint8)
    fd, raw = tempfile.mkstemp(suffix=".raw")
    os.close(fd)
    try:
        arr.tofile(raw)
        r = subprocess.run([BALL_CLS_BIN, raw, str(len(patches))],
                           capture_output=True, text=True, timeout=30)
    finally:
        try:
            os.unlink(raw)
        except OSError:
            pass
    out = []
    for line in r.stdout.splitlines():
        t = line.split()
        if len(t) == 3 and t[0] == "SCORE":
            out.append(float(t[2]))
    return out


def slide_ball(frame, clf, th=0.70, stride=16, min_d=40.0):
    """单帧滑窗找球：640x360 上 32x32 窗口，按分数做距离抑制。"""
    small = cv2.resize(frame, (640, 360))
    cands = []
    for y in range(0, 360 - 32, stride):
        for x in range(0, 640 - 32, stride):
            p = clf.prob(small[y:y + 32, x:x + 32])
            if p >= th:
                cands.append({"x": (x + 16) * 2.0, "y": (y + 16) * 2.0, "score": p})
    cands.sort(key=lambda c: -c["score"])
    keep = []
    for c in cands:
        if all((c["x"] - k["x"]) ** 2 + (c["y"] - k["y"]) ** 2 > min_d ** 2 for k in keep):
            keep.append(c)
    return keep

USE_PL = False
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _is_grass(frame, u, v):
    h, w = frame.shape[:2]
    x0, x1 = max(0, int(u) - 3), min(w, int(u) + 4)
    y0, y1 = max(0, int(v) - 3), min(h, int(v) + 4)
    if x1 <= x0 or y1 <= y0:
        return False
    hsv = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
    H, S, V = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    g = ((H >= 30) & (H <= 90) & (S >= 50) & (V >= 40))
    return float(g.mean()) >= 0.4


def detect(image_path, bin_path, param, model, conf, iou):
    return bd_detect(image_path, bin_path, param, model, conf, iou,
                     use_pl=USE_PL, repo=REPO)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--every", type=int, default=15)
    ap.add_argument("--max-det", type=int, default=8)
    ap.add_argument("--max-frames", type=int, default=0)
    ap.add_argument("--bin", default="/home/xilinx/ncnn_infer")
    ap.add_argument("--param", default="/home/xilinx/person_v8n25.param")
    ap.add_argument("--model", default="/home/xilinx/person_v8n25.bin")
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--iou", type=float, default=0.45)
    ap.add_argument("--calib", default="", help="field_coords.py 生成的 calib.json")
    ap.add_argument("--pl", action="store_true", default=None, help="强制启用 PL preproc")
    ap.add_argument("--no-pl", action="store_true", help="禁用 PL preproc")
    ap.add_argument("--ball-scan", action="store_true", help="用单帧滑窗找球（慢但召回高）")
    ap.add_argument("--ball-every", type=int, default=0, help="球路径独立节奏（0=跟随主检测）")
    ap.add_argument("--proposal", action="store_true", help="额外输出方案 §2.1 的 JSON 结构")
    ap.add_argument("--out", default="/home/xilinx/tracks.json")
    args = ap.parse_args()
    global USE_PL
    _has_pl = os.path.exists(os.path.join(REPO, "board", "overlay", "preproc.bit"))
    USE_PL = (args.pl is True) or (args.pl is None and _has_pl and not args.no_pl)
    if USE_PL:
        try:
            init_pl(REPO)
            print("PL_PREPROC enabled")
        except Exception as _e:      # 没 pynq / overlay 不存在时自动回退
            print("PL_PREPROC unavailable:", _e)
            USE_PL = False

    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        print("PIPELINE_FAIL video_open")
        return 1

    H = None
    hull = None
    if args.calib:
        _c = json.load(open(args.calib, encoding="utf-8"))
        H = _c["H"]
        hull = _c.get("image")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    hits = {}
    prop = []
    tr = MultiTracker(iou_th=0.3, max_missed=max(args.every * 2, 10), min_hits=2)
    ball_clf = None
    _npz = os.path.join(REPO, "build", "models", "ball_roi.npz")
    if os.path.exists(_npz):
        ball_clf = BallClassifier(_npz)
    ball_tr = BallTracker(min_hits=2, max_miss=4, min_speed=3.0)
    ball = None
    prev_frame = None
    t_det = t_ball = 0.0
    ball_roi = None
    roi_miss = 0
    last_ball = None   # (x, y, vx, vy, hits)
    frames = []
    n_det = 0
    infer_ms = []
    idx = 0
    tmp = "/tmp/_frame.jpg"
    prev_gray = None
    sx_ratio = 1.0
    dx = dy = 0.0
    cam_ok, cam_state, cam_reason = True, "LIMITED_FIELD_VIEW", "init"

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if args.max_frames and idx >= args.max_frames:
            break
        small = cv2.resize(frame, (320, 180))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)
        sx_ratio = frame.shape[1] / 320.0
        if prev_gray is not None:
            (sx, sy), _resp = cv2.phaseCorrelate(prev_gray, gray)
            dx, dy = sx * sx_ratio, sy * (frame.shape[0] / 180.0)
        else:
            dx = dy = 0.0
        prev_gray = gray
        is_det = (idx % args.every == 0) and (args.max_det == 0 or n_det < args.max_det)
        if is_det:
            cv2.imwrite(tmp, frame)
            _t0 = time.perf_counter()
            dets, ms = detect(tmp, args.bin, args.param, args.model, args.conf, args.iou)
            t_det += time.perf_counter() - _t0
            dets = [d for d in dets if _is_grass(frame, (d[0] + d[2]) / 2.0, d[3])]
            tracks = tr.step(dets, dx, dy, dt=args.every)
            n_det += 1
            if ms:
                infer_ms.append(ms)
        else:
            tracks = tr.predict(dx, dy)
        rows = []
        for t in tracks:
            row = [t[0], round(t[1], 1), round(t[2], 1), round(t[3], 1), round(t[4], 1),
                   round(t[5], 3)]
            if H is not None:
                u = (t[1] + t[3]) * 0.5
                v = t[4]
                if hull is None or inside_hull(hull, u, v):
                    f = box_to_field(H, t[1:5])
                    row += [None, None] if f is None else [round(f[0], 2), round(f[1], 2)]
                else:
                    row += [None, None]
            rows.append(row)
        if is_det:
            for t in tracks:
                hits[t[0]] = hits.get(t[0], 0) + 1
        _h = frame.shape[0]
        rows = [r for r in rows
                if r[5] >= 0.30 and (r[4] - r[2]) >= max(16.0, 0.02 * _h)]
        rows.sort(key=lambda r: (r[5], hits.get(r[0], 1)), reverse=True)
        rows = rows[:25]
        if is_det:
            hsv = cv2.cvtColor(cv2.resize(frame, (160, 90)), cv2.COLOR_BGR2HSV)
            gf = float(((hsv[..., 0] >= 30) & (hsv[..., 0] <= 90) &
                        (hsv[..., 1] >= 50) & (hsv[..., 2] >= 35)).mean())
            n_field = sum(1 for r in rows if len(r) > 7 and r[6] is not None)
            cam_ok, cam_state, cam_reason = cam_assess(gf, len(rows), n_field,
                                                       (dx * dx + dy * dy) ** 0.5)

            bdiff = None
            if prev_frame is not None:
                g1 = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                g0 = cv2.cvtColor(prev_frame, cv2.COLOR_BGR2GRAY)
                bdiff = cv2.GaussianBlur(cv2.absdiff(g1, g0), (3, 3), 0)
        _do_ball = is_det if args.ball_every <= 0 else (idx % max(1, args.ball_every) == 0)
        _tb = time.perf_counter()
        bt = []
        if _do_ball and args.ball_scan and ball_clf is not None:
            bc = slide_ball(frame, ball_clf)          # 已自带分数与距离抑制
        elif _do_ball:
            if prev_frame is not None:
                g1 = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                g0 = cv2.cvtColor(prev_frame, cv2.COLOR_BGR2GRAY)
                bdiff = cv2.GaussianBlur(cv2.absdiff(g1, g0), (3, 3), 0)
            bc = ball_cands(frame, None, roi=ball_roi)   # 全分辨率：降采样会显著增加误检
            if os.path.exists(BALL_CLS_BIN) and bc:
                pat, sel = [], []
                for j, c in enumerate(bc):
                    x, y = int(c["x"]), int(c["y"])
                    x0, y0 = max(0, x - 32), max(0, y - 32)
                    patch = frame[y0:y0 + 64, x0:x0 + 64]
                    if patch.shape[0] != 64 or patch.shape[1] != 64:
                        continue
                    pat.append(patch)
                    sel.append(j)
                sc = cls_scores(pat)
                bc = [bc[j] for j, s in zip(sel, sc) if s >= 0.50]
        bt = ball_tr.step(bc, dt=(args.ball_every if args.ball_every > 0 else 1.0))
        if bt:
            b = max(bt, key=lambda t: t.hits)
            conf = round(min(1.0, b.hits / 5.0), 2)
            placed = False
            if H is not None:
                f_xy = box_to_field(H, [b.x - 1, b.y - 1, b.x + 1, b.y + 1])
                if f_xy and -5 <= f_xy[0] <= 110 and -5 <= f_xy[1] <= 73:
                    ball = {"x": round(f_xy[0], 2), "y": round(f_xy[1], 2),
                            "confidence": conf, "valid": True, "space": "field"}
                    placed = True
            if not placed:
                ball = {"x": round(b.x, 1), "y": round(b.y, 1),
                        "confidence": conf, "valid": True, "space": "image"}
        prev_frame = frame
        if not bt:
            roi_miss += 1
            if roi_miss > 25:
                ball_roi = None
        t_ball += time.perf_counter() - _tb
        if cam_state == "INVALID_VIEW":
            rows = []              # 镜头无效（特写/回放）：不输出坐标，避免错误数据
        keep_ids = {r[0] for r in rows}
        tracks = [t for t in tracks if t[0] in keep_ids]
        frames.append({"frame": idx, "detected": is_det, "tracks": rows})
        if args.proposal:
            _f = build_frame(int(round(idx * 1000.0 / fps)), rows, hits=hits,
                             camera_valid=cam_ok, ball=ball)
            _f["camera_state"] = cam_state
            _f["camera_reason"] = cam_reason
            prop.append(_f)
        idx += 1

    cap.release()
    out = {
        "video": args.video, "frames": idx, "detections": n_det,
        "detect_every": args.every,
        "infer_ms_med": statistics.median(infer_ms) if infer_ms else None,
        "frames_out": frames,
        "proposal": prop,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print("STAGES detect_ms=%.0f ball_ms=%.0f frames=%d" % (t_det * 1e3, t_ball * 1e3, idx))
    print("PIPELINE_OK frames=%d detections=%d med_infer_ms=%s out=%s"
          % (idx, n_det, out["infer_ms_med"], args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())