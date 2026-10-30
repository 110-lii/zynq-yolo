// ============================================================================
// lcd_fb_reader.v —— 800x480 / 1bpp 状态面板帧缓存读取器（双行缓存版）
//
// 帧缓存布局：每行 800 像素 = 25 个 32-bit 字，共 480 行 = 12,000 字。
//   bit0 = 行内最左像素，bit31 = 行内最右像素；与 tools/bram_fb.py 的
//   np.packbits(..., bitorder="little") 一致。
//
// 结构：
//   1. 两个 25x32 行缓存 A/B；当前行从其中一个移位输出，另一个在整行时间里
//      预取下一行（每字 4 拍，25 字 100 拍，远小于 928 拍行周期）；
//   2. 行尾（xpos==800）切换 A/B，下一行直接使用预取好的缓存；
//   3. pixel_data 是组合输出，与 pixel_xpos 同拍，不能再打拍。
//
// 这样读侧不依赖“BRAM 地址到数据恰好 1 拍”、也不依赖行消隐有多长。
// ============================================================================
module lcd_fb_reader(
    input               pclk,
    input               reset_n,
    input       [10:0]  xpos,
    input       [10:0]  ypos,
    output reg  [13:0]  bram_addr,
    input       [31:0]  bram_dout,
    output      [23:0]  pixel_data
);

localparam [23:0] WHITE = 24'hFFFFFF;
localparam [23:0] BLACK = 24'h101820;

localparam [4:0]  LAST_WORD = 5'd24;
localparam [13:0] WORDS     = 14'd25;
localparam [9:0]  LAST_ROW  = 10'd479;

wire active_x = (xpos >= 11'd1) && (xpos <= 11'd800);
wire active_y = (ypos >= 11'd1) && (ypos <= 11'd480);
wire active   = active_x && active_y;

wire [9:0] cur_col = active_x ? (xpos[9:0] - 10'd1) : 10'd0;
wire [4:0] word_sel = cur_col[9:5];
wire [4:0] next_word = (word_sel == LAST_WORD) ? 5'd0 : (word_sel + 5'd1);

// 当前行 N（ypos=N，1 基）正在显示第 N-1 行；下一行要显示第 N 行，
// 所以预取行号就是 ypos。垂直消隐期 ypos=0，预取第 0 行。
wire [9:0]  next_row  = (ypos == 11'd0) ? 10'd0 :
                        ((ypos >= 11'd480) ? LAST_ROW : ypos[9:0]);
wire [13:0] fill_base = {4'd0, next_row} * WORDS;

// 双行缓存：异步读、同步写，综合成分布式 RAM
(* ram_style = "distributed" *) reg [31:0] line_a [0:24];
(* ram_style = "distributed" *) reg [31:0] line_b [0:24];

reg        buf_sel;      // 0=显示 line_a，1=显示 line_b
wire [31:0] disp_word0   = buf_sel ? line_b[0]         : line_a[0];
wire [31:0] disp_word    = buf_sel ? line_b[word_sel]  : line_a[word_sel];
wire [31:0] disp_next    = buf_sel ? line_b[next_word] : line_a[next_word];

localparam [2:0] S_ADDR = 3'd0;
localparam [2:0] S_W1   = 3'd1;
localparam [2:0] S_W2   = 3'd2;
localparam [2:0] S_CAP  = 3'd3;

reg [2:0]  st;
reg [4:0]  fill_idx;
reg        fill_done;
reg [9:0]  fill_row_d;

always @(posedge pclk or negedge reset_n) begin
    if (!reset_n) begin
        bram_addr  <= 14'd0;
        st         <= S_ADDR;
        fill_idx   <= 5'd0;
        fill_done  <= 1'b0;
        fill_row_d <= 10'd0;
        buf_sel    <= 1'b0;
    end else begin
        // 行尾切换 A/B：最后一个像素仍用旧缓存，下一行用新缓存
        if (xpos == 11'd800)
            buf_sel <= ~buf_sel;

        // 进入新的一行（或离开垂直消隐）时从第 0 字重装
        if (next_row != fill_row_d) begin
            bram_addr  <= fill_base;
            st         <= S_ADDR;
            fill_idx   <= 5'd0;
            fill_done  <= 1'b0;
            fill_row_d <= next_row;
        end else if (!fill_done) begin
            case (st)
                S_ADDR: begin
                    bram_addr <= fill_base + {9'd0, fill_idx};
                    st        <= S_W1;
                end
                S_W1: st <= S_W2;
                S_W2: st <= S_CAP;
                S_CAP: begin
                    if (ypos == 11'd0) begin
                        // 垂直消隐：两个缓存都装第 0 行，保证首行必定有数据
                        line_a[fill_idx] <= bram_dout;
                        line_b[fill_idx] <= bram_dout;
                    end else if (buf_sel) begin
                        line_a[fill_idx] <= bram_dout;
                    end else begin
                        line_b[fill_idx] <= bram_dout;
                    end

                    if (fill_idx == LAST_WORD) begin
                        fill_idx  <= 5'd0;
                        fill_done <= 1'b1;
                    end else begin
                        fill_idx <= fill_idx + 5'd1;
                    end
                    st <= S_ADDR;
                end
                default: st <= S_ADDR;
            endcase
        end
    end
end

reg [31:0] sr;

always @(posedge pclk or negedge reset_n) begin
    if (!reset_n) begin
        sr <= 32'd0;
    end else if (!active) begin
        // 行/场消隐：始终把当前缓存的行首字装进移位寄存器
        sr <= disp_word0;
    end else if (cur_col[4:0] == 5'd31) begin
        // 当前字最后一个像素：下一拍切换到下一个字
        sr <= disp_next;
    end else begin
        sr <= {1'b0, sr[31:1]};
    end
end

assign pixel_data = active ? (sr[0] ? WHITE : BLACK) : BLACK;

endmodule
