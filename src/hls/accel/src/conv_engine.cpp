// ============================================================================
// conv_engine.cpp —— 单路 YOLOv8n 的通用 INT8 卷积引擎（HLS）
//
// 契约：docs/accel_interface.md（§1 公式、§2 布局、§3 定点口径、§4 签名、§5 层范围）
// ★ 本文件所有数值口径必须与契约逐字一致；改这里必须同时改 tools/ref_int8_conv.py
//   并重跑 tb_conv_engine.cpp 的逐位比对。
//
// ============================ 与探针 acc_conv3x3.cpp 的区别 ============================
// 探针（v11）是**编译期固定几何**的性能标定件：C_IN/C_OUT/H/W 全是 #define，输入整层进
// icache。它能回答"这个结构能跑多快"，但**不能用来跑真实网络**：
//   ① 真实层形状有 64 种，逐层综合不可接受 ⇒ 几何必须是**运行期寄存器**
//   ② 真实层的最宽 W=320，早期层 16×160×160 = 409 600 B，而 BRAM 总共 ~612 500 B
//      ⇒ **整层 icache 装不下**（一层吃掉 67%），必须改成行缓冲流式
//   ③ 真实网络要 per-channel requant（Q31 乘子 + 变量右移）与 SiLU，探针只有单一 scale_shift
//
// ============================ 结构 ============================
//   for co0（输出通道块，步长 PAR_OUT）
//     ① 读本块 PAR_OUT 个通道的 params（bias_i32 / M / S）
//     ② for t0（输出行块，步长 TILE_H）
//        for w0（输出列块，步长 TILE_W）
//           a. acc[PAR][TILE_H][TILE_W] = 0
//           b. for ci（全部输入通道）
//                把 TILE_H×K 行的输入窗口读进行缓冲 lb（**越界填 zp_in，不是 0**）
//                把本 ci 的权重读进 wbuf[PAR][K][K]
//                卷积累加进 acc（寄存器）
//           c. requant（64 位乘积 + 变量右移）→ LUT → 写回 DDR
//
// ============================ 为什么行缓冲是 "TILE_H × K 行" 而不是 "最少的几行" ============================
// 这是本文件最容易改错的一处，先说结论：**故意多读 (K-stride) 倍的行数，换索引静态化。**
//
//   最省流量的做法是只存 TILE_H*stride+K-1 行（stride=1/K=3/TILE_H=4 时 6 行），
//   但那时计算要写 `lb[hl*stride+kh][...]`，而 **stride 是运行期参数** ⇒ 行下标是
//   运行期值 ⇒ ARRAY_PARTITION 分不掉、只能落 BRAM ⇒ 每个像素要 9 次 BRAM 读，
//   II 必然是 3 以上。
//   改成 `lb[hl][kh][...]`（TILE_H×K 行，12 行）之后 hl、kh 都是编译期常量，两个维度
//   可以 complete 分区，每拍的 9 个抽头是 9 个寄存器的直接连线，II 才能做到 1。
//
//   代价是加载量从 1 字节/(像素·ci) 涨到 K 字节/(像素·ci)——**但这正是探针 v11 的实测
//   工况**（v4→v11 的填充循环一直是每个输出行读 K 行，实测 3 字节/像素/ci，PAR=8 时
//   4 拍/像素 = 4.74 GOPS）。所以这里不是退步，而是与已验证的性能基线对齐。
//
// ============================ 已验证的教训（改代码前先读） ============================
//   * 累加器必须在**寄存器**里。v10 曾把它放进 BRAM（cyclic 分区），被读-改-写 +
//     Bank 冲突串行化，实测慢 2.8×、内层 II 从 1 退化到 8。
//   * 回写循环**禁止** `#pragma HLS UNROLL`。v6 加了之后综合 20 分钟跑不完。
//   * 乘法位宽是 DSP 用量的直接决定者。v1 把 int8 强转 int16 再乘 ⇒ 16×16 乘法器，
//     LUT 冲到 334%。这里用 9 位（激活 0..255）⇒ 9×9，一个 DSP48E1 装得下。
//   * 输入**不能**整层进片：早期层 409 600 B > BRAM 总量 612 500 B 的 67%。
//   * ★★ 任何修改后的「裸 csynth」必须与出片配置对齐：PAR_OUT=8 / TILE_H=1 /
//     TILE_W=40 / ACC_FULL=0（即 export_engine.py 的默认 tag p8_1_40）。改了这里
//     的默认值却不出片，报告里的资源表就会与板上的 .bit 脱钩。
// ============================================================================
#include <ap_int.h>

