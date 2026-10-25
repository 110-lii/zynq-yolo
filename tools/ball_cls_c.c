// ball_cls_c.c —— 足球 ROI 分类器（纯 C，BN 已折叠）
// 输入：crops.raw = N × 32×32×3，uint8，RGB 交织
// 输出：每行 "SCORE <i> <prob>"
// 编译：gcc -O3 -mcpu=cortex-a9 -mfpu=neon -mfloat-abi=hard ball_cls_c.c -lm -o ball_cls_c
#include <math.h>
#include <stdio.h>
#include <stdlib.h>

#include "ball_weights.h"

#define SZ 32

static float buf0[3 * SZ * SZ], buf1[16 * SZ * SZ];
static float t0f[16 * 32 * 32], t1f[32 * 16 * 16], t2f[64 * 8 * 8];

static void conv3x3(const float *in, int cin, int h, int w,
                    const float *wt, const float *bs, int cout, float *out) {
    for (int oc = 0; oc < cout; ++oc) {
        for (int y = 0; y < h; ++y)
            for (int x = 0; x < w; ++x) {
                float acc = bs[oc];
                for (int ic = 0; ic < cin; ++ic) {
                    const float *p = in + ic * h * w;
                    const float *k = wt + ((oc * cin + ic) * 9);
                    for (int ky = -1; ky <= 1; ++ky) {
                        int yy = y + ky;
                        if (yy < 0 || yy >= h) continue;
                        for (int kx = -1; kx <= 1; ++kx) {
                            int xx = x + kx;
                            if (xx < 0 || xx >= w) continue;
                            acc += p[yy * w + xx] * k[(ky + 1) * 3 + (kx + 1)];
                        }
                    }
                }
                out[oc * h * w + y * w + x] = acc > 0.f ? acc : 0.f;   // ReLU
            }
    }
}

static void maxpool2(const float *in, int c, int h, int w, float *out) {
    int oh = h / 2, ow = w / 2;
    for (int i = 0; i < c; ++i)
        for (int y = 0; y < oh; ++y)
            for (int x = 0; x < ow; ++x) {
                float m = -1e30f;
                for (int dy = 0; dy < 2; ++dy)
                    for (int dx = 0; dx < 2; ++dx) {
                        float v = in[i * h * w + (y * 2 + dy) * w + (x * 2 + dx)];
                        if (v > m) m = v;
                    }
                out[i * oh * ow + y * ow + x] = m;
            }
}

int main(int argc, char **argv) {
    if (argc < 3) { printf("USAGE: ball_cls_c crops.raw N\n"); return 2; }
    int N = atoi(argv[2]);
    FILE *f = fopen(argv[1], "rb");
    if (!f) { printf("INPUT_FAIL\n"); return 1; }
    FILE *df = (argc > 3) ? fopen(argv[3], "wb") : NULL;
    FILE *dfd = (argc > 4) ? fopen(argv[4], "wb") : NULL;
    FILE *dfp = (argc > 5) ? fopen(argv[5], "wb") : NULL;
    for (int i = 0; i < N; ++i) {
        unsigned char raw[SZ * SZ * 3];
        if (fread(raw, 1, sizeof(raw), f) != sizeof(raw)) { printf("INPUT_FAIL\n"); return 1; }
        for (int y = 0; y < SZ; ++y)
            for (int x = 0; x < SZ; ++x)
                for (int c = 0; c < 3; ++c)
                    buf0[c * SZ * SZ + y * SZ + x] = raw[(y * SZ + x) * 3 + c] / 255.f;

        conv3x3(buf0, 3, 32, 32, W1, B1, 16, t0f);
        if (df && i == 0) fwrite(t0f, sizeof(float), 16 * 32 * 32, df);
        maxpool2(t0f, 16, 32, 32, buf1);            // 16x16x16
        if (dfp && i == 0) fwrite(buf1, sizeof(float), 16 * 16 * 16, dfp);
        conv3x3(buf1, 16, 16, 16, W2, B2, 32, t1f);
        if (dfd && i == 0) fwrite(t1f, sizeof(float), 32 * 16 * 16, dfd);
        maxpool2(t1f, 32, 16, 16, buf1);            // 32x8x8
        conv3x3(buf1, 32, 8, 8, W3, B3, 64, t2f);   // 64x8x8
        float v = BH[0];
        for (int c = 0; c < 64; ++c) {
            float s = 0.f;
            for (int k = 0; k < 64; ++k) s += t2f[c * 64 + k];
            v += (s / 64.f) * WH[c];
        }
        printf("SCORE %d %.4f\n", i, 1.0 / (1.0 + exp(-v)));
    }
    fclose(f);
    if (df) fclose(df);
    if (dfd) fclose(dfd);
    if (dfp) fclose(dfp);
    return 0;
}