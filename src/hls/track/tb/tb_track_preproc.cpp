// 主机对拍：cur/prev BGR -> track_preproc -> RGB 平面 + 掩码
// 用法: tb_track_preproc cur.raw prev.raw rgb.raw mask.raw W H
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "track_preproc.cpp"

int main(int argc, char **argv) {
    if (argc < 7) {
        printf("USAGE: tb_track_preproc cur.raw prev.raw rgb.raw mask.raw W H\n");
        return 2;
    }
    int W = atoi(argv[5]), H = atoi(argv[6]);
    size_t n_in = (size_t)W * H * 3;
    size_t n_rgb = (size_t)OUT_W * OUT_H * 3;
    size_t n_mask = (size_t)OUT_W * OUT_H;

    unsigned char *cur = (unsigned char *)malloc(n_in + 64);
    unsigned char *prev = (unsigned char *)malloc(n_in + 64);
    unsigned char *rgb = (unsigned char *)malloc(n_rgb + 64);
    unsigned char *mask = (unsigned char *)malloc(n_mask + 64);
    if (!cur || !prev || !rgb || !mask) { printf("ALLOC_FAIL\n"); return 1; }

    FILE *f = fopen(argv[1], "rb");
    if (!f || fread(cur, 1, n_in, f) != n_in) { printf("CUR_FAIL\n"); return 1; }
    fclose(f);
    f = fopen(argv[2], "rb");
    if (!f || fread(prev, 1, n_in, f) != n_in) { printf("PREV_FAIL\n"); return 1; }
    fclose(f);
    memset(rgb, 0, n_rgb);
    memset(mask, 0, n_mask);

    track_preproc((const ap_uint<8> *)cur, (const ap_uint<8> *)prev,
                  (ap_uint<8> *)rgb, (ap_uint<8> *)mask,
                  W, H, 150, 40, 12);

    f = fopen(argv[3], "wb");
    fwrite(rgb, 1, n_rgb, f);
    fclose(f);
    f = fopen(argv[4], "wb");
    fwrite(mask, 1, n_mask, f);
    fclose(f);
    printf("TRACK_TB_OK rgb=%zu mask=%zu\n", n_rgb, n_mask);
    return 0;
}
