#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_full_flow.py —— 一条命令串起整条链路，最后打印一张 PASS/FAIL 表。

为什么要有它：单件都能各自自检，但「赛场那天到底能不能从头跑到尾」没人验证过。
这里把**已有的件**按真实顺序串一遍，每一步留一条带令牌的证据行：

    时间对齐 → 赛前配置(UI) → 采集/结果落盘 → 板→PC 推流 → PC 视图 → LCD 面板 → 赛前 UI

两条纪律：
* **没有令牌的证据不算过**。每一步要么给出它自己的成功令牌，要么 SKIP（附原因）。
* 画面类结论**一律不在这里下**：面板/UI 只验「渲染 + 写屏/打包」这一步，
  颜色和版面对不对仍然要人眼看（表里标 `human` 备注）。

用法：
    py tools/check_full_flow.py --dry-run              # 主机上全跑（不接板）
    py tools/check_full_flow.py --board                # 板子在线时：含时间对齐 + 写真屏
    py tools/check_full_flow.py --replay x.jsonl       # 用真实管线输出代替合成数据

令牌：FLOW_STEP <名字> <OK|FAIL|SKIP> <证据>  … 末行 FULL_FLOW_OK / FULL_FLOW_FAIL n
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

PY = sys.executable

# 板端布局（AGENTS.md：只有 Jupyter 通道可用；这块目录是历次推送留下的）
BOARD_ROOT = "/home/xilinx/zynq-yolo"
BOARD_TOOLS = ["status_display.py", "pc_dashboard.py", "bram_fb.py", "lcd_ui.py",
               "match_setup.py"]
BOARD_DATA = ["data/fonts/simhei_subset.ttf", "board/ui_config.json"]

RESULTS = []


def _safe(s):
    """控制台是 GBK 时，子进程的 UTF-8/替换字符会让 print 直接抛异常。"""
    enc = sys.stdout.encoding or "utf-8"
    return str(s).encode(enc, "replace").decode(enc, "replace")


def step(name, status, evidence, human=""):
    RESULTS.append((name, status, evidence, human))
    print("FLOW_STEP %-10s %-4s %s%s" % (name, status, _safe(evidence),
                                        ("  (human: %s)" % _safe(human)) if human else ""))


def run_mod(args, timeout=300):
    """跑本仓的一个 python 工具，返回 (rc, 末几行输出)。超时按失败处理。"""
    try:
        env = dict(os.environ, PYTHONIOENCODING="utf-8")   # 子进程输出统一 UTF-8
        p = subprocess.run([PY] + args, cwd=str(ROOT), stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, env=env,
                           encoding="utf-8", errors="replace", timeout=timeout)
        out = p.stdout
        rc = p.returncode
    except subprocess.TimeoutExpired:
        return 124, ["timeout after %ss" % timeout]
    lines = [ln for ln in out.strip().splitlines() if ln.strip()]
    return rc, lines[-3:]


# ---------------------------------------------------------------- 1 时间对齐
def _board_env():
    return dict(os.environ, PYTHONIOENCODING="utf-8")


def board_push(local, remote, timeout=900):
    """把一个文件推到板上（`board_jupyter.py --push`：分片 base64 + 两端 sha256）。"""
    p = subprocess.run([PY, str(HERE / "board_jupyter.py"), "--push",
                        str(local), remote], cwd=str(ROOT), stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, text=True, env=_board_env(),
                       encoding="utf-8", errors="replace", timeout=timeout)
    lines = [ln for ln in p.stdout.strip().splitlines() if ln.strip()]
    return p.returncode, lines[-2:]


