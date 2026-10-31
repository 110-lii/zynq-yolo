#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hdmi_geom_probe.py —— 用「八条横带 / 八条竖带」把画面几何一次问清楚。

背景（2026-10-30 23:4x 第一次上板，2026-10-31 补充）
---------------------------------------------------
彩条（不碰 DDR）人眼确认，含左侧按行灰阶 ⇒ TMDS、像素时钟、行推进都通。
切回帧缓存后，本该是「左上 128x128 白块」的画面被描述成
「左边白、其余黑、白里有黑横条、还在闪」。

把 BLOCK 那次的累加和反解（`sum = M·word mod 2^32`，word 只有 0 和 0xFFFFFF）
得到 **M = 92,978 个白像素**，而正确的 BLOCK 只有 16,384 个白像素。
92,978 / 720 行 ≈ 129 列/行 ⇒ 屏幕上**每一行**的左边 128 列都是白的 ——
和「左边白、其余黑」逐字对上，所以「哪一行送到屏幕的哪一行」确实错了，
而且不是「少数几行错」，是**每一行都读到了靠前的行**。

本次上板要一次问清三件事
------------------------
1. 行映射：8 条横带，每条 90 行、颜色各不相同。8 条等宽顺序正确 ⇒ 行映射对；
   条数变少/重复/厚度不均 ⇒ 行号在打滑；整屏一个颜色 ⇒ 所有行读同一条行缓存。
2. 列映射：8 条竖带同理。
3. 取数是否落后：读 `0x41290000`（HDMI dbg，vs 沿锁存）
       [10:0]  under_lines   本帧「行首没数据」的行数（正确 0）
       [21:11] behind_lines  本帧「取数落后于显示」的行数（正确 0）
       [31:22] last_disp     本帧最后显示到的行号（正确 719）
   两者都 0 而行号 719 ⇒ 显示与取数都在正常推进，错只能在地址（stride/base）
   或行缓存下标上；behind>0 ⇒ 取数被显示甩开，屏幕上是旧行缓存。

累加和的期望值一律按**屏幕口径**算（显示列 0/1/2 都是源列 0，源列 1278/1279
永远不显示），这也是 `st_sum` 现在的累加口径（2026-10-31 改成 video_de 对齐）。

用法：py build/hdmi_geom_probe.py --wait 300
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import run_board_acceptance as r  # noqa: E402

LOG = ROOT / "build" / "hdmi_geom_probe.log"

