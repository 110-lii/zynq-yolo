import os, shutil
from pathlib import Path
REPO=Path(r'D:\zynq-yolo')
SRC=REPO/'data'/'train'/'det'
DST=REPO/'data'/'train'/'det_person'
if DST.exists():
    shutil.rmtree(DST)
for split in ('train','val'):
    (DST/'images'/split).mkdir(parents=True,exist_ok=True)
    (DST/'labels'/split).mkdir(parents=True,exist_ok=True)
    n_img=n_lbl=n_box=0
    for img in (SRC/'images'/split).glob('*.jpg'):
        os.link(img, DST/'images'/split/img.name)
        n_img+=1
        lab=SRC/'labels'/split/(img.stem+'.txt')
        lines=[]
        if lab.exists():
            for line in lab.read_text(encoding='utf-8').splitlines():
                p=line.split()
                if len(p)==5 and int(float(p[0]))==0:
                    lines.append('0 '+' '.join(p[1:]))
        (DST/'labels'/split/(img.stem+'.txt')).write_text('\n'.join(lines),encoding='utf-8')
        n_lbl+=1; n_box+=len(lines)
    print(split,'images',n_img,'labels',n_lbl,'person_boxes',n_box)
(DST/'person.yaml').write_text('path: %s\ntrain: images/train\nval: images/val\nnc: 1\nnames: [person]\n' % DST.as_posix(),encoding='utf-8')
print('yaml',DST/'person.yaml')
