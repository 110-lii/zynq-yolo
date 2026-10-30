// ============================================================================
// lcd_hstub.v —— LCD 硬件通路自证（会话 017）
//
// 只验证「屏 + 排线 + 引脚 + 像素时钟 + 时序参数」这一层，不接帧缓存：
//   · 时序完全照正点原子 4.3" 800x480（lcd_id=4384）参数；
//   · 像素数据由 pclk（25 MHz，BD 里 clk_wiz 给）产生；
//   · 画面 = 8 段彩条 + 一条每帧下移 8 行的黑带 + 一条每帧右移 8 px 的绿块。
// 游标在动 ⇒ 时钟/行场计数/DE 窗口都活着，问题只可能在帧缓存与读侧。
//
// 不复用 lcd_driver，避免"官方驱动 + 我们改过的端口"再次混入变量；
// 但参数与 `lcd_driver.v` 的 4384 分支逐项相同（128/88/800/40/1056、2/33/480/10/525）。
// ============================================================================
module lcd_hstub(
    input               pclk,        // 25 MHz
    input               reset_n,     // 低有效

    output reg          lcd_de,
    output              lcd_hs,
    output              lcd_vs,
    output              lcd_bl,
    output              lcd_clk,
    output              lcd_rst,
    output reg  [23:0]  lcd_rgb
);

localparam [10:0] H_SYNC  = 11'd128;
localparam [10:0] H_BACK  = 11'd88;
localparam [10:0] H_DISP  = 11'd800;
localparam [10:0] V_SYNC  = 11'd2;
localparam [10:0] V_BACK  = 11'd33;
localparam [10:0] V_DISP  = 11'd480;
localparam [10:0] H_TOTAL = 11'd1056;
localparam [10:0] V_TOTAL = 11'd525;

localparam [23:0] C_WHITE = 24'hFFFFFF;
localparam [23:0] C_BLACK = 24'h000000;
localparam [23:0] C_RED   = 24'hFF0000;
localparam [23:0] C_GREEN = 24'h00FF00;
localparam [23:0] C_BLUE  = 24'h0000FF;
localparam [23:0] C_YEL   = 24'hFFFF00;
localparam [23:0] C_CYAN  = 24'h00FFFF;
localparam [23:0] C_MAG   = 24'hFF00FF;

assign lcd_hs  = 1'b1;
assign lcd_vs  = 1'b1;
assign lcd_bl  = 1'b1;
assign lcd_clk = pclk;
assign lcd_rst = 1'b1;

reg  [10:0] h_cnt, v_cnt;
reg  [5:0]  frame_cnt;

wire in_h = (h_cnt >= H_SYNC + H_BACK) && (h_cnt < H_SYNC + H_BACK + H_DISP);
wire in_v = (v_cnt >= V_SYNC + V_BACK) && (v_cnt < V_SYNC + V_BACK + V_DISP);

always @(posedge pclk or negedge reset_n) begin
    if (!reset_n) begin
        h_cnt     <= 11'd0;
        v_cnt     <= 11'd0;
        frame_cnt <= 6'd0;
        lcd_de    <= 1'b0;
    end else begin
        if (h_cnt == H_TOTAL - 11'd1) begin
            h_cnt <= 11'd0;
            if (v_cnt == V_TOTAL - 11'd1) begin
                v_cnt     <= 11'd0;
                frame_cnt <= frame_cnt + 6'd1;
            end else begin
                v_cnt <= v_cnt + 11'd1;
            end
        end else begin
            h_cnt <= h_cnt + 11'd1;
        end
        lcd_de <= in_h && in_v;
    end
end

wire [10:0] hx = in_h ? (h_cnt - H_SYNC - H_BACK) : 11'd0;
wire [10:0] vy = in_v ? (v_cnt - V_SYNC - V_BACK) : 11'd0;

wire [10:0] scan_raw = {5'd0, frame_cnt} << 3;
wire [10:0] scan_y   = (scan_raw >= 11'd480) ? (scan_raw - 11'd480) : scan_raw;
wire [10:0] live_x   = scan_raw;

always @(*) begin
    if      (hx < 11'd100) lcd_rgb = C_WHITE;
    else if (hx < 11'd200) lcd_rgb = C_YEL;
    else if (hx < 11'd300) lcd_rgb = C_CYAN;
    else if (hx < 11'd400) lcd_rgb = C_GREEN;
    else if (hx < 11'd500) lcd_rgb = C_MAG;
    else if (hx < 11'd600) lcd_rgb = C_RED;
    else if (hx < 11'd700) lcd_rgb = C_BLUE;
    else                   lcd_rgb = C_BLACK;

    if ((vy >= scan_y) && (vy < scan_y + 11'd32))
        lcd_rgb = C_BLACK;

    if ((vy >= 11'd200) && (vy < 11'd280) &&
        (hx >= live_x) && (hx < live_x + 11'd64))
        lcd_rgb = C_GREEN;

    if (!lcd_de) lcd_rgb = C_BLACK;
end

endmodule
