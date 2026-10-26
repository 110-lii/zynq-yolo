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

always @(posedge pclk or negedge reset_n) begin
    if (!reset_n) begin
        bram_addr  <= 15'd0;
        sr         <= 32'd0;
        pixel_data <= BLACK;
    end else begin
        bram_addr <= ypos * 11'd40 + (xpos >> 5);
        if (xpos[4:0] == 5'd0) begin
            sr <= 32'd0;
        end else if (xpos[4:0] == 5'd1) begin
            sr <= bram_dout;              // 字地址在 x%32==0 时已发出
        end else begin
            sr <= {sr[30:0], 1'b0};
        end
        pixel_data <= sr[0] ? WHITE : BLACK;
    end
end

endmodule