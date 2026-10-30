#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_board_acceptance.py —— 板子一上线就把整套板端验收自动跑完。

为什么要有它
------------
2026-10-30 的板子一天掉好几次，每次都要人盯着重上电、再手工一条条敲。
更糟的是：**掉线往往就发生在测试中间**，已经跑完的那几段结果跟着内存一起没了。

这个编排器的三条纪律：
1. **等**：`--wait` 秒内每 `--poll` 秒探一次 9090，板子不通就不动硬件（AGENTS.md）；
2. **分段落盘**：每一步跑完立刻写 `data/results/board_acceptance.json`，
   板子中途掉线也不会丢掉前面的结论；
3. **不冒充**：任何一步失败就记 FAIL/SKIP 并写原因，不写「已验证」。

步骤（顺序按「先证明硬件通、再谈业务」）：
    wait    → 等板子上线
    deploy  → 推 flow_system overlay + 工具 + 分层片段
    overlay → flow_overlay.py --selftest（LCD 回读 + pclk 计数 + HDMI frames/under + 按键）
    hdmi    → check_hdmi_cards.py（红/绿/蓝/白/黑五张卡，供人眼判）
    scenes  → scene_suite_board.py（分层片段跑 board_pipeline，逐段落盘）

用法
----
    py tools/run_board_acceptance.py --wait 2400        # 等 40 分钟
    py tools/run_board_acceptance.py --only overlay     # 只跑某一步

判据令牌
--------
    ACCEPT_BEGIN / ACCEPT_STEP <名> <OK|FAIL|SKIP> <证据> / ACCEPT_DONE
