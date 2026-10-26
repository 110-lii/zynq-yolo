// ============================================================================
// track_preproc.cpp —— PL 预处理 + 跟踪辅助掩码（单 IP，单 overlay）
//
// 为什么融合
//   原来 preproc（BGR->320 RGB）与 mask_preproc（白/运动掩码）是两个 IP；
//   PYNQ 同一时刻只能加载一个 overlay，导致“PL 预处理”和“PL 掩码”不能同时用。
//   这里把两件事放进同一个 HLS 顶层、同一遍输出循环：
//     * rgb   : 3 x 320 x 320 RGB 平面（channel-first，ncnn 布局）
//     * mask  : 320 x 320 每像素 1 字节，bit0=白（高亮低饱和），bit1=运动
//   运动掩码来自 curr/prev 的 G 通道差，供 PS 跟踪做“新轨迹运动门控”。
//
// 输入  in   : BGR888，W_IN x H_IN，行主序
// 输入  prev : 上一帧 BGR888，同尺寸（首帧可传当前帧）
// 输出  rgb  : 3 x 320 x 320 字节
// 输出  mask : 320 x 320 字节
//
// 资源预期：preproc 3.8k LUT + mask 2.2k LUT 量级；4 个 m_axi -> HP0..HP3。
// 最近邻而非双线性：双线性 HLS 综合 LUT 175% / DSP 904%，30 分钟不收敛（见
// docs/system_status.md 与 ai/sessions/009）。
// ============================================================================
#include <ap_int.h>

#ifndef OUT_W
#define OUT_W 320
#endif
#ifndef OUT_H
#define OUT_H 320
#endif

void track_preproc(const ap_uint<8> *in,
                   const ap_uint<8> *prev,
                   ap_uint<8> *rgb,
                   ap_uint<8> *mask,
                   int W_IN, int H_IN,
                   int white_thr, int sat_thr, int diff_thr)
{
#pragma HLS INTERFACE m_axi port=in   offset=slave bundle=gmem_in   depth=4194304 num_read_outstanding=16 max_read_burst_length=64
#pragma HLS INTERFACE m_axi port=prev offset=slave bundle=gmem_prev depth=4194304 num_read_outstanding=16 max_read_burst_length=64
#pragma HLS INTERFACE m_axi port=rgb  offset=slave bundle=gmem_rgb  depth=307200  num_write_outstanding=16 max_write_burst_length=64
#pragma HLS INTERFACE m_axi port=mask offset=slave bundle=gmem_mask depth=102400  num_write_outstanding=8  max_write_burst_length=64
#pragma HLS INTERFACE s_axilite port=in        bundle=control
#pragma HLS INTERFACE s_axilite port=prev      bundle=control
#pragma HLS INTERFACE s_axilite port=rgb       bundle=control
#pragma HLS INTERFACE s_axilite port=mask      bundle=control
#pragma HLS INTERFACE s_axilite port=W_IN      bundle=control
#pragma HLS INTERFACE s_axilite port=H_IN      bundle=control
#pragma HLS INTERFACE s_axilite port=white_thr bundle=control
#pragma HLS INTERFACE s_axilite port=sat_thr   bundle=control
#pragma HLS INTERFACE s_axilite port=diff_thr  bundle=control
#pragma HLS INTERFACE s_axilite port=return    bundle=control

    const int xstep = (W_IN << 16) / OUT_W;
    const int ystep = (H_IN << 16) / OUT_H;
    const int xmax = (W_IN - 1) << 16;
    const int ymax = (H_IN - 1) << 16;

    for (int oy = 0; oy < OUT_H; ++oy) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=320
        int ypos = (ystep >> 1) - 32768 + oy * ystep;
        int sy = (ypos < 0) ? 0 : ((ypos > ymax ? ymax : ypos) >> 16);
        int row = sy * W_IN * 3;

        for (int ox = 0; ox < OUT_W; ++ox) {
#pragma HLS PIPELINE II=1
            int xpos = (xstep >> 1) - 32768 + ox * xstep;
            int sx = (xpos < 0) ? 0 : ((xpos > xmax ? xmax : xpos) >> 16);
            int base = row + sx * 3;

            int b = (int)in[base + 0];
            int g = (int)in[base + 1];
            int r = (int)in[base + 2];
            int pg = (int)prev[base + 1];

            // 最近邻缩放到 320x320，RGB 平面（ncnn 需要 channel-first）
            rgb[0 * (OUT_W * OUT_H) + oy * OUT_W + ox] = (ap_uint<8>)r;
            rgb[1 * (OUT_W * OUT_H) + oy * OUT_W + ox] = (ap_uint<8>)g;
            rgb[2 * (OUT_W * OUT_H) + oy * OUT_W + ox] = (ap_uint<8>)b;

            int mx = b;
            if (g > mx) mx = g;
            if (r > mx) mx = r;
            int mn = b;
            if (g < mn) mn = g;
            if (r < mn) mn = r;

            int m = 0;
            if (mx > white_thr && (mx - mn) < sat_thr) {
                m |= 1;                       // 白球/白线候选
            }
            int d = g - pg;
            if (d < 0) d = -d;
            if (d > diff_thr) {
                m |= 2;                       // 运动：跟踪门控/活跃度
            }
            mask[oy * OUT_W + ox] = (ap_uint<8>)m;
        }
    }
}
