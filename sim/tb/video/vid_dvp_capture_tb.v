// =============================================================================
// vid_dvp_capture_tb —— DVP→AXIS 捕获的定向测试台
// -----------------------------------------------------------------------------
// 被测：src/rtl/video/vid_dvp_capture.v（H_ACTIVE=4 / V_ACTIVE=3，缩小以便穷举）
//
// 检查项：
//   1. 像素搬运：H*V 个像素一个不少、顺序对；
//   2. RGB565→RGB888 的位复制直译与独立公式一致（四种颜色：红/绿/蓝/白）；
//   3. tuser 只出现在每场第一个像素；tlast 出现在每行最后一个像素；
//   4. 背压：整行不 ready 时像素先攒 FIFO（16 深），松手后不丢不乱；
//   5. 溢出：送 V+2 行（像素数 > FIFO 深度）且整场不 ready ⇒ stat_overflow=1
//      （粘滞位，不静默丢）。
//
// 判据：打印 VID_DVP_CAPTURE_TB_PASS（errors=0）
// 跑法：python sim/scripts/run_sim.py --only vid_dvp_capture
//      （或手工：iverilog -g2012 -o build/sim/v.vvp src/rtl/video/vid_dvp_capture.v
//        sim/tb/video/vid_dvp_capture_tb.v && vvp build/sim/v.vvp）
// 调试：vvp build/sim/v.vvp +trace  打开逐像素 TRACE 打印
// =============================================================================
`timescale 1ns / 1ps
`default_nettype none

