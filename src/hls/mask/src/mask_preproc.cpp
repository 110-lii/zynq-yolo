// mask_preproc.cpp —— 速度优先的像素预处理：白球掩码 + 运动掩码
//
// 为什么放 PL
//   板端 PS 实测：640x360 全帧 HSV 阈值 + 形态学 + 轮廓 = 41 ms；320x180 帧差 = 9.6 ms。
//   这里把“每像素阈值”搬到 PL，PS 只做形态学/连通域/闸门。
//
// 接口（与 conv_engine 相同的 m_axi + s_axilite 模式）：
//   cur  : 当前帧 BGR（W*H*3 字节，按 32-bit 字访问）
//   prev : 前一帧 BGR（只读 G 通道参与帧差）
//   out  : 每像素 1 字节：bit0=白（高亮低饱和），bit1=运动（|G-Gprev|>diff_thr）
//
// ★ 带宽写法（v4）：每轮 16 个像素 = 48 字节 = 每个缓冲区连续 12 个 32-bit 字。
//   v1（字节级）板端 54.6 ms；v2（4 像素/轮，3 字）20.2 ms —— 后者暴露了
//   “3 字突发太短、每字约 4 拍”的问题（实测 7.3 拍/像素，II 本应 1.5）。
//   v4 把连续读拉长到 12 字，配合 num_read_outstanding=16，目标是 ~4 ms/帧。
//   要求 (W*H) % 16 == 0（640x360、320x180、1280x720、64x64 都满足）。
//
// 形态学（open/dilate）与连通域仍在 PS：对稀疏掩码做 cv2 只需 2-4 ms，且便于调参。
#include <ap_int.h>

#define N_WORDS_IN 12   // 16 像素 × 3 字节 = 48 字节
#define N_WORDS_OUT 4   // 16 像素 × 1 字节

extern "C" void mask_preproc(const ap_uint<32> *cur,
                             const ap_uint<32> *prev,
                             ap_uint<32> *out,
                             int W,
                             int H,
                             int white_thr,
                             int sat_thr,
                             int diff_thr) {
#pragma HLS INTERFACE m_axi port=cur offset=slave bundle=gmem_c depth=4194304 num_read_outstanding=16 max_read_burst_length=64
#pragma HLS INTERFACE m_axi port=prev offset=slave bundle=gmem_p depth=4194304 num_read_outstanding=16 max_read_burst_length=64
#pragma HLS INTERFACE m_axi port=out offset=slave bundle=gmem_o depth=4194304 num_write_outstanding=8 max_write_burst_length=64
#pragma HLS INTERFACE s_axilite port=cur
#pragma HLS INTERFACE s_axilite port=prev
#pragma HLS INTERFACE s_axilite port=out
#pragma HLS INTERFACE s_axilite port=W
#pragma HLS INTERFACE s_axilite port=H
#pragma HLS INTERFACE s_axilite port=white_thr
#pragma HLS INTERFACE s_axilite port=sat_thr
#pragma HLS INTERFACE s_axilite port=diff_thr
#pragma HLS INTERFACE s_axilite port=return

    const int n16 = (W * H) >> 4;
    for (int p16 = 0; p16 < n16; ++p16) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=57600
#pragma HLS PIPELINE II=1
        const int base = p16 * N_WORDS_IN;
        ap_uint<32> cw[N_WORDS_IN];
        ap_uint<32> qw[N_WORDS_IN];
#pragma HLS ARRAY_PARTITION variable=cw complete
#pragma HLS ARRAY_PARTITION variable=qw complete
#pragma HLS UNROLL
        for (int t = 0; t < N_WORDS_IN; ++t) {
#pragma HLS UNROLL
            cw[t] = cur[base + t];
        }
#pragma HLS UNROLL
        for (int t = 0; t < N_WORDS_IN; ++t) {
#pragma HLS UNROLL
            qw[t] = prev[base + t];
        }

        ap_uint<32> outw[N_WORDS_OUT];
#pragma HLS ARRAY_PARTITION variable=outw complete
#pragma HLS UNROLL
        for (int t = 0; t < N_WORDS_OUT; ++t) outw[t] = 0;

#pragma HLS UNROLL
        for (int j = 0; j < 16; ++j) {
#pragma HLS UNROLL
            const int n = 3 * j;
            const int wb = n >> 2;
            const int lb = (n & 3) * 8;
            ap_uint<8> b = (ap_uint<8>)cw[wb].range(lb + 7, lb);
            ap_uint<8> g, r, pg;
            if ((n & 3) <= 1) {
                // B/G/R 同字
                g = (ap_uint<8>)cw[wb].range(lb + 15, lb + 8);
                r = (ap_uint<8>)cw[wb].range(lb + 23, lb + 16);
                pg = (ap_uint<8>)qw[wb].range(lb + 15, lb + 8);
            } else if ((n & 3) == 2) {
                // B/G 在本字，R 在下个字
                g = (ap_uint<8>)cw[wb].range(31, 24);
                r = (ap_uint<8>)cw[wb + 1].range(7, 0);
                pg = (ap_uint<8>)qw[wb].range(31, 24);
            } else {
                // B 在本字，G/R 在下个字
                g = (ap_uint<8>)cw[wb + 1].range(7, 0);
                r = (ap_uint<8>)cw[wb + 1].range(15, 8);
                pg = (ap_uint<8>)qw[wb + 1].range(7, 0);
            }

            ap_uint<8> mx = b;
            if (g > mx) mx = g;
            if (r > mx) mx = r;
            ap_uint<8> mn = b;
            if (g < mn) mn = g;
            if (r < mn) mn = r;

            ap_uint<8> m = 0;
            if (mx > (ap_uint<8>)white_thr && (ap_uint<8>)(mx - mn) < (ap_uint<8>)sat_thr) {
                m = m | 1;
            }
            int d = (int)g - (int)pg;
            if (d < 0) d = -d;
            if (d > diff_thr) {
                m = m | 2;
            }
            outw[j >> 2] |= ((ap_uint<32>)m) << (8 * (j & 3));
        }
#pragma HLS UNROLL
        for (int t = 0; t < N_WORDS_OUT; ++t) {
#pragma HLS UNROLL
            out[p16 * N_WORDS_OUT + t] = outw[t];
        }
    }
}