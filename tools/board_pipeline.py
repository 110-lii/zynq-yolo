"""board_pipeline.py —— 板上：视频 -> 稀疏检测(ncnn) -> SORT 跟踪 -> 每帧输出

检测每 N 帧跑一次（默认 15 帧 ≈ 1.1 s @14fps），其余帧用跟踪器外推。
输出 JSON：每帧的 tracks（id + 原图坐标框），以及检测次数/耗时统计。

证据帧（为什么）
----------------
JSONL 只留坐标，事后无法自证「第 N 帧确实出界/确实有人倒地」。
`--store-evidence` 把触发事件的那一帧原图存进 `<store-dir>/evidence/`，
JSONL 同步多一条 type=evidence 行，复盘时点一下就能看到当时的画面。
* 出界（events.out_of_play=true）→ tag=out；
* 倒地候选（框变扁：宽>1.15*高 且 高<0.70*该轨迹历史中位高）→ tag=fall；
* 限流：每分钟最多 `--store-evidence-rate` 张（默认 6），超出计入 summary 的
  `evidence.skipped`，不写盘、不抛异常（防一人倒地刷出上百张图）。
* 注意：`out_of_play` 来自 events（只在 `--proposal` 模式下计算），所以不开
  `--proposal` 时只有 fall 证据帧。

用法（板上）：
    python3 tools/board_pipeline.py --video /home/xilinx/inc.mp4 --every 15 --max-det 8 \
        --out /home/xilinx/tracks.json
    # 边跑边往 SD 卡存帧+证据帧（每分钟最多 6 张）
    python3 tools/board_pipeline.py --video ... --proposal \
        --store-dir /mnt/sd/zynq-yolo --store-evidence
判据令牌：末行 PIPELINE_OK；summary.json 里 evidence.written/skipped/errors。
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
from ball_policy import place_ball, resolve_max_miss  # noqa: E402
from ball_track import BallTracker  # noqa: E402
from board_detect import detect as bd_detect, detect_planar as bd_detect_planar, init_pl  # noqa: E402
from ball_cls_np import BallClassifier  # noqa: E402
from track_preproc import TrackPreproc  # noqa: E402
from result_store import ResultStore  # noqa: E402


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
TRACK = None
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _motion_frac(mask, track, frame_shape):
    """track=[id,x1,y1,x2,y2,...]；mask 是 320x320 的 bit1=运动。"""
    if mask is None:
        return 0.0
    h, w = frame_shape[:2]
    x1, y1, x2, y2 = track[1:5]
    mx0 = max(0, int(x1 * 320.0 / w))
    mx1 = min(319, int(x2 * 320.0 / w))
    my0 = max(0, int(y1 * 320.0 / h))
    my1 = min(319, int(y2 * 320.0 / h))
    if mx1 <= mx0 or my1 <= my0:
        return 0.0
    roi = mask[my0:my1 + 1, mx0:mx1 + 1] & 2
    return float(roi.mean() / 2.0) if roi.size else 0.0


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


# ---------- 证据帧：倒地候选判定 ----------
# 与 tools/fast_events.py 同口径（几何判据，不做姿态估计）：
#   人直立时框高 > 框宽；倒地时框变扁（宽>1.15*高）且因透视/遮挡比自身历史身高明显矮。
FALL_ASPECT = 1.15       # 框宽 > 1.15 * 框高
FALL_SHRINK = 0.70       # 框高 < 0.70 * 该轨迹历史中位高
FALL_HIST = 30           # 每条轨迹保留的最近框高样本数
FALL_MIN_SAMPLES = 3     # 中位高至少要这么多样本才可信，否则不判


def fall_candidates(rows, hist):
    """从本帧球员框判「倒地候选」；hist={track_id: [历史框高]} 就地更新。

    rows: [[id, x1,y1,x2,y2, score, ...], ...]（board_pipeline 的 rows 格式）
    返回候选 track_id 列表。**这是候选，不是检出**——只用于决定要不要留证据帧。
    """
    out = []
    for r in rows:
        try:
            tid = int(r[0])
            w = float(r[3]) - float(r[1])
            h = float(r[4]) - float(r[2])
        except (TypeError, ValueError, IndexError):
            continue
        hs = hist.setdefault(tid, [])
        if len(hs) >= FALL_MIN_SAMPLES and h > 0:
            med = float(np.median(hs))
            if w > FALL_ASPECT * h and h < FALL_SHRINK * med:
                out.append(tid)
        if h > 0:
            hs.append(h)
            if len(hs) > FALL_HIST:
                hs.pop(0)
    return out


def save_evidence_for(store, frame, idx, events, fall_ids, cam_state, ball):
    """按本帧事件存证据帧：出界 tag=out，倒地 tag=fall。

    限流/失败处理全在 ResultStore.save_evidence 里（返回 None，不抛异常），
    所以这里不需要 try/except——写盘失败不会打断主循环。
    """
    if store is None:
        return
    events = events or {}
    if events.get("out_of_play"):
        store.save_evidence(frame, "out", frame=idx, meta={
            "frame": idx, "event": "out_of_play", "side": events.get("out_side"),
            "camera_state": cam_state,
            "ball": None if not ball else {"x": ball.get("x"), "y": ball.get("y")},
        })
    if fall_ids:
        store.save_evidence(frame, "fall", frame=idx, meta={
            "frame": idx, "event": "fall_candidate", "tracks": list(fall_ids),
            "camera_state": cam_state,
        })


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
    ap.add_argument("--pl-track", action="store_true",
                    help="启用融合 PL 预处理+运动掩码（track_preproc.bit）")
    ap.add_argument("--track-bit", default="",
                    help="组合 overlay 的 bit 路径（如 track_display.bit）；空=track_preproc.bit")
    ap.add_argument("--motion-gate", action="store_true",
                    help="用 PL 运动掩码过滤无运动的新轨迹（去静态误检）")
    ap.add_argument("--store-dir", default="",
                    help="SD 卡增量存储目录（如 /mnt/sd/zynq-yolo）；空=不存")
    ap.add_argument("--store-prefix", default="run")
    ap.add_argument("--store-fsync", type=int, default=30, help="每 N 行 fsync")
    ap.add_argument("--store-max-mb", type=int, default=64, help="单文件轮转阈值")
    ap.add_argument("--store-evidence", action="store_true",
                    help="事件帧落盘到 <store-dir>/evidence（出界 tag=out / 倒地 tag=fall）")
    ap.add_argument("--store-evidence-rate", type=int, default=6,
                    help="证据帧每分钟上限（默认 6，防刷屏；0=不限）")
    ap.add_argument("--ball-scan", action="store_true", help="用单帧滑窗找球（慢但召回高）")
    ap.add_argument("--ball-every", type=int, default=0, help="球路径独立节奏（0=跟随主检测）")
    ap.add_argument("--ball-cls-th", type=float, default=0.50,
                    help="球候选的 C 分类器阈值（默认 0.50；调高可压误检，代价是召回）")
    ap.add_argument("--ball-lost-sec", type=float, default=None,
                    help="球丢失容忍窗口（秒，按视频帧换算）；默认 None=沿用 max_miss=4")
    ap.add_argument("--ball-predict-past-boundary", action="store_true",
                    help="预测点（tracker miss>0）允许越过球场范围 ±5 m（默认关）")
    ap.add_argument("--proposal", action="store_true", help="额外输出方案 §2.1 的 JSON 结构")
    ap.add_argument("--out", default="/home/xilinx/tracks.json")
    args = ap.parse_args()
    global USE_PL, TRACK
    _has_pl = os.path.exists(os.path.join(REPO, "board", "overlay", "preproc.bit"))
    USE_PL = (args.pl is True) or (args.pl is None and _has_pl and not args.no_pl)
    if args.pl_track:
        USE_PL = False
        try:
            TRACK = TrackPreproc(REPO, bitfile=(args.track_bit or None))
            print("PL_TRACK enabled")
        except Exception as _e:
            print("PL_TRACK unavailable:", _e)
            TRACK = None
    elif USE_PL:
        try:
            init_pl(REPO)
            print("PL_PREPROC enabled")
        except Exception as _e:      # 没 pynq / overlay 不存在时自动回退
            print("PL_PREPROC unavailable:", _e)
            USE_PL = False

    store = None
    if args.store_dir:
        try:
            store = ResultStore(args.store_dir, prefix=args.store_prefix,
                                fsync_every=args.store_fsync, max_mb=args.store_max_mb,
                                evidence_max_per_min=args.store_evidence_rate)
            print("STORE_DIR", args.store_dir)
        except Exception as _e:
            print("STORE_UNAVAILABLE:", _e)
            store = None
    if args.store_evidence and store is None:
        print("STORE_EVIDENCE_OFF no --store-dir（证据帧无处可写）")
    elif args.store_evidence and not args.proposal:
        print("STORE_EVIDENCE_NOTE 未开 --proposal：events 不生成，本次只有 fall 证据帧")

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
    try:
        _ball_max_miss = resolve_max_miss(args.ball_lost_sec, fps)
    except ValueError as _e:
        print("PIPELINE_FAIL ball_lost_sec:", _e)
        cap.release()
        return 2
    if args.ball_lost_sec is not None:
        print("BALL_LOST_WINDOW max_miss=%d lost_sec=%.3f fps=%.2f"
              % (_ball_max_miss, args.ball_lost_sec, fps))
    ball_tr = BallTracker(min_hits=2, max_miss=_ball_max_miss, min_speed=3.0)
    ball = None
    n_ball_predicted = 0
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
    fall_hist = {}     # track_id -> 最近框高，倒地候选判据用（仅 --store-evidence 时维护）

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
        rgb_planar = None
        mask_small = None
        pl_ms = None
        if TRACK is not None:
            rgb_planar, mask_small, pl_ms = TRACK.run(frame, prev_frame)

        is_det = (idx % args.every == 0) and (args.max_det == 0 or n_det < args.max_det)
        if is_det:
            _t0 = time.perf_counter()
            try:
                if TRACK is not None:
                    dets, ms = bd_detect_planar(rgb_planar, frame.shape[1], frame.shape[0],
                                               args.bin, args.param, args.model,
                                               args.conf, args.iou)
                else:
                    cv2.imwrite(tmp, frame)
                    dets, ms = detect(tmp, args.bin, args.param, args.model, args.conf, args.iou)
            except Exception as _e:
                print("DETECT_FAIL:", _e)
                dets, ms = [], None
            t_det += time.perf_counter() - _t0
            dets = [d for d in dets if _is_grass(frame, (d[0] + d[2]) / 2.0, d[3])]
            tracks = tr.step(dets, dx, dy, dt=args.every)
            if mask_small is not None and args.motion_gate:
                tracks = [t for t in tracks
                          if hits.get(t[0], 0) > 0 or _motion_frac(mask_small, t, frame.shape) >= 0.004]
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
            bdiff = None
            if prev_frame is not None:
                g1 = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                g0 = cv2.cvtColor(prev_frame, cv2.COLOR_BGR2GRAY)
                bdiff = cv2.GaussianBlur(cv2.absdiff(g1, g0), (3, 3), 0)
            bc = ball_cands(frame, bdiff, roi=ball_roi)   # 传帧差：静态白块（线/广告/鞋）在这里被挡掉
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
                bc = [bc[j] for j, s in zip(sel, sc) if s >= args.ball_cls_th]
        bt = ball_tr.step(bc, dt=(args.ball_every if args.ball_every > 0 else 1.0))
        if bt:
            b = max(bt, key=lambda t: t.hits)
            conf = round(min(1.0, b.hits / 5.0), 2)
            f_xy = None
            if H is not None:
                f_xy = box_to_field(H, [b.x - 1, b.y - 1, b.x + 1, b.y + 1])
            ball = place_ball(f_xy, (b.x, b.y), conf, is_predicted=(b.miss > 0),
                              allow_predict_past_boundary=args.ball_predict_past_boundary)
            if ball.get("predicted"):
                n_ball_predicted += 1
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
        # 倒地候选：只在真的要写证据帧时算（否则白维护中位高）
        fall_ids = (fall_candidates(rows, fall_hist)
                    if (args.store_evidence and store is not None) else [])
        rec = {"frame": idx, "detected": is_det, "tracks": rows}
        if pl_ms is not None:
            rec["pl_ms"] = round(pl_ms, 2)
        if mask_small is not None:
            rec["motion"] = round(float((mask_small & 2).mean() / 2.0), 5)
        frames.append(rec)
        ev = None
        if args.proposal:
            _f = build_frame(int(round(idx * 1000.0 / fps)), rows, hits=hits,
                             camera_valid=cam_ok, ball=ball)
            _f["camera_state"] = cam_state
            _f["camera_reason"] = cam_reason
            if pl_ms is not None:
                _f["pl_ms"] = round(pl_ms, 2)
            prop.append(_f)
            ev = _f.get("events") or {}
            if store is not None:
                store.append(dict(_f, type="frame", frame=idx))
                if ev.get("out_of_play") or ev.get("goal"):
                    store.append({"type": "event", "frame": idx, "events": ev,
                                  "camera_state": cam_state})
        elif store is not None:
            store.append(dict(rec, type="frame"))
        # 证据帧：出界/倒地各存当前帧原图（限流与失败处理在 ResultStore 内部）
        if store is not None and args.store_evidence:
            save_evidence_for(store, frame, idx, ev, fall_ids, cam_state, ball)
        idx += 1

    cap.release()
    pl_ms_med = statistics.median([f["pl_ms"] for f in frames if "pl_ms" in f]) if TRACK else None
    out = {
        "video": args.video, "frames": idx, "detections": n_det,
        "detect_every": args.every,
        "infer_ms_med": statistics.median(infer_ms) if infer_ms else None,
        "pl_track": TRACK is not None,
        "pl_ms_med": pl_ms_med,
        "frames_out": frames,
        "proposal": prop,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    if store is not None:
        summary = store.close(summary={
            "video": args.video, "frames": idx, "detections": n_det,
            "pl_track": TRACK is not None, "pl_ms_med": pl_ms_med,
            "store_evidence": bool(args.store_evidence),
        })
        print("STORE_SUMMARY", summary)
        if args.store_evidence:
            _ev = store.evidence_stats()
            print("STORE_EVIDENCE written=%d skipped=%d errors=%d max_per_min=%d"
                  % (_ev["written"], _ev["skipped"], _ev["errors"], _ev["max_per_min"]))
            if _ev["last_error"]:
                print("STORE_EVIDENCE_LAST_ERROR", _ev["last_error"])
    print("STAGES detect_ms=%.0f ball_ms=%.0f frames=%d pl_ms_med=%s"
          % (t_det * 1e3, t_ball * 1e3, idx, pl_ms_med))
    if args.ball_predict_past_boundary:
        print("BALL_PREDICT_PAST_BOUNDARY frames=%d" % n_ball_predicted)
    print("PIPELINE_OK frames=%d detections=%d med_infer_ms=%s out=%s"
          % (idx, n_det, out["infer_ms_med"], args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())