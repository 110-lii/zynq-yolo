// =============================================================================
// inf_axis_async_fifo_tb —— 跨时钟域 AXIS 异步 FIFO 的定向测试台
// -----------------------------------------------------------------------------
// 被测：src/rtl/common/inf_axis_async_fifo.v
//       DATA_W=32 / USER_W=1 / DEPTH_LOG2=4（16 深，几拍就能灌满）
//
// 双时钟（故意取非整数比，制造持续变化的相位关系）：
//   s_clk 周期 10 ns，m_clk 周期 37 ns（37/10 = 3.7，10 拍才对齐一次）
//
// 检查项（每项独立判据，失败先打印 "FAIL <该项> ..."，再打印失败令牌）：
//   a. 基本搬运：FWFT 立即出数（不等握手、tvalid 不掉、数据已挂总线）
//      + 100 条序列（tdata=序列号，tuser=序列号低位，tlast 每 16 条一次 = 一行），
//      顺序/数量/数据/旁带逐条比对。
//   b. 反压：m_tready 由 16 位 LFSR 伪随机拉低（固定种子 ⇒ 可复现），写侧偶发
//      空拍；120 条仍必须一条不丢、不重、不乱。
//   c. 满：读侧完全停读，写满 16 深；s_tready 必须拉低；满期间再猛灌 8 次
//      （marker 数据）必须**一次都不生效**，之后读出的正好是前 16 条、序列无缝。
//   d. 空：读空后 m_tvalid 必须为 0；此时 m_tready 拉高 10 拍不得让数据凭空
//      消失（随后写 1 条，仍能完整读出）。
//   e. 复位：传输中途**分别只**复位写侧 / 只复位读侧（另一侧继续跑），确认复位
//      后不出现「永远满 / 永远空」，且复位后能正常继续搬运（被复位打断的在飞
//      数据语义已失效，不计入比对，用丢弃模式排干净）。
//
// 统计并打印：写入条目数 / 读出条目数 / 数据错误数（另有 user/last/顺序/丢弃）。
// 成功判据：最后一行单独打印 INF_AXIS_ASYNC_FIFO_TB_PASS
// 失败：打印 INF_AXIS_ASYNC_FIFO_TB_FAIL <原因> 后 $finish；另有看门狗兜底。
//
// 跑法（产物只落 build/sim/）：
//   iverilog -g2012 -o build/sim/fifo_tb.vvp src/rtl/common/inf_axis_async_fifo.v ^
//            sim/tb/common/inf_axis_async_fifo_tb.v
//   vvp build/sim/fifo_tb.vvp
// =============================================================================
`timescale 1ns / 1ps
`default_nettype none

