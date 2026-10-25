"""label_ball.py -- 球/非球候选 ROI 的本地标注页。

候选由 tools/make_ball_rois.py 生成（build/ball_rois/*.png）。
标注结果写到 data/annot/ball_rois_labels.csv，随时关掉再开接着上次进度。

口径：只看正中间那个白块是不是足球。
    b = 是球
    n = 不是球（白袜/白鞋/白线/广告牌/观众席亮点）
    u = 看不清 / 只有半个

用法：
    python tools/label_ball.py
    # 浏览器打开 http://127.0.0.1:8766/
"""
import csv
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.dont_write_bytecode = True

REPO = Path(__file__).resolve().parents[1]
IMG_DIR = REPO / "build" / "ball_rois"
MANIFEST = REPO / "build" / "ball_rois.csv"
LABELS = REPO / "data" / "annot" / "ball_rois_labels.csv"
PORT = 8766


def _args():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default=str(MANIFEST))
    ap.add_argument("--img-dir", default=str(IMG_DIR))
    ap.add_argument("--labels", default=str(LABELS))
    ap.add_argument("--port", type=int, default=PORT)
    return ap.parse_args()

_lock = threading.Lock()


def load_manifest():
    with open(MANIFEST, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def load_labels():
    if not LABELS.exists():
        return {}
    with open(LABELS, encoding="utf-8", newline="") as fh:
        return {r["id"]: r for r in csv.DictReader(fh)}


def save_labels(labels):
    LABELS.parent.mkdir(parents=True, exist_ok=True)
    tmp = LABELS.with_suffix(".csv.tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["id", "label", "note"])
        w.writeheader()
        for k in sorted(labels, key=lambda x: int(x)):
            w.writerow({"id": k, "label": labels[k]["label"], "note": labels[k].get("note", "")})
    tmp.replace(LABELS)


HTML = """<!doctype html><html><head><meta charset="utf-8"><title>ball / not-ball</title>
<style>
body{background:#111;color:#ddd;font-family:system-ui,Segoe UI,sans-serif;margin:0}
#wrap{display:flex;flex-direction:column;align-items:center;padding:18px}
#pic{position:relative;width:512px;height:512px;background:#000;border-radius:6px}
#pic img{width:512px;height:512px;image-rendering:pixelated}
#cross{position:absolute;left:50%;top:50%;width:56px;height:56px;margin:-28px 0 0 -28px;
       border:2px solid #f0f;border-radius:50%;pointer-events:none}
#bar{display:flex;gap:10px;margin:16px 0}
button{font-size:20px;padding:10px 22px;border-radius:6px;border:1px solid #444;
       background:#1d1d1d;color:#eee;cursor:pointer}
button.b{background:#173a17;border-color:#2f6}button.n{background:#3a1717;border-color:#f66}
#meta{opacity:.75;font-size:14px}#prog{font-size:14px;opacity:.8}
</style></head><body><div id="wrap">
<div id="prog"></div>
<div id="pic"><img id="im"><div id="cross"></div></div>
<div id="bar">
 <button class="b" onclick="lab('b')">B 是球</button>
 <button class="n" onclick="lab('n')">N 不是</button>
 <button onclick="lab('u')">U 看不清</button>
 <button onclick="nav(-1)">&larr;</button><button onclick="nav(1)">&rarr;</button>
</div>
<div id="meta"></div>
</div><script>
let rows=[],labels={},i=0;
function cur(){return rows[i];}
function render(){
  if(!rows.length)return;
  const r=cur();
  document.getElementById('im').src='/img/'+r.img;
  const done=Object.keys(labels).length;
  document.getElementById('prog').textContent='进度 '+done+'/'+rows.length+'   #'+r.id+'  ('+(i+1)+'/'+rows.length+')';
  document.getElementById('meta').textContent='source='+r.source+'  frame='+r.frame+
    '  cand='+r.w+'x'+r.h+'px  score='+r.score+'  grass='+r.grass+
    (labels[r.id]?('   已标: '+labels[r.id].label):'');
}
function lab(v){
  const r=cur(); if(!r)return;
  fetch('/label',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id:r.id,label:v})}).then(()=>{labels[r.id]={label:v};nav(1);});
}
function nav(d){ i=(i+d+rows.length)%rows.length; render(); }
document.addEventListener('keydown',e=>{
  if(e.key==='b')lab('b'); else if(e.key==='n')lab('n'); else if(e.key==='u')lab('u');
  else if(e.key==='ArrowRight'){nav(1);e.preventDefault();}
  else if(e.key==='ArrowLeft'){nav(-1);e.preventDefault();}
});
fetch('/state').then(r=>r.json()).then(s=>{rows=s.rows;labels=s.labels||{};
  const first=rows.findIndex(r=>!labels[r.id]); i=first<0?0:first; render();});
</script></body></html>
"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        raw = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/":
            return self._send(200, HTML, "text/html; charset=utf-8")
        if self.path == "/state":
            with _lock:
                rows = load_manifest()
                labels = {k: {"label": v["label"]} for k, v in load_labels().items()}
            return self._send(200, json.dumps({"rows": rows, "labels": labels}))
        if self.path.startswith("/img/"):
            name = self.path.split("/img/", 1)[1]
            if "/" in name or "\\" in name or not name.endswith(".png"):
                return self._send(403, b"no")
            p = IMG_DIR / name
            if not p.exists():
                return self._send(404, b"no")
            return self._send(200, p.read_bytes(), "image/png")
        return self._send(404, b"no")

    def do_POST(self):
        if self.path != "/label":
            return self._send(404, b"no")
        n = int(self.headers.get("Content-Length", "0"))
        req = json.loads(self.rfile.read(n).decode("utf-8"))
        rid, lab = str(req["id"]), str(req["label"])
        if lab not in ("b", "n", "u"):
            return self._send(400, b"bad label")
        with _lock:
            labels = load_labels()
            labels[rid] = {"label": lab, "note": ""}
            save_labels(labels)
            done = len(labels)
            total = len(load_manifest())
        print("LABEL %s=%s  %d/%d" % (rid, lab, done, total), flush=True)
        return self._send(200, b"{}")


def main():
    global IMG_DIR, MANIFEST, LABELS, PORT
    a = _args()
    MANIFEST = Path(a.manifest)
    IMG_DIR = Path(a.img_dir)
    LABELS = Path(a.labels)
    PORT = a.port
    if not MANIFEST.exists():
        print("LABEL_BALL_FAIL manifest_missing (run tools/make_ball_rois.py)")
        return 2
    total = len(load_manifest())
    done = len(load_labels())
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print("LABEL_BALL_OK http://127.0.0.1:%d/ %d/%d" % (PORT, done, total), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())