"""赛前输入：把「双方队服颜色 / 裁判颜色 / 各自半场」录成一份**机读配置**（D013 的逻辑真源）。

为什么需要它
------------
D013 决定把板载 4.3 寸触摸屏当**数据输入端**：赛前由人录入
「双方球队球衣颜色、裁判球衣颜色、各自半场」。这份配置是下游三件事的共同输入：

1. `tools/cross_match_eval.py` 的 `--palette`（两支队的颜色名）；
2. `tools/kit_calib.py` 的按场色板校准（实测参考色 → `--palette-refs`）；
3. 后续的球权/半场相关逻辑（哪个队守哪边）。

本工具只做两件**与输入设备无关**的事：**定义 schema + 校验 + 写盘**，并额外提供一个
网页前端作为**触屏 UI 的模拟**（现在就能演示/测试；真机 LCD 版本应复用同一份校验与 schema）。

为什么不直接把颜色写进命令行
----------------------------
命令行参数没有留痕、无法校验、也无法区分「谁在什么时候录的」。而这份配置是
**人工输入的证据**：出错时要能回答「是录入错了还是算法错了」。所以：
- 文件带 `entered_by` / `entered_at` / `source`；
- 颜色名必须是 `classify_players.PALETTE_REF` 的键（防拼写错误静默变 unknown）；
- 两队颜色不能同名；裁判颜色不能与两队相近（相近只 WARN，因为脏球衣/光照会漂移）。

用法
----
    # 1) 网页录入（模拟触屏；默认 http://127.0.0.1:8767）
    & D:\\python\\python.exe tools\\match_setup.py --serve

    # 2) 命令行录入（脚本化/无浏览器时）
    & D:\\python\\python.exe tools\\match_setup.py --set `
        --match-id migu968460165 --team-a 大连英博海发 --team-b 深圳新鹏城 `
        --color-a white --color-b blue --ref-color red `
        --half-a first_left

    # 3) 查看 / 列表 / 打印可直接复制的下游命令
    & D:\\python\\python.exe tools\\match_setup.py --show migu968460165
    & D:\\python\\python.exe tools\\match_setup.py --list
    & D:\\python\\python.exe tools\\match_setup.py --print-cmd migu968460165 `
        --video "D:\\yt-dlp\\downloads\\migu_968460165\\<全场 mp4>"

产物
----
    data/config/match_<id>.json      # 机读配置（人工输入的证据）
    data/config/README.md            # 目录说明（首次写入时生成）

判据令牌
--------
    MATCH_SETUP_OK <id> <A颜色>/<B颜色> ref=<裁判颜色>
    MATCH_SETUP_FAIL <reason>        # 颜色名非法 / 两队同名 / 半场非法 / 找不到配置
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import importlib.util
import json
import re
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools"
CONFIG_DIR = REPO / "data" / "config"

VALID_HALVES = ("first_left", "first_right", "unknown")


def _load(path, name):
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


kl = _load(TOOLS / "classify_players.py", "_ms_classify")

COLOR_CHOICES = sorted(kl.PALETTE_REF)


def _slug(text):
    s = re.sub(r"[^A-Za-z0-9]+", "_", str(text)).strip("_").lower()
    return s[:48] or "match"


def _validate(payload):
    """返回 (problems, warnings)。problems 非空 ⇒ 不写盘。"""
    probs, warns = [], []
    teams = payload.get("teams") or {}
    for key in ("A", "B"):
        t = teams.get(key) or {}
        c = t.get("kit_color")
        if c not in kl.PALETTE_REF:
            probs.append("bad_color_%s %r（可选：%s）" % (key, c, ", ".join(COLOR_CHOICES)))
        if not t.get("name"):
            warns.append("team_%s_name_empty（只影响报告可读性）" % key)
    a = (teams.get("A") or {}).get("kit_color")
    b = (teams.get("B") or {}).get("kit_color")
    if a and b and a == b:
        probs.append("same_team_color %s" % a)
    ref = (payload.get("referee") or {}).get("kit_color")
    if ref is not None and ref not in kl.PALETTE_REF:
        probs.append("bad_color_ref %r（可选：%s）" % (ref, ", ".join(COLOR_CHOICES)))
    for key in ("A", "B"):
        half = (teams.get(key) or {}).get("first_half_side")
        if half not in VALID_HALVES:
            probs.append("bad_half_%s %r（可选：%s）" % (key, half, ", ".join(VALID_HALVES)))
    if ref and ref not in (a, b):
        # 裁判色与队色相近才 WARN：颜色空间上「近」用的是同一把尺子
        for key, c in (("A", a), ("B", b)):
            if not c:
                continue
            d = kl.distance_to_ref(kl.color_of_name(ref), kl.ref_of(c))[0]
            if d < kl.MAX_COLOR_DIST:
                warns.append("referee_color_near_team_%s dist=%.3f（裁判易被判成该队）"
                             % (key, d))
    if (teams.get("A") or {}).get("first_half_side") == \
            (teams.get("B") or {}).get("first_half_side") and \
            (teams.get("A") or {}).get("first_half_side") not in (None, "unknown"):
        probs.append("same_half_side（两队不可能同侧开局）")
    return probs, warns


def build_payload(match_id, team_a, team_b, color_a, color_b, ref_color,
                  half_a, half_b, calib_file, source):
    return {
        "schema": "zynq-yolo/match-config/1",
        "match": {"id": match_id, "entered_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")},
        "teams": {
            "A": {"name": team_a, "kit_color": color_a, "first_half_side": half_a},
            "B": {"name": team_b, "kit_color": color_b, "first_half_side": half_b},
        },
        "referee": {"kit_color": ref_color},
        "calibration": {"refs_file": calib_file,
                        "note": "按场实测参考色（tools/kit_calib.py 产出）；留空则用预置 PALETTE_REF"},
        "entered_by": "human",
        "source": source,
        "why": "D013：LCD 触屏作为数据输入端——颜色与半场由人录入，不由算法猜",
    }


def save(payload):
    probs, warns = _validate(payload)
    if probs:
        return None, probs, warns
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    readme = CONFIG_DIR / "README.md"
    if not readme.exists():
        readme.write_text(
            "# data/config —— 赛前人工输入（D013）\n\n"
            "| | |\n|---|---|\n"
            "| 本目录装什么 | 每场比赛一份 `match_<id>.json`：双方队名/队服颜色/裁判颜色/各自半场，"
            "以及按场校准的参考色文件路径 |\n"
            "| 谁产生 | **人**（`tools/match_setup.py`，真机上由 LCD 触屏录入） |\n"
            "| 进 git | 是——它是「人工输入」的证据，出错时要能分清录入错还是算法错 |\n"
            "| 生成方式 | `python tools/match_setup.py --serve`（网页模拟）或 `--set`（命令行） |\n",
            encoding="utf-8", newline="\n")
    path = CONFIG_DIR / ("match_%s.json" % _slug(payload["match"]["id"]))
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                    encoding="utf-8", newline="\n")
    return path, [], warns


def load(match_id):
    path = CONFIG_DIR / ("match_%s.json" % _slug(match_id))
    if not path.exists():
        return None, "config_missing %s" % path
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except Exception as exc:                              # noqa: BLE001
        return None, "config_unreadable %s: %s" % (path, exc)


def print_cmd(cfg, video):
    teams = cfg["teams"]
    a, b = teams["A"]["kit_color"], teams["B"]["kit_color"]
    refs = (cfg.get("calibration") or {}).get("refs_file") or ""
    lines = [
        "# 复制即用（色板 %s,%s；校准参考色 %s）" % (a, b, refs or "（无，用预置 PALETTE_REF）"),
        r"D:\python\python.exe tools\cross_match_eval.py `",
        r'  --video-dir "%s" `' % video,
        "  --palette %s,%s `" % (a, b),
    ]
    if refs:
        lines.append("  --palette-refs %s `" % refs)
    lines += ["  --n-clips 12 --play-windows --max-total-s 0"]
    lines.append("# 裁判色 = %s；半场：A=%s / B=%s"
                 % ((cfg.get("referee") or {}).get("kit_color") or "未录",
                    teams["A"].get("first_half_side"), teams["B"].get("first_half_side")))
    return "\n".join(lines)


HTML = """<!doctype html><html><head><meta charset="utf-8">
<title>赛前输入（LCD 触屏模拟）</title>
<style>
 body{background:#111;color:#eee;font:15px/1.6 system-ui;margin:0;padding:18px}
 h1{font-size:19px;margin:0 0 4px} .sub{color:#9aa0a6;font-size:13px;margin-bottom:16px}
 .row{margin:10px 0} .lbl{display:inline-block;width:104px;color:#9aa0a6}
 input[type=text]{background:#1e1e1e;border:1px solid #333;color:#eee;padding:8px;border-radius:6px;width:220px}
 .sw{display:inline-block;width:58px;height:58px;margin:4px;border-radius:8px;border:3px solid #333;cursor:pointer;position:relative}
 .sw.sel{border-color:#8ab4f8} .sw span{position:absolute;bottom:-2px;left:0;right:0;font-size:10px;text-align:center;color:#000;background:#fffc}
 button{background:#8ab4f8;color:#111;border:0;padding:10px 18px;border-radius:8px;font-size:15px;cursor:pointer;margin-top:14px}
 #out{white-space:pre-wrap;background:#1a1a1a;border:1px solid #333;border-radius:8px;padding:12px;margin-top:14px;font-size:13px;color:#b7f7c1}
 .warn{color:#fdd663} .err{color:#f28b82}
</style></head><body>
<h1>赛前输入：队服颜色 / 裁判颜色 / 各自半场</h1>
<div class="sub">这是板载 4.3 寸触摸屏的**网页模拟**；真机 UI 复用同一份 schema 与校验（D013）。</div>
<div class="row"><span class="lbl">比赛 ID</span><input id="mid" type="text" value="migu968460165"></div>
<div class="row"><span class="lbl">A 队名</span><input id="ta" type="text" value="大连英博海发">
  <span class="lbl" style="width:70px">先开左半</span><select id="ha"><option value="first_left">是</option><option value="first_right">否</option><option value="unknown">未录</option></select></div>
<div class="row"><span class="lbl">A 队队服</span><div id="ca" style="display:inline-block"></div></div>
<div class="row"><span class="lbl">B 队名</span><input id="tb" type="text" value="深圳新鹏城">
  <span class="lbl" style="width:70px">先开左半</span><select id="hb"><option value="first_right">是</option><option value="first_left">否</option><option value="unknown">未录</option></select></div>
<div class="row"><span class="lbl">B 队队服</span><div id="cb" style="display:inline-block"></div></div>
<div class="row"><span class="lbl">裁判队服</span><div id="cr" style="display:inline-block"></div></div>
<div class="row"><span class="lbl">校准参考色</span><input id="refs" type="text" value="data/results/kit_calib.json" style="width:320px"></div>
<button onclick="save()">保存并生成下游命令</button>
<div id="out"></div>
<script>
const COLORS=__COLORS__;
let sel={ca:null,cb:null,cr:null};
function paint(){
  for(const box of ["ca","cb","cr"]){
    const el=document.getElementById(box); el.innerHTML="";
    for(const c of COLORS){
      const d=document.createElement("div"); d.className="sw"+(sel[box]===c?" sel":"");
      d.style.background=CSS[c]||"#555"; d.title=c; d.onclick=()=>{sel[box]=c;paint();};
      const s=document.createElement("span"); s.textContent=c; d.appendChild(s); el.appendChild(d);
    }
  }
}
const CSS={red:"#c00",orange:"#e80",yellow:"#dd0",green:"#080",cyan:"#0aa",blue:"#1580ff",
purple:"#80c",magenta:"#c3c",white:"#fff",black:"#222"};
paint();
async function save(){
  const body={match_id:document.getElementById("mid").value,
    team_a:document.getElementById("ta").value,team_b:document.getElementById("tb").value,
    color_a:sel.ca,color_b:sel.cb,ref_color:sel.cr,
    half_a:document.getElementById("ha").value,half_b:document.getElementById("hb").value,
    refs_file:document.getElementById("refs").value};
  const r=await (await fetch("/api/save",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify(body)})).json();
  const o=document.getElementById("out");
  if(r.ok){o.innerHTML="<span style='color:#b7f7c1'>已保存 "+r.path+"</span>\\n"+r.cmd;} 
  else{o.innerHTML="<span class='err'>未保存</span>\\n"+(r.problems||[]).join("\\n")
    +"\\n"+(r.warns||[]).map(w=>"<span class='warn'>WARN "+w+"</span>").join("\\n");}
}
</script></body></html>"""


def serve(port):
    import http.server
    import socketserver
    from urllib.parse import urlparse

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path in ("/", "/index.html"):
                html = HTML.replace("__COLORS__", json.dumps(COLOR_CHOICES))
                self._send(200, html, "text/html; charset=utf-8")
                return
            self._send(404, b"{}")

        def do_POST(self):
            u = urlparse(self.path)
            if u.path != "/api/save":
                self._send(404, b"{}")
                return
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n).decode("utf-8"))
            except Exception:                       # noqa: BLE001
                self._send(400, json.dumps({"ok": False, "problems": ["bad_json"]}))
                return
            payload = build_payload(body.get("match_id") or "match",
                                    body.get("team_a") or "", body.get("team_b") or "",
                                    body.get("color_a"), body.get("color_b"),
                                    body.get("ref_color"),
                                    body.get("half_a") or "unknown",
                                    body.get("half_b") or "unknown",
                                    body.get("refs_file") or "", "web_touch_mock (8767)")
            path, probs, warns = save(payload)
            if path is None:
                self._send(200, json.dumps({"ok": False, "problems": probs,
                                            "warns": warns}, ensure_ascii=False))
                return
            cmd = print_cmd(payload, "<视频路径>")
            self._send(200, json.dumps({"ok": True, "path": str(path),
                                        "warns": warns, "cmd": cmd},
                                       ensure_ascii=False))

    with socketserver.TCPServer(("127.0.0.1", port), H) as srv:
        print("赛前输入（触屏模拟）: http://127.0.0.1:%d/" % port)
        print("颜色可选：%s" % ", ".join(COLOR_CHOICES))
        srv.serve_forever()


def main(argv=None):
    ap = argparse.ArgumentParser(description="赛前输入（队服颜色/裁判颜色/半场）→ data/config/match_<id>.json")
    ap.add_argument("--serve", action="store_true", help="起网页前端（模拟 LCD 触屏）")
    ap.add_argument("--port", type=int, default=8767)
    ap.add_argument("--set", action="store_true", help="命令行录入")
    ap.add_argument("--show", default="", help="打印某场比赛的配置")
    ap.add_argument("--list", action="store_true", help="列出已录入的比赛")
    ap.add_argument("--print-cmd", default="", help="打印可直接复制的下游命令")
    ap.add_argument("--match-id", default="")
    ap.add_argument("--team-a", default="A 队")
    ap.add_argument("--team-b", default="B 队")
    ap.add_argument("--color-a", default="")
    ap.add_argument("--color-b", default="")
    ap.add_argument("--ref-color", default=None)
    ap.add_argument("--half-a", default="unknown", choices=VALID_HALVES)
    ap.add_argument("--half-b", default="unknown", choices=VALID_HALVES)
    ap.add_argument("--refs-file", default="", help="kit_calib.json（按场校准参考色）")
    ap.add_argument("--video", default="<全场 mp4>", help="--print-cmd 用的视频路径")
    args = ap.parse_args(argv)

    if args.serve:
        serve(args.port)
        return 0

    if args.list:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        files = sorted(CONFIG_DIR.glob("match_*.json"))
        if not files:
            print("MATCH_SETUP_FAIL no_configs（先 --set 或 --serve 录一场）")
            return 2
        for f in files:
            cfg = json.loads(f.read_text(encoding="utf-8"))
            t = cfg["teams"]
            print("  %-28s %s=%s / %s=%s  ref=%s  entered=%s"
                  % (f.name, t["A"]["kit_color"], t["A"]["name"],
                     t["B"]["kit_color"], t["B"]["name"],
                     (cfg.get("referee") or {}).get("kit_color") or "-",
                     (cfg.get("match") or {}).get("entered_at")))
        print("MATCH_SETUP_OK list %d" % len(files))
        return 0

    if args.show or args.print_cmd:
        mid = args.show or args.print_cmd
        cfg, err = load(mid)
        if err:
            print("MATCH_SETUP_FAIL %s" % err)
            return 2
        if args.show:
            print(json.dumps(cfg, ensure_ascii=False, indent=1))
        if args.print_cmd:
            print(print_cmd(cfg, args.video))
        print("MATCH_SETUP_OK %s %s/%s ref=%s"
              % (mid, cfg["teams"]["A"]["kit_color"], cfg["teams"]["B"]["kit_color"],
                 (cfg.get("referee") or {}).get("kit_color")))
        return 0

    if args.set:
        if not args.match_id:
            print("MATCH_SETUP_FAIL match_id_missing（--set 需要 --match-id）")
            return 2
        payload = build_payload(args.match_id, args.team_a, args.team_b,
                                args.color_a, args.color_b, args.ref_color,
                                args.half_a, args.half_b, args.refs_file, "cli")
        path, probs, warns = save(payload)
        if path is None:
            print("MATCH_SETUP_FAIL %s" % probs[0])
            for p in probs[1:]:
                print("  - %s" % p)
            return 2
        for w in warns:
            print("  WARN %s" % w)
        print("  已写 %s" % path)
        print(print_cmd(payload, args.video))
        print("MATCH_SETUP_OK %s %s/%s ref=%s"
              % (args.match_id, args.color_a, args.color_b, args.ref_color))
        return 0

    ap.print_help()
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:                        # noqa: BLE001 - 兜底也要留令牌
        print("MATCH_SETUP_FAIL uncaught %s: %s" % (type(exc).__name__, exc))
        raise SystemExit(2)