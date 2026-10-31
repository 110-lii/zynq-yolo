# Session 024 — 2026-10-31 —— 撤回结论的全文清理；按修订后的事实重新出包、出文档

> 承接 [023](023_pc_video_view_ball_air.md)（PC 视频视图、空中球、三屏统一）。
> 用户本轮的诉求逐字：「**现在距离交付，还缺什么**」→「**现在项目还有什么没有完成的吗**」
> →「**现在你并发去完成剩余的，明天我要交了**」。

| 字段 | 内容 |
|---|---|
| 会话号 | 024 |
| 日期 | 2026-10-31 |
| 执行 AI（模型 ID） | `mythos-magpie-5487751739`（客户端 `claude`，桌面端 Code 标签页） |
| 执行客户端 / 工具 | Claude Code（桌面端）+ 1 个后台子智能体（主机侧验证批）；通道：`tools/board_jupyter.py`（Jupyter 9090，本轮仅做连通性确认） |
| 角色 | 单人 |
| 任务目标 | ① 把「已撤回的硬件结论」从全部提交件里清掉；② 补今日新增能力与已核实的边界；③ 重出文档/出包/推送 |
| 上下文投喂 | `AGENTS.md`、`ai/coordination/DECISIONS.md`、`data/results/hdmi_visual_ok.json`、`report/design_report.md`、`report/submission_checklist.md`、`report/technical_document.md`、`docs/system_status.md`、`tools/video_back.py` |
| **原文位置** | **待导出**：本会话在 Claude Code 桌面端，仓库仍无 Claude Code 会话导出脚本（与 023 同一条已知缺口，不编造文件名） |
| 关联决策 | D015（以太网视频入口原则）、**D027**（输入侧端口/协议/帧格式）、D018（显示/PC 软件）、D026（HDMI 带框环出） |
| 复核方 | 未复核（屏幕类结论仍待人眼） |

---

## 轮次 1 — 差距盘点：**最急的不是缺功能，是报告在说假话**

用户问「还缺什么」。逐文件核对后，交付风险最高的一条不是功能缺口，而是：

**`HDMI_VISUAL_OK` 已于当日 20:23 撤回**（证据文件 `data/results/hdmi_visual_ok.json`
第一行已是 `"token": "HDMI_VISUAL_RETRACTED"`），但**仍有 5 个提交件在宣称它成立**：

| 文件 | 原状态 |
|---|---|
| `report/design_report.md` | 6 处（摘要表、§2.9 判据表、§8 第 8/12 条、§9 对照表、§12 结论） |
| `report/submission_checklist.md` | 4 处（§1 表、§3.1 表、§3.2 表、§7 第 5 条） |
| `report/technical_document.md` | 1 处（§3.2.2 电路成果） |
| `docs/README.md`、`docs/flow_overlay.md` | 各 1–2 处 |
| `report/llm_logs/06_hdmi_three_defects.md` | 2 处（结论段与落点表） |

`docs/system_status.md` 是**唯一**已经改对的（245–261 行），所以这属于「局部修补没推全」，
不是「判断失误」——但对外只认提交件，必须全部清理。

**判据**：改完后 `grep -rn HDMI_VISUAL_OK --include=*.md` 的每一处命中都带
「撤回 / RETRACTED / 不成立 / 当时」限定词，无一处裸宣称。

---

## 轮次 2 — 清理方式：不删历史，只降级结论

**没做的**：把 13:56 的记录删掉、或改写成人眼从未确认。
**做的**：保留每一步的时间线，把「⇒ 通路可用」这一步单独标为撤回，并写清**为什么
当时的判据集体失灵**：

> `under=0`、六屏累加和、「每个源列恰好 1 次」这三类判据**对整行循环旋转都是瞎的** ——
> 它们量的是一行的内容集合，不是位置。所以「全 MATCH + 人眼全对」可以同时成立，
> 之后同一片 bit 又会整体旋转 k 列。

