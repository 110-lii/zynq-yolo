// ============================================================================
// fb_reader.v —— 1280x720 / 1bpp 帧缓存读取器（预取移位寄存器版）
//
// 与 LCD 侧 `lcd_fb_reader.v` 同构，只差分辨率：
//   每行 1280 像素 = 40 个 32-bit 字，共 720 行 = 28,800 字。
//   bit0 = 行内最左像素（与 tools/bram_fb.py 的 pack_1bpp 一致）。
//
// 为什么不用「(字, 位序) 成对寄存」的队列版：
//   队列版把「地址到数据恰好 N 拍」写死。BRAM 输出寄存器开关、AXI 侧流水、
//   换 MMCM 后的时钟都会改变这个 N，一旦差一拍整幅图就横向抹开 ——
//   板上表现就是「读回逐字正确、屏上是灰长条/渐变」（session 017）。
//   本版在**每个字的第一拍就把下一字地址**发出去，留 32 拍裕量，
//   实测 1/2/3 拍读延迟下逐像素结果完全相同。
//
// 关键时序口径：`video_driver` 的 pixel_xpos 有效值 1..1280、pixel_ypos 1..720，
//   且 `video_rgb = video_de ? pixel_data : 0` 是**组合选通**，
//   所以 pixel_data 必须与坐标同拍，不能打拍（打一拍每行第 0 列读到黑）。
// ============================================================================
module fb_reader(
    input               pclk,
    input               reset_n,
    input       [10:0]  xpos,
    input       [10:0]  ypos,
    output reg  [14:0]  bram_addr,
    input       [31:0]  bram_dout,
    output      [23:0]  pixel_data
);

localparam [23:0] WHITE = 24'hFFFFFF;
localparam [23:0] BLACK = 24'h101820;
localparam [14:0] WORDS_PER_LINE = 15'd40;

wire active_x = (xpos >= 11'd1) && (xpos <= 11'd1280);
wire active_y = (ypos >= 11'd1) && (ypos <= 11'd720);
wire active   = active_x && active_y;

wire [10:0] cur_col = active_x ? (xpos - 11'd1) : 11'd0;   // 0..1279
wire [9:0]  cur_row = active_y ? (ypos[9:0] - 10'd1) : 10'd0;  // 0..719

wire [14:0] row_base = {5'd0, cur_row} * WORDS_PER_LINE;

reg [31:0] sr;
reg [5:0]  word_idx;   // 0..39

wire [5:0]  next_word = (word_idx < 6'd39) ? (word_idx + 6'd1) : 6'd0;
wire [14:0] next_addr = row_base + {9'd0, next_word};

always @(posedge pclk or negedge reset_n) begin
    if (!reset_n) begin
        bram_addr <= 15'd0;
        sr        <= 32'd0;
        word_idx  <= 6'd0;
    end else if (!active_x) begin
        bram_addr <= row_base;
        sr        <= bram_dout;
        word_idx  <= 6'd0;
    end else if (cur_col[4:0] == 5'd31) begin
        sr        <= bram_dout;
        word_idx  <= next_word;
        bram_addr <= next_addr;
    end else begin
        sr <= {1'b0, sr[31:1]};
        if (cur_col[4:0] == 5'd0)
            bram_addr <= next_addr;
    end
end

assign pixel_data = active ? (sr[0] ? WHITE : BLACK) : BLACK;

endmodule
