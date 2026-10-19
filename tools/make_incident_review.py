"""把候选事件片段做成一个单页复核页，让人一次看完并填裁定。

为什么要有这个
--------------
`tools/find_incidents.py` 只负责「把值得人看的时刻切出来」，但 12 个 mp4 一个个
打开、再回头找对应的 CSV 行去填，是纯粹浪费人的时间。判罚金标准只有人能提供，
所以让人**一次看完全部候选、当场填、一键导出能直接粘回来的 CSV**。

产物 `data/samples/incidents/review.html`：
  * 每行一个候选：片段（可播）、三张静帧、切换密度、时间；
  * 每行三个输入：结论 / 置信度 / 备注，结论是下拉（避免自由文本导致统计困难）；
  * 顶部一个「导出 CSV」按钮，产出与 MANIFEST.csv 同列同序的文本，复制回来即可。

★ 本页是**本地便利视图**，不进 git（`data/samples/**/*.html` 已排除）；
  真正入库的裁定载体是 `MANIFEST.csv`。
"""
import csv
import html
import sys
from pathlib import Path

sys.dont_write_bytecode = True

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "data" / "samples" / "incidents"

# 结论枚举。**必须固定**：自由文本会让「一共判了几个越位」这种统计做不了。
CHOICES = ["", "进球", "越位", "出界", "手球", "犯规", "点球", "红黄牌",
           "无事件", "看不清"]


def fmt(t):
    t = float(t)
    return "%d:%05.1f" % (int(t // 60), t % 60)


def main():
    man = OUT / "MANIFEST.csv"
    if not man.exists():
        print("MAKE_REVIEW_FAIL 先跑 tools/find_incidents.py --extract")
        return 1
    with open(man, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    out = []
    w = out.append
    w("<!doctype html><html lang='zh'><meta charset='utf-8'>")
    w("<title>候选事件复核</title>")
    w("""<style>
body{font:14px/1.5 system-ui,"Microsoft YaHei",sans-serif;background:#14171c;color:#e8eaed;margin:0;padding:18px}
h1{font-size:19px;margin:0 0 6px}
p.hint{color:#9aa0a6;margin:0 0 14px;font-size:13px}
table{border-collapse:collapse;width:100%}
th,td{border:1px solid #2c313a;padding:6px 8px;vertical-align:top;text-align:left}
th{background:#1d2128;position:sticky;top:0;z-index:2}
video{width:420px;background:#000;display:block}
img{width:132px;margin:2px 2px 0 0;border:1px solid #2c313a}
select,input,textarea{background:#1d2128;color:#e8eaed;border:1px solid #3c4149;border-radius:3px;padding:3px 5px;font:inherit;width:100%}
textarea{height:52px}
.id{font-weight:600;color:#8ab4f8;white-space:nowrap}
.t{color:#9aa0a6;font-size:12px;white-space:nowrap}
#bar{position:fixed;right:16px;bottom:16px;background:#1d2128;border:1px solid #3c4149;border-radius:6px;padding:10px 12px;box-shadow:0 4px 18px #0008}
#bar button{background:#8ab4f8;color:#14171c;border:0;border-radius:4px;padding:7px 14px;font:inherit;font-weight:600;cursor:pointer}
#out{width:min(1100px,90vw);height:150px;margin-top:8px;display:none;font-family:ui-monospace,Consolas,monospace;font-size:12px}
</style>""")
    w("<h1>候选事件复核（%d 条）</h1>" % len(rows))
    w("<p class='hint'>填「结论」一列后点右下角导出，把文本贴回对话即可。"
      "结论请只从下拉里选——自由文本会让「一共判了几个越位」这种统计做不了。"
      "看不清就选「看不清」，这本身是有用的信息（说明我切错了位置）。</p>")
    w("<table><thead><tr>"
      "<th>#</th><th>片段</th><th>静帧 -1s / 0 / +1s</th>"
      "<th>视频时间</th><th>切换</th><th>结论</th><th>置信度</th><th>备注</th>"
      "</tr></thead><tbody>")

    for r in rows:
        iid = r["incident_id"]
        opts = "".join("<option value='%s'>%s</option>" % (html.escape(c), html.escape(c) or "—请选择—")
                       for c in CHOICES)
        stills = "".join(
            "<a href='%s' target='_blank'><img src='%s' title='%s'></a>" % (r[k], r[k], k)
            for k in ("still_before", "still_at", "still_after") if r.get(k))
        w("<tr>")
        w("<td class='id'>%s</td>" % iid)
        w("<td><video src='%s' controls preload='metadata'></video></td>" % r["clip_file"])
        w("<td>%s</td>" % stills)
        w("<td class='t'>%s s<br>(%s)</td>" % (r["t_center_s"], fmt(r["t_center_s"])))
        w("<td class='t'>%s</td>" % r["n_cuts"])
        w("<td><select id='d_%s'>%s</select></td>" % (iid, opts))
        w("<td><input id='c_%s' placeholder='0.0-1.0'></td>" % iid)
        w("<td><textarea id='n_%s'></textarea></td>" % iid)
        w("</tr>")
    w("</tbody></table>")

    w("<div id='bar'><button onclick='exp()'>导出 CSV</button>"
      "<textarea id='out' readonly></textarea></div>")
    ids = [r["incident_id"] for r in rows]
    w("<script>const IDS=%s;</script>" % str(ids).replace("'", '"'))
    w("""<script>
function exp(){
  let L=['incident_id,decision,confidence,notes'];
  for(const id of IDS){
    const d=document.getElementById('d_'+id).value;
    const c=document.getElementById('c_'+id).value;
    const n=document.getElementById('n_'+id).value.replace(/[\\r\\n,]+/g,' ').trim();
    L.push([id,d,c,n].join(','));
  }
  const t=L.join('\\n');
  const o=document.getElementById('out');
  o.style.display='block';o.value=t;o.select();
  try{document.execCommand('copy');}catch(e){}
}
</script></body></html>""")

    dst = OUT / "review.html"
    with open(dst, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out))
    print("复核页 -> %s（%d 条候选）" % (dst.relative_to(REPO), len(rows)))
    print("MAKE_REVIEW_OK %d" % len(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
