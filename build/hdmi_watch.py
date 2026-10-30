#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hdmi_watch.py —— 板子一活就立刻播五张色卡（人眼判据），播成之后再补跑片段。

为什么是循环而不是一次：2026-10-30 晚上板子每几分钟断一次，
20:20 那次「等到 ALIVE 之后 22 秒」再去跑，命令发出时它已经又掉了 ——
准备动作和真正跑之间也必须重新确认内核活着。
"""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import run_board_acceptance as r  # noqa: E402

LOG = ROOT / "build" / "hdmi_watch.log"
PY = sys.executable
SECONDS_PER_CARD = 20


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def accept(args, timeout=900):
    p = subprocess.run([PY, str(ROOT / "tools" / "run_board_acceptance.py")] + args,
                       cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return (p.stdout or "")


def main():
    deadline = time.time() + 5400
    done_hdmi = False
    while time.time() < deadline and not done_hdmi:
        if not r.board_alive(timeout=20):
            time.sleep(15)
            continue
        log("BOARD_ALIVE，播色卡（每张 %ds）" % SECONDS_PER_CARD)
        # deploy 也带上：sha 一致的会跳过，只把改过的 check_hdmi_cards.py 补上去
        out = accept(["--only", "deploy,hdmi", "--hdmi-seconds", str(SECONDS_PER_CARD),
                      "--hdmi-rounds", "1"], timeout=2400)
        line = [x for x in out.splitlines() if "ACCEPT_STEP hdmi" in x]
        log(line[0] if line else "（没有 hdmi 步骤输出）")
        done_hdmi = bool(line) and " OK " in line[0]
        if not done_hdmi:
            log("没播成（多半是中途掉线），30 秒后重试")
            time.sleep(30)

    if done_hdmi:
        log("HDMI_CARDS_PLAYED —— 屏上读字仍要人眼报")
        return 0
    log("HDMI_NOT_PLAYED（一个半小时里板子没给窗口）")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
