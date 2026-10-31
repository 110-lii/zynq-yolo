// ============================================================================
// tb_fb_scanout_frame.v —— 整帧 + 真实时序 + 真实 AXI 延迟的扫描输出回归
//
// 为什么还要有一个整帧 TB（2026-10-30 上板后补）
// ------------------------------------------------
// `tb_fb_scanout` 只跑 64×8，行消隐只有 4 拍、AXI 从机**零延迟**，于是：
//   · 行缓存的 4 条环形队列只转过两圈；
//   · 取数引擎永远「提前完成」，行首未就绪这类退化根本不会出现；
//   · 每行留给取数的时间 = 有效像素时间（真机是 有效 + 行消隐，约 1650 拍）。
// 上板实测：`frames` 60/s、`under=0`、DDR 读回数据逐位正确，画面却是一团乱
// ——「哪一行被送到屏幕的哪一行」这件事，小 TB 从来没测过。
//
// 本 TB 按真机规模搭：
//   · 1280×720，行消隐 370 拍（真机 720p 一行 1650 拍，有效 1280）；
//   · AXI 从机每突发有 LAT 拍起始延迟、突发之间有 GAP 拍空档，**数据拍 1 拍/字**
//     —— 一行 20 个 64 拍突发，20×(1+8+64+4)=1540 拍，卡在 1650 拍/行的预算里，
//     余量只剩 110 拍，这就是这块板子上真实存在的余量；
//   · 内存图案把 (行, 列) 编进像素低 24 位：{row[9:0], col[10:0], 3'b000}，
//     任何一个像素被送到错的行/列都会当场对不上。
//
// 判据：TB_FB_SCANOUT_FRAME_OK / TB_FB_SCANOUT_FRAME_FAIL
// ============================================================================
`timescale 1ns / 1ps

module tb_fb_scanout_frame;

localparam integer W      = 1280;
localparam integer H      = 720;
localparam integer STRIDE = 5632;          // 字节/行（> W*4=5120，故意留跨距）
localparam integer LBUF   = 2048;
localparam integer BURST  = 64;
localparam integer LAT    = 8;             // AR 握手到第一个 R 拍
localparam integer GAP    = 4;             // 突发之间的空档
localparam integer HBLANK = 370;           // 行消隐拍数
localparam integer VBLANK = 30;            // 场消隐行数
localparam [31:0]  BASE   = 32'h3800_0000;

reg clk = 1'b0;
reg reset_n = 1'b0;
always #5 clk = ~clk;

reg         vs = 1'b0;
reg         de = 1'b0;
reg  [10:0] xpos = 11'd0;
reg  [10:0] ypos = 11'd0;
reg  [31:0] cfg_addr   = BASE;
reg  [15:0] cfg_stride = STRIDE;
reg  [10:0] cfg_width  = W;
reg         cfg_en     = 1'b0;

wire [23:0] pixel_data;
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
) dut (
    .pclk(clk), .reset_n(reset_n),
    .vs(vs), .de(de), .xpos(xpos), .ypos(ypos),
    .cfg_addr(cfg_addr), .cfg_stride(cfg_stride), .cfg_width(cfg_width), .cfg_en(cfg_en),
    .pixel_data(pixel_data), .st_frames(st_frames), .st_under(st_under), .st_dbg(st_dbg),
    .m_araddr(m_araddr), .m_arlen(m_arlen), .m_arsize(m_arsize), .m_arburst(m_arburst),
    .m_arvalid(m_arvalid), .m_arready(m_arready),
    .m_rdata(m_rdata), .m_rvalid(m_rvalid), .m_rready(m_rready),
    .m_rlast(m_rlast), .m_rresp(m_rresp)
);

// ---------------------------------------------------------------------------
// AXI4 从机：带延迟的存储器模型（内容按地址算，不占内存）
// ---------------------------------------------------------------------------
function [31:0] mem_word(input [31:0] byte_addr);
    reg [31:0] off;
    reg [10:0] row, col;
    begin
        off = byte_addr - BASE;
        row = off / STRIDE;
        col = (off % STRIDE) / 4;
        mem_word = {8'h00, row[9:0], col[10:0], 3'b000};
    end
endfunction

localparam [1:0] SL_IDLE = 2'd0;   // 收 AR
localparam [1:0] SL_LAT  = 2'd1;   // 起始延迟
localparam [1:0] SL_DATA = 2'd2;   // 1 拍/字回数据
localparam [1:0] SL_GAP  = 2'd3;   // 突发之间空档

reg [1:0]  sl;
reg [31:0] rd_addr;
reg [8:0]  rd_left;
reg [4:0]  cnt;
reg        m_arready_r, m_rvalid_r, m_rlast_r;
reg [31:0] m_rdata_r;

assign m_arready = m_arready_r;
assign m_rvalid  = m_rvalid_r;
assign m_rdata   = m_rdata_r;
assign m_rlast   = m_rlast_r;

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        sl          <= SL_IDLE;
        m_arready_r <= 1'b1;
        m_rvalid_r  <= 1'b0;
        m_rlast_r   <= 1'b0;
        m_rdata_r   <= 32'd0;
        rd_addr     <= 32'd0;
        rd_left     <= 9'd0;
        cnt         <= 5'd0;
    end else begin
        case (sl)
        SL_IDLE: begin
            m_arready_r <= 1'b1;
            if (m_arvalid && m_arready_r) begin
                rd_addr     <= m_araddr;
                rd_left     <= {1'b0, m_arlen} + 9'd1;
                m_arready_r <= 1'b0;
                cnt         <= LAT[4:0];
                sl          <= SL_LAT;
            end
        end
        SL_LAT: begin
            if (cnt <= 5'd1) begin
                m_rvalid_r <= 1'b1;
                m_rdata_r  <= mem_word(rd_addr);
                m_rlast_r  <= (rd_left == 9'd1);
                sl         <= SL_DATA;
            end else begin
                cnt <= cnt - 5'd1;
            end
        end
        SL_DATA: begin
            if (m_rvalid_r && m_rready) begin
                if (rd_left == 9'd1) begin
                    m_rvalid_r <= 1'b0;
                    m_rlast_r  <= 1'b0;
                    cnt        <= GAP[4:0];
                    sl         <= SL_GAP;
                end else begin
                    rd_left   <= rd_left - 9'd1;
                    rd_addr   <= rd_addr + 32'd4;
                    m_rdata_r <= mem_word(rd_addr + 32'd4);
                    m_rlast_r <= (rd_left == 9'd2);
                end
            end
        end
        SL_GAP: begin
            if (cnt <= 5'd1) sl <= SL_IDLE;
            else             cnt <= cnt - 5'd1;
        end
        endcase
    end
end

// ---------------------------------------------------------------------------
// 视频时序 + 逐像素检查
// ---------------------------------------------------------------------------
integer errs = 0;
integer chk = 0;
integer frame, line, px;
integer bad_lines = 0;
integer line_errs;
reg [23:0] exp_pix;
reg [10:0] src;

task do_active_line(input integer lno, input integer do_check);
    begin
        line_errs = 0;
        for (px = 0; px < W; px = px + 1) begin
            @(negedge clk);
            de   = 1'b1;
            xpos = px[10:0];
            ypos = lno[10:0] + 11'd1;      // video_driver 的 ypos 是 1 起算
            @(posedge clk);
            #1;
            if (do_check) begin
                src     = px[10:0];            // DUT 读地址 = xpos ⇒ 第 px 拍拿到源列 px
                exp_pix = {lno[9:0], src, 3'b000};
                chk = chk + 1;
                if (pixel_data !== exp_pix) begin
                    errs = errs + 1;
                    line_errs = line_errs + 1;
                    if (errs <= 10)
                        $display("MISMATCH line=%0d xpos=%0d src=%0d got=%06h exp=%06h",
                                 lno, px, src, pixel_data, exp_pix);
                end
            end
        end
        @(negedge clk);
        de <= 1'b0;
        if (do_check && line_errs > 0) begin
            if (bad_lines < 12)
                $display("BADLINE line=%0d errs=%0d disp_line=%0d ar_line=%0d wr_line=%0d ready=%b",
                         lno, line_errs, dut.disp_line, dut.ar_line, dut.wr_line, dut.ready);
            bad_lines = bad_lines + 1;
        end
    end
endtask

task do_blank_line(input integer lno);
    begin
        @(negedge clk);
        de   = 1'b0;
        xpos = 11'd0;
        repeat (HBLANK) @(negedge clk);
    end
endtask

initial begin
    reset_n = 1'b0;
    repeat (5) @(posedge clk);
    reset_n = 1'b1;
    cfg_en  = 1'b1;

    for (frame = 0; frame < 3; frame = frame + 1) begin
        vs <= 1'b1;
        ypos = 11'd0;                     // 场消隐期 ypos = 0（video_driver 同款）
        repeat (2) @(posedge clk);
        vs <= 1'b0;
        // 场消隐：真机约 25 行，这里给足，让预取把环形队列填满
        repeat (VBLANK * HBLANK) @(negedge clk);
        for (line = 0; line < H; line = line + 1) begin
            // 第 0 帧不查（刚上电，首行的预取还没完成），后面两帧逐像素查
            do_active_line(line, frame > 0);
            do_blank_line(line);
        end
        $display("FRAME=%0d  last_disp_line=%0d behind_lines=%0d under_lines=%0d under=%0d",
                 frame, st_dbg[31:22], st_dbg[21:11], st_dbg[10:0], st_under);
    end

    $display("CHECKED=%0d ERRS=%0d BAD_LINES=%0d", chk, errs, bad_lines);
    $display("DBG last_disp_line=%0d behind_lines=%0d under_lines=%0d",
             st_dbg[31:22], st_dbg[21:11], st_dbg[10:0]);
    if (errs == 0 && chk > 100000 && st_dbg[31:22] == (H - 1) && st_dbg[10:0] == 11'd0)
        $display("TB_FB_SCANOUT_FRAME_OK");
    else
        $display("TB_FB_SCANOUT_FRAME_FAIL errs=%0d bad_lines=%0d last_disp_line=%0d under_lines=%0d",
                 errs, bad_lines, st_dbg[31:22], st_dbg[10:0]);
    $finish;
end

endmodule
