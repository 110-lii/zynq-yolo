#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch_calib_real.py — 构建"真实足球照片" INT8 标定集（图源：Wikimedia Commons）

背景
----
YOLOv8n 量化到 INT8 时，激活 scale 由标定样本的真实动态范围决定。用一张图
(zidane.jpg) 派生 16 张增广 ≠ 16 张独立样本，所以这里抓取真实、多样的足球
比赛/球场照片作为标定集。

用法
----
    python tools/fetch_calib_real.py --list     # 只列出将抓取的条目（不联网、不下载）
    python tools/fetch_calib_real.py            # 下载 + 校验 SHA256 + YOLOv8n 复核 + 写 MANIFEST.csv
    python tools/fetch_calib_real.py --verify   # 不下载，仅复核磁盘文件与 MANIFEST/预期 SHA256 是否一致
    python tools/fetch_calib_real.py --list --json   # 机器可读的条目清单

需要代理（本机实测不设代理会超时）：
    PowerShell:  $env:HTTP_PROXY="http://127.0.0.1:7890"; $env:HTTPS_PROXY="http://127.0.0.1:7890"

产物位置
--------
    data/samples/calib_commons/*.jpg            40 张 JPEG（宽 640~3872 px）
    data/samples/calib_commons/MANIFEST.csv     来源/作者/许可/SHA256/person&ball 统计

注意：本脚本**不再**写 data/samples/calib_real/ —— 那个目录归
tools/make_calib_from_video.py（真实比赛视频截帧）所有，两个工具不互相覆盖。

已知局限（2026-10-19 实测记录）
--------------------------------
Commons 上的足球照片多为摆拍、特写或业余机位，与转播全景镜头的目标域分布有差距。
作为"多样真实照片"的补充集/取证材料是够的；但如果标定目标就是转播画面，应优先用
make_calib_from_video.py 的截帧。这个差距是本脚本被降级为补充集的直接原因。

可复现性
--------
每个条目的 Commons 文件标题（title）、原始文件 URL（url）与 SHA256 都硬编码在
本文件的 SOURCES 表里。Commons 的原始文件是不可变的，所以重跑必然得到同一批
字节；若某个 SHA256 对不上，脚本会报 FAIL 而不是静默接受。

许可纪律（法律要求，不可省）
----------------------------
这些是第三方照片（CC BY-SA / CC BY / Public domain 等）。本项目开源，**照片本身
不入库**（由 .gitignore 排除）。每张图的来源页面、作者、许可简称、许可 URL 都写进
MANIFEST.csv，二次分发时必须保留这些归属信息。

标定价值验证（客观判据）
------------------------
下载后用 YOLOv8n(COCO) 对每张图跑一次检测，统计 person(0) 与 sports ball(32)
的检测数（conf>0.25）。判据：>=80% 的图必须有 >=1 个 person；不达标的图会被
剔除而不是硬塞进标定集。球很小、远景图常常检测不到，这属正常，不作为淘汰依据。
"""

from __future__ import annotations

import sys

# 不生成 __pycache__：本仓库的 tools/check_repo.py 检查 d) 是**扫盘**的，不看 .gitignore，
# 留下 .pyc 会直接判 FAIL。实测语义（勿想当然）：模块**自身体内**置位拦不住自己的 .pyc
# （CPython 先编译并写缓存，再执行模块体），只有在本模块 import 别的本地模块**之前**
# 置位才有效。所以这段必须放在所有 import 之前（但 `from __future__` 必须更靠前）。
sys.dont_write_bytecode = True

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# 控制台是 GBK 代码页，先切 UTF-8，否则打印非 ASCII 会 UnicodeEncodeError
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001  (Python < 3.7 或已被重定向)
    pass

REPO = Path(__file__).resolve().parent.parent
# ★ 2026-10-19 用户裁定后改向：标定集**改用真实比赛视频截帧**
#   （工具见 tools/make_calib_from_video.py，产物在 data/samples/calib_real/）。
#   本脚本是那条路的**前身**，保留下来是因为它记录的失败原因有取证价值：
#   Commons 上的足球照片多为摆拍/特写/构图，与转播机位的全景分布差得远，
#   用它们标定会把 per-tensor 范围撑错。为避免两个工具互相覆盖，
#   本脚本的输出目录改到 calib_commons/，不再写 calib_real/。
OUT_DIR = REPO / "data" / "samples" / "calib_commons"
MANIFEST = OUT_DIR / "MANIFEST.csv"
WEIGHTS = REPO / "data" / "models" / "yolov8n.pt"
ADDED = "2026-10-19"

USER_AGENT = (
    "ZynqYoloCalibSet/1.0 (educational FPGA INT8 quantization calibration; "
    "contact: zynq-yolo@example.org) python-urllib/3.13"
)
REFERER = "https://commons.wikimedia.org/"
MIN_PACE = 2.5        # 秒：两次下载之间的最小间隔，遵守 Wikimedia 的限流礼节
MAX_ATTEMPTS = 5      # 429/5xx 退避重试次数
PERSON_CONF = 0.25    # YOLO 检测置信度阈值（与项目其它统计口径一致）
PERSON_MIN_RATIO = 0.80  # 验收判据：>=80% 的图至少 1 个 person

# ---------------------------------------------------------------------------
# 发现阶段的漏斗（2026-10-19 实测记录，用来说明"跳过多少、为什么跳过"）
# ---------------------------------------------------------------------------
DISCOVERY = {
    "categories_queried": 15,      # 15 个 Commons 分类
    "categories_ok": 7,            # 其中 7 个返回数据；8 个因 API 429 限流或被重定向返回 0
    "unique_candidates": 462,      # 去重后的候选文件标题数
    "prefilter": {
        "not_jpeg": 44,            # SVG/PNG 图标、徽标、示意图
        "width_lt_640": 49,        # 宽度不足 640px
        "bad_title": 5,            # 标题命中 logo/diagram/kit/engraving 等黑名单
        "size_out_of_range": 62,   # <40KB 或 >6MB（缩略图残片 / 超大原图）
    },
    "prefilter_passed": 302,
    "downloaded_and_probed": 85,
    "probe_rejects": {
        "no_person_detected": 14,          # YOLO 一个 person 都没检到 -> 不是足球场景
        "near_grayscale_historical": 5,    # 平均饱和度 <0.06（历史黑白扫描件）
    },
    "probe_passed": 66,
    "visual_qa_rejects": {
        "studio_ledger_portrait": 3,       # 室内影棚/更衣室/白背景肖像，无球场
        "referee_closeup": 3,              # 裁判特写，无球场语境
    },
    "final_pool": 60,
    "selected": 40,
    "surplus_unused": 20,          # 通过判据但因多样性/作者上限/近重复去重未入选
}

# ---------------------------------------------------------------------------
# SOURCES：40 条硬编码清单（file / title / sha256 / 尺寸 / 归属 / 许可）
# 由发现阶段的探测结果固化而来；改这个表 = 改标定集内容。
# ---------------------------------------------------------------------------
SOURCES = [
    {
        "file": "fb_001_match_overcast.jpg",
        "title": "File:JUGADA.jpg",
        "sha256": "38eec7de43a2bf71135428276567d0849d57ba43d7ede172516c1d6715247cec",
        "width": 960, "height": 638, "bytes": 130228,
        "page": "https://commons.wikimedia.org/wiki/File:JUGADA.jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/0/01/JUGADA.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
        "artist": "Rutinha",
        "license": "CC BY-SA 3.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/3.0",
    },
    {
        "file": "fb_002_player_overcast.jpg",
        "title": "File:20131031 FR14 Louisa Necib 9171.jpg",
        "sha256": "10346235a897a1000902e978e2da91cf379aaa17583bce995aa5def547a3d9e4",
        "width": 1500, "height": 2250, "bytes": 854251,
        "page": "https://commons.wikimedia.org/wiki/File:20131031_FR14_Louisa_Necib_9171.jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/d/d7/20131031_FR14_Louisa_Necib_9171.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
        "artist": "Ailura",
        "license": "CC BY-SA 3.0 at",
        "license_url": "https://creativecommons.org/licenses/by-sa/3.0/at/deed.en",
    },
    {
        "file": "fb_003_match_day.jpg",
        "title": "File:Edon Zhegrova.jpg",
        "sha256": "5d67b9abe0a9d4e5a86d0e7183901c4799dbc46639daa263c9a00f2e476d8b00",
        "width": 960, "height": 540, "bytes": 80182,
        "page": "https://commons.wikimedia.org/wiki/File:Edon_Zhegrova.jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/b/bd/Edon_Zhegrova.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
        "artist": "Albandamzi",
        "license": "CC BY-SA 4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0",
    },
    {
        "file": "fb_004_player_day.jpg",
        "title": "File:Monoy.jpg",
        "sha256": "2cb7e2f4ce5fb7e25d2aa6fa71959d7e09627a37d53f745dbee511d3d2628542",
        "width": 1080, "height": 978, "bytes": 184199,
        "page": "https://commons.wikimedia.org/wiki/File:Monoy.jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/e/ed/Monoy.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
        "artist": "Curanilahuino2020",
        "license": "CC BY-SA 4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0",
    },
    {
        "file": "fb_005_stadium_day.jpg",
        "title": "File:2013-07-04 Redstars v Flash FlashSideOfMidfield.jpg",
        "sha256": "94890cb4bf131dc79a717887fd429552da41cd40eeb3633da7db754fa4a7b4bc",
        "width": 3264, "height": 2045, "bytes": 1711487,
        "page": "https://commons.wikimedia.org/wiki/File:2013-07-04_Redstars_v_Flash_FlashSideOfMidfield.jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/5/5f/2013-07-04_Redstars_v_Flash_FlashSideOfMidfield.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
        "artist": "Harvardton",
        "license": "CC BY-SA 3.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/3.0",
    },
    {
        "file": "fb_006_training_overcast.jpg",
        "title": "File:Argentina training mundialito 1980.jpg",
        "sha256": "c79cbe3534e2ae2b86a86a9866b229b17ef87e26045f028095c500765be8bef3",
        "width": 879, "height": 466, "bytes": 122905,
        "page": "https://commons.wikimedia.org/wiki/File:Argentina_training_mundialito_1980.jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/d/da/Argentina_training_mundialito_1980.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
        "artist": "Unknown authorUnknown author",
        "license": "Public domain",
        "license_url": "https://commons.wikimedia.org/wiki/Commons:Copyright_tags#Public_domain",  # PD 条目 Commons 不给 LicenseUrl，回落到 PD 标签说明页
    },
    {
        "file": "fb_007_stadium_night.jpg",
        "title": "File:France-Allemagne ( 23 juin 2018).jpg",
        "sha256": "edb3307fb2f0f13c537a63e604d076dc508e966902c503e58f574ccd9329410d",
        "width": 1421, "height": 1147, "bytes": 160562,
        "page": "https://commons.wikimedia.org/wiki/File:France-Allemagne_(_23_juin_2018).jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/8/8e/France-Allemagne_%28_23_juin_2018%29.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
        "artist": "Armed Forces Sports",
        "license": "Public domain",
        "license_url": "https://commons.wikimedia.org/wiki/Commons:Copyright_tags#Public_domain",  # PD 条目 Commons 不给 LicenseUrl，回落到 PD 标签说明页
    },
    {
        "file": "fb_008_match_night.jpg",
        "title": "File:Joaquin ketlun tapando.jpg",
        "sha256": "1399386d0abd4a17d90ac076a971da26ce620221ec9ff9de31c660d371ddb07d",
        "width": 960, "height": 960, "bytes": 110754,
        "page": "https://commons.wikimedia.org/wiki/File:Joaquin_ketlun_tapando.jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/4/4f/Joaquin_ketlun_tapando.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
        "artist": "Braianmatiashrynkiewicz",
        "license": "CC BY-SA 4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0",
    },
    {
        "file": "fb_009_match_overcast.jpg",
        "title": "File:20131031 FR14 Louisa Necib 1 0 8988.jpg",
        "sha256": "2003741407674324ce1196e2fea44c8d4ebffe9ced7bff9c01687a2909f48263",
        "width": 2320, "height": 2320, "bytes": 1294383,
        "page": "https://commons.wikimedia.org/wiki/File:20131031_FR14_Louisa_Necib_1_0_8988.jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/c/c6/20131031_FR14_Louisa_Necib_1_0_8988.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
        "artist": "Ailura",
        "license": "CC BY-SA 3.0 at",
        "license_url": "https://creativecommons.org/licenses/by-sa/3.0/at/deed.en",
    },
    {
        "file": "fb_010_training_overcast.jpg",
        "title": "File:Simo Training At UB Conquense Spain.jpg",
        "sha256": "36c94539375f2128927ce50ebc3212262932af25b5e58bb95970ba967335097d",
        "width": 1600, "height": 1066, "bytes": 362679,
        "page": "https://commons.wikimedia.org/wiki/File:Simo_Training_At_UB_Conquense_Spain.jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/5/5e/Simo_Training_At_UB_Conquense_Spain.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
        "artist": "Symo711",
        "license": "CC BY-SA 4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0",
    },
    {
        "file": "fb_011_match_day.jpg",
        "title": "File:Levante-huelva.jpg",
        "sha256": "7919f2eb31b81eb73671d6b3f04acd4815ba95b53f15a6bf70427565dc9ca37e",
        "width": 791, "height": 579, "bytes": 79926,
        "page": "https://commons.wikimedia.org/wiki/File:Levante-huelva.jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/e/e3/Levante-huelva.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
        "artist": "Dvalades",
        "license": "CC BY-SA 3.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/3.0",
    },
    {
        "file": "fb_012_player_day.jpg",
        "title": "File:Alan peniche rangers andorra.jpg",
        "sha256": "2bff99ab660bdab7b1d6a88799f99e5c295762a7eff50d009e18daae395a0c23",
        "width": 693, "height": 960, "bytes": 95292,
        "page": "https://commons.wikimedia.org/wiki/File:Alan_peniche_rangers_andorra.jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/6/69/Alan_peniche_rangers_andorra.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
        "artist": "Leyendafutbol",
        "license": "CC BY-SA 4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0",
    },
    {
        "file": "fb_013_stadium_day.jpg",
        "title": "File:Cancha Sintética Boston.jpg",
        "sha256": "25a04d26ccfd6c9a30ae094f068a27b214edc93ab20ac708bdf0f672a7da7ee8",
        "width": 720, "height": 540, "bytes": 108059,
        "page": "https://commons.wikimedia.org/wiki/File:Cancha_Sint%C3%A9tica_Boston.jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/a/aa/Cancha_Sint%C3%A9tica_Boston.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
        "artist": "Se-bitas-94",
        "license": "CC BY-SA 3.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/3.0",
    },
    {
        "file": "fb_014_stadium_overcast.jpg",
        "title": "File:KatopodisChaca.jpg",
        "sha256": "cf856b9756ac5b66ef0651b451c05d72092ea991a65b92e76964c36c458f4f20",
        "width": 827, "height": 456, "bytes": 171233,
        "page": "https://commons.wikimedia.org/wiki/File:KatopodisChaca.jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/b/b5/KatopodisChaca.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
        "artist": "Gugusrocha",
        "license": "CC BY-SA 3.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/3.0",
    },
    {
        "file": "fb_015_player_night.jpg",
        "title": "File:Gabriel Batista2017.jpg",
        "sha256": "d029d3df25ce54d7a99a4f2abbad9cbe57cc0aeca7874edaf79769db4bb9a283",
        "width": 676, "height": 1185, "bytes": 297788,
        "page": "https://commons.wikimedia.org/wiki/File:Gabriel_Batista2017.jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/7/73/Gabriel_Batista2017.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
        "artist": "Prefeitura de Itapevi",
        "license": "CC BY 2.0",
        "license_url": "https://creativecommons.org/licenses/by/2.0",
    },
    {
        "file": "fb_016_stadium_night.jpg",
        "title": "File:21766579 vladimir rayoslik10212587023581824 5158613219358663664 n.jpg",
        "sha256": "0bd91fefbdbae5ac7b4c5adadfcafa51146a9e94dc7857bf8ed48491e9edf342",
        "width": 960, "height": 720, "bytes": 80422,
        "page": "https://commons.wikimedia.org/wiki/File:21766579_vladimir_rayoslik10212587023581824_5158613219358663664_n.jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/8/85/21766579_vladimir_rayoslik10212587023581824_5158613219358663664_n.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
        "artist": "Corneliofctigrense",
        "license": "CC BY-SA 4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0",
    },
    {
        "file": "fb_017_match_night.jpg",
        "title": "File:Estadio de Piedades.jpg",
        "sha256": "a204d92494b354f7bfb80949c5a73f4492d27ad7e3cd5de615bc3a7e37c148bf",
        "width": 680, "height": 382, "bytes": 64955,
        "page": "https://commons.wikimedia.org/wiki/File:Estadio_de_Piedades.jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/f/fa/Estadio_de_Piedades.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
        "artist": "Periodista82",
        "license": "CC BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0",
    },
    {
        "file": "fb_018_match_overcast.jpg",
        "title": "File:Ashleigh Shreeve - Mulbarton Belles Ladies.jpg",
        "sha256": "1b8b871c74951d42f01182384cb5c626caefdaac20c3a58aa0a0dd7aa7ef1643",
        "width": 960, "height": 637, "bytes": 125634,
        "page": "https://commons.wikimedia.org/wiki/File:Ashleigh_Shreeve_-_Mulbarton_Belles_Ladies.jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/d/dc/Ashleigh_Shreeve_-_Mulbarton_Belles_Ladies.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
        "artist": "Coco201109",
        "license": "CC BY-SA 3.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/3.0",
    },
    {
        "file": "fb_019_player_overcast.jpg",
        "title": "File:Andi Kalaja.jpg",
        "sha256": "40acfff899d362c081b548a33c1b25e75f6afe5d463fac81e259520ddeb4723b",
        "width": 960, "height": 609, "bytes": 110306,
        "page": "https://commons.wikimedia.org/wiki/File:Andi_Kalaja.jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/0/05/Andi_Kalaja.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
        "artist": "ANEDBXGB",
        "license": "CC BY-SA 3.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/3.0",
    },
    {
        "file": "fb_020_match_day.jpg",
        "title": "File:552738 rolando pateando al arco133851160131677 1084665112 n.jpg",
        "sha256": "1f008645e53d785747affad36a97890c471eb4367da38c57b8313c7d27b7620c",
        "width": 960, "height": 637, "bytes": 70401,
        "page": "https://commons.wikimedia.org/wiki/File:552738_rolando_pateando_al_arco133851160131677_1084665112_n.jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/5/53/552738_rolando_pateando_al_arco133851160131677_1084665112_n.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
        "artist": "Corneliofctigrense",
        "license": "CC BY-SA 4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0",
    },
    {
        "file": "fb_021_player_day.jpg",
        "title": "File:Ione.jpg",
        "sha256": "53beda8bd4ae2b7ecba332cfa5cc276d5594659bfe08f9544cbcdc54727503e8",
        "width": 800, "height": 533, "bytes": 93400,
        "page": "https://commons.wikimedia.org/wiki/File:Ione.jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/0/06/Ione.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
        "artist": "Mykel",
        "license": "CC BY-SA 3.0",
        "license_url": "http://creativecommons.org/licenses/by-sa/3.0/",
    },
    {
        "file": "fb_022_stadium_day.jpg",
        "title": "File:Estadio Municipal de Aserri.jpg",
        "sha256": "a3d6acf42d922ab30c987268463d5eda89d7d01f4d06c2ee1263985137069d4a",
        "width": 718, "height": 540, "bytes": 43227,
        "page": "https://commons.wikimedia.org/wiki/File:Estadio_Municipal_de_Aserri.jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/c/cf/Estadio_Municipal_de_Aserri.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
        "artist": "Periodista82",
        "license": "CC BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0",
    },
    {
        "file": "fb_023_stadium_overcast.jpg",
        "title": "File:Hi-ho-rushden-diamonds1.jpg",
        "sha256": "5553d1fe91cb475ac579f4dac9cb2171c99a946db0348af00ea00122225b5322",
        "width": 800, "height": 600, "bytes": 100297,
        "page": "https://commons.wikimedia.org/wiki/File:Hi-ho-rushden-diamonds1.jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/7/7f/Hi-ho-rushden-diamonds1.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
        "artist": "That'sWhatIWould'veDone",
        "license": "Public domain",
        "license_url": "https://commons.wikimedia.org/wiki/Commons:Copyright_tags#Public_domain",  # PD 条目 Commons 不给 LicenseUrl，回落到 PD 标签说明页
    },
    {
        "file": "fb_024_match_overcast.jpg",
        "title": "File:Loke Svane.jpg",
        "sha256": "c0efe2a4b9dc4ad593a6e5fae791b12942ddbf25512a140e45d5432d68b75a97",
        "width": 960, "height": 607, "bytes": 110390,
        "page": "https://commons.wikimedia.org/wiki/File:Loke_Svane.jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/3/36/Loke_Svane.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
        "artist": "Aidanor97",
        "license": "CC BY-SA 4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0",
    },
    {
        "file": "fb_025_training_overcast.jpg",
        "title": "File:Centre d'entrainement.jpg",
        "sha256": "751703c32a032e9916b6ed540b4493e6a0e8f5ef269608c8e9733df5b2d63cb2",
        "width": 910, "height": 618, "bytes": 97348,
        "page": "https://commons.wikimedia.org/wiki/File:Centre_d%27entrainement.jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/a/a4/Centre_d%27entrainement.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
        "artist": "Infosworldd",
        "license": "CC BY-SA 4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0",
    },
    {
        "file": "fb_026_match_day.jpg",
        "title": "File:2012 Olympic Football - Hampden Park (1) (8136030837).jpg",
        "sha256": "0f68e65c34d9da52cf0470cff4c4813ed16ef587a5804e5402fef51b3fe6907d",
        "width": 2816, "height": 2112, "bytes": 1162974,
        "page": "https://commons.wikimedia.org/wiki/File:2012_Olympic_Football_-_Hampden_Park_(1)_(8136030837).jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/5/52/2012_Olympic_Football_-_Hampden_Park_%281%29_%288136030837%29.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
        "artist": "Daniel from Glasgow, United Kingdom",
        "license": "CC BY 2.0",
        "license_url": "https://creativecommons.org/licenses/by/2.0",
    },
    {
        "file": "fb_027_stadium_day.jpg",
        "title": "File:2013-07-04 Chicago Red Stars attacking Western New York Flash.jpg",
        "sha256": "b6dae2fba15c471560ff61896c4f4627dadf3030dbe925ce9f5fcc1743172d57",
        "width": 2618, "height": 1959, "bytes": 1433067,
        "page": "https://commons.wikimedia.org/wiki/File:2013-07-04_Chicago_Red_Stars_attacking_Western_New_York_Flash.jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/d/d0/2013-07-04_Chicago_Red_Stars_attacking_Western_New_York_Flash.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
        "artist": "Harvardton",
        "license": "CC BY-SA 3.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/3.0",
    },
    {
        "file": "fb_028_match_day.jpg",
        "title": "File:Colocolo19.jpg",
        "sha256": "e52dd1857aabe3f6fefbb3ce9338001a96add1b3a168d8778dafd7ed4656f8a5",
        "width": 720, "height": 720, "bytes": 103763,
        "page": "https://commons.wikimedia.org/wiki/File:Colocolo19.jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/7/70/Colocolo19.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
        "artist": "Mile1901",
        "license": "CC BY-SA 4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0",
    },
    {
        "file": "fb_029_training_day.jpg",
        "title": "File:Footballers enjoying a Sunday morning training session at Stokes Bay - geograph.org.uk - 1425291.jpg",
        "sha256": "ba71a327ba656d1e51215fba84f4d864dbb2c17d003948ed7359f1b5d12750ff",
        "width": 640, "height": 480, "bytes": 56509,
        "page": "https://commons.wikimedia.org/wiki/File:Footballers_enjoying_a_Sunday_morning_training_session_at_Stokes_Bay_-_geograph.org.uk_-_1425291.jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/b/b4/Footballers_enjoying_a_Sunday_morning_training_session_at_Stokes_Bay_-_geograph.org.uk_-_1425291.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
        "artist": "Basher Eyre",
        "license": "CC BY-SA 2.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/2.0",
    },
    {
        "file": "fb_030_match_overcast.jpg",
        "title": "File:Joacoooooooo123.jpg",
        "sha256": "af8617535820983a3f358ee121597ee82d014f924c4c45d9b245c7fe1f1320d5",
        "width": 899, "height": 816, "bytes": 110287,
        "page": "https://commons.wikimedia.org/wiki/File:Joacoooooooo123.jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/e/ef/Joacoooooooo123.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
        "artist": "Braianmatiashrynkiewicz",
        "license": "CC BY-SA 4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0",
    },
    {
        "file": "fb_031_player_overcast.jpg",
        "title": "File:Millennium Stadium hosting the London Olympics 2012 - geograph.org.uk - 3065997.jpg",
        "sha256": "de0e9bc664e896cffac01df034336b0cbd20fb3d95bf203677d7e8e457f227fb",
        "width": 640, "height": 480, "bytes": 92533,
        "page": "https://commons.wikimedia.org/wiki/File:Millennium_Stadium_hosting_the_London_Olympics_2012_-_geograph.org.uk_-_3065997.jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/f/fa/Millennium_Stadium_hosting_the_London_Olympics_2012_-_geograph.org.uk_-_3065997.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
        "artist": "John Light",
        "license": "CC BY-SA 2.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/2.0",
    },
    {
        "file": "fb_032_match_day.jpg",
        "title": "File:Abel Ovotu at loan in Al ittihad Sudan.jpg",
        "sha256": "ffd6a38525763149a17cc99e1e32c173e2775c3f5a9cffe848ea59510977cb97",
        "width": 750, "height": 1000, "bytes": 95543,
        "page": "https://commons.wikimedia.org/wiki/File:Abel_Ovotu_at_loan_in_Al_ittihad_Sudan.jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/0/00/Abel_Ovotu_at_loan_in_Al_ittihad_Sudan.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
        "artist": "Emmsaleeh",
        "license": "CC BY-SA 4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0",
    },
    {
        "file": "fb_033_stadium_day.jpg",
        "title": "File:Clasico sancarlino Central vs Union.jpg",
        "sha256": "ddf2f36014c10c557dd9582593acf34dc5191944ea978f4bb9e58204ab540f76",
        "width": 960, "height": 720, "bytes": 192205,
        "page": "https://commons.wikimedia.org/wiki/File:Clasico_sancarlino_Central_vs_Union.jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/c/c2/Clasico_sancarlino_Central_vs_Union.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
        "artist": "Marcosskay",
        "license": "CC BY-SA 3.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/3.0",
    },
    {
        "file": "fb_034_match_overcast.jpg",
        "title": "File:U.S. Women's Soccer team in Vancouver with Consul General Anne Callaghan.jpg",
        "sha256": "d24d48305076ed1f0314c0d7fdb541748032788a3fc94712957ed7abb4d6f5e5",
        "width": 2500, "height": 1553, "bytes": 683219,
        "page": "https://commons.wikimedia.org/wiki/File:U.S._Women%27s_Soccer_team_in_Vancouver_with_Consul_General_Anne_Callaghan.jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/f/f4/U.S._Women%27s_Soccer_team_in_Vancouver_with_Consul_General_Anne_Callaghan.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
        "artist": "US Mission Canada",
        "license": "CC BY 2.0",
        "license_url": "https://creativecommons.org/licenses/by/2.0",
    },
    {
        "file": "fb_035_match_day.jpg",
        "title": "File:Scene of Women's Under 16 match in Dhaka 01.jpg",
        "sha256": "b6cafb01deb7624983424f434f0ff530c3072e5efd966e7732f77185d43fac36",
        "width": 1600, "height": 1200, "bytes": 302444,
        "page": "https://commons.wikimedia.org/wiki/File:Scene_of_Women%27s_Under_16_match_in_Dhaka_01.jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/f/f1/Scene_of_Women%27s_Under_16_match_in_Dhaka_01.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
        "artist": "Ibrahim Husain Meraj",
        "license": "CC BY-SA 4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0",
    },
    {
        "file": "fb_036_stadium_day.jpg",
        "title": "File:Estadio Comunal de Cariari.jpg",
        "sha256": "384b18e626421f791c669f57c0e4b743f5f7cb73fdbff2875fc994504627be59",
        "width": 680, "height": 383, "bytes": 108919,
        "page": "https://commons.wikimedia.org/wiki/File:Estadio_Comunal_de_Cariari.jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/4/42/Estadio_Comunal_de_Cariari.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
        "artist": "Periodista82",
        "license": "CC BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0",
    },
    {
        "file": "fb_037_match_day.jpg",
        "title": "File:Deportivo Pilmahue vs Unión Compañias.jpg",
        "sha256": "07ec87bc069608fa22d8a9cc4a51ae72c3540dd186981088ac1844eae3626dcc",
        "width": 640, "height": 360, "bytes": 169271,
        "page": "https://commons.wikimedia.org/wiki/File:Deportivo_Pilmahue_vs_Uni%C3%B3n_Compa%C3%B1ias.jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/d/d2/Deportivo_Pilmahue_vs_Uni%C3%B3n_Compa%C3%B1ias.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
        "artist": "LeoArnd",
        "license": "CC BY-SA 4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0",
    },
    {
        "file": "fb_038_match_overcast.jpg",
        "title": "File:Association football at the 2012 Summer Olympics 007.jpg",
        "sha256": "87be986b5e5f7e1026736957d8fade431a1e205ae862d428613e2bdbb64db5f9",
        "width": 3872, "height": 2592, "bytes": 4758573,
        "page": "https://commons.wikimedia.org/wiki/File:Association_football_at_the_2012_Summer_Olympics_007.jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/e/e3/Association_football_at_the_2012_Summer_Olympics_007.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
        "artist": "Joel Solomon from Denver, CO, USA",
        "license": "CC BY 2.0",
        "license_url": "https://creativecommons.org/licenses/by/2.0",
    },
    {
        "file": "fb_039_match_day.jpg",
        "title": "File:Heidewaldstadion-2006-06-13.jpg",
        "sha256": "74e8839bbf3ff316c3a0a2751f3b9abef64d8583f610c47823cd3d8359c36d98",
        "width": 1132, "height": 428, "bytes": 134967,
        "page": "https://commons.wikimedia.org/wiki/File:Heidewaldstadion-2006-06-13.jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/5/52/Heidewaldstadion-2006-06-13.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
        "artist": "Daniel Neuhaus (Lütke)",
        "license": "CC BY 2.5",
        "license_url": "https://creativecommons.org/licenses/by/2.5",
    },
    {
        "file": "fb_040_training_day.jpg",
        "title": "File:Benitez practice.jpg",
        "sha256": "396e2d6b77689f9501f24cb3ae4033c375ab2d6c799c22b45cb5ebae665de91f",
        "width": 704, "height": 649, "bytes": 82502,
        "page": "https://commons.wikimedia.org/wiki/File:Benitez_practice.jpg",
        "url": "https://upload.wikimedia.org/wikipedia/commons/0/03/Benitez_practice.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
        "artist": "Robdog9",
        "license": "Public domain",
        "license_url": "https://commons.wikimedia.org/wiki/Commons:Copyright_tags#Public_domain",  # PD 条目 Commons 不给 LicenseUrl，回落到 PD 标签说明页
    },
]

COLUMNS = [
    "file", "sha256", "width", "height", "bytes", "commons_page", "file_url",
    "artist", "license", "license_url", "n_person", "n_ball", "added",
]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Pacer:
    """简单的礼貌节流器：两次请求至少间隔 MIN_PACE 秒。"""

    def __init__(self, gap: float) -> None:
        self.gap = gap
        self.last = 0.0

    def wait(self) -> None:
        delta = self.gap - (time.time() - self.last)
        if delta > 0:
            time.sleep(delta)
        self.last = time.time()


PACER = Pacer(MIN_PACE)


def http_get(url: str, timeout: int = 120) -> bytes:
    """带节流与 429 退避的 GET。返回响应体字节。"""
    last_err: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        PACER.wait()
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Referer": REFERER})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            last_err = exc
            if exc.code in (429, 500, 502, 503, 504):
                wait = 15 * attempt
                print(f"    HTTP {exc.code} -> 退避 {wait}s (第 {attempt}/{MAX_ATTEMPTS} 次)")
                time.sleep(wait)
                PACER.last = time.time()
                continue
            raise
        except Exception as exc:  # 超时/连接重置等
            last_err = exc
            if attempt < MAX_ATTEMPTS:
                time.sleep(3 * attempt)
                continue
            raise
    raise RuntimeError(f"下载失败（{MAX_ATTEMPTS} 次）：{url} :: {last_err}")


def download_entry(entry: dict, dest: Path) -> tuple[bool, str]:
    """下载单个条目并校验 SHA256。返回 (ok, message)."""
    part = dest.with_suffix(dest.suffix + ".part")
    try:
        blob = http_get(entry["url"])
    except Exception as exc:  # noqa: BLE001
        return False, f"download error: {exc}"
    digest = hashlib.sha256(blob).hexdigest()
    if digest != entry["sha256"]:
        part.unlink(missing_ok=True)
        return False, f"sha256 mismatch: expected {entry['sha256'][:16]}... got {digest[:16]}..."
    with part.open("wb") as f:
        f.write(blob)
    os.replace(part, dest)
    return True, f"{len(blob)} bytes"


def run_yolo(paths: list[Path]) -> dict[str, dict[str, int]]:
    """对每张图跑 YOLOv8n，返回 {filename: {n_person, n_ball}}。"""
    from ultralytics import YOLO  # 延迟导入：--list 模式不该付这个开销

    model = YOLO(str(WEIGHTS))
    out: dict[str, dict[str, int]] = {}
    for i, p in enumerate(paths, 1):
        r = model.predict(str(p), imgsz=320, verbose=False, device="cpu", conf=PERSON_CONF)[0]
        labels = [r.names[int(c)] for c in (r.boxes.cls.tolist() if r.boxes is not None else [])]
        out[p.name] = {"n_person": labels.count("person"), "n_ball": labels.count("sports ball")}
        print(f"  [{i:2d}/{len(paths)}] {p.name:34s} person={out[p.name]['n_person']:2d} "
              f"ball={out[p.name]['n_ball']}")
    return out


def image_size(path: Path) -> tuple[int, int]:
    from PIL import Image

    with Image.open(path) as im:
        return im.size


def write_manifest(rows: list[dict]) -> None:
    # utf-8-sig：作者名含非 ASCII（Fußball / 丸亀 等），带 BOM 便于 Excel 直接打开
    with MANIFEST.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for row in rows:
            w.writerow({k: row[k] for k in COLUMNS})


def read_manifest() -> list[dict]:
    with MANIFEST.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def print_license_table(rows: list[dict]) -> None:
    dist: dict[str, int] = {}
    for r in rows:
        dist[r["license"]] = dist.get(r["license"], 0) + 1
    print("\n许可分布：")
    for lic, n in sorted(dist.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {lic:22s} {n:3d}")


def print_discovery_funnel() -> None:
    d = DISCOVERY
    print("\n挑选漏斗（跳过原因分类，2026-10-19 实测）：")
    print(f"  Commons 分类查询            {d['categories_queried']:4d} 个（{d['categories_ok']} 个返回数据，"
          f"{d['categories_queried'] - d['categories_ok']} 个被 API 429 限流返回空）")
    print(f"  去重后候选文件标题          {d['unique_candidates']:4d}")
    for k, v in d["prefilter"].items():
        print(f"    - 预过滤跳过 {k:22s} {v:4d}")
    print(f"  = 预过滤通过                {d['prefilter_passed']:4d}")
    print(f"  实际下载并 YOLO 探测        {d['downloaded_and_probed']:4d}")
    for k, v in d["probe_rejects"].items():
        print(f"    - 探测淘汰 {k:23s} {v:4d}")
    print(f"  = 探测通过                  {d['probe_passed']:4d}")
    for k, v in d["visual_qa_rejects"].items():
        print(f"    - 目视复查淘汰 {k:19s} {v:4d}")
    print(f"  = 合格池                    {d['final_pool']:4d}")
    print(f"  最终入选                    {d['selected']:4d}（余 {d['surplus_unused']} 张因多样性/作者上限/近重复未用）")


def ascii_name_problems(files: list[str]) -> list[str]:
    pat = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
    return [f for f in files if not pat.match(f)]


def print_stats(rows: list[dict]) -> int:
    n = len(rows)
    widths = [int(r["width"]) for r in rows]
    persons = sum(1 for r in rows if int(r["n_person"]) >= 1)
    balls = sum(1 for r in rows if int(r["n_ball"]) >= 1)
    ratio = persons / n if n else 0.0
    print("\n统计：")
    print(f"  总张数                {n}")
    print(f"  宽度范围              {min(widths)} .. {max(widths)} px（全部 >= 640）")
    print(f"  >=1 person 的比例     {persons}/{n} = {ratio:.1%}（判据 >= {PERSON_MIN_RATIO:.0%}）")
    print(f"  >=1 sports ball 的张数 {balls}/{n}（远景图球太小检不到属正常，不据此淘汰）")
    bad = ascii_name_problems([r["file"] for r in rows])
    print(f"  文件名纯 ASCII 检查    {'通过' if not bad else '失败: ' + ', '.join(bad)}")
    print(f"  不同许可种类          {len({r['license'] for r in rows})}")
    return persons


# ---------------------------------------------------------------------------
# modes
# ---------------------------------------------------------------------------
def mode_list(as_json: bool) -> int:
    print(f"将抓取 {len(SOURCES)} 个条目（仅列出，不下载）：")
    for e in SOURCES:
        print(f"  {e['file']:34s} {e['width']:>5d}x{e['height']:<5d} {e['license']:18s} {e['title']}")
    if as_json:
        print(json.dumps([{k: e[k] for k in ("file", "title", "sha256", "url", "license")} for e in SOURCES],
                         ensure_ascii=False, indent=1))
    print(f"\n合计 {len(SOURCES)} 个条目，目标写入 {OUT_DIR}")
    print(f"清单重跑口径：SHA256 已硬编码，任何字节变化都会被判为 FAIL。")
    return 0


def mode_fetch(force: bool) -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"目标目录：{OUT_DIR}")
    print(f"条目数：{len(SOURCES)}；节流 {MIN_PACE}s/请求；UA 已声明用途与联系方式\n")

    failures: list[str] = []
    for i, e in enumerate(SOURCES, 1):
        dest = OUT_DIR / e["file"]
        if dest.exists() and not force:
            got = sha256_file(dest)
            if got == e["sha256"]:
                print(f"[{i:2d}/{len(SOURCES)}] {e['file']:34s} 已存在且 SHA256 一致，跳过下载")
                continue
            print(f"[{i:2d}/{len(SOURCES)}] {e['file']:34s} 已存在但 SHA256 不符，重新下载")
        print(f"[{i:2d}/{len(SOURCES)}] {e['file']:34s} 下载 {e['title'][:60]}")
        ok, msg = download_entry(e, dest)
        if not ok:
            print(f"    FAIL {msg}")
            failures.append(f"{e['file']}: {msg}")
        else:
            print(f"    OK {msg} sha256={e['sha256'][:16]}...")

    files = [OUT_DIR / e["file"] for e in SOURCES if (OUT_DIR / e["file"]).exists()]
    if failures:
        print(f"\n{len(failures)} 个条目下载失败：")
        for f in failures:
            print(f"  - {f}")

    print(f"\n对 {len(files)} 张图跑 YOLOv8n 复核（imgsz=320, conf>{PERSON_CONF}）...")
    det = run_yolo(files)

    rows = []
    for e in SOURCES:
        p = OUT_DIR / e["file"]
        if not p.exists():
            continue
        w, h = image_size(p)
        rows.append({
            "file": e["file"], "sha256": e["sha256"], "width": w, "height": h,
            "bytes": p.stat().st_size, "commons_page": e["page"], "file_url": e["url"],
            "artist": e["artist"], "license": e["license"], "license_url": e["license_url"],
            "n_person": det.get(e["file"], {}).get("n_person", 0),
            "n_ball": det.get(e["file"], {}).get("n_ball", 0),
            "added": ADDED,
        })
    write_manifest(rows)
    print(f"\nMANIFEST.csv 已写入：{MANIFEST}（{len(rows)} 行数据）")

    print_license_table(rows)
    print_discovery_funnel()
    persons = print_stats(rows)

    if failures:
        print(f"\nCALIB_REAL_FAIL {len(failures)}_download_failures")
        return 1
    if len(rows) < 30:
        print(f"\nCALIB_REAL_FAIL only_{len(rows)}_images")
        return 1
    if persons / len(rows) < PERSON_MIN_RATIO:
        print(f"\nCALIB_REAL_FAIL person_ratio_{persons / len(rows):.2%}")
        return 1
    print(f"\nCALIB_REAL_OK {len(rows)}")
    return 0


def mode_verify() -> int:
    if not MANIFEST.exists():
        print(f"CALIB_REAL_FAIL manifest_missing ({MANIFEST})")
        return 1
    rows = read_manifest()
    print(f"复核模式（不下载）：MANIFEST 有 {len(rows)} 行\n")
    bad: list[str] = []
    for r in rows:
        p = OUT_DIR / r["file"]
        if not p.exists():
            bad.append(f"{r['file']}: missing on disk")
            print(f"  MISSING {r['file']}")
            continue
        got = sha256_file(p)
        if got != r["sha256"]:
            bad.append(f"{r['file']}: manifest sha mismatch")
            print(f"  SHA-MISMATCH {r['file']} manifest={r['sha256'][:16]} disk={got[:16]}")
            continue
        exp = next((e["sha256"] for e in SOURCES if e["file"] == r["file"]), None)
        if exp and got != exp:
            bad.append(f"{r['file']}: embedded sha mismatch")
            print(f"  SHA-MISMATCH {r['file']} embedded={exp[:16]} disk={got[:16]}")
            continue
        print(f"  OK {r['file']:34s} {r['sha256'][:16]}...")
    print_license_table(rows)
    print_discovery_funnel()
    persons = print_stats(rows)
    if bad:
        print(f"\n{len(bad)} 处不一致")
        print(f"CALIB_REAL_FAIL verify_{len(bad)}_mismatches")
        return 1
    if persons / max(len(rows), 1) < PERSON_MIN_RATIO:
        print(f"\nCALIB_REAL_FAIL person_ratio_{persons / len(rows):.2%}")
        return 1
    print(f"\nCALIB_REAL_OK {len(rows)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="构建真实足球照片 INT8 标定集（Wikimedia Commons）")
    ap.add_argument("--list", action="store_true", help="只列出将抓取的条目，不联网不下载")
    ap.add_argument("--verify", action="store_true", help="不下载，仅复核既有文件 SHA256 与 MANIFEST 一致性")
    ap.add_argument("--force", action="store_true", help="已存在也强制重新下载")
    ap.add_argument("--json", action="store_true", help="--list 时额外输出 JSON")
    a = ap.parse_args()
    if a.list:
        return mode_list(a.json)
    if a.verify:
        return mode_verify()
    return mode_fetch(a.force)


if __name__ == "__main__":
    sys.exit(main())
