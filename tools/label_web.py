"""球员/非球员标注网页（本地服务，浏览器点 P / N / U）。

为什么用网页而不是 PDF
--------------------
PDF 要来回翻页、手写字母、再回填 CSV，73 个框容易标错行。网页里：
  · 一个大图（已放大裁剪），绿框标出要判的框
  · 三个大按钮 P / N / U，或直接按键盘 p / n / u
  · 按空格 = 不确定就跳过（等同 U）
  · 自动保存到 data/annot/labels.csv，随时关掉再开会接着上次进度
  · 上一张 / 下一张，可跳转任意编号

口径（页面顶部也写着）
  P 球员（含守门员）
  N 非球员（裁判 / 教练 / 替补 / 看台观众 / 球童 / 摄像师 / 安保）
  U 看不清 / 只有半个身子 / 太小

用法
----
    & D:\\python\\python.exe tools\\label_web.py
    # 浏览器打开 http://127.0.0.1:8765/  （脚本会自动打印真实地址）

标完点页面顶部「显示评分结果」，或在命令行跑：
    & D:\\python\\python.exe tools\\make_player_labels.py --score

判据令牌
    LABEL_WEB_OK <url> <n_labeled>/<n_total>
    LABEL_WEB_FAIL <reason>
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import csv
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

REPO = Path(__file__).resolve().parents[1]
ANNOT = REPO / "data" / "annot"

# 多批次：每批一套 (CSV / 帧目录 / 评分文件)，互不覆盖。
#   1 = 第一批 73 框（分层：hi / lo_kept / lo_drop），已完成
#   2 = 第二批 159 框（hi 层颜色 unknown 按失败原因分层），见 docs/hi_layer_color_gate.md §4
# 人工标注不可重建，所以批次之间**不做任何自动迁移或覆盖**。
BATCHES = {
    1: {"name": "第一批 · 分层样本",
        "csv": ANNOT / "labels.csv",
        "frames": ANNOT / "frames",
        "score": ANNOT / "scores.json"},
    2: {"name": "第二批 · hi 层颜色失败",
        "csv": ANNOT / "labels2.csv",
        "frames": ANNOT / "frames2",
        "score": ANNOT / "scores2.json"},
    # 3 = 第三批 60 框：**严格重复标注**（同一批框、同一张图，隔一段时间再标一遍）。
    #     图名与列都不暴露来源，标注者不该知道这是重复框；来源映射只在 MANIFEST3.json，
    #     本文件**不读**它。一致性用 tools/repeat_agreement.py 算。
    3: {"name": "第三批 · 重复标注（复检）",
        "csv": ANNOT / "labels3.csv",
        "frames": ANNOT / "frames3",
        "score": ANNOT / "scores3.json"},
    # 4 = 第四批（新比赛 · 跨场）：由 tools/make_newmatch_labels.py --build 出图，
    #     每层（purity/color/too_small/rect/colored）抽 20 个，验「旧场层内非球员率
    #     能不能搬到另一场球」。判据不在网页里算 —— 它要跟旧场四层并排，
    #     请跑 tools/make_newmatch_labels.py --score。
    4: {"name": "第四批 · 新比赛（跨场）",
        "csv": ANNOT / "labels4.csv",
        "frames": ANNOT / "frames4",
        "score": ANNOT / "scores4.json"},
}
DEFAULT_BATCH = 2

FIELDS_V1 = ["id", "stratum", "clip", "frame", "box_idx", "conf", "h_px",
             "color_role", "color_dist", "img", "label", "note"]
FIELDS_V2 = ["id", "stratum", "fail_reason", "clip", "frame", "box_idx", "conf",
             "h_px", "purity", "color_role", "color_dist", "img", "label", "note"]
VALID = ("P", "N", "U")

_lock = threading.Lock()


def load_rows(batch):
    p = BATCHES[batch]["csv"]
    if not p.exists():
        return []
    with open(p, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def save_rows(batch, rows, fields):
    p = BATCHES[batch]["csv"]
    tmp = p.with_suffix(".csv.tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})
    tmp.replace(p)


def _band(rows):
    """按框高分档的球员占比 —— 两批都用得上，且是第二批的主判决线索。"""
    bands = ((0, 30, "0-30"), (30, 50, "30-50"), (50, 100, "50-100"),
             (100, 150, "100-150"), (150, 1e9, "150+"))
    out = {}
    for lo, hi, name in bands:
        sub = [r for r in rows if lo <= float(r["h_px"]) < hi]
        if not sub:
            continue
        n_p = sum(1 for r in sub if r["label"] == "P")
        out[name] = {"n": len(sub), "n_player": n_p,
                     "pct_player": round(100.0 * n_p / len(sub))}
    return out


def score(batch, rows):
    """两套评分口径分开写，避免把不同问题的数字混在一张表里。"""
    def pr(sel, name):
        s = [r for r in rows if sel(r)]
        lab = [r["label"] for r in s if r["label"] in VALID]
        n_lab = len(lab)
        n_pos = sum(1 for x in lab if x == "P")
        return {"strategy": name, "n_kept": len(s), "n_labeled": n_lab,
                "n_player": n_pos,
                "precision": (n_pos / n_lab) if n_lab else None}

    if batch == 1:
        # 第一批：颜色先验有/无 的对照
        with_col = pr(lambda r: r["stratum"] in ("hi", "lo_kept"),
                      "有颜色先验（hi + lo_kept）")
        without = pr(lambda r: r["stratum"] in ("hi", "lo_kept", "lo_drop"),
                     "无颜色先验（hi + 全部 lo）")
        return {"batch": 1, "kind": "color_prior_ab",
                "with_color": with_col, "without_color": without}

    if batch == 3:
        # 第三批是重复标注批次：这里**不**报任何"提升/退化"的结论，
        # 只报进度。一致性/冲突一律交给 tools/repeat_agreement.py
        # （它要拿三批做对齐，网页只能看到一批，算出来的必然是错的）。
        lab = [r["label"] for r in rows if r["label"] in VALID]
        by_s = {}
        for s in ("hi", "lo_kept", "lo_drop", "hi_unk"):
            sub = [r for r in rows if r["stratum"] == s]
            by_s[s] = {"n": len(sub),
                       "n_labeled": sum(1 for r in sub if r["label"] in VALID)}
        return {"batch": 3, "kind": "repeat_progress",
                "n_total": len(rows), "n_labeled": len(lab),
                "done": len(rows) > 0 and len(lab) == len(rows),
                "by_stratum": by_s,
                "verdict": ("本批是重复标注批次，进度 %d/%d；"
                            "一致性与冲突率请跑 tools/repeat_agreement.py"
                            % (len(lab), len(rows)))}

    if batch == 4:
        # 第四批（跨场）：网页只报进度。分层判决必须与**旧场四层并排**才有意义，
        # 那是 tools/make_newmatch_labels.py --score 的活（它带 Wilson CI 与保序检验）。
        lab = [r["label"] for r in rows if r["label"] in VALID]
        by_s = {}
        for s in ("hi_unk_purity", "hi_unk_color", "hi_unk_toosmall",
                  "hi_unk_rect", "hi_colored"):
            sub = [r for r in rows if r["stratum"] == s]
            by_s[s] = {"n": len(sub),
                       "n_labeled": sum(1 for r in sub if r["label"] in VALID)}
        return {"batch": 4, "kind": "pack_progress",
                "title": "第四批：新比赛（跨场）标注进度",
                "n_total": len(rows), "n_labeled": len(lab),
                "done": len(rows) > 0 and len(lab) == len(rows),
                "by_stratum": by_s,
                "after_done": "python tools\\make_newmatch_labels.py --score",
                "verdict": ("本批是跨场分层样本，进度 %d/%d；分层判决请跑 "
                            "tools/make_newmatch_labels.py --score" % (len(lab), len(rows)))}

    # 第二批：只问一件事 —— 颜色判不出的框里，非球员是不是更多？
    #   参照：第一批标注的 hi 层整体（24 个里 6 个非球员 = 25%）
    ref = {"n": 24, "n_nonplayer": 6, "nonplayer_rate": 0.25,
           "source": "第一批 data/annot/labels.csv 的 hi 层"}

    def rate(sel, name):
        s = [r for r in rows if sel(r)]
        lab = [r["label"] for r in s if r["label"] in VALID]
        n_lab = len(lab)
        n_np = sum(1 for x in lab if x == "N")
        n_p = sum(1 for x in lab if x == "P")
        return {"stratum": name, "n": len(s), "n_labeled": n_lab,
                "n_player": n_p, "n_nonplayer": n_np,
                "nonplayer_rate": (n_np / n_lab) if n_lab else None,
                "precision_if_kept": (n_p / n_lab) if n_lab else None}

    by_stratum = {s: rate(lambda r, s=s: r["stratum"] == s, s)
                  for s in ("hi_unk_purity", "hi_unk_color",
                            "hi_unk_toosmall", "hi_unk_rect")}
    overall = rate(lambda r: True, "全部颜色-unknown")
    gap = None
    if overall["n_labeled"]:
        gap = round(overall["nonplayer_rate"] - ref["nonplayer_rate"], 3)
    verdict = None
    if gap is not None:
        if gap >= 0.10:
            verdict = ("颜色判不出的框里非球员占 %.0f%%，明显高于 hi 层整体的 25%% "
                       "⇒ 「颜色判不出」本身就是非球员信号，不必去救颜色"
                       % (100 * overall["nonplayer_rate"]))
        elif gap <= -0.10:
            verdict = ("颜色判不出的框里非球员只占 %.0f%%，低于 hi 层整体的 25%% "
                       "⇒ 这些框更像球员，丢掉会伤 recall，颜色在 hi 层没有信息量"
                       % (100 * overall["nonplayer_rate"]))
        else:
            verdict = ("颜色判不出的框里非球员占 %.0f%%，与 hi 层整体 25%% 无显著差异 "
                       "⇒ 颜色在 hi 层没有信息量，这条路应放弃"
                       % (100 * overall["nonplayer_rate"]))
    return {"batch": 2, "kind": "hi_unknown_verdict",
            "reference_hi_layer": ref, "unknown_overall": overall,
            "by_stratum": by_stratum, "gap_vs_hi_layer": gap,
            "by_box_height": _band([r for r in rows if r["label"] in VALID]),
            "verdict": verdict}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        try:
            batch = int((qs.get("batch") or [DEFAULT_BATCH])[0])
        except (TypeError, ValueError):
            batch = DEFAULT_BATCH
        if batch not in BATCHES:
            batch = DEFAULT_BATCH
        if u.path in ("/", "/index.html"):
            self._send(200, PAGE, "text/html; charset=utf-8")
            return
        if u.path.startswith("/frames/"):
            name = Path(u.path[len("/frames/"):]).name
            p = BATCHES[batch]["frames"] / name
            if not p.exists():
                self._send(404, {"err": "no_such_frame"}, "application/json")
                return
            self._send(200, p.read_bytes(), "image/jpeg")
            return
        if u.path == "/api/batches":
            out = []
            for b, cfg in BATCHES.items():
                rows = load_rows(b)
                out.append({"batch": b, "name": cfg["name"],
                            "n_total": len(rows),
                            "n_labeled": sum(1 for r in rows if r["label"] in VALID)})
            self._send(200, {"batches": out, "default": DEFAULT_BATCH})
            return
        if u.path == "/api/state":
            with _lock:
                rows = load_rows(batch)
            self._send(200, {
                "batch": batch,
                "batch_name": BATCHES[batch]["name"],
                "rows": rows,
                "n_total": len(rows),
                "n_labeled": sum(1 for r in rows if r["label"] in VALID),
            })
            return
        if u.path == "/api/score":
            with _lock:
                rows = load_rows(batch)
            self._send(200, score(batch, rows))
            return
        self._send(404, {"err": "not_found"})

    def do_POST(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        try:
            batch = int((qs.get("batch") or [DEFAULT_BATCH])[0])
        except (TypeError, ValueError):
            batch = DEFAULT_BATCH
        if batch not in BATCHES:
            self._send(400, {"err": "bad_batch"})
            return
        n = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception:
            self._send(400, {"err": "bad_json"})
            return
        if u.path == "/api/label":
            rid = str(payload.get("id"))
            lab = str(payload.get("label", "")).strip().upper()
            if lab not in VALID + ("",):
                self._send(400, {"err": "bad_label"})
                return
            with _lock:
                rows = load_rows(batch)
                if not rows:
                    self._send(409, {"err": "empty_batch"})
                    return
                fields = list(rows[0].keys())
                hit = None
                for r in rows:
                    if r["id"] == rid:
                        r["label"] = lab
                        if "note" in payload:
                            r["note"] = str(payload["note"])
                        hit = r
                        break
                if hit is None:
                    self._send(404, {"err": "bad_id"})
                    return
                save_rows(batch, rows, fields)
                n_lab = sum(1 for x in rows if x["label"] in VALID)
            self._send(200, {"ok": True, "id": rid, "label": lab,
                             "n_labeled": n_lab, "n_total": len(rows)})
            return
        self._send(404, {"err": "not_found"})


PAGE = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>球员 / 非球员 标注</title>
<style>
  :root{--bg:#15171b;--fg:#e8eaed;--dim:#9aa0a6;--p:#2e7d32;--n:#c62828;--u:#f9a825;--card:#1e2126;}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);font-family:"Microsoft YaHei",system-ui,sans-serif;}
  header{position:sticky;top:0;background:#0d0f12;border-bottom:1px solid #2c3037;padding:8px 16px;
         display:flex;gap:14px;align-items:center;flex-wrap:wrap;z-index:9;}
  h1{font-size:15px;margin:0;font-weight:600;}
  .prog{font-size:13px;color:var(--dim)}
  .bar{width:220px;height:8px;background:#2c3037;border-radius:4px;overflow:hidden}
  .bar>i{display:block;height:100%;background:#4c8bf5;width:0%}
  main{display:flex;gap:18px;padding:14px 16px;flex-wrap:wrap;align-items:flex-start;justify-content:center}
  .imgwrap{position:relative;background:#000;border-radius:6px;overflow:hidden;line-height:0}
  .imgwrap img{display:block;max-width:min(62vw,900px);max-height:72vh;image-rendering:auto}
  .side{min-width:280px;max-width:360px;display:flex;flex-direction:column;gap:10px}
  .meta{background:var(--card);border-radius:6px;padding:10px 12px;font-size:13px;line-height:1.7;color:var(--dim)}
  .meta b{color:var(--fg)}
  button{font:inherit;border:0;border-radius:6px;padding:14px 10px;cursor:pointer;font-weight:700;font-size:17px;color:#fff}
  button:disabled{opacity:.4;cursor:default}
  .bp{background:var(--p)} .bn{background:var(--n)} .bu{background:var(--u);color:#222}
  .row{display:flex;gap:8px}
  .row>button{flex:1}
  .nav{display:flex;gap:8px}
  .nav button{background:#2c3037;font-size:14px;padding:10px 8px;flex:1}
  .hint{font-size:12px;color:#6b7280;line-height:1.6}
  .flash{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:#2e7d32;color:#fff;
         padding:10px 18px;border-radius:20px;font-size:14px;opacity:0;transition:opacity .18s;pointer-events:none}
  .flash.on{opacity:1}
  table{border-collapse:collapse;font-size:13px} td,th{padding:5px 10px;border-bottom:1px solid #2c3037;text-align:left}
  dialog{background:var(--card);color:var(--fg);border:1px solid #3a3f47;border-radius:8px;padding:18px}
</style></head>
<body>
<header>
  <h1>球员 / 非球员 标注</h1>
  <span class="prog" id="bname"></span>
  <span class="prog" id="prog">…</span>
  <div class="bar"><i id="bar"></i></div>
  <span class="prog" id="sid"></span>
  <button id="btnScore" style="background:#4c8bf5;font-size:13px;padding:7px 12px">显示评分结果</button>
</header>
<main>
  <div class="imgwrap"><img id="im" alt="frame"></div>
  <div class="side">
    <div class="meta" id="meta"></div>
    <button class="bp" id="bp">P &nbsp; 球员<span style="font-weight:400;font-size:12px">（键盘 p）</span></button>
    <button class="bn" id="bn">N &nbsp; 非球员<span style="font-weight:400;font-size:12px">（键盘 n）</span></button>
    <button class="bu" id="bu">U &nbsp; 看不清<span style="font-weight:400;font-size:12px">（键盘 u / 空格）</span></button>
    <div class="nav">
      <button id="prev">← 上一张</button>
      <button id="next">下一张 →</button>
    </div>
    <div class="hint">
      P = 球员（含守门员）　N = 非球员（裁判 / 教练 / 替补 / 看台观众 / 球童 / 摄像师 / 安保）<br>
      U = 看不清 / 只有半个身子 / 太小。<br>
      绿色框是<b>要判的这个框</b>。判完自动跳下一张，进度自动保存。
    </div>
  </div>
</main>
<div class="flash" id="flash"></div>
<dialog id="dlg"><div id="dlgBody"></div>
  <div style="text-align:right;margin-top:14px"><button onclick="dlg.close()" style="background:#2c3037;font-size:13px;padding:8px 16px">关闭</button></div>
</dialog>
<script>
let rows=[], idx=0, BATCH=1;
const $=id=>document.getElementById(id);
const VALID=["P","N","U"];
const api=(p)=>p+(p.includes("?")?"&":"?")+"batch="+BATCH;

function done(){return rows.filter(r=>VALID.includes(r.label)).length;}
function render(){
  if(!rows.length){$("meta").textContent="没有数据";return;}
  idx=Math.max(0,Math.min(rows.length-1,idx));
  const r=rows[idx];
  $("im").src=api("/frames/"+r.img.split("/").pop());
  let extra="";
  if(r.fail_reason) extra="取色失败 <b>"+r.fail_reason+"</b>　纯度 <b>"+r.purity+"</b><br>";
  $("meta").innerHTML =
    "编号 <b>"+r.id+"</b> / "+rows.length+"<br>"+
    "分层 <b>"+r.stratum+"</b><br>"+
    extra+
    "检测置信度 <b>"+r.conf+"</b>　框高 <b>"+r.h_px+" px</b><br>"+
    "颜色判定 <b>"+r.color_role+"</b>（距离 "+r.color_dist+"）<br>"+
    "是球员吗？当前：<b>"+(r.label||"—")+"</b>";
  const n=done();
  $("prog").textContent=n+" / "+rows.length+" 已标";
  $("bar").style.width=(rows.length?100*n/rows.length:0)+"%";
  $("bp").disabled=$("bn").disabled=$("bu").disabled=false;
  $("prev").disabled=idx<=0; $("next").disabled=idx>=rows.length-1;
}
function flash(msg){const f=$("flash");f.textContent=msg;f.classList.add("on");
  clearTimeout(f._t);f._t=setTimeout(()=>f.classList.remove("on"),900);}
async function setLabel(lab){
  if(!rows.length)return;
  const r=rows[idx];
  const res=await fetch(api("/api/label"),{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({id:r.id,label:lab})});
  if(!res.ok){flash("保存失败");return;}
  r.label=lab;
  flash("已标 "+lab+"　"+r.id);
  if(idx<rows.length-1){idx++;} else {toastEnd();}
  render();
  if(idx>=rows.length-1 && done()===rows.length){toastEnd();}
}
let endShown=false;
function toastEnd(){ if(!endShown){endShown=true; showScore();} }
async function showScore(){
  const s=await (await fetch(api("/api/score"))).json();
  const d=$("dlgBody");
  const f=x=>(x==null?"—":(100*x).toFixed(1)+"%");
  if(s.kind==="color_prior_ab"){
    d.innerHTML =
      "<h3 style='margin:0 0 10px'>第一批：颜色先验有没有让球员识别变好</h3>"+
      "<table><tr><th>策略</th><th>保留框</th><th>已标注</th><th>球员数</th><th>precision</th></tr>"+
      "<tr><td>有颜色先验（hi + lo_kept）</td><td>"+s.with_color.n_kept+"</td><td>"+
        s.with_color.n_labeled+"</td><td>"+s.with_color.n_player+"</td><td><b>"+f(s.with_color.precision)+"</b></td></tr>"+
      "<tr><td>无颜色先验（hi + 全部 lo）</td><td>"+s.without_color.n_kept+"</td><td>"+
        s.without_color.n_labeled+"</td><td>"+s.without_color.n_player+"</td><td><b>"+f(s.without_color.precision)+"</b></td></tr>"+
      "</table><p style='color:#9aa0a6;font-size:12px'>样本是分层抽的 ⇒ 样本内精确率</p>";
  } else if(s.kind==="repeat_progress"||s.kind==="pack_progress"){
    let tr="";
    for(const k in s.by_stratum){
      const v=s.by_stratum[k];
      tr+="<tr><td>"+k+"</td><td>"+v.n+"</td><td>"+v.n_labeled+"</td></tr>";
    }
    const title = s.title || "第三批：重复标注进度";
    const tail = s.after_done
      ? ("<code>"+s.after_done.replace(/\\\\/g,"\\\\")+"</code>")
      : ("<code>python tools\\repeat_agreement.py</code>");
    const tailNote = s.after_done
      ? "分层判决（层内非球员率 + Wilson CI + 与旧场保序检验）要标完才算，请跑：<br>"
      : "本页只报进度。一致性（Cohen's kappa、P/N 冲突率、按层拆分）要三批对齐才能算，请跑：<br>";
    d.innerHTML =
      "<h3 style='margin:0 0 10px'>"+title+"</h3>"+
      "<p style='font-size:13px;margin:0 0 8px'>已标 <b>"+s.n_labeled+"</b> / "+s.n_total+
        (s.done?"　<b style='color:#81c995'>已标完</b>":"　<span style='color:#9aa0a6'>（未标完）</span>")+"</p>"+
      "<table><tr><th>层</th><th>框数</th><th>已标</th></tr>"+tr+"</table>"+
      "<p style='color:#9aa0a6;font-size:12px;line-height:1.7;margin:8px 0 0'>"+
      tailNote+tail+"</p>";
  } else {
    const o=s.unknown_overall, ref=s.reference_hi_layer;
    let tr="";
    for(const k in s.by_stratum){
      const v=s.by_stratum[k];
      if(!v.n_labeled) continue;
      tr+="<tr><td>"+k+"</td><td>"+v.n_labeled+"</td><td>"+v.n_player+"</td><td>"+v.n_nonplayer+
          "</td><td><b>"+f(v.nonplayer_rate)+"</b></td><td>"+f(v.precision_if_kept)+"</td></tr>";
    }
    d.innerHTML =
      "<h3 style='margin:0 0 10px'>第二批：颜色判不出的框里，非球员是不是更多？</h3>"+
      "<p style='font-size:13px;color:#9aa0a6;margin:0 0 8px'>参照：hi 层整体（第一批 24 个）非球员占 <b>"+
        f(ref.nonplayer_rate)+"</b></p>"+
      "<table><tr><th>失败原因</th><th>已标注</th><th>球员</th><th>非球员</th><th>非球员率</th><th>保留时精度</th></tr>"+
      tr+
      "<tr><td><b>合计</b></td><td>"+o.n_labeled+"</td><td>"+o.n_player+"</td><td>"+o.n_nonplayer+
        "</td><td><b>"+f(o.nonplayer_rate)+"</b></td><td>"+f(o.precision_if_kept)+"</td></tr>"+
      "</table>"+
      "<p style='margin:12px 0 0;font-size:14px;line-height:1.7'><b>判决：</b>"+(s.verdict||"未标完，暂不判决")+"</p>"+
      "<p style='color:#9aa0a6;font-size:12px;line-height:1.7;margin:8px 0 0'>"+
      "非球员率明显高于 25% ⇒ 「颜色判不出」本身是信号；与 25% 无差异 ⇒ 颜色在 hi 层没信息量，放弃这条路。<br>"+
      "样本是分层抽的（按失败原因），样本内比例，不能外推。</p>";
  }
  $("dlg").showModal();
}
document.addEventListener("keydown",e=>{
  if($("dlg").open)return;
  const k=e.key.toLowerCase();
  if(k==="p"){e.preventDefault();setLabel("P");}
  else if(k==="n"){e.preventDefault();setLabel("N");}
  else if(k==="u"||k===" "){e.preventDefault();setLabel("U");}
  else if(k==="arrowright"){e.preventDefault();idx++;render();}
  else if(k==="arrowleft"){e.preventDefault();idx--;render();}
});
$("bp").onclick=()=>setLabel("P");
$("bn").onclick=()=>setLabel("N");
$("bu").onclick=()=>setLabel("U");
$("prev").onclick=()=>{idx--;render();};
$("next").onclick=()=>{idx++;render();};
$("btnScore").onclick=showScore;
const dlg=$("dlg");
(async()=>{
  // 批次从 URL 读，默认用服务端给的默认批次
  const b0=await (await fetch("/api/batches")).json();
  const want=parseInt(new URLSearchParams(location.search).get("batch")||"",10);
  BATCH=(Number.isFinite(want)&&b0.batches.some(b=>b.batch===want))?want:b0.default;
  const info=b0.batches.find(b=>b.batch===BATCH);
  $("bname").textContent=info?info.name:"";
  const st=await (await fetch(api("/api/state"))).json();
  rows=st.rows;
  const firstOpen=rows.findIndex(r=>!VALID.includes(r.label));
  idx=firstOpen>=0?firstOpen:0;
  render();
})();
</script></body></html>
"""


def main(argv=None):
    ap = argparse.ArgumentParser(description="球员/非球员标注网页")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--batch", type=int, default=DEFAULT_BATCH,
                    help="浏览器默认打开的批次（URL 里仍可用 ?batch=N 覆盖）")
    args = ap.parse_args(argv)

    if args.batch not in BATCHES:
        print("LABEL_WEB_FAIL bad_batch %s" % args.batch)
        return 2
    for b, cfg in BATCHES.items():
        if not cfg["csv"].exists():
            continue
        rows = load_rows(b)
        n_lab = sum(1 for r in rows if r["label"] in VALID)
        print("BATCH %d %s %d/%d %s" % (b, cfg["name"], n_lab, len(rows), cfg["csv"]),
              flush=True)
    rows = load_rows(args.batch)
    if not rows:
        print("LABEL_WEB_FAIL no_rows batch=%d" % args.batch)
        return 2
    n_lab = sum(1 for r in rows if r["label"] in VALID)

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    url = "http://%s:%d/?batch=%d" % (args.host, srv.server_address[1], args.batch)
    print("LABEL_WEB_OK %s %d/%d" % (url, n_lab, len(rows)), flush=True)
    print("（浏览器打开上面的地址；Ctrl+C 结束）", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())