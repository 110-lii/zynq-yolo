// ============================================================================
// fb_reader.v —— 1bpp BRAM 状态帧缓存读侧（1280x720）
//
// 帧缓存布局：每行 40 个 32-bit 字（1280 bit），共 720 行 = 28,800 字。
// AXI BRAM Controller 从 PS 侧写 Port A；本模块读 Port B。
//
// 时序说明：BRAM 读延迟 1 拍，本模块按 32 像素一字装载移位寄存器；
// 相对像素时钟有 2 像素固定偏移（状态面板可接受，不是视频环出）。
// ============================================================================
module fb_reader(
    input               pclk,
    input               reset_n,
    input       [10:0]  xpos,
    input       [10:0]  ypos,
    output reg  [14:0]  bram_addr,
    input       [31:0]  bram_dout,
    output reg  [23:0]  pixel_data
);

localparam [23:0] WHITE = 24'hFFFFFF;
localparam [23:0] BLACK = 24'h101820;

reg [31:0] sr;

// pixel_xpos = cnt_h+2-H_SYNC-H_BACK，data_req 期初即为 2 ⇒ 像素序号要减 2 才从 0 起。
// 不减则 xpos[4:0]==1 永不命中，sr 一次不载入、pixel_data 恒 BLACK（同 LCD，session 013）
wire [10:0] xe;
assign xe = (xpos >= 11'd2) ? (xpos - 11'd2) : 11'd0;

always @(posedge pclk or negedge reset_n) begin
    if (!reset_n) begin
        bram_addr  <= 15'd0;
        sr         <= 32'd0;
        pixel_data <= BLACK;
    end else begin
        bram_addr <= ypos * 11'd40 + (xe >> 5);
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