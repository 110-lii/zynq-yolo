// ============================================================================
// hdmi_status_top.v —— HDMI 状态面板顶层（1280x720@30）
//
// 数据通路：
//   PS 写 AXI BRAM（Port A，32-bit 字）→ 1bpp 帧缓存
//   → fb_reader 读 Port B → video_driver 产生 HS/VS/DE/RGB
//   → dvi_transmitter_top（TMDS）→ HDMI 连接器
//
// 视频时序与 vendor 24_hdmi_block_move 的 1280x720p30 参数一致；
// TMDS 引脚见 build/scripts/hdmi_status.xdc。
// ============================================================================
module hdmi_status_top(
    input               pclk,
    input               pclk_x5,
    input               reset_n,

    output      [14:0]  bram_addr,
    input       [31:0]  bram_dout,

    output              tmds_clk_p,
    output              tmds_clk_n,
    output      [2:0]   tmds_data_p,
    output      [2:0]   tmds_data_n
);

wire [10:0] pixel_xpos;
wire [10:0] pixel_ypos;
wire [23:0] pixel_data;
wire [23:0] video_rgb;
wire        video_hs;
wire        video_vs;
wire        video_de;

video_driver u_video_driver(
    .pixel_clk  (pclk),
    .sys_rst_n  (reset_n),
    .video_hs   (video_hs),
    .video_vs   (video_vs),
    .video_de   (video_de),
    .video_rgb  (video_rgb),
    .data_req   (),
    .pixel_data (pixel_data),
    .pixel_xpos (pixel_xpos),
    .pixel_ypos (pixel_ypos)
);

fb_reader u_fb_reader(
    .pclk      (pclk),
    .reset_n   (reset_n),
    .xpos      (pixel_xpos),
    .ypos      (pixel_ypos),
    .bram_addr (bram_addr),
    .bram_dout (bram_dout),
    .pixel_data(pixel_data)
);

dvi_transmitter_top u_dvi(
    .pclk        (pclk),
    .pclk_x5     (pclk_x5),
    .reset_n     (reset_n),
    .video_din   (video_rgb),
    .video_hsync (video_hs),
    .video_vsync (video_vs),
    .video_de    (video_de),
    .tmds_clk_p  (tmds_clk_p),
    .tmds_clk_n  (tmds_clk_n),
    .tmds_data_p (tmds_data_p),
    .tmds_data_n (tmds_data_n),
    .tmds_oen    ()
);

endmodule