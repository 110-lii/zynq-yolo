// ============================================================================
// hdmi_video_top.v —— HDMI 视频环出顶层（1280x720@30，带检测框的视频）
//
// 与 hdmi_status_top.v（1bpp 文字状态面板）并列的第二个 HDMI 顶层：
//   PS 每帧画好带框的画面（BGR888 → 32bpp 字）写进 DDR 帧缓存，
//   本模块用 fb_scanout 经 AXI4 读主机把 DDR 读出来，交 video_driver 出时序，
//   再经 dvi_transmitter_top 走 TMDS 到 HDMI 连接器。
//
// 端口分组：
//   · 视频时序侧：pclk / pclk_x5 / reset_n（与状态面板一致，74.25 / 371.25 MHz）
//   · 寄存器侧：cfg_addr / cfg_stride / cfg_width / cfg_en（AXI GPIO 输出）
//               st_frames / st_under（AXI GPIO 输入）
//   · AXI4 读主机：接 PS 的 S_AXI_HP0（**ACLK 必须与 pclk 同源**，见 fb_scanout 头注释）
//   · TMDS 输出：四对差分
//
// 注意：ARID/RID 固定为 0（单主设备、单突发在飞），因此不需要 ID 路由。
// ============================================================================
module hdmi_video_top (
    input               pclk,
    input               pclk_x5,
    input               reset_n,

    // 寄存器（AXI GPIO 输出通道 1 / 通道 2）
    //   cfg_pack[15:0]  = 行跨距（字节）
    //   cfg_pack[26:16] = 每行像素数
    //   cfg_pack[27]    = 使能
    input       [31:0]  cfg_addr,
    input       [31:0]  cfg_pack,
    // 状态（AXI GPIO 输入通道）：{st_frames[15:0], st_under[15:0]}
    output      [31:0]  st_status,

    // AXI4 读主机（接 S_AXI_HP0）
    output      [31:0]  m_axi_araddr,
    output      [7:0]   m_axi_arlen,
    output      [2:0]   m_axi_arsize,
    output      [1:0]   m_axi_arburst,
    output      [5:0]   m_axi_arid,
    output              m_axi_arvalid,
    input               m_axi_arready,
    input       [31:0]  m_axi_rdata,
    input       [5:0]   m_axi_rid,
    input       [1:0]   m_axi_rresp,
    input               m_axi_rlast,
    input               m_axi_rvalid,
    output              m_axi_rready,

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
wire        data_req;

assign m_axi_arid = 6'd0;

wire [15:0] cfg_stride = cfg_pack[15:0];
wire [10:0] cfg_width  = cfg_pack[26:16];
wire        cfg_en     = cfg_pack[27];
wire [15:0] st_frames;
wire [15:0] st_under;

assign st_status = {st_frames, st_under};

video_driver u_video_driver(
    .pixel_clk  (pclk),
    .sys_rst_n  (reset_n),
    .video_hs   (video_hs),
    .video_vs   (video_vs),
    .video_de   (video_de),
    .video_rgb  (video_rgb),
    .data_req   (data_req),
    .pixel_data (pixel_data),
    .pixel_xpos (pixel_xpos),
    .pixel_ypos (pixel_ypos)
);

fb_scanout #(
    .H_ACTIVE (1280),
    .V_ACTIVE (720),
    .LBUF     (2048),
    .BURST    (64)
) u_fb_scanout (
    .pclk       (pclk),
    .reset_n    (reset_n),
    .vs         (video_vs),
    .de         (data_req),
    .xpos       (pixel_xpos),
    .ypos       (pixel_ypos),
    .cfg_addr   (cfg_addr),
    .cfg_stride (cfg_stride),
    .cfg_width  (cfg_width),
    .cfg_en     (cfg_en),
    .pixel_data (pixel_data),
    .st_frames  (st_frames),
    .st_under   (st_under),
    .m_araddr   (m_axi_araddr),
    .m_arlen    (m_axi_arlen),
    .m_arsize   (m_axi_arsize),
    .m_arburst  (m_axi_arburst),
    .m_arvalid  (m_axi_arvalid),
    .m_arready  (m_axi_arready),
    .m_rdata    (m_axi_rdata),
    .m_rvalid   (m_axi_rvalid),
    .m_rready   (m_axi_rready),
    .m_rlast    (m_axi_rlast),
    .m_rresp    (m_axi_rresp)
);

dvi_transmitter_top u_dvi(
    .pclk        (pclk),
    .pclk_x5     (pclk_x5),
    .reset_n     (reset_n),
    .video_hs    (video_hs),
    .video_vs    (video_vs),
    .video_de    (video_de),
    .video_rgb   (video_rgb),
    .tmds_clk_p  (tmds_clk_p),
    .tmds_clk_n  (tmds_clk_n),
    .tmds_data_p (tmds_data_p),
    .tmds_data_n (tmds_data_n)
);

endmodule
