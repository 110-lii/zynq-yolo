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
LABELS = REPO / "data" / "annot" / "labels.csv"

FIELDS = ["id", "stratum", "clip", "frame", "box_idx", "conf", "h_px",
          "color_role", "color_dist", "img", "label", "note"]
VALID = ("P", "N", "U")

_lock = threading.Lock()


def load_rows():
    if not LABELS.exists():
        return []
    with open(LABELS, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def save_rows(rows):
    tmp = LABELS.with_suffix(".csv.tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    tmp.replace(LABELS)


def score(rows):
    """分层加权的 precision 对比（与 make_player_labels.py --score 同口径）。"""
    def pr(sel, name):
        s = [r for r in rows if sel(r)]
        lab = [r["label"] for r in s if r["label"] in VALID]
        n_lab = len(lab)
        n_pos = sum(1 for x in lab if x == "P")
        return {
            "strategy": name,
            "n_kept": len(s),
            "n_labeled": n_lab,
            "n_player": n_pos,
            "precision": (n_pos / n_lab) if n_lab else None,
        }

    hi = lambda r: r["stratum"] == "hi"
    kept = lambda r: r["stratum"] in ("hi", "lo_kept")
    allb = lambda r: r["stratum"] in ("hi", "lo_kept", "lo_drop")
    with_col = pr(kept, "有颜色先验（hi + lo_kept）")
    without = pr(allb, "无颜色先验（hi + 全部 lo）")
    return {"with_color": with_col, "without_color": without}


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
        if u.path in ("/", "/index.html"):
            self._send(200, PAGE, "text/html; charset=utf-8")
            return
        if u.path.startswith("/frames/"):
            name = Path(u.path[len("/frames/"):]).name
            p = REPO / "data" / "annot" / "frames" / name
            if not p.exists():
                self._send(404, {"err": "no_such_frame"}, "application/json")
                return
            self._send(200, p.read_bytes(), "image/jpeg")
            return
        if u.path == "/api/state":
            with _lock:
                rows = load_rows()
            self._send(200, {
                "rows": rows,
                "n_total": len(rows),
                "n_labeled": sum(1 for r in rows if r["label"] in VALID),
            })
            return
        if u.path == "/api/score":
            with _lock:
                rows = load_rows()
            self._send(200, score(rows))
            return
        self._send(404, {"err": "not_found"})

    def do_POST(self):
        u = urlparse(self.path)
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
                rows = load_rows()
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
                save_rows(rows)
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
let rows=[], idx=0, rowsById={};
const $=id=>document.getElementById(id);
const VALID=["P","N","U"];

function done(){return rows.filter(r=>VALID.includes(r.label)).length;}
function render(){
  if(!rows.length){$("meta").textContent="没有数据";return;}
  idx=Math.max(0,Math.min(rows.length-1,idx));
  const r=rows[idx];
  $("im").src="/frames/"+r.img.split("/").pop();
  $("meta").innerHTML =
    "编号 <b>"+r.id+"</b> / "+rows.length+"<br>"+
    "分层 <b>"+r.stratum+"</b><br>"+
    "检测置信度 <b>"+r.conf+"</b>　框高 <b>"+r.h_px+" px</b><br>"+
    "颜色判定 <b>"+r.color_role+"</b>（距离 "+r.color_dist+"）<br>"+
    "是球员吗？当前：<b>"+(r.label||"—")+"</b>";
  $("sid").textContent="";
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
  const res=await fetch("/api/label",{method:"POST",headers:{"Content-Type":"application/json"},
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
  const r=await (await fetch("/api/score")).json();
  const fmt=x=>(x.precision==null?"—":(100*x.precision).toFixed(1)+"%");
  $("dlgBody").innerHTML =
    "<h3 style='margin:0 0 10px'>颜色先验有没有让球员识别变好</h3>"+
    "<table><tr><th>策略</th><th>保留框</th><th>已标注</th><th>球员数</th><th>precision</th></tr>"+
    "<tr><td>有颜色先验（hi + lo_kept）</td><td>"+r.with_color.n_kept+"</td><td>"+
      r.with_color.n_labeled+"</td><td>"+r.with_color.n_player+"</td><td><b>"+fmt(r.with_color)+"</b></td></tr>"+
    "<tr><td>无颜色先验（hi + 全部 lo）</td><td>"+r.without_color.n_kept+"</td><td>"+
      r.without_color.n_labeled+"</td><td>"+r.without_color.n_player+"</td><td><b>"+fmt(r.without_color)+"</b></td></tr>"+
    "</table>"+
    "<p style='color:#9aa0a6;font-size:12px;line-height:1.7'>样本是分层抽的，不是随机抽 ⇒ 这是<b>样本内</b>精确率，不能外推到全体。<br>"+
    "未标完时数字会变，标满 73 个才最终。</p>";
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
  const st=await (await fetch("/api/state")).json();
  rows=st.rows;
  // 从第一个未标的开始
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
    args = ap.parse_args(argv)

    if not LABELS.exists():
        print("LABEL_WEB_FAIL labels_csv_missing %s" % LABELS)
        return 2
    rows = load_rows()
    if not rows:
        print("LABEL_WEB_FAIL no_rows")
        return 2
    n_lab = sum(1 for r in rows if r["label"] in VALID)

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    url = "http://%s:%d/" % (args.host, srv.server_address[1])
    print("LABEL_WEB_OK %s %d/%d" % (url, n_lab, len(rows)), flush=True)
    print("（浏览器打开上面的地址；Ctrl+C 结束）", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())