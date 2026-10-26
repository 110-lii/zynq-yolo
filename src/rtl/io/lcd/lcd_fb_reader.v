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

// xpos 由 lcd_driver 算成 h_cnt+2-h_sync-h_back，data_req 期初就是 2 ⇒ 实际像素
// 序号要减 2 才从 0 开始。不减的话字边界 xpos[4:0]==1 永远等不到，sr 一次也不载入，
// pixel_data 恒为 BLACK（session 013 板上实测：h_cnt/v_cnt 在跑、de=1、pix 恒 0x101820）
wire [10:0] xe;
assign xe = (xpos >= 11'd2) ? (xpos - 11'd2) : 11'd0;

always @(posedge pclk or negedge reset_n) begin
    if (!reset_n) begin
        bram_addr  <= 14'd0;
        sr         <= 32'd0;
        pixel_data <= BLACK;
    end else begin
        bram_addr <= ypos * 11'd25 + (xe >> 5);
        if (xe[4:0] == 5'd0) begin
            // 上一字的最后一位（bit31）：右移 30 次后留在 sr[0]
            pixel_data <= sr[0] ? WHITE : BLACK;
            sr         <= 32'd0;
        end else if (xe[4:0] == 5'd1) begin
            // 字数据到位：直接输出 bit0
            pixel_data <= bram_dout[0] ? WHITE : BLACK;
            sr         <= bram_dout;
        end else begin
            // 右移一格，下一拍从 sr[0] 取；本拍先取 sr[1] 即下一位
            pixel_data <= sr[1] ? WHITE : BLACK;
            sr         <= {1'b0, sr[31:1]};
        end
    end
end

endmodule