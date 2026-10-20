// =============================================================================
// vid_test_pattern_tb —— DVP 测试图样发生器的定向测试台
// -----------------------------------------------------------------------------
// 被测：src/rtl/video/vid_test_pattern.v
//
// 参数取小（H_ACTIVE=4, V_ACTIVE=3, H_TOTAL=6, V_TOTAL=5）：
//   行周期 = 2*H_TOTAL = 12 拍；场周期 = VSYNC_LEN(4) + V_TOTAL(5)*12 = 64 拍。
//   3 场 + run 停止/恢复一共几百拍，一眼能看完；H_TOTAL>H_ACTIVE 保证水平消隐非 0。
//
// 采样约定：所有检查都在 clk 上升沿读 DUT 输出。DUT 的输出是上升沿非阻塞赋值的，
// 因此这里读到的正是"刚结束的那一拍"的稳定值 —— 和下游 vid_dvp_capture 看到的
// 完全一致（同一套时序语义），不会出现"仿真里对、上板错"的采样点偏差。
//
// 检查项（每条独立打印 FAIL <项> ...）：
//   a. 一场内 href=1 的拍数 = 2*H_ACTIVE*V_ACTIVE；href 上升沿数 = V_ACTIVE；
//      场周期与"VSYNC_LEN + 2*H_TOTAL*V_TOTAL"自洽（消隐账不能算错）
//   b. 逐字节比对：按 (x,y) 公式**独立**算期望高低字节，统计不符个数（须为 0）
//   c. vsync_n 低电平拍数 ∈ [2,8]；下降沿后第一拍 href 必须为 0（不能紧接有效数据）
//   d. 连续 3 场字节序列完全相同（图样相位每场复位）
//   e. run=0 时 href≡0 / vsync_n≡1 / dvp_d≡0；run 恢复后第一场从 (x=0,y=0) 起
//   w. 看门狗：RTL 卡死（等不到场同步）时打 FAIL 令牌退出，而不是把仿真挂死
//
// 判据：无 FAIL 时单独一行打印 VID_TEST_PATTERN_TB_PASS；
//       失败打印 VID_TEST_PATTERN_TB_FAIL <原因>。脚本只认这两个令牌。
// 跑法：
//   iverilog -g2012 -o build/sim/pattern_tb.vvp src/rtl/video/vid_test_pattern.v \
//            sim/tb/video/vid_test_pattern_tb.v
//   vvp build/sim/pattern_tb.vvp
// =============================================================================
`timescale 1ns / 1ps
`default_nettype none

