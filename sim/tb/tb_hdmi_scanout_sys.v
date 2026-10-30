// ============================================================================
// tb_hdmi_scanout_sys.v —— HDMI 扫描输出的**系统级**回归（真的 video_driver）
//
// 为什么又加一个 TB（2026-10-30 夜里，第二次上板仍然只有左边一条白）
// --------------------------------------------------------------------
// `tb_fb_scanout_frame` 里 de / xpos 是 TB 自己造的，虽然按真机取值域驱动，
// 但**相位是我推的**。真机是 video_driver 内部：
//
//     data_req   <= 条件(cnt_h, cnt_v)          // 镜后一拍
//     pixel_xpos <= data_req ? cnt_h+2-260 : 0  // 再镜后一拍，读的是上一拍的 data_req
//     video_en   <= data_req                    // 又是镜后一拍
//     video_rgb   = video_en ? pixel_data : 0
//
// 三者互差一拍，任何一处推错，「哪一行送到哪一行」就是错的，而逐像素比对用的
// 期望值也会跟着错——于是小 TB 全绿、上板全土。本 TB 不再自己造时序，
// **直接例化真的 video_driver**，只把 TMDS 编码器换成像素采样器。
//
// 判据
// ----
//   1) 逐像素：显示到屏幕第 (r, c) 个位置时，pixel_data 必须等于
//      enc(row=r, col=max(c-2,0))（内存图案把行列编进像素低 24 位）；
//   2) 帧像素累加和（与 hdmi_video_top 的 st_sum 同口径）必须与主机算式逐位相等 ——
//      这条是上板 `DIAG_SUM` 的同一把尺子，仿真与实物共用同一个判据；
//   3) 帧末 disp_line/fetch_line 快照：应为本帧行数 / 行数 / 0 行未就绪。
//
// 令牌：TB_HDMI_SCANOUT_SYS_OK / TB_HDMI_SCANOUT_SYS_FAIL
// ============================================================================
`timescale 1ns / 1ps

module tb_hdmi_scanout_sys;

localparam integer W      = 1280;
localparam integer H      = 720;
localparam integer STRIDE = 5120;          // = W*4，与主机 pack_frame 一致
localparam integer LBUF   = 2048;
localparam integer BURST  = 64;
integer lat_cfg = 8;                       // AR → 首拍 R（可 +LAT=n 覆盖）
integer gap_cfg = 4;                       // 突发间空档（可 +GAP=n 覆盖）
localparam [31:0]  BASE   = 32'h1F00_0000;

localparam integer PAT_ENC    = 0;         // 行列编码（逐像素查）
localparam integer PAT_BLOCK  = 1;         // 左上 128x128 白块（与上板 DIAG 同图）
localparam integer PAT_ROWPTR = 2;         // 单行全白（+ROW=n）：问「源行 n 被显示了几次」
localparam integer PAT_COLPTR = 3;         // 单列全白（+COL=n）：问「源列 n 被显示了几次」

integer pattern = PAT_ENC;
integer prow    = -1;                      // +ROW=n
integer pcol    = -1;                      // +COL=n
integer frames_cfg = 4;                    // +FRAMES=n（单行/单列探针只需 2 帧）

reg clk = 1'b0;
reg reset_n = 1'b0;
always #6.734 clk = ~clk;                  // 74.25 MHz

// ---------------- video_driver ----------------
wire        video_hs, video_vs, video_de, data_req;
wire [23:0] video_rgb;
wire [23:0] pixel_data;
wire [10:0] pixel_xpos, pixel_ypos;

video_driver u_vd (
    .pixel_clk  (clk),
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

// ---------------- fb_scanout ----------------
reg  [31:0] cfg_addr   = BASE;
reg  [15:0] cfg_stride = STRIDE;
reg  [10:0] cfg_width  = W;
reg         cfg_en     = 1'b0;

wire [15:0] st_frames, st_under;
wire [31:0] st_dbg;
wire [31:0] m_araddr;
wire [7:0]  m_arlen;
wire [2:0]  m_arsize;
wire [1:0]  m_arburst;
wire        m_arvalid, m_arready;
wire [31:0] m_rdata;
wire        m_rvalid, m_rready, m_rlast;
wire [1:0]  m_rresp = 2'b00;

fb_scanout #(
    .H_ACTIVE(W), .V_ACTIVE(H), .LBUF(LBUF), .BURST(BURST)
) u_fb (
    .pclk(clk), .reset_n(reset_n),
    .vs(video_vs), .de(data_req), .xpos(pixel_xpos), .ypos(pixel_ypos),
    .cfg_addr(cfg_addr), .cfg_stride(cfg_stride), .cfg_width(cfg_width), .cfg_en(cfg_en),
    .pixel_data(pixel_data), .st_frames(st_frames), .st_under(st_under), .st_dbg(st_dbg),
    .m_araddr(m_araddr), .m_arlen(m_arlen), .m_arsize(m_arsize), .m_arburst(m_arburst),
    .m_arvalid(m_arvalid), .m_arready(m_arready),
    .m_rdata(m_rdata), .m_rvalid(m_rvalid), .m_rready(m_rready),
    .m_rlast(m_rlast), .m_rresp(m_rresp)
);

// ---------------- 与 hdmi_video_top 同口径的帧累加和 ----------------
reg [31:0] px_sum, st_sum_r;
reg        vs_d_sum;
always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        px_sum <= 0; st_sum_r <= 0; vs_d_sum <= 0;
    end else begin
        vs_d_sum <= video_vs;
        if (video_vs & ~vs_d_sum) begin
            px_sum   <= 32'd0;
            st_sum_r <= px_sum;
        end else if (video_de) begin                 // 与 hdmi_video_top 同口径
            px_sum <= px_sum + {8'd0, video_rgb};
        end
    end
end

// ---------------- 存储器模型 ----------------
function [31:0] mem_word(input [31:0] byte_addr);
    reg [31:0] off;
    integer row, col;
    begin
        off = byte_addr - BASE;
        row = off / STRIDE;
        col = (off % STRIDE) / 4;
        if (pattern == PAT_BLOCK) begin
            mem_word = ((row < 128) && (col < 128)) ? 32'h00FFFFFF : 32'd0;
        end else if (pattern == PAT_ROWPTR) begin
            mem_word = (row == prow) ? 32'h00FFFFFF : 32'd0;
        end else if (pattern == PAT_COLPTR) begin
            mem_word = (col == pcol) ? 32'h00FFFFFF : 32'd0;
        end else begin
            mem_word = {8'h00, row[9:0], col[10:0], 3'b000};
        end
    end
endfunction

// 主机算式（与 tools/hdmi_video.py + build/hdmi_ddr_diag.py 的 COLS_DEDUP 口径一致，模 2^32）
//   DUT 累加发生在 data_req 拍上，此时 xpos = 0..1279、local_x = 0,0,0,1,...,1277：
//   第 0 列被采 3 次，第 1278/1279 列永远采不到 —— 主机必须用同一个列映射算期望值，
//   否则两边差的不是「读错了」而是「压根不是同一批像素」。上板 DIAG 里那 0.8% 就是这么来的。
function [31:0] host_sum(input integer pat);
    integer r, c;
    reg [31:0] acc, w;
    begin
        acc = 32'd0;
        for (r = 0; r < H; r = r + 1) begin
            for (c = 0; c < W; c = c + 1) begin
                if (pat == PAT_BLOCK)
                    w = ((r < 128) && (c < 128)) ? 32'h00FFFFFF : 32'd0;
                else if (pat == PAT_ROWPTR)
                    w = (r == prow) ? 32'h00FFFFFF : 32'd0;
                else if (pat == PAT_COLPTR)
                    w = (c == pcol) ? 32'h00FFFFFF : 32'd0;
                else
                    w = {8'h00, r[9:0], c[10:0], 3'b000};
                if (c == 0)      acc = acc + w + w + w;   // 显示列 0/1/2 都是源列 0
                else if (c < W-2) acc = acc + w;          // 源列 1..1277
            end
        end
        host_sum = acc;
    end
endfunction

localparam [1:0] SL_IDLE = 2'd0, SL_LAT = 2'd1, SL_DATA = 2'd2, SL_GAP = 2'd3;
reg [1:0]  sl;
reg [31:0] rd_addr;
reg [8:0]  rd_left;
reg [9:0]  cnt;
reg        m_arready_r, m_rvalid_r, m_rlast_r;
reg [31:0] m_rdata_r;

assign m_arready = m_arready_r;
assign m_rvalid  = m_rvalid_r;
assign m_rdata   = m_rdata_r;
assign m_rlast   = m_rlast_r;

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        sl <= SL_IDLE; m_arready_r <= 1; m_rvalid_r <= 0; m_rlast_r <= 0;
        m_rdata_r <= 0; rd_addr <= 0; rd_left <= 0; cnt <= 0;
    end else case (sl)
    SL_IDLE: begin
        m_arready_r <= 1'b1;
        if (m_arvalid && m_arready_r) begin
            rd_addr <= m_araddr; rd_left <= {1'b0, m_arlen} + 9'd1;
            m_arready_r <= 1'b0; cnt <= lat_cfg[9:0]; sl <= SL_LAT;
        end
    end
    SL_LAT: begin
        if (cnt <= 10'd1) begin
            m_rvalid_r <= 1'b1; m_rdata_r <= mem_word(rd_addr);
            m_rlast_r <= (rd_left == 9'd1); sl <= SL_DATA;
        end else cnt <= cnt - 10'd1;
    end
    SL_DATA: begin
        if (m_rvalid_r && m_rready) begin
            if (rd_left == 9'd1) begin
                m_rvalid_r <= 1'b0; m_rlast_r <= 1'b0;
                cnt <= gap_cfg[9:0]; sl <= SL_GAP;
            end else begin
                rd_left <= rd_left - 9'd1; rd_addr <= rd_addr + 32'd4;
                m_rdata_r <= mem_word(rd_addr + 32'd4);
                m_rlast_r <= (rd_left == 9'd2);
            end
        end
    end
    SL_GAP: begin
        if (cnt <= 10'd1) sl <= SL_IDLE;
        else             cnt <= cnt - 10'd1;
    end
    endcase
end

// ---------------- 逐像素比对（在 video_de 拍上看 video_rgb） ----------------
integer errs = 0, chk = 0, frame = 0;
integer srow, scol, ssrc;
reg [23:0] exp_pix;

always @(posedge clk) begin
    if (reset_n && video_de && (cfg_en) && (frame > 0)) begin
        srow = u_vd.cnt_v - 25;                        // 屏幕行
        scol = u_vd.cnt_h - 260;                       // 屏幕列
        if ((srow >= 0) && (srow < H) && (scol >= 0) && (scol < W)) begin
            ssrc = (scol < 2) ? 0 : (scol - 2);        // DUT 内部 local_x = xpos-2
            exp_pix = {srow[9:0], ssrc[10:0], 3'b000};
            if (pattern == PAT_BLOCK)
                exp_pix = ((srow < 128) && (scol < 130)) ? 24'hFFFFFF : 24'h000000;
            else if (pattern == PAT_ROWPTR)
                exp_pix = (srow == prow) ? 24'hFFFFFF : 24'h000000;
            else if (pattern == PAT_COLPTR)
                exp_pix = (ssrc == pcol) ? 24'hFFFFFF : 24'h000000;
            chk = chk + 1;
            if (video_rgb !== exp_pix) begin
                errs = errs + 1;
                if (errs <= 12)
                    $display("MISMATCH row=%0d col=%0d got=%06h exp=%06h xpos=%0d disp_line=%0d",
                             srow, scol, video_rgb, exp_pix, pixel_xpos, u_fb.disp_line);
            end
        end
    end
end

// ---------------- 主流程 ----------------
integer f;
integer cnt_rec, cnt_exp, n;
reg [63:0] tmp64;
initial begin
    reset_n = 0;
    repeat (8) @(posedge clk);
    reset_n = 1;

    for (f = 0; f < frames_cfg; f = f + 1) begin
        @(posedge video_vs);
        frame = frame + 1;
        $display("VS frame=%0d st_frames=%0d st_under=%0d sum=%08X dbg=%08X",
                 frame, st_frames, st_under, st_sum_r, st_dbg);
        repeat (1640 * 4) @(posedge clk);   // 进到场消隐后再开 en，避免首帧假象
        cfg_en = 1'b1;
    end
    @(posedge video_vs);
    $display("FINAL frame=%0d st_frames=%0d under=%0d sum=%08X host_sum=%08X dbg=%08X",
             frame, st_frames, st_under, st_sum_r, host_sum(pattern), st_dbg);
    $display("CHECKED=%0d ERRS=%0d", chk, errs);
    $display("DBG under_lines=%0d behind_lines=%0d last_disp=%0d",
             st_dbg[10:0], st_dbg[21:11], st_dbg[31:22]);

    // 单行/单列探针的**反解**：上板 DIAG_ROWPTR/DIAG_COLPTR 用同一个算式，这里先证明它可逆。
    //   word = 0x00FFFFFF（奇数，与 2^32 互素）⇒ 乘法在模 2^32 下是双射，
    //   sum = (count × 每行/每列像素数 × word) mod 2^32 可以**唯一**反解出 count。
    //   这里直接穷举 count（≤720/1280 次 64 位乘法），与主机侧 `pow(w,-1,2**32)` 等价 ——
    //   2026-10-31 第一版按「word ≡ -1」写成 sum=-1280*count，列探针当场算出 1,887,438
    //   （因为 0x00FFFFFF ≠ 0xFFFFFFFF，只有 1280×2^24 恰好 ≡ 0 时行探针才凑巧对）。
    if (pattern == PAT_ROWPTR || pattern == PAT_COLPTR) begin
        cnt_rec = -1;
        for (n = 0; n <= H; n = n + 1) begin
            tmp64 = (pattern == PAT_ROWPTR)
                  ? (n * W) * 64'd16777215     // 一行白 = 1280 个白像素
                  : (n * H) * 64'd16777215;    // 一列白 = 720 个白像素
            if (tmp64[31:0] == st_sum_r) cnt_rec = n;
        end
        cnt_exp = (pattern == PAT_ROWPTR) ? 1
                : ((pcol == 0) ? 3 : ((pcol < W-2) ? 1 : 0));
        $display("PTR %s=%0d sum=%08X recovered_count=%0d expected=%0d %s",
                 (pattern == PAT_ROWPTR) ? "row" : "col",
                 (pattern == PAT_ROWPTR) ? prow : pcol,
                 st_sum_r, cnt_rec, cnt_exp,
                 (cnt_rec == cnt_exp) ? "OK" : "FAIL");
    end
    if ((errs == 0) && (chk > 800000) && (st_sum_r == host_sum(pattern)))
        $display("TB_HDMI_SCANOUT_SYS_OK");
    else
        $display("TB_HDMI_SCANOUT_SYS_FAIL errs=%0d chk=%0d sum=%08X host=%08X",
                 errs, chk, st_sum_r, host_sum(pattern));
    $finish;
end

integer ok;
initial begin
    #1;  // 让 pattern 在 t=0 之后稳定
    pattern = PAT_ENC;
    if ($test$plusargs("block"))  pattern = PAT_BLOCK;
    if ($test$plusargs("rowptr")) pattern = PAT_ROWPTR;
    if ($test$plusargs("colptr")) pattern = PAT_COLPTR;
    ok = $value$plusargs("ROW=%d", prow);
    ok = $value$plusargs("COL=%d", pcol);
    ok = $value$plusargs("FRAMES=%d", frames_cfg);
    ok = $value$plusargs("LAT=%d", lat_cfg);
    ok = $value$plusargs("GAP=%d", gap_cfg);
    $display("PATTERN=%s row=%0d col=%0d FRAMES=%0d LAT=%0d GAP=%0d",
             (pattern == PAT_BLOCK) ? "BLOCK" : (pattern == PAT_ROWPTR) ? "ROWPTR" :
             (pattern == PAT_COLPTR) ? "COLPTR" : "ENC",
             prow, pcol, frames_cfg, lat_cfg, gap_cfg);
end

endmodule
