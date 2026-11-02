# scene_validity_gating —— 帧级内容闸门：这一帧该不该算

> 上级：[`../README.md`](../README.md) · 类型：**通用技能包（固定机位视觉管线的场景有效性判定）**

## 1. 适用范围

- **管线形态**：固定或半固定机位的视频 + 目标检测/跟踪 + 几何映射（单应、测距、坐标）。
- **本队场景**：足球转播画面（草坪占比、框底草皮、机位三态）；同样适用于交通监控（路面占比）、
  巡检（目标区域占比）等「全图统计量便宜、几何结论昂贵」的场合。
- **依赖**：一个廉价的全图统计量（颜色占比 / 亮度 / 边缘密度）与检测框；不需要额外模型。
- **不覆盖**：镜头切换检测、多机位切换、动态机位（PTZ）下的标定切换。

## 2. 使用方法

### 2.1 三层闸门，各管一件事

| 层 | 判据 | 动作 | 本队实现 |
|---|---|---|---|
| **场景层** | 草坪像素占比 <0.20 → 特写/看台 | 判 `INVALID_VIEW`：**清空坐标，保留画面与事件展示** | `tools/camera_state.py` |
| **目标层** | 框底中心 7×7 邻域 HSV 绿色占比 ≥0.4 才计入场内目标 | 把看台/广告牌上的误检挡在跟踪之外 | `board_pipeline.py:_is_grass` |
| **节奏层** | 主检测每 N 帧一次；球路径独立 M 帧 | 非检测帧由跟踪器外推，算力集中在有效帧 | `--every`（默认 15）/ `--ball-every` |

三态定义（保守优先，先用规则不用模型）：

```
grass < 0.20                         -> INVALID_VIEW  (close_up_or_crowd)
n_det >= 8 且 valid_frac < 0.10      -> INVALID_VIEW  (calibration_mismatch)
grass >= 0.35 且 valid_frac >= 0.40 且 n_det >= 5 -> VALID_FIELD_VIEW
其余                                  -> LIMITED_FIELD_VIEW
```

### 2.2 最小接入

```python
from camera_state import assess
valid, state, reason = assess(grass_frac, n_det, n_field, motion)
if not valid:
    row["field"] = None          # 只清几何，画面/检测/事件照常输出
```

```bash
python3 tools/camera_state.py --selftest     # 期望 CAMERA_SELFTEST OK（5 个用例）
```

### 2.3 设计原则

1. **闸门只决定「算不算」，不决定「显不显示」**：清空坐标，不丢帧，否则证据帧、事件展示、
   上位机回放会一起断。
2. **上闸门之前先有基线**：先量无闸门时的检测数与坐标数，再加闸门，否则分不清是「模型差」还是「闸门严」。
3. **每个闸门都要有 selftest**：边界值（0.19 / 0.20 / 0.35）各一条用例，判据要能失败。

## 3. 失效条件（可观察现象）

| 条件 | 现象 | 处理 |
|---|---|---|
| 非草地球场（室内、硬地、沙滩） | 绿色占比恒低，整场判 `INVALID_VIEW`，坐标全空 | 换场地颜色/纹理统计量，阈值重新标定 |
| 夜间、低饱和、强逆光 | 绿色掩膜大面积丢失，`grass` 忽高忽低 | 改用亮度归一化或纹理特征；不要在低饱和场景套 HSV 阈值 |
| 特写镜头里仍有草地背景 | `grass` 可能 >0.20，闸门不触发 | 增加画幅/相机运动判据（如 `motion`、可视人数） |
| 框底不在脚点（俯拍、遮挡、半身框） | 目标层误杀真实球员 | 改用框中心或分割脚点；对俯拍机位单独设阈值 |
| 固定检测节奏遇到快攻/镜头切换 | 两次检测之间目标位移过大，跟踪外推失真 | 用运动幅度或场景变化调节 `--every`；事件模式缩短到 5 帧 |
| 清空坐标但下游仍按「有坐标」处理 | 事件距离被外推值放大，出现假事件 | 下游显式判断 `field is None`；事件只在有效帧成立 |
| 闸门阈值照搬本队数字 | 换分辨率/换机位后行为不同 | 阈值随画面统计量重标；技能包给的是方法与量级，不是常数 |

## 4. 提炼来源

> 范围（D007）：只提炼本仓库自己的会话与实测。

- `ai/sessions/009_ps_pipeline_pl_preproc.md`：机位三态与 377 帧分布；
- `ai/sessions/010_*`：球路径节奏（`--ball-every`）与「放大丢失窗口反而更差」的教训；
- `ai/sessions/026_pc_push_integration_and_clips.md`、`ai/sessions/027_*`：
  整段 250 帧运行中「0.45 人/帧」被误判为检测问题，追查后确认是 `INVALID_VIEW` 按设计清空坐标；
- `tools/camera_state.py`（规则与 selftest）、`tools/board_pipeline.py`（`_is_grass`、`--every`）；
- `docs/system_status.md` §60–§77（球链路与分辨率、空中球候选）。

## 5. 已验证的效果

| 判据 | 结果 | 出处 |
|---|---|---|
| 三态规则自检 | `CAMERA_SELFTEST OK`：5 个用例含边界（0.05 → INVALID、0.60+20 框+1 场内 → INVALID、0.60+15 → VALID） | `tools/camera_state.py --selftest` |
| 真实片段分布 | 377 帧：**300 LIMITED / 77 INVALID** | `ai/sessions/009`、`report/design_report.md` §4.6 |
| 「人少」归因纠正 | 板上整段出现 0.45 人/帧，追查为未给 `--calib` 时按设计清空坐标，**不是检测退化** | `ai/sessions/026`/`027`、`docs/system_status.md` |
| 闸门与事件共存 | c05 250 帧仍产出 32 帧有效球（28 帧球场坐标）、10 帧球权（闸门只清无效帧的几何） | `build/pull/evt_demo_20261101.jsonl` |
| 目标层闸门 | 已接入每次检测（`_is_grass`）；**对误检的定量收益尚未单独消融** | `board_pipeline.py:578` |

> 判据是**能失败**的：selftest 的边界用例写错就会 FAIL；去掉 `--calib` 时坐标清空可被复现。
> **未验证**：目标层闸门（框底草皮）的召回/误检收益没有做消融，报告里不写「提升了多少」。
