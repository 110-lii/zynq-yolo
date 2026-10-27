#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""result_store.py —— 板端增量结果存储（SD 卡安全写）。

设计目标
--------
* **不丢帧**：每帧/每事件一行 JSONL，追加写；`fsync_every` 控制落盘频率；
* **可恢复**：运行中崩溃最多丢最后 `fsync_every` 行，`summary.json` 记录已写计数；
* **可轮转**：单文件超过 `max_mb` 自动切新文件，避免 FAT32 单文件/写放大问题；
* **可回放**：PC 端 `pc_dashboard.py` 直接读 JSONL。

证据帧（为什么）
----------------
JSONL 只能证明「第 N 帧判了出界」，不能证明「那一帧画的是什么」。
调参/复盘时靠录屏找帧成本极高，所以把**事件发生的那一帧原图**一起落盘：
`<store-dir>/evidence/<tag>_<frame>_<ts>.jpg`，JSONL 里同步追加一条
`{"type":"evidence","path":...}`。`path` 一律是**相对 store 根**的 posix 路径，
整个 store 目录打包拷到 PC 后相对关系不变，不需要重新对路径。

证据帧（用法）::

    store = ResultStore("/mnt/sd/zynq-yolo", fsync_every=30)
    rel = store.save_evidence(frame_bgr, "out", meta={"frame": 42, "side": "left"})
    # rel -> "evidence/out_42_20261027_101530_120.jpg"（失败/被限流 -> None）
    store.close()        # summary.json 里 "evidence": {written, skipped, errors, max_per_min}

约定 / 判据令牌
---------------
* 文件名只含 ASCII 字母数字与 `_`/`-`：SD 卡（FAT32）+ 跨平台拷贝都不出问题；
* 限流：默认每分钟最多 6 张（`max_per_min`，0=不限），多出的计入 `skipped`，**不抛异常**；
* 写失败（编码失败/磁盘满/无权限）只累计 `errors` 并返回 None，**绝不打断主循环**；
* 证据记录写成功后立即 fsync，避免掉电丢最后一帧的图与记录；
* 自检末行 `RESULT_STORE_OK`。

板上典型用法::

    store = ResultStore("/mnt/sd/zynq-yolo", fsync_every=30)
    store.append({"type": "frame", "frame": 0, ...})
    store.close(summary={"frames": 202, "ball_frames": 144})

自检::

    python tools/result_store.py --selftest
    # 末行 RESULT_STORE_OK
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.dont_write_bytecode = True


