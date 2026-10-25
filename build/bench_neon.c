// bench_neon.c —— Cortex-A9 NEON INT8 3x3 卷积吞吐微基准
//
// 布局：输入 HWC（C 连续），权重 [co][kh][kw][ci]（ci 连续），
// 用 vmlal_s8 一次算 8 个输入通道。测的是"纯乘加吞吐"，不含 im2col/后处理。
//
// 编译（板上）：gcc -O3 -mcpu=cortex-a9 -mfpu=neon -mfloat-abi=hard bench_neon.c -o bench_neon -lpthread
#include <arm_neon.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static double now_s(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec * 1e-9;
}

int main(int argc, char **argv) {
    const int C_IN = 64, C_OUT = 64, H = 40, W = 40, K = 3;
    const int iters = argc > 1 ? atoi(argv[1]) : 10;

    signed char *in = (signed char *)aligned_alloc(16, (size_t)H * W * C_IN);
    signed char *w = (signed char *)aligned_alloc(16, (size_t)C_OUT * K * K * C_IN);
    int *out = (int *)aligned_alloc(16, (size_t)C_OUT * H * W * sizeof(int));
    for (size_t i = 0; i < (size_t)H * W * C_IN; ++i) in[i] = (signed char)(i * 7);
    for (size_t i = 0; i < (size_t)C_OUT * K * K * C_IN; ++i) w[i] = (signed char)(i * 13);

    double best = 1e9;
    for (int it = 0; it < iters; ++it) {
        double t0 = now_s();
        for (int co = 0; co < C_OUT; ++co) {
            const signed char *wc = w + (size_t)co * K * K * C_IN;
            for (int y = 1; y < H - 1; ++y) {
                for (int x = 1; x < W - 1; ++x) {
                    int16x8_t acc = vdupq_n_s16(0);
                    for (int kh = 0; kh < K; ++kh) {
                        for (int kw = 0; kw < K; ++kw) {
                            const signed char *wp = wc + ((kh * K + kw) * C_IN);
                            const signed char *ip = in + ((y + kh - 1) * W + (x + kw - 1)) * C_IN;
                            for (int ci = 0; ci < C_IN; ci += 8) {
                                acc = vmlal_s8(acc, vld1_s8(ip + ci), vld1_s8(wp + ci));
                            }
                        }
                    }
                    int32x4_t lo = vmovl_s16(vget_low_s16(acc));
                    int32x4_t hi = vmovl_s16(vget_high_s16(acc));
                    out[((size_t)co * H + y) * W + x] = vgetq_lane_s32(lo, 0)
                                                      + vgetq_lane_s32(lo, 1)
                                                      + vgetq_lane_s32(hi, 0)
                                                      + vgetq_lane_s32(hi, 1);
                }
            }
        }
        double dt = now_s() - t0;
        if (dt < best) best = dt;
    }

    double macs = (double)C_OUT * (H - 2) * (W - 2) * K * K * C_IN;
    printf("NEON_BENCH C_IN=%d C_OUT=%d H=%d W=%d K=%d\n", C_IN, C_OUT, H, W, K);
    printf("NEON_BENCH macs=%.3fM best_s=%.6f GMAC_s=%.3f GFLOPs=%.3f\n",
           macs / 1e6, best, macs / best / 1e9, 2.0 * macs / best / 1e9);
    printf("NEON_BENCH checksum=%d\n", out[0] + out[1234]);
    return 0;
}