这条比「撤回」本身更重要：它是 [`report/llm_logs/06_hdmi_three_defects.md`](../../report/llm_logs/06_hdmi_three_defects.md)
那篇「判据如何被重新定义」的**第二个自我纠错**，已在该篇追加「⚠️ 追记（同日 20:23）」。

另外修掉一处**报告内部自相矛盾**：§12 结论写「队别与球权未接入主线」，而 §8 第 11 条
写「队别已接入主线（`board_pipeline --ui-config`）」——按后者的实际状态改写为
「已接入主线但只在主机侧自证，板上与真实画面未验」。

---

## 轮次 3 — 补今日新增能力与已核实的边界（不是新结论，是把已有的写进去）

| 补进报告的事实 | 出处 |
|---|---|
| 球「覆盖率 ≠ 连续性」：250 帧 / 扫球 50 帧中有球 **20 帧 ≈ 40 %**，但只集中在 `f100–f110` 与 `f140–f144` 两段 | `data/results/ball_air_c03_20261031.jsonl` |
| 修掉两个既有 bug（候选每帧不清零、`ball` 永不清空）+ 补草场外空中白块搜索 | 023 轮次 2 |
| PC 比赛画面视图（`--video`），三处显示同源 | `report/figures/pc_dashboard_video_ball.png`、`lcd_panel_ball_20261031.png` |
| **D015 只有一半**：板→PC 的 JPEG 回传已上板通过（`VIDEO_BACK_OK 250 19.61 5.1`），缺的只是 PC→板的**输入** | `tools/video_back.py`、022 |
| HDMI 间歇缺陷定量为恒定 `+313` 列（21 源列 × 7 行，`sum=1`） | 023 轮次 4 |

**没有**把「球帧 40 %」写成像「球检测达标」：D010 的物理限制（转播画面里球只有 4–12 px）
原样保留在 §8 第 2 条。

---

## 轮次 4 — 并发验证与重出包

后台子智能体跑完**全部主机侧自检 + 仿真**（本机，未碰板）：

| 组 | 结果 |
|---|---|
| 仓库自检 `check_repo.py` | `SUMMARY: 6 PASS / 0 FAIL / 0 WARN` + `CHECK_REPO_OK` |
| `check_track_preproc.py` | `RGB_MATCH True` / `MASK_MATCH True` |
| 工具自检 12 个 | `RESULT_STORE_SELFTEST OK`、`PC_DASHBOARD_SELFTEST OK`、`STATUS_DISPLAY_SELFTEST OK`、`BRAM_FB_SELFTEST OK`、`HDMI_VIDEO_SELFTEST OK`、`TEAM_ASSIGN_SELFTEST OK`、`EVENT_SELFTEST OK`、`NP_CLS_SELFTEST OK`、`TRACK_SELFTEST OK`、`CAMERA_SELFTEST OK`、`FIELD_SELFTEST OK`、`LCD_DATA_SELFTEST OK` 等 |
| 仿真 12 个 TB | `SIM_PASS 12/12 cases` |
| HWH 地址核对 | `FLOW_OVERLAY_HWH_OK`（需显式传 `--bit board/overlay/flow_system.bit`；默认路径是板端路径，主机上必然 `no_hwh`——**这是参数问题，不是缺陷**） |

出包与文档：

| 产物 | 结果 |
|---|---|
| `report/technical_document.docx` → `.pdf` | 按修订后正文重生成，0.9 MB |
| `report/figures/*.png` | 6 张重生成 |
| `dist/zynq-yolo_submission_20261031_2211.zip` | `SUBMISSION_PACK_OK`，678 文件 / 28.8 MB |
| `dist/zynq-yolo_submission_20261031_2211.rar` | `PORTAL_RAR_OK`，23.2 MB（Rar.exe 在本机 `D:/软件/winrar/`，不在 PATH，需 `--rar-exe`） |
| 旧包 | 14:03 的 zip 已按 AGENTS.md「只留当天一份」删除 |
| GitHub | `681ed52..084ee66` 已 push，`ahead_of_origin_main=0` |