class ResultStore:
    # 证据帧子目录名：固定英文，避免 FAT32 / 拷贝工具处理中文路径的差异
    EVIDENCE_DIR = "evidence"

    def __init__(self, root, prefix="run", fsync_every=30, max_mb=64,
                 evidence_max_per_min=6):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.prefix = prefix
        self.fsync_every = max(1, int(fsync_every))
        self.max_bytes = max(1, int(max_mb)) * 1024 * 1024
        self._fh = None
        self._path = None
        self._bytes = 0
        self._since_sync = 0
        self._rot = 0
        self._opened_at = time.time()
        self.counts = {}
        # 证据帧计数：written=真的写了图并记了行；skipped=被每分钟限流挡掉；
        # errors=编码/落盘失败（不影响主循环）
        self.evidence = {"written": 0, "skipped": 0, "errors": 0, "last_error": None}
        self.evidence_max_per_min = max(0, int(evidence_max_per_min))
        self._ev_times = []      # 最近成功写盘的时间戳，用于 60 s 滑动窗口限流
        self._ev_seq = 0         # meta 里没给 frame 时的自增序号，保证文件名唯一
        self._open_new()


    def _open_new(self):
        if self._fh is not None:
            self._close_fh()
        stamp = time.strftime("%Y%m%d_%H%M%S")
        name = "%s_%s_%02d.jsonl" % (self.prefix, stamp, self._rot)
        self._rot += 1
        self._path = self.root / name
        self._fh = open(self._path, "a", encoding="utf-8", buffering=1)
        self._bytes = self._path.stat().st_size if self._path.exists() else 0
        self._since_sync = 0

    def _close_fh(self):
        try:
            self._fh.flush()
            os.fsync(self._fh.fileno())
        except Exception:
            pass
        try:
            self._fh.close()
        except Exception:
            pass
        self._fh = None

    def append(self, record):
        if not isinstance(record, dict):
            raise TypeError("record must be a dict")
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        data = (line + "\n").encode("utf-8")
        self._fh.write(data.decode("utf-8"))
        self._bytes += len(data)
        self._since_sync += 1
        typ = str(record.get("type", "record"))
        self.counts[typ] = self.counts.get(typ, 0) + 1
        if self._since_sync >= self.fsync_every:
            self.flush()
        if self._bytes >= self.max_bytes:
            self._open_new()

    def flush(self):
        if self._fh is None:
            return
        self._fh.flush()
        try:
            os.fsync(self._fh.fileno())
        except OSError:
            pass
        self._since_sync = 0

    def evidence_stats(self):
        """证据帧计数快照（写进 summary，也供板上日志直接打印）。"""
        return {
            "written": self.evidence.get("written", 0),
            "skipped": self.evidence.get("skipped", 0),
            "errors": self.evidence.get("errors", 0),
            "max_per_min": self.evidence_max_per_min,
            "last_error": self.evidence.get("last_error"),
        }

    @staticmethod
    def _safe_tag(tag):
        """标签只留 ASCII 字母数字与 _/-，其余一律变 _：文件名不含中文、不含空格。"""
        raw = str(tag if tag is not None else "")
        out = "".join(ch if (ch.isascii() and (ch.isalnum() or ch in "_-")) else "_"
                      for ch in raw)
        out = out.strip("_")
        return (out or "ev")[:32]

    def _evidence_dir(self):
        d = self.root / self.EVIDENCE_DIR
        if not d.is_dir():
            d.mkdir(parents=True, exist_ok=True)
        return d

    def save_evidence(self, frame_bgr, tag, meta=None, frame=None,
                      rate_limit_per_min=None):
        """把一帧 BGR 原图写成 `<store>/evidence/<tag>_<frame>_<ts>.jpg`，
        并在当前 JSONL 追加一条 type=evidence 记录。

        参数
        ----
        frame_bgr : ndarray   BGR 图像（cv2 读出来的那一帧，不缩放）
        tag       : str       事件标签（如 out / fall），文件名里会被清洗成 ASCII
        meta      : dict      附加信息（事件内容、相机状态等），原样进 JSONL
        frame     : int|None  帧号；None 时依次取 meta["frame"]、内部自增序号
        rate_limit_per_min : int|None  None=用实例默认（默认每分钟 6 张；0=不限）

        返回
        ----
        str  相对 store 根的 posix 路径（如 "evidence/out_12_....jpg"）
        None 被限流跳过 / 编码或落盘失败（计数已更新，**不抛异常**）

        判据：返回的是相对路径；`(store.root / rel).is_file()` 必须为真。
        """
        # 1) 限流：60 s 滑动窗口，超限直接记 skipped（防刷屏，不动磁盘）
        lim = self.evidence_max_per_min if rate_limit_per_min is None else int(rate_limit_per_min)
        now = time.time()
        if lim > 0:
            self._ev_times = [t for t in self._ev_times if now - t < 60.0]
            if len(self._ev_times) >= lim:
                self.evidence["skipped"] = self.evidence.get("skipped", 0) + 1
                return None
        # 2) 帧号：显式 > meta > 自增（保证同名秒内多帧也不撞名）
        if frame is None and isinstance(meta, dict):
            frame = meta.get("frame")
        if frame is None:
            frame = self._ev_seq
        try:
            frame_i = int(frame)
        except (TypeError, ValueError):
            frame_i = self._ev_seq
        self._ev_seq += 1
        try:
            import cv2  # 延迟导入：不存证据帧的板端路径不必加载 cv2
            if frame_bgr is None or getattr(frame_bgr, "size", 0) == 0:
                raise ValueError("empty frame")
            ok, buf = cv2.imencode(".jpg", frame_bgr,
                                   [int(cv2.IMWRITE_JPEG_QUALITY), 85])
            if not ok:
                raise IOError("imencode failed")
            ev_dir = self._evidence_dir()
            stamp = time.strftime("%Y%m%d_%H%M%S") + "_%03d" % int((now % 1.0) * 1000)
            base = "%s_%d_%s" % (self._safe_tag(tag), frame_i, stamp)
            path = ev_dir / (base + ".jpg")
            n = 1
            while path.exists():        # 同一毫秒撞名时补序号，绝不覆盖已有证据
                path = ev_dir / ("%s_%d.jpg" % (base, n))
                n += 1
            # 自己写字节 + fsync：cv2.imwrite 既不管 fsync，也不吃中文/相对路径的坑
            with open(path, "wb") as f:
                f.write(buf.tobytes())
                f.flush()
                os.fsync(f.fileno())
            rel = path.relative_to(self.root).as_posix()
            self.append({"type": "evidence", "path": rel, "tag": self._safe_tag(tag),
                         "frame": frame_i,
                         "meta": dict(meta) if isinstance(meta, dict) else {}})
            self.flush()                # 证据帧少而贵：一行也要立刻落盘
            self._ev_times.append(now)
            self.evidence["written"] = self.evidence.get("written", 0) + 1
            return rel
        except Exception as _e:         # 写失败不能拖垮主循环
            self.evidence["errors"] = self.evidence.get("errors", 0) + 1
            self.evidence["last_error"] = "%s: %s" % (type(_e).__name__, _e)
            return None

    def close(self, summary=None):
        self.flush()
        files = sorted(str(p.name) for p in self.root.glob(self.prefix + "_*.jsonl"))
        out = {
            "type": "summary",
            "created": self._opened_at,
            "closed": time.time(),
            "elapsed_s": round(time.time() - self._opened_at, 3),
            "files": files,
            "counts": dict(self.counts),
            # 证据帧计数：written/skipped/errors（--store-evidence 关掉时全为 0）
            "evidence": self.evidence_stats(),
        }
        if summary:
            out.update(summary)
        tmp = self.root / "summary.json.tmp"
        tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.root / "summary.json")
        self._close_fh()
        return self.root / "summary.json"


