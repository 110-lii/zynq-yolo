// bench_ncnn.cpp —— 板上 ncnn 整网推理基准（只测速度，不做后处理）
// 编译：g++ -O2 -mcpu=cortex-a9 -mfpu=neon -mfloat-abi=hard -fopenmp
//        -I ncnn-20240820/src -I ncnn-20240820/build/src bench_ncnn.cpp
//        ncnn-20240820/build/src/libncnn.a -lpthread -o bench_ncnn
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

#include "net.h"

static double now_s() {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec * 1e-9;
}

int main(int argc, char **argv) {
    if (argc < 3) {
        printf("USAGE: bench_ncnn model.param model.bin [threads] [runs]\n");
        return 2;
    }
    const int threads = argc > 3 ? atoi(argv[3]) : 1;
    const int runs = argc > 4 ? atoi(argv[4]) : 5;

    ncnn::Net net;
    net.opt.num_threads = threads;
    net.opt.use_vulkan_compute = false;
    if (net.load_param(argv[1]) != 0) { printf("PARAM_FAIL\n"); return 1; }
    if (net.load_model(argv[2]) != 0) { printf("MODEL_FAIL\n"); return 1; }

    ncnn::Mat in(320, 320, 3);
    in.fill(0.f);

    double best = 1e9;
    for (int r = 0; r < runs + 2; ++r) {
        double t0 = now_s();
        ncnn::Extractor ex = net.create_extractor();
        ex.input("images", in);
        ncnn::Mat out;
        int rc = ex.extract("output0", out);
        double dt = now_s() - t0;
        if (r == 0 && rc != 0) { printf("EXTRACT_FAIL %d\n", rc); return 1; }
        if (r >= 2 && dt < best) best = dt;
    }
    printf("NCNN_BENCH threads=%d best_s=%.4f fps=%.2f\n", threads, best, 1.0 / best);
    return 0;
}