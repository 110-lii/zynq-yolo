// =============================================================================
// vid_dvp_capture —— OV5640 DVP（RGB565，8-bit 两字节/像素）→ AXI4-Stream RG888
// -----------------------------------------------------------------------------
// 职责：只做「把摄像头口的像素搬进 AXI 世界」，不做缩放/裁切/去噪。
//
// 时序约定（与 OV5640 数据手册的 DVP 输出一致）：
//   * vsync_n 低有效：**下降沿 = 新场开始**，只在这一下清 x/y 计数；
//   * href 高有效：每时钟一个字节；每 2 字节拼成一个像素（高字节先到）；
//   * RGB565 → RGB888 用 **位复制**（不用浮点/移位乘法，综合后是纯连线）：
//       R8 = {R5, R5[4:2]}（5 位最高两位复制到低位）
//       G8 = {G6, G6[5:4]} G6 = {b0[2:0], b1[7:5]}
//       B8 = {B5, B5[4:2]}
//
// 输出流（AXI4-Stream，**一个 packet = 一整帧**）：
//   * tuser = 1 只出现于每场第一个像素（SOF）；
//   * tlast = 1 只出现于每场最后一个像素（EOF，最后一行最后一个像素）；
//   * tdata = **32 位**，高 8 位恒 0，低 24 位 = {R,G,B}。
//     ★ 为什么 tlast 是 EOF 而不是行末 EOL（2026-10-20 上板实测踩出来的）：
//       下游 AXI DMA 用 **simple（无 SG）模式**，它要求 tlast 恰好落在 length 结尾；
//       tlast 提前到达（例如每行末一个）会被判为 Internal Error（DMASR bit4=1），
//       DMA 随即停住不再收数，表现为「FIFO 写满、DMA 看着还在跑但一个字节都不进
//       DDR」。板上实测证据：写 LENGTH=1024 字后读 DMASR=0x00005011
//       （Halted + Int_Err + IOC），第 640 个字（第一行末）就 tlast 了。
//       代价：行边界不再从流里体现 —— 我们不需要（分辨率固定，PS 按 frame_bytes
//       切片），需要时由 PS 用 H_ACTIVE 自行换算。
//     ★ 为什么是 32 位而不是 24 位：下游要接 AXI DMA（S2MM），而 DMA 的
//       stream data width 只支持 8/16/32/64/128…（不支持 24），Xilinx 的
//       axis_dwidth_converter 也不做 24→32。所以在这里补零到 32 位，
//       比在 BD 里塞一个位宽转换器少一级流水、也少一处 CDC 风险。
//       代价：DDR 里每像素占 4 字节（高字节恒 0），PS 侧丢掉高字节即可；
//       黄金模型判定时**只比低 24 位**，并断言高 8 位为 0（多一条自检）。
//
// 背压：DVP 口没有反压能力，所以内部放 16 深像素 FIFO；FIFO 满时置
// `stat_overflow`（粘滞位），**不静默丢**——上板看到 overflow=1 就说明下游慢了。
//
// 复位：同步、高有效。整机里由 inf_reset 把异步复位同步到 pclk 域再喂进来。
// =============================================================================
`timescale 1ns / 1ps
`default_nettype none

