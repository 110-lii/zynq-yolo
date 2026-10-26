"""从真实比赛视频截帧，生成 INT8 量化标定集。

为什么用视频而不是网络图库
--------------------------
INT8 标定需要的是「硬件部署时真正会看到的输入分布」的样本。网络图库里的足球照片
多是摆拍、特写、艺术构图，与转播机位的全景画面分布差得远；用它们标定出来的
per-tensor 范围会把激活范围撑错，量化精度反而下降。所以标定集直接取自真实转播画面。

用法
----
    D:\\python\\python.exe tools\\make_calib_from_video.py [--video PATH] [--n 40]

    # 只做抽取与筛选、不写 MANIFEST（调试用）
    ... --dry-run

产物
----
    data/samples/calib_real/frames_calib/*.jpg   标定帧（640 宽，JPEG）
    data/samples/calib_real/frames_test/*.jpg    留出帧（评价用，**不参与标定**）
    data/samples/calib_real/MANIFEST.csv         可追溯清单（含 sha256 / 时间戳 / 检测统计 / split）
    data/samples/calib_real/_scratch/            中间产物（关键帧池、评分表）

★ 为什么必须切出留出集
  「标定集 = 测试集」是这一环最容易得到的假绿：用同一批图既定 scale 又验精度，
  结果必然好看，但说明不了任何泛化。所以按 `--holdout-every K` 把每第 K 帧切到
  `frames_test/`，标定只用 `frames_calib/`。

★ 许可与分发
  源视频是第三方转播内容（受版权保护）。生成的帧**不得**提交到公开仓库，
  `data/samples/**/*.jpg` 已在 .gitignore 中排除。`MANIFEST.csv` 只记录哈希与统计，
  不含图像数据，可以作为「我确实用过这些帧」的可核验凭据提交。

判据
----
末行打印 `CALIB_VIDEO_OK <n>`；任一环节不满足则打印 `CALIB_VIDEO_FAIL <原因>`。
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VIDEO = Path(r"D:\yt-dlp\downloads\migu_5106947698\migu_5106947698.mp4")
FFMPEG = Path(r"D:\yt-dlp\ffmpeg\ffmpeg.exe")
MODEL = ROOT / "data" / "models" / "yolov8n.pt"
OUT = ROOT / "data" / "samples" / "calib_real"
FRAMES_CALIB = OUT / "frames_calib"
FRAMES_TEST = OUT / "frames_test"
SCRATCH = OUT / "_scratch"

# 判据阈值。这些数字是「筛选条件」，不是「调参旋钮」：
# 改它们必须重跑同一套精度判据并如实记录，不允许“调到通过为止”。
GREEN_MIN = 0.15  # 草坪可见才算转播机位画面（低于此多为特写/广告/回放插播）
GREEN_MAX = 0.90  # 近乎纯草坪的空镜对检测没有信息量
PERSON_MIN = 4  # 转播全景应有多名球员；少于 4 人多为特写/替补席
POOL_MAX = 600  # YOLO 打分前的池上限（控制耗时）
TARGET_N = 40  # 最终帧数

# COCO 类别号
PERSON = 0
SPORTS_BALL = 32


def run(cmd, **kw):
    return subprocess.run(cmd, check=False, **kw)


def probe_video(path: Path):
    """用 cv2 读基本参数（本机没有 ffprobe，但有 cv2）。"""
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return None
    info = {
        "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        "fps": float(cap.get(cv2.CAP_PROP_FPS)),
        "frames": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
    }
    cap.release()
    info["duration_s"] = info["frames"] / info["fps"] if info["fps"] else 0.0
    return info


def extract_keyframes(video: Path, pool: Path, width: int = 0):
    """只解关键帧（-skip_frame nokey），比全解码快两个数量级。

    `width` = 关键帧池的缩放宽度；**0 表示原分辨率不缩放**（默认）。
    """
    if pool.exists():
        shutil.rmtree(pool)
    pool.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(FFMPEG),
        "-hide_banner",
        "-loglevel",
        "error",
        "-skip_frame",
        "nokey",
        "-i",
        str(video),
        "-vsync",
        "0",
    ]
    if width and width > 0:
        cmd += ["-vf", f"scale={width}:-2"]
    cmd += [
        "-q:v",
        "3",
        str(pool / "%05d.jpg"),
    ]
    r = run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"FFMPEG_FAIL {r.stderr[-800:]}")
        return []
    return sorted(pool.glob("*.jpg"))


def green_ratio(p: Path):
    """草坪占比：草地上 G 显著高于 R 和 B。"""
    import numpy as np
    from PIL import Image

    with Image.open(p) as im:
        a = np.asarray(im.convert("RGB").resize((160, 120))).astype(np.int16)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    return float(((g > 55) & (g > r + 12) & (g > b + 12)).mean())


def dhash(p: Path):
    """64 位差hash，用于剔除相邻关键帧（同一机位几秒内的画面）。"""
    import numpy as np
    from PIL import Image

    with Image.open(p) as im:
        a = np.asarray(im.convert("L").resize((9, 8), Image.LANCZOS), dtype=np.int16)
    v = 0
    for bit in (a[:, 1:] > a[:, :-1]).flatten():
        v = (v << 1) | int(bit)
    return v


def hamming(a: int, b: int):
    return bin(a ^ b).count("1")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default=str(DEFAULT_VIDEO))
    ap.add_argument("--n", type=int, default=TARGET_N)
    ap.add_argument("--holdout-every", type=int, default=4,
                    help="每第 K 帧进留出集（默认 4 ⇒ 40 帧里 10 张留出）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--pool-width",
        type=int,
        default=0,
        help=(
            "关键帧池的缩放宽度像素；0 = 原分辨率不缩放（默认）。"
            "★ 2026-10-19 起默认原分辨率：此前写死 640 宽，"
            "导致下游所有「输入分辨率」实验实际是在 640x360 的降采样源上做的"
            "（1280 输入等于上采样 2 倍），会低估原生 1280 喂入时的精度。"
        ),
    )
    args = ap.parse_args()

    video = Path(args.video)
    if not video.exists():
        print(f"CALIB_VIDEO_FAIL video not found: {video}")
        return 1
    if not FFMPEG.exists():
        print(f"CALIB_VIDEO_FAIL ffmpeg not found: {FFMPEG}")
        return 1

    info = probe_video(video)
    if not info:
        print("CALIB_VIDEO_FAIL cv2 cannot open video")
        return 1
    print(f"video={video.name} {info['width']}x{info['height']} "
          f"fps={info['fps']:.2f} frames={info['frames']} "
          f"duration={info['duration_s'] / 60:.1f} min")

    SCRATCH.mkdir(parents=True, exist_ok=True)
    pool = SCRATCH / "pool"
    print("extracting keyframes ...")
    frames = extract_keyframes(video, pool, args.pool_width)
    if not frames:
        print("CALIB_VIDEO_FAIL no keyframes extracted")
        return 1
    print(f"keyframes={len(frames)}")

    # 关键帧序号 -> 视频时间：关键帧在视频里位置并不均匀，用序号比例只是近似。
    # 精确时间需要 ffprobe，本机没有；这里用「序号比例 × 总时长」并在 MANIFEST 里标 approx。
    step = info["duration_s"] / max(len(frames), 1)

    recs = []
    for i, p in enumerate(frames):
        g = green_ratio(p)
        recs.append(
            dict(path=str(p), idx=i, t_approx=round(i * step, 1), green=round(g, 4),
                 dhash=dhash(p))
        )

    # 1) 草坪占比筛选
    cand = [r for r in recs if GREEN_MIN <= r["green"] <= GREEN_MAX]
    print(f"green in [{GREEN_MIN},{GREEN_MAX}]: {len(cand)}/{len(recs)}")
    if not cand:
        print("CALIB_VIDEO_FAIL green filter removed everything")
        return 1

    # 2) 时间上均匀抽稀到 POOL_MAX（保证覆盖全场，而不是集中在某个时段）
    if len(cand) > POOL_MAX:
        idx = [round(k * (len(cand) - 1) / (POOL_MAX - 1)) for k in range(POOL_MAX)]
        cand = [cand[j] for j in sorted(set(idx))]
        # 抽稀会把相邻帧重新凑到一起，再做一次 dHash 去重
    print(f"pool after thinning: {len(cand)}")

    # 3) dHash 去重（与已选帧汉明距离 < 6 视为重复机位/同一画面）
    kept = []
    for r in cand:
        if all(hamming(r["dhash"], k["dhash"]) >= 6 for k in kept):
            kept.append(r)
    print(f"after dhash dedupe: {len(kept)}")
    if len(kept) < args.n:
        print(f"CALIB_VIDEO_FAIL only {len(kept)} distinct frames, need {args.n}")
        return 1

    # 4) YOLO 打分（人数）
    print("scoring with YOLOv8n ...")
    from ultralytics import YOLO

    model = YOLO(str(MODEL))
    scored = []
    for r in kept:
        res = model.predict(r["path"], imgsz=320, device="cpu", conf=0.25, verbose=False)[0]
        cls = res.boxes.cls.tolist() if res.boxes is not None else []
        r["n_person"] = sum(1 for c in cls if int(c) == PERSON)
        r["n_ball"] = sum(1 for c in cls if int(c) == SPORTS_BALL)
        scored.append(r)

    good = [r for r in scored if r["n_person"] >= PERSON_MIN]
    print(f"person>={PERSON_MIN}: {len(good)}/{len(scored)}")
    if len(good) < args.n:
        print(f"CALIB_VIDEO_FAIL only {len(good)} frames pass person filter, need {args.n}")
        return 1

    # 5) 按视频时间分成 n 段，每段取人数最多的一帧 —— 保证覆盖全场而非集中在某段
    good.sort(key=lambda r: r["t_approx"])
    buckets = [[] for _ in range(args.n)]
    for j, r in enumerate(good):
        buckets[min(j * args.n // len(good), args.n - 1)].append(r)
    picked = []
    for b in buckets:
        if not b:
            continue
        picked.append(max(b, key=lambda r: (r["n_person"], r["green"])))
    picked.sort(key=lambda r: r["t_approx"])
    print(f"picked: {len(picked)}")

    if args.dry_run:
        print("DRY_RUN — 不写文件")
        for r in picked:
            print(f"  t={r['t_approx']:7.1f}s p={r['n_person']:2d} "
                  f"ball={r['n_ball']} green={r['green']:.3f} {Path(r['path']).name}")
        print(f"CALIB_VIDEO_OK {len(picked)}")
        return 0

    # 6) 落盘：标定集 / 留出集分开
    for d in (FRAMES_CALIB, FRAMES_TEST):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True, exist_ok=True)
    rows = []
    for k, r in enumerate(picked, 1):
        src = Path(r["path"])
        is_test = (args.holdout_every > 0 and k % args.holdout_every == 0)
        r["split"] = "test" if is_test else "calib"
        dst = (FRAMES_TEST if is_test else FRAMES_CALIB) / f"match_{k:03d}.jpg"
        shutil.copy2(src, dst)
        data = dst.read_bytes()
        r["dst"] = str(dst.relative_to(ROOT)).replace("\\", "/")
        r["sha256"] = hashlib.sha256(data).hexdigest()
        r["bytes"] = len(data)
        r["file"] = dst.name
        # ★ 修于 2026-10-19：原来这里写的是 `info["width"]`（= **源视频** 尺寸 1280x720），
        # 而落盘的是 `-vf scale=640:-2` 缩放后的 640x360 ⇒ MANIFEST 的 width/height
        # 对 40 行**全部**是错的（实测 disk=640x360 vs manifest=1280x720，40/40 不符）。
        # 判据教训：记录尺寸必须读**落盘的那个文件**，不能读上游对象。
        _im = cv2.imread(str(dst))
        r["width"] = int(_im.shape[1])
        r["height"] = int(_im.shape[0])
        rows.append(r)

    man = OUT / "MANIFEST.csv"
    cols = ["file", "split", "t_approx_s", "width", "height", "bytes", "sha256",
            "n_person", "n_ball", "green_ratio", "source_video", "source_sha256",
            "license_note", "added"]
    src_sha = hashlib.sha256()
    with open(video, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            src_sha.update(chunk)

    with open(man, "w", encoding="utf-8", newline="\n") as f:
        f.write(",".join(cols) + "\n")
        for r in rows:
            f.write(
                ",".join(
                    [
                        r["file"],
                        r["split"],
                        f"{r['t_approx']:.1f}",
                        str(r["width"]),
                        str(r["height"]),
                        str(r["bytes"]),
                        r["sha256"],
                        str(r["n_person"]),
                        str(r["n_ball"]),
                        f"{r['green']:.4f}",
                        video.name,
                        src_sha.hexdigest(),
                        "third-party broadcast; NOT redistributable; frames gitignored",
                        "2026-10-19",
                    ]
                )
                + "\n"
            )

    # 评分表留档（可复核筛选过程）
    with open(SCRATCH / "selection.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(
            dict(
                video=str(video),
                video_info=info,
                n_keyframes=len(frames),
                n_after_green=len(cand),
                n_after_dedupe=len(kept),
                n_pass_person=len(good),
                picked=[{k: v for k, v in r.items()} for r in rows],
                thresholds=dict(
                    green_min=GREEN_MIN, green_max=GREEN_MAX,
                    person_min=PERSON_MIN, target_n=args.n,
                ),
            ),
            f,
            ensure_ascii=False,
            indent=1,
        )

    np_ = sum(1 for r in rows if r["n_person"] >= 1)
    nb = sum(1 for r in rows if r["n_ball"] >= 1)
    n_calib = sum(1 for r in rows if r["split"] == "calib")
    n_test = len(rows) - n_calib
    print(f"WROTE {man.relative_to(ROOT)}")
    print(f"标定帧 {n_calib} 张 -> frames_calib/ ；留出帧 {n_test} 张 -> frames_test/")
    print(f"person>=1: {np_}/{len(rows)}; ball>=1: {nb}/{len(rows)}")
    if n_calib == 0 or n_test == 0:
        print("CALIB_VIDEO_FAIL 标定集或留出集为空")
        return 1
    print(f"CALIB_VIDEO_OK {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
