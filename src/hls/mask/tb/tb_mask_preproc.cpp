// tb_mask_preproc.cpp —— 逐位比对：PL 核 vs 同一公式的 C 参考
//
// 覆盖：W*H 为 4 的倍数（36x24）、极亮/极暗、低饱和/高饱和、静止/运动。
// 判据：MASK_TB_OK <n> / MASK_TB_FAIL <mismatch>
#include <cstdio>

#include <ap_int.h>

extern "C" void mask_preproc(const ap_uint<32> *cur, const ap_uint<32> *prev,
                             ap_uint<32> *out, int W, int H,
                             int white_thr, int sat_thr, int diff_thr);

static int ref_mask(int b, int g, int r, int pg, int white_thr, int sat_thr, int diff_thr) {
    int mx = b > g ? (b > r ? b : r) : (g > r ? g : r);
    int mn = b < g ? (b < r ? b : r) : (g < r ? g : r);
    int m = 0;
    if (mx > white_thr && (mx - mn) < sat_thr) m |= 1;
    int d = g - pg;
    if (d < 0) d = -d;
    if (d > diff_thr) m |= 2;
    return m;
}

int main() {
    const int W = 36, H = 24;
    const int n = W * H;
    ap_uint<8> *cur = new ap_uint<8>[n * 3];
    ap_uint<8> *prev = new ap_uint<8>[n * 3];
    ap_uint<8> *out = new ap_uint<8>[n];
    ap_uint<8> *ref = new ap_uint<8>[n];
    const int white_thr = 150, sat_thr = 40, diff_thr = 12;
    unsigned seed = 12345u;
    for (int i = 0; i < n; ++i) {
        for (int c = 0; c < 3; ++c) {
            seed = seed * 1664525u + 1013904223u;
            cur[i * 3 + c] = (ap_uint<8>)((seed >> 16) & 0xFF);
        }
        if ((i % 7) == 0) { cur[i * 3 + 0] = 230; cur[i * 3 + 1] = 235; cur[i * 3 + 2] = 228; }
        if ((i % 11) == 0) { cur[i * 3 + 0] = 40; cur[i * 3 + 1] = 42; cur[i * 3 + 2] = 41; }
        int pg = (int)cur[i * 3 + 1] + ((i % 5) - 2) * 9;
        if (pg < 0) pg = 0;
        if (pg > 255) pg = 255;
        prev[i * 3 + 0] = 0;
        prev[i * 3 + 1] = (ap_uint<8>)pg;
        prev[i * 3 + 2] = 0;
        ref[i] = (ap_uint<8>)ref_mask(cur[i * 3 + 0], cur[i * 3 + 1], cur[i * 3 + 2],
                                      pg, white_thr, sat_thr, diff_thr);
    }

    mask_preproc((const ap_uint<32> *)cur, (const ap_uint<32> *)prev,
                 (ap_uint<32> *)out, W, H, white_thr, sat_thr, diff_thr);

    int bad = 0;
    for (int i = 0; i < n; ++i) {
        if (out[i] != ref[i]) {
            if (bad < 5) printf("MISMATCH i=%d got=%d ref=%d\n", i, (int)out[i], (int)ref[i]);
            bad++;
        }
    }
    if (bad == 0) printf("MASK_TB_OK %d\n", n);
    else printf("MASK_TB_FAIL %d\n", bad);
    delete[] cur;
    delete[] prev;
    delete[] out;
    delete[] ref;
    return bad == 0 ? 0 : 1;
}