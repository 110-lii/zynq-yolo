// ncnn_infer.cpp —— 板上 YOLOv8 单帧推理 + 解码 + NMS（无 OpenCV）
//
// 输入：raw RGB 320x320x3（uint8），由 Python 侧生成
// 输出：每行 "DET x1 y1 x2 y2 score"（输入像素坐标）
//       最后一行 "INFER_MS <ms>"
//
// 编译：g++ -O2 -mcpu=cortex-a9 -mfpu=neon -mfloat-abi=hard -fopenmp \
//        -I ncnn-20240820/src -I ncnn-20240820/build/src ncnn_infer.cpp \
//        ncnn-20240820/build/src/libncnn.a -lpthread -o ncnn_infer
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include <algorithm>
#include <vector>

#include "net.h"

struct Box { float x1, y1, x2, y2, s; };

static double now_s() {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec * 1e-9;
}

static float iou(const Box &a, const Box &b) {
    float ix1 = std::max(a.x1, b.x1), iy1 = std::max(a.y1, b.y1);
    float ix2 = std::min(a.x2, b.x2), iy2 = std::min(a.y2, b.y2);
    float iw = std::max(0.f, ix2 - ix1), ih = std::max(0.f, iy2 - iy1);
    float inter = iw * ih;
    float ua = (a.x2 - a.x1) * (a.y2 - a.y1) + (b.x2 - b.x1) * (b.y2 - b.y1) - inter;
    return ua > 0 ? inter / ua : 0.f;
}

int main(int argc, char **argv) {
    if (argc < 4) {
        printf("USAGE: ncnn_infer model.param model.bin input.rgb [conf] [iou]\n");
        return 2;
    }
    const float conf_th = argc > 4 ? atof(argv[4]) : 0.25f;
    const float iou_th = argc > 5 ? atof(argv[5]) : 0.45f;
    const int planar = argc > 6 ? atoi(argv[6]) : 0;   // 1 = 3xHxW 平面（PL 预处理输出）

    const int W = 320, H = 320;
    std::vector<unsigned char> buf((size_t)W * H * 3);
    FILE *f = fopen(argv[3], "rb");
    if (!f || fread(buf.data(), 1, buf.size(), f) != buf.size()) {
        printf("INPUT_FAIL\n");
        return 1;
    }
    fclose(f);

    ncnn::Net net;
    net.opt.num_threads = 2;
    net.opt.use_vulkan_compute = false;
    if (net.load_param(argv[1]) != 0 || net.load_model(argv[2]) != 0) {
        printf("LOAD_FAIL\n");
        return 1;
    }

    ncnn::Mat in(W, H, 3);
    if (planar) {
        for (int c = 0; c < 3; ++c)
            for (int y = 0; y < H; ++y) {
                float *p = in.channel(c).row(y);
                const unsigned char *src = buf.data() + ((size_t)c * H + y) * W;
                for (int x = 0; x < W; ++x) p[x] = src[x] / 255.f;
            }
    } else {
        for (int y = 0; y < H; ++y) {
            float *py = in.channel(0).row(y);
            float *pu = in.channel(1).row(y);
            float *pv = in.channel(2).row(y);
            const unsigned char *src = buf.data() + (size_t)y * W * 3;
            for (int x = 0; x < W; ++x) {
                py[x] = src[x * 3 + 0] / 255.f;
                pu[x] = src[x * 3 + 1] / 255.f;
                pv[x] = src[x * 3 + 2] / 255.f;
            }
        }
    }

    double t0 = now_s();
    ncnn::Extractor ex = net.create_extractor();
    ex.input("images", in);
    ncnn::Mat out;
    int rc = ex.extract("output0", out);
    double t1 = now_s();
    if (rc != 0 || out.w < 5) {
        printf("EXTRACT_FAIL %d %d\n", rc, out.w);
        return 1;
    }

    printf("OUT_SHAPE w=%d h=%d c=%d\n", out.w, out.h, out.c);
    const bool anchors_along_w = out.w > out.h;
    const int n = anchors_along_w ? out.w : out.h;
    auto val = [&](int row, int i) -> float {
        return anchors_along_w ? out.row(row)[i] : out.row(i)[row];
    };
    std::vector<Box> boxes;
    for (int i = 0; i < n; ++i) {
        float s = val(4, i);
        if (s < conf_th) continue;
        float cx = val(0, i), cy = val(1, i);
        float w = val(2, i), h = val(3, i);
        boxes.push_back({cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2, s});
    }
    std::sort(boxes.begin(), boxes.end(), [](const Box &a, const Box &b) { return a.s > b.s; });
    std::vector<Box> keep;
    for (const auto &b : boxes) {
        bool ok = true;
        for (const auto &k : keep) if (iou(b, k) > iou_th) { ok = false; break; }
        if (ok) keep.push_back(b);
    }
    for (const auto &b : keep) {
        printf("DET %.1f %.1f %.1f %.1f %.3f\n", b.x1, b.y1, b.x2, b.y2, b.s);
    }
    printf("INFER_MS %.1f\n", (t1 - t0) * 1e3);
    return 0;
}