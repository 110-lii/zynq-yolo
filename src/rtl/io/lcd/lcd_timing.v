// ============================================================================
// lcd_timing.v —— 800x480@60 RGB LCD 时序发生器（DE 模式）
//
// 取代 vendor lcd_driver 在本项目里的时序角色，只保留一条明确口径：
//   * 像素时钟 30.000 MHz（clk_wiz_lcd），按 ATK-MD0430R-800480 手册
//     表 2.2.1.2：DCLK typ 30 MHz、Th 928（800 + 40 hbp + 88 hfp）、Tv 525；
//   * DE 模式下 HS/VS 恒高、BL/RST 恒高，行场靠 DE 划分（与官方例程一致）；
//   * pixel_xpos 在 DE 有效期为 1..800，pixel_ypos 为 1..480；
//   * lcd_de / pixel_xpos / pixel_ypos 三者由同一组计数器在同一拍寄存，
//     因此 pixel_data 只要组合输出即可与坐标同拍对齐，不允许再打拍。
//
// 注意：pixel_ypos 在整条有效行（含行消隐）都保持当前行号，帧缓存读侧靠它
// 在行消隐期预取“下一行”。像素坐标本身只在 DE 有效期为非零。
// ============================================================================
module lcd_timing #(
    parameter integer H_SYNC  = 0,
    parameter integer H_BACK  = 40,
    parameter integer H_DISP  = 800,
    parameter integer H_FRONT = 88,
    parameter integer V_SYNC  = 2,
    parameter integer V_BACK  = 33,
    parameter integer V_DISP  = 480,
    parameter integer V_FRONT = 10
)(
    input               pclk,
    input               reset_n,

    output reg          lcd_de,
    output              lcd_hs,
    output              lcd_vs,
    output              lcd_bl,
    output              lcd_clk,
    output              lcd_rst,

    output reg  [10:0]  pixel_xpos,
    output reg  [10:0]  pixel_ypos,
    output      [10:0]  h_cnt_o,
    output      [10:0]  v_cnt_o
);

localparam [10:0] H_ACT0  = H_SYNC + H_BACK;
localparam [10:0] H_ACT1  = H_SYNC + H_BACK + H_DISP;
localparam [10:0] H_TOTAL = H_SYNC + H_BACK + H_DISP + H_FRONT;

localparam [10:0] V_ACT0  = V_SYNC + V_BACK;
localparam [10:0] V_ACT1  = V_SYNC + V_BACK + V_DISP;
localparam [10:0] V_TOTAL = V_SYNC + V_BACK + V_DISP + V_FRONT;

reg [10:0] h_cnt;
reg [10:0] v_cnt;

wire h_active = (h_cnt >= H_ACT0) && (h_cnt < H_ACT1);
wire v_active = (v_cnt >= V_ACT0) && (v_cnt < V_ACT1);

wire de_next = h_active && v_active;
wire [10:0] x_next = h_active ? (h_cnt - H_ACT0 + 11'd1) : 11'd0;
wire [10:0] y_next = v_active ? (v_cnt - V_ACT0 + 11'd1) : 11'd0;

// DE 模式：同步信号保持高电平，背光/复位常开
assign lcd_hs  = 1'b1;
assign lcd_vs  = 1'b1;
assign lcd_bl  = 1'b1;
assign lcd_clk = pclk;
assign lcd_rst = 1'b1;

assign h_cnt_o = h_cnt;
assign v_cnt_o = v_cnt;

always @(posedge pclk or negedge reset_n) begin
    if (!reset_n) begin
        h_cnt      <= 11'd0;
        v_cnt      <= 11'd0;
        lcd_de     <= 1'b0;
        pixel_xpos <= 11'd0;
        pixel_ypos <= 11'd0;
    end else begin
        if (h_cnt == H_TOTAL - 11'd1) begin
            h_cnt <= 11'd0;
            if (v_cnt == V_TOTAL - 11'd1)
                v_cnt <= 11'd0;
            else
                v_cnt <= v_cnt + 11'd1;
        end else begin
            h_cnt <= h_cnt + 11'd1;
        end

        lcd_de     <= de_next;
        pixel_xpos <= x_next;
        pixel_ypos <= y_next;
    end
end

endmodule
