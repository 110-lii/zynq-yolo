# scenes/ —— 按画面性质分层的比赛片段

> 上级：[`../README.md`](../README.md)
> 产生工具：[`../../tools/split_scenes.py`](../../tools/split_scenes.py)

| | |
|---|---|
| 本目录装什么 | 从整场转播切出来的两类测试片段：`contest`（争抢）/ `normal`（普通） |
| 谁产生 | `tools/split_scenes.py --extract` |
| 进 git | **只有 `MANIFEST.csv`**；`.mp4` 是第三方转播内容，`.gitignore` 已排除 |

## 为什么要分层

`data/samples/incidents/` 那 12 段是 `find_incidents.py` 按「导播切回放 = 有争议事件」
挑出来的，**只代表最难的一类画面**。只用它上板，等于只测了系统最吃力的场景；
只测平淡画面又证明不了关键帧不丢。所以测试集要按画面性质分层：

| 层 | 画面特征 | 检测难在哪 |
|---|---|---|
| `contest` | 多人聚在球周围，框互相遮挡 | 球常年不可见；框粘连、易漏检 |
| `normal` | 阵型展开，球权清晰 | 相对容易 |

## 判据（几何代理，不是人工真值）

    含 50% 检出人的最窄 x 区间宽度 w（归一化到画面宽）
    w 小 ⇒ 人挤在一起 ⇒ 争抢      w 大 ⇒ 阵型铺开 ⇒ 普通

两道硬门槛把非球场画面挡在外面（第一版就是被这两种骗的）：

- **草皮占比 < 0.35** ⇒ 判为 `not_pitch`（首发名单图、球员通道、看台）
- **人物框中位高度 > 0.30 画面高** ⇒ 判为 `closeup`（替补席/庆祝近景）

## MANIFEST.csv

| 列 | 含义 |
|---|---|
| `scene_id` | `c01..c05` = 争抢，`n01..n05` = 普通 |
| `t_start_s` / `t_end_s` | 在**原片**里的时间窗（可复现） |
| `n_probe_frames` | 该窗口内有效探针数（=1 表示只有一个探针点支撑，别当成统计量） |
| `bunch_median` / `players_median` | 聚集度 / 人数中位数 |
| `sha256_16` | 片段哈希，确认复现出来的片是同一份 |
| `human_ok` | **留给人填**：这段到底是不是「争抢」 |

## 用法

```bash
py tools/split_scenes.py --extract --t0 300 --t1 2700 --probe-step 10 \
    --n-contest 5 --n-normal 5 --clip-len 10 --sep 60
py tools/check_scene_suite.py --fps 5
```

宿主指标（2026-10-30，640@0.25，各 5 段）：争抢球检出 **11.6%**、普通 **19.6%**，
聚集度 0.171 vs 0.294。**样本小，不作准确率用。**