**门户仍缺（只能人工）**：演示视频、承诺及使用授权书、作品全貌图。分镜脚本已备
`report/demo_video_storyboard.md`。

---

## 轮次 5 — 上板复验演示通道：网口回传真的能带框带球

用户要在明天拍演示视频，而 HDMI 通道已废。所以当晚把**「板端 → 网口 → PC」**这条
备用通道在真板上跑了一遍，而不是沿用 022 的旧记录。

| 步骤 | 实测 |
|---|---|
| 板端 c03 管线 | `PIPELINE_OK frames=250 detections=17 med_infer_ms=980.9`；`STAGES detect_ms=24532 ball_ms=30782`；`TEAM_ASSIGN tracks=42 labeled=15` |
| 落盘 | 250 行，**20 行带球**：帧 100–114、140–144 |
| 回传（match_clip 200 帧） | `VIDEO_BACK_OK 200 21.56 4.9`，屏上 17 个绿框 + `#id` |
| 回传（c03 带球 190 帧） | `VIDEO_BACK_OK 190 19.65 5.0`，**帧 105 有红圈 + `BALL` 在 (739,212)** |

证据图：`report/figures/video_back_c03_ball_20261031.png`。

### 过程中修掉的两个真 bug（都在 `tools/video_back.py`）

1. **框落在错误的帧上**：取框回退用 `seq[n]`（第 n 行），而 `--every 15` 的 JSONL 里
   `frame` 是**源帧号**（0,15,30…），两者只在 `every==1` 时相等 —— 实测把第 105 帧的框
   画到了视频第 7 帧上。改为**只按视频自己的帧计数**查表。
2. **由框内容反推播放位置**：`idx = st["frame"] + 1`，一旦错位就自我延续。已删。

另加 `--hold`（默认开）：空检测帧沿用上一帧有内容的框，**不插值、不预测**；
`--no-hold` 用于拍「逐帧真检测」的证据。

### 一个会浪费一晚上的坑（已写进分镜脚本）

`board_pipeline --out xxx.json` 写的是**汇总 JSON**（`frames`/`detections`/`ball_pct`…），
**不是逐帧 JSONL**。把它传给 `video_back --tracks`，画面里**一个框都没有，而且不报错**，
极易误判成「检测没出来」。逐帧结果只在 `--store-dir` 下的 `*_00.jsonl`。

---

## 轮次 6 — D015 输入侧：PC 经以太网送视频进主线

用户指令（逐字）：「**现在先git，然后去做第一条**」。「第一条」= 本会话盘点里
功能缺口的第 1 条 = **PC → 板 的视频输入**。

**先登记决策，再改代码**：D015 的状态是 `decided（原则）/ discussing（端口与协议）`，
按本仓库硬规则（`discussing` 不得据以改动 `src/`、`build/`、`board/`），不能直接开写。
所以先在 `ai/coordination/DECISIONS.md` 登记 **D027**，把选型一次定死：

| 子项 | 定为 | 理由 |
|---|---|---|
| 走 GEM 还是 RGMII | **PS 侧 GEM** | PYNQ Linux 自带网络栈；不动 PL、不出新片 |
| 协议 | **TCP** | 与已有的输出半条 `video_back.py` 同构，现场只维护一套认知 |
| 帧格式 | **JPEG**（默认 q95，见下） | 720p 裸帧 2.7 MB → 100 Mbit 只够 4 fps；q95 实测 ~235 KB/帧，25 fps 约 5.9 MB/s，板端解码 ~5 ms，相对 ncnn 980 ms 不是瓶颈 |
| 同步 | **TCP 流顺序 + 长度前缀** | `magic b"ZYVI"` + `[u32 len][JPEG]`，`len=0` 结束；不做时间戳 |

**D027 明写了代价**：JPEG 有损，板上像素与文件源不完全相同，判据只能说「帧数/量级一致」，
**不能说「逐位一致」**。

### 改动

