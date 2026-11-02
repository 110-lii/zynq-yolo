import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import board_detect
SHIM = os.path.abspath(os.path.join(os.path.dirname(__file__), "_fake_infer.bat"))
items = json.load(open("build/_val40/gt.json"))
def iou(a,b):
    x1,y1=max(a[0],b[0]),max(a[1],b[1]); x2,y2=min(a[2],b[2]),min(a[3],b[3])
    w,h=max(0,x2-x1),max(0,y2-y1); inter=w*h
    ua=(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-inter
    return inter/ua if ua>0 else 0
CFG=[("v8n25@320","build/models/person_v8n25",320),
     ("w05@320","build/models/person_w05_320",320),
     ("w05@480","build/models/person_w05_480",480),
     ("w05@640","build/models/person_w05_640",640)]
for name,stem,S in CFG:
    hits=ngt=0
    for it in items:
        dets,_=board_detect.detect("build/_val40/"+it["file"], SHIM, stem+".param", stem+".bin",
                                   0.25,0.45,size=S,letterbox=True)
        ngt+=len(it["gt"])
        hits+=sum(1 for g in it["gt"] if any(iou(g,d)>=0.5 for d in dets))
    print("MICRO %-10s recall=%.3f (gt=%d)"%(name,hits/ngt,ngt))
