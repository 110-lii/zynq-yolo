"""把人工裁定合并回 `data/samples/incidents/MANIFEST.csv`。

为什么单独一个脚本
------------------
裁定是人给的原始数据（金标准），必须**逐字保留、有归属、可追溯**，不能靠手改 CSV
（手改会静默改错行、改错列、或者把别处的 CRLF 带回来）。这里做三件事：
  1) 只按 `incident_id` 匹配合并，不按行号；
  2) `decision` 必须在固定枚举里，写错就报错退出，不静默接受；
  3) 每行记 `judged` 时间戳与 `judged_by`，区分"还没判"和"判了是看不清"。

用法
----
    python tools/log_incident_decisions.py --in .research/user_decisions_r1.csv --by user
"""
import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

sys.dont_write_bytecode = True

REPO = Path(__file__).resolve().parents[1]
MAN = REPO / "data" / "samples" / "incidents" / "MANIFEST.csv"

CHOICES = {"", "进球", "越位", "出界", "手球", "犯规", "点球", "红黄牌",
           "无事件", "看不清"}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True, help="人工裁定 CSV")
    ap.add_argument("--by", default="user", help="裁定人标识")
    args = ap.parse_args(argv)

    inp = Path(args.inp)
    if not inp.is_absolute():
        inp = REPO / inp
    if not MAN.exists():
        print("LOG_DECISIONS_FAIL 找不到 %s" % MAN)
        return 1

    with open(MAN, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    fields = list(rows[0].keys())
    for extra in ("judged", "judged_by"):
        if extra not in fields:
            fields.append(extra)

    by_id = {r["incident_id"]: r for r in rows}
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S%z")
    n_ok = 0
    bad = []
    with open(inp, encoding="utf-8-sig", newline="") as f:
        for rec in csv.DictReader(f):
            iid = (rec.get("incident_id") or "").strip()
            dec = (rec.get("decision") or "").strip()
            if not iid:
                continue
            if iid not in by_id:
                bad.append("%s 不在 MANIFEST 里" % iid)
                continue
            if dec not in CHOICES:
                bad.append("%s 的结论 %r 不在枚举里" % (iid, dec))
                continue
            r = by_id[iid]
            r["decision"] = dec
            r["confidence"] = (rec.get("confidence") or "").strip()
            # 备注里的逗号/换行会撑破 CSV 行结构，统一压成分号
            r["notes"] = (rec.get("notes") or "").replace("\n", " ").replace(",", "；").strip()
            r["judged"] = stamp
            r["judged_by"] = args.by
            n_ok += 1

    if bad:
        print("LOG_DECISIONS_FAIL 有 %d 条不合格，未写入任何东西：" % len(bad))
        for b in bad:
            print("  -", b)
        return 1

    with open(MAN, "w", encoding="utf-8", newline="\n") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n",
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    # ---- 汇总（这是唯一能进报告的数字来源）----
    from collections import Counter
    filled = [r for r in rows if r["decision"]]
    cnt = Counter(r["decision"] for r in filled)
    conf = [float(r["confidence"]) for r in filled if r["confidence"]]
    print("已合并 %d 条裁定（裁定人 %s，时间 %s）" % (n_ok, args.by, stamp))
    print("MANIFEST 共 %d 条，其中已判 %d 条，未判 %d 条"
          % (len(rows), len(filled), len(rows) - len(filled)))
    print("结论分布：" + "  ".join("%s=%d" % (k, v) for k, v in cnt.most_common()))
    if conf:
        conf.sort()
        print("置信度：n=%d  min=%.1f  median=%.2f  max=%.1f  "
              "低于 0.5 的 %d 条"
              % (len(conf), conf[0], conf[len(conf) // 2], conf[-1],
                 sum(1 for c in conf if c < 0.5)))
    print("LOG_DECISIONS_OK %d" % n_ok)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