// ---- 实现参数（不属于契约，可自由调优；调了要重新扫资源） ----
#ifndef PAR_OUT
#define PAR_OUT 8          // 输出通道并行度：决定 DSP 用量与吞吐，实测见 docs/accel_interface.md §7
#endif
#ifndef TILE_H
// ★ 当前出片配置是 TILE_H=1（见下条注释）；这里若不改默认值，裸 csynth 会综合出
//   TILE_H=4 的另一套硬件。脚本（export_engine.py / csynth_engine.tcl）一律显式
//   传 -DTILE_H=1，故两条路径都能对上；此默认值只为「裸跑也能复现出片配置」。
#define TILE_H 1           // 每次算几行输出；选定 1（docs/accel_interface.md §7.2：
                           // TILE_H=4 会把行缓冲/requant 副本各复制 4 份，LUT 138% 装不下）
#endif
#ifndef TILE_W
#define TILE_W 40          // 每次算几列输出（最宽 W=320 靠列分块覆盖）
#endif

// ---- 实验开关：累加器分区维度 ------------------------------------------------
//   ★ 当前出片配置是 ACC_FULL=0（p8_1_40_a0，docs/accel_interface.md §7.2 选定行）。
//   ACC_FULL=0：`acc` 只按 PAR_OUT 分区 ⇒ 每个通道槽是一块 [TILE_H][TILE_W]
//       的**存储器**，综合报告里出现 16 个地址端口，每个 403 LUT ⇒ 合计 6 448 LUT。
//   ACC_FULL=1：三维修全展开 ⇒ 全部落寄存器，地址变常量、理论上消掉这些 mux，
//       代价是 PAR_OUT*TILE_H*TILE_W*32 bit 的触发器（8*1*40*32 = 10 240 FF）。
//       实测反直觉（§6 教训）：全展开 LUT 反而从 35 385 涨到 70 928 —— 真正的成本
//       是副本数，不是分区方式 ⇒ 保持 0。
//   为什么做成开关而不是直接删：这是**用 FF 换 LUT** 的交易，两侧都是硬上限
//   （LUT 53 200 / FF 106 400），必须实测才知道哪边先撞墙。
#ifndef ACC_FULL
#define ACC_FULL 0
#endif

// ---- 编译期上界（运行期几何不得超过它们；TB 与 PS 侧都要挡） ----
#define K_MAX 3
#define STRIDE_MAX 2
#define LB_W_MAX (TILE_W * STRIDE_MAX + K_MAX - 1)   // 行缓冲每行的列数上界

