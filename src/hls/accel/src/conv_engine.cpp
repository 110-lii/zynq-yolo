// ============================================================================
// conv_engine.cpp —— 单层通用 INT8 卷积引擎（HLS）
//
// 数据布局（字节口径不变，PS 侧无需改动）：
//   in     uint8 [C_IN][H_IN][W_IN]     激活，无符号，行主序
//   w      int8  [C_OUT][C_IN][K][K]    权重，有符号
//   params int32 [C_OUT][3]             {bias_i32, M, S}
//   lut    uint8 [256]                  激活表
//   out    uint8 [C_OUT][H_OUT][W_OUT]
//
// 访存口径（本文件的核心）：
//   m_axi 的事务粒度由指针类型决定，不由 depth/pragma 决定。用 ap_uint<8>* 时
//   HLS 只会发出单字节事务，实测 DDR 读吞吐被压在 10–22 MB/s；改成 32-bit 字
//   指针后，每个 beat 搬 4 字节，连续地址才有可能被合并成长突发。
//   同时：所有 read 都是**无条件**的——越界/尾块用"地址钳位 + 数据选择"处理，
//   绝不在读地址上挂 if，否则 HLS 无法把事务合并成突发。
//
// 算术口径与 tools/ref_int8_conv.py 逐位一致，改动必须重跑 tb_conv_engine.cpp。
// ============================================================================
#include <ap_int.h>

// ---- 实现参数（不属于数值契约） ----
#ifndef PAR_OUT
#define PAR_OUT 8
#endif
#ifndef TILE_H
#define TILE_H 1
#endif
#ifndef TILE_W
#define TILE_W 40
#endif
#ifndef ACC_FULL
#define ACC_FULL 0
#endif

// ---- 编译期上界 ----
#define K_MAX 3
#define STRIDE_MAX 2
#define LB_W_MAX (TILE_W * STRIDE_MAX + K_MAX - 1)

// ============================================================================
// 三个 stage：用 DATAFLOW 让「装载 ci+1」与「计算 ci」重叠
// ============================================================================
static void ce_stage_in(const ap_uint<32> *in, int ci,
                        int H_IN, int W_IN, int t0, int w0,
                        int K, int stride, int pad, int zp_in, int ncol,
                        ap_uint<8> (&lb)[TILE_H][K_MAX][LB_W_MAX])
{
    #pragma HLS INLINE
    #pragma HLS ARRAY_PARTITION variable=lb complete dim=0

    for (int hl = 0; hl < TILE_H; ++hl) {
        #pragma HLS UNROLL
        for (int kh = 0; kh < K_MAX; ++kh) {
            #pragma HLS UNROLL
            if (kh < K) {
                int hr_raw = (t0 + hl) * stride + kh - pad;
                bool row_ok = (hr_raw >= 0) && (hr_raw < H_IN);
                int hr = row_ok ? hr_raw : 0;
                int cb = w0 * stride - pad;
                int row_byte = (ci * H_IN + hr) * W_IN;
                int g0 = row_byte + cb;
                int q0 = (g0 < 0) ? 0 : (g0 >> 2);
                int q1 = (g0 + ncol - 1) >> 2;

                if (cb < 0) {
                    lb[hl][kh][0] = ap_uint<8>(zp_in);
                }

                for (int q = q0; q <= q1; ++q) {
                    #pragma HLS PIPELINE II=1
                    ap_uint<32> wd = in[q];
                    int sb = (q << 2) - g0;
                    for (int i = 0; i < 4; ++i) {
                        #pragma HLS UNROLL
                        int s = sb + i;
                        if (s >= 0 && s < ncol) {
                            int ac = (q << 2) + i - row_byte;
                            lb[hl][kh][s] =
                                (row_ok && ac >= 0 && ac < W_IN)
                                ? ap_uint<8>(wd >> (8 * i))
                                : ap_uint<8>(zp_in);
                        }
                    }
                }
            }
        }
    }
}

