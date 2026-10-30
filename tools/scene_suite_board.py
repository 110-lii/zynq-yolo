#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scene_suite_board.py —— 把分层测试集（争抢 / 普通）拉到板上跑一遍。

为什么要单独一个工具
--------------------
`check_scene_suite.py` 在主机上量的是 **YOLOv8n(PC)** 的表现；板上跑的是
**ncnn + PL 预处理**的另一条链路。主机上的数字不能直接代表板上。
分层测试集的意义就在于：如果板端在 `contest` 上塌得比 `normal` 多，那说明
「难画面」确实被切出来了；如果两层一样，说明分层没切到点子上 —— 两种情况都是结论。

★ 板子会掉线。所以每跑完一段就把结果**立刻写盘**，并支持 `--resume`：
  中断后重跑只会补没跑完的段，不会从头再来。

用法
----
    py tools/scene_suite_board.py --clips c01,n01          # 先小范围试
    py tools/scene_suite_board.py                          # 全量 10 段
    py tools/scene_suite_board.py --resume                 # 接着上次

判据令牌
--------
    SCENE_BOARD_ROW <id> <kind> frames=<n> det=<n> infer_ms=<f> rc=<n>
    SCENE_BOARD_AGG contest=<...> normal=<...>
    SCENE_BOARD_OK / SCENE_BOARD_FAIL <reason>
