// ============================================================================
// lcd_status_top.v —— LCD 800x480 状态面板顶层
// PS 写 AXI BRAM（1bpp）→ lcd_fb_reader → vendor lcd_driver → RGB LCD 接口。
// 使用 4.3 寸 800x480 时序（lcd_id=16'h4384）。
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
    output      [23:0]  lcd_rgb
    ,
    output      [31:0]  dbg1,       // {7'b0, lcd_de, pixel_data}
    output      [31:0]  dbg2        // {10'b0, v_cnt, h_cnt}
);

wire [10:0] pixel_xpos;
wire [10:0] pixel_ypos;
wire [23:0] pixel_data;
wire [10:0] h_cnt_o;
wire [10:0] v_cnt_o;

lcd_fb_reader u_fb_reader(
    .pclk       (pclk),
    .reset_n    (reset_n),
    .xpos       (pixel_xpos),
    .ypos       (pixel_ypos),
    .bram_addr  (bram_addr),
    .bram_dout  (bram_dout),
    .pixel_data (pixel_data)
);

assign dbg1 = {7'b0, lcd_de, pixel_data};
assign dbg2 = {10'b0, v_cnt_o, h_cnt_o};

lcd_driver u_lcd_driver(
    .lcd_pclk   (pclk),
    .rst_n      (reset_n),
    .lcd_id     (16'h4384),
    .pixel_data (pixel_data),
    .pixel_xpos (pixel_xpos),
    .pixel_ypos (pixel_ypos),
    .h_disp     (),
    .v_disp     (),
    .data_req   (),
    .lcd_de     (lcd_de),
    .lcd_hs     (lcd_hs),
    .lcd_vs     (lcd_vs),
    .lcd_bl     (lcd_bl),
    .lcd_clk    (lcd_clk),
    .lcd_rst    (lcd_rst),
    .lcd_rgb    (lcd_rgb),
    .h_cnt_o    (h_cnt_o),
    .v_cnt_o    (v_cnt_o)
);

endmodule