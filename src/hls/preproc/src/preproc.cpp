// ============================================================================
// preproc.cpp —— PL 预处理（最近邻版）：BGR888 帧 -> 320x320 RGB 平面
//
// 为什么是最近邻：双线性版本（preproc_bilinear.cpp.bak）功能正确、与 OpenCV
// 差 1 LSB，但 HLS 综合要 30 分钟以上仍不出报告。最近邻每像素只读 3 字节、
// 无行缓冲、无插值乘法，逻辑量小一到两个数量级，先保证 PL 上有可跑的流水线。
//
// 输入  in : BGR888，W_IN x H_IN，行主序
// 输出  out: RGB888 平面，3 x 320 x 320 字节（channel 优先，ncnn 布局）
// ============================================================================
#include <ap_int.h>

#ifndef OUT_W
#define OUT_W 320
#endif
#ifndef OUT_H
#define OUT_H 320
#endif

void preproc(const ap_uint<8> *in,
             ap_uint<8> *out,
             int W_IN, int H_IN)
{
    #pragma HLS INTERFACE m_axi port=in  offset=slave bundle=gmem_in  depth=4194304 num_read_outstanding=16 max_read_burst_length=64
    #pragma HLS INTERFACE m_axi port=out offset=slave bundle=gmem_out depth=307200  num_write_outstanding=16 max_write_burst_length=64
    #pragma HLS INTERFACE s_axilite port=W_IN  bundle=control
    #pragma HLS INTERFACE s_axilite port=H_IN  bundle=control
    #pragma HLS INTERFACE s_axilite port=return bundle=control

    // 最近邻坐标：src = (dst + 0.5) * scale - 0.5，取整后 clamp
    int xstep = (W_IN << 16) / OUT_W;
    int ystep = (H_IN << 16) / OUT_H;
    int xmax = (W_IN - 1) << 16;
    int ymax = (H_IN - 1) << 16;

    for (int oy = 0; oy < OUT_H; ++oy) {
        #pragma HLS LOOP_TRIPCOUNT min=1 max=320
        int ypos = (ystep >> 1) - 32768 + oy * ystep;
        int sy = (ypos < 0) ? 0 : ((ypos > ymax ? ymax : ypos) >> 16);
        long long row = (long long)sy * W_IN * 3;

        for (int ox = 0; ox < OUT_W; ++ox) {
            #pragma HLS PIPELINE II=1
            int xpos = (xstep >> 1) - 32768 + ox * xstep;
            int sx = (xpos < 0) ? 0 : ((xpos > xmax ? xmax : xpos) >> 16);
            long long base = row + (long long)sx * 3;
            ap_uint<8> b = in[base + 0];
            ap_uint<8> g = in[base + 1];
            ap_uint<8> r = in[base + 2];
            out[0 * (OUT_W * OUT_H) + oy * OUT_W + ox] = r;
            out[1 * (OUT_W * OUT_H) + oy * OUT_W + ox] = g;
            out[2 * (OUT_W * OUT_H) + oy * OUT_W + ox] = b;
        }
    }
}