def run_board(argv, timeout=600):
    """在板上跑一条命令（Jupyter 内核不能吃 shell 串，所以走 subprocess）。"""
    code = ("import subprocess\n"
            "p = subprocess.run(%r, cwd=%r, capture_output=True, text=True)\n"
            "print(p.stdout[-4000:])\n"
            "print('STDERR>', p.stderr[-1500:])\n"
            "print('RC', p.returncode)\n") % (argv, BOARD_ROOT)
    try:
        p = subprocess.run([PY, str(HERE / "board_jupyter.py")], cwd=str(ROOT),
                           input=code, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, env=_board_env(), encoding="utf-8",
                           errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, ["timeout after %ss" % timeout]
    lines = [ln for ln in p.stdout.strip().splitlines() if ln.strip()]
    return p.returncode, lines


def deploy_board():
    """把「写真屏」这一步在板上要用的 py/字体/配置推过去，返回 (ok, 摘要)。"""
    todo = [(HERE / n, BOARD_ROOT + "/tools/" + n) for n in BOARD_TOOLS]
    todo += [(ROOT / rel, BOARD_ROOT + "/" + rel) for rel in BOARD_DATA]
    bad = []
    for local, remote in todo:
        if not Path(local).is_file():
            bad.append("%s(缺)" % Path(local).name)
            continue
        rc, out = board_push(local, remote)
        if rc != 0 or not any("BOARD_PUSH" in ln for ln in out):
            # 通道偶发一次失败（板子满载/内核忙）很常见，重试一次再算失败
            time.sleep(1.0)
            rc, out = board_push(local, remote)
        if rc != 0 or not any("BOARD_PUSH" in ln for ln in out):
            bad.append("%s(rc=%d %s)" % (Path(local).name, rc,
                                         (out[-1][:80] if out else "无输出")))
    if bad:
        return False, "推送失败: " + "; ".join(bad)
    return True, "tools+字体+ui_config 共 %d 个文件 sha256 对上" % len(todo)


def step_clock(board):
    if not board:
        step("clock", "SKIP", "dry-run 不接板", human="板子在线时看时间是否正确")
        return
    ip = "192.168.2.99"
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1.5)
    try:
        sock.connect((ip, 9090))
        up = True
    except OSError:
        up = False
    finally:
        sock.close()
    if not up:
        step("clock", "SKIP", "board_offline:9090 不通")
        return
    rc, out = run_mod([str(HERE / "board_clock.py"), "sync"], timeout=180)
    tok = next((ln for ln in out if "BOARD_CLOCK_SYNC_OK" in ln), "")
    step("clock", "OK" if tok else "FAIL", tok or (out[-1] if out else "no output"))


# ---------------------------------------------------------------- 2 赛前配置
def step_ui_config(path):
    """读赛前配置：优先 v2 schema（lcd_ui / match_setup 写的），兼容旧的扁平格式。"""
    p = Path(path)
    if not p.is_file():
        step("ui_config", "SKIP", "no %s（下游用默认球衣色）" % p.name,
             human="赛前要在屏上选一次颜色")
        return {"a": "red", "b": "blue"}
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:                                   # noqa: BLE001
        step("ui_config", "FAIL", "unreadable: %s" % e)
        return None
    if cfg.get("schema", "").startswith("zynq-yolo/match-config"):
        t = cfg.get("teams") or {}
        a = ((t.get("A") or {}).get("kit_color") or "").lower()
        b = ((t.get("B") or {}).get("kit_color") or "").lower()
        side = (cfg.get("first_half") or {}).get("a_side", "unknown")
        ok = bool(a) and bool(b) and a != b and side in ("left", "right")
        step("ui_config", "OK" if ok else "FAIL",
             "schema=v2 a=%s b=%s gk=%s/%s ref=%s side=%s" % (
                 a or "-", b or "-",
                 ((t.get("A") or {}).get("keeper_color") or "-"),
                 ((t.get("B") or {}).get("keeper_color") or "-"),
                 ((cfg.get("referee") or {}).get("kit_color") or "-"), side))
        return cfg
    a = str(cfg.get("jersey_a", "")).lower()
    b = str(cfg.get("jersey_b", "")).lower()
    miss = [k for k in ("jersey_a", "jersey_b") if not cfg.get(k)]
    if miss:
        step("ui_config", "FAIL", "flat schema missing=%s" % ",".join(miss))
    else:
        step("ui_config", "FAIL" if a == b else "OK", "flat a=%s b=%s" % (a, b))
    return cfg


# ---------------------------------------------------------------- 2b 队别
def step_team(cfg, tracks_path="", palette=""):
    """队别链路（D012/D013）：用**已有跟踪结果 + 原片**投票，不重跑检测。

    没有人工队别真值 ⇒ 这里只判「链路通、且没有塌成单一类」，不判准确率。
    """
    tp = Path(tracks_path) if tracks_path else (ROOT / "data" / "results" / "tracks"
                                                / "inc_01_t01154.9.json")
    if not tp.is_file():
        step("team", "SKIP", "no tracks json（%s）" % tp.name,
             human="队别需要跟踪结果；先跑 tools/track_players.py")
        return
    if not palette:
        t = (cfg or {}).get("teams") or {}
        a = ((t.get("A") or {}).get("kit_color") or "").lower()
        b = ((t.get("B") or {}).get("kit_color") or "").lower()
        palette = ("A=%s,B=%s" % (a, b)) if (a and b) else "A=red,B=white"
    rc, tail = run_mod(["tools/check_team_assign.py", "--tracks", str(tp),
                        "--palette", palette])
    line = next((ln for ln in reversed(tail) if ln.startswith("TEAM_EVAL ")), "")
    ok = bool(line) and rc == 0 and "A=0 B=0" not in line
    step("team", "OK" if ok else "FAIL", line or (tail[-1] if tail else "no output"),
         human="队别准确率要人工标注，此处只证链路通")


