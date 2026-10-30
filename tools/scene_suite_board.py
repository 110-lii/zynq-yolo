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
    SCENE_BOARD_ROW <id> <kind> frames=<n> det=<n> players=<f> ball_pct=<f> infer_ms=<f>
    SCENE_BOARD_AGG contest={players,ball_pct,infer_ms,n} normal={...}
    SCENE_BOARD_OK / SCENE_BOARD_FAIL <reason>

★ `det=` 是**检测调用次数**（`--max-det` 封顶），不是人数。要比「每帧多少人」看 `players=`。
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


def run_code(code, timeout=900):
    """在板上跑一段 Python 代码，返回输出的行。

    为什么不用 argv：跑完 pipeline 还得读它写的 JSON 算统计量，
    两件事塞进同一个内核调用能省一次往返 —— 板子会掉线，往返次数就是风险。
    """
    try:
        p = subprocess.run([PY, str(HERE / "board_jupyter.py")], cwd=str(ROOT),
                           input=code, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, env=_env(), encoding="utf-8", errors="replace",
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, ["timeout after %ss" % timeout]
    return p.returncode, [ln for ln in (p.stdout or "").strip().splitlines() if ln.strip()]


#: 板端「跑 pipeline → 读它写的 JSON → 算每帧人数/球可见率」的一段代码。
#: 两件事必须在同一个内核调用里做完：多一次往返，就多一次板子掉线的机会。
#: 用 __占位符__ 替换而不是 %-格式化 —— 这段代码自己就带 `%d`。
STAT_CODE = """
import subprocess, json
p = subprocess.run(__ARGV__, cwd=__ROOT__, capture_output=True, text=True)
print(p.stdout[-1500:])
print('STDERR>', p.stderr[-600:])
print('RC', p.returncode)
try:
    d = json.load(open(__OUT__))
    fr = d.get('frames_out') or []
    pl = [len(f.get('tracks') or []) for f in fr]
    print('SUITE_STAT frames=%d det=%d raw=%d kept=%d rows=%d rowsok=%d players=%.2f ball_pct=%s' % (
        d.get('frames', 0), d.get('detections', 0),
        d.get('raw_dets') or 0, d.get('kept_dets') or 0,
        d.get('rows_pre_filter') or 0, d.get('rows_post_filter') or 0,
        (sum(pl) / len(pl)) if pl else 0.0,
        d.get('ball_pct') if d.get('ball_pct') is not None else -1.0))
except Exception as e:
    print('SUITE_STAT_ERR', e)
"""


def stat_code(argv, out_json):
    return (STAT_CODE.replace("__ARGV__", repr(argv))
            .replace("__ROOT__", repr(BOARD_ROOT))
            .replace("__OUT__", repr(out_json)))


def kind_of(name):
    return "contest" if Path(name).name[:1] == "c" else (
        "normal" if Path(name).name[:1] == "n" else "unknown")


NO_BIN = "/home/xilinx/ncnn_infer"
#: 模型优先用**仓库里那一份**（deploy 会推过去），只有仓库里没有时才退回 /home/xilinx。
#: 为什么：板子每次重启后 /home/xilinx 下的临时件不一定还在，仓库副本才是可复现的那个。
PARAM = BOARD_ROOT + "/build/models/person_v8n25.param"
MODEL = BOARD_ROOT + "/build/models/person_v8n25.bin"


def probe_deps():
    """板上要有的东西：ncnn 推理程序 + 模型。缺了就 SKIP，不要假装跑过。"""
    rc, out = run_board(["bash", "-lc",
                         "for f in %s %s %s; do "
                         "[ -e $f ] && echo HAVE $f || echo MISS $f; done"
                         % (NO_BIN, PARAM, MODEL)], timeout=120)
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
    ap.add_argument("--every", type=int, default=15,
                    help="每 N 帧检测一次（传给 board_pipeline）。板子会掉线，"
                         "用 --every 10 --max-frames 200 换到约 20 次检测/段，比 5 帧/段稳")
    ap.add_argument("--no-pl", action="store_true",
                    help="禁用 PL 预处理（板端没有 preproc.bit 时用；慢但结果可比）")
    ap.add_argument("--no-push", action="store_true",
                    help="假设片段已经在板上（run_board_acceptance 已带 sha256 推过），不再重复传")
    ap.add_argument("--clip-timeout", type=float, default=300.0,
                    help="单段超时（秒）。超时按板子卡死处理并中止本轮，默认 300")
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
                # ★ 只认**按新口径跑成功**的段：既有 frames、又有 players。
                #   失败行不能当「已完成」，否则 --resume 会把上一轮失败的段全跳过、
                #   永远补不上（2026-10-30 踩过：c01 因 ModuleNotFoundError 记了失败行）。
                #   同理，`--max-det 8` 那一轮的旧行虽然 `frames` 齐，但 `det=8` 只是
                #   「检测调用次数」，人数/球可见率都没算 —— 不给它发「已完成」证。
                if "frames" in r and "players" in r:
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
        if not args.no_push:
            rc, msg = board_push(clip, CLIP_DIR + "/" + clip.name)
            if rc != 0 or "BOARD_PUSH" not in msg:
                print("SCENE_BOARD_ROW %-8s %-7s push_failed %s"
                      % (clip.stem[:8], kind_of(clip.name), msg[:80]))
                continue
        out_json = "/home/xilinx/scene_%s.json" % clip.stem.split("_")[0]
        argv = ["python3", "tools/board_pipeline.py",
                "--video", CLIP_DIR + "/" + clip.name,
                "--max-frames", str(args.max_frames),
                "--every", str(args.every),
                # ★ --max-det 是「检测调用次数上限」，默认 8 会让每段都只检 8 次，
                #   出来的 det=8 对所有片段都一样、毫无信息量（2026-10-30 踩过）。
                #   0 = 不限，按 --every 的节奏逐次检测。
                "--max-det", "0",
                "--param", PARAM, "--model", MODEL,
                "--out", out_json]
        if args.no_pl:
            argv.append("--no-pl")
        t0 = time.time()
        # 单段超时 = 实测耗时 × 3 左右。2026-10-30 有一段卡了十几分钟才发现：
        # 板子卡死时 TCP 9090 仍然可以 accept，只有内核/HTTP 不再应答 ——
        # 光用 board_up() 判断会把「卡死」当成「在线」，于是一直干等。
        rc, out = run_code(stat_code(argv, out_json), timeout=args.clip_timeout)
        if rc == 124:
            print("SCENE_BOARD_FAIL board_wedged at=%s（%ss 无应答，按离线处理）"
                  % (clip.name, args.clip_timeout))
            break
        dok = parse_ok(out)
        stat = {}
        for ln in out:
            if ln.startswith("SUITE_STAT "):
                for tok in ln.split()[1:]:
                    if "=" in tok:
                        k, v = tok.split("=", 1)
                        try:
                            stat[k] = float(v)
                        except ValueError:
                            pass
        rec = {"clip": clip.name, "kind": kind_of(clip.name), "rc": rc,
               "elapsed_s": round(time.time() - t0, 1)}
        rec.update(stat)
        if dok:
            rec.update({"frames": int(dok.get("frames", 0)),
                        "detections": int(dok.get("detections", 0)),
                        "med_infer_ms": float(dok.get("med_infer_ms", 0) or 0)})
            print("SCENE_BOARD_ROW %-8s %-7s frames=%d det=%d raw=%.0f kept=%.0f"
                  " rows=%.0f->%.0f players=%.2f ball_pct=%.1f infer_ms=%.1f"
                  % (clip.stem[:8], rec["kind"], rec["frames"], rec["detections"],
                     rec.get("raw", 0.0), rec.get("kept", 0.0),
                     rec.get("rows", 0.0), rec.get("rowsok", 0.0),
                     rec.get("players", 0.0), rec.get("ball_pct", 0.0), rec["med_infer_ms"]))
        else:
            rec["error"] = (out[-1] if out else "no output")[:160]
            print("SCENE_BOARD_ROW %-8s %-7s FAILED rc=%d %s"
                  % (clip.stem[:8], rec["kind"], rc, rec["error"]))
        rows.append(rec)
        # ★ 每段立即落盘：板子说掉就掉，不能把已跑的结果留在内存里
        out_path.write_text(json.dumps({"max_frames": args.max_frames, "rows": rows},
                                       ensure_ascii=False, indent=1), encoding="utf-8")

    # 聚合只报「和主机跑表同口径」的量：每帧人数、球可见率、单帧推理耗时。
    # det（检测调用次数）*不是*人数，别混进来 —— 2026-10-30 就在这上面绕了一轮。
    agg = {}
    for k in ("contest", "normal"):
        sel = [r for r in rows if r["kind"] == k and "frames" in r]
        if sel:
            have_pl = [r for r in sel if "players" in r]
            have_ba = [r for r in have_pl if r.get("ball_pct", -1) >= 0]
            n_det = max(1.0, sum(r["detections"] for r in sel))
            agg[k] = {"n": len(sel),
                      # 模型每给一个框都要过草皮门限；两段数字要并排报，
                      # 否则「板上 0 人」说不清是模型没看见还是后处理杀光了。
                      "raw_per_det": round(sum(r.get("raw", 0) for r in sel) / n_det, 2),
                      "kept_per_det": round(sum(r.get("kept", 0) for r in sel) / n_det, 2),
                      "rows_post_per_det": round(sum(r.get("rowsok", 0) for r in sel) / n_det, 2),
                      "players": round(sum(r["players"] for r in have_pl) / len(have_pl), 2)
                      if have_pl else None,
                      "ball_pct": round(sum(r["ball_pct"] for r in have_ba) / len(have_ba), 1)
                      if have_ba else None,
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
