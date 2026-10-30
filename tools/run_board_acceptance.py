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


def board_alive(timeout=25):
    """端口通 ≠ 板子可用：内核卡死时 TCP 照样握手，但连一条 print 都不会回来。

    2026-10-30 19:00 踩到：分层跑表跑到第二段，9090 一直能 connect，
    `GET /login` 与内核都无应答 —— 光用 board_up() 会把卡死当成在线，然后干等十几分钟。
    """
    if not board_up():
        return False
    rc, out = run_board(["python3", "-c", "print('ALIVE_OK')"], timeout=timeout)
    return any("ALIVE_OK" in ln for ln in out)


def wait_for_board(seconds, poll):
    t0 = time.time()
    n = 0
    wedged = 0
    # `--wait 0` 的语义是「立刻探一次」，但 `while time.time()-t0 < 0` 一次都不进，
    # 直接跳过 —— 2026-10-30 连着两次「板子明明活着却 SKIP board_offline」就是这么来的。
    # 至少给一轮的时间预算。
    budget = max(seconds, poll)
    while time.time() - t0 < budget:
        n += 1
        if board_alive():
            log("BOARD_ONLINE after %.0fs (%d probes)" % (time.time() - t0, n))
            return True
        if board_up():
            wedged += 1
            if wedged in (1, 5) or wedged % 15 == 0:
                log("TCP 通但内核无应答 %d 次（板子卡死，需要断电重启）" % wedged)
        elif n % 6 == 1:
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


def save_raw(name, lines):
    """把某一步的完整输出落盘。只留令牌的话，FAIL 之后得整轮重跑才知道为什么
    （2026-10-30 两次踩到：`FLOW_OVERLAY_OK（机器判据…）` 被判 FAIL、片段全部
    `ModuleNotFoundError` 而证据里只有 `RC 1`）。"""
    d = ROOT / "build" / "accept"
    d.mkdir(parents=True, exist_ok=True)
    (d / ("%s.log" % name)).write_text("\n".join(lines), encoding="utf-8")


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
def board_tool_closure():
    """`board_pipeline` 在板上跑起来需要的 tools/ 模块闭包（按 import 递归收）。

    为什么不用手写清单：2026-10-30 就因为漏了 `team_assign`（新加的一行 import），
    16 段片段在板上全部 `ModuleNotFoundError` 而白跑一轮。依赖关系代码里写着，
    就别在另一个地方再维护一份。
    """
    import ast
    tools = ROOT / "tools"
    seen, todo = set(), ["board_pipeline"]
    while todo:
        mod = todo.pop()
        if mod in seen:
            continue
        f = tools / (mod + ".py")
        if not f.is_file():                      # 标准库/第三方：不在 tools/ 下，跳过
            continue
        seen.add(mod)
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and not node.level and node.module:
                todo.append(node.module.split(".")[0])
            elif isinstance(node, ast.Import):
                for a in node.names:
                    todo.append(a.name.split(".")[0])
    return sorted(seen)


DEPLOY = [
    ("board/overlay/flow_system.bit", BOARD_ROOT + "/board/overlay/flow_system.bit"),
    ("board/overlay/flow_system.hwh", BOARD_ROOT + "/board/overlay/flow_system.hwh"),
    ("tools/flow_overlay.py", BOARD_ROOT + "/tools/flow_overlay.py"),
    ("tools/check_hdmi_cards.py", BOARD_ROOT + "/tools/check_hdmi_cards.py"),
    ("tools/hdmi_video.py", BOARD_ROOT + "/tools/hdmi_video.py"),
    ("tools/bram_fb.py", BOARD_ROOT + "/tools/bram_fb.py"),
    ("tools/scene_suite_board.py", BOARD_ROOT + "/tools/scene_suite_board.py"),
    # 分层片段检测要用的模型：推仓库里那一份，让板端不依赖 /home/xilinx 下的旧副本
    ("board/overlay/preproc.bit", BOARD_ROOT + "/board/overlay/preproc.bit"),
    ("board/overlay/preproc.hwh", BOARD_ROOT + "/board/overlay/preproc.hwh"),
    ("build/models/person_v8n25.param", BOARD_ROOT + "/build/models/person_v8n25.param"),
    ("build/models/person_v8n25.bin", BOARD_ROOT + "/build/models/person_v8n25.bin"),
] + [("tools/%s.py" % m, BOARD_ROOT + "/tools/%s.py" % m) for m in board_tool_closure()]


