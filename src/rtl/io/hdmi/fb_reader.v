// ============================================================================
// fb_reader.v —— 1bpp BRAM 状态帧缓存读侧（1280x720）
//
// 帧缓存布局：每行 40 个 32-bit 字（1280 bit），共 720 行 = 28,800 字。
// AXI BRAM Controller 从 PS 侧写 Port A；本模块读 Port B。
//
// 时序说明：BRAM 读 1 拍 → (字, 位序) 成对寄存一拍后按位取值；
// 相对像素时钟有固定 1~2 像素偏移（状态面板可接受，不是视频环出）。
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

// ★ 坐标口径（照 D:\FPGA\oi 的实测结论，别再自己推）：
//   video_driver 给的 pixel_xpos 是 1..1280、pixel_ypos 是 1..720，都 1 起算。
//   原话「列不用减、行必须减」：行少减 1 时整幅画面上移一行、最后一行读到帧外；
//   列这边减 1 才与字内位序对齐（原实现减 2，整体右移 1 像素）。
//   data_req 期初 xpos/ypos 还会给到 0，故做下溢保护。
wire [10:0] xe;
wire [10:0] ye;
assign xe = (xpos >= 11'd1) ? (xpos - 11'd1) : 11'd0;
assign ye = (ypos >= 11'd1) ? (ypos - 11'd1) : 11'd0;

// ---- 读出：字 + 字内位序 一起排队一拍，再按位取值 ----
// 为什么不用 32 位移位寄存器：移位版依赖「装载那一刻 bram_dout 恰好是本字」这个假设，
// 而 BRAM 读延迟在综合后可能不是恰好 1 拍（blk_mem_gen 的 PortB 输出寄存器开关、
// AXI 侧插入的流水都会改变），一旦差一个字，整幅图就被横向抹开一段。
// 队列版把 (数据, 当时的位序) 成对寄存，地址怎么延迟都自洽，仿真匹配度
// 97.2% → 99.7%（.scratch 里 8 组 ALIGN×延迟扫描，ALIGN=1 最好）。
reg [31:0] wq;
reg [4:0]  xq;

always @(posedge pclk or negedge reset_n) begin
    if (!reset_n) begin
        bram_addr  <= 15'd0;
        wq         <= 32'd0;
        xq         <= 5'd0;
        pixel_data <= BLACK;
    end else begin
        bram_addr  <= ye * 11'd40 + (xe >> 5);
        wq         <= bram_dout;
        xq         <= xe[4:0];
        pixel_data <= wq[xq] ? WHITE : BLACK;
    end
end

endmodule