module vid_dvp_capture #(
    parameter integer H_ACTIVE = 640,   // 一行有效像素数
    parameter integer V_ACTIVE = 480    // 一场有效行数
) (
    input  wire        clk,             // = dvp_pclk（OV5640 常用 24 MHz）
    input  wire        rst,             // 1=复位
    // DVP 输入
    input  wire        vsync_n,         // 低有效
    input  wire        href,            // 高有效
    input  wire [7:0]  dvp_d,           // RGB565 字节流，高字节先到
    // AXI4-Stream 输出（视频流）
    output wire [31:0] m_axis_tdata,    // 高 8 位恒 0，低 24 位 = {R,G,B}
    output wire        m_axis_tvalid,
    output wire        m_axis_tlast,
    output wire        m_axis_tuser,
    input  wire        m_axis_tready,
    // 状态（给 PS 读的粘滞标志）
    output reg         stat_overflow    // 像素 FIFO 溢出过
);

    localparam integer XW = (H_ACTIVE > 1) ? $clog2(H_ACTIVE) : 1;
    localparam integer YW = (V_ACTIVE > 1) ? $clog2(V_ACTIVE) : 1;

    // ---------------------------------------------------------------- 同步链
    reg vsync_d, href_d;
    always @(posedge clk) begin
        vsync_d <= vsync_n;
        href_d  <= href;
    end

    wire sof = vsync_d && !vsync_n;     // 场同步下降沿

    // -------------------------------------------------------------- 像素拼装
    reg         second;                  // 0=等高字节 1=等低字节
    reg  [7:0]  b_hi;
    reg  [XW-1:0] x_cnt;
    reg  [YW-1:0] y_cnt;
    reg         sof_pending;             // 本场第一个像素还没写

    wire [7:0] r8 = {b_hi[7:3], b_hi[7:5]};
    wire [5:0] g6 = {b_hi[2:0], dvp_d[7:5]};
    wire [7:0] g8 = {g6, g6[5:4]};
    wire [7:0] b8 = {dvp_d[4:0], dvp_d[4:2]};
    wire [23:0] rgb888 = {r8, g8, b8};

    wire eol = (x_cnt == H_ACTIVE[XW-1:0] - 1'b1);
    wire eof = eol && (y_cnt == V_ACTIVE[YW-1:0] - 1'b1);   // 整帧最后一个像素

    // ---------------------------------------------------------------- 像素 FIFO
    localparam integer FIFO_DEPTH = 16;
    localparam integer FIFO_AW    = 4;
    reg  [25:0] fifo [0:FIFO_DEPTH-1];  // {user(SOF), last(EOF), data[23:0]}
    reg  [FIFO_AW:0] wptr, rptr;
    wire [FIFO_AW:0] wptr_inc = wptr + 1'b1;
    wire [FIFO_AW:0] rptr_inc = rptr + 1'b1;
    wire [FIFO_AW:0] cnt = wptr - rptr;
    wire empty  = (cnt == {(FIFO_AW+1){1'b0}});
    wire full   = (cnt == FIFO_DEPTH[FIFO_AW:0]);
    wire px_ok  = href && second;        // 本拍低字节到达 ⇒ 一个像素拼完（不论能否入 FIFO）
    wire wr_en  = px_ok && !full;
    wire rd_pop = !empty && m_axis_tready;

    always @(posedge clk) begin
        if (rst) begin
            wptr <= {(FIFO_AW+1){1'b0}};
            rptr <= {(FIFO_AW+1){1'b0}};
        end else begin
            if (wr_en) begin
                fifo[wptr[FIFO_AW-1:0]] <= {sof_pending, eof, rgb888};
                wptr <= wptr_inc;
            end
            if (rd_pop) begin
                rptr <= rptr_inc;
            end
        end
    end

    assign m_axis_tvalid = !empty;
    // {user, last, rgb888} 在 FIFO 里按 26 位存；出口补零到 32 位（见文件头说明）
    assign {m_axis_tuser, m_axis_tlast} = fifo[rptr[FIFO_AW-1:0]][25:24];
    assign m_axis_tdata = {8'h00, fifo[rptr[FIFO_AW-1:0]][23:0]};

    // ------------------------------------------------------------- 控制/计数
    // 注意：FIFO 满时像素仍要「消费」——降配第二字节、推进 x/y、只丢数据。
    // 否则 second 卡在 1，后续字节的配对会整体错位（比丢一个像素更糟）。
    always @(posedge clk) begin
        if (rst) begin
            second        <= 1'b0;
            b_hi          <= 8'd0;
            x_cnt         <= {XW{1'b0}};
            y_cnt         <= {YW{1'b0}};
            sof_pending   <= 1'b0;
            stat_overflow <= 1'b0;
        end else begin
            if (sof) begin
                second      <= 1'b0;
                x_cnt       <= {XW{1'b0}};
                y_cnt       <= {YW{1'b0}};
                sof_pending <= 1'b1;
            end
            if (px_ok) begin
                second <= 1'b0;
                sof_pending <= 1'b0;
                if (eol) begin
                    x_cnt <= {XW{1'b0}};
                    y_cnt <= eof ? {YW{1'b0}} : (y_cnt + 1'b1);
                end else begin
                    x_cnt <= x_cnt + 1'b1;
                end
                if (full) begin
                    stat_overflow <= 1'b1;
                end
            end else if (href) begin
                b_hi   <= dvp_d;
                second <= 1'b1;
            end
        end
    end

endmodule

`default_nettype wire
