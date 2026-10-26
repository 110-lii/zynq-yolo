# track_preproc —— PL 预处理 + 跟踪辅助掩码（D019）

把原 `preproc`（BGR→320×320 RGB）与 `mask_preproc`（白/运动掩码）融合成一个 HLS 顶层，
一次调用同时产出 ncnn 输入和跟踪用的 320×320 运动掩码，避免 PYNQ 同时只能加载一个
overlay 的限制。

## 文件

| 路径 | 说明 |
|---|---|
| `src/track_preproc.cpp` | HLS 顶层；4 个 `m_axi`（in/prev/rgb/mask）+ 1 个 `s_axilite` |
| `tb/b_track_preproc.cpp` | 主机对拍：cur/prev → RGB 平面 + mask |
| `scripts/export_track.tcl` | HLS csynth + 导出 IP |
| `scripts/export_track.py` | 处理 Vivado 2020.2 `core_revision` 溢出并打包 |

## 产物

- IP：`build/ip_repo/track_preproc/`
- 板级：`board/overlay/track_preproc.bit/.hwh`（由 `build/scripts/bd_track_preproc.tcl` 生成）

## 判据

1. `tb_track_preproc` 输出 `TRACK_TB_OK`；
2. HLS 导出 `IP_EXPORT_OK track_preproc`；
3. BD 出片 `BD_DONE_OK`；
4. 板上 `tools/track_preproc.py` 加载成功，RGB 与 `preproc` 旧路径一致、mask 与
   Python 参考一致（逐像素）。

> 当前状态：源码/BD/驱动已落盘，**第 2–4 步待用户批准后执行**；没有 bitstream 时
> `board_pipeline.py --pl-track` 自动回退 PS。