module vid_dvp_capture_tb;

    localparam integer H = 4;
    localparam integer V = 3;

    reg         clk = 1'b0;
    reg         rst = 1'b1;
    reg         vsync_n = 1'b1;
    reg         href    = 1'b0;
    reg  [7:0]  dvp_d   = 8'd0;
    reg         tready  = 1'b0;

    wire [31:0] tdata;          // 高 8 位恒 0，低 24 位 = RGB888
    wire        tvalid;
    wire        tlast;
    wire        tuser;
    wire        stat_overflow;

    vid_dvp_capture #(.H_ACTIVE(H), .V_ACTIVE(V)) dut (
        .clk(clk), .rst(rst),
        .vsync_n(vsync_n), .href(href), .dvp_d(dvp_d),
        .m_axis_tdata(tdata), .m_axis_tvalid(tvalid),
        .m_axis_tlast(tlast), .m_axis_tuser(tuser),
        .m_axis_tready(tready),
        .stat_overflow(stat_overflow)
    );

    always #21 clk = ~clk;      // ≈23.8 MHz，接近 OV5640 的 24 MHz

    // ------------------------------------------------------------------ 检查器
    integer pix_total = 0;      // 收到的像素总数
    integer user_cnt  = 0;
    integer errors    = 0;
    integer chk_expected = 1;   // 1=比对颜色（帧 3 整场丢弃时不比）

    // 独立参考公式（不复用 DUT 的写法：移位/位复制算 565→888）
    function [23:0] ref_rgb(input [7:0] hi, input [7:0] lo);
        reg [5:0] g6;
        begin
            g6 = {hi[2:0], lo[7:5]};
            ref_rgb = {hi[7:3], hi[7:5], g6, g6[5:4], lo[4:0], lo[4:2]};
        end
    endfunction

    function [7:0] pat_hi(input integer idx);
        case (idx)
            0: pat_hi = 8'hF8;  // 红
            1: pat_hi = 8'h07;  // 绿
            2: pat_hi = 8'h00;  // 蓝
            default: pat_hi = 8'hFF; // 白
        endcase
    endfunction

    function [7:0] pat_lo(input integer idx);
        case (idx)
            0: pat_lo = 8'h00;
            1: pat_lo = 8'hE0;
            2: pat_lo = 8'h1F;
            default: pat_lo = 8'hFF;
        endcase
    endfunction

    // 每个被接受的像素做检查（在时钟上升沿采样，检查的是握手成功那一拍的数据）
    integer pix_in_frame;
    integer exp_idx;
    reg [23:0] exp_rgb;
    always @(posedge clk) begin
        if (tvalid && tready) begin
            pix_in_frame = pix_total % (H*V);
            if (pix_in_frame == 0) begin
                if (!tuser) begin
                    $display("FAIL tuser@%0d 应为 1", pix_total);
                    errors = errors + 1;
                end else begin
                    user_cnt = user_cnt + 1;
                end
            end else if (tuser) begin
                $display("FAIL tuser@%0d 只该在帧首", pix_total);
                errors = errors + 1;
            end
            if (tlast != ((pix_in_frame % H) == H-1)) begin
                $display("FAIL tlast@%0d", pix_total);
                errors = errors + 1;
            end
            if (chk_expected) begin
                exp_idx = pix_in_frame % 4;      // H=4 时行内位置 %4 即图案序号
                exp_rgb = ref_rgb(pat_hi(exp_idx), pat_lo(exp_idx));
                if (tdata[23:0] !== exp_rgb) begin
                    $display("FAIL 颜色@%0d 得到 %06h 期望 %06h",
                             pix_total, tdata[23:0], exp_rgb);
                    errors = errors + 1;
                end
                // 出口是 32 位、高 8 位补零（为了接只支持 8/16/32/64 的 AXI DMA）
                if (tdata[31:24] !== 8'h00) begin
                    $display("FAIL 高字节@%0d 得到 %02h 期望 00", pix_total, tdata[31:24]);
                    errors = errors + 1;
                end
            end
            pix_total = pix_total + 1;
        end
    end

    // ------------------------------------------------------------------ 激励任务
    task start_frame;
        begin
            @(negedge clk); vsync_n <= 1'b0; href <= 1'b0; dvp_d <= 8'd0;
            repeat (4) @(posedge clk);
            @(negedge clk); vsync_n <= 1'b1;
            repeat (2) @(posedge clk);
        end
    endtask

    // 在 negedge 改数据、posedge 被采样：一个像素占 2 拍（高字节 → 低字节）
    task drive_pixel(input [7:0] hi, input [7:0] lo);
        begin
            @(negedge clk); href <= 1'b1; dvp_d <= hi;
            @(posedge clk);
            @(negedge clk); dvp_d <= lo;
            @(posedge clk);
        end
    endtask

    task end_line;
        begin
            @(negedge clk); href <= 1'b0; dvp_d <= 8'd0;
            repeat (2) @(posedge clk);
        end
    endtask

    task drive_row(input integer y);
        integer x, idx;
        begin
            for (x = 0; x < H; x = x + 1) begin
                idx = (y*H + x) % 4;
                drive_pixel(pat_hi(idx), pat_lo(idx));
            end
            end_line;
        end
    endtask

    // --------------------------------------------------------------- 主激励流程
    // 可选：vvp +trace 打开逐像素打印
    integer dbg_trace = 0;
    initial dbg_trace = $test$plusargs("trace");
    always @(posedge clk) begin
        if (dbg_trace && tvalid) begin
            $display("TRACE t=%0t data=%h last=%b user=%b ready=%b",
                     $time, tdata, tlast, tuser, tready);
        end
    end

    integer y;
    initial begin
        $display("== vid_dvp_capture_tb: H=%0d V=%0d ==", H, V);
        tready = 1'b1;
        repeat (5) @(posedge clk);
        @(negedge clk); rst <= 1'b0;
        repeat (2) @(posedge clk);

        // ---- 帧 1：正常流，逐像素校验颜色/tuser/tlast ----
        start_frame;
        for (y = 0; y < V; y = y + 1) begin
            drive_row(y);
        end
        repeat (20) @(posedge clk);

        if (pix_total != H*V) begin
            $display("FAIL frame1 像素数 %0d != %0d", pix_total, H*V);
            errors = errors + 1;
        end
        if (user_cnt != 1) begin
            $display("FAIL frame1 tuser 次数 %0d != 1", user_cnt);
            errors = errors + 1;
        end
        if (stat_overflow != 1'b0) begin
            $display("FAIL frame1 不该溢出");
            errors = errors + 1;
        end
        $display("  frame1: pixels=%0d users=%0d", pix_total, user_cnt);

        // ---- 帧 2：背压测试：整行停 ready，像素先攒 FIFO，松手后必须一个不少、顺序不乱 ----
        // 注意：DVP 口没有反压，停 ready 期间**不能**让 href 挂着不动（那等于在灌"幽灵字节"），
        // 必须继续按真实节奏打像素，由内部 16 深 FIFO 吸收。
        pix_total = 0; user_cnt = 0;
        start_frame;
        for (y = 0; y < V; y = y + 1) begin
            @(negedge clk); tready <= 1'b0;
            drive_row(y);
            @(negedge clk); tready <= 1'b1;   // 松手：FIFO 里的像素开始排出
            repeat (H + 2) @(posedge clk);
        end
        repeat (20) @(posedge clk);
        if (pix_total != H*V) begin
            $display("FAIL frame2(背压) 像素数 %0d != %0d", pix_total, H*V);
            errors = errors + 1;
        end
        if (stat_overflow != 1'b0) begin
            $display("FAIL frame2(背压) 不该溢出");
            errors = errors + 1;
        end
        $display("  frame2(整行背压): pixels=%0d", pix_total);

        // ---- 帧 3：整场不 ready 且送 V+2 行（像素数 > FIFO 深度）⇒ 应溢出 ----
        chk_expected = 0;   // 帧 3 的像素大量丢弃，残留像素不参与检查
        start_frame;
        @(negedge clk); tready <= 1'b0;
        for (y = 0; y < V+2; y = y + 1) begin
            drive_row(y);
        end
        repeat (4) @(posedge clk);
        if (stat_overflow != 1'b1) begin
            $display("FAIL frame3(整场不ready) 应置 stat_overflow");
            errors = errors + 1;
        end else begin
            $display("  frame3(整场不ready): stat_overflow=1（预期）");
        end
        @(negedge clk); tready <= 1'b1;

        // ---- 收尾 ----
        repeat (10) @(posedge clk);
        if (errors == 0) begin
            $display("VID_DVP_CAPTURE_TB_PASS errors=0");
        end else begin
            $display("VID_DVP_CAPTURE_TB_FAIL errors=%0d", errors);
        end
        $finish;
    end

endmodule

`default_nettype wire
