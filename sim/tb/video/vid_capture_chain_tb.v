// =============================================================================
// vid_capture_chain_tb -- 采集链整链回归：vid_test_pattern -> vid_dvp_capture
//                        -> inf_axis_async_fifo（跨时钟）-> AXIS 消费者
//
// 为什么要有这一级：两个模块各自的 TB 只能证明「模块自己自洽」。上板判据是
// 「DDR 里的字节等于 Python 黄金模型」，而黄金模型（tools/capture_golden.py）
// 是照着 RTL 的公式手写的第三份实现。所以这里做的是**三方对拍**：
//
//   1) 本 TB 用闭式公式独立算出每像素期望值（Verilog 里另写一遍）
//   2) 消费者把收到的 32 位字逐字与 (1) 比对，并检查 tuser/tlast 的帧结构
//   3) 同时把收到的字节流**原样 dump** 成二进制（默认 build/sim/chain_frame.bin），
//      交给 tools/capture_golden.py --compare —— 这与上板 JTAG dump 走的是
//      **同一个比对器、同一份格式**，所以「仿真过」与「板上过」是同一把尺子。
//
// 时序/结构：
//   * pclk  = 25 MHz（40 ns）—— 与 BD 里 FCLK1 一致
//   * aclk  = 100 MHz（10 ns）—— 与 BD 里 FCLK0 一致（FIFO 跨这两个域）
//   * 复位高有效；s_rst/m_rst 同时释放前先保持 10 个各自时钟
//   * 消费者的 m_tready 故意 25% 拍拉低，逼出 FIFO 的反压路径（深度 512 足够，
//     因为 aclk 是 pclk 的 4 倍速）
//
// 成功令牌：VID_CAPTURE_CHAIN_TB_PASS words=<n> frame=<path>
// 失败    ：VID_CAPTURE_CHAIN_TB_FAIL <reason>（第一处不符会给出坐标与实测/期望）
//
// 仿真用 +GOLDEN=<path> 可换 dump 路径；默认 build/sim/chain_frame.bin。
// =============================================================================
`timescale 1ns / 1ps
`default_nettype none

