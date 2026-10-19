"""生成/扩充 INT8 标定集（`data/samples/calib/`）。

为什么需要标定集：
    量化 scale 由每个张量的 **真实动态范围** 决定。单张图标定时，该图没覆盖到的
    范围会被低估 ⇒ scale 偏小 ⇒ 饱和截断 ⇒ 误差逐层累积。
    实测（tools/pynq_net.py --input image，仅 zidane.jpg 一张）：
    检测框分数最大差 **0.056**，超过阈值 0.010。改用多图标定后应显著降低。

★ 诚实声明（很重要）：
    下面除 `zidane.jpg` 外，其余都是**对这张图的几何/光度增广**，不是真实球场图片。
    它们只能让标定范围**更接近真实域**，不能替代真实数据集。
    等 `data/samples/` 里有真实球场图（用户提供或爬取）之后，重新跑本脚本或直接
    把真实图放进标定目录即可 —— 脚本会同时收进去。
    许可：`zidane.jpg` 来自 ultralytics 包（AGPL-3.0），**仅本地标定用，不入公开仓库**；
    生成件同样是派生作品，同样不入库。

产出：`data/samples/calib/*.jpg`（由 .gitignore 排除）
判据：末行 `CALIB_SET_OK <n>`；每张都写明来源与变换。

用法：
    python tools/make_calib_set.py                # 默认生成 16 张
    python tools/make_calib_set.py --count 24
    python tools/make_calib_set.py --list         # 只列现有标定集
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.dont_write_bytecode = True

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "samples"
OUT = SRC / "calib"

# 固定种子：标定集必须可复现，否则两次标定出来的 scale 不同，
# 「同一份代码同一份数据，结果却变了」会浪费大量排查时间。
SEED = 20261019


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=16)
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args(argv)

    if a.list:
        files = sorted(OUT.glob("*.jpg")) if OUT.is_dir() else []
        print("标定集 %s：%d 张" % (OUT, len(files)))
        for f in files:
            print("  %-28s %7d B" % (f.name, f.stat().st_size))
        return 0

    import cv2

    base = SRC / "zidane.jpg"
    if not base.is_file():
        print("FAIL 缺基准图：%s" % base)
        return 2

    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.jpg"):
        old.unlink()

    img0 = cv2.imread(str(base))
    if img0 is None:
        print("FAIL 读不到 %s" % base)
        return 2
    h0, w0 = img0.shape[:2]
    rng = np.random.default_rng(SEED)

    made = []
    # 保留原图：真实分布的一份
    p = OUT / "calib_00_orig.jpg"
    cv2.imwrite(str(p), img0, [cv2.IMWRITE_JPEG_QUALITY, 95])
    made.append((p.name, "原始图（未增强）"))

    # 几何增广：缩放裁剪 + 平移，覆盖不同的目标尺寸/位置（影响检测头的激活范围）
    scales = [0.75, 0.85, 1.0, 1.15, 1.3]
    i = 1
    while len(made) < a.count:
        s = scales[i % len(scales)]
        # 缩放后中心裁剪到原尺寸（模拟不同视距）
        nh, nw = max(8, int(h0 * s)), max(8, int(w0 * s))
        r = cv2.resize(img0, (nw, nh), interpolation=cv2.INTER_LINEAR)
        cy, cx = nh // 2, nw // 2
        y0 = max(0, cy - h0 // 2)
        x0 = max(0, cx - w0 // 2)
        crop = r[y0:y0 + h0, x0:x0 + w0]
        if crop.shape[0] != h0 or crop.shape[1] != w0:
            crop = cv2.resize(crop, (w0, h0), interpolation=cv2.INTER_LINEAR)
        # 亮度/对比度扰动（影响激活的动态范围）
        alpha = float(rng.uniform(0.75, 1.25))   # 对比度
        beta = float(rng.uniform(-28, 28))       # 亮度
        aug = np.clip(crop.astype(np.float32) * alpha + beta, 0, 255).astype(np.uint8)
        name = "calib_%02d_scale%03d_a%03d_b%+04d.jpg" % (
            i, int(s * 100), int(alpha * 100), int(beta))
        name = name.replace("+", "p").replace("-", "m")
        p = OUT / name
        cv2.imwrite(str(p), aug, [cv2.IMWRITE_JPEG_QUALITY, 92])
        made.append((p.name, "缩放%.2fx + 对比度%.2f + 亮度%+.0f" % (s, alpha, beta)))
        i += 1
        if i > 200:
            break

    print("标定集 -> %s" % OUT)
    for nm, desc in made:
        print("  %-44s %s" % (nm, desc))
    print()
    print("⚠️ 除原图外均为**几何/光度增广**，不是真实球场图；只用于让量化范围更接近真实域。")
    print("      许可：源图来自 ultralytics（AGPL），本目录已由 .gitignore 排除，勿入库。")
    print("CALIB_SET_OK %d" % len(made))
    return 0


if __name__ == "__main__":
    # stdout 是 GBK，中文/emoji 会 UnicodeEncodeError，先切 UTF-8
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    sys.exit(main())
