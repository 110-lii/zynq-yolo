// =============================================================================
// vid_test_pattern —— DVP 测试图样发生器（替代摄像头，给 vid_dvp_capture 喂已知图样）
// -----------------------------------------------------------------------------
// 为什么需要它：上板时没有摄像头、PS 也不跑软件，采集链路的对错就没有判据。
// 本模块在 pclk 域按 OV5640 的 DVP 时序号输出一个**已知图样**，Python 端用同一套
// 公式算出期望值，就能逐像素判定 DDR 里的内容对不对（对拍而不只是"能跑"）。
//
// 时序契约（与 src/rtl/video/vid_dvp_capture.v 头注释对齐）：
//   * vsync_n 低有效，**下降沿 = 新场开始**，每场仅一次、宽 VSYNC_LEN=4 拍；
//   * href 高有效，每拍 1 个字节；一行 = H_ACTIVE 个像素 = 2*H_ACTIVE 拍；
//   * 每像素 2 个字节，**高字节先发**（RGB565）。
//   每场结构：vsync 脉冲 -> 垂直后沿消隐 -> V_ACTIVE 行（水平后沿消隐 +
//   2*H_ACTIVE 拍有效区 + 水平前沿消隐）-> 垂直前沿消隐 -> 场结束。
//
// 图样公式（Python 端必须逐字用同一套，否则期望值对不上）：
//   R5 = x[4:0]                    // x = 行内第几个像素，0 起
//   G6 = y[5:0]                    // y = 场内第几个有效行，0 起
//   B5 = (x + y)[4:0]
//   高字节 = {R5, G6[5:3]}         （先发）
//   低字节 = {G6[2:0], B5}         （后发）
//
// 参数单位（**关键**：H_TOTAL/V_TOTAL 是"像素"，不是"拍"，因为一个像素占 2 拍）：
//   行周期(拍) = 2*H_TOTAL = 2*H_ACTIVE(href=1) + 2*(H_TOTAL-H_ACTIVE)(水平消隐)
//   场周期(行) = V_TOTAL   = V_ACTIVE + (V_TOTAL-V_ACTIVE)(垂直消隐行)
//   ⇒ 每场拍数 = VSYNC_LEN + V_TOTAL*2*H_TOTAL
//     默认 640x480 / H_TOTAL=800 / V_TOTAL=525 ⇒ 4 + 525*1600 = 840004 拍/场
//     有效字节/场 = 2*H_ACTIVE*V_ACTIVE = 614400（24 MHz 下 35 ms/场，约 28 fps）
//
// 实现要点（为什么这么写）：
//   * **不用除法/取模**：行内位置用"拍计数器 pos + 常数比较"；像素号 x = 字节号>>1
//     （右移一位）；公式里的 [4:0]/[5:0] 直接由**赋值截位/补零**完成（等价 & 掩码），
//     综合后全是连线，没有比较链之外的运算单元。
//   * **不用 initial 赋初值**：所有状态一律由同步复位进入，上板行为与仿真一致。
//   * 三个输出在**同一个 always** 里用非阻塞赋值寄存 ⇒ href/vsync_n/dvp_d 同拍对齐、
//     无组合毛刺。对外看到的场比内部计数器晚 1 拍，不影响任何时序契约。
//   * 只有一组计数器（场相位 + 行内 pos + 场内行号 ln），面积很小。
//
// run 语义：
//   run=0 ⇒ 立刻停在"场消隐"：回场首、href=0、vsync_n=1、dvp_d=0，且不发场；
//   run=1 ⇒ 从新的一场、图样相位 (0,0) 起连续发场。
// =============================================================================
`timescale 1ns / 1ps
`default_nettype none

