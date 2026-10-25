"""label_field_points.py —— 手动标定：在画面上点 4~8 个已知点，生成点对 JSON

用法（PC 上，需要图形界面）：
    python tools/label_field_points.py --image 某帧.jpg --out data/calib/pairs.json
    python tools/label_field_points.py --video 某段.mp4 --t 5.0 --out data/calib/pairs.json

操作：
    鼠标左键   添加一个点（编号显示在图上）
    鼠标右键   撤销上一个点
    q / Esc    结束点选
结束后在控制台按顺序为每个点输入球场坐标（米），例如：0 0

球场坐标约定（方案 §12）：
    x 沿球场长度 0~105 m，y 沿宽度 0~68 m。
    常见点的标准坐标：
        左侧底线两角     (0,0)      (0,68)
        右侧底线两角     (105,0)    (105,68)
        左侧禁区两角     (16.5,13.84) (16.5,54.16)
        右侧禁区两角     (88.5,13.84) (88.5,54.16)
        中点             (52.5,34)
    点的顺序不必固定，只要"图像点"和"球场点"一一对应即可。
"""
import argparse
import json
import os

import cv2


def grab_frame(video, t):
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        raise SystemExit("无法打开视频: %s" % video)
    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise SystemExit("取帧失败: t=%.2f" % t)
    return frame


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default="")
    ap.add_argument("--video", default="")
    ap.add_argument("--t", type=float, default=0.0)
    ap.add_argument("--out", default="data/calib/pairs.json")
    args = ap.parse_args()

    if args.image:
        img = cv2.imread(args.image)
        if img is None:
            raise SystemExit("无法读取图片: %s" % args.image)
    elif args.video:
        img = grab_frame(args.video, args.t)
    else:
        raise SystemExit("需要 --image 或 --video")

    pts = []
    win = "label_field_points (left=add, right=undo, q=done)"

    def on_mouse(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            pts.append((x, y))
        elif event == cv2.EVENT_RBUTTONDOWN and pts:
            pts.pop()

    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(win, on_mouse)
    while True:
        view = img.copy()
        for i, (x, y) in enumerate(pts):
            cv2.circle(view, (x, y), 6, (0, 0, 255), -1)
            cv2.putText(view, str(i + 1), (x + 8, y - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        cv2.imshow(win, view)
        k = cv2.waitKey(30) & 0xFF
        if k in (ord("q"), 27):
            break
    cv2.destroyAllWindows()

    if len(pts) < 4:
        raise SystemExit("至少需要 4 个点，当前 %d 个" % len(pts))

    print("\n已选 %d 个点。按顺序输入对应的球场坐标（米），格式：x y" % len(pts))
    field = []
    for i, (u, v) in enumerate(pts):
        while True:
            s = input("  点%d 图像(%d,%d) -> 球场 x y: " % (i + 1, u, v)).strip()
            t = s.replace(",", " ").split()
            if len(t) == 2:
                try:
                    field.append([float(t[0]), float(t[1])])
                    break
                except ValueError:
                    pass
            print("    格式不对，请输入两个数，例如：16.5 13.84")

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    json.dump({"image": [[p[0], p[1]] for p in pts], "field": field},
              open(args.out, "w", encoding="utf-8"), indent=2)
    print("\n已保存: %s（%d 个点对）" % (args.out, len(pts)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())