# ---------------------------------------------------------------- 3 结果落盘
def _synth_rows(n=40):
    """合成一局的最小记录集：球员 + 球 + 事件，形状与 board_pipeline 的输出一致。"""
    rows = []
    for i in range(n):
        t = i * 0.04
        rows.append({
            "type": "frame", "frame": 1000 + i, "timestamp": round(t, 3),
            "players": [{"id": k, "x": 10.0 + 2 * k + i * 0.1, "y": 8.0 + k,
                         "conf": 0.9} for k in range(6)],
            "ball": {"valid": i % 3 != 0, "x": 20.0 + i * 0.2, "y": 12.0 + i * 0.05},
            "events": {"possession": i % 6, "possession_dist": 1.5},
            "camera_state": "fixed",
        })
    return rows


def step_store(store_dir, replay, n_synth):
    import result_store
    store_dir = Path(store_dir)
    if store_dir.exists():
        import shutil
        shutil.rmtree(store_dir, ignore_errors=True)
    store = result_store.ResultStore(str(store_dir), fsync_every=1, max_mb=64)
    if replay and Path(replay).is_file():
        src = Path(replay)
        n = 0
        for ln in src.read_text(encoding="utf-8", errors="replace").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                rec = json.loads(ln)
            except json.JSONDecodeError:
                continue
            store.append(rec)
            n += 1
        origin = "replay=%s" % src.name
    else:
        rows = _synth_rows(n_synth)
        for rec in rows:
            store.append(rec)
        n = len(rows)
        origin = "synth"
    store.close(summary={"frames": n, "origin": origin})
    summ = store_dir / "summary.json"
    ok = summ.is_file()
    got = json.loads(summ.read_text(encoding="utf-8")) if ok else {}
    frames = got.get("frames", got.get("summary", {}).get("frames"))
    files = sorted(p.name for p in store_dir.glob("*.jsonl"))
    step("store", "OK" if (ok and files) else "FAIL",
         "%s rows=%d summary_frames=%s jsonl=%s" % (origin, n, frames, ",".join(files) or "-"))
    return store_dir / files[0] if files else None