module vid_test_pattern_tb;

    localparam integer H_ACTIVE  = 4;
    localparam integer V_ACTIVE  = 3;
    localparam integer H_TOTAL   = 6;
    localparam integer V_TOTAL   = 5;

    localparam integer FRAME_BYTES = 2*H_ACTIVE*V_ACTIVE;   // 每场有效字节数 = 24
    localparam integer NFRAMES     = 3;                     // 检查项 d 要连跑 3 场
    localparam integer NF          = NFRAMES + 2;           // 统计数组槽位（留余量）
    localparam integer MAXB        = 64;                    // 每场字节缓冲（> FRAME_BYTES）
    localparam integer VSYNC_LEN   = 4;                     // 与 RTL 的 VSYNC_LEN 对齐
    localparam integer EXP_PERIOD  = VSYNC_LEN + V_TOTAL*2*H_TOTAL;  // 期望场周期 = 64 拍

    // ------------------------------------------------------------------ DUT 挂载
    reg         clk = 1'b0;
    reg         rst = 1'b1;
    reg         run = 1'b0;

    wire        vsync_n;
    wire        href;
    wire [7:0]  dvp_d;

    vid_test_pattern #(
        .H_ACTIVE(H_ACTIVE),
        .V_ACTIVE(V_ACTIVE),
        .H_TOTAL (H_TOTAL),
        .V_TOTAL (V_TOTAL)
    ) dut (
        .clk(clk), .rst(rst), .run(run),
        .vsync_n(vsync_n), .href(href), .dvp_d(dvp_d)
    );

    always #21 clk = ~clk;      // ≈23.8 MHz，和 vid_dvp_capture_tb 取同一节奏

    // ------------------------------------------------------------------ 看门狗
    // 为什么需要：若 RTL 卡死（例如场相位不复位、永远等不到下一个 vsync 下降沿），
    // 主流程里的 `while (frame_count < target) @(posedge clk)` 会永远等下去。
    // 那样 CI 只能看到"超时"，一个判据令牌都拿不到，定位成本极高。
    // 这里给一个宽松的超时（正常流程约 1000 拍，6000 拍足够），超时就打 FAIL 令牌退出。
    localparam integer WATCHDOG_CYC = 6000;
    reg tb_done = 1'b0;         // 主流程走完置 1，避免看门狗和正常判据抢令牌
    initial begin
        repeat (WATCHDOG_CYC) @(posedge clk);
        if (!tb_done) begin
            $display("FAIL w 看门狗超时：%0d 拍内没跑完全部检查（疑似 RTL 卡死/无场同步）", WATCHDOG_CYC);
            $display("VID_TEST_PATTERN_TB_FAIL watchdog_timeout");
        end
        $finish;
    end

    // ------------------------------------------------------------ 独立参考模型
    // 刻意不复用 RTL 的写法：这里用 integer 运算 + 位与掩码算，RTL 用截位，
    // 两套实现如果写错同一个地方反而更难发现。
    function [7:0] exp_byte(input integer x, input integer y, input integer hi);
        reg [4:0] r5;
        reg [5:0] g6;
        reg [4:0] b5;
        begin
            r5 = x[4:0];                    // R5 = x & 0x1F（用位与，不写 x % 32）
            g6 = y[5:0];                    // G6 = y & 0x3F
            b5 = (x + y) & 5'h1F;           // B5 = (x+y) & 0x1F
            exp_byte = hi ? {r5, g6[5:3]}   // 高字节先发
                          : {g6[2:0], b5};  // 低字节后发
        end
    endfunction

    // ---------------------------------------------------------------- 统计/记录
    // 注意：integer 在 Verilog 里未初始化就是 x（不是 0），必须显式清零，
    // 否则"一个错误都没出"会算成 x，最后判据永远失败。
    integer err_a = 0;      // 检查项 a
    integer err_b = 0;      // 检查项 b
    integer err_c = 0;      // 检查项 c
    integer err_d = 0;      // 检查项 d
    integer err_e = 0;      // 检查项 e

    reg [7:0] seq  [0:NF-1][0:MAXB-1];  // 每场采到的字节序列（检查项 d/e 用）
    integer   f_bytes [0:NF-1];         // 每场 href=1 拍数
    integer   f_rise  [0:NF-1];         // 每场 href 上升沿数（= 行数）
    integer   f_vlow  [0:NF-1];         // 每场 vsync_n 低电平拍数
    integer   f_cyc   [0:NF-1];         // 每场场周期（拍）
    integer   f_mis   [0:NF-1];         // 每场局域错误数（含逐字节不符/消隐非 0）
    integer   f_len   [0:NF-1];         // 每场实际采到的字节数

    // ------------------------------------------------------------------ 采样器
    // 一个后台 always 把所有逐拍检查做掉；主 initial 只负责阶段编排和汇总判定。
    integer frame_count;    // 已"收口"的场数
    integer cur_f;          // 正在累积的场号（-1 = 没有）
    integer prev_vsync, prev_href;
    integer f_nb, f_nr, f_nv, f_ne;   // 本场临时累加：字节/上升沿/vsync 低拍/错误
    integer f_bptr;                   // 本场字节缓冲指针
    integer f_by, f_bx;               // 本场当前行号（href 上升沿 +1）/ 行内字节号
    integer f_first_after_fall;       // 1 = 还在 vsync 下降沿后的第一拍
    integer f_cyc_cur;                // 本场已经过的拍数

    always @(posedge clk) begin
        if (rst) begin
            prev_vsync    = 1'b1;
            prev_href     = 1'b0;
            cur_f         = -1;
            frame_count   = 0;
            f_nb = 0; f_nr = 0; f_nv = 0; f_ne = 0; f_bptr = 0;
            f_by = -1; f_bx = 0; f_first_after_fall = 0; f_cyc_cur = 0;
        end else if (!run) begin
            // run=0：场被截断，丢弃半场，不做任何统计（静止性由 check_idle 检查）
            cur_f      = -1;
            prev_vsync = 1'b1;
            prev_href  = 1'b0;
        end else begin
            // ---------------- 场边界：vsync_n 下降沿（新场开始） ----------------
            if (prev_vsync == 1'b1 && vsync_n == 1'b0) begin
                if (cur_f >= 0) begin                     // 上一场收口
                    f_bytes[cur_f] = f_nb;
                    f_rise [cur_f] = f_nr;
                    f_vlow [cur_f] = f_nv;
                    f_cyc  [cur_f] = f_cyc_cur;
                    f_mis  [cur_f] = f_ne;
                    f_len  [cur_f] = (f_bptr > MAXB) ? MAXB : f_bptr;
                    frame_count    = frame_count + 1;
                end
                cur_f   = frame_count;
                f_nb = 0; f_nr = 0; f_nv = 0; f_ne = 0; f_bptr = 0;
                f_by = -1; f_bx = 0; f_cyc_cur = 0;
                f_first_after_fall = 1;
                prev_href = 1'b0;                        // 上一场末尾 href 必然是 0
            end

            // ---------------- 逐拍统计（含本拍） ----------------
            f_cyc_cur = f_cyc_cur + 1;

            if (vsync_n === 1'b0) begin
                f_nv = f_nv + 1;
                if (f_first_after_fall) begin
                    f_first_after_fall = 0;
                    // 检查项 c：vsync 下降沿之后第一拍必须还是场消隐
                    if (href !== 1'b0) begin
                        $display("FAIL c frame%0d vsync_n 下降沿后第一拍 href=1（应仍为场消隐）", cur_f);
                        err_c = err_c + 1;
                        f_ne  = f_ne + 1;
                    end
                end
            end

            if (href === 1'b1) begin
                if (prev_href === 1'b0) begin
                    f_nr = f_nr + 1;                      // href 上升沿 = 一行开始
                    f_by = f_by + 1;
                    f_bx = 0;
                end
                // 检查项 b：逐字节比对（期望值由 (x,y) 独立算，不看 DUT 内部任何信号）
                if (dvp_d !== exp_byte(f_bx >> 1, f_by, (f_bx & 1) == 0)) begin
                    if (f_ne < 8) begin
                        $display("FAIL b frame%0d 字节#%0d (x=%0d,y=%0d,%s) 得到 %02h 期望 %02h",
                                 cur_f, f_bx, f_bx >> 1, f_by,
                                 ((f_bx & 1) == 0) ? "H" : "L",
                                 dvp_d, exp_byte(f_bx >> 1, f_by, (f_bx & 1) == 0));
                    end
                    f_ne = f_ne + 1;
                end
                if (cur_f >= 0 && f_bptr < MAXB) seq[cur_f][f_bptr] = dvp_d;
                f_bptr = f_bptr + 1;
                f_bx   = f_bx + 1;
                f_nb   = f_nb + 1;      // 检查项 a：本场 href=1 拍数
            end else begin
                // 检查项 b（消隐侧）：href=0 时 dvp_d 必须为 0，不能留 x
                if (dvp_d !== 8'h00) begin
                    if (f_ne < 8) begin
                        $display("FAIL b frame%0d href=0 时 dvp_d=%02h（应恒为 00）", cur_f, dvp_d);
                    end
                    f_ne = f_ne + 1;
                end
            end

            prev_href  = href;
            prev_vsync = vsync_n;
        end
    end

    // -------------------------------------------------------------- 检查/打印任务
    task wait_frames(input integer target);
        begin
            while (frame_count < target) @(posedge clk);
        end
    endtask

    // 检查项 e（静止段）：run=0 时三个输出必须一动不动
    task check_idle(input integer n);
        integer i;
        begin
            for (i = 0; i < n; i = i + 1) begin
                @(posedge clk);
                if (vsync_n !== 1'b1 || href !== 1'b0 || dvp_d !== 8'h00) begin
                    $display("FAIL e run=0 未静止: t=%0t vsync_n=%b href=%b dvp_d=%02h",
                             $time, vsync_n, href, dvp_d);
                    err_e = err_e + 1;
                end
            end
        end
    endtask

    // 单场汇总检查（a / b / c）
    task check_frame(input integer f);
        begin
            // a. 有效拍数 / 行数 / 场周期
            if (f_bytes[f] !== FRAME_BYTES) begin
                $display("FAIL a frame%0d href=1 拍数 %0d != %0d", f, f_bytes[f], FRAME_BYTES);
                err_a = err_a + 1;
            end
            if (f_rise[f] !== V_ACTIVE) begin
                $display("FAIL a frame%0d href 上升沿 %0d != %0d（行数不对）",
                         f, f_rise[f], V_ACTIVE);
                err_a = err_a + 1;
            end
            if (f_cyc[f] !== EXP_PERIOD) begin
                $display("FAIL a frame%0d 场周期 %0d != %0d 拍（消隐账不对）",
                         f, f_cyc[f], EXP_PERIOD);
                err_a = err_a + 1;
            end
            if (f_len[f] !== FRAME_BYTES) begin
                $display("FAIL a frame%0d 采到字节 %0d != %0d", f, f_len[f], FRAME_BYTES);
                err_a = err_a + 1;
            end
            // b. 逐字节不符个数
            if (f_mis[f] !== 0) begin
                $display("FAIL b frame%0d 逐字节比对有 %0d 处不符", f, f_mis[f]);
                err_b = err_b + 1;
            end
            // c. vsync_n 低电平宽度
            if (f_vlow[f] < 2 || f_vlow[f] > 8) begin
                $display("FAIL c frame%0d vsync_n 低电平 %0d 拍，不在 2..8", f, f_vlow[f]);
                err_c = err_c + 1;
            end
            $display("  frame%0d: href拍数=%0d 行数=%0d vsync低拍=%0d 场周期=%0d 字节不符=%0d",
                     f, f_bytes[f], f_rise[f], f_vlow[f], f_cyc[f], f_mis[f]);
        end
    endtask

    // 检查项 d：3 场字节序列逐字节完全相同
    task check_identical;
        integer i, bad;
        begin
            bad = 0;
            for (i = 0; i < FRAME_BYTES; i = i + 1) begin
                if (seq[0][i] !== seq[1][i]) begin
                    if (bad < 4) $display("  d 差异 i=%0d f0=%02h f1=%02h", i, seq[0][i], seq[1][i]);
                    bad = bad + 1;
                end
                if (seq[0][i] !== seq[2][i]) begin
                    if (bad < 4) $display("  d 差异 i=%0d f0=%02h f2=%02h", i, seq[0][i], seq[2][i]);
                    bad = bad + 1;
                end
            end
            if (bad != 0) begin
                $display("FAIL d 3 场字节序列不一致：%0d 处不同（图样相位没每场复位？）", bad);
                err_d = err_d + 1;
            end else begin
                $display("  d 3 场字节序列完全一致（%0d 字节/场）", FRAME_BYTES);
            end
        end
    endtask

    task dump_frame(input integer f);
        integer i;
        begin
            $write("  frame%0d 字节:", f);
            for (i = 0; i < FRAME_BYTES; i = i + 1) $write(" %02h", seq[f][i]);
            $write("\n");
        end
    endtask

    task dump_expected;
        integer x, y;
        begin
            $write("  期望字节  :");
            for (y = 0; y < V_ACTIVE; y = y + 1) begin
                for (x = 0; x < H_ACTIVE; x = x + 1) begin
                    $write(" %02h %02h", exp_byte(x, y, 1), exp_byte(x, y, 0));
                end
            end
            $write("\n");
        end
    endtask

    // --------------------------------------------------------------- 主激励流程
    integer frames_before;
    integer i;
    initial begin
        $display("== vid_test_pattern_tb: H_ACTIVE=%0d V_ACTIVE=%0d H_TOTAL=%0d V_TOTAL=%0d ==",
                 H_ACTIVE, V_ACTIVE, H_TOTAL, V_TOTAL);
        $display("   期望：href拍/场=%0d 行/场=%0d 场周期=%0d 拍",
                 FRAME_BYTES, V_ACTIVE, EXP_PERIOD);

        // ---- 复位（run 保持 0，DUT 停在场消隐）----
        rst = 1'b1; run = 1'b0;
        repeat (5) @(posedge clk);
        @(negedge clk); rst <= 1'b0;
        repeat (3) @(posedge clk);

        // ---- 检查项 e①：run=0 时输出必须静止 ----
        check_idle(20);

        // ---- 检查项 a/b/c：连续跑 3 场 ----
        @(negedge clk); run <= 1'b1;
        wait_frames(NFRAMES);
        repeat (2) @(posedge clk);

        for (i = 0; i < NFRAMES; i = i + 1) check_frame(i);
        for (i = 0; i < NFRAMES; i = i + 1) dump_frame(i);
        dump_expected;
        check_identical;

        // ---- 停在场中间也不行：run=0 必须立刻回到场消隐并静止 ----
        @(negedge clk); run <= 1'b0;
        repeat (3) @(posedge clk);
        check_idle(30);

        // ---- 检查项 e②：run 重新拉高后第一场必须从 (x=0,y=0) 起 ----
        frames_before = frame_count;
        @(negedge clk); run <= 1'b1;
        wait_frames(frames_before + 1);
        repeat (2) @(posedge clk);

        check_frame(frames_before);
        dump_frame(frames_before);
        if (seq[frames_before][0] !== exp_byte(0, 0, 1) ||
            seq[frames_before][1] !== exp_byte(0, 0, 0) ||
            seq[frames_before][2] !== exp_byte(1, 0, 1) ||
            seq[frames_before][3] !== exp_byte(1, 0, 0)) begin
            $display("FAIL e run 恢复后首场开头字节 %02h %02h %02h %02h，期望 %02h %02h %02h %02h",
                     seq[frames_before][0], seq[frames_before][1],
                     seq[frames_before][2], seq[frames_before][3],
                     exp_byte(0,0,1), exp_byte(0,0,0), exp_byte(1,0,1), exp_byte(1,0,0));
            err_e = err_e + 1;
        end else begin
            $display("  e run 恢复后首场从 (0,0) 起：%02h %02h %02h %02h（符合）",
                     seq[frames_before][0], seq[frames_before][1],
                     seq[frames_before][2], seq[frames_before][3]);
        end

        // ---- 汇总判据 ----
        repeat (5) @(posedge clk);
        tb_done = 1'b1;         // 先关看门狗，再打令牌，保证只出现一个令牌
        if (err_a == 0 && err_b == 0 && err_c == 0 && err_d == 0 && err_e == 0) begin
            $display("VID_TEST_PATTERN_TB_PASS");
        end else begin
            $display("VID_TEST_PATTERN_TB_FAIL a=%0d b=%0d c=%0d d=%0d e=%0d",
                     err_a, err_b, err_c, err_d, err_e);
        end
        $finish;
    end

endmodule

`default_nettype wire