#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""board_recover_probe.py —— 板子一活过来，自动做三件事并留档。

为什么单独写：2026-10-30 晚上板子每 5–15 分钟断一次，人工盯着不现实。
这个脚本只用管一件事 —— **在一次性窗口里把能拿的证据都拿到**：

    1) 诊断：ncnn_infer 到底还跑不跑（含 OMP_NUM_THREADS=1）
    2) 补跑 n08（分层跑表 16/16 的最后一段）
    3) 标定实验：c01 带 --calib 跑一遍，看 camera_state 分布变不变

不做任何写盘以外的事，不进仓库（build/ 是产物目录）。
用法：py build/board_recover_probe.py --wait 5400
"""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import run_board_acceptance as r  # noqa: E402

LOG = ROOT / "build" / "recover_probe.log"
PY = sys.executable


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def board(code, timeout=600):
    p = subprocess.run([PY, str(ROOT / "tools" / "board_jupyter.py")], cwd=str(ROOT),
                       input=code, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return [ln for ln in (p.stdout or "").strip().splitlines() if ln.strip()]


DIAG = r'''
import subprocess, os, hashlib
P = "/home/xilinx/person_v8n25.param"; M = "/home/xilinx/person_v8n25.bin"
def one(tag, env=None):
    p = subprocess.run(["/home/xilinx/ncnn_infer", P, M, "/tmp/z.rgb", "0.25", "0.45", "0"],
                       capture_output=True, text=True, env=env)
    print("DIAG", tag, "rc", p.returncode, (p.stdout or "").strip().splitlines()[:2])
one("default")
one("omp1", dict(os.environ, OMP_NUM_THREADS="1"))
one("omp1_passive", dict(os.environ, OMP_NUM_THREADS="1", OMP_WAIT_POLICY="passive"))
print("SIZE", os.path.getsize("/home/xilinx/ncnn_infer"))
print("SHA", hashlib.sha256(open("/home/xilinx/ncnn_infer", "rb").read()).hexdigest())
q = subprocess.run("df -h / | tail -1", shell=True, capture_output=True, text=True)
print("DF", q.returncode, (q.stdout or "").strip())
'''


def main():
    want = float(sys.argv[sys.argv.index("--wait") + 1]) if "--wait" in sys.argv else 5400
    t0 = time.time()
    while time.time() - t0 < want:
        if r.board_alive(timeout=30):
            break
        time.sleep(30)
    else:
        log("BOARD_NEVER_CAME_BACK; 什么都没做")
        return 2

    log("BOARD_ONLINE; 开始诊断")
    for ln in board(DIAG, timeout=600):
        log("  " + ln)

    # 2) 补跑 n08：只跑这一段，结果直接并进 scene_suite_board.json
    log("补跑 n08")
    p = subprocess.run([PY, str(ROOT / "tools" / "scene_suite_board.py"), "--clips", "n08",
                        "--no-push", "--max-frames", "250", "--every", "10"],
                       cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, encoding="utf-8", errors="replace", timeout=1800)
    for ln in (p.stdout or "").strip().splitlines()[-6:]:
        log("  " + ln)

    # 3) 标定实验：同一段带/不带 --calib 各跑一次，比较 camera_state
    log("标定实验 c01")
    code = r'''
import subprocess, json, collections
def run(clip, calib, out):
    argv = ["python3","tools/board_pipeline.py","--video","/home/xilinx/zynq-yolo/data/samples/scenes/"+clip,
            "--max-frames","120","--every","10","--max-det","0","--out",out,"--proposal",
            "--param","/home/xilinx/zynq-yolo/build/models/person_v8n25.param",
            "--model","/home/xilinx/zynq-yolo/build/models/person_v8n25.bin"]
    if calib:
        argv += ["--calib", calib]
    p = subprocess.run(argv, cwd="/home/xilinx/zynq-yolo", capture_output=True, text=True)
    try:
        d = json.load(open(out))
    except Exception as e:
        print("CAL RUN_FAIL", clip, "calib" if calib else "none", e); return
    st = collections.Counter(f.get("camera_state") for f in (d.get("proposal") or []))
    pl = [len(f.get("tracks") or []) for f in (d.get("frames_out") or [])]
    print("CAL", "calib" if calib else "none",
          "rows_pre", d.get("rows_pre_filter"), "rows_post", d.get("rows_post_filter"),
          "players %.2f" % (sum(pl)/len(pl) if pl else 0), dict(st))
run("c01_t01010.0.mp4", None, "/tmp/cal_none.json")
run("c01_t01010.0.mp4", "/home/xilinx/zynq-yolo/data/calib/field_calib.json", "/tmp/cal_yes.json")
'''
    for ln in board(code, timeout=1800):
        log("  " + ln)

    log("DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