module vid_capture_chain_tb;

    // ---------------------------------------------------------------- 参数
    // 固定成与 BD/上板完全相同的口径（640x480）。刻意**不做** +H/+V 缩小：
    // 上板跑的就是这个尺寸，仿真跑别的尺寸会让「仿真过」失去参照意义。
    localparam integer H_ACTIVE = 640;
    localparam integer V_ACTIVE = 480;
    localparam integer H_TOTAL  = 800;   // 与 BD 里的图样参数一致（含消隐）
    localparam integer V_TOTAL  = 525;

    localparam real PCLK_HALF = 20.0;   // 25 MHz
    localparam real ACLK_HALF = 5.0;    // 100 MHz

    // ---------------------------------------------------------------- 时钟/复位
    reg pclk = 1'b0;
    reg aclk = 1'b0;
    always #(PCLK_HALF) pclk = ~pclk;
    always #(ACLK_HALF) aclk = ~aclk;

    reg s_rst = 1'b1;
    reg m_rst = 1'b1;
    reg run   = 1'b0;

    // ---------------------------------------------------------------- DUT 互联
    wire        vsync_n;
    wire        href;
    wire [7:0]  dvp_d;

    wire [31:0] cap_tdata;
    wire        cap_tvalid;
    wire        cap_tready;
    wire        cap_tlast;
    wire        cap_tuser;
    wire        stat_overflow;

    wire [31:0] m_tdata;
    wire        m_tvalid;
    wire        m_tready;
    wire        m_tlast;
    wire        m_tuser;

    vid_test_pattern #(
        .H_ACTIVE (H_ACTIVE),
        .V_ACTIVE (V_ACTIVE),
        .H_TOTAL  (H_TOTAL),
        .V_TOTAL  (V_TOTAL)
    ) u_pat (
        .clk     (pclk),
        .rst     (s_rst),
        .run     (run),
        .vsync_n (vsync_n),
        .href    (href),
        .dvp_d   (dvp_d)
    );

    vid_dvp_capture #(
        .H_ACTIVE (H_ACTIVE),
        .V_ACTIVE (V_ACTIVE)
    ) u_cap (
        .clk           (pclk),
        .rst           (s_rst),
        .vsync_n       (vsync_n),
        .href          (href),
        .dvp_d         (dvp_d),
        .m_axis_tdata  (cap_tdata),
        .m_axis_tvalid (cap_tvalid),
        .m_axis_tready (cap_tready),
        .m_axis_tlast  (cap_tlast),
        .m_axis_tuser  (cap_tuser),
        .stat_overflow (stat_overflow)
    );

    inf_axis_async_fifo #(
        .DATA_W     (32),
        .USER_W     (1),
        .DEPTH_LOG2 (9)
    ) u_fifo (
        .s_clk    (pclk),
        .s_rst    (s_rst),
        .s_tvalid (cap_tvalid),
        .s_tready (cap_tready),
        .s_tdata  (cap_tdata),
        .s_tuser  (cap_tuser),
        .s_tlast  (cap_tlast),
        .m_clk    (aclk),
        .m_rst    (m_rst),
        .m_tvalid (m_tvalid),
        .m_tready (m_tready),
        .m_tdata  (m_tdata),
        .m_tuser  (m_tuser),
        .m_tlast  (m_tlast)
    );

    // ---------------------------------------------------------------- 反压
    // 每 4 拍有 1 拍拉低（25% 停），足够触发 FIFO 的满/反压分支，又不会掉像素
    reg [1:0] bp = 2'd0;
    always @(posedge aclk) bp <= bp + 2'd1;
    assign m_tready = (bp != 2'd0);

    // ---------------------------------------------------------------- 期望值
    // 与 vid_test_pattern.v 头注释的公式逐字对应（第三份独立实现）
    function [31:0] expect_word(input [15:0] x, input [15:0] y);
        reg [4:0] r5;
        reg [5:0] g6;
        reg [4:0] b5;
        begin
            r5 = x[4:0];
            g6 = y[5:0];
            b5 = x[4:0] + y[4:0];
            expect_word = {8'h00, r5, r5[4:2], g6, g6[5:4], b5, b5[4:2]};
        end
    endfunction

    // ---------------------------------------------------------------- 消费者
    integer fd;
    integer words   = 0;
    integer x_cnt   = 0;
    integer y_cnt   = 0;
    integer errors  = 0;
    reg     done    = 1'b0;
    reg [31:0] exp_w;

    task fail_now(input [255:0] why);
        begin
            $display("VID_CAPTURE_CHAIN_TB_FAIL %0s (words=%0d x=%0d y=%0d)", why, words, x_cnt, y_cnt);
            $fclose(fd);
            $finish;
        end
    endtask

    always @(posedge aclk) begin
        if (!m_rst && m_tvalid && m_tready && !done) begin
            exp_w = expect_word(x_cnt, y_cnt);
            if (m_tdata !== exp_w) begin
                errors = errors + 1;
                if (errors <= 5) begin
                    $display("MISMATCH word=%0d x=%0d y=%0d got=%08h expect=%08h",
                             words, x_cnt, y_cnt, m_tdata, exp_w);
                end
            end
            // 帧结构：首字 tuser=1，**整帧最后一个字** tlast=1，其余 tuser=0/tlast=0
            if (x_cnt == 0 && y_cnt == 0) begin
                if (m_tuser !== 1'b1) begin
                    errors = errors + 1;
                    $display("MISMATCH tuser word=0 got=%b expect=1", m_tuser);
                end
            end else if (m_tuser !== 1'b0) begin
                errors = errors + 1;
                if (errors <= 5) $display("MISMATCH tuser word=%0d got=%b expect=0", words, m_tuser);
            end
            if (x_cnt == H_ACTIVE - 1 && y_cnt == V_ACTIVE - 1) begin
                if (m_tlast !== 1'b1) begin
                    errors = errors + 1;
                    if (errors <= 5) $display("MISMATCH tlast word=%0d got=%b expect=1", words, m_tlast);
                end
            end else if (m_tlast !== 1'b0) begin
                errors = errors + 1;
                if (errors <= 5) $display("MISMATCH tlast word=%0d got=%b expect=0", words, m_tlast);
            end
            // 32 位字的 4 个字节按小端落盘（与 DDR/黄金 bin 同序）
            $fwrite(fd, "%c", m_tdata[7:0]);
            $fwrite(fd, "%c", m_tdata[15:8]);
            $fwrite(fd, "%c", m_tdata[23:16]);
            $fwrite(fd, "%c", m_tdata[31:24]);
            words = words + 1;
            if (x_cnt == H_ACTIVE - 1) begin
                x_cnt = 0;
                y_cnt = y_cnt + 1;
            end else begin
                x_cnt = x_cnt + 1;
            end
            if (words == H_ACTIVE * V_ACTIVE) done = 1'b1;
        end
    end

    // ---------------------------------------------------------------- 主流程
    reg [1023:0] dump_path;
    integer      cycles;

    initial begin
        if (!$value$plusargs("GOLDEN=%s", dump_path)) begin
            dump_path = "build/sim/chain_frame.bin";
        end
        fd = $fopen(dump_path, "wb");
        if (fd == 0) begin
            $display("VID_CAPTURE_CHAIN_TB_FAIL cannot open dump file %0s", dump_path);
            $finish;
        end
        $display("== vid_capture_chain_tb: %0dx%0d (H_TOTAL=%0d V_TOTAL=%0d) ==",
                 H_ACTIVE, V_ACTIVE, H_TOTAL, V_TOTAL);
        $display("   pclk=25MHz aclk=100MHz  fifo DEPTH_LOG2=9  dump=%0s", dump_path);

        // 双复位 -> 释放 -> 放 run
        repeat (20) @(posedge pclk);
        s_rst = 1'b0;
        m_rst = 1'b0;
        repeat (10) @(posedge pclk);
        run = 1'b1;
        $display("[TB] run=1, waiting for %0d words (one full frame)...", H_ACTIVE * V_ACTIVE);

        // 看门狗：两场的时间还没齐就说明卡住了
        cycles = 0;
        while (!done && cycles < 3 * V_TOTAL * 2 * H_TOTAL) begin
            @(posedge pclk);
            cycles = cycles + 1;
        end
        run = 1'b0;

        if (!done) fail_now("watchdog timeout: frame never completed");
        if (stat_overflow !== 1'b0) fail_now("stat_overflow=1: capture dropped pixels");
        if (errors != 0) fail_now("pixel mismatch");
        if (words != H_ACTIVE * V_ACTIVE) fail_now("word count mismatch");

        $fclose(fd);
        $display("== 统计: words=%0d errors=%0d overflow=%0b", words, errors, stat_overflow);
        $display("VID_CAPTURE_CHAIN_TB_PASS words=%0d frame=%0s", words, dump_path);
        $finish;
    end

endmodule

`default_nettype wire