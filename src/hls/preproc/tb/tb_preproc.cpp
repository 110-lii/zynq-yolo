// 主机对拍：raw BGR -> preproc -> 3 个 RGB 平面
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "preproc.cpp"

int main(int argc, char **argv) {
    if (argc < 5) { printf("USAGE: tb in.raw out.raw W H\n"); return 2; }
    int W = atoi(argv[3]), H = atoi(argv[4]);
    size_t n_in = (size_t)W * H * 3;
    size_t n_out = (size_t)OUT_W * OUT_H * 3;
    unsigned char *in = (unsigned char *)malloc(n_in + 64);
    unsigned char *out = (unsigned char *)malloc(n_out + 64);
    FILE *f = fopen(argv[1], "rb");
    if (!f || fread(in, 1, n_in, f) != n_in) { printf("IN_FAIL\n"); return 1; }
    fclose(f);
    memset(out, 0, n_out);

    preproc((const ap_uint<8> *)in, (ap_uint<8> *)out, W, H);

    f = fopen(argv[2], "wb");
    fwrite(out, 1, n_out, f);
    fclose(f);
    printf("TB_OK %zu\n", n_out);
    return 0;
}