BOARD_CODE = r'''
import sys, time
import numpy as np
from pynq import Overlay, MMIO
ov = Overlay('/home/xilinx/zynq-yolo/board/overlay/flow_system.bit')
sys.path.insert(0, '/home/xilinx/zynq-yolo/tools')
from hdmi_video import HdmiVideoOut, pack_frame, W, H

out = HdmiVideoOut()
ST, CFG = out.st, out.cfg
DBG = MMIO(0x41290000, 0x10000)

def rd():
    v = int(ST.read(0)) & 0xFFFFFFFF
    return (v >> 16) & 0xFFFF, v & 0xFFFF, int(ST.read(8)) & 0xFFFFFFFF

def dbg():
    v = int(DBG.read(0)) & 0xFFFFFFFF
    return dict(under=v & 0x7FF, behind=(v >> 11) & 0x7FF, last_disp=(v >> 22) & 0x3FF)

# 累加口径 = 屏幕口径：显示列 0/1/2 都取源列 0，源列 1278/1279 不显示
COLS = list(range(W))
def expect(words):
    w = np.asarray(words, dtype=np.uint64) & np.uint64(0xFFFFFF)
    return int(w[:, COLS].sum() & np.uint64(0xFFFFFFFF))

def solid(bgr):
    img = np.zeros((H, W, 3), np.uint8); img[:, :] = bgr; return img

def report(tag, img, note=''):
    words = pack_frame(img)
    e = expect(words)
    out.show(img)
    time.sleep(0.45)
    fr, un, got = rd()
    d = dbg()
    print('DIAG_SUM %-7s expect=%08X got=%08X %-8s frames=%d under=%d dbg(under=%d behind=%d last=%d) %s'
          % (tag, e, got, 'MATCH' if got == e else 'MISMATCH', fr, un,
             d['under'], d['behind'], d['last_disp'], note), flush=True)

print('DIAG_CFG_READBACK addr=%08X pack=%08X' %
      (int(CFG.read(0)) & 0xFFFFFFFF, int(CFG.read(8)) & 0xFFFFFFFF), flush=True)

# ★ 先问一句「配置到底进没进 PL」。2026-10-30 21:0x 的教训：frames 60/s、under=0
#   全部正常，屏上什么都没有，最后查出 cfg_pack 读回是 0、cfg_addr 读回的是 status 寄存器
#   —— 配置根本没写进去，cfg_en=0。所有「扫描在不在跑」的判据都不会报警。
#   这里用两个魔数写-读闭环，再把 pack 拆开与应有值逐字段对一遍。
try:
    cc = out.check_cfg()
    print('DIAG_CFG_WRITABLE addr=%s pack=%s' %
          ('OK' if cc['addr'] else 'FAIL', 'OK' if cc['pack'] else 'FAIL'), flush=True)
except Exception as e:                                     # noqa: BLE001
    print('DIAG_CFG_WRITABLE EXC %s: %s' % (type(e).__name__, e), flush=True)
pk = int(CFG.read(8)) & 0xFFFFFFFF
print('DIAG_CFG_DECODE stride=%d width=%d en=%d  want=5120/1280/1 %s' %
      (pk & 0xFFFF, (pk >> 16) & 0x7FF, (pk >> 27) & 1,
       'OK' if (pk & 0xFFFF) == 5120 and ((pk >> 16) & 0x7FF) == W and ((pk >> 27) & 1) else 'MISMATCH'),
      flush=True)


# ---------------- 0. 把行号/列号本身写进像素值：累加和可**精确反解** ----------------
# 像素字 = b | g<<8 | r<<16（24 位），所以可以直接把 0..1279 的整数当像素值写。
# 整幅图每行同值 ⇒ 累加和 = 1280 × Σ(屏幕上实际出现的源行号)，不涉及任何推断。
#   · 行映射正确：Σ = 0+1+…+719 = 258,840
#   · 所有行都读第 0 行：Σ = 0
#   · 行号打滑（2 行并 1 行、循环、冻结）：Σ 与 258,840 的差直接给出「整体偏移多少行」
#   720×719×1280 = 6.6e8 < 2^32，不会回绕，反解是唯一的。
rowimg = np.zeros((H, W, 3), np.uint8)
rr = np.arange(H, dtype=np.uint32)
rowimg[:, :, 0] = (rr & 0xFF).astype(np.uint8)[:, None]          # b = row & 0xFF
rowimg[:, :, 1] = ((rr >> 8) & 0xFF).astype(np.uint8)[:, None]   # g = row >> 8
out.show(rowimg)
time.sleep(0.45)
fr, un, got = rd()
d = dbg()
sf = got / 1280.0
print('DIAG_ROWCODE sum=%08X frames=%d under=%d dbg(under=%d behind=%d last=%d)' %
      (got, fr, un, d['under'], d['behind'], d['last_disp']), flush=True)
print('DIAG_ROWCODE sigma_row=%.0f  expected=258840  mean_row=%.2f  (正确应 359.50)' %
      (sf, sf / H), flush=True)
if abs(sf - 258840) < 1:
    print('DIAG_ROWCODE_VERDICT 行映射正确', flush=True)
elif sf < 1:
    print('DIAG_ROWCODE_VERDICT 所有行都读第 0 行（行号卡死）', flush=True)
else:
    print('DIAG_ROWCODE_VERDICT 行号打滑：Σ 差 %+.0f，折算整体偏移 %+.2f 行'
          % (sf - 258840, (sf - 258840) / H), flush=True)

# 基址是否真的指向刚写的那块帧缓存（双缓冲下应等于两者之一）
addr_now = int(CFG.read(0)) & 0xFFFFFFFF
phys = [int(b.physical_address) for b in out.bufs]
print('DIAG_CFG_ADDR addr=%08X bufs=%s match=%s' %
      (addr_now, ' '.join('%08X' % a for a in phys), addr_now in phys), flush=True)

# 列同理：像素值 = 列号。屏幕列 C ↔ 源列 C，每行应贡献 0+1+…+1279 = 818,560。
colimg = np.zeros((H, W, 3), np.uint8)
cc = np.arange(W, dtype=np.uint32)
colimg[:, :, 0] = (cc & 0xFF).astype(np.uint8)[None, :]
colimg[:, :, 1] = ((cc >> 8) & 0xFF).astype(np.uint8)[None, :]
out.show(colimg)
time.sleep(0.45)
fr, un, got = rd()
per_row = got / float(H)
print('DIAG_COLCODE sum=%08X per_row=%.0f  expected=818560（每列显示 1 次）' %
      (got, per_row), flush=True)

# ---------------- 0b. 单行/单列：直接量「某个源行被显示了几次」 ----------------
# ROWCODE 只给一个总数（Σ 行号），行号打滑和「少数行读错」在同一个 Σ 里分不开。
# 这一节把行映射的**每个纤维**单独量出来：整幅图只有源行 r 是白的，
# 屏幕上有几行显示它，累加和里就有几组 1280 个白像素。
#
#   像素字 = 0x00FFFFFF（奇数，与 2^32 互素）⇒ x ↦ x·word (mod 2^32) 是双射，
#   白像素个数 = sum · word⁻¹ (mod 2^32) 可**精确**反解，不需要任何假设。
#   正确值：每个源行恰好被显示 1 次 ⇒ 白像素 1280、行数 1。
#   若某一行被显示 720 次 ⇒ 行号卡死在该行；被显示 0 次 ⇒ 该行丢失。
# 2026-10-31 先在仿真里把同一条算式跑过：8 个探针（行 0/360/700/719、列 0/1/1277/1279）
# 全部反解正确（含「列 0 显示 3 次、列 1279 显示 0 次」这两个边界），见
# sim/tb/tb_hdmi_scanout_sys.v 的 +ROW=/+COL= 模式。
WORD = 0xFFFFFF
INV_WORD = pow(WORD, -1, 1 << 32)


def white_pixels(sumv):
    """sum = 白像素数 × WORD（模 2^32）；WORD 与 2^32 互素 ⇒ 精确反解。"""
    return (sumv * INV_WORD) & 0xFFFFFFFF


def ptr_line(tag, label, n, sumv, per_row, want):
    """per_row：一行/一列全白时贡献的白像素数（行探针 1280，列探针 720）。

    打印**原始白像素数**而不只是整除后的次数：如果某些行只显示了一部分
    （欠载/末列黑），白像素数就不是 per_row 的整数倍，余数正是缺了多少。
    10-30 的 VRAMP 数 `0x3ACB0000` 用任何「每行整行显示某一行」的模型都推不出来
    （要求 1280·S ≡ 15051 mod 65536，左边恒被 256 整除），正好说明当时屏上
    **不一定每行都是整行**，所以这里必须把余数露出来，不能悄悄整除掉。
    """
    x = white_pixels(sumv)
    cnt, rem = divmod(x, per_row)
    ok = (cnt == want and rem == 0)
    print('DIAG_%s %s=%-4d sum=%08X white=%-7d => %d 次(%d 像素/次) + 余 %d  want=%d %s'
          % (tag, label, n, sumv, x, cnt, per_row, rem, want, '✓' if ok else '← 不符'),
          flush=True)
    return ok, cnt, rem


print('DIAG_ROWPTR_BEGIN 每个源行应恰好被显示 1 次（每次 = 整行 1280 个白像素）', flush=True)
rows_bad = []
for r in (0, 1, 2, 3, 360, 716, 717, 718, 719):
    img = np.zeros((H, W, 3), np.uint8)
    img[r, :] = (255, 255, 255)
    out.show(img)
    time.sleep(0.45)
    fr, un, got = rd()
    ok, cnt, rem = ptr_line('ROWPTR', 'r', r, got, W, 1)
    if not ok:
        rows_bad.append((r, cnt, rem))
print('DIAG_ROWPTR_END bad=%d %s' % (len(rows_bad), rows_bad), flush=True)
if not rows_bad:
    print('DIAG_ROWPTR_VERDICT 行映射逐行正确（每个源行显示且仅显示 1 次）', flush=True)
elif rows_bad[0][1] == H and rows_bad[0][2] == 0:
    print('DIAG_ROWPTR_VERDICT 行号卡死在第 %d 行（屏幕 720 行全是它）' % rows_bad[0][0], flush=True)
elif any(b[2] for b in rows_bad):
    print('DIAG_ROWPTR_VERDICT 有行只显示了半截（余数≠0）⇒ 欠载或末列黑，先看 under_lines',
          flush=True)
else:
    print('DIAG_ROWPTR_VERDICT 行映射有错，明细见上（次数 720=卡死，0=该行没显示，其它=重复/丢失）',
          flush=True)

print('DIAG_COLPTR_BEGIN 每个源列都应恰好被显示 1 次（屏幕列 C ↔ 源列 C）',
      flush=True)
cols_bad = []
for c in (0, 1, 2, 3, 4, 1276, 1277, 1278, 1279):
    img = np.zeros((H, W, 3), np.uint8)
    img[:, c] = (255, 255, 255)
    out.show(img)
    time.sleep(0.45)
    fr, un, got = rd()
    want = 1
    ok, cnt, rem = ptr_line('COLPTR', 'c', c, got, H, want)
    if not ok:
        cols_bad.append((c, cnt, rem, want))
print('DIAG_COLPTR_END bad=%d %s' % (len(cols_bad), cols_bad), flush=True)
if not cols_bad:
    print('DIAG_COLPTR_VERDICT 列映射正确', flush=True)

# ---------------- 1. 两个「已知会说话」的图案 ----------------
img = np.zeros((H, W, 3), np.uint8)
img[0:128, 0:128] = (255, 255, 255)
report('BLOCK', img, '正确显示时白像素应恰好 128*128=16384')

img = np.zeros((H, W, 3), np.uint8)
yy = np.arange(H, dtype=np.uint32) & 0xFF
img[:, :, 2] = yy[:, None].astype(np.uint8)          # r = y&0xFF，整行同色
report('VRAMP', img, '按行渐变；整屏同色 ⇒ 所有行读同一行')

# ---------------- 2. 八条横带（行映射） ----------------
BANDS = [(255,255,255), (0,255,255), (0,255,0), (255,255,0),
         (0,0,255), (255,0,0), (255,0,255), (0,0,0)]      # BGR
img = np.zeros((H, W, 3), np.uint8)
step = H // 8
for i, c in enumerate(BANDS):
    img[i*step:(i+1)*step] = c
print('DIAG_HBANDS 从上到下应为：白 青 绿 黄 蓝 红 品红 黑（8 条等宽）', flush=True)
report('HBANDS', img, '人眼核：条数=8、顺序对不对、厚度是否均匀')
print('DIAG_HBANDS_HOLD 40s', flush=True)
time.sleep(40)
print('DIAG_HBANDS_DBG', dbg(), flush=True)

# ---------------- 3. 八条竖带（列映射） ----------------
img = np.zeros((H, W, 3), np.uint8)
wstep = W // 8
for i, c in enumerate(BANDS):
    img[:, i*wstep:(i+1)*wstep] = c
print('DIAG_VBANDS 从左到右应为：白 青 绿 黄 蓝 红 品红 黑（8 条等宽）', flush=True)
report('VBANDS', img, '人眼核：条数=8、顺序对不对、第 1 条是否比其余宽 2 像素')
print('DIAG_VBANDS_HOLD 40s', flush=True)
time.sleep(40)
print('DIAG_VBANDS_DBG', dbg(), flush=True)

# ---------------- 4. 静止画面累加和的稳定性 ----------------
vals, ds = [], []
for k in range(6):
    vals.append(rd()[2]); ds.append(dbg())
    time.sleep(0.25)
print('DIAG_STABLE', ' '.join('%08X' % v for v in vals),
      'distinct=%d' % len(set(vals)), flush=True)
print('DIAG_STABLE_DBG', ds, flush=True)

# ---------------- 5. 三个角标（人眼几何锚点） ----------------
img = np.zeros((H, W, 3), np.uint8)
img[0:180, 0:320] = (255, 255, 255)
img[0:180, W-320:W] = (0, 0, 255)
img[H-180:H, 0:320] = (255, 0, 0)
out.show(img)
print('DIAG_MARKERS_ON 90s：左上白、右上红、左下蓝，其余黑', flush=True)
t0 = time.time()
while time.time() - t0 < 90:
    time.sleep(10)
    print('DIAG_MARK_TICK', rd(), dbg(), flush=True)
print('DIAG_DONE')
'''


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def main():
    want = float(sys.argv[sys.argv.index("--wait") + 1]) if "--wait" in sys.argv else 300
    t0 = time.time()
    while time.time() - t0 < want:
        if r.board_alive(timeout=25):
            break
        time.sleep(20)
    else:
        log("BOARD_NEVER_CAME_BACK")
        return 2
    rc, out = r.run_board(["python3", "-c", BOARD_CODE], timeout=900)
    for ln in out:
        log("  " + ln)
    log("rc=%d" % rc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
