import glob, json, os, sys, time
import cv2
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import board_detect
SHIM = os.path.abspath(os.path.join(os.path.dirname(__file__), "_fake_infer.bat"))
def iou(a,b):
    x1,y1=max(a[0],b[0]),max(a[1],b[1]); x2,y2=min(a[2],b[2]),min(a[3],b[3])
    w,h=max(0,x2-x1),max(0,y2-y1); inter=w*h
    ua=(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-inter
    return inter/ua if ua>0 else 0
items=[]
for f in sorted(glob.glob("data/train/det/images/val/*.jpg"))[:100]:
    img=cv2.imread(f)
    if img is None: continue
    H,W=img.shape[:2]
    g=[]
    lf=f.replace("images","labels").rsplit(".",1)[0]+".txt"
    if not os.path.exists(lf): continue
    for ln in open(lf):
        t=ln.split()
        if len(t)<5 or t[0]!="0": continue
        cx,cy,w,h=map(float,t[1:5]); g.append([(cx-w/2)*W,(cy-h/2)*H,(cx+w/2)*W,(cy+h/2)*H])
    if g: items.append((f,g))
CFG=[("v8n25@320lb","build/models/person_v8n25",320,True),
     ("v8n25@320sq","build/models/person_v8n25",320,False),
     ("w05@480lb","build/models/person_w05_480",480,True),
     ("w05@640lb","build/models/person_w05_640",640,True)]
for name,stem,S,lb in CFG:
    t0=time.time(); hits=ngt=0
    for f,g in items:
        dets,_=board_detect.detect(f,SHIM,stem+".param",stem+".bin",0.25,0.45,size=S,letterbox=lb)
        ngt+=len(g); hits+=sum(1 for gg in g if any(iou(gg,d)>=0.5 for d in dets))
    print("MICRO100 %-12s recall=%.3f gt=%d imgs=%d %.0fs"%(name,hits/ngt,ngt,len(items),time.time()-t0),flush=True)
