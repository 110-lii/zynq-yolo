import argparse
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True

import numpy as np

SCREENS = {
    "hdmi": {"size": (1280, 720), "bit": "hdmi_status.bit", "key": "hdmi"},
    "lcd": {"size": (800, 480), "bit": "lcd_status.bit", "key": "lcd"},
}


def chessboard(w, h, blk=64):
    yy, xx = np.mgrid[0:h, 0:w]
    on = ((xx // blk) + (yy // blk)) % 2 == 0
    return np.where(on[:, :, None], 255, 0).astype(np.uint8).repeat(3, axis=2)


def solid(w, h, value):
    return np.full((h, w, 3), value, dtype=np.uint8)


def stripes(w, h, blk=32, vertical=True):
    yy, xx = np.mgrid[0:h, 0:w]
    axis = xx if vertical else yy
    on = (axis // blk) % 2 == 0
    return np.where(on[:, :, None], 255, 0).astype(np.uint8).repeat(3, axis=2)


def panel_image(w, h):
    from pc_dashboard import FrameState
    from status_display import render
    st = FrameState(
        frame=202, camera_state="VALID",
        players=[{"id": 3, "x": 52.5, "y": 34.0, "conf": 0.9},
                 {"id": 7, "x": 40.0, "y": 22.0, "conf": 0.8}],
        ball={"x": 52.0, "y": 30.0, "valid": True, "space": "field"},
        events={"out_of_play": False},
    )
    return render(st, w, h)


def make_image(pattern, w, h):
    if pattern == "chess":
        return chessboard(w, h)
    if pattern == "white":
        return solid(w, h, 255)
    if pattern == "black":
        return solid(w, h, 0)
    if pattern == "hstripe":
        return stripes(w, h, 32, vertical=False)
    if pattern == "vstripe":
        return stripes(w, h, 32, vertical=True)
    if pattern == "split":
        img = solid(w, h, 255)
        img[:, : w // 2] = 0
        return img
    if pattern == "panel":
        return panel_image(w, h)
    raise ValueError("unknown pattern: %s" % pattern)


def _bram_cells(ovl):
    out = []
    for name, info in (getattr(ovl, "mem_dict", None) or {}).items():
        addr = info.get("phys_addr")
        if addr is None or not isinstance(addr, int):
            continue
        if "PSDDR" in str(name).upper():
            continue
        out.append((str(name), int(addr)))
    for name, info in ovl.ip_dict.items():
        addr = info.get("phys_addr")
        if not isinstance(addr, int):
            continue
        if any(str(name) == n for n, _ in out):
            continue
        out.append((str(name), int(addr)))
    return out


def find_base(ovl, key):
    cells = _bram_cells(ovl)
    hit = [c for c in cells if key and key in c[0].lower()]
    if hit:
        return hit[0]
    if len(cells) == 1:
        return cells[0]
    return None, None


def check_screen(ovl, which, pattern, lcd_gpio=None):
    from pynq import MMIO
    from bram_fb import pack_1bpp, write_image

    cfg = SCREENS[which]
    w, h = cfg["size"]
    name, base = find_base(ovl, cfg["key"])
    if base is None:
        print("DISPLAY_BOARD_FAIL no_bram_ctrl_for_%s cells=%s" % (which, _bram_cells(ovl)))
        return False, None
    print("DISPLAY_BOARD_BASE %s %s %s" % (which, name, hex(base)))

    img = make_image(pattern, w, h)
    words = pack_1bpp(img, w, h)
    if pattern == "white" and int(words.min()) != 0xFFFFFFFF:
        print("DISPLAY_BOARD_FAIL %s pack_not_all_ones" % which)
        return False, None
    if pattern == "black" and int(words.max()) != 0:
        print("DISPLAY_BOARD_FAIL %s pack_not_all_zeros" % which)
        return False, None

    mode = write_image(img, base, w, h)

    mm = MMIO(base, len(words) * 4)
    bad = 0
    first_bad = None
    for i in range(len(words)):
        got = int(mm.read(i * 4))
        if got != int(words[i]):
            bad += 1
            if first_bad is None:
                first_bad = (i, int(words[i]), got)
    print("DISPLAY_BOARD_WRITE %s mode=%s pattern=%s words=%d nonzero=%d"
          % (which, mode, pattern, len(words), int(np.count_nonzero(words))))
    if bad:
        i, e, g = first_bad
        print("DISPLAY_BOARD_READBACK %s FAIL mismatches=%d first=(%d exp=%s got=%s)"
              % (which, bad, i, hex(e), hex(g)))
        return False, None
    print("DISPLAY_BOARD_READBACK %s OK" % which)
    bits = np.unpackbits(words.view(np.uint8), bitorder="little")
    return True, float(bits.mean())


def _selftest():
    from bram_fb import pack_1bpp
    ok = True
    words = pack_1bpp(chessboard(1280, 720), 1280, 720)
    ok &= words.shape == (28800,)
    ok &= int(words[0]) == 0xFFFFFFFF
    ok &= int(words[64 * 40]) == 0x00000000
    ok &= int(words[64 * 40 + 2]) == 0xFFFFFFFF
    white = pack_1bpp(solid(800, 480, 255), 800, 480)
    ok &= white.shape == (12000,) and int(white.min()) == 0xFFFFFFFF
    ok &= int(pack_1bpp(solid(800, 480, 0), 800, 480).max()) == 0
    sp = pack_1bpp(make_image("split", 800, 480), 800, 480)
    ok &= int(sp[11]) == 0 and int(sp[12]) == 0xFFFF0000 and int(sp[13]) == 0xFFFFFFFF
    hs = pack_1bpp(make_image("hstripe", 800, 480), 800, 480)
    ok &= int(hs[0]) == 0xFFFFFFFF and int(hs[32 * 25]) == 0
    vs = pack_1bpp(make_image("vstripe", 800, 480), 800, 480)
    ok &= int(vs[0]) == 0xFFFFFFFF and int(vs[1]) == 0
    HT, VT = 928, 525
    good1 = ([0x01FFFFFF] * 350 + [0x01101820] * 350 + [0x00101820] * 100)
    good2 = [((v * 3) % VT) * 2048 + ((v * 7) % HT) for v in range(800)]
    r = analyze_samples(good1, good2, HT, VT, 0.5, pclk_hz=30.0e6)
    if not r["ok"]:
        ok = False
        print("DISPLAY_SELFTEST_PROBLEM good_case_rejected", r["problems"])
    bad1 = [0x01101820] * 800
    bad2 = [0] * 800
    r2 = analyze_samples(bad1, bad2, HT, VT, 0.5, pclk_hz=25.0e6)
    if r2["ok"]:
        ok = False
        print("DISPLAY_SELFTEST_PROBLEM bad_case_passed")
    sticky = ([0x01101820] * 400 + [0x01FFFFFF] * 400)
    r3 = analyze_samples(sticky, good2, HT, VT, 0.5, pclk_hz=30.0e6)
    if r3["ok"]:
        ok = False
        print("DISPLAY_SELFTEST_PROBLEM sticky_bram_case_passed")
    print("DISPLAY_SELFTEST", "OK" if ok else "FAIL",
          "chess0=%s row64=%s" % (hex(int(words[0])), hex(int(words[64 * 40]))))
    return 0 if ok else 1


def analyze_samples(d1, d2, h_total, v_total, seconds, pclk_hz=None,
                    pclk_expect=30.000e6, white_expect=None):
    """从采样序列算出 LCD 显示域的自洽判据（纯函数，可脱板自检）。

    d1 = {7'b0, lcd_de, pixel_data}，d2 = {11'b0, v_cnt, h_cnt}。
    注意 d2 的 h_cnt 只有 11 位，v_cnt 最多 11 位：拼成 `{v_cnt, h_cnt}` 时
    h_cnt 在 1056 处折返，**不能**用 `& 0x7FF` 直接切（v_cnt<<11 与 h_cnt 会重叠）。
    """
    de = [(x >> 24) & 1 for x in d1]
    px = [x & 0xFFFFFF for x in d1]
    hs = [x & 0x7FF for x in d2]
    vs = [(x >> 11) & 0x7FF for x in d2]
    n = max(1, len(de))
    duty = sum(de) / n
    active = [p for p, d in zip(px, de) if d]
    white_frac = (sum(1 for p in active if p & 0xFFFFFF == 0xFFFFFF) / len(active)) if active else 0.0
    r = {
        "samples": len(d1),
        "de_duty": duty,
        "h_min": min(hs) if hs else -1, "h_max": max(hs) if hs else -1,
        "v_min": min(vs) if vs else -1, "v_max": max(vs) if vs else -1,
        "v_uniq": len(set(vs)) if d2 else -1,
        "h_uniq": len(set(hs)) if hs else -1,
        "white_frac": white_frac,
        "pclk_mhz": (pclk_hz / 1e6) if pclk_hz else None,
        "problems": [],
    }
    p = r["problems"]
    if duty < 0.80 or duty > 0.95:
        p.append("de_duty=%.3f" % duty)
    if r["samples"] < 50:
        p.append("too_few_samples=%d (AXI GPIO 输入没同步，连读两次不一致)" % r["samples"])
    if r["h_max"] < 0.9 * h_total:
        p.append("h_max=%d < 0.9*%d" % (r["h_max"], h_total))
    if r["v_uniq"] < 3:
        p.append("v_not_advancing uniq=%d" % r["v_uniq"])
    if pclk_hz is not None and abs(pclk_hz - pclk_expect) > 0.05 * pclk_expect:
        p.append("pclk=%.3fMHz expect %.3f" % (pclk_hz / 1e6, pclk_expect / 1e6))
    if not active:
        p.append("no_active_pixels")
    if white_expect is not None and active:
        lo, hi = min(white_expect, 0.02) / 3.0, max(white_expect, 0.02) * 3.0 + 0.02
        if not (lo <= white_frac <= hi):
            p.append("white_frac=%.4f expect~%.4f" % (white_frac, white_expect))
    r["ok"] = not p
    return r


def probe_lcd_live(gpio_addr, h_total, v_total, white_expect=None, verbose=True,
                   reset_sample=0x00000000, pclk_expect=30.000e6):
    """读 AXI GPIO 判断 LCD 显示域是否真的在扫、扫的是不是我们写的那块缓存。

    三个已知坑（2026-10-28 上板踩到）：
    1. GPIO 输入口没有同步器，连读两次一致的个数可能只有一小半 —— 别要求 de 全为 1；
    2. `dbg1` 在复位期间是 0（de=0、像素黑），会把 de 的占空比拉低。
       只统计 de=1 的样本算像素比例，de 的判据只当作「必须见过 de=1」；
    3. **`dbg2` 是 {v_cnt,h_cnt} 计数器，每拍都在变，"连读两次取相同值"永远取不到**
       （实测 SAME=0）。改用「解码后越界的样本当撕裂丢弃」：
       合法值必满足 h < h_total 且 v < v_total，撕裂值几乎不会同时落在范围内。
    """
    import time
    from pynq import MMIO
    g = MMIO(int(gpio_addr), 0x1000)
    d1, d2 = [], []
    for _ in range(1500):
        a = int(g.read(0x00)); b = int(g.read(0x00))
        if a == b and a != reset_sample:
            d1.append(a)
        x = int(g.read(0x08))
        if x != reset_sample and (x & 0x7FF) < h_total and ((x >> 11) & 0x7FF) < v_total:
            d2.append(x)
    prev, total, n = None, 0, 0
    t0 = time.time()
    while time.time() - t0 < 0.5:
        x = int(g.read(0x08))
        if (x & 0x7FF) >= h_total or ((x >> 11) & 0x7FF) >= v_total:
            continue          # 撕裂样本，不计入
        c = ((x >> 11) & 0x7FF) * h_total + (x & 0x7FF)
        if prev is not None:
            total += (c - prev) % (h_total * v_total)
        prev = c
        n += 1
    dt = max(1e-6, time.time() - t0)
    r = analyze_samples(d1, d2, h_total, v_total, dt, pclk_hz=total / dt,
                        white_expect=white_expect, pclk_expect=pclk_expect)
    if verbose:
        print("DISPLAY_BOARD_LIVE %s samples=%d de=%.3f h=[%d,%d] v=[%d,%d] uniq=%d "
              "white=%.4f pclk=%.3fMHz"
              % ("OK" if r["ok"] else "FAIL", r["samples"], r["de_duty"],
                 r["h_min"], r["h_max"], r["v_min"], r["v_max"], r["v_uniq"],
                 r["white_frac"], r["pclk_mhz"] or 0.0))
        for msg in r["problems"]:
            print("DISPLAY_BOARD_LIVE_PROBLEM %s" % msg)
    return r["ok"]


def probe_hdmi_live(gpio_addr, words=28800, rows=720, verbose=True):
    """读 HDMI 侧 AXI GPIO 判断「读口是不是按栅格顺序把整块缓存扫了一遍」。

    `hdmi_status_top` 的探针语义与 LCD 不同（LCD 是 pixel_data，HDMI 是地址）：
      dbg_x = bram_addr（15 位有效，0..28799）
      dbg_y = {20'd0, video_de, pixel_ypos[10:0]}

    判据：
      · 地址覆盖到帧尾（max ≥ words-200）、行号覆盖到 720 附近（max ≥ rows-20）；
      · de 占空比 ≈ 1280/1650 = 0.776（窗口 [0.6, 0.88]）；
      · **用「地址总推进量 / 时间」反推帧率**，60±5 fps。这一条对撕裂免疫：
        单次撕裂只会让某一个 delta 偏大，总推进量仍由真实扫描速率决定；
        若读口根本没在扫（地址停住）或跳着扫，帧率会立刻掉出窗口。
    撕裂样本（越界）直接丢弃，不要求连读一致 —— `bram_addr` 在扫的时候一直在变。
    """
    import time
    from pynq import MMIO
    g = MMIO(int(gpio_addr), 0x1000)

    addr_ok, ys = [], []
    anom = 0
    prev = None
    total = 0
    t0 = time.time()
    while time.time() - t0 < 0.5:
        a = int(g.read(0x00))
        y = int(g.read(0x08))
        if a < words:
            if prev is not None:
                d = (a - prev) % words
                if d <= 500:
                    total += d
                else:
                    anom += 1        # 撕裂样本：整笔丢弃，不要计入总推进量
            prev = a
            addr_ok.append(a)
        v = y & 0x7FF
        de = (y >> 11) & 1
        if v <= rows:
            ys.append((de, v))
    dt = max(1e-6, time.time() - t0)

    problems = []
    a_max = max(addr_ok) if addr_ok else -1
    v_max = max((v for _, v in ys), default=-1)
    de_duty = (sum(d for d, _ in ys) / len(ys)) if ys else 0.0
    fps = (total / dt) / words if addr_ok else 0.0
    if a_max < words - 200:
        problems.append("addr_max=%d < %d" % (a_max, words - 200))
    if v_max < rows - 20:
        problems.append("ypos_max=%d < %d" % (v_max, rows - 20))
    if not (0.60 <= de_duty <= 0.88):
        problems.append("de_duty=%.3f" % de_duty)
    if not (55.0 <= fps <= 65.0):
        problems.append("fps=%.1f (expect 60)" % fps)
    if len(addr_ok) < 50:
        problems.append("too_few_samples=%d" % len(addr_ok))

    ok = not problems
    if verbose:
        print("DISPLAY_BOARD_HDMI_LIVE %s samples=%d addr_max=%d ypos_max=%d de=%.3f "
              "fps=%.1f anom=%d"
              % ("OK" if ok else "FAIL", len(addr_ok), a_max, v_max, de_duty, fps, anom))
        for msg in problems:
            print("DISPLAY_BOARD_HDMI_LIVE_PROBLEM %s" % msg)
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--which", choices=["hdmi", "lcd", "both"], default="both")
    ap.add_argument("--bit", help="overlay 的 .bit 路径（默认按 --which 取单屏 bit）")
    ap.add_argument("--repo", default="/home/xilinx/zynq-yolo")
    ap.add_argument("--pattern",
                    choices=["chess", "white", "black", "hstripe", "vstripe",
                             "split", "panel"],
                    default="chess")
    ap.add_argument("--lcd-gpio", default="",
                    help="LCD 活体探针的 AXI GPIO 基址（dbg_gpio_lcd），如 0x41210000；"
                         "给了就同时验「显示域在扫、扫的是刚写的内容」")
    ap.add_argument("--hdmi-gpio", default="",
                    help="HDMI 活体探针的 AXI GPIO 基址（dbg_gpio），如 0x41200000；"
                         "给了就验「读口按栅格顺序扫完整块缓存」")
    ap.add_argument("--hold", type=float, default=0.0,
                    help="写完停若干秒（人眼判读用），0 表示不等待")
    ap.add_argument("--h-total", type=int, default=928)
    ap.add_argument("--v-total", type=int, default=525)
    ap.add_argument("--pclk-mhz", type=float, default=30.000,
                    help="LCD 像素时钟期望值（MHZ），默认 30（ATK-MD0430R-800480 typ）")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()

    repo = Path(args.repo)
    sys.path.insert(0, str(repo / "tools"))
    from pynq import Overlay

    if args.which == "both" and not args.bit:
        print("DISPLAY_BOARD_FAIL need_bit_for_both "
              "(--bit board/overlay/display_both.bit)")
        return 1

    if args.bit:
        bit = Path(args.bit)
        if not bit.is_absolute():
            bit = repo / bit
    else:
        bit = repo / "board" / "overlay" / SCREENS[args.which]["bit"]
    if not bit.exists():
        print("DISPLAY_BOARD_FAIL no_bit %s" % bit)
        return 1

    ovl = Overlay(str(bit))
    print("DISPLAY_BOARD_BIT", bit.name)
    targets = ["hdmi", "lcd"] if args.which == "both" else [args.which]
    results = {t: check_screen(ovl, t, args.pattern) for t in targets}
    ok = all(r[0] for r in results.values())
    if not ok:
        print("DISPLAY_BOARD_FAIL screen_readback")
        return 1
    if args.lcd_gpio and "lcd" in results:
        white_expect = results["lcd"][1]
        if not probe_lcd_live(int(args.lcd_gpio, 0), args.h_total, args.v_total,
                              white_expect=white_expect,
                              pclk_expect=args.pclk_mhz * 1e6):
            print("DISPLAY_BOARD_FAIL lcd_live_probe")
            return 1
    if args.hdmi_gpio and "hdmi" in results:
        if not probe_hdmi_live(int(args.hdmi_gpio, 0)):
            print("DISPLAY_BOARD_FAIL hdmi_live_probe")
            return 1
    token = "DISPLAY_BOTH_BOARD_OK" if args.which == "both" else "DISPLAY_BOARD_OK"
    print("%s bit=%s pattern=%s screens=%s"
          % (token, bit.name, args.pattern, ",".join(targets)))
    print("NOTE: 实际画面需人眼确认；本脚本只验证帧缓存写入/读回。")
    if args.hold > 0:
        print("HOLD %.1fs 供人眼判读" % args.hold)
        time.sleep(args.hold)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