static void ce_stage_w(const ap_int<32> *w, int ci, int co0,
                       int C_IN, int C_OUT, int K,
                       ap_int<8> (&wbuf)[PAR_OUT * K_MAX * K_MAX])
{
    #pragma HLS INLINE
    #pragma HLS ARRAY_PARTITION variable=wbuf complete dim=1

    if (K * K <= 4) {
        for (int p = 0; p < PAR_OUT; ++p) {
            #pragma HLS UNROLL
            int co = co0 + p;
            bool vld = (co < C_OUT);
            int rec = (vld ? co : (C_OUT - 1)) * C_IN + ci;
            ap_uint<32> r0 = ap_uint<32>(w[rec]);
            for (int kk = 0; kk < K_MAX * K_MAX; ++kk) {
                #pragma HLS UNROLL
                wbuf[p * (K_MAX * K_MAX) + kk] =
                    (vld && kk < K * K)
                    ? ap_int<8>(ap_uint<8>(r0 >> (8 * kk)))
                    : ap_int<8>(0);
            }
        }
    } else {
        for (int p = 0; p < PAR_OUT; ++p) {
            #pragma HLS UNROLL
            int co = co0 + p;
            bool vld = (co < C_OUT);
            int rec = ((vld ? co : (C_OUT - 1)) * C_IN + ci) * 3;
            ap_uint<32> r0 = ap_uint<32>(w[rec + 0]);
            ap_uint<32> r1 = ap_uint<32>(w[rec + 1]);
            ap_uint<32> r2 = ap_uint<32>(w[rec + 2]);
            wbuf[p * (K_MAX * K_MAX) + 0] = vld ? ap_int<8>(ap_uint<8>(r0))       : ap_int<8>(0);
            wbuf[p * (K_MAX * K_MAX) + 1] = vld ? ap_int<8>(ap_uint<8>(r0 >> 8))  : ap_int<8>(0);
            wbuf[p * (K_MAX * K_MAX) + 2] = vld ? ap_int<8>(ap_uint<8>(r0 >> 16)) : ap_int<8>(0);
            wbuf[p * (K_MAX * K_MAX) + 3] = vld ? ap_int<8>(ap_uint<8>(r0 >> 24)) : ap_int<8>(0);
            wbuf[p * (K_MAX * K_MAX) + 4] = vld ? ap_int<8>(ap_uint<8>(r1))       : ap_int<8>(0);
            wbuf[p * (K_MAX * K_MAX) + 5] = vld ? ap_int<8>(ap_uint<8>(r1 >> 8))  : ap_int<8>(0);
            wbuf[p * (K_MAX * K_MAX) + 6] = vld ? ap_int<8>(ap_uint<8>(r1 >> 16)) : ap_int<8>(0);
            wbuf[p * (K_MAX * K_MAX) + 7] = vld ? ap_int<8>(ap_uint<8>(r1 >> 24)) : ap_int<8>(0);
            wbuf[p * (K_MAX * K_MAX) + 8] = vld ? ap_int<8>(ap_uint<8>(r2))       : ap_int<8>(0);
        }
    }
}

static void ce_stage_conv(ap_uint<8> (&lb)[TILE_H][K_MAX][LB_W_MAX],
                          ap_int<8> (&wbuf)[PAR_OUT * K_MAX * K_MAX],
                          ap_int<32> (&acc)[PAR_OUT][TILE_H][TILE_W],
                          int K, int stride)
{
    #pragma HLS INLINE
    #pragma HLS ARRAY_PARTITION variable=lb complete dim=0
    #pragma HLS ARRAY_PARTITION variable=wbuf complete dim=1
    #pragma HLS ARRAY_PARTITION variable=acc complete dim=1

    for (int hl = 0; hl < TILE_H; ++hl) {
        #pragma HLS LOOP_TRIPCOUNT min=1 max=8
        for (int wo = 0; wo < TILE_W; ++wo) {
            #pragma HLS PIPELINE II=1
            for (int p = 0; p < PAR_OUT; ++p) {
                #pragma HLS UNROLL
                ap_int<32> partial = 0;
                for (int kh = 0; kh < K_MAX; ++kh) {
                    #pragma HLS UNROLL
                    for (int kw = 0; kw < K_MAX; ++kw) {
                        #pragma HLS UNROLL
                        if (kh < K && kw < K) {
                            ap_int<18> pr =
                                ap_int<9>(lb[hl][kh][wo * stride + kw])
                              * ap_int<9>(wbuf[p * (K_MAX * K_MAX)
                                              + kh * K_MAX + kw]);
                            partial += ap_int<32>(pr);
                        }
                    }
                }
                acc[p][hl][wo] += partial;
            }
        }
    }
}

