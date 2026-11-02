"""抽 N 张带 person 真值的 val 图 + 标签，打成板端复核用的小包。"""
import glob, json, os, random
import cv2
OUT = "build/_val40"
os.makedirs(OUT, exist_ok=True)
files = sorted(glob.glob("data/train/det/images/val/*.jpg"))
random.seed(0)
items = []
for f in files:
    if len(items) >= 40:
        break
    img = cv2.imread(f)
    if img is None:
        continue
    H, W = img.shape[:2]
    gts = []
    lf = f.replace("images", "labels").rsplit(".", 1)[0] + ".txt"
    if not os.path.exists(lf):
        continue
    for ln in open(lf):
        t = ln.split()
        if len(t) < 5 or t[0] != "0":
            continue
        cx, cy, w, h = map(float, t[1:5])
        gts.append([(cx-w/2)*W, (cy-h/2)*H, (cx+w/2)*W, (cy+h/2)*H])
    if not gts:
        continue
    name = "v%03d.jpg" % len(items)
    cv2.imwrite(os.path.join(OUT, name), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    items.append({"file": name, "w": W, "h": H, "gt": gts})
json.dump(items, open(os.path.join(OUT, "gt.json"), "w"))
print("PACK", len(items), "gt_boxes", sum(len(i["gt"]) for i in items))
