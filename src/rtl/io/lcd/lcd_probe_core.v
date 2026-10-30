// ============================================================================
// lcd_probe_core.v —— LCD 显示通路「重做版」单分辨率核心（4bpp 调色板版）
//
// 与旧 lcd_timing / lcd_fb_reader 无任何复用关系，口径只在本文件内：
//   1. 同步计数器产生 DE / x / y；图案、RGB、DE 全部同拍寄存后输出。
//   2. 帧缓存读侧不依赖 BRAM 读延迟：整行先读进 stag_flat，在「有效行刚结束
//      的那一拍」一次性并行装进 line_buf，显示期只做 BPP 位右移。
//      地址→数据的延迟只要小于 3 个 pclk，结构都成立。
//   3. 纯 PL 图案（cfg[4]=0）不碰 BRAM，用来单独证明「时序 + 引脚 + 屏」；
//      帧缓存（cfg[4]=1）才走 BRAM。
//   4. 本模块不直接驱动引脚（inout/ODDR/ID 采样都在 lcd_probe_top），
//      这样两种分辨率可以同时跑、由顶层无缝切换。
//
// 帧缓存格式（PS 侧唯一接口，BPP=4 默认）：
//   每行 WPR 个 32-bit 字，每字 = 32/BPP 个像素，字内「最低 BPP 位 = 最左像素」；
//   共 V_DISP 行。字地址 = row * WPR + word。800x480 → WPR=100，48,000 字。
//   PS 先按 16 色调色板把渲染结果量化成 4bpp 索引，再写进来。
//
// 调色板（运行时可变，不占额外 IP）：
//   帧缓存字地址 PAL_BASE .. PAL_BASE+15 是 16 个调色板字，每个 `{8'b0, R, G, B}`。
//   核心在每帧场消隐期把这 16 个字读进 16 个寄存器，全帧用它们做 索引→RGB 查表。
//   ⇒ 换配色只需要 PS 重写这 16 个字，不用重新出片。
//
// 端口 B 的地址口径（2026-10-29 上板抓出来的坑，勿改）：
//   本模块的 bram_addr 必须是**字节地址** = 字地址 << 2。
//   理由：BD 里的 blk_mem_gen 被 axi_bram_ctrl 的字节写使能接口带成了
//   C_USE_BYTE_WEA/WEB=1、C_ADDRx_WIDTH=32 —— 这种模式下 addra/addrb 都是
//   字节地址，IP 内部右移 2 才是字地址；axi_bram_ctrl 的 bram_addr_a[15:0]
//   也是字节地址。PS 侧读写都走端口 A，两边一起移位、回环永远自洽，
//   所以「写进去再读回来一致」抓不到这类错；只有 PL 读侧会读成 mem[addr>>2]
//   （现象：整屏 4 倍放大、每行只剩该源行最左边 7 个字）。
//   仿真台 sim/tb/io/lcd/lcd_probe_core_tb.v 现在也按 mem[addr>>2] 建模。
// ============================================================================
module lcd_probe_core #(
    parameter integer H_DISP  = 800,
    parameter integer V_DISP  = 480,
    parameter integer H_TOTAL = 1056,
    parameter integer V_TOTAL = 525,
    parameter integer H_ACT0  = 216,   // H_SYNC + H_BACK
    parameter integer V_ACT0  = 35,    // V_SYNC + V_BACK
    parameter integer BPP     = 4,     // 每像素位数（4 = 16 色调色板）
    parameter integer WPR     = 100,   // words per row = H_DISP/(32/BPP)
    parameter integer LAST_ROW= 479
)(
    input               pclk,
    input               reset_n,
    input       [31:0]  cfg,          // bit1 背光低有效, [3:2] 图案, bit4 帧缓存

    output      [17:0]  bram_addr,     // 字节地址 = {字地址, 2'b00}，见文件头
    input       [31:0]  bram_dout,

    output reg          lcd_de,
    output reg  [23:0]  lcd_rgb_o,
    output      [10:0]  h_cnt_o,
    output      [10:0]  v_cnt_o,
    output      [31:0]  dbg_tick,

    // 调试抓取：把「面板第 cfg[15:6] 行实际要显示的 32 位」抓成寄存器，
    // 让 PS 能逐行读数字，而不是靠拍照反推。
    output      [31:0]  dbg_lb0,
    output      [31:0]  dbg_lbmid,
    output      [31:0]  dbg_st0,
    output      [31:0]  dbg_stmid,
    output      [31:0]  dbg_cnt
);

