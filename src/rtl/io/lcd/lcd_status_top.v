// ============================================================================
// lcd_status_top.v —— LCD 800x480 状态面板顶层
//
// 数据链：PS 写 AXI BRAM（1bpp）→ lcd_fb_reader（整行预取）→ lcd_timing
//         → RGB LCD 接口。
//
// 时序发生器已替换为项目内的 lcd_timing（DE 模式，30.000 MHz，928x525），
// 不再依赖 vendor lcd_driver。dbg1 保持 {de,pixel_data}；dbg2 高位带上
// 复位时从 R7/G7/B7 采到的 3 位屏 ID，低位仍是 {v_cnt,h_cnt}。
// ============================================================================
module lcd_status_top(
    input               pclk,
    input               reset_n,

    output      [13:0]  bram_addr,
    input       [31:0]  bram_dout,

    output              lcd_de,
    output              lcd_hs,
    output              lcd_vs,
    output              lcd_bl,
    output              lcd_clk,
    output              lcd_rst,
    inout       [23:0]  lcd_rgb,

    output      [31:0]  dbg1,       // {7'b0, lcd_de, pixel_data}
    output      [31:0]  dbg2        // {id_bits, 7'b0, v_cnt, h_cnt}
);

wire [10:0] pixel_xpos;
wire [10:0] pixel_ypos;
wire [23:0] pixel_data;
wire [23:0] lcd_rgb_o;
wire [23:0] lcd_rgb_i;
wire [10:0] h_cnt;
wire [10:0] v_cnt;

lcd_timing u_timing(
    .pclk       (pclk),
    .reset_n    (reset_n),
    .lcd_de     (lcd_de),
    .lcd_hs     (lcd_hs),
    .lcd_vs     (lcd_vs),
    .lcd_bl     (lcd_bl),
    .lcd_clk    (lcd_clk),
    .lcd_rst    (lcd_rst),
    .pixel_xpos (pixel_xpos),
    .pixel_ypos (pixel_ypos),
    .h_cnt_o    (h_cnt),
    .v_cnt_o    (v_cnt)
);

lcd_fb_reader u_fb_reader(
    .pclk       (pclk),
    .reset_n    (reset_n),
    .xpos       (pixel_xpos),
    .ypos       (pixel_ypos),
    .bram_addr  (bram_addr),
    .bram_dout  (bram_dout),
    .pixel_data (pixel_data)
);

// 消隐期释放 RGB 总线，给屏上的 ID 电阻让出 R7/G7/B7（原厂 rd_id 同口径）
assign lcd_rgb_o = pixel_data;
assign lcd_rgb   = lcd_de ? lcd_rgb_o : 24'bz;
assign lcd_rgb_i = lcd_rgb;

// 复位后第一拍采样 ID：{B7,G7,R7} = 000 → 480x272；100 → 800x480
reg [2:0] id_bits;
reg       id_sampled;

always @(posedge pclk or negedge reset_n) begin
    if (!reset_n) begin
        id_bits    <= 3'b000;
        id_sampled <= 1'b0;
    end else if (!id_sampled) begin
        id_sampled <= 1'b1;
        id_bits    <= {lcd_rgb_i[7], lcd_rgb_i[15], lcd_rgb_i[23]};
    end
end

assign dbg1 = {7'b0, lcd_de, pixel_data};
assign dbg2 = {id_bits, 7'b0, v_cnt, h_cnt};

endmodule
