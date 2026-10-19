// ============================================================================
// acc_conv3x3.cpp —— 3x3 量化卷积核（HLS），**v9 结构 + PAR_OUT 可扫**
//
// ============================ 版本史（全部本机实测，别重犯） ============================
//  器件 xc7z020clg400-2，10ns 时钟，测例 C_IN=64 C_OUT=128 H=W=40（YOLOv8n 的 model.8）
//
//  v1  int8 强转 int16 + ci 在内层   ⇒ LUT 178082 (334%)、DSP 20、II≈2575。不可用。
//  v2  修正定点与循环嵌套            ⇒ DSP 96 (44%)、LUT 28492、Slack +4.66ns；但内层 II=10
//                                      （每像素从 m_axi 读 9 次窗口）。
//  v3  逐字节行缓冲                  ⇒ 卷积 II=1，行填充 II=10。
//  v4  整行连续读                    ⇒ 填充 II=1、卷积 II=1；但访存 3 倍冗余、MAC/周期 17.5。
//  v5  行分块 TILE=4                 ⇒ 5 969 609 周期，3.95 GOPS，LUT 32943 (61%)。
//  v6  回写加 UNROLL                 ⇒ **综合 20 分钟未完成**（同一 loop 反复 re-schedule）。
//  v7  TILE=4、回写不加 UNROLL       ⇒ 5 969 609 周期。
//  v8  输入缓存 icache               ⇒ 5 703 378 周期（仅 −4%）；根因转移到权重被重读 10 次。
//  v9  权重缓存 wcache              ⇒ **4 979 771 周期，MAC/周期 23.7，4.74 GOPS，DSP 43**
//                                      ★ 每-DSP 效率 0.1102 GOPS/DSP，**高于 ARC2020 同芯片的 0.0653**
//                                      ⇒ 结论：瓶颈不是结构效率，而是**只用了 43 个 DSP**。
//  v10 ci 提到外层 + acc 全通道驻留   ⇒ ❌ **倒退**：13 891 652 周期（2.8x 差），卷积 II 从 1 退化到 8。
//                                      根因：acc 落进 BRAM 后被读-改-写串行化（Bank 冲突）。
//                                      ⇒ **教训：累加器必须在寄存器里（complete 分区），不能放 BRAM。**
//  v11（本版）= 回到 v9 结构，把 PAR_OUT 做成可扫参数，用**更多 DSP**换吞吐。
//
// ============================ 结构（v9） ============================
//   for co0（输出通道块，步长 PAR_OUT）
//     ① 预读本块权重到 wcache（片上，全 ci）
//     ② for t0（行块，步长 TILE）
//          初始化 acc[PAR_OUT][TILE][W] = bias
//          for ci:
//             从 icache 载入 TILE+2 行到 lb（行缓冲）
//             for hl, wo: 用 lb[hl..hl+2] 卷积，累加进 acc（**寄存器**）
//          写回 TILE 行
//
//   三块片上缓存：icache（输入整层）、wcache（本通道块权重全 ci）、acc（寄存器）
//   容量核算（测例）：icache 102 400 B；wcache = PAR×64×9 = 4 608 B (PAR=8)；
//                     acc = PAR×TILE×W×4B = 5 120 B (PAR=8,TILE=4) —— 全在寄存器/BRAM 预算内。
//
// ============================ 定点口径（不可改） ============================
//   激活 int8、权重 int8、累加 int32、输出 int8；zero_point = 0；
//   输出 = clamp(acc >> scale_shift, 0, 127)。与 data/golden 的比对依赖此口径。
// ============================================================================
#include <ap_int.h>

#ifndef C_IN
#define C_IN 8
#endif
#ifndef C_OUT
#define C_OUT 8
#endif
#ifndef H_IN
#define H_IN 8
#endif
#ifndef W_IN
#define W_IN 8
#endif
#ifndef PAR_OUT
#define PAR_OUT 8
#endif
#ifndef TILE
#define TILE 4
#endif

#define K 3
#define H_OUT H_IN
#define W_OUT W_IN
#define N_PIX (H_OUT * W_OUT)
#define LB_W (W_IN + 2)
#define LB_R (TILE + 2)