SHA_CODE = """
import hashlib, json, os
paths = __PATHS__
out = {}
for p in paths:
    try:
        out[p] = hashlib.sha256(open(p, 'rb').read()).hexdigest() if os.path.exists(p) else ''
    except Exception as e:
        out[p] = 'ERR:%s' % e
print('SHA_MAP ' + json.dumps(out))
"""


def remote_sha_map(paths):
    """一次往返拿到板端所有文件的 sha256（空的表示不存在）。

    为什么需要：板子每 5–15 分钟掉一次，而完整部署要传 60 MB、十几分钟 ——
    按「全部重传」的思路永远跑不完。先比 sha，只补缺的，一轮能压到几分钟。
    """
    if not paths:
        return {}
    # 用占位符替换而不是 %-格式化：SHA_CODE 自己带 '%s'（拼错误信息用），
    # 和 '%% (paths,)' 撞在一起会抛 TypeError（2026-10-30 踩过）
    rc, out = run_board(["python3", "-c", SHA_CODE.replace("__PATHS__", repr(list(paths)))],
                        timeout=240)
    for ln in out:
        if ln.startswith("SHA_MAP "):
            try:
                return json.loads(ln[len("SHA_MAP "):])
            except ValueError:
                return {}
    return {}


def sha256_file(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def do_deploy():
    bad = []
    ok_n = skip_n = 0
    pairs = []
    for rel, remote in DEPLOY:
        local = ROOT / rel
        if not local.is_file():
            bad.append("%s(缺)" % local.name)
            continue
        pairs.append((local, remote))
    clips = sorted((ROOT / "data" / "samples" / "scenes").glob("*.mp4"))
    for c in clips:
        pairs.append((c, BOARD_ROOT + "/data/samples/scenes/" + c.name))

    remote = remote_sha_map([r for _l, r in pairs])
    todo = []
    for local, rpath in pairs:
        if remote.get(rpath) and remote[rpath] == sha256_file(local):
            skip_n += 1
            continue
        todo.append((local, rpath))
    log("deploy: %d 个已在板上且 sha 一致，需要传 %d 个" % (skip_n, len(todo)))

    for local, rpath in todo:
        ok, msg = board_push(local, rpath)
        if not ok:
            time.sleep(1.0)
            ok, msg = board_push(local, rpath)
        if ok:
            ok_n += 1
        else:
            bad.append("%s: %s" % (local.name, msg[:70]))
    if bad:
        return False, "失败: " + "; ".join(bad)
    return True, "新传 %d 个、跳过 %d 个（sha 已一致），共 %d 个文件 + %d 段片段" % (
        ok_n, skip_n, len(DEPLOY) + len(clips), len(clips))


# ------------------------------------------------------------------ 各步
def do_overlay(seconds):
    rc, out = run_board(["python3", "tools/flow_overlay.py", "--selftest",
                         "--hdmi-seconds", str(seconds)], timeout=600)
    lcd = tok_line(out, "FLOW_OVERLAY_LCD")
    hdmi = tok_line(out, "FLOW_OVERLAY_HDMI")
    ok = any(ln.strip().startswith("FLOW_OVERLAY_OK") for ln in out)
    ev = "%s | %s" % (lcd or "-", hdmi or (out[-1] if out else "rc=%d" % rc))
    save_raw("overlay", out)
    step("overlay", "OK" if ok else "FAIL", ev[:300])
    return ok


def do_hdmi(seconds, rounds):
    rc, out = run_board(["python3", "tools/check_hdmi_cards.py",
                         "--seconds", str(seconds), "--rounds", str(rounds)], timeout=900)
    save_raw("hdmi", out)
    done = tok_line(out, "HDMI_CARDS_DONE")
    ready = tok_line(out, "HDMI_CARDS_READY")
    cfg = tok_line(out, "HDMI_CARDS_CFG")
    # 配置读回不一致 ⇒ 直接 FAIL：那种情况下 frames/under 照样好看，但屏上一定没东西
    cfg_bad = bool(cfg) and ("pack_ok=0" in cfg or "addr_ok=0" in cfg)
    ok = bool(done) and not cfg_bad
    step("hdmi", "OK" if ok else "FAIL",
         ("%s | %s | %s | 人眼待判：屏上应依次出现 STEP1-RED … STEP5-BLACK，最后停在横幅"
          % (cfg or "-", ready or "-",
             done or (out[-1] if out else "rc=%d" % rc)))[:300])
    return ok


QUIET_CODE = """
import os
from pynq import Overlay
p = '/home/xilinx/zynq-yolo/board/overlay/preproc.bit'
if os.path.exists(p):
    Overlay(p)
    print('QUIET_OVERLAY ok preproc.bit（已停掉 HDMI 扫描）')
else:
    print('QUIET_OVERLAY no_bit（preproc.bit 不在板上，HDMI 仍在扫描）')
"""


def do_scenes(max_frames, every):
    """跑片段之前先把 HDMI 扫描停掉。

    2026-10-30 观察：每次掉线都紧跟在 HDMI 满速扫描之后（色卡跑完 83 s 后不久板子就没了）。
    flow_system 的 `fb_scanout` 一旦加载就一直在扫，不会因为没人更新帧缓存而停。
    跑片段本来就要换 preproc overlay，顺手把这路功耗关掉 —— 掉线是硬件问题，但能少一份负载。
    """
    rcq, outq = run_board(["python3", "-c", QUIET_CODE], timeout=180)
    print("[QUIET] " + (tok_line(outq, "QUIET_OVERLAY") or (outq[-1] if outq else "no output")))

    # 片段上一步已经带 sha256 推过，这里 --no-push 不再重复传（64 MB 的 base64 很贵）
    p = subprocess.run([PY, str(HERE / "scene_suite_board.py"), "--resume", "--no-push",
                        "--max-frames", str(max_frames), "--every", str(every)],
                       cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, env=_env(), encoding="utf-8", errors="replace",
                       timeout=3600)
    out = [ln for ln in (p.stdout or "").strip().splitlines() if ln.strip()]
    save_raw("scenes", out)
    agg = tok_line(out, "SCENE_BOARD_AGG")

    # `SCENE_BOARD_OK` 只要求两类都有；板子中途掉线时它照样会打印（剩下几段没跑）。
    # 验收要的是**16 段都有数**，所以按落盘的段数判，而不是按令牌判。
    done = total = 0
    try:
        rows = json.loads((ROOT / "data" / "results" / "scene_suite_board.json")
                          .read_text(encoding="utf-8")).get("rows", [])
        done = sum(1 for r in rows if "players" in r)
        total = len([c for c in (ROOT / "data" / "samples" / "scenes").glob("*.mp4")])
    except (OSError, ValueError):
        pass
    if total and done >= total:
        status = "OK"
    elif done:
        status = "PARTIAL"
    else:
        status = "FAIL"
    step("scenes", status, ("%d/%d 段有数 | %s" % (done, total, agg or
                                                 (out[-1] if out else "rc=%d" % p.returncode)))[:300])
    return status == "OK"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", type=float, default=0, help="等板子上线的秒数（0=立刻检查一次）")
    ap.add_argument("--poll", type=float, default=20.0)
    ap.add_argument("--only", default="", help="只跑某一/几步（逗号分隔）")
    ap.add_argument("--hdmi-seconds", type=float, default=8.0)
    ap.add_argument("--hdmi-rounds", type=int, default=2)
    ap.add_argument("--scene-max-frames", type=int, default=200)
    ap.add_argument("--scene-every", type=int, default=10)
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
        do_scenes(args.scene_max_frames, args.scene_every)

    nfail = sum(1 for s in STEPS if s["status"] == "FAIL")
    nskip = sum(1 for s in STEPS if s["status"] == "SKIP")
    npart = sum(1 for s in STEPS if s["status"] == "PARTIAL")
    nok = len(STEPS) - nfail - nskip - npart
    log("ACCEPT_DONE ok=%d fail=%d skip=%d partial=%d json=%s"
        % (nok, nfail, nskip, npart, OUT.name))
    return 0 if (nfail == 0 and npart == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
