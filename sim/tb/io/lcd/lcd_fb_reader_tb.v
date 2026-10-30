`timescale 1ns / 1ps
// ============================================================================
// lcd_fb_reader_tb.v —— lcd_fb_reader（经 lcd_status_top）整场逐像素回归
//
// 判据：5 种正交图案各跑完整一帧 800x480，任何像素不符即 FAIL。
//   · 64x64 棋盘：暴露「字库 / 行基址」错误
//   · 0xAAAAAAAA / 0x55555555 竖条：暴露「字内位序」错误（bit0 必须是最左像素）
//   · 1 行横条：暴露「行基址 25 字/行」错误
//   · 单 bit 漂移：暴露「每字第 0 位与第 31 位」边界错误，同时 dump 成
//     build/sim/lcd_frame.txt 供 tools/bram_fb.py --verify-frame 做 Python↔RTL 位序对拍
//
// 成功令牌：LCD_FB_READER_TB_PASS
// ============================================================================
module lcd_fb_reader_tb;

reg pclk = 0;
always #15 pclk = ~pclk;   // 仿真用 33.333 MHz；RTL 时序参数与 TB 时钟频率无关

reg reset_n = 0;
wire [13:0] bram_addr;
reg  [31:0] bram_dout = 32'h0;
wire lcd_de, lcd_hs, lcd_vs, lcd_bl, lcd_clk, lcd_rst;
wire [23:0] lcd_rgb;
wire [31:0] dbg1, dbg2;

// 1bpp 帧缓存模型：480 行 x 25 字 = 12,000 字
reg [31:0] bmem [0:11999];

always @(posedge pclk)
    bram_dout <= bmem[bram_addr];

lcd_status_top dut (
    .pclk(pclk), .reset_n(reset_n),
    .bram_addr(bram_addr), .bram_dout(bram_dout),
    .lcd_de(lcd_de), .lcd_hs(lcd_hs), .lcd_vs(lcd_vs), .lcd_bl(lcd_bl),
    .lcd_clk(lcd_clk), .lcd_rst(lcd_rst), .lcd_rgb(lcd_rgb),
    .dbg1(dbg1), .dbg2(dbg2)
);

integer row, col, i, fd;
integer checked, bad;
integer total_bad = 0;
reg [31:0] word;
reg        exp_bit;
reg [23:0] exp_pix;
reg        dump;

task check_field(input [8*24-1:0] name, input dump_frame);
    begin
        checked = 0;
        bad     = 0;
        dump    = dump_frame;

        while (dut.u_timing.v_cnt != 0) @(posedge pclk);

        for (row = 0; row < 480; row = row + 1) begin
            for (col = 0; col < 800; col = col + 1) begin
                while (!lcd_de) begin
                    @(posedge pclk);
                    #1;
                end

                word    = bmem[row * 25 + (col / 32)];
                exp_bit = word[col % 32];
                exp_pix = exp_bit ? 24'hFFFFFF : 24'h101820;
                checked = checked + 1;

                if (lcd_rgb !== exp_pix) begin
                    bad = bad + 1;
                    if (bad <= 5)
                        $display("MISMATCH %0s row=%0d col=%0d got=%h exp=%h",
                                 name, row, col, lcd_rgb, exp_pix);
                end
                if (dump)
                    $fwrite(fd, "%0d", (lcd_rgb == 24'hFFFFFF) ? 1 : 0);

                @(posedge pclk);
                #1;
            end
            if (dump) $fwrite(fd, "\n");
        end

        total_bad = total_bad + bad;
        if (bad == 0) $display("PASS %0s (%0d px)", name, checked);
        else          $display("FAIL %0s (%0d/%0d px)", name, bad, checked);
    end
endtask

initial begin
    fd = $fopen("build/sim/lcd_frame.txt", "w");
    if (fd == 0) begin
        $display("LCD_FB_READER_TB_FAIL cannot_open build/sim/lcd_frame.txt");
        $finish;
    end

    reset_n = 0;
    repeat (10) @(posedge pclk);
    reset_n = 1;

    // ① 64x64 棋盘
    for (row = 0; row < 480; row = row + 1)
        for (col = 0; col < 25; col = col + 1)
            bmem[row * 25 + col] = (((row / 64) % 2) == ((col / 2) % 2))
                                   ? 32'hFFFFFFFF : 32'h00000000;
    check_field("CHESSBOARD-64", 1'b0);

    // ② 奇数列亮
    for (i = 0; i < 12000; i = i + 1) bmem[i] = 32'hAAAAAAAA;
    check_field("VSTRIPE-ODD", 1'b0);

    // ③ 偶数列亮
    for (i = 0; i < 12000; i = i + 1) bmem[i] = 32'h55555555;
    check_field("VSTRIPE-EVEN", 1'b0);

    // ④ 1 行交替横条
    for (row = 0; row < 480; row = row + 1)
        for (col = 0; col < 25; col = col + 1)
            bmem[row * 25 + col] = (row % 2 == 0) ? 32'hFFFFFFFF : 32'h00000000;
    check_field("HSTRIPE-1LINE", 1'b0);

    // ⑤ 单 bit 漂移（同时 dump 供 Python 对拍位序）
    for (row = 0; row < 480; row = row + 1)
        for (col = 0; col < 25; col = col + 1)
            bmem[row * 25 + col] = (32'h1 << ((row + col) % 32));
    check_field("BIT-DRIFT", 1'b1);

    $fclose(fd);

    if (total_bad == 0) $display("LCD_FB_READER_TB_PASS 5 patterns / 1920000 px");
    else                $display("LCD_FB_READER_TB_FAIL mismatches=%0d", total_bad);
    $finish;
end

endmodule
