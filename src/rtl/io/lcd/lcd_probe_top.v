// ============================================================================
// lcd_probe_top.v —— LCD 双分辨率探针顶层
//
// 同一个 bitstream 里同时跑 800x480@33.333MHz 与 480x272@9.000MHz 两个核心，
// cfg[5] 选择谁驱动引脚（0=800x480 默认，1=480x272）。这样「屏到底是哪个
// 分辨率」不用靠 ID 猜、也不用反复出片：上板切一下 cfg 就能 A/B。
//
// cfg 位定义（AXI GPIO 输出，默认全 0）：
//   [0]   像素时钟相位：0 = 反相 180°（默认，数据在 pclk 上升沿发出、
//         屏在下降沿采，等价中心对齐）；1 = 同相 0°
//   [1]   背光：0 = 亮，1 = 灭（lcd_bl = ~cfg[1]）
//   [3:2] 纯 PL 图案：0 彩条+边框+刻度+十字+移动块 / 1 全白 / 2 棋盘 / 3 网格
//   [4]   1 = 显示 BRAM 帧缓存（4bpp + 16 色调色板），0 = 纯 PL 图案
//   [5]   1 = 480x272 核心，0 = 800x480 核心
//
// 引脚侧只在这里驱动：RGB 在 DE=0 时释放为高阻，给屏上的 ID 电阻让路；
// 同时用 pclk33 在消隐期采样全部 24 根 RGB 线，输出 level 与 toggle
// （toggle=1 表示该位在采样期间翻转，基本就是浮空/不可信）。
// ============================================================================
module lcd_probe_top(
    input               pclk33,
    input               reset_n33,
    input               pclk9,
    input               reset_n9,
    input       [31:0]  cfg,

    output      [17:0]  bram_addr_800,     // 字节地址（IP 是字节地址口径，见 core 文件头）
    input       [31:0]  bram_dout_800,
    output      [15:0]  bram_addr_480,     // 480 那块 BRAM 只有 64 KB（16 位字节地址）
    input       [31:0]  bram_dout_480,

    output              lcd_de,
    output              lcd_hs,
    output              lcd_vs,
    output              lcd_bl,
    output              lcd_clk,
    output              lcd_rst,
    inout       [23:0]  lcd_rgb,

    output      [31:0]  dbg_id_level,
    output      [31:0]  dbg_id_toggle,
    output      [31:0]  dbg_timing,
    output      [31:0]  dbg_tick,

    // 800x480 核心的逐行自证抓取（cfg[15:6] 选行），PS 侧读来看实际显示内容
    output      [31:0]  dbg_lb0,
    output      [31:0]  dbg_lbmid,
    output      [31:0]  dbg_st0,
    output      [31:0]  dbg_stmid,
    output      [31:0]  dbg_cnt
);

wire sel480 = cfg[5];

// cfg 是准静态控制字：各自域两级同步后使用（第一级标 ASYNC_REG）
(* ASYNC_REG = "TRUE" *) reg [31:0] cfg_s1_33;
reg [31:0] cfg_s2_33;
(* ASYNC_REG = "TRUE" *) reg [31:0] cfg_s1_9;
reg [31:0] cfg_s2_9;

always @(posedge pclk33 or negedge reset_n33) begin
    if (!reset_n33) begin
        cfg_s1_33 <= 32'd0;
        cfg_s2_33 <= 32'd0;
    end else begin
        cfg_s1_33 <= cfg;
        cfg_s2_33 <= cfg_s1_33;
    end
end

always @(posedge pclk9 or negedge reset_n9) begin
    if (!reset_n9) begin
        cfg_s1_9 <= 32'd0;
        cfg_s2_9 <= 32'd0;
    end else begin
        cfg_s1_9 <= cfg;
        cfg_s2_9 <= cfg_s1_9;
    end
end

// ---------------------------------------------------------------------------
// 两个核心常驻运行
// ---------------------------------------------------------------------------
wire        de_800, de_480;
wire [23:0] rgb_800, rgb_480;
wire [10:0] h_800, v_800, h_480, v_480;
wire [31:0] tick_800, tick_480;
wire [17:0] addr480;
assign bram_addr_480 = addr480[15:0];   // 480x272 4bpp 只用到 16,336 字 = 65,344 B < 64 K*4

lcd_probe_core #(
    .H_DISP  (800), .V_DISP  (480),
    .H_TOTAL (1056), .V_TOTAL (525),
    .H_ACT0  (216),  .V_ACT0  (35),
    .BPP     (4),    .WPR     (100),  .LAST_ROW(479)
) u_core_800 (
    .pclk       (pclk33),
    .reset_n    (reset_n33),
    .cfg        (cfg_s2_33),
    .bram_addr  (bram_addr_800),
    .bram_dout  (bram_dout_800),
    .lcd_de     (de_800),
    .lcd_rgb_o  (rgb_800),
    .h_cnt_o    (h_800),
    .v_cnt_o    (v_800),
    .dbg_tick   (tick_800),
    .dbg_lb0    (dbg_lb0),
    .dbg_lbmid  (dbg_lbmid),
    .dbg_st0    (dbg_st0),
    .dbg_stmid  (dbg_stmid),
    .dbg_cnt    (dbg_cnt)
);