"""
import argparse
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PY = sys.executable

BOARD_IP, BOARD_PORT = "192.168.2.99", 9090
BOARD_ROOT = "/home/xilinx/zynq-yolo"
CLIP_DIR = BOARD_ROOT + "/data/samples/scenes"
OUT_LOCAL = ROOT / "data" / "results" / "scene_suite_board.json"


def board_up(timeout=2.5):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((BOARD_IP, BOARD_PORT))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _env():
    import os
    return dict(os.environ, PYTHONIOENCODING="utf-8")


def board_push(local, remote, timeout=900):
    p = subprocess.run([PY, str(HERE / "board_jupyter.py"), "--push", str(local), remote],
                       cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, env=_env(), encoding="utf-8", errors="replace",
                       timeout=timeout)
    lines = [ln for ln in (p.stdout or "").strip().splitlines() if ln.strip()]
    return p.returncode, lines[-1] if lines else ""


def run_board(argv, timeout=900):
    """Jupyter 内核不能吃 shell 串 —— 一律走 subprocess.run。"""
    code = ("import subprocess\n"
            "p = subprocess.run(%r, cwd=%r, capture_output=True, text=True)\n"
            "print(p.stdout[-4000:])\n"
            "print('STDERR>', p.stderr[-1200:])\n"
            "print('RC', p.returncode)\n") % (argv, BOARD_ROOT)
    try:
        p = subprocess.run([PY, str(HERE / "board_jupyter.py")], cwd=str(ROOT),
                           input=code, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, env=_env(), encoding="utf-8", errors="replace",
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, ["timeout after %ss" % timeout]
    return p.returncode, [ln for ln in (p.stdout or "").strip().splitlines() if ln.strip()]


def kind_of(name):
    return "contest" if Path(name).name[:1] == "c" else (
        "normal" if Path(name).name[:1] == "n" else "unknown")


def probe_deps():
    """板上要有的东西：ncnn 推理程序 + 模型。缺了就 SKIP，不要假装跑过。"""
    rc, out = run_board(["bash", "-lc",
                         "for f in /home/xilinx/ncnn_infer /home/xilinx/person_v8n25.param "
                         "/home/xilinx/person_v8n25.bin; do "
                         "[ -e $f ] && echo HAVE $f || echo MISS $f; done"], timeout=120)
    have = [ln for ln in out if ln.startswith("HAVE")]
    miss = [ln for ln in out if ln.startswith("MISS")]
    return (len(miss) == 0), have + miss


def parse_ok(out):
    for ln in out:
        if ln.startswith("PIPELINE_OK"):
            d = {}
            for tok in ln.split()[1:]:
                if "=" in tok:
                    k, v = tok.split("=", 1)
                    d[k] = v
            return d
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(ROOT / "data" / "samples" / "scenes"))
    ap.add_argument("--clips", default="", help="只跑这些 id（逗号分隔）")
    ap.add_argument("--max-frames", type=int, default=60,
                    help="每段跑多少帧（默认 60 ≈ 10 s 片段过半）")
    ap.add_argument("--out", default=str(OUT_LOCAL))
    ap.add_argument("--resume", action="store_true", help="跳过已完成的段")
    args = ap.parse_args()

    if not board_up():
        print("SCENE_BOARD_FAIL board_offline（Jupyter %s:%d 不通；AGENTS.md：离线不做硬件验证）"
              % (BOARD_IP, BOARD_PORT))
        return 2

    d = Path(args.dir)
    clips = sorted(d.glob("*.mp4"))
    if args.clips:
        want = [s.strip() for s in args.clips.split(",") if s.strip()]
        clips = [c for c in clips if any(c.name.startswith(w) for w in want)]
    if not clips:
        print("SCENE_BOARD_FAIL no_clips %s（先跑 tools/split_scenes.py --extract）" % d)
        return 2

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    done = {}
    if args.resume and out_path.is_file():
        try:
            for r in json.loads(out_path.read_text(encoding="utf-8")).get("rows", []):
                done[r["clip"]] = r
        except Exception:                                    # noqa: BLE001
            pass

    ok, deps = probe_deps()
    for ln in deps:
        print("SCENE_BOARD_DEP %s" % ln)
    if not ok:
        print("SCENE_BOARD_FAIL missing_board_deps（板上缺 ncnn 推理程序或模型，未跑）")
        return 2

    run_board(["bash", "-lc", "mkdir -p %s" % CLIP_DIR], timeout=120)

    rows = list(done.values())
    for clip in clips:
        if clip.name in done:
            print("SCENE_BOARD_SKIP %s (already done)" % clip.name)
            continue
        if not board_up():
            print("SCENE_BOARD_FAIL board_offline_during_run at=%s" % clip.name)
            break
        rc, msg = board_push(clip, CLIP_DIR + "/" + clip.name)
        if rc != 0 or "BOARD_PUSH" not in msg:
            print("SCENE_BOARD_ROW %-8s %-7s push_failed %s"
                  % (clip.stem[:8], kind_of(clip.name), msg[:80]))
            continue
        argv = ["python3", "tools/board_pipeline.py",
                "--video", CLIP_DIR + "/" + clip.name,
                "--max-frames", str(args.max_frames)]
        t0 = time.time()
        rc, out = run_board(argv, timeout=1200)
        dok = parse_ok(out)
        rec = {"clip": clip.name, "kind": kind_of(clip.name), "rc": rc,
               "elapsed_s": round(time.time() - t0, 1)}
        if dok:
            rec.update({"frames": int(dok.get("frames", 0)),
                        "detections": int(dok.get("detections", 0)),
                        "med_infer_ms": float(dok.get("med_infer_ms", 0) or 0)})
            print("SCENE_BOARD_ROW %-8s %-7s frames=%d det=%d infer_ms=%.1f"
                  % (clip.stem[:8], rec["kind"], rec["frames"], rec["detections"],
                     rec["med_infer_ms"]))
        else:
            rec["error"] = (out[-1] if out else "no output")[:160]
            print("SCENE_BOARD_ROW %-8s %-7s FAILED rc=%d %s"
                  % (clip.stem[:8], rec["kind"], rc, rec["error"]))
        rows.append(rec)
        # ★ 每段立即落盘：板子说掉就掉，不能把已跑的结果留在内存里
        out_path.write_text(json.dumps({"max_frames": args.max_frames, "rows": rows},
                                       ensure_ascii=False, indent=1), encoding="utf-8")

    agg = {}
    for k in ("contest", "normal"):
        sel = [r for r in rows if r["kind"] == k and "frames" in r]
        if sel:
            agg[k] = {"n": len(sel),
                      "det_per_frame": round(sum(r["detections"] for r in sel) /
                                             max(1, sum(r["frames"] for r in sel)), 2),
                      "infer_ms": round(sum(r["med_infer_ms"] for r in sel) / len(sel), 1)}
    print("SCENE_BOARD_AGG %s" % json.dumps(agg, ensure_ascii=False))
    if len(agg) < 2:
        print("SCENE_BOARD_FAIL need_both_kinds（已得 %s）" % (",".join(agg) or "-"))
        return 1
    print("SCENE_BOARD_OK clips=%d json=%s" % (len([r for r in rows if "frames" in r]),
                                               out_path.name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