module vid_test_pattern #(
    parameter integer H_ACTIVE = 640,   // 一行有效像素
    parameter integer V_ACTIVE = 480,   // 一场有效行
    parameter integer H_TOTAL  = 800,   // 行周期（含水平 blanking，必须 >= H_ACTIVE）
    parameter integer V_TOTAL  = 525    // 场周期（含垂直 blanking，必须 >= V_ACTIVE）
) (
    input  wire       clk,       // 像素时钟（= DVP pclk 域）
    input  wire       rst,       // 同步、高有效
    input  wire       run,       // 1 = 连续输出场；0 = 停止在「场消隐」状态
    output reg        vsync_n,   // 低有效，每场一个脉冲
    output reg        href,      // 高有效，行长 H_ACTIVE 个**拍**（每拍 1 字节）
    output reg  [7:0] dvp_d      // RGB565 字节流：每像素先高字节后低字节
);

    // ------------------------------------------------------------- 参数换算
    // 全部用 localparam 的加法/移位算出来（综合是常数折叠），不出现除法/取模。
    // vsync_n 低电平拍数：规范要求"至少 2 拍"，这里取 4 拍，给下游同步链留余量。
    localparam integer VSYNC_LEN = 4;

    // 水平消隐拍数 = 2*(H_TOTAL-H_ACTIVE)（每个像素 2 拍）。
    // 参数写错（H_TOTAL<=H_ACTIVE）时兜底给 2 拍，保证 HBP/HFP 都不为 0，
    // 否则 href 会和消隐粘连，行边界在上板波形上就看不出来了。
    localparam integer HBLANK   = (H_TOTAL > H_ACTIVE) ? (2*(H_TOTAL - H_ACTIVE)) : 2;
    localparam integer HBP      = (HBLANK >= 2) ? (HBLANK >> 1) : 1;  // 水平后沿消隐（>=1）
    localparam integer HFP      = HBLANK - HBP;                       // 水平前沿消隐（>=1）
    localparam integer LINE_CYC = 2*H_ACTIVE + HBLANK;                // 行周期（拍）

    // 垂直消隐行数 = V_TOTAL-V_ACTIVE（兜底 1 行），一半放场后沿、一半放场前沿。
    localparam integer VBLANK   = (V_TOTAL > V_ACTIVE) ? (V_TOTAL - V_ACTIVE) : 1;
    localparam integer VBP      = (VBLANK >= 2) ? (VBLANK >> 1) : 1;  // 垂直后沿消隐（行）
    localparam integer VFP      = VBLANK - VBP;                       // 垂直前沿消隐（行）
    localparam integer VLINES   = VBP + V_ACTIVE + VFP;               // 场内总行数

    // ------------------------------------------------------------- 位宽推导
    localparam integer XW    = (H_ACTIVE > 1)  ? $clog2(H_ACTIVE)  : 1;
    localparam integer WPOS  = (LINE_CYC > 1)  ? $clog2(LINE_CYC)  : 1;
    localparam integer WLINE = (VLINES  > 1)   ? $clog2(VLINES)    : 1;
    localparam integer WVC   = (VSYNC_LEN > 1) ? $clog2(VSYNC_LEN) : 1;

    // 计数器比较用的定宽常数（避免在 always 里对 integer 做比较，综合更干净）
    localparam [WVC-1:0]   VC_END    = VSYNC_LEN - 1;
    localparam [WPOS-1:0]  LINE_END  = LINE_CYC - 1;
    localparam [WPOS-1:0]  HBP_C     = HBP;
    // 有效区右开边界：因为 HFP>=1，HACT_END <= LINE_CYC-1，不会溢出 WPOS 位
    localparam [WPOS-1:0]  HACT_END  = HBP + 2*H_ACTIVE;
    localparam [WLINE-1:0] VLINE_END = VLINES - 1;
    localparam [WLINE-1:0] VBP_C     = VBP;
    localparam [WLINE-1:0] VACT_END  = VBP + V_ACTIVE;

    // ------------------------------------------------------------------- 状态
    // 场相位只有两个：vsync 脉冲期（st=0）与"逐行输出+垂直消隐"期（st=1）。
    // 把 vsync 抽成独立相位而不是塞进行计数，是为了让 vsync 宽度与行周期解耦，
    // 改 H_TOTAL/V_TOTAL 时不会意外改到 vsync 宽度。
    localparam        S_VSYNC = 1'b0;
    localparam        S_FRAME = 1'b1;

    reg               st;        // 当前场相位
    reg  [WVC-1:0]    vc;        // vsync 脉冲内的拍计数
    reg  [WPOS-1:0]   pos;       // 行内拍计数 0..LINE_CYC-1
    reg  [WLINE-1:0]  ln;        // 场内行序号 0..VLINES-1（含前后消隐行）

    // ------------------------------------------------------- 位置计数器（时序）
    // 复位或 run=0 时一律回到"场首"：这样 run 再次拉高一定从新的一场、(0,0) 相位开始。
    // 注意场结束时只回到 S_VSYNC，不清 pos/ln 以外的东西——vc 在离开 S_VSYNC 时
    // 已经归零，所以下一场 vsync 宽度依旧精确等于 VSYNC_LEN。
    always @(posedge clk) begin
        if (rst || !run) begin
            st  <= S_VSYNC;
            vc  <= {WVC{1'b0}};
            pos <= {WPOS{1'b0}};
            ln  <= {WLINE{1'b0}};
        end else if (st == S_VSYNC) begin
            if (vc == VC_END) begin
                st  <= S_FRAME;             // vsync 脉冲放完 -> 进入逐行输出
                vc  <= {WVC{1'b0}};
                pos <= {WPOS{1'b0}};
                ln  <= {WLINE{1'b0}};
            end else begin
                vc  <= vc + 1'b1;
            end
        end else begin
            if (pos == LINE_END) begin              // 一行结束
                pos <= {WPOS{1'b0}};
                if (ln == VLINE_END) begin
                    st  <= S_VSYNC;               // 一场结束 -> 下一场从 vsync 相位开始
                    ln  <= {WLINE{1'b0}};
                end else begin
                    ln  <= ln + 1'b1;
                end
            end else begin
                pos <= pos + 1'b1;
            end
        end
    end

    // --------------------------------------------------- 位置 -> 输出（组合译码）
    // 判"当前这一拍该发什么"，然后统一在下面打一拍寄存。
    wire in_frame   = (st == S_FRAME);
    wire ln_active  = in_frame && (ln >= VBP_C) && (ln < VACT_END);   // 落在有效行区
    wire pos_active = (pos >= HBP_C) && (pos < HACT_END);             // 落在行内有效区
    wire href_c     = ln_active && pos_active;

    // 行内字节号：0..2*H_ACTIVE-1。只在 href_c=1 时有意义（消隐期会下溢，但被丢弃）。
    wire [WPOS-1:0]  k  = pos - HBP_C;
    wire [XW-1:0]    x  = k[WPOS-1:1];       // 像素号 = 字节号 / 2（右移，不用除法）
    wire [WLINE-1:0] yv = ln - VBP_C;        // 有效行号 y（消隐行时无意义）

    // 图样公式：赋值时的**补零/截位**天然实现 x[4:0]、y[5:0]、和取低 5 位，
    // 等价于 & 0x1F / & 0x3F 掩码，但综合出来是纯连线。
    wire [4:0] r5 = x;                       // R5 = x[4:0]
    wire [5:0] g6 = yv;                      // G6 = y[5:0]
    wire [4:0] b5 = x + g6;                  // B5 = (x+y)[4:0]（低 5 位即模 32）

    // 每像素两拍：k[0]=0 发高字节，k[0]=1 发低字节。
    wire [7:0] dvp_c   = href_c ? (k[0] ? {g6[2:0], b5} : {r5, g6[5:3]}) : 8'h00;
    wire       vsync_c = (st == S_VSYNC) ? 1'b0 : 1'b1;

    // --------------------------------------------------------- 输出寄存器（时序）
    // 三个输出同一个 always、同一拍更新：href/vsync_n/dvp_d 严格对齐、无毛刺。
    // 消隐期 dvp_d 强制 0（不留 x），下游 capture 不受影响，上板抓波形也好看。
    always @(posedge clk) begin
        if (rst || !run) begin
            vsync_n <= 1'b1;                 // run=0 停在"场消隐"：vsync_n 保持 1
            href    <= 1'b0;
            dvp_d   <= 8'h00;
        end else begin
            vsync_n <= vsync_c;
            href    <= href_c;
            dvp_d   <= dvp_c;
        end
    end

endmodule

`default_nettype wire