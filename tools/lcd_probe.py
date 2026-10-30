"""lcd_probe.py -- 板端：加载 lcd_probe.bit，切分辨率/图案，读 ID/时序/像素时钟，写帧缓存。

在板子的 Jupyter 里跑（PYNQ 2.7）。所有判据都是「读寄存器 + 读回」，不猜画面。

用法（板端）：
    python3 tools/lcd_probe.py --size 800x480 --pattern 0
    python3 tools/lcd_probe.py --size 800x480 --fb-bin /home/xilinx/fb800.bin
    python3 tools/lcd_probe.py --size 480x272 --pattern 0 --hold 30

cfg 位： [0] 时钟相位(0=180°) [1] 背光(0=亮) [3:2] 图案 [4] 帧缓存 [5] 480x272
"""
import argparse
import struct
import sys
import time
from pathlib import Path

CFG_BASE_DEFAULT = 0x41220000
D0_BASE_DEFAULT = 0x41230000   # {dbg_lb0, dbg_lbmid}
D1_BASE_DEFAULT = 0x41240000   # {dbg_st0, dbg_stmid}
D2_BASE_DEFAULT = 0x41250000   # dbg_cnt
ID_BASE_DEFAULT = 0x41200000
T_BASE_DEFAULT = 0x41210000
BRAM_DEFAULT = {"800x480": 0x40000000, "480x272": 0x40080000}   # 480 挪到 0x40080000：800 的 4bpp 要 192 KB，AXI 段开 256K
GEOM = {
    "800x480": dict(w=800, h=480, ht=1056, vt=525, pclk=33.333e6),
    "480x272": dict(w=480, h=272, ht=525, vt=286, pclk=9.000e6),
}


def find_ip(ovl, needle, default):
    for name, info in (getattr(ovl, "ip_dict", None) or {}).items():
        if needle in str(name):
            a = info.get("phys_addr")
            if isinstance(a, int):
                return str(name), a
    return None, default


def read_words(mm, n):
    return [int(mm.read(4 * i)) for i in range(n)]


