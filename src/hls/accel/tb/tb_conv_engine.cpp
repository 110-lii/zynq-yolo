// ============================================================================
// tb_conv_engine.cpp —— conv_engine 的 C 仿真测试台（Vitis HLS C simulation）
//
// 作用：把硬件实现和 tools/ref_int8_conv.py 的 numpy 参考实现**逐位比对**。
//       契约见 docs/accel_interface.md；本 TB 只负责"喂数据 + 比结果"，
//       不自己算期望值（否则 TB 和 RTL 一起错，比对就没有判别力）。
//
// 用例目录格式（由 sim/scripts/make_hls_case.py 生成）：
//   <case>/meta.txt      key=value 行，见下
//   <case>/in.bin        uint8   [C_IN ][H_IN ][W_IN ]
//   <case>/w.bin         int8    [C_OUT][C_IN ][K][K]
//   <case>/params.bin    int32   [C_OUT][3]  = {bias_i32, M, S}
//   <case>/lut.bin       uint8   [256]
//   <case>/out_ref.bin   uint8   [C_OUT][H_OUT][W_OUT]   ← numpy 参考实现的输出
//
// meta.txt 必含键：C_IN C_OUT H_IN W_IN K stride pad zp_in zp_pre H_OUT W_OUT
//   （且必须含 case 名 name=，失败信息里会打出来，便于定位是哪种层形状坏了）
//
// 判据：末行 `TB_CONV_OK <case> <checked>` 或 `TB_CONV_FAIL <case> <first_bad_detail>`
//       逐位相等才算过 —— 量化链路没有"差不多对"，差 1 就是 requant/折叠写错了。
//
// 送参约定：用例目录由**环境变量 TB_CASE_DIR** 传入（不是宏）。
//   为什么不用 -DCASE_DIR="..."：Windows 路径里全是反斜杠，要塞进 Tcl 的 -cflags
//   字符串再传给编译器，嵌套转义极易出错，且路径一变就要重综合/重编译。
//   环境变量在 csim 进程里 getenv 取，干净且可复跑。
// ============================================================================
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <map>

#include <ap_int.h>

// 只声明原型，不 #include conv_engine.cpp：
//   该 .cpp 已作为 design file 加入 HLS 工程，TB 再 include 一次会在 csim 链接时
//   出现重复定义。csim 阶段接口 pragma 不参与语义，原型足够。
void conv_engine(const ap_uint<32> *in,
                 const ap_int<32>  *w,
                 const ap_int<32> *params,
                 const ap_uint<8> *lut,
                 ap_uint<8>       *out,
                 int C_IN, int C_OUT,
                 int H_IN, int W_IN,
                 int K, int stride, int pad,
                 int zp_in, int zp_pre,
                 int H_OUT, int W_OUT);

// 编译期上界必须与 conv_engine.cpp 保持一致（改了那边要同步改这里）
#define K_MAX 3
#define STRIDE_MAX 2

static std::map<std::string, long> g_meta;

static bool read_meta(const char *path) {
    FILE *f = fopen(path, "r");
    if (!f) return false;
    char line[512];
    while (fgets(line, sizeof(line), f)) {
        char *eq = strchr(line, '=');
        if (!eq) continue;
        *eq = 0;
        std::string k(line);
        // 去尾部空白
        while (!k.empty() && (k.back() == ' ' || k.back() == '\t' || k.back() == '\r'))
            k.pop_back();
        g_meta[k] = atol(eq + 1);
    }
    fclose(f);
    return true;
}

static long meta(const char *k, long dflt = -1) {
    std::map<std::string, long>::iterator it = g_meta.find(k);
    return (it == g_meta.end()) ? dflt : it->second;
}

template <typename T>
static bool read_bin(const char *path, T *dst, size_t n) {
    FILE *f = fopen(path, "rb");
    if (!f) { printf("TB_OPEN_FAIL %s\n", path); return false; }
    size_t got = fread(dst, sizeof(T), n, f);
    fclose(f);
    if (got != n) {
        printf("TB_SHORT %s want=%zu got=%zu\n", path, n, got);
        return false;
    }
    return true;
}