void acc_conv3x3(const ap_int<8>  *in,
                 const ap_int<8>  *weight,
                 const ap_int<32> *bias,
                 ap_int<8>        *out,
                 const int         scale_shift)
{
#pragma HLS INTERFACE m_axi port=in     offset=slave bundle=gmem0 depth=1048576
#pragma HLS INTERFACE m_axi port=weight offset=slave bundle=gmem1 depth=1048576
#pragma HLS INTERFACE m_axi port=bias   offset=slave bundle=gmem2 depth=4096
#pragma HLS INTERFACE m_axi port=out    offset=slave bundle=gmem3 depth=1048576
#pragma HLS INTERFACE s_axilite port=scale_shift bundle=control
#pragma HLS INTERFACE s_axilite port=return      bundle=control

    // 输入整层缓存（一次性读入，避免 co0 块之间重读）
    ap_int<8> icache[C_IN * H_IN * W_IN];

    // 本通道块的权重（全 ci），避免 t0 之间重读
    ap_int<8> wcache[PAR_OUT][C_IN][K][K];

    // 累加器：**必须 complete 分区 ⇒ 落寄存器**（v10 放 BRAM 的教训）
    ap_int<32> acc[PAR_OUT][TILE][W_OUT];
#pragma HLS ARRAY_PARTITION variable=acc complete dim=1

    ap_int<8> lb[LB_R][LB_W];
#pragma HLS ARRAY_PARTITION variable=lb complete dim=0

    ap_int<8> wbuf[PAR_OUT][K][K];
#pragma HLS ARRAY_PARTITION variable=wbuf complete dim=0

    // ---- ① 输入整层预读 ----
    for (int i = 0; i < C_IN * H_IN * W_IN; ++i) {
#pragma HLS PIPELINE II=1
        icache[i] = in[i];
    }

    for (int co0 = 0; co0 < C_OUT; co0 += PAR_OUT) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=256

        // ---- ①b 本通道块权重预读（全 ci）----
        for (int p = 0; p < PAR_OUT; ++p) {
#pragma HLS UNROLL
            for (int ci = 0; ci < C_IN; ++ci) {
                for (int kh = 0; kh < K; ++kh) {
#pragma HLS UNROLL
                    for (int kw = 0; kw < K; ++kw) {
#pragma HLS UNROLL
                        wcache[p][ci][kh][kw] =
                            weight[((co0 + p) * C_IN + ci) * K * K + kh * K + kw];
                    }
                }
            }
        }

        for (int t0 = 0; t0 < H_OUT; t0 += TILE) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=1024

            // ---- ② 初始化累加器 ----
            for (int hl = 0; hl < TILE; ++hl) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=32
                for (int wo = 0; wo < W_OUT; ++wo) {
#pragma HLS PIPELINE II=1
                    for (int p = 0; p < PAR_OUT; ++p) {
#pragma HLS UNROLL
                        acc[p][hl][wo] = bias[co0 + p];
                    }
                }
            }

            // ---- ③ 逐输入通道累加 ----
            for (int ci = 0; ci < C_IN; ++ci) {
#pragma HLS LOOP_TRIPCOUNT min=3 max=1024

                // 行缓冲：从 icache 载入 LB_R 行（tile 内所有输出行共用）
                // ★ 整行"连续"读（不是逐像素散读）—— v3→v4 的实测教训：
                //   散读会让 II=10，连续读才是 1。
                for (int r = 0; r < LB_R; ++r) {
#pragma HLS LOOP_TRIPCOUNT min=3 max=34
                    int hr = t0 + r - 1;
                    if (hr < 0 || hr >= H_IN) {
                        for (int c = 0; c < LB_W; ++c) {
#pragma HLS PIPELINE II=1
                            lb[r][c] = ap_int<8>(0);
                        }
                    } else {
                        for (int c = 0; c < W_IN; ++c) {
#pragma HLS PIPELINE II=1
                            lb[r][c + 1] = icache[((ci * H_IN) + hr) * W_IN + c];
                        }
                        lb[r][0]        = ap_int<8>(0);
                        lb[r][W_IN + 1] = ap_int<8>(0);
                    }
                }

                // 权重取到小缓存（片上，不碰 m_axi）
                for (int p = 0; p < PAR_OUT; ++p) {
#pragma HLS UNROLL
                    for (int kh = 0; kh < K; ++kh) {
#pragma HLS UNROLL
                        for (int kw = 0; kw < K; ++kw) {
#pragma HLS UNROLL
                            wbuf[p][kh][kw] = wcache[p][ci][kh][kw];
                        }
                    }
                }

                // 卷积：每时钟 PAR_OUT × 9 个 int8 MAC
                for (int hl = 0; hl < TILE; ++hl) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=32
                    for (int wo = 0; wo < W_OUT; ++wo) {
#pragma HLS PIPELINE II=1
                        for (int p = 0; p < PAR_OUT; ++p) {
#pragma HLS UNROLL
                            ap_int<32> partial = 0;
                            for (int kh = 0; kh < K; ++kh) {
#pragma HLS UNROLL
                                for (int kw = 0; kw < K; ++kw) {
#pragma HLS UNROLL
                                    partial += ap_int<16>(lb[hl + kh][wo + kw])
                                             * ap_int<16>(wbuf[p][kh][kw]);
                                }
                            }
                            acc[p][hl][wo] += partial;
                        }
                    }
                }
            }

            // ---- ④ 写回（按通道分流 ⇒ 连续地址；**不加 UNROLL**，见 v6 教训）----
            for (int p = 0; p < PAR_OUT; ++p) {
                for (int hl = 0; hl < TILE; ++hl) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=32
                    int ho = t0 + hl;
                    if (ho < H_OUT) {
                        ap_int<8> *op = out + (co0 + p) * N_PIX + ho * W_OUT;
                        for (int wo = 0; wo < W_OUT; ++wo) {
#pragma HLS PIPELINE II=1
                            ap_int<32> v = acc[p][hl][wo] >> scale_shift;
                            if (v < 0) v = 0;
                            if (v > 127) v = 127;
                            op[wo] = ap_int<8>(v);
                        }
                    }
                }
            }
        }
    }
}
