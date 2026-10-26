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

reg [31:0] sr;

always @(posedge pclk or negedge reset_n) begin
    if (!reset_n) begin
        bram_addr  <= 14'd0;
        sr         <= 32'd0;
        pixel_data <= BLACK;
    end else begin
        bram_addr <= ypos * 11'd25 + (xpos >> 5);
        if (xpos[4:0] == 5'd0) begin
            sr <= 32'd0;
        end else if (xpos[4:0] == 5'd1) begin
            sr <= bram_dout;
        end else begin
            sr <= {sr[30:0], 1'b0};
        end
        pixel_data <= sr[0] ? WHITE : BLACK;
    end
end

endmodule