int main() {
    const char *envdir = getenv("TB_CASE_DIR");
    if (envdir == NULL) {
        printf("TB_CONV_FAIL noenv : 需要环境变量 TB_CASE_DIR 指向用例目录\n");
        return 1;
    }
    const std::string dir = envdir;
    const std::string metap = dir + "/meta.txt";
    if (!read_meta(metap.c_str())) { printf("TB_CONV_FAIL meta : %s 打不开\n", metap.c_str()); return 1; }

    const int C_IN  = (int)meta("C_IN");
    const int C_OUT = (int)meta("C_OUT");
    const int H_IN  = (int)meta("H_IN");
    const int W_IN  = (int)meta("W_IN");
    const int K     = (int)meta("K");
    const int stride= (int)meta("stride");
    const int pad   = (int)meta("pad");
    const int zp_in = (int)meta("zp_in");
    const int zp_pre= (int)meta("zp_pre");
    const int H_OUT = (int)meta("H_OUT");
    const int W_OUT = (int)meta("W_OUT");

    printf("TB case=%s C_IN=%d C_OUT=%d H_IN=%d W_IN=%d K=%d stride=%d pad=%d "
           "zp_in=%d zp_pre=%d H_OUT=%d W_OUT=%d\n",
           dir.c_str(), C_IN, C_OUT, H_IN, W_IN, K, stride, pad, zp_in, zp_pre, H_OUT, W_OUT);

    // ---- 编译期上界检查：运行期几何不得超过内核数组尺寸 ----
    // （内核用 LB_R_MAX/LB_W_MAX 固定数组；超了会静默越界，必须在 TB 里挡住）
    if (K > K_MAX || stride > STRIDE_MAX) {
        printf("TB_CONV_FAIL %s : K=%d stride=%d 超过编译期上界 K_MAX=%d STRIDE_MAX=%d\n",
               dir.c_str(), K, stride, K_MAX, STRIDE_MAX);
        return 1;
    }

    const size_t n_in  = (size_t)C_IN  * H_IN  * W_IN;
    const size_t n_w   = (size_t)C_OUT * C_IN  * K * K;
    const size_t n_pr  = (size_t)C_OUT * 3;
    const size_t n_out = (size_t)C_OUT * H_OUT * W_OUT;

    ap_uint<8>  *in  = (ap_uint<8> *) malloc(n_in  + 64);
    ap_int<8>   *w   = (ap_int<8>  *) malloc(n_w   + 64);
    ap_int<32>  *pm  = (ap_int<32> *) malloc((n_pr + 16) * sizeof(ap_int<32>));
    ap_uint<8>  *lut = (ap_uint<8> *) malloc(256 + 64);
    ap_uint<8>  *out = (ap_uint<8> *) malloc(n_out + 64);
    ap_uint<8>  *ref = (ap_uint<8> *) malloc(n_out + 64);

    bool ok = true;
    ok &= read_bin((dir + "/in.bin").c_str(),  in,  n_in);
    ok &= read_bin((dir + "/w.bin").c_str(),   w,   n_w);
    ok &= read_bin((dir + "/params.bin").c_str(), pm, n_pr);
    ok &= read_bin((dir + "/lut.bin").c_str(), lut, 256);
    ok &= read_bin((dir + "/out_ref.bin").c_str(), ref, n_out);
    if (!ok) { printf("TB_CONV_FAIL %s : 输入文件读不全\n", dir.c_str()); return 1; }

    // ---- 权重重排：外壳按 (co,ci) 记录补齐到 4/12 字节 ----
    // 板端由 tools/make_board_stream_pack.py 做同样的补齐；TB 里现做，保证
    // 用例目录仍保存 ONNX 原始布局 [C_OUT][C_IN][K][K]。
    const int kk = K * K;
    const int rec = (kk <= 4) ? 4 : 12;
    const size_t n_rec = (size_t)C_OUT * C_IN;
    ap_int<8> *wp = (ap_int<8> *) malloc(n_rec * rec + 64);
    memset(wp, 0, n_rec * rec);
    for (size_t r = 0; r < n_rec; ++r) {
        memcpy(wp + r * rec, w + r * kk, (size_t)kk);
    }

    // ---- 跑内核 ----
    // 内核按 32-bit 字访问 in/w（位宽由指针类型决定，见 conv_engine.cpp 头注释）。
    // 缓冲区起点必须 4 字节对齐：malloc 在 csim 主机上按 max_align_t 对齐，满足。
    conv_engine((const ap_uint<32> *)in, (const ap_int<32> *)wp, pm, lut, out,
                C_IN, C_OUT, H_IN, W_IN,
                K, stride, pad, zp_in, zp_pre, H_OUT, W_OUT);

    // ---- 逐位比对 ----
    size_t bad = 0, first = (size_t)-1;
    for (size_t i = 0; i < n_out; ++i) {
        if ((unsigned)out[i] != (unsigned)ref[i]) {
            if (first == (size_t)-1) first = i;
            ++bad;
        }
    }
    if (bad == 0) {
        printf("TB_CONV_OK %s %zu\n", dir.c_str(), n_out);
        return 0;
    }
    // 首个不符的定位信息：反解出 c/h/w，便于判断是布局还是 requant 错
    size_t c = first / ((size_t)H_OUT * W_OUT);
    size_t rem = first % ((size_t)H_OUT * W_OUT);
    size_t h = rem / W_OUT, ww = rem % W_OUT;
    printf("TB_CONV_FAIL %s : bad=%zu/%zu first=idx%zu (c=%zu,h=%zu,w=%zu) "
           "got=%u ref=%u\n",
           dir.c_str(), bad, n_out, first, c, h, ww,
           (unsigned)out[first], (unsigned)ref[first]);
    return 1;
}
