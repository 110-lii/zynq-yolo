# data/config —— 赛前人工输入（D013）

| | |
|---|---|
| 本目录装什么 | 每场比赛一份 `match_<id>.json`：双方队名/队服颜色/裁判颜色/各自半场，以及按场校准的参考色文件路径 |
| 谁产生 | **人**（`tools/match_setup.py`，真机上由 LCD 触屏录入） |
| 进 git | 是——它是「人工输入」的证据，出错时要能分清录入错还是算法错 |
| 生成方式 | `python tools/match_setup.py --serve`（网页模拟）或 `--set`（命令行） |