# ---------------------------------------------------------------- 4 推流
def step_stream(jsonl, port=5015):
    if not jsonl:
        step("stream", "SKIP", "上一步没有 JSONL")
        return
    dash = subprocess.Popen([PY, str(HERE / "pc_dashboard.py"), "--listen",
                             "127.0.0.1:%d" % port, "--headless"],
                            cwd=str(ROOT), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace")
    try:
        time.sleep(2.0)          # 等它 bind；不用「试连一下」探活 ——
        if dash.poll() is not None:   # 空连接会让 dashboard 当成一次会话结束而后退
            out = (dash.stdout.read() or "").strip().splitlines()
            step("stream", "FAIL", "dashboard 起不来: %s" % (out[-1] if out else "?"))
            return
        # --from-start：文件已经在 EOF，不从头读就一行也发不出去
        rc, out = run_mod([str(HERE / "result_stream.py"), "--file", str(jsonl),
                           "--host", "127.0.0.1", "--port", str(port),
                           "--from-start", "--max-lines", "40"], timeout=45)
        tok = next((ln for ln in out if "RESULT_STREAM_OK" in ln), "")
        step("stream", "OK" if tok else "FAIL", tok or (out[-1] if out else "rc=%d" % rc))
    finally:
        dash.terminate()
        try:
            dash.wait(timeout=5)
        except subprocess.TimeoutExpired:
            dash.kill()


# ---------------------------------------------------------------- 5 面板
def step_panel(board, out_dir, fb_base, jsonl=None):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    png = out_dir / "status_lcd.png"
    if board:
        # 写 BRAM 只能在板上做（PYNQ MMIO / /dev/mem）；主机侧跑就是 2026-10-30 之前那个
        # `os.O_SYNC` 假失败。这里把文件推过去、在板上跑，同一套工具、同一份版面。
        remote_jsonl = ""
        if jsonl and Path(jsonl).is_file():
            remote_jsonl = BOARD_ROOT + "/build/flow/" + Path(jsonl).name
            board_push(str(jsonl), remote_jsonl)
        argv = ["python3", "tools/status_display.py", "--size", "800x480",
                "--fb-bram", fb_base, "--lcd-cfg", "0x41220000", "--once"]
        if remote_jsonl:
            argv += ["--replay", remote_jsonl]
        rc, out = run_board(argv)
        tok = next((ln for ln in out if "STATUS_DISPLAY_BRAM" in ln), "")
        cfg_tok = next((ln for ln in out if "LCD_CFG_OK" in ln), "")
        step("panel", "OK" if (tok and cfg_tok) else "FAIL",
             "%s | %s" % (tok or (out[-1] if out else "rc=%d" % rc), cfg_tok or "无 LCD_CFG_OK（屏上会停在 PL 彩条）"),
             human="颜色/版面要人眼看")
        return
    rc, out = run_mod([str(HERE / "status_display.py"), "--size", "800x480",
                       "--replay", str(jsonl or ""), "--png", str(png), "--once"],
                      timeout=300)
    tok = next((ln for ln in out if "STATUS_DISPLAY" in ln), "")
    ok = png.is_file() and png.stat().st_size > 5000
    step("panel", "OK" if ok else "FAIL",
         "%s png=%d B" % (tok or (out[-1] if out else "rc=%d" % rc),
                          png.stat().st_size if png.is_file() else 0),
         human="颜色/版面要人眼看")


# ---------------------------------------------------------------- 6 赛前 UI
def step_ui(board, out_dir, fb_base):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    png = out_dir / "lcd_ui.png"
    if board:
        rc, out = run_board(["python3", "tools/lcd_ui.py", "--fb-bram", fb_base, "--once"])
        tok = next((ln for ln in out if "LCD_UI_DRAW" in ln), "")
        step("lcd_ui", "OK" if tok else "FAIL", tok or (out[-1] if out else "rc=%d" % rc),
             human="按键操作与配色要人眼/手验")
        return
    rc, out = run_mod([str(HERE / "lcd_ui.py"), "--preview-only", "--preview", str(png)],
                      timeout=300)
    tok = next((ln for ln in out if "LCD_UI_DRAW" in ln), "")
    ok = png.is_file() and png.stat().st_size > 5000
    step("lcd_ui", "OK" if ok else "FAIL",
         "%s png=%d B" % (tok.split(" preview=")[0] if tok else (out[-1] if out else ""),
                          png.stat().st_size if png.is_file() else 0),
         human="量化的颜色与按键交互要人眼看/手按")


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--board", action="store_true", help="板子在线：含时间对齐与写真屏")
    ap.add_argument("--dry-run", action="store_true", help="（默认行为）只在主机上跑")
    ap.add_argument("--replay", default="", help="用真实管线 JSONL 代替合成数据")
    ap.add_argument("--ui-config", default=str(ROOT / "board" / "ui_config.json"))
    ap.add_argument("--out", default=str(ROOT / "build" / "flow"))
    ap.add_argument("--n-synth", type=int, default=40)
    ap.add_argument("--fb-base", default="0x40000000")
    ap.add_argument("--team-tracks", default="",
                    help="队别检查用的 tracks JSON（默认 data/results/tracks/inc_01_*.json）")
    ap.add_argument("--palette", default="",
                    help="队别检查的色板（默认从 ui_config 取；再缺省 A=red,B=white）")
    args = ap.parse_args()

    t0 = time.time()
    print("FULL_FLOW_BEGIN board=%d replay=%s out=%s"
          % (1 if args.board else 0, args.replay or "-", args.out))

    step_clock(args.board)
    if args.board:
        ok, ev = deploy_board()
        step("deploy", "OK" if ok else "FAIL", ev)
    cfg = step_ui_config(args.ui_config)
    if cfg is None:
        print("FULL_FLOW_NOTE ui_config 读不出来，后续仍继续（面板/UI 不依赖它）")
    step_team(cfg, args.team_tracks, args.palette)
    jsonl = step_store(Path(args.out) / "store", args.replay, args.n_synth)
    step_stream(jsonl)
    step_panel(args.board, args.out, args.fb_base, jsonl)
    step_ui(args.board, args.out, args.fb_base)

    nfail = sum(1 for r in RESULTS if r[1] == "FAIL")
    nskip = sum(1 for r in RESULTS if r[1] == "SKIP")
    nok = len(RESULTS) - nfail - nskip
    print("\n%-12s %-5s %s" % ("STEP", "RES", "EVIDENCE"))
    for name, st, ev, human in RESULTS:
        print("%-12s %-5s %s" % (name, st, ev))
    print("FULL_FLOW_TABLE ok=%d skip=%d fail=%d elapsed=%.1fs"
          % (nok, nskip, nfail, time.time() - t0))
    print("FULL_FLOW_FAIL %d" % nfail if nfail else "FULL_FLOW_OK")
    return 0 if nfail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
