// ============================================================================
// axi_bw.cpp —— 纯 AXI 主口带宽标定（不参与任何算法）
//
// 目的：把「PL 主口经 S_AXI_HP 读/写 DDR 的实际吞吐」单独量出来，与以下因素解耦：
//   - conv_engine 的循环结构与 II
//   - 权重/激活的访问形状
//   - PS 侧组帧、后处理
//
// 四个模式（运行期选择，一次出片全部可测）：
//   mode 0：32-bit 读   in32[0..n32)        （对应 conv_engine 现在的读口）
//   mode 1：64-bit 读   in64[0..n64)        （HP 口原生宽度）
//   mode 2：32-bit 写   out32[0..n32)
//   mode 3：32-bit 拷贝 in32 -> out32（读+写同时，走两个 HP 口）
//
// `reps` 在核内重复整趟，摊掉 ap_start / 中断 / PS 侧轮询的开销。
// 读模式把异或结果写到 out32[0]，防止综合把整趟读优化掉。
// ============================================================================
#include <ap_int.h>

void axi_bw(const ap_uint<32> *in32,
            const ap_uint<64> *in64,
            ap_uint<32>       *out32,
            int n32, int n64, int mode, int reps)
{
    #pragma HLS INTERFACE m_axi port=in32  offset=slave bundle=gmem_r32 depth=8388608 num_read_outstanding=32 max_read_burst_length=64
    #pragma HLS INTERFACE m_axi port=in64  offset=slave bundle=gmem_r64 depth=8388608 num_read_outstanding=32 max_read_burst_length=64
    #pragma HLS INTERFACE m_axi port=out32 offset=slave bundle=gmem_w   depth=8388608 num_write_outstanding=32 max_write_burst_length=64
    #pragma HLS INTERFACE s_axilite port=n32   bundle=control
    #pragma HLS INTERFACE s_axilite port=n64   bundle=control
    #pragma HLS INTERFACE s_axilite port=mode  bundle=control
    #pragma HLS INTERFACE s_axilite port=reps  bundle=control
    #pragma HLS INTERFACE s_axilite port=return bundle=control

    if (mode == 0) {
        ap_uint<32> acc = 0;
        for (int r = 0; r < reps; ++r) {
            for (int i = 0; i < n32; ++i) {
                #pragma HLS PIPELINE II=1
                acc ^= in32[i];
            }
        }
        out32[0] = acc;

    } else if (mode == 1) {
        ap_uint<64> acc = 0;
        for (int r = 0; r < reps; ++r) {
            for (int i = 0; i < n64; ++i) {
                #pragma HLS PIPELINE II=1
                acc ^= in64[i];
            }
        }
        out32[0] = ap_uint<32>(acc) ^ ap_uint<32>(acc >> 32);

    } else if (mode == 2) {
        for (int r = 0; r < reps; ++r) {
            for (int i = 0; i < n32; ++i) {
                #pragma HLS PIPELINE II=1
                out32[i] = ap_uint<32>(i);
            }
        }

    } else {
        for (int r = 0; r < reps; ++r) {
            for (int i = 0; i < n32; ++i) {
                #pragma HLS PIPELINE II=1
                out32[i] = in32[i];
            }
        }
    }
}
