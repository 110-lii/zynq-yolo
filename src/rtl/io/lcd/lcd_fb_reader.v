// ============================================================================
// lcd_fb_reader.v —— 800x480 1bpp BRAM 读侧（LCD 状态面板）
// 每行 25 个 32-bit 字，共 12,000 字；2 像素固定偏移（同 HDMI）。
// ============================================================================
module lcd_fb_reader(
    input               pclk,
    input               reset_n,
    input       [10:0]  xpos,
    input       [10:0]  ypos,
    output reg  [13:0]  bram_addr,
    input       [31:0]  bram_dout,
    output reg  [23:0]  pixel_data
);

localparam [23:0] WHITE = 24'hFFFFFF;
localparam [23:0] BLACK = 24'h101820;

// ★ 坐标口径同 HDMI（照 D:\FPGA\oi 的实测结论）：lcd_driver 的 pixel_xpos 也是
//   1 起算（`cnt_h + 2 - h_sync - h_back`），所以列减 1、行减 1。
//   原实现列减 2（整体右移 1 像素）、行没减（整幅上移一行、最后一行读到帧外）。
wire [10:0] xe;
wire [10:0] ye;
assign xe = (xpos >= 11'd1) ? (xpos - 11'd1) : 11'd0;
assign ye = (ypos >= 11'd1) ? (ypos - 11'd1) : 11'd0;

// 读出：字 + 字内位序 成对寄存一拍后按位取值（同 HDMI，见 fb_reader.v 说明）
reg [31:0] wq;
reg [4:0]  xq;

always @(posedge pclk or negedge reset_n) begin
    if (!reset_n) begin
        bram_addr  <= 14'd0;
        wq         <= 32'd0;
        xq         <= 5'd0;
        pixel_data <= BLACK;
    end else begin
        bram_addr  <= ye * 11'd25 + (xe >> 5);
        wq         <= bram_dout;
        xq         <= xe[4:0];
        pixel_data <= wq[xq] ? WHITE : BLACK;
    end
end

endmodule