"""
import argparse
import json
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
try:  # Windows 控制台默认 GBK，中文日志会变乱码；日志文件本身一律写 utf-8
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PY = sys.executable

BOARD_IP, BOARD_PORT = "192.168.2.99", 9090
BOARD_ROOT = "/home/xilinx/zynq-yolo"
OUT = ROOT / "data" / "results" / "board_acceptance.json"
LOG = ROOT / "build" / "board_acceptance.log"

STEPS = []


def _env():
    import os
    return dict(os.environ, PYTHONIOENCODING="utf-8")


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


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


def wait_for_board(seconds, poll):
    t0 = time.time()
    n = 0
    while time.time() - t0 < seconds:
        n += 1
        if board_up():
            log("BOARD_ONLINE after %.0fs (%d probes)" % (time.time() - t0, n))
            return True
        if n % 6 == 1:
            log("waiting for board… %ds / %ds" % (int(time.time() - t0), seconds))
        time.sleep(poll)
    return False


def board_push(local, remote, timeout=1200):
    """推一个文件，并把两端的 sha256 对上才算成功（不是「命令没报错」就算成功）。"""
    p = subprocess.run([PY, str(HERE / "board_jupyter.py"), "--push", str(local), remote],
                       cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, env=_env(), encoding="utf-8", errors="replace",
                       timeout=timeout)
    out = p.stdout or ""
    m_b = re.search(r"BOARD_PUSH \S+ bytes=(\d+) sha256=([0-9a-f]{64})", out)
    m_h = re.search(r"HOST_PUSH .* sha256=([0-9a-f]{64})", out)
    if p.returncode != 0 or not m_b or not m_h:
        last = [ln for ln in out.strip().splitlines() if ln.strip()]
        return False, "rc=%d %s" % (p.returncode, last[-1][:70] if last else "无输出")
    if m_b.group(2) != m_h.group(1):
        return False, "sha256 不一致 (%s 板端 vs %s 主机)" % (m_b.group(2)[:12], m_h.group(1)[:12])
    return True, "%s %s" % (m_b.group(1), m_b.group(2)[:12])


def run_board(argv, timeout=1800):
    code = ("import subprocess\n"
            "p = subprocess.run(%r, cwd=%r, capture_output=True, text=True)\n"
            "print(p.stdout[-6000:])\n"
            "print('STDERR>', p.stderr[-1500:])\n"
            "print('RC', p.returncode)\n") % (argv, BOARD_ROOT)
    try:
        p = subprocess.run([PY, str(HERE / "board_jupyter.py")], cwd=str(ROOT),
                           input=code, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, env=_env(), encoding="utf-8", errors="replace",
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, ["timeout after %ss" % timeout]
    return p.returncode, [ln for ln in (p.stdout or "").strip().splitlines() if ln.strip()]


def step(name, status, evidence, extra=None):
    STEPS.append({"step": name, "status": status, "evidence": evidence,
                  "at": time.strftime("%Y-%m-%d %H:%M:%S")})
    log("ACCEPT_STEP %-8s %-4s %s" % (name, status, evidence))
    payload = {"steps": STEPS}
    if extra:
        payload.update(extra)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def tok_line(lines, prefix):
    for ln in lines:
        if ln.startswith(prefix):
            return ln.strip()
    return ""


# ------------------------------------------------------------------ 部署
DEPLOY = [
    ("board/overlay/flow_system.bit", BOARD_ROOT + "/board/overlay/flow_system.bit"),
    ("board/overlay/flow_system.hwh", BOARD_ROOT + "/board/overlay/flow_system.hwh"),
    ("tools/flow_overlay.py", BOARD_ROOT + "/tools/flow_overlay.py"),
    ("tools/check_hdmi_cards.py", BOARD_ROOT + "/tools/check_hdmi_cards.py"),
    ("tools/hdmi_video.py", BOARD_ROOT + "/tools/hdmi_video.py"),
    ("tools/bram_fb.py", BOARD_ROOT + "/tools/bram_fb.py"),
    ("tools/board_pipeline.py", BOARD_ROOT + "/tools/board_pipeline.py"),
    ("tools/scene_suite_board.py", BOARD_ROOT + "/tools/scene_suite_board.py"),
    # 分层片段检测要用的模型：推仓库里那一份，让板端不依赖 /home/xilinx 下的旧副本
    ("build/models/person_v8n25.param", BOARD_ROOT + "/build/models/person_v8n25.param"),
    ("build/models/person_v8n25.bin", BOARD_ROOT + "/build/models/person_v8n25.bin"),
]


def do_deploy():
    bad = []
    ok_n = 0
    for rel, remote in DEPLOY:
        local = ROOT / rel
        if not local.is_file():
            bad.append("%s(缺)" % local.name)
            continue
        ok, msg = board_push(local, remote)
        if not ok:
            time.sleep(1.0)
            ok, msg = board_push(local, remote)
        if ok:
            ok_n += 1
        else:
            bad.append("%s: %s" % (local.name, msg[:70]))
    # 分层片段（不进 git，但板上跑必须传）
    clips = sorted((ROOT / "data" / "samples" / "scenes").glob("*.mp4"))
    clip_n = 0
    for c in clips:
        ok, msg = board_push(c, BOARD_ROOT + "/data/samples/scenes/" + c.name)
        if ok:
            clip_n += 1
        else:
            bad.append("%s: %s" % (c.name, msg[:60]))
    if bad:
        return False, "失败: " + "; ".join(bad)
    return True, "%d/%d 个文件 + %d/%d 段片段 sha256 对上" % (ok_n, len(DEPLOY), clip_n, len(clips))


# ------------------------------------------------------------------ 各步
def do_overlay(seconds):
    rc, out = run_board(["python3", "tools/flow_overlay.py", "--selftest",
                         "--hdmi-seconds", str(seconds)], timeout=600)
    lcd = tok_line(out, "FLOW_OVERLAY_LCD")
    hdmi = tok_line(out, "FLOW_OVERLAY_HDMI")
    ok = any(ln.strip() == "FLOW_OVERLAY_OK" for ln in out)
    ev = "%s | %s" % (lcd or "-", hdmi or (out[-1] if out else "rc=%d" % rc))
    step("overlay", "OK" if ok else "FAIL", ev[:300])
    return ok


def do_hdmi(seconds, rounds):
    rc, out = run_board(["python3", "tools/check_hdmi_cards.py",
                         "--seconds", str(seconds), "--rounds", str(rounds)], timeout=900)
    done = tok_line(out, "HDMI_CARDS_DONE")
    ready = tok_line(out, "HDMI_CARDS_READY")
    ok = bool(done)
    step("hdmi", "OK" if ok else "FAIL",
         ("%s | %s | 人眼待判：屏上应依次出现 STEP1-RED … STEP5-BLACK"
          % (ready or "-", done or (out[-1] if out else "rc=%d" % rc)))[:300])
    return ok


def do_scenes(max_frames):
    # 片段上一步已经带 sha256 推过，这里 --no-push 不再重复传（64 MB 的 base64 很贵）
    p = subprocess.run([PY, str(HERE / "scene_suite_board.py"), "--resume", "--no-push",
                        "--max-frames", str(max_frames)],
                       cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, env=_env(), encoding="utf-8", errors="replace",
                       timeout=3600)
    out = [ln for ln in (p.stdout or "").strip().splitlines() if ln.strip()]
    agg = tok_line(out, "SCENE_BOARD_AGG")
    ok = any(ln.startswith("SCENE_BOARD_OK") for ln in out)
    step("scenes", "OK" if ok else "FAIL",
         (agg or (out[-1] if out else "rc=%d" % p.returncode))[:300])
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", type=float, default=0, help="等板子上线的秒数（0=立刻检查一次）")
    ap.add_argument("--poll", type=float, default=20.0)
    ap.add_argument("--only", default="", help="只跑某一/几步（逗号分隔）")
    ap.add_argument("--hdmi-seconds", type=float, default=8.0)
    ap.add_argument("--hdmi-rounds", type=int, default=2)
    ap.add_argument("--scene-max-frames", type=int, default=60)
    args = ap.parse_args()

    LOG.write_text("", encoding="utf-8") if not LOG.exists() else None
    log("ACCEPT_BEGIN wait=%ss only=%s" % (args.wait, args.only or "all"))

    if not wait_for_board(args.wait, args.poll):
        step("wait", "SKIP", "board_offline: %s:%d 不通（AGENTS.md：离线不做硬件验证）"
             % (BOARD_IP, BOARD_PORT))
        log("ACCEPT_DONE ok=0 fail=0 skip=1 —— 板子没上线，什么都没验")
        return 2

    only = set(s.strip() for s in args.only.split(",") if s.strip())
    do = (lambda n: (not only) or (n in only))

    if do("deploy"):
        ok, ev = do_deploy()
        step("deploy", "OK" if ok else "FAIL", ev[:300])
        if not ok:
            log("ACCEPT_DONE deploy_failed —— 不往下跑，免得在缺文件的状态下得出假结论")
            return 1
    if do("overlay"):
        do_overlay(args.hdmi_seconds)
    if do("hdmi"):
        do_hdmi(args.hdmi_seconds, args.hdmi_rounds)
    if do("scenes"):
        do_scenes(args.scene_max_frames)

    nfail = sum(1 for s in STEPS if s["status"] == "FAIL")
    nskip = sum(1 for s in STEPS if s["status"] == "SKIP")
    nok = len(STEPS) - nfail - nskip
    log("ACCEPT_DONE ok=%d fail=%d skip=%d json=%s" % (nok, nfail, nskip, OUT.name))
    return 0 if nfail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
