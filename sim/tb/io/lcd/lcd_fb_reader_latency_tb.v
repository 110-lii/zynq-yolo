`timescale 1ns / 1ps
// ============================================================================
// lcd_fb_reader_latency_tb.v —— 同一份 lcd_fb_reader，换 BRAM 读延迟（1/2 拍）
//
// 目的：证明「预取 + 行缓存」不依赖「地址到数据恰好 1 拍」这个假设。
//   017 的队列版就是因为把固定延迟写死，换 MMCM/BD 后整体错位。
//
// 成功令牌：LCD_FB_READER_LATENCY_TB_PASS
// ============================================================================
module lcd_fb_reader_latency_tb;

reg pclk = 0;
always #15 pclk = ~pclk;

reg reset_n = 0;
reg [1:0] lat_mode = 2'd0;   // 0=1 拍，1=2 拍

wire [13:0] bram_addr;
reg  [31:0] bram_dout = 32'h0;
wire lcd_de, lcd_hs, lcd_vs, lcd_bl, lcd_clk, lcd_rst;
wire [23:0] lcd_rgb;
wire [31:0] dbg1, dbg2;

reg [31:0] bmem [0:11999];
reg [31:0] d1;

always @(posedge pclk) begin
    d1 <= bmem[bram_addr];
    bram_dout <= (lat_mode == 2'd0) ? bmem[bram_addr] : d1;
end

lcd_status_top dut (
    .pclk(pclk), .reset_n(reset_n),
    .bram_addr(bram_addr), .bram_dout(bram_dout),
    .lcd_de(lcd_de), .lcd_hs(lcd_hs), .lcd_vs(lcd_vs), .lcd_bl(lcd_bl),
    .lcd_clk(lcd_clk), .lcd_rst(lcd_rst), .lcd_rgb(lcd_rgb),
    .dbg1(dbg1), .dbg2(dbg2)
);

integer row, col, checked, bad;
integer total_bad = 0;
reg [31:0] word;
reg        exp_bit;
reg [23:0] exp_pix;

task check_field(input integer lat);
    begin
        checked = 0;
        bad     = 0;
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
                        $display("MISMATCH lat=%0d row=%0d col=%0d got=%h exp=%h",
                                 lat, row, col, lcd_rgb, exp_pix);
                end
                @(posedge pclk);
                #1;
            end
        end

        total_bad = total_bad + bad;
        if (bad == 0) $display("PASS lat=%0d cycles (%0d px)", lat, checked);
        else          $display("FAIL lat=%0d cycles (%0d/%0d px)", lat, bad, checked);
    end
endtask

initial begin
    // 混合图案：既含棋盘（字序），又含单 bit（位序）
    for (row = 0; row < 480; row = row + 1)
        for (col = 0; col < 25; col = col + 1)
            bmem[row * 25 + col] = (row % 3 == 0)
                                   ? (32'h1 << ((row + col) % 32))
                                   : (((row / 32) % 2 == (col / 2) % 2) ? 32'hFFFFFFFF : 32'h00000000);

    reset_n = 0;
    repeat (10) @(posedge pclk);
    reset_n = 1;

    lat_mode = 2'd0; check_field(1);
    lat_mode = 2'd1; check_field(2);

    if (total_bad == 0) $display("LCD_FB_READER_LATENCY_TB_PASS 1/2 cycles");
    else                $display("LCD_FB_READER_LATENCY_TB_FAIL mismatches=%0d", total_bad);
    $finish;
end

endmodule
