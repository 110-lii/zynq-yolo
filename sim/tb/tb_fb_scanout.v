// ============================================================================
// tb_fb_scanout.v —— fb_scanout 的仿真测试平台（iverilog）
//
// 目的：在没有板子的情况下，验证三件最容易错的事：
//   1) AXI4 读主机的 AR/R 握手（ARVALID 保持到 ARREADY、RLAST/beats 对齐）
//   2) 行缓存按光栅顺序换行（stride != width*4 时也要对）
//   3) 显示侧像素值 == DDR 里的值（允许 1 像素固定偏移与首行欠载）
//
// 内存图案：mem[i] = {8'h5A, line[7:0], px[7:0], 8'h00}
//   => 像素低 24 位 = {line, px, 8'h00}，用 line/px 就能反查是哪个像素。
//
// 判据：仿真结束打印 TB_FB_SCANOUT_OK / TB_FB_SCANOUT_FAIL <原因>
// ============================================================================
`timescale 1ns / 1ps

module tb_fb_scanout;

localparam integer W = 64;     // 每行像素
localparam integer H = 8;      // 行数
localparam integer STRIDE = 320; // 字节/行（> W*4=256，故意留跨距，测 stride 是否正确）
localparam integer BASE = 32'h0010_0000;

reg clk = 1'b0;
reg reset_n = 1'b0;
always #5 clk = ~clk;   // 100 MHz 仿真钟（真实是 74.25，不影响功能）

// ---------------- DUT 连线 ----------------
reg         vs = 1'b0;
reg         de = 1'b0;
reg  [10:0] xpos = 11'd0;
reg  [10:0] ypos = 11'd0;
reg  [31:0] cfg_addr = BASE;
reg  [15:0] cfg_stride = STRIDE;
reg  [10:0] cfg_width = W;
reg         cfg_en = 1'b0;

wire [23:0] pixel_data;
wire [15:0] st_frames;
wire [15:0] st_under;

wire [31:0] m_araddr;
wire [7:0]  m_arlen;
wire [2:0]  m_arsize;
wire [1:0]  m_arburst;
wire        m_arvalid;
wire        m_arready;
wire [31:0] m_rdata;
wire        m_rvalid;
wire        m_rready;
wire        m_rlast;
wire [1:0]  m_rresp = 2'b00;

fb_scanout #(
    .H_ACTIVE(W), .V_ACTIVE(H), .LBUF(256), .BURST(32)
) dut (
    .pclk(clk), .reset_n(reset_n),
    .vs(vs), .de(de), .xpos(xpos), .ypos(ypos),
    .cfg_addr(cfg_addr), .cfg_stride(cfg_stride), .cfg_width(cfg_width), .cfg_en(cfg_en),
    .pixel_data(pixel_data), .st_frames(st_frames), .st_under(st_under),
    .m_araddr(m_araddr), .m_arlen(m_arlen), .m_arsize(m_arsize), .m_arburst(m_arburst),
    .m_arvalid(m_arvalid), .m_arready(m_arready),
    .m_rdata(m_rdata), .m_rvalid(m_rvalid), .m_rready(m_rready), .m_rlast(m_rlast), .m_rresp(m_rresp)
);

// ---------------- AXI4 从机内存模型 ----------------
reg [31:0] mem [0:65535];
reg [31:0] rd_addr;
reg [8:0]  rd_left;
reg        pend;
reg        m_arready_r;
reg        m_rvalid_r;
reg [31:0] m_rdata_r;
reg        m_rlast_r;

assign m_arready = m_arready_r;
assign m_rvalid  = m_rvalid_r;
assign m_rdata   = m_rdata_r;
assign m_rlast   = m_rlast_r;

function [31:0] mem_word(input [31:0] byte_addr);
    reg [7:0] li, px;
    begin
        li = (byte_addr - BASE) / STRIDE;
        px = ((byte_addr - BASE) % STRIDE) / 4;
        mem_word = {8'h5A, li, px, 8'h00};
    end
endfunction

integer k;
initial begin
    for (k = 0; k < 65536; k = k + 1)
        mem[k] = 32'h0;
end

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        m_arready_r <= 1'b1;
        m_rvalid_r  <= 1'b0;
        m_rdata_r   <= 32'd0;
        m_rlast_r   <= 1'b0;
        rd_addr     <= 32'd0;
        rd_left     <= 9'd0;
    end else if (m_arvalid && m_arready_r) begin
        // 接受突发：下一拍就把第一拍数据推出去（流水式，不留空拍）
        rd_addr     <= m_araddr;
        rd_left     <= {1'b0, m_arlen} + 9'd1;
        m_rvalid_r  <= 1'b1;
        m_rdata_r   <= mem_word(m_araddr);
        m_rlast_r   <= (m_arlen == 8'd0);
        m_arready_r <= 1'b0;
    end else if (m_rvalid_r && m_rready) begin
        if (rd_left == 9'd1) begin
            m_rvalid_r  <= 1'b0;
            m_rlast_r   <= 1'b0;
            m_arready_r <= 1'b1;
        end else begin
            rd_left   <= rd_left - 9'd1;
            rd_addr   <= rd_addr + 32'd4;
            m_rdata_r <= mem_word(rd_addr + 32'd4);
            m_rlast_r <= (rd_left == 9'd2);
        end
    end
end

// ---------------- 视频时序发生器 + 检查 ----------------
integer errs = 0;
integer chk = 0;
integer frame, line, px, i;
reg [23:0] exp_pix;
reg [7:0]  pxv;

task do_line(input integer line_no);
    begin
        // 行消隐（在下降沿驱动，避免与采样竞争）
        @(negedge clk);
        de   <= 1'b0;
        xpos <= 11'd0;
        ypos <= line_no[10:0];
        repeat (4) @(negedge clk);
        // 有效像素：xpos 从 2 起（与 video_driver 一致）
        for (px = 0; px < W; px = px + 1) begin
            @(negedge clk);
            de   <= 1'b1;
            xpos <= px + 2;
            @(posedge clk);
            #1;
            // 采样点：本地像素 px 的数据在本次时钟沿后即出现在 pixel_data 上
            if (line_no >= 0) begin
                exp_pix = {line_no[7:0], px[7:0], 8'h00};   // mem_word 的低 24 位
                chk = chk + 1;
                if (pixel_data !== exp_pix) begin
                    errs = errs + 1;
                    if (errs <= 8)
                        $display("MISMATCH line=%0d px=%0d got=%h exp=%h", line_no, px, pixel_data, exp_pix);
                end
            end
        end
        @(negedge clk);
        de <= 1'b0;
    end
endtask

initial begin
    reset_n = 1'b0;
    repeat (3) @(posedge clk);
    reset_n = 1'b1;
    cfg_en  = 1'b1;

    for (frame = 0; frame < 4; frame = frame + 1) begin
        // vs 脉冲
        vs <= 1'b1;
        repeat (2) @(posedge clk);
        vs <= 1'b0;
        // 场消隐：真实 720p30 的场消隐约 25 行（≈430 us），这里给 300 拍，
        // 让取数引擎有足够时间预取前 3 行（否则起始几行欠载是仿真的假象）
        repeat (300) @(posedge clk);
        for (line = 0; line < H; line = line + 1)
            do_line(line);
        repeat (4) @(posedge clk);
    end

    $display("CHECKED=%0d ERRS=%0d UNDER=%0d FRAMES=%0d", chk, errs, st_under, st_frames);
    if (errs == 0 && chk > 100) $display("TB_FB_SCANOUT_OK");
    else                        $display("TB_FB_SCANOUT_FAIL errs=%0d chk=%0d", errs, chk);
    $finish;
end

endmodule
