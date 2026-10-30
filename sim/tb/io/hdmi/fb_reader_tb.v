`timescale 1ns / 1ps
// fb_reader_tb.v —— fb_reader（1280x720）整场逐像素回归
module fb_reader_tb;

reg pclk = 0;
always #5 pclk = ~pclk;   // 100 MHz 仿真

reg reset_n = 0;
wire [10:0] xpos, ypos;
wire        de, hs, vs;
wire [23:0] pixel_data, rgb;
wire [14:0] bram_addr;
reg  [31:0] bram_dout = 32'h0;
reg  [31:0] bmem [0:28799];

always @(posedge pclk)
    bram_dout <= bmem[bram_addr];

video_driver u_video (
    .pixel_clk(pclk), .sys_rst_n(reset_n),
    .video_hs(hs), .video_vs(vs), .video_de(de), .video_rgb(rgb),
    .data_req(), .pixel_data(pixel_data),
    .pixel_xpos(xpos), .pixel_ypos(ypos)
);

fb_reader u_fb (
    .pclk(pclk), .reset_n(reset_n),
    .xpos(xpos), .ypos(ypos),
    .bram_addr(bram_addr), .bram_dout(bram_dout),
    .pixel_data(pixel_data)
);

integer row, col, i, fd;
integer checked, bad, total_bad;
reg [31:0] word;
reg        exp_bit;
reg [23:0] exp_pix;
reg        dump;

task check_field(input [8*24-1:0] name, input dump_frame);
    begin
        checked = 0; bad = 0; dump = dump_frame;
        while (u_video.cnt_v != 0) @(posedge pclk);

        for (row = 0; row < 720; row = row + 1) begin
            for (col = 0; col < 1280; col = col + 1) begin
                while (!de) begin
                    @(posedge pclk); #1;
                end
                word    = bmem[row * 40 + (col / 32)];
                exp_bit = word[col % 32];
                exp_pix = exp_bit ? 24'hFFFFFF : 24'h101820;
                checked = checked + 1;
                if (rgb !== exp_pix) begin
                    bad = bad + 1;
                    if (bad <= 5)
                        $display("MISMATCH %0s row=%0d col=%0d got=%h exp=%h", name, row, col, rgb, exp_pix);
                end
                if (dump) $fwrite(fd, "%0d", (rgb == 24'hFFFFFF) ? 1 : 0);
                @(posedge pclk); #1;
            end
            if (dump) $fwrite(fd, "\n");
        end

        total_bad = total_bad + bad;
        if (bad == 0) $display("PASS %0s (%0d px)", name, checked);
        else          $display("FAIL %0s (%0d/%0d px)", name, bad, checked);
    end
endtask

initial begin
    total_bad = 0;
    fd = $fopen("build/sim/hdmi_frame.txt", "w");
    if (fd == 0) begin
        $display("FB_READER_TB_FAIL cannot_open");
        $finish;
    end

    reset_n = 0;
    repeat (5) @(posedge pclk);
    reset_n = 1;

    // ① 64x64 棋盘
    for (row = 0; row < 720; row = row + 1)
        for (col = 0; col < 40; col = col + 1)
            bmem[row * 40 + col] = (((row / 64) % 2) == ((col / 2) % 2))
                                   ? 32'hFFFFFFFF : 32'h00000000;
    check_field("CHESSBOARD-64", 1'b0);

    // ② 竖条 0xAAAAAAAA
    for (i = 0; i < 28800; i = i + 1) bmem[i] = 32'hAAAAAAAA;
    check_field("VSTRIPE-ODD", 1'b0);

    // ③ 单 bit 漂移（dump 供 Python 位序对拍）
    for (row = 0; row < 720; row = row + 1)
        for (col = 0; col < 40; col = col + 1)
            bmem[row * 40 + col] = (32'h1 << ((row + col) % 32));
    check_field("BIT-DRIFT", 1'b1);

    $fclose(fd);
    if (total_bad == 0) $display("FB_READER_TB_PASS 3 patterns / 2764800 px");
    else                $display("FB_READER_TB_FAIL mismatches=%0d", total_bad);
    $finish;
end
endmodule