localparam integer H_ACT1 = H_ACT0 + H_DISP;
localparam integer V_ACT1 = V_ACT0 + V_DISP;
// 参数显式定宽，避免 integer(32 位) 把表达式的位宽带偏
localparam [10:0]  H_ACT0_11 = H_ACT0;
localparam [10:0]  V_ACT0_11 = V_ACT0;
localparam [10:0]  H_ACT1_11 = H_ACT1;
localparam [10:0]  V_ACT1_11 = V_ACT1;
localparam [15:0]  WPR16     = WPR;
localparam integer NB        = H_DISP * BPP;   // line_buf 位宽
localparam integer PAL_N     = 16;
localparam [15:0]  PAL_BASE  = WPR * V_DISP;   // 调色板首字（紧随图像数据）

localparam [23:0] C_WHITE = 24'hFFFFFF;
localparam [23:0] C_BLACK = 24'h000000;
localparam [23:0] C_RED   = 24'hFF0000;
localparam [23:0] C_GREEN = 24'h00FF00;
localparam [23:0] C_BLUE  = 24'h0000FF;
localparam [23:0] C_CYAN  = 24'h00FFFF;
localparam [23:0] C_MAG   = 24'hFF00FF;
localparam [23:0] C_YEL   = 24'hFFFF00;

localparam integer BAR_W = H_DISP / 8;
localparam integer MID_X = H_DISP / 2;
localparam integer MID_Y = V_DISP / 2;

// ---------------------------------------------------------------------------
// 同步计数器
// ---------------------------------------------------------------------------
reg [10:0] h_cnt;
reg [10:0] v_cnt;
reg [15:0] frame_cnt;