void conv_engine(const ap_uint<8> *in,
                 const ap_int<8>  *w,
                 const ap_int<32> *params,
                 const ap_uint<8> *lut,
                 ap_uint<8>       *out,
                 int C_IN, int C_OUT,
                 int H_IN, int W_IN,
                 int K, int stride, int pad,
                 int zp_in, int zp_pre,
                 int H_OUT, int W_OUT)
{
    // ★ 只保留**两个** AXI 主口：一个读、一个写。
    //   为什么不按语义分五个口：Zynq-7020 只有 4 个 AXI HP 口（+1 个 ACP），
    //   五个 m_axi 口就得五个从口 ⇒ **接不上**，而且 BD 里要塞五组互联，
    //   读/写分离已经能拿到大部分收益（读与写可并发），再拆只会增加布线。
    //   注意：`params` 与 `lut` 都很小（8 KB / 256 B），挂读口即可。
    #pragma HLS INTERFACE m_axi port=in     offset=slave bundle=gmem_r depth=4194304
    #pragma HLS INTERFACE m_axi port=w      offset=slave bundle=gmem_r depth=4194304
    #pragma HLS INTERFACE m_axi port=params offset=slave bundle=gmem_r depth=8192
    #pragma HLS INTERFACE m_axi port=lut    offset=slave bundle=gmem_r depth=256
    #pragma HLS INTERFACE m_axi port=out    offset=slave bundle=gmem_w depth=4194304
#pragma HLS INTERFACE s_axilite port=C_IN     bundle=control
#pragma HLS INTERFACE s_axilite port=C_OUT    bundle=control
#pragma HLS INTERFACE s_axilite port=H_IN     bundle=control
#pragma HLS INTERFACE s_axilite port=W_IN     bundle=control
#pragma HLS INTERFACE s_axilite port=K        bundle=control
#pragma HLS INTERFACE s_axilite port=stride   bundle=control
#pragma HLS INTERFACE s_axilite port=pad      bundle=control
#pragma HLS INTERFACE s_axilite port=zp_in    bundle=control
#pragma HLS INTERFACE s_axilite port=zp_pre   bundle=control
#pragma HLS INTERFACE s_axilite port=H_OUT    bundle=control
#pragma HLS INTERFACE s_axilite port=W_OUT    bundle=control
#pragma HLS INTERFACE s_axilite port=return   bundle=control

    // ---- 行缓冲：[输出行][抽头行][列] ----
    // ★ 前两维都是编译期常量 ⇒ complete 分区后每拍的 K*K 个抽头是寄存器直连。
    //   越界像素填 zp_in（uint8 域的"零"是零点，不是 0）。
    ap_uint<8> lb[TILE_H][K_MAX][LB_W_MAX];
#pragma HLS ARRAY_PARTITION variable=lb complete dim=0

    // 权重小块：本 ci 的 PAR_OUT×K×K
    ap_int<8> wbuf[PAR_OUT][K_MAX][K_MAX];
#pragma HLS ARRAY_PARTITION variable=wbuf complete dim=0

    // 累加器：**必须 complete 分区落寄存器**（v10 的教训：落 BRAM 后读-改-写 + Bank
    // 冲突会把卷积内层 II 从 1 退化到 8）。
    // ★ 但"分区到哪一维"有讲究，见下面的 ACC_FULL 实验开关。
    ap_int<32> acc[PAR_OUT][TILE_H][TILE_W];
#if ACC_FULL
#pragma HLS ARRAY_PARTITION variable=acc complete dim=0
#else
#pragma HLS ARRAY_PARTITION variable=acc complete dim=1
#endif

    // 本通道块的 requant 参数
    ap_int<32> pm_bias[PAR_OUT];
    ap_int<32> pm_mult[PAR_OUT];
    ap_int<8>  pm_shift[PAR_OUT];
#pragma HLS ARRAY_PARTITION variable=pm_bias  complete dim=1
#pragma HLS ARRAY_PARTITION variable=pm_mult  complete dim=1
#pragma HLS ARRAY_PARTITION variable=pm_shift complete dim=1

    // 激活表搬进片上（256 B），避免每个像素都去碰 DDR
    ap_uint<8> lutc[256];
#pragma HLS ARRAY_PARTITION variable=lutc complete dim=1
    for (int i = 0; i < 256; ++i) {
#pragma HLS PIPELINE II=1
        lutc[i] = lut[i];
    }

    // ---- 主循环 ----
    for (int co0 = 0; co0 < C_OUT; co0 += PAR_OUT) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=32

        // ① 本通道块的 requant 参数（每通道 3 个字，连续摆放）
        for (int p = 0; p < PAR_OUT; ++p) {
#pragma HLS UNROLL
            if (co0 + p < C_OUT) {
                pm_bias[p]  = params[(co0 + p) * 3 + 0];
                pm_mult[p]  = params[(co0 + p) * 3 + 1];
                pm_shift[p] = ap_int<8>(params[(co0 + p) * 3 + 2]);
            } else {                      // 尾块补齐：多算的行不写回
                pm_bias[p] = 0; pm_mult[p] = 0; pm_shift[p] = 0;
            }
        }

        for (int t0 = 0; t0 < H_OUT; t0 += TILE_H) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=64

            for (int w0 = 0; w0 < W_OUT; w0 += TILE_W) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=32

                // a. 累加器初值 = 本通道 folding 后的 bias（契约 §3.2）
                //    ★ 这里不是清零！`acc[c] = Σ x_q*w_q + bias_i32[c]`，bias 必须在初值里。
                //      第一版写成清零、只在别处读了 pm_bias 却没用 ⇒ csim 逐位比对
                //      在 (c=0,h=0,w=0) 就红了。把 bias 放初值比"最后再加一次"少一遍加法，
                //      且不占额外周期。
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

                // b. 逐输入通道累加
                for (int ci = 0; ci < C_IN; ++ci) {
#pragma HLS LOOP_TRIPCOUNT min=3 max=512

                    // b1. 载入本 tile 的输入窗口（TILE_H×K 行 × LB_W_MAX 列）
                    //     ★ 整行连续读：探针 v3→v4 实测，逐字节散读会让 II=10。
                    for (int hl = 0; hl < TILE_H; ++hl) {
#pragma HLS UNROLL
                        for (int kh = 0; kh < K_MAX; ++kh) {
#pragma HLS UNROLL
                            int hr = (t0 + hl) * stride + kh - pad;   // 该抽头行在输入中的绝对行号
                            bool row_ok = (kh < K) && (hr >= 0) && (hr < H_IN);
                            for (int c = 0; c < LB_W_MAX; ++c) {
#pragma HLS PIPELINE II=1
                                int hc = w0 * stride + c - pad;       // 绝对列号
                                bool ok = row_ok && (hc >= 0) && (hc < W_IN);
                                // 越界填 zp_in：uint8 域的「零」是零点，不是 0
                                lb[hl][kh][c] = ok ? in[((ci * H_IN) + hr) * W_IN + hc]
                                                   : ap_uint<8>(zp_in);
                            }
                        }
                    }

                    // b2. 权重取到片上小块
                    //     ★ 尾块必须守卫：C_OUT 不是 PAR_OUT 整数倍时（例 C_OUT=20、
                    //       PAR_OUT=8，尾块只有 4 个有效通道），不守卫就会**越界读**
                    //       w[]，读到别的东西当权重用。补 0 即可 —— 反正这些通道的
                    //       结果不会写回（见 c 段守卫）。
                    for (int p = 0; p < PAR_OUT; ++p) {
#pragma HLS UNROLL
                        for (int kh = 0; kh < K_MAX; ++kh) {
#pragma HLS UNROLL
                            for (int kw = 0; kw < K_MAX; ++kw) {
#pragma HLS UNROLL
                                if (co0 + p < C_OUT) {
                                    wbuf[p][kh][kw] = w[((co0 + p) * C_IN + ci) * K * K + kh * K + kw];
                                } else {
                                    wbuf[p][kh][kw] = ap_int<8>(0);
                                }
                            }
                        }
                    }

                    // b3. 卷积累加
                    // ★ `hl` **不 UNROLL** —— 这是 DSP 预算的关键开关。实测（p8_4_40）：
                    //   TILE_H=4 且 UNROLL ⇒ 乘法器 = PAR_OUT(8) × TILE_H(4) × 9 = 288 个
                    //                      ⇒ 实测 DSP 256 → 全设计 361 (164%)，装不下。
                    //   去掉 UNROLL      ⇒ 乘法器 = PAR_OUT(8) × 9 = 72 个。
                    //   代价：hl 循环变成顺序执行（周期 ×TILE_H），但 **MAC/周期不变**
                    //   ——因为原来 288 个乘法器只有 40 个 wo 迭代在喂，本来就是空转。
                    //   ⇒ 并行度只用 PAR_OUT 一个旋钮控制，不要叠加 TILE_H。
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
                                            // ★ 乘法位宽是 DSP 用量的直接决定者：
                                            //   9 位（激活 0..255）⇒ 9×9，一个 DSP48E1（25×18）装得下。
                                            ap_int<18> pr = ap_int<9>(lb[hl][kh][wo * stride + kw])
                                                          * ap_int<9>(wbuf[p][kh][kw]);
                                            partial += ap_int<32>(pr);
                                        }
                                    }
                                }
                                acc[p][hl][wo] += partial;
                            }
                        }
                    }
                }

                // c. requant → LUT → 写回
                //    ★ 按通道分流到外层、像素连续在内层；**不加 UNROLL**（v6 教训）。
                //    ★★ 三重守卫缺一不可：`co0+p < C_OUT`（尾块越界**写**）、
                //       `ho < H_OUT`（尾行块）、`wo_abs < W_OUT`（尾列块）。
                //       少了第一个的后果特别隐蔽：csim 里是堆被写坏 → `@E Simulation failed.`
                //       且 TB 的 printf 根本没机会输出（表现为"没有任何线索的崩溃"）。
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
                                    // ★★ requant 的 DSP 成本 —— 这里有两个坑，都实测过：
                                    //
                                    //  坑 1（已修）：写成 `ap_int<64>(acc) * ap_int<64>(M)`
                                    //      ⇒ 生成 **64×64 乘法器**（每个 4 DSP），requant 独吃 105 DSP。
                                    //
                                    //  坑 2（本段正在修的）：写成 `ap_int<64>(a * b)`
                                    //      ⇒ 括号里的 `a*b` **先按 32 位算**（会溢出！），再转 64 位。
                                    //      实测仍出现 `mul_64s_64s_64` 实例 ⇒ 说明 HLS 把它当 64×64 综合了。
                                    //
                                    //  正确写法：把两个操作数**显式转成 64 位再做乘法**是不行的（回到坑 1）；
                                    //  要的是「操作数保持 32/32 位宽，乘积用 64 位承接」。HLS 里可靠的做法是
                                    //  先各自扩到一个**足够宽的中间类型**（48 位，覆盖 27+31=58 位乘积 → 用 64），
                                    //  但**宽度写 48 而不是 64**，HLS 才会生成 48×48 而不是 64×64：
                                    //      |acc| < 2^27、|M| < 2^31 ⇒ 乘积 < 2^58
                                    //      用 ap_int<64> 承接、操作数 ap_int<48> ⇒ 48×48→64
                                    //  收益：48×48 在 DSP48E1 上是 3 个（vs 64×64 的 4 个），
                                    //  且由于两个操作数实际有效位远小于 48，HLS 还可能进一步裁剪。
                                    ap_int<48> a48 = ap_int<48>(acc[p][hl][wo]);
                                    ap_int<48> b48 = ap_int<48>(pm_mult[p]);
                                    ap_int<64> prod = ap_int<64>(a48 * b48);
                                    // 四舍五入：加半个 LSB 再算术右移。
                                    // ★ S=0 时 (1<<(S-1)) 是未定义行为 ⇒ 必须保护；
                                    //   契约 §3.3 要求导出侧保证 S∈[1,62]，这里是第二道防线。
                                    ap_int<64> half = (pm_shift[p] > 0)
                                                    ? (ap_int<64>(1) << (pm_shift[p] - 1))
                                                    : ap_int<64>(0);
                                    ap_int<64> shifted = (prod + half) >> pm_shift[p];
                                    ap_int<32> v = ap_int<32>(shifted) + zp_pre;
                                    if (v < 0)   v = 0;
                                    if (v > 255) v = 255;
                                    out[((co0 + p) * H_OUT + ho) * W_OUT + wo_abs] = lutc[ap_uint<8>(v)];
                                }
                            }
                        }
                    }
                    }   // ← 对应 `if (co0 + p < C_OUT)`
                }
            }
        }
    }
}
