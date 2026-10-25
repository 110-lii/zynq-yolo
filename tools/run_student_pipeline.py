"""run_student_pipeline.py -- 后台长任务：建检测数据集 → 训 width-0.5@160 学生 → 训 n@192 参照。

为什么两个都训
--------------
学生（w0.5@160，~34 MMAC）是**可上板**的候选；YOLOv8n@192 是精度上限参照。
两者都在 CPU 上训（本机无 GPU），总时长预计 3-5 小时，日志写在 build/train_student/。

用法（后台）：
    python tools/run_student_pipeline.py --per-video 800
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LOG = REPO / "build" / "train_student"


def run(cmd, logname):
    LOG.mkdir(parents=True, exist_ok=True)
    log = LOG / logname
    with open(log, "w", encoding="utf-8") as fh:
        fh.write("CMD: %s\n" % " ".join(cmd))
        fh.flush()
        t0 = time.perf_counter()
        p = subprocess.run(cmd, cwd=str(REPO), stdout=fh, stderr=subprocess.STDOUT)
        fh.write("\nEXIT %d in %.0fs\n" % (p.returncode, time.perf_counter() - t0))
    print("%s exit=%d log=%s" % (logname, p.returncode, log), flush=True)
    return p.returncode


def make_cfg():
    import yaml
    import ultralytics
    src = Path(ultralytics.__file__).parent / "cfg" / "models" / "v8" / "yolov8.yaml"
    cfg = yaml.safe_load(open(src, encoding="utf-8"))
    cfg["nc"] = 2
    # ultralytics 会用文件名覆盖 scale（yolov8n05.yaml → "n"），所以把自定义宽度挂在 "n" 下
    cfg["scales"] = {"n": [0.33, 0.50, 160]}
    out = REPO / "data" / "train" / "yolov8n05.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        yaml.safe_dump(cfg, fh, allow_unicode=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-video", type=int, default=800)
    ap.add_argument("--skip-ds", action="store_true")
    args = ap.parse_args()

    ds = REPO / "data" / "train" / "det" / "data.yaml"
    if not (args.skip_ds and ds.exists()):
        rc = run([sys.executable, "tools/build_match_ds.py",
                  "--per-video", str(args.per_video), "--imgsz", "960"], "dataset.log")
        if rc != 0:
            print("PIPELINE_FAIL dataset")
            return 2

    cfg = make_cfg()

    # 学生：w0.5@160，从头训
    rc = run([sys.executable, "-c",
              "from ultralytics import YOLO;"
              "YOLO(r'%s').train(data=r'%s', imgsz=160, epochs=80, batch=16, workers=2,"
              " device='cpu', project=r'%s', name='student_w05_160', exist_ok=True,"
              " patience=20, plots=False)" % (cfg, ds, LOG)], "train_student.log")
    if rc != 0:
        print("PIPELINE_FAIL student")
        return 3

    # 参照：YOLOv8n@192（COCO 预训练微调）
    rc = run([sys.executable, "-c",
              "from ultralytics import YOLO;"
              "YOLO(r'%s').train(data=r'%s', imgsz=192, epochs=30, batch=16, workers=2,"
              " device='cpu', project=r'%s', name='ref_yolov8n_192', exist_ok=True,"
              " patience=10, plots=False)" % (REPO / "data" / "models" / "yolov8n.pt", ds, LOG)],
             "train_ref.log")

    print("PIPELINE_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())