wire h_active = (h_cnt >= H_ACT0_11) && (h_cnt < H_ACT1_11);
wire v_active = (v_cnt >= V_ACT0_11) && (v_cnt < V_ACT1_11);
wire de_next  = h_active && v_active;
wire [10:0] x_next = de_next ? (h_cnt - H_ACT0_11 + 11'd1) : 11'd0;
wire [10:0] y_next = de_next ? (v_cnt - V_ACT0_11 + 11'd1) : 11'd0;

wire frame_start = (h_cnt == 11'd0) && (v_cnt == 11'd0);

always @(posedge pclk or negedge reset_n) begin
    if (!reset_n) begin
        h_cnt     <= 11'd0;
        v_cnt     <= 11'd0;
        frame_cnt <= 16'd0;
    end else begin
        if (h_cnt == H_TOTAL - 1) begin
            h_cnt <= 11'd0;
            if (v_cnt == V_TOTAL - 1) begin
                v_cnt     <= 11'd0;
                frame_cnt <= frame_cnt + 16'd1;
            end else begin
                v_cnt <= v_cnt + 11'd1;
            end
        end else begin
            h_cnt <= h_cnt + 11'd1;
        end
    end
end

assign h_cnt_o = h_cnt;
assign v_cnt_o = v_cnt;

// ---------------------------------------------------------------------------
// 帧缓存读取
// ---------------------------------------------------------------------------
wire [9:0] cur_row = v_active ? (v_cnt[9:0] - V_ACT0_11[9:0]) : 10'd0;
wire [9:0] want_row = v_active
                    ? ((cur_row == LAST_ROW) ? 10'd0 : (cur_row + 10'd1))
                    : 10'd0;

reg [WPR*32-1:0] stag_flat;      // 预取行：WPR 个 32-bit 字平铺
reg [NB-1:0]     line_buf;       // 正在显示的行（最低 BPP 位 = 最左像素）

// 内部只算字地址；出模块前左移 2 位变成 IP 要的字节地址
reg [15:0] bram_wa;
assign bram_addr = {bram_wa, 2'b00};

reg [2:0]  fst;
reg [6:0]  fidx;
reg [9:0]  fill_row;
reg [15:0] fill_base;
reg        filled_once;

// 调色板：场消隐期从 BRAM 读进寄存器
reg [PAL_N*24-1:0] pal_flat;
reg [3:0]  pal_i;
reg        pal_pending;

localparam [2:0] S_IDLE = 3'd6,
                 S_ADDR = 3'd0,
                 S_W1   = 3'd1,
                 S_W2   = 3'd2,
                 S_CAP  = 3'd3,
                 S_PAL0 = 3'd4,
                 S_PAL1 = 3'd5,
                 S_PAL2 = 3'd7;

always @(posedge pclk or negedge reset_n) begin
    if (!reset_n) begin
        fst         <= S_IDLE;
        fidx        <= 7'd0;
        fill_row    <= 10'h3FF;
        fill_base   <= 16'd0;
        bram_wa     <= 16'd0;
        filled_once <= 1'b0;
        stag_flat   <= {WPR*32{1'b0}};
        pal_flat    <= {PAL_N*24{1'b0}};
        pal_i       <= 4'd0;
        pal_pending <= 1'b1;
    end else begin
        if (frame_start) pal_pending <= 1'b1;

        // 只在「空闲」时决定要不要开下一行。
        // 不能在每个时钟都用 !filled_once 判断：filled_once 只在 S_CAP 置位，
        // 若重启条件优先于状态机，状态机永远停在起动分支、永远进不了 S_CAP
        // ⇒ 死锁，整屏黑（iverilog 黄金模型抓到的就是这个）。
        if (fst == S_IDLE) begin
            if (pal_pending) begin
                // 先装调色板：16 个调色板字，每字 3 拍（地址 → 2 拍等待 → 捕获）
                bram_wa <= PAL_BASE + {12'd0, pal_i};
                fst     <= S_PAL0;
            end else if (!filled_once || (want_row != fill_row)) begin
                fill_row  <= want_row;
                fill_base <= want_row * WPR16;
                bram_wa   <= want_row * WPR16;
                fidx      <= 7'd0;
                fst       <= S_ADDR;
            end
        end else begin
            case (fst)
                S_ADDR: begin
                    bram_wa <= fill_base + {{9{1'b0}}, fidx};
                    fst     <= S_W1;
                end
                S_W1: fst <= S_W2;
                S_W2: fst <= S_CAP;
                S_CAP: begin
                    stag_flat[fidx*32 +: 32] <= bram_dout;
                    if (fidx == WPR - 1) begin
                        fidx        <= 7'd0;
                        fst         <= S_IDLE;
                        filled_once <= 1'b1;
                    end else begin
                        fidx <= fidx + 7'd1;
                        fst  <= S_ADDR;
                    end
                end
                S_PAL0: fst <= S_PAL1;
                S_PAL1: fst <= S_PAL2;
                S_PAL2: begin
                    pal_flat[pal_i*24 +: 24] <= bram_dout[23:0];
                    if (pal_i == PAL_N - 1) begin
                        pal_i       <= 4'd0;
                        pal_pending <= 1'b0;
                        fst         <= S_IDLE;
                    end else begin
                        pal_i   <= pal_i + 4'd1;
                        bram_wa <= PAL_BASE + {12'd0, pal_i} + 16'd1;
                        fst     <= S_PAL0;
                    end
                end
                default: fst <= S_IDLE;
            endcase
        end
    end
end

// 索引 → RGB（16 项查表，全部来自场消隐期装载的寄存器）
wire [BPP-1:0] pix_idx = line_buf[BPP-1:0];
reg  [23:0]    fb_pixel;
always @(*) begin
    case (pix_idx)
        4'd0:  fb_pixel = pal_flat[0*24  +: 24];
        4'd1:  fb_pixel = pal_flat[1*24  +: 24];
        4'd2:  fb_pixel = pal_flat[2*24  +: 24];
        4'd3:  fb_pixel = pal_flat[3*24  +: 24];
        4'd4:  fb_pixel = pal_flat[4*24  +: 24];
        4'd5:  fb_pixel = pal_flat[5*24  +: 24];
        4'd6:  fb_pixel = pal_flat[6*24  +: 24];
        4'd7:  fb_pixel = pal_flat[7*24  +: 24];
        4'd8:  fb_pixel = pal_flat[8*24  +: 24];
        4'd9:  fb_pixel = pal_flat[9*24  +: 24];
        4'd10: fb_pixel = pal_flat[10*24 +: 24];
        4'd11: fb_pixel = pal_flat[11*24 +: 24];
        4'd12: fb_pixel = pal_flat[12*24 +: 24];
        4'd13: fb_pixel = pal_flat[13*24 +: 24];
        4'd14: fb_pixel = pal_flat[14*24 +: 24];
        default: fb_pixel = pal_flat[15*24 +: 24];
    endcase
end

always @(posedge pclk or negedge reset_n) begin
    if (!reset_n) begin
        line_buf <= {NB{1'b0}};
    end else if ((h_cnt == H_ACT1_11) && filled_once) begin
        // 只在「有效行刚结束的那一拍」装一次，且此时 stag_flat 恰好是下一行。
        // （若在整个消隐期反复装，会被下一行的预取覆盖成半新半旧。）
        line_buf <= stag_flat[NB-1:0];
    end else if (de_next) begin
        line_buf <= {{BPP{1'b0}}, line_buf[NB-1:BPP]};
    end
end

// ---------------------------------------------------------------------------
// 纯 PL 图案
// ---------------------------------------------------------------------------
wire [10:0] px = (x_next == 11'd0) ? 11'd0 : (x_next - 11'd1);
wire [10:0] py = (y_next == 11'd0) ? 11'd0 : (y_next - 11'd1);

reg [23:0] bar_rgb;
always @(*) begin
    if      (px <  BAR_W)  bar_rgb = C_RED;
    else if (px < 2*BAR_W) bar_rgb = C_GREEN;
    else if (px < 3*BAR_W) bar_rgb = C_BLUE;
    else if (px < 4*BAR_W) bar_rgb = C_CYAN;
    else if (px < 5*BAR_W) bar_rgb = C_MAG;
    else if (px < 6*BAR_W) bar_rgb = C_YEL;
    else if (px < 7*BAR_W) bar_rgb = C_WHITE;
    else                   bar_rgb = C_BLACK;
end

wire on_border = (px < 2) || (px >= H_DISP-2) || (py < 2) || (py >= V_DISP-2);
wire on_rule_x = (py < 16) && (px[4:0] == 5'd0);   // 上边 32px 一刻度
wire on_rule_y = (px < 16) && (py[4:0] == 5'd0);   // 左边 32px 一刻度
wire on_cross  = (px == MID_X) || (py == MID_Y);

// 每帧横移的白块：证明画面在刷新，不是卡住的一帧
wire [10:0] blk_x = frame_cnt[2:0] * BAR_W;
wire on_blk = (py >= MID_Y - 24) && (py < MID_Y + 24) &&
              (px >= blk_x) && (px < blk_x + 48);

wire [23:0] pat_rgb = (on_border | on_rule_x | on_rule_y | on_cross | on_blk)
                    ? C_WHITE : bar_rgb;

wire [23:0] grid_rgb = ((px[4:0] == 5'd0) || (py[4:0] == 5'd0) || on_border)
                     ? C_WHITE : C_BLACK;
// 注意：变量名不能叫 checker —— 那是 SystemVerilog 保留字（iverilog -g2012 会拒编译）
wire [23:0] checker_rgb = ((px[5] ^ py[5]) ? C_WHITE : C_BLACK);

wire [1:0]  pat_sel = cfg[3:2];
wire [23:0] pl_rgb  = (pat_sel == 2'd0) ? pat_rgb :
                      (pat_sel == 2'd1) ? C_WHITE :
                      (pat_sel == 2'd2) ? checker_rgb : grid_rgb;

wire [23:0] pix_next = cfg[4] ? fb_pixel : pl_rgb;

always @(posedge pclk or negedge reset_n) begin
    if (!reset_n) begin
        lcd_de    <= 1'b0;
        lcd_rgb_o <= C_BLACK;
    end else begin
        lcd_de    <= de_next;
        lcd_rgb_o <= pix_next;
    end
end

// ---------------------------------------------------------------------------
// pclk 自由计数器（PS 侧测实际像素时钟）
// ---------------------------------------------------------------------------
reg [31:0] tick;
always @(posedge pclk or negedge reset_n) begin
    if (!reset_n) tick <= 32'd0;
    else          tick <= tick + 32'd1;
end
assign dbg_tick = tick;

// ---------------------------------------------------------------------------
// 调试抓取（PS 逐行扫）
//   cfg[15:6] = 目标面板行号 cap_row（0..V_DISP-1）
//   dbg_lb0   = 该行第一个 DE 像素处 line_buf[31:0]（4bpp ⇒ 最左 8 个像素）
//   dbg_lbmid = 同一拍 line_buf 中间那个字（看行内有没有错位）
//   dbg_st0   = 该行装载那一拍（h==H_ACT1）的 stag_flat[31:0]
//   dbg_stmid = 同拍 stag_flat 的中间字
//   dbg_cnt   = {本帧已完成的预取次数[31:16], 上一行 DE 拍数[15:0]}
//               正确设计下 DE 拍数恒为 H_DISP（800），预取次数每行 +1。
//               只取「行尾已经结算完」的值，避免读到半行的中间态。
// 期望值（RTL 正确时）：DE 拍数 == H_DISP、预取次数 == 1/行、
//   lb* 与上一拍的 st* 相同，且内容属于 want_row 那一行。
// ---------------------------------------------------------------------------
localparam integer MIDW = (WPR / 2) * 32;     // 800→1600, 480→960

wire [9:0]  cap_row    = cfg[15:6];
wire [10:0] cap_v      = V_ACT0_11 + {1'b0, cap_row};
wire        hit_first  = de_next && (v_cnt == cap_v) && (h_cnt == H_ACT0_11);
wire        hit_load   = (h_cnt == H_ACT1_11) && (cap_row != 10'd0) &&
                         (v_cnt == (cap_v - 11'd1));

wire        fill_done  = (fst == S_CAP) && (fidx == WPR - 1);

reg [15:0] de_line;      // 本行累计 DE 拍数
reg [15:0] de_last;      // 上一行结算后的 DE 拍数
reg [15:0] fills_cnt;    // 自由运行：每完成一次预取 +1
reg [31:0] lb0_r, lbmid_r, st0_r, stmid_r, cnt_r;

always @(posedge pclk or negedge reset_n) begin
    if (!reset_n) begin
        de_line   <= 16'd0;
        de_last   <= 16'd0;
        fills_cnt <= 16'd0;
        lb0_r   <= 32'd0;
        lbmid_r <= 32'd0;
        st0_r   <= 32'd0;
        stmid_r <= 32'd0;
        cnt_r   <= 32'd0;
    end else begin
        if (h_cnt == 11'd0) begin
            de_last <= de_line;
            de_line <= 16'd0;
        end else if (de_next) begin
            de_line <= de_line + 16'd1;
        end
        if (fill_done) fills_cnt <= fills_cnt + 16'd1;

        if (hit_first) begin
            lb0_r   <= line_buf[31:0];
            lbmid_r <= line_buf[MIDW +: 32];
        end
        if (hit_load) begin
            st0_r   <= stag_flat[31:0];
            stmid_r <= stag_flat[MIDW +: 32];
            cnt_r   <= {fills_cnt, de_last};
        end
    end
end

assign dbg_lb0   = lb0_r;
assign dbg_lbmid = lbmid_r;
assign dbg_st0   = st0_r;
assign dbg_stmid = stmid_r;
assign dbg_cnt   = cnt_r;

endmodule
