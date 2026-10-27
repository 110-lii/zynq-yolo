// ============================================================================
// hdmi_testpattern_top.v —— 纯 PL 生成的测试图案（不碰 BRAM）
//
// 目的：把「显示链路」与「帧缓存读出」两件事切开。
//   本设计里没有任何存储器：pixel_data 完全由 xpos/ypos 组合逻辑算出。
//   若屏上能正确看到 8 色竖条 + 移动白块 ⇒ TMDS/时序/引脚/显示器这条链是好的，
//   问题就落在 fb_reader / BRAM 读侧；若连这个都不对 ⇒ 问题在显示链路本身。
//
// 图案（1280x720）：
//   · 8 条竖色条：红/绿/蓝/青/品红/黄/白/黑，各 160 px
//   · 顶部 y<8 全白、左侧 x<8 全白：1:1 时是两条 8 px 细线，缩放/移位一眼能看出
//   · 左上 64x64 纯黑方块：方位标记（哪边被翻转会立刻显现）
//   · 96x96 白块每帧右移 53 px：证明在刷新，不是卡住的一帧
//
// 像素时钟：外部 50 MHz → 75.000 MHz（pclk）+ 375.000 MHz（pclk_x5，5 倍）
//   —— 与正点原子 23_hdmi_colorbar 例程逐参数一致（74.25 MHz 在本板从 50 MHz 不可达）
// ============================================================================
module hdmi_testpattern_top(
    input               pclk,
    input               pclk_x5,
    input               reset_n,
    input               locked,          // clk_wiz/locked（回读给 PS 看 MMCM 有没有锁）

    // 调试回读（AXI GPIO，只有 PS 能读；不影响画面）
    output      [31:0]  dbg_a,           // {30'b0, de, locked}
    output      [31:0]  dbg_b,           // {10'b0, ypos[10:0], xpos[10:0]}

    output              tmds_clk_p,
    output              tmds_clk_n,
    output      [2:0]   tmds_data_p,
    output      [2:0]   tmds_data_n
);

wire [10:0] xpos;
wire [10:0] ypos;
wire [23:0] pixel_data;
wire [23:0] video_rgb;
wire        video_hs;
wire        video_vs;
wire        video_de;

// 帧计数（vs 上升沿）
reg        vs_d;
reg [11:0] frame_cnt;
always @(posedge pclk or negedge reset_n) begin
    if (!reset_n) begin
        vs_d      <= 1'b0;
        frame_cnt <= 12'd0;
    end else begin
        vs_d <= video_vs;
        if (video_vs && !vs_d) frame_cnt <= frame_cnt + 12'd1;
    end
end

// xpos/ypos 在本设计里是 1 起算（1..1280 / 1..720），组合逻辑里先归零
wire [10:0] x = xpos - 11'd1;
wire [10:0] y = ypos - 11'd1;

// 竖色条：8 段，每段 160 px
reg [23:0] bar_rgb;
always @(*) begin
    if      (x <  11'd160) bar_rgb = 24'hFF0000;   // 红
    else if (x <  11'd320) bar_rgb = 24'h00FF00;   // 绿
    else if (x <  11'd480) bar_rgb = 24'h0000FF;   // 蓝
    else if (x <  11'd640) bar_rgb = 24'h00FFFF;   // 青
    else if (x <  11'd800) bar_rgb = 24'hFF00FF;   // 品红
    else if (x <  11'd960) bar_rgb = 24'hFFFF00;   // 黄
    else if (x <  11'd1120) bar_rgb = 24'hFFFFFF;  // 白
    else                    bar_rgb = 24'h000000;  // 黑
end

// 每帧右移的白块
wire [11:0] bx = (frame_cnt * 12'd53) % 12'd1280;

assign pixel_data =
    (y < 11'd8)            ? 24'hFFFFFF :   // 顶边白线
    (x < 11'd8)            ? 24'hFFFFFF :   // 左边白线
    (x < 11'd64 && y < 11'd64) ? 24'h000000 : // 左上黑方块（方位标记）
    ((y >= 11'd300) && (y < 11'd396) &&
     (x >= bx[10:0]) && (x < bx[10:0] + 11'd96)) ? 24'hFFFFFF :  // 移动白块
    bar_rgb;

assign dbg_a = {30'b0, video_de, locked};
assign dbg_b = {10'b0, ypos, xpos};

video_driver u_video_driver(
    .pixel_clk  (pclk),
    .sys_rst_n  (reset_n),
    .video_hs   (video_hs),
    .video_vs   (video_vs),
    .video_de   (video_de),
    .video_rgb  (video_rgb),
    .data_req   (),
    .pixel_data (pixel_data),
    .pixel_xpos (xpos),
    .pixel_ypos (ypos)
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