module inf_axis_async_fifo_tb;

    // ------------------------------------------------------------ 被测参数
    localparam integer DATA_W     = 32;
    localparam integer USER_W     = 1;
    localparam integer DEPTH_LOG2 = 4;
    localparam integer DEPTH      = (1 << DEPTH_LOG2);   // 16

    // ------------------------------------------------------------ 连接信号
    reg               s_clk;
    reg               m_clk;
    reg               s_rst;
    reg               m_rst;
    reg               s_tvalid;
    wire              s_tready;
    reg  [DATA_W-1:0] s_tdata;
    reg  [USER_W-1:0] s_tuser;
    reg               s_tlast;
    wire              m_tvalid;
    reg               m_tready;
    wire [DATA_W-1:0] m_tdata;
    wire [USER_W-1:0] m_tuser;
    wire              m_tlast;

    // 双时钟：10 ns / 37 ns，非整数比（37/10=3.7）
    initial begin
        s_clk = 1'b0;
        forever #5 s_clk = ~s_clk;
    end
    initial begin
        m_clk = 1'b0;
        forever #18.5 m_clk = ~m_clk;
    end

    // ------------------------------------------------------------ DUT
    inf_axis_async_fifo #(
        .DATA_W     (DATA_W),
        .USER_W     (USER_W),
        .DEPTH_LOG2 (DEPTH_LOG2)
    ) dut (
        .s_clk    (s_clk),
        .s_rst    (s_rst),
        .s_tvalid (s_tvalid),
        .s_tready (s_tready),
        .s_tdata  (s_tdata),
        .s_tuser  (s_tuser),
        .s_tlast  (s_tlast),
        .m_clk    (m_clk),
        .m_rst    (m_rst),
        .m_tvalid (m_tvalid),
        .m_tready (m_tready),
        .m_tdata  (m_tdata),
        .m_tuser  (m_tuser),
        .m_tlast  (m_tlast)
    );

    // ------------------------------------------------------------ 计分板
    // 写侧每被接受一条就压进队列，读侧每弹出一条就与队首逐项比对。
    // 队列用「环形不方便判空」的写法，改成 wr_idx/rd_idx 单向增长（上限 QMAX 够用）。
    localparam integer QMAX = 1024;
    reg [DATA_W-1:0] exp_data [0:QMAX-1];
    reg [USER_W-1:0] exp_user [0:QMAX-1];
    reg              exp_last [0:QMAX-1];

    integer wr_idx;          // 已入队条目数（写侧）
    integer rd_idx;          // 已比对条目数（读侧）
    integer wr_count;        // 累计被接受的写（含复位前）
    integer rd_count;        // 累计读出（含丢弃）
    integer discard_count;   // check_en=0 期间读出的条数（复位后残留）
    integer data_err;
    integer user_err;
    integer last_err;
    integer order_err;
    reg     check_en;        // 1=逐条比对，0=丢弃（复位后语义失效的残留）
    integer base_rd;         // 阶段基线：用于「本阶段写入 == 本阶段读出」判据
    integer base_wr;

    // ------------------------------------------------------------ 读侧激励
    // ready_mode: 0=m_tready 恒 1，1=随机（LFSR），2=m_tready 恒 0
    reg  [1:0]  ready_mode;
    reg  [15:0] lfsr;
    always @(posedge m_clk) begin
        // Galois LFSR，固定种子 ⇒ 波形可复现
        lfsr <= {lfsr[14:0], (lfsr[15] ^ lfsr[13] ^ lfsr[12] ^ lfsr[10])};
        case (ready_mode)
            2'd0:    m_tready <= 1'b1;
            2'd1:    m_tready <= (lfsr[0] | lfsr[1] | lfsr[4]);  // 约 87% 为 1
            default: m_tready <= 1'b0;                           // 2=完全停读
        endcase
    end

    // ------------------------------------------------------------ 读侧检查器
    // 在 m_clk 上升沿采样 m_tvalid && m_tready：这一拍就是 AXI 意义上的弹出。
    // 注意：同一时刻读到的 m_tready 是「边沿前」的值，DUT 内部也是用这个值判
    // 握手，所以两边判定严格一致。
    always @(posedge m_clk) begin
        if ((m_tvalid === 1'b1) && (m_tready === 1'b1)) begin
            rd_count = rd_count + 1;
            if (check_en) begin
                if (rd_idx >= wr_idx) begin
                    // 读出多于写入：FIFO 凭空吐数据（指针/同步链错）
                    order_err = order_err + 1;
                    if (order_err <= 5) begin
                        $display("FAIL 顺序: 读出多于写入 rd_idx=%0d wr_idx=%0d data=%h",
                                 rd_idx, wr_idx, m_tdata);
                    end
                end else begin
                    if (m_tdata !== exp_data[rd_idx]) begin
                        data_err = data_err + 1;
                        if (data_err <= 5) begin
                            $display("FAIL 数据: idx=%0d 期望 %h 得到 %h",
                                     rd_idx, exp_data[rd_idx], m_tdata);
                        end
                    end
                    if (m_tuser !== exp_user[rd_idx]) begin
                        user_err = user_err + 1;
                        if (user_err <= 5) begin
                            $display("FAIL tuser: idx=%0d 期望 %b 得到 %b",
                                     rd_idx, exp_user[rd_idx], m_tuser);
                        end
                    end
                    if (m_tlast !== exp_last[rd_idx]) begin
                        last_err = last_err + 1;
                        if (last_err <= 5) begin
                            $display("FAIL tlast: idx=%0d 期望 %b 得到 %b",
                                     rd_idx, exp_last[rd_idx], m_tlast);
                        end
                    end
                    rd_idx = rd_idx + 1;
                end
            end else begin
                discard_count = discard_count + 1;
            end
        end
    end

    // ------------------------------------------------------------ 看门狗
    // 握手死锁（永远满/永远空）会让主流程卡住，这里兜底给失败令牌。
    initial begin
        #20000000;   // 20 ms 仿真时间，本 TB 实测 < 1 ms
        $display("FAIL watchdog: 20 ms 内没跑完，疑似握手死锁（永远满/永远空）");
        $display("INF_AXIS_ASYNC_FIFO_TB_FAIL watchdog_timeout");
        $finish;
    end

    // =======================================================================
    // 激励/检查任务
    // =======================================================================
    // 写一条：negde 摆数据、posedge 采 s_tready（这就是 AXI 握手采样点）
    task wr_item;
        input [DATA_W-1:0] d;
        input [USER_W-1:0] u;
        input              l;
        begin
            @(negedge s_clk);
            s_tvalid = 1'b1;
            s_tdata  = d;
            s_tuser  = u;
            s_tlast  = l;
            @(posedge s_clk);
            while (s_tready !== 1'b1) @(posedge s_clk);   // 反压时保持 tvalid/数据不变
            exp_data[wr_idx] = d;
            exp_user[wr_idx] = u;
            exp_last[wr_idx] = l;
            wr_idx   = wr_idx + 1;
            wr_count = wr_count + 1;
            @(negedge s_clk);
            s_tvalid = 1'b0;
        end
    endtask

    task pulse_s_rst;
        input integer ncyc;
        begin
            @(negedge s_clk);
            s_rst = 1'b1;
            repeat (ncyc) @(negedge s_clk);
            s_rst = 1'b0;
            @(negedge s_clk);
        end
    endtask

    task pulse_m_rst;
        input integer ncyc;
        begin
            @(negedge m_clk);
            m_rst = 1'b1;
            repeat (ncyc) @(negedge m_clk);
            m_rst = 1'b0;
            @(negedge m_clk);
        end
    endtask

    // 等 m_tvalid 拉高（FWFT 判据）
    task wait_tvalid;
        input integer max_cycles;
        integer ncyc;
        begin
            ncyc = 0;
            while ((ncyc < max_cycles) && (m_tvalid !== 1'b1)) begin
                @(posedge m_clk);
                ncyc = ncyc + 1;
            end
            if (m_tvalid !== 1'b1) begin
                $display("FAIL a.FWFT: %0d 个 m_clk 内 m_tvalid 未拉高（数据到了但 tvalid 不来）",
                         max_cycles);
                tb_fail(1);
            end
        end
    endtask

    // 等本阶段写入的条目全部被读出
    task wait_drained;
        input integer max_cycles;
        integer ncyc;
        begin
            ncyc = 0;
            while ((ncyc < max_cycles) && ((rd_count - base_rd) < (wr_count - base_wr))) begin
                @(posedge m_clk);
                ncyc = ncyc + 1;
            end
            if ((rd_count - base_rd) < (wr_count - base_wr)) begin
                $display("FAIL 读出超时: 本阶段写入 %0d 条、只读出 %0d 条",
                         wr_count - base_wr, rd_count - base_rd);
                tb_fail(19);
            end
        end
    endtask

    // 一直读到 m_tvalid 连续 20 个 m_clk 为 0（含丢弃模式）
    // 20 拍远大于跨域同步链的 2~3 拍，避免把「同步窗口里的假空」当成真空
    task flush_empty;
        input integer max_cycles;
        integer ncyc, idle;
        begin
            ncyc = 0;
            idle = 0;
            while ((ncyc < max_cycles) && (idle < 20)) begin
                @(posedge m_clk);
                ncyc = ncyc + 1;
                if (m_tvalid === 1'b1) idle = 0;
                else                   idle = idle + 1;
            end
            if (idle < 20) begin
                $display("FAIL 排空超时: %0d 拍后 m_tvalid 仍为 %b（卡在非空）",
                         max_cycles, m_tvalid);
                tb_fail(18);
            end
        end
    endtask

    task clear_scoreboard;
        begin
            wr_idx = 0;
            rd_idx = 0;
        end
    endtask

    // 阶段判据：无任何比对错误，且本阶段写入的条目全部被比对过
    task check_phase;
        input integer phase_id;
        input integer reason;
        begin
            if ((data_err + user_err + last_err + order_err) != 0) begin
                $display("FAIL 阶段%0d: data_err=%0d user_err=%0d last_err=%0d order_err=%0d",
                         phase_id, data_err, user_err, last_err, order_err);
                tb_fail(reason);
            end
            if (rd_idx != wr_idx) begin
                $display("FAIL 阶段%0d: 条目数 rd_idx=%0d wr_idx=%0d（有写入没读出）",
                         phase_id, rd_idx, wr_idx);
                tb_fail(reason);
            end
        end
    endtask

    // 阶段判据：本阶段写入数 == 本阶段读出数（防「多读/少读」）
    task check_count;
        input integer phase_id;
        input integer reason;
        begin
            if ((rd_count - base_rd) != (wr_count - base_wr)) begin
                $display("FAIL 阶段%0d: 本阶段写入 %0d 条、读出 %0d 条（数量不符）",
                         phase_id, wr_count - base_wr, rd_count - base_rd);
                tb_fail(reason);
            end
        end
    endtask

    // 失败出口：先让调用点打印具体的 "FAIL <该项>"，这里打印令牌并结束
    task tb_fail;
        input integer code;
        begin
            case (code)
                1:  $display("INF_AXIS_ASYNC_FIFO_TB_FAIL a_FWFT_tvalid（非空时 tvalid 没来/掉下去）");
                2:  $display("INF_AXIS_ASYNC_FIFO_TB_FAIL a_FWFT_payload（tvalid 时数据/旁带不对）");
                3:  $display("INF_AXIS_ASYNC_FIFO_TB_FAIL a_basic_data（基本搬运数据/顺序错）");
                4:  $display("INF_AXIS_ASYNC_FIFO_TB_FAIL a_basic_count（基本搬运数量错）");
                5:  $display("INF_AXIS_ASYNC_FIFO_TB_FAIL b_backpressure_data（反压期间丢数/重数/乱序）");
                6:  $display("INF_AXIS_ASYNC_FIFO_TB_FAIL b_backpressure_count（反压期间数量不符）");
                7:  $display("INF_AXIS_ASYNC_FIFO_TB_FAIL c_full_tready_not_low（满时 s_tready 未拉低）");
                8:  $display("INF_AXIS_ASYNC_FIFO_TB_FAIL c_full_write_while_full（满期间写入被接受）");
                9:  $display("INF_AXIS_ASYNC_FIFO_TB_FAIL c_full_count（满/排空后数量不符）");
                10: $display("INF_AXIS_ASYNC_FIFO_TB_FAIL d_empty_tvalid（空时 m_tvalid 非 0）");
                11: $display("INF_AXIS_ASYNC_FIFO_TB_FAIL d_empty_loss（空时 tready 高导致数据丢失）");
                12: $display("INF_AXIS_ASYNC_FIFO_TB_FAIL e1_reset_stuck_full（写侧复位后卡满）");
                13: $display("INF_AXIS_ASYNC_FIFO_TB_FAIL e1_after_reset_data（写侧复位后搬运错）");
                14: $display("INF_AXIS_ASYNC_FIFO_TB_FAIL e1_stuck_nonempty（写侧复位后卡非空）");
                15: $display("INF_AXIS_ASYNC_FIFO_TB_FAIL e2_stuck_nonempty（读侧复位后卡非空）");
                16: $display("INF_AXIS_ASYNC_FIFO_TB_FAIL e2_after_reset_data（读侧复位后搬运错）");
                17: $display("INF_AXIS_ASYNC_FIFO_TB_FAIL e2_reset_stuck_full（读侧复位后卡满）");
                18: $display("INF_AXIS_ASYNC_FIFO_TB_FAIL timeout_flush（排空超时，卡非空）");
                19: $display("INF_AXIS_ASYNC_FIFO_TB_FAIL timeout_rd（读出超时）");
                20: $display("INF_AXIS_ASYNC_FIFO_TB_FAIL init_reset_tvalid（初始复位后 m_tvalid 非 0，指针初值/X 问题）");
                default: $display("INF_AXIS_ASYNC_FIFO_TB_FAIL unknown");
            endcase
            $finish;
        end
    endtask

    // =======================================================================
    // 主流程
    // =======================================================================
    reg [31:0] seq;          // 全局递增序列号（跨阶段连续，复位也不回 0）
    integer    i, k, n0;
    reg [DATA_W-1:0] item_d;
    reg [USER_W-1:0] item_u;
    reg              item_l;

    initial begin
        // ---------------- 初始化 ----------------
        s_rst      = 1'b1;
        m_rst      = 1'b1;
        s_tvalid   = 1'b0;
        s_tdata    = {DATA_W{1'b0}};
        s_tuser    = {USER_W{1'b0}};
        s_tlast    = 1'b0;
        ready_mode = 2'd0;
        lfsr       = 16'hA5C3;
        check_en   = 1'b1;
        seq        = 32'd0;
        wr_idx = 0;
        rd_idx = 0;
        wr_count = 0;
        rd_count = 0;
        discard_count = 0;
        data_err = 0;
        user_err = 0;
        last_err = 0;
        order_err = 0;
        base_rd = 0;
        base_wr = 0;

        $display("== inf_axis_async_fifo_tb: DATA_W=%0d USER_W=%0d DEPTH_LOG2=%0d (DEPTH=%0d) ==",
                 DATA_W, USER_W, DEPTH_LOG2, DEPTH);
        $display("   s_clk=10ns  m_clk=37ns（异步非整数比）");

        // ---------------- 初始复位（两侧） ----------------
        repeat (4) @(negedge s_clk);
        s_rst = 1'b0;
        repeat (4) @(negedge m_clk);
        m_rst = 1'b0;
        repeat (8) @(negedge m_clk);
        repeat (4) @(negedge s_clk);

        // 指针初值必须是 0：复位后空 ⇒ m_tvalid 必须为 0（若指针是 x 这里就挂）
        if (m_tvalid !== 1'b0) begin
            $display("FAIL 初始复位: m_tvalid=%b，期望 0（指针初值/ x 传播问题）", m_tvalid);
            tb_fail(20);
        end

        // =================================================================
        // a. 基本搬运 + FWFT
        // =================================================================
        $display("[TB] phase a: 基本搬运 + FWFT");
        ready_mode = 2'd2;                       // 读侧先不 ready，专测 FWFT
        @(negedge m_clk);
        while (m_tready !== 1'b0) @(negedge m_clk);

        base_rd = rd_count;
        base_wr = wr_count;

        item_d = seq;
        item_u = seq[0];
        item_l = ((seq & 32'hF) == 32'hF);
        wr_item(item_d, item_u, item_l);
        seq = seq + 32'd1;

        // FWFT 判据 1：不等任何握手，tvalid 自己拉高（CDC 2~3 拍内）
        wait_tvalid(12);
        // FWFT 判据 2：数据已经在总线上（组合读出，不是「握手后才给数」）
        if (m_tdata !== item_d) begin
            $display("FAIL a.FWFT 数据: 期望 %h 得到 %h", item_d, m_tdata);
            tb_fail(2);
        end
        if (m_tuser !== item_u) begin
            $display("FAIL a.FWFT tuser: 期望 %b 得到 %b", item_u, m_tuser);
            tb_fail(2);
        end
        if (m_tlast !== item_l) begin
            $display("FAIL a.FWFT tlast: 期望 %b 得到 %b", item_l, m_tlast);
            tb_fail(2);
        end
        // FWFT 判据 3：下游不 ready 时 tvalid 不许掉（数据要一直挂得住）
        for (k = 0; k < 5; k = k + 1) begin
            @(posedge m_clk);
            if (m_tvalid !== 1'b1) begin
                $display("FAIL a.FWFT: 下游未 ready 时 m_tvalid 掉了（第 %0d 拍）", k);
                tb_fail(1);
            end
        end
        ready_mode = 2'd0;                       // 放行
        wait_drained(4000);
        repeat (6) @(posedge m_clk);
        if (rd_count - base_rd != 1) begin
            $display("FAIL a.FWFT 数量: 读侧不 ready 期间凭空丢/多了数据（读出 %0d 条）",
                     rd_count - base_rd);
            tb_fail(2);
        end

        // 100 条批量：tdata=序列号，tuser=序列号低位，tlast 每 16 条一次（= 一行）
        clear_scoreboard();
        base_rd = rd_count;
        base_wr = wr_count;
        for (i = 0; i < 100; i = i + 1) begin
            item_d = seq;
            item_u = seq[0];
            item_l = ((seq & 32'hF) == 32'hF);
            wr_item(item_d, item_u, item_l);
            seq = seq + 32'd1;
        end
        wait_drained(200000);
        repeat (6) @(posedge m_clk);
        check_phase(1, 3);
        check_count(1, 4);
        if ((rd_count - base_rd) != 100) begin
            $display("FAIL a.基本搬运: 读出 %0d 条，期望 100 条", rd_count - base_rd);
            tb_fail(4);
        end
        $display("[TB] phase a: PASS（FWFT + 100 条）");

        // =================================================================
        // b. 反压：随机 m_tready + 写侧偶发空拍
        // =================================================================
        $display("[TB] phase b: 反压（随机 tready）+ 写侧空拍");
        lfsr       = 16'hBEEF;                   // 固定种子 ⇒ 可复现
        ready_mode = 2'd1;
        check_en   = 1'b1;
        clear_scoreboard();
        base_rd = rd_count;
        base_wr = wr_count;
        for (i = 0; i < 120; i = i + 1) begin
            item_d = seq;
            item_u = seq[0];
            item_l = ((seq & 32'hF) == 32'hF);
            wr_item(item_d, item_u, item_l);
            seq = seq + 32'd1;
            // 写侧偶发空拍，逼出「满 → 等 → 继续」的路径
            case (i % 7)
                0:       repeat (3) @(negedge s_clk);
                1, 2:    repeat (2) @(negedge s_clk);
                3:       @(negedge s_clk);
                default: ;                   // 4/5/6：连续写
            endcase
        end
        wait_drained(200000);
        repeat (6) @(posedge m_clk);
        check_phase(2, 5);
        check_count(2, 6);
        if ((rd_count - base_rd) != 120) begin
            $display("FAIL b.反压: 读出 %0d 条，期望 120 条", rd_count - base_rd);
            tb_fail(6);
        end
        $display("[TB] phase b: PASS（120 条，随机反压）");

        // =================================================================
        // c. 满：写满后 s_tready=0，且满期间写入不生效
        // =================================================================
        $display("[TB] phase c: 满 + s_tready=0 期间写入不得生效");
        ready_mode = 2'd2;                       // 读侧完全停读
        @(negedge m_clk);
        while (m_tready !== 1'b0) @(negedge m_clk);
        check_en = 1'b1;
        clear_scoreboard();
        base_rd = rd_count;
        base_wr = wr_count;

        for (i = 0; i < DEPTH; i = i + 1) begin
            item_d = seq;
            item_u = seq[0];
            item_l = ((seq & 32'hF) == 32'hF);
            wr_item(item_d, item_u, item_l);
            seq = seq + 32'd1;
        end
        repeat (3) @(negedge s_clk);
        if (s_tready !== 1'b0) begin
            $display("FAIL c.满: 灌满 %0d 条后 s_tready=%b，期望 0", DEPTH, s_tready);
            tb_fail(7);
        end
        // 满期间再猛灌 8 次 marker：AXI 语义下 tready=0 就是没写，必须一次都不生效
        for (k = 0; k < 8; k = k + 1) begin
            s_tvalid = 1'b1;
            s_tdata  = {DATA_W{1'b1}};           // DEADBEEF 类 marker，出现即算错
            s_tuser  = {USER_W{1'b1}};
            s_tlast  = 1'b1;
            @(posedge s_clk);
            if (s_tready !== 1'b0) begin
                $display("FAIL c.满: tready=0 期间写入被接受（第 %0d 次尝试），多写进 1 条", k);
                tb_fail(8);
            end
            @(negedge s_clk);
            s_tvalid = 1'b0;
        end
        // 放行：读出的必须正好是前面 16 条，序列无缝（marker 不许混进来）
        ready_mode = 2'd0;
        wait_drained(20000);
        repeat (6) @(posedge m_clk);
        check_phase(3, 9);
        check_count(3, 9);
        if ((rd_count - base_rd) != DEPTH) begin
            $display("FAIL c.满: 读出 %0d 条，期望 %0d 条", rd_count - base_rd, DEPTH);
            tb_fail(9);
        end
        if (s_tready !== 1'b1) begin
            $display("FAIL c.满: 排空后 s_tready=%b，期望 1（卡满）", s_tready);
            tb_fail(9);
        end
        $display("[TB] phase c: PASS（满=16，满期间 8 次写入全部被拒）");

        // =================================================================
        // d. 空：m_tvalid=0，且 tready 拉高不丢数据
        // =================================================================
        $display("[TB] phase d: 空 + tready 高不丢数据");
        ready_mode = 2'd0;                       // tready 恒 1
        repeat (8) @(posedge m_clk);
        if (m_tvalid !== 1'b0) begin
            $display("FAIL d.空: 读空后 m_tvalid=%b，期望 0", m_tvalid);
            tb_fail(10);
        end
        n0 = rd_count;
        for (k = 0; k < 10; k = k + 1) begin
            @(posedge m_clk);
            if (m_tvalid !== 1'b0) begin
                $display("FAIL d.空: 空且有数据尚未写入时 m_tvalid 拉高（第 %0d 拍）", k);
                tb_fail(10);
            end
        end
        if (rd_count != n0) begin
            $display("FAIL d.空: 空时 m_tready=1 却产生了 %0d 次弹出（凭空丢/吐数据）",
                     rd_count - n0);
            tb_fail(10);
        end
        // 空时 tready 高不丢数据：随后写 1 条，必须完整读出
        clear_scoreboard();
        base_rd = rd_count;
        base_wr = wr_count;
        item_d = seq;
        item_u = seq[0];
        item_l = ((seq & 32'hF) == 32'hF);
        wr_item(item_d, item_u, item_l);
        seq = seq + 32'd1;
        wait_drained(4000);
        repeat (6) @(posedge m_clk);
        check_phase(4, 11);
        check_count(4, 11);
        $display("[TB] phase d: PASS");

        // =================================================================
        // e1. 传输中途 **只** 复位写侧
        // =================================================================
        // 契约：只复位一侧时另一侧指针位置未知，在飞数据语义失效；这里只验证
        //       「不卡死、不永远满/永远空，复位后能继续正常搬运」。
        $display("[TB] phase e1: 传输中途只复位写侧");
        ready_mode = 2'd0;
        check_en   = 1'b1;
        // 先灌 8 条，制造「FIFO 里还有数据」的中间态
        for (i = 0; i < 8; i = i + 1) begin
            item_d = seq;
            item_u = seq[0];
            item_l = ((seq & 32'hF) == 32'hF);
            wr_item(item_d, item_u, item_l);
            seq = seq + 32'd1;
        end
        check_en = 1'b0;                         // 复位后残留数据不再比对
        pulse_s_rst(6);
        // 写侧指针已回 0、读侧指针还在老位置 ⇒ 把残留读干净（丢弃）
        flush_empty(5000);
        repeat (3) @(negedge s_clk);
        if (s_tready !== 1'b1) begin
            $display("FAIL e1.写侧复位后: s_tready=%b，FIFO 卡在满（永远满）", s_tready);
            tb_fail(12);
        end
        if (m_tvalid !== 1'b0) begin
            $display("FAIL e1.写侧复位后: m_tvalid=%b，FIFO 卡在非空（永远空不了）", m_tvalid);
            tb_fail(14);
        end
        // 复位后必须能继续搬运
        check_en = 1'b1;
        clear_scoreboard();
        base_rd = rd_count;
        base_wr = wr_count;
        for (i = 0; i < 20; i = i + 1) begin
            item_d = seq;
            item_u = seq[0];
            item_l = ((seq & 32'hF) == 32'hF);
            wr_item(item_d, item_u, item_l);
            seq = seq + 32'd1;
        end
        wait_drained(20000);
        repeat (6) @(posedge m_clk);
        check_phase(5, 13);
        check_count(5, 13);
        $display("[TB] phase e1: PASS（写侧复位后未卡死，20 条继续正常）");

        // =================================================================
        // e2. 传输中途 **只** 复位读侧
        // =================================================================
        $display("[TB] phase e2: 传输中途只复位读侧");
        ready_mode = 2'd0;
        check_en   = 1'b1;
        for (i = 0; i < 8; i = i + 1) begin
            item_d = seq;
            item_u = seq[0];
            item_l = ((seq & 32'hF) == 32'hF);
            wr_item(item_d, item_u, item_l);
            seq = seq + 32'd1;
        end
        check_en = 1'b0;
        pulse_m_rst(6);
        // 读侧指针已回 0、写侧指针还在老位置 ⇒ 丢弃残留
        flush_empty(5000);
        repeat (3) @(negedge m_clk);
        if (m_tvalid !== 1'b0) begin
            $display("FAIL e2.读侧复位后: m_tvalid=%b，FIFO 卡在非空（永远空不了）", m_tvalid);
            tb_fail(15);
        end
        repeat (3) @(negedge s_clk);
        if (s_tready !== 1'b1) begin
            $display("FAIL e2.读侧复位后: s_tready=%b，FIFO 卡在满（永远满）", s_tready);
            tb_fail(17);
        end
        check_en = 1'b1;
        clear_scoreboard();
        base_rd = rd_count;
        base_wr = wr_count;
        for (i = 0; i < 20; i = i + 1) begin
            item_d = seq;
            item_u = seq[0];
            item_l = ((seq & 32'hF) == 32'hF);
            wr_item(item_d, item_u, item_l);
            seq = seq + 32'd1;
        end
        wait_drained(20000);
        repeat (6) @(posedge m_clk);
        check_phase(6, 16);
        check_count(6, 16);
        $display("[TB] phase e2: PASS（读侧复位后未卡死，20 条继续正常）");

        // =================================================================
        // 统计与结论
        // =================================================================
        $display("== 统计: 写入条目数=%0d 读出条目数=%0d 数据错误数=%0d（user_err=%0d last_err=%0d order_err=%0d，复位丢弃=%0d）",
                 wr_count, rd_count, data_err, user_err, last_err, order_err, discard_count);
        $display("INF_AXIS_ASYNC_FIFO_TB_PASS");
        $finish;
    end

endmodule

`default_nettype wire