| 文件 | 内容 |
|---|---|
| `tools/board_pipeline.py` | 新增 `NetFrameSource`：`read()/get()/isOpened()/release()` 与 `cv2.VideoCapture` 同接口 ⇒ **主线取帧那段一行未改**；新增 `--listen/--net-fps/--net-timeout`；`--video` 改为非必填（与 `--listen` 二选一） |
| `tools/video_send.py` | 新增。PC 端发送：`--video <文件>` 或 `--camera N`（演示「实时送」用摄像头）、`--fps` 上限、`--scale`、重试连接；判据 `VIDEO_SEND_OK` |
| `tools/video_send.py --selftest` | 本机回环自检，**不需要板子**：起一个接收线程 + 发 6 帧可辨识图，校验帧数、形状、均值单调、字节计数 ⇒ `VIDEO_SEND_SELFTEST OK` |

### 验证到哪一步（说实话）

| 判据 | 结果 |
|---|---|
| 本机传输回环（20 帧真实片段） | `VIDEO_SEND_OK 20 1.39 13.97 200.8`，接收端 20/20 解码，1280×720 |
| `VIDEO_SEND_SELFTEST` | `OK frames=6 bytes=9521` |
| `check_repo.py` | `SUMMARY: 6 PASS / 0 FAIL / 0 WARN`（144 个 .py） |
| **上板等价性** | ✅ **已补验**（详见下节「轮次 7」） |

登记当下板子离线（ping 100 % 丢包、Jupyter 超时），按 AGENTS.md 只记了主机侧通过；
`现在板子可以了` 之后当夜补上了上板等价性，结果见轮次 7。

---

## 轮次 7 — 板子回来了：D015 输入侧的上板等价性，以及一个只有上板才会暴露的质量坑

| 步骤 | 实测 |
|---|---|
| 文件源基线（`democ03`，板本地 250 帧） | `PIPELINE_OK frames=250 detections=17 med_infer_ms=980.9`；250 行 JSONL；**20 行带球**：100–114、140–144 |
| 网络源 q80（`net01`） | `VIDEO_SEND_OK 250 27.71 0.25 2.3`；板端 `PIPELINE_OK frames=250 detections=17 med_infer_ms=975.4`；250 行，但**30 行带球**（多出 f45–54） |
| 网络源 q95（`net02`） | `VIDEO_SEND_OK 250 58.74 0.53 2.2`；板端 `PIPELINE_OK frames=250 detections=17 med_infer_ms=976.2`；带球帧 **`[100…114, 140…144]` = 20 帧，与文件源完全一致** |

**结论**：帧数、检测数、带球帧集合三项都与文件源相等 ⇒ 输入侧上板等价性成立。

### q80 那 10 帧不是「JPEG 略糊」，是**换了个判决**

先按「JPEG 有损 ⇒ ±1」的预期去看，实际多了整整 10 帧，所以要查到底：
把 f50 的 `(647, 571)` 抠出来看，**是球员的白鞋**，不是球；同一坐标在连续多帧里
**一字不差地重复**，置信度 0.4/0.6 —— 典型的低质量压缩把白色小方块抹成类球纹理。
改成 `--quality 95` 后假球全部消失、带球帧与文件源逐帧对齐。

所以 `--quality` 的默认值定为 **95** 并写进帮助文本（附带原因），不再按「省带宽」直觉取 80。
这也是本轮唯一一个**只有上板才能暴露**的问题：主机回环和 `--selftest` 都测不出判决偏移。

---

## 未决 / 下一步

1. **人眼终验**：LCD 面板、PC 面板本轮的图只有机器判据 + PNG，上板后要人眼看屏（AGENTS.md 第 5 条）。
2. **HDMI 整行旋转**：演示已明确不走 HDMI；要修先写「多笔在飞」的 AXI 从机 TB 复现。
3. **D015 输入侧**已接主线并上板验过等价性（轮次 7）；仍未验的是摄像头源 `--camera` 与长时段连续传输的丢帧/超时行为。
4. **演示视频 / 授权书 / 全貌图**：只能人工产出，脚本与清单已备。