def write_words(mm, words):
    for i, v in enumerate(words):
        mm.write(4 * i, int(v))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bit", default="/home/xilinx/zynq-yolo/board/overlay/lcd_probe.bit")
    ap.add_argument("--size", choices=list(GEOM), default="800x480")
    ap.add_argument("--pattern", type=int, choices=[0, 1, 2, 3], default=0)
    ap.add_argument("--phase", type=int, choices=[0, 1], default=0)
    ap.add_argument("--bl-off", action="store_true")
    ap.add_argument("--fb-bin", help="原始 uint32 小端文件（4bpp 时末尾 16 字是调色板）")
    ap.add_argument("--bpp", type=int, choices=[1, 4], default=4,
                    help="帧缓存格式：4=16 色（LCD 现行，默认），1=旧黑白")
    ap.add_argument("--keys", action="store_true", help="读一次按键 GPIO(0x41260000)")
    ap.add_argument("--hold", type=float, default=0.0)
    ap.add_argument("--settle", type=float, default=0.4)
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--dbg-row", help="读侧自证：面板行号，逗号分隔，如 0,1,120,240,479")
    ap.add_argument("--dbg-scan", help="读侧自证扫描：起:步:止，如 0:60:479")
    args = ap.parse_args()

    from pynq import Overlay, MMIO

    g = GEOM[args.size]
    bit = Path(args.bit)
    if not bit.exists():
        print("LCD_PROBE_FAIL no_bit %s" % bit)
        return 1
    ovl = Overlay(str(bit))
    print("LCD_PROBE_BIT %s" % bit)

    name_cfg, cfg_base = find_ip(ovl, "gpio_cfg", CFG_BASE_DEFAULT)
    name_id, id_base = find_ip(ovl, "gpio_id", ID_BASE_DEFAULT)
    name_t, t_base = find_ip(ovl, "gpio_t", T_BASE_DEFAULT)
    name_b, bram_base = find_ip(ovl, "bram_ctrl_%s" % ("800" if args.size == "800x480" else "480"),
                               BRAM_DEFAULT[args.size])
    print("LCD_PROBE_IPS cfg=%s@%s id=%s@%s t=%s@%s bram=%s@%s"
          % (name_cfg, hex(cfg_base), name_id, hex(id_base), name_t, hex(t_base),
             name_b, hex(bram_base)))
    if args.dump:
        for k, v in ovl.ip_dict.items():
            print("  IP %-28s %s" % (k, v.get("phys_addr")))

    cfgv = (args.phase & 1) | ((1 if args.bl_off else 0) << 1)
    cfgv |= (args.pattern & 3) << 2
    cfgv |= (1 if args.fb_bin else 0) << 4
    cfgv |= (1 if args.size == "480x272" else 0) << 5

    mm_cfg = MMIO(cfg_base, 0x1000)
    mm_cfg.write(0, cfgv)
    print("LCD_PROBE_CFG size=%s pattern=%s phase=%d fb=%d value=0x%08X"
          % (args.size, args.pattern, args.phase, 1 if args.fb_bin else 0, cfgv))
    time.sleep(args.settle)

    # ---- 帧缓存 ----
    if args.fb_bin:
        raw = Path(args.fb_bin).read_bytes()
        n = len(raw) // 4
        words = list(struct.unpack("<%dI" % n, raw))
        expect = (g["w"] // (32 // args.bpp)) * g["h"]
        # 4bpp 文件尾部可以跟 16 个调色板字（现行格式），两种长度都接受
        oklen = n == expect or (args.bpp == 4 and n == expect + 16)
        print("LCD_PROBE_FB_BIN %s words=%d expect=%d bpp=%d palette=%s"
              % (args.fb_bin, n, expect, args.bpp, "yes" if n == expect + 16 else "no"))
        if not oklen:
            print("LCD_PROBE_FAIL fb_words=%d expect=%d" % (n, expect))
            return 1
        mm_b = MMIO(bram_base, 4 * n)
        t0 = time.time()
        write_words(mm_b, words)
        dt = time.time() - t0
        back = read_words(mm_b, n)
        bad = sum(1 for a, b in zip(words, back) if a != b)
        first = next(((i, words[i], back[i]) for i in range(n) if words[i] != back[i]), None)
        print("LCD_PROBE_FB_WRITE words=%d %.2fs readback_bad=%d first=%s"
              % (n, dt, bad, first))
        if bad:
            print("LCD_PROBE_FAIL fb_readback")
            return 1

    # ---- 读侧自证：把「面板某行实际要显示的 32 位」读回来 ----
    if args.dbg_row or args.dbg_scan:
        rows = []
        if args.dbg_row:
            rows += [int(x) for x in args.dbg_row.split(",") if x.strip()]
        if args.dbg_scan:
            a, st, b = [int(x) for x in args.dbg_scan.split(":")]
            rows += list(range(a, b + 1, st))
        _, d0b = find_ip(ovl, "gpio_d0", D0_BASE_DEFAULT)
        _, d1b = find_ip(ovl, "gpio_d1", D1_BASE_DEFAULT)
        _, d2b = find_ip(ovl, "gpio_d2", D2_BASE_DEFAULT)
        mm_d0, mm_d1, mm_d2 = MMIO(d0b, 0x1000), MMIO(d1b, 0x1000), MMIO(d2b, 0x1000)
        midw = 384 if args.size == "800x480" else 224
        hdisp = 800 if args.size == "800x480" else 480
        print("LCD_PROBE_DBG base d0=%s d1=%s d2=%s rows=%d" % (hex(d0b), hex(d1b), hex(d2b), len(rows)))
        bad_cnt = 0
        for r in rows:
            mm_cfg.write(0, cfgv | ((r & 0x3FF) << 6))
            time.sleep(0.06)                 # >= 3 帧
            lb0, lbmid = int(mm_d0.read(0x0)), int(mm_d0.read(0x8))
            st0, stmid = int(mm_d1.read(0x0)), int(mm_d1.read(0x8))
            cnt = int(mm_d2.read(0x0))
            de_last, fills = cnt & 0xFFFF, (cnt >> 16) & 0xFFFF
            flag = "" if de_last == hdisp else "  <-- DE拍数应为%d" % hdisp
            bad_cnt += 0 if de_last == hdisp else 1
            print("DBG row=%3d lb=[%08X|%08X] st=[%08X|%08X] de=%d fills=%d%s"
                  % (r, lb0, lbmid, st0, stmid, de_last, fills, flag))
        print("LCD_PROBE_DBG_COUNTERS de_bad=%d/%d" % (bad_cnt, len(rows)))
        mm_cfg.write(0, cfgv)

    if args.keys:
        mm_k = MMIO(0x41260000, 0x1000)
        v = int(mm_k.read(0))
        print("LCD_PROBE_KEYS raw=0x%X key0=%d key1=%d" % (v, v & 1, (v >> 1) & 1))

    # ---- ID ----
    mm_id = MMIO(id_base, 0x1000)
    lvl = int(mm_id.read(0x0)) & 0xFFFFFF
    tog = int(mm_id.read(0x8)) & 0xFFFFFF
    r7, g7, b7 = (lvl >> 23) & 1, (lvl >> 15) & 1, (lvl >> 7) & 1
    print("LCD_PROBE_ID level=0x%06X toggle=0x%06X R7G7B7=%d%d%d  (000->480x272, 100->800x480)"
          % (lvl, tog, r7, g7, b7))
    if tog:
        print("LCD_PROBE_ID_UNSTABLE bits=0x%06X（这些线在消隐期翻转，ID 不可信）" % tog)

    # ---- 时序样本 ----
    mm_t = MMIO(t_base, 0x1000)
    hs, vs, des = [], [], 0
    samples = 3000
    for _ in range(samples):
        x = int(mm_t.read(0x0))
        h = x & 0x7FF
        v = (x >> 11) & 0x7FF
        d = (x >> 22) & 1
        if h < g["ht"] and v < g["vt"]:
            hs.append(h)
            vs.append(v)
            des += d
    print("LCD_PROBE_TIMING samples=%d h=[%d,%d] v=[%d,%d] de_frac=%.3f expect_h=%d expect_v=%d"
          % (len(hs), min(hs) if hs else -1, max(hs) if hs else -1,
             min(vs) if vs else -1, max(vs) if vs else -1,
             des / max(1, len(hs)), g["ht"] - 1, g["vt"] - 1))

    # ---- 像素时钟（dbg_tick 差分） ----
    a = int(mm_t.read(0x8))
    t0 = time.perf_counter()
    time.sleep(0.25)
    b = int(mm_t.read(0x8))
    t1 = time.perf_counter()
    dt = t1 - t0
    f = ((b - a) & 0xFFFFFFFF) / dt
    print("LCD_PROBE_PCLK %.3f MHz (expect %.3f, tick %d->%d dt=%.3fs)"
          % (f / 1e6, g["pclk"] / 1e6, a, b, dt))
    ok = abs(f - g["pclk"]) < 0.03 * g["pclk"]
    if not ok:
        print("LCD_PROBE_FAIL pclk")
        return 1
    if len(hs) < 100:
        print("LCD_PROBE_FAIL too_few_timing_samples")
        return 1
    if max(vs) < g["vt"] - 20:
        print("LCD_PROBE_FAIL v_not_scanning max_v=%d" % max(vs))
        return 1

    print("LCD_PROBE_OK size=%s pclk=%.3fMHz id_R7G7B7=%d%d%d fb=%s"
          % (args.size, f / 1e6, r7, g7, b7, args.fb_bin or "-"))
    print("LCD_PROBE_EYE 请看屏：外框 2px + 上/左 32px 刻度 + 中心十字 + 每帧横移白块")
    if args.hold > 0:
        print("LCD_PROBE_HOLD %.1fs" % args.hold)
        time.sleep(args.hold)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