lcd_probe_core #(
    .H_DISP  (480), .V_DISP  (272),
    .H_TOTAL (525), .V_TOTAL (286),
    .H_ACT0  (43),  .V_ACT0  (12),
    .BPP     (4),   .WPR     (60),   .LAST_ROW(271)
) u_core_480 (
    .pclk       (pclk9),
    .reset_n    (reset_n9),
    .cfg        (cfg_s2_9),
    .bram_addr  (addr480),
    .bram_dout  (bram_dout_480),
    .lcd_de     (de_480),
    .lcd_rgb_o  (rgb_480),
    .h_cnt_o    (h_480),
    .v_cnt_o    (v_480),
    .dbg_tick   (tick_480)
);

// ---------------------------------------------------------------------------
// 输出选择
// ---------------------------------------------------------------------------
wire        de_sel  = sel480 ? de_480  : de_800;
wire [23:0] rgb_sel = sel480 ? rgb_480 : rgb_800;
wire [10:0] h_sel   = sel480 ? h_480   : h_800;
wire [10:0] v_sel   = sel480 ? v_480   : v_800;
wire [31:0] tick_sel= sel480 ? tick_480: tick_800;

assign lcd_de  = de_sel;
assign lcd_hs  = 1'b1;        // DE 模式：HS/VS 恒高（厂商例程同口径）
assign lcd_vs  = 1'b1;
assign lcd_rst = 1'b1;
assign lcd_bl  = ~cfg[1];

assign lcd_rgb = de_sel ? rgb_sel : 24'bz;

wire [23:0] lcd_rgb_i = lcd_rgb;

// ---------------------------------------------------------------------------
// 前向像素时钟：BUFGMUX 选源 + ODDR 选相位（cfg[0]）
// ---------------------------------------------------------------------------
wire clk_sel;
BUFGMUX_CTRL u_clk_mux (
    .O  (clk_sel),
    .I0 (pclk33),     // S=0 → 800x480
    .I1 (pclk9),      // S=1 → 480x272
    .S  (sel480)
);

ODDR #(
    .DDR_CLK_EDGE("SAME_EDGE"),
    .INIT(1'b0),
    .SRTYPE("ASYNC")
) u_oddr_clk (
    .Q  (lcd_clk),
    .C  (clk_sel),
    .CE (1'b1),
    .D1 (~cfg[0]),
    .D2 ( cfg[0]),
    .R  (1'b0),
    .S  (1'b0)
);

// ---------------------------------------------------------------------------
// 屏 ID：消隐期采样 24 根 RGB 线（pclk33 域，de 先做两级同步）
// ---------------------------------------------------------------------------
reg [1:0] de_sync;
always @(posedge pclk33 or negedge reset_n33) begin
    if (!reset_n33) de_sync <= 2'b00;
    else            de_sync <= {de_sync[0], de_sel};
end
wire blank33 = ~de_sync[1];

reg [23:0] id_level;
reg [23:0] id_toggle;      // 最近 256 个消隐样本内翻转过的位
reg [23:0] id_prev;
reg [23:0] id_win;
reg [7:0]  id_win_cnt;
reg [19:0] id_settle;

always @(posedge pclk33 or negedge reset_n33) begin
    if (!reset_n33) begin
        id_level    <= 24'd0;
        id_toggle   <= 24'd0;
        id_prev     <= 24'd0;
        id_win      <= 24'd0;
        id_win_cnt  <= 8'd0;
        id_settle   <= 20'd0;
    end else if (id_settle != 20'hFFFFF) begin
        id_settle <= id_settle + 20'd1;     // ≈31 ms @33 MHz，等屏上电稳定
    end else if (blank33) begin
        id_level <= lcd_rgb_i;
        id_prev  <= lcd_rgb_i;
        id_win   <= id_win | (lcd_rgb_i ^ id_prev);
        if (id_win_cnt == 8'd255) begin
            // 用滑动窗口而不是永久 OR：上电/切换瞬间的毛刺不会永久污染结论
            id_toggle  <= id_win | (lcd_rgb_i ^ id_prev);
            id_win     <= 24'd0;
            id_win_cnt <= 8'd0;
        end else begin
            id_win_cnt <= id_win_cnt + 8'd1;
        end
    end
end

assign dbg_id_level  = {8'b0, id_level};
assign dbg_id_toggle = {8'b0, id_toggle};
assign dbg_timing    = {9'b0, de_sel, v_sel, h_sel};
assign dbg_tick      = tick_sel;

endmodule
