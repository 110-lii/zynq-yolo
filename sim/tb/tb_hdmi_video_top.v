// ============================================================================
// tb_hdmi_video_top.v —— **顶层**回归：把 hdmi_video_top 整个例化进来
//
// 为什么还要一个 TB（2026-10-31）
// -------------------------------
// 已有的两个 TB 例化的是 `video_driver` + `fb_scanout` **两个子模块**，中间还有一层
// 顶层没人测过，而顶层恰好是上板跑的那一层：
//   · cfg_addr / cfg_pack 的**两拍同步**（跨时钟域，准静态配置）；
//   · `cfg_test`（bit28）把 TMDS 输入切到内置彩条的那一刀；
//   · `st_status` / `st_sum` / `st_dbg` 三个诊断量怎么拼、拼得对不对。
// 其中 `st_sum` 的口径（累加 video_de + video_rgb 还是早一拍的 data_req + pixel_data）
// 恰恰是 2026-10-31 仿真里抓到的第三处缺陷 —— 那次是在本文件出现之前、靠手算发现的，
// 说明这一层缺一把尺子。
//
// 怎么绕开 OSERDES
// ----------------
// `dvi_transmitter_top` 里是 OSERDESE2 原语，iverilog 没有它的行为模型。本 TB **不编译**
// 那个文件，而是在这里提供一个同名同端口的空壳，把 `video_din / video_de` 留给层次化引用
// 观察（`u_top.u_dvi.*`）。被替换的只有「并串转换 + TMDS 编码」这一步，而它在 10-30
// 已经被纯 RTL 彩条图人眼确认过；本 TB 要测的是**送进编码器之前**的那批像素。
//
// 判据
// ----
//   1) `st_sum` 与主机算式（屏幕口径，模 2^32）逐位相等；
//   2) `st_status` 的 frames 在涨、under 为 0；
//   3) `st_dbg` = {last_disp=719, behind=0, under=0}；
//   4) `cfg_test=1` 时送进编码器的是内置彩条：第 0 段按行灰阶、其余段固定色，
//      且与 `u_top.bar_y` 逐像素一致。
//
// 令牌：TB_HDMI_VIDEO_TOP_OK / TB_HDMI_VIDEO_TOP_FAIL
// ============================================================================
`timescale 1ns / 1ps

// ---- 同名同端口的空壳，替代 OSERDES 版 transmitter（见头注释） ----------------
module dvi_transmitter_top(
    input        pclk,
    input        pclk_x5,
    input        reset_n,
    input  [23:0] video_din,
    input        video_hsync,
    input        video_vsync,
    input        video_de,
    output       tmds_clk_p,
    output       tmds_clk_n,
    output [2:0] tmds_data_p,
    output [2:0] tmds_data_n,
    output       tmds_oen
);
    assign tmds_clk_p  = 1'b0;
    assign tmds_clk_n  = 1'b1;
    assign tmds_data_p = 3'b000;
    assign tmds_data_n = 3'b111;
    assign tmds_oen    = 1'b1;
endmodule

module tb_hdmi_video_top;

localparam integer W      = 1280;
localparam integer H      = 720;
localparam integer STRIDE = 5120;
localparam [31:0]  BASE   = 32'h1F00_0000;

reg clk = 1'b0, clk5 = 1'b0, reset_n = 1'b0;
always #6.734  clk  = ~clk;          // 74.25 MHz
always #1.3468 clk5 = ~clk5;         // 371.25 MHz（空壳不用，但端口要动）

// ---- 配置（AXI GPIO 的两路输出，准静态） -------------------------------------
reg [31:0] cfg_addr = BASE;
wire [31:0] cfg_pack;
assign cfg_pack = {3'b000, cfg_test, 1'b1, W[10:0], STRIDE[15:0]};

reg         cfg_test = 1'b0;         // cfg_pack[28] = 内置彩条

wire [31:0] st_status, st_sum, st_dbg;

wire        m_arvalid, m_arready, m_rvalid, m_rready, m_rlast;
wire [31:0] m_araddr, m_rdata;
wire [7:0]  m_arlen;
wire [2:0]  m_arsize;
wire [1:0]  m_arburst;
wire [1:0]  m_rresp;
wire [5:0]  m_arid;
wire [5:0]  m_rid;
assign m_rresp = 2'b00;
assign m_rid   = 6'd0;

hdmi_video_top u_top (
    .pclk        (clk),
    .pclk_x5     (clk5),
    .reset_n     (reset_n),
    .cfg_addr    (cfg_addr),
    .cfg_pack    (cfg_pack),
    .st_status   (st_status),
    .st_sum      (st_sum),
    .st_dbg      (st_dbg),
    .m_axi_araddr (m_araddr),
    .m_axi_arlen  (m_arlen),
    .m_axi_arsize (m_arsize),
    .m_axi_arburst(m_arburst),
    .m_axi_arid   (m_arid),
    .m_axi_arvalid(m_arvalid),
    .m_axi_arready(m_arready),
    .m_axi_rdata  (m_rdata),
    .m_axi_rid    (m_rid),
    .m_axi_rresp  (m_rresp),
    .m_axi_rlast  (m_rlast),
    .m_axi_rvalid (m_rvalid),
    .m_axi_rready (m_rready),
    .tmds_clk_p   (),
    .tmds_clk_n   (),
    .tmds_data_p  (),
    .tmds_data_n  ()
);

wire [23:0] din  = u_top.u_dvi.video_din;
wire        dde  = u_top.u_dvi.video_de;

// ---- AXI 从机模型：固定延迟、突发内 1 拍/周期 ---------------------------------
localparam integer LAT = 8, GAP = 4;
localparam [1:0] SL_IDLE = 2'd0, SL_LAT = 2'd1, SL_DATA = 2'd2, SL_GAP = 2'd3;
reg [1:0]  sl;
reg [31:0] rd_addr;
reg [8:0]  rd_left;
reg [9:0]  cnt;
reg        arready_r, rvalid_r, rlast_r;
reg [31:0] rdata_r;

assign m_arready = arready_r;
assign m_rvalid  = rvalid_r;
assign m_rdata   = rdata_r;
assign m_rlast   = rlast_r;

function [31:0] mem_word(input [31:0] byte_addr);
    reg [31:0] off;
    integer row, col;
    begin
        off = byte_addr - BASE;
        row = off / STRIDE;
        col = (off % STRIDE) / 4;
        mem_word = {8'h00, row[9:0], col[10:0], 3'b000};
    end
endfunction

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        sl <= SL_IDLE; arready_r <= 1; rvalid_r <= 0; rlast_r <= 0;
        rdata_r <= 0; rd_addr <= 0; rd_left <= 0; cnt <= 0;
    end else case (sl)
    SL_IDLE: begin
        arready_r <= 1'b1;
        if (m_arvalid && arready_r) begin
            rd_addr <= m_araddr; rd_left <= {1'b0, m_arlen} + 9'd1;
            arready_r <= 1'b0; cnt <= LAT[9:0]; sl <= SL_LAT;
        end
    end
    SL_LAT: begin
        if (cnt <= 10'd1) begin
            rvalid_r <= 1'b1; rdata_r <= mem_word(rd_addr);
            rlast_r <= (rd_left == 9'd1); sl <= SL_DATA;
        end else cnt <= cnt - 10'd1;
    end
    SL_DATA: begin
        if (rvalid_r && m_rready) begin
            if (rd_left == 9'd1) begin
                rvalid_r <= 1'b0; rlast_r <= 1'b0;
                cnt <= GAP[9:0]; sl <= SL_GAP;
            end else begin
                rd_left <= rd_left - 9'd1; rd_addr <= rd_addr + 32'd4;
                rdata_r <= mem_word(rd_addr + 32'd4);
                rlast_r <= (rd_left == 9'd2);
            end
        end
    end
    SL_GAP: begin
        if (cnt <= 10'd1) sl <= SL_IDLE;
        else             cnt <= cnt - 10'd1;
    end
    endcase
end

// ---- 主机算式（与 build/hdmi_geom_probe.py 的 COLS 口径一致） ------------------
function [31:0] host_sum(input integer pat);
    integer r, c;
    reg [31:0] acc, w;
    begin
        acc = 32'd0;
        for (r = 0; r < H; r = r + 1) begin
            for (c = 0; c < W; c = c + 1) begin
                if (pat == 1)          // 单行白（row 0..9 的伪随机选择，这里用 row 5）
                    w = (r == 5) ? 32'h00FFFFFF : 32'd0;
                else
                    w = {8'h00, r[9:0], c[10:0], 3'b000};
                acc = acc + w;                  // 屏幕列 C ↔ 源列 C
            end
        end
        host_sum = acc;
    end
endfunction

// ---- 彩条核验（cfg_test=1 时） ------------------------------------------------
integer bar_errs = 0, bar_chk = 0;
reg [23:0] bar_exp;
always @(posedge clk) begin
    if (reset_n && cfg_test && dde && (u_top.bar_x > 11'd0)) begin
        // bar_x/bar_y 是**上一拍**的值（本拍 din 对应的是寄存器里的 bar_rgb），
        // 所以期望用 bar_rgb 本身：这里只要求「din 等于某个合法的条色」，
        // 且第 0 段必须与 bar_y 相同（行推进的证据）。
        bar_chk = bar_chk + 1;
        case (u_top.bar_x[9:7])
            3'd0: bar_exp = {u_top.bar_y[7:0], u_top.bar_y[7:0], u_top.bar_y[7:0]};
            3'd1: bar_exp = 24'hFFFF00;
            3'd2: bar_exp = 24'h00FFFF;
            3'd3: bar_exp = 24'h00FF00;
            3'd4: bar_exp = 24'hFF00FF;
            3'd5: bar_exp = 24'hFF0000;
            3'd6: bar_exp = 24'h0000FF;
            default: bar_exp = 24'hFFFFFF;
        endcase
        if (din !== bar_exp) begin
            bar_errs = bar_errs + 1;
            if (bar_errs <= 8)
                $display("BAR_MISMATCH bar_x=%0d bar_y=%0d din=%06h exp=%06h",
                         u_top.bar_x, u_top.bar_y, din, bar_exp);
        end
    end
end

// ---- 主流程 ------------------------------------------------------------------
// 4 帧走 DDR 帧缓存（累加和/诊断量的口径），再切 cfg_test=1 看 2 帧内置彩条。
integer f, frame = 0;
integer frames0, frames1, under1, last_disp, behind, under_l;
reg [31:0] sum_fb;
initial begin
    reset_n = 0;
    repeat (8) @(posedge clk);
    reset_n = 1;
    cfg_test = 1'b0;

    for (f = 0; f < 4; f = f + 1) begin
        @(posedge u_top.video_vs);
        frame = frame + 1;
        repeat (1640 * 4) @(posedge clk);   // 进到场消隐
    end
    sum_fb   = st_sum;                      // 上一整帧（ENC 图案）的累加和
    frames0  = st_status[31:16];
    last_disp = st_dbg[31:22]; behind = st_dbg[21:11]; under_l = st_dbg[10:0];

    cfg_test = 1'b1;                        // 切到内置彩条，只验 din，不动 DDR 口径
    for (f = 0; f < 2; f = f + 1) begin
        @(posedge u_top.video_vs);
        frame = frame + 1;
        repeat (1640 * 4) @(posedge clk);
    end
    frames1 = st_status[31:16];
    under1  = st_status[15:0];

    $display("SUM st_sum=%08X host=%08X", sum_fb, host_sum(0));
    $display("STATUS frames=%0d->%0d under=%0d", frames0, frames1, under1);
    $display("DBG under=%0d behind=%0d last_disp=%0d", under_l, behind, last_disp);
    $display("BAR chk=%0d errs=%0d", bar_chk, bar_errs);

    if ((sum_fb == host_sum(0)) && (frames1 != frames0) && (under1 == 0) &&
        (under_l == 0) && (behind == 0) && (last_disp == 719) &&
        (bar_chk > 100000) && (bar_errs == 0))
        $display("TB_HDMI_VIDEO_TOP_OK");
    else
        $display("TB_HDMI_VIDEO_TOP_FAIL sum=%08X host=%08X frames=%0d->%0d under=%0d dbg(%0d,%0d,%0d) bar=%0d/%0d",
                 sum_fb, host_sum(0), frames0, frames1, under1,
                 under_l, behind, last_disp, bar_errs, bar_chk);
    $finish;
end

endmodule