void conv_engine(const ap_uint<32> *in,
                 const ap_int<32>  *w,
                 const ap_int<32>  *params,
                 const ap_uint<8>  *lut,
                 ap_uint<8>        *out,
                 int C_IN, int C_OUT,
                 int H_IN, int W_IN,
                 int K, int stride, int pad,
                 int zp_in, int zp_pre,
                 int H_OUT, int W_OUT)
{
    // 读口：32-bit 字，32 个未完成读 + 64 beat 突发。未完成读是掩盖 DDR 延迟的
    // 唯一手段（单字节/低 outstanding 时实测只有 ~20 MB/s）。
    #pragma HLS INTERFACE m_axi port=in     offset=slave bundle=gmem_rin depth=33554432 num_read_outstanding=32 max_read_burst_length=64
    #pragma HLS INTERFACE m_axi port=w      offset=slave bundle=gmem_rw depth=33554432 num_read_outstanding=32 max_read_burst_length=64
    #pragma HLS INTERFACE m_axi port=params offset=slave bundle=gmem_rw depth=8192  num_read_outstanding=32 max_read_burst_length=64
    #pragma HLS INTERFACE m_axi port=lut    offset=slave bundle=gmem_rw depth=256   num_read_outstanding=32 max_read_burst_length=64
    #pragma HLS INTERFACE m_axi port=out    offset=slave bundle=gmem_w depth=33554432 num_write_outstanding=16 max_write_burst_length=64
    #pragma HLS INTERFACE s_axilite port=C_IN   bundle=control
    #pragma HLS INTERFACE s_axilite port=C_OUT  bundle=control
    #pragma HLS INTERFACE s_axilite port=H_IN   bundle=control
    #pragma HLS INTERFACE s_axilite port=W_IN   bundle=control
    #pragma HLS INTERFACE s_axilite port=K      bundle=control
    #pragma HLS INTERFACE s_axilite port=stride bundle=control
    #pragma HLS INTERFACE s_axilite port=pad    bundle=control
    #pragma HLS INTERFACE s_axilite port=zp_in  bundle=control
    #pragma HLS INTERFACE s_axilite port=zp_pre bundle=control
    #pragma HLS INTERFACE s_axilite port=H_OUT  bundle=control
    #pragma HLS INTERFACE s_axilite port=W_OUT  bundle=control
    #pragma HLS INTERFACE s_axilite port=return bundle=control

    // 行缓冲：[输出行][抽头行][列]，全展开 ⇒ 每拍 K*K 个抽头是寄存器直连
    ap_uint<8> lb[TILE_H][K_MAX][LB_W_MAX];
    #pragma HLS ARRAY_PARTITION variable=lb complete dim=0

    // 本 ci 的权重小块：PAR_OUT × K_MAX × K_MAX
    ap_int<8> wbuf[PAR_OUT * K_MAX * K_MAX];
    #pragma HLS ARRAY_PARTITION variable=wbuf complete dim=0

    ap_uint<8> lb2[TILE_H][K_MAX][LB_W_MAX];
    #pragma HLS ARRAY_PARTITION variable=lb2 complete dim=0
    ap_int<8> wbuf2[PAR_OUT * K_MAX * K_MAX];
    #pragma HLS ARRAY_PARTITION variable=wbuf2 complete dim=0

    // 累加器必须落寄存器（落 BRAM 会被读-改-写串行化）
    ap_int<32> acc[PAR_OUT][TILE_H][TILE_W];
#if ACC_FULL
    #pragma HLS ARRAY_PARTITION variable=acc complete dim=0
#else
    #pragma HLS ARRAY_PARTITION variable=acc complete dim=1
#endif

    ap_int<32> pm_bias[PAR_OUT];
    ap_int<32> pm_mult[PAR_OUT];
    ap_int<8>  pm_shift[PAR_OUT];
    #pragma HLS ARRAY_PARTITION variable=pm_bias  complete dim=1
    #pragma HLS ARRAY_PARTITION variable=pm_mult  complete dim=1
    #pragma HLS ARRAY_PARTITION variable=pm_shift complete dim=1

    ap_uint<8> lutc[256];
    #pragma HLS ARRAY_PARTITION variable=lutc complete dim=1
    for (int i = 0; i < 256; ++i) {
        #pragma HLS PIPELINE II=1
        lutc[i] = lut[i];
    }

    for (int co0 = 0; co0 < C_OUT; co0 += PAR_OUT) {
        #pragma HLS LOOP_TRIPCOUNT min=1 max=32

        // ① requant 参数：尾块用钳位下标读，读后按有效性清零（不做条件读）
        for (int p = 0; p < PAR_OUT; ++p) {
            #pragma HLS UNROLL
            int co = co0 + p;
            bool vld = (co < C_OUT);
            int idx = (vld ? co : (C_OUT - 1)) * 3;
            pm_bias[p]  = vld ? params[idx + 0] : ap_int<32>(0);
            pm_mult[p]  = vld ? params[idx + 1] : ap_int<32>(0);
            pm_shift[p] = vld ? ap_int<8>(params[idx + 2]) : ap_int<8>(0);
        }

        for (int t0 = 0; t0 < H_OUT; t0 += TILE_H) {
            #pragma HLS LOOP_TRIPCOUNT min=1 max=64

            for (int w0 = 0; w0 < W_OUT; w0 += TILE_W) {
                #pragma HLS LOOP_TRIPCOUNT min=1 max=32

                for (int hl = 0; hl < TILE_H; ++hl) {
                    #pragma HLS UNROLL
                    for (int wo = 0; wo < TILE_W; ++wo) {
                        #pragma HLS UNROLL
                        for (int p = 0; p < PAR_OUT; ++p) {
                            #pragma HLS UNROLL
                            acc[p][hl][wo] = pm_bias[p];
                        }
                    }
                }

                int wo_valid = W_OUT - w0;
                if (wo_valid > TILE_W) wo_valid = TILE_W;
                int ncol = (wo_valid - 1) * stride + K;
                if (ncol > LB_W_MAX) ncol = LB_W_MAX;

                // ci 两两成组：先把 ci 与 ci+1 的读事务全部发出，再依次计算。
                // 每两次 ci 只暴露一次 DDR 往返，寄存器代价约 636 B。
                for (int c2 = 0; c2 < C_IN; c2 += 2) {
                    #pragma HLS LOOP_TRIPCOUNT min=2 max=256
                    ce_stage_in(in, c2, H_IN, W_IN, t0, w0,
                                K, stride, pad, zp_in, ncol, lb);
                    ce_stage_w(w, c2, co0, C_IN, C_OUT, K, wbuf);
                    if (c2 + 1 < C_IN) {
                        ce_stage_in(in, c2 + 1, H_IN, W_IN, t0, w0,
                                    K, stride, pad, zp_in, ncol, lb2);
                        ce_stage_w(w, c2 + 1, co0, C_IN, C_OUT, K, wbuf2);
                    }
                    ce_stage_conv(lb, wbuf, acc, K, stride);
                    if (c2 + 1 < C_IN) {
                        ce_stage_conv(lb2, wbuf2, acc, K, stride);
                    }
                }

                // ---- c requant → LUT → 写回 ----
                for (int p = 0; p < PAR_OUT; ++p) {
                    #pragma HLS LOOP_TRIPCOUNT min=1 max=32
                    if (co0 + p < C_OUT) {
                        for (int hl = 0; hl < TILE_H; ++hl) {
                            #pragma HLS UNROLL
                            int ho = t0 + hl;
                            if (ho < H_OUT) {
                                for (int wo = 0; wo < TILE_W; ++wo) {
                                    #pragma HLS PIPELINE II=1
                                    int wo_abs = w0 + wo;
                                    if (wo_abs < W_OUT) {
                                        ap_int<48> a48 = ap_int<48>(acc[p][hl][wo]);
                                        ap_int<48> b48 = ap_int<48>(pm_mult[p]);
                                        ap_int<64> prod = ap_int<64>(a48 * b48);
                                        ap_int<64> half = (pm_shift[p] > 0)
                                                        ? (ap_int<64>(1) << (pm_shift[p] - 1))
                                                        : ap_int<64>(0);
                                        ap_int<64> shifted = (prod + half) >> pm_shift[p];
                                        ap_int<32> v = ap_int<32>(shifted) + zp_pre;
                                        if (v < 0)   v = 0;
                                        if (v > 255) v = 255;
                                        out[((co0 + p) * H_OUT + ho) * W_OUT + wo_abs] =
                                            lutc[ap_uint<8>(v)];
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