def _selftest():
    """自检两部分：①原有 JSONL 追加/计数；②证据帧（2 张 64x48 假帧 + 2 条事件）+
    限流（8 次调用只能落 6 张，2 张记 skipped）。全部真实读盘校验。"""
    tmp = Path(tempfile.mkdtemp(prefix="result_store_"))
    try:
        import numpy as np

        st = ResultStore(tmp, prefix="t", fsync_every=2, max_mb=1)
        for i in range(5):
            st.append({"type": "frame", "frame": i, "players": [], "ball": None})
        st.append({"type": "event", "event": "goal", "frame": 5})
        # 证据帧：两张不同内容的 64x48 假帧，各带一条事件 meta
        ev_rels = []
        for k, (tag, evname) in enumerate((("out", "out_of_play"), ("fall", "fall_candidate"))):
            fig = np.zeros((48, 64, 3), np.uint8)
            fig[:, :, k] = 200
            ev_rels.append(st.save_evidence(fig, tag, meta={"frame": 10 + k, "event": evname}))
        summary = st.close(summary={"frames": 5})
        data = json.loads(summary.read_text(encoding="utf-8"))
        lines = []
        for p in sorted(tmp.glob("t_*.jsonl")):
            lines.extend(p.read_text(encoding="utf-8").splitlines())
        recs = [json.loads(x) for x in lines]
        ev_recs = [r for r in recs if r.get("type") == "evidence"]

        # 证据文件真的存在、能被 cv2 读回原尺寸、文件名是纯 ASCII 无空格
        files_ok = True
        for rel in ev_rels:
            if not rel or not (tmp / rel).is_file() or (tmp / rel).stat().st_size <= 0:
                files_ok = False
                continue
            name = Path(rel).name
            if any(ch.isspace() for ch in name) or not name.isascii() or not rel.startswith("evidence/"):
                files_ok = False
            img = __import__("cv2").imread(str(tmp / rel))
            if img is None or img.shape[:2] != (48, 64):
                files_ok = False

        # 限流：默认每分钟 6 张，第 7、8 次必须返回 None 并记 skipped
        st2 = ResultStore(tmp / "rate", prefix="r")
        ok_n = sum(1 for i in range(8)
                   if st2.save_evidence(np.zeros((8, 8, 3), np.uint8), "out",
                                        meta={"frame": i}, rate_limit_per_min=6))
        d2 = json.loads(st2.close().read_text(encoding="utf-8"))

        ok = (data["counts"].get("frame") == 5
              and data["counts"].get("event") == 1
              and data["counts"].get("evidence") == 2
              and len(recs) == 8                      # 5 帧 + 1 事件 + 2 证据
              and len(ev_recs) == 2
              and [r["tag"] for r in ev_recs] == ["out", "fall"]
              and [r["frame"] for r in ev_recs] == [10, 11]
              and all((tmp / r["path"]).is_file() for r in ev_recs)
              and files_ok
              and data["evidence"]["written"] == 2
              and data["evidence"]["skipped"] == 0
              and data["evidence"]["errors"] == 0
              and ok_n == 6
              and d2["evidence"]["written"] == 6
              and d2["evidence"]["skipped"] == 2)
        print("RESULT_STORE_SELFTEST", "OK" if ok else "FAIL",
              "files=%d lines=%d evidence=%d rate(w/s)=%d/%d"
              % (len(data["files"]), len(recs), len(ev_recs),
                 d2["evidence"]["written"], d2["evidence"]["skipped"]))
        if ok:
            print("RESULT_STORE_OK")
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        raise SystemExit(_selftest())
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
