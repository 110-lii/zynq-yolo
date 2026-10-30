// ============================================================================
// lcd_probe_core_tb.v —— lcd_probe_core 的逐像素黄金模型
//
// 目的（对应 ai/sessions/021「未决问题：逐周期黄金模型未写」）：
//   在脱板环境里证明「BRAM 内容 → DE 期 RGB 序列」是一一对应、逐像素正确的，
//   尤其覆盖三条最容易错的口径：
//     1) 行缓存装载时刻（有效行末一拍，装的是下一行）；
//     2) 最后一行的回绕（LAST_ROW 预取 row 0，下一帧第一行不得错位）；
//     3) 帧缓存行内位序（bit0 = 最左像素）。
//
// BRAM 用行为模型（latency 可配 1/2 拍），因此「读侧不依赖固定读延迟」这条
// 设计承诺也被直接验证，而不是靠推演。
//
// 坐标口径（与被测模块的寄存输出对齐）：
//   lcd_de / lcd_rgb_o 是「相对 h_cnt/v_cnt 晚一拍」的寄存输出，
//   故 de=1 那一拍：col = h_cnt-1-H_ACT0，row = v_cnt-1-V_ACT0。
//   黄金模型直接按 (row,col) 取 BRAM，不依赖「第几行第几拍」的推演。
//
// 判据：从第 2 帧起，每个 DE 像素都必须等于 exp_px(row,col)；
//       且每帧 DE 拍数 == H_DISP*V_DISP。任一不符即失败。
// ============================================================================
`timescale 1ns/1ps

module lcd_probe_core_tb;

// ---- 几何：默认小尺寸快跑；+define+REAL_800 / REAL_480 跑真实参数 ----
`ifdef REAL_800
localparam integer H_DISP   = 800;
localparam integer V_DISP   = 480;
localparam integer H_TOTAL  = 1056;
localparam integer V_TOTAL  = 525;
localparam integer H_ACT0   = 216;
localparam integer V_ACT0   = 35;
localparam integer WPR      = H_DISP/8;    // 100（4bpp）
`elsif REAL_480
localparam integer H_DISP   = 480;
localparam integer V_DISP   = 272;
localparam integer H_TOTAL  = 525;
localparam integer V_TOTAL  = 286;
localparam integer H_ACT0   = 43;
localparam integer V_ACT0   = 12;
localparam integer WPR      = H_DISP/8;    // 60（4bpp）
`else
localparam integer H_DISP   = 64;
localparam integer V_DISP   = 8;
localparam integer H_TOTAL  = 96;
localparam integer V_TOTAL  = 16;
localparam integer H_ACT0   = 16;
localparam integer V_ACT0   = 4;
localparam integer WPR      = H_DISP/8;    // 8（4bpp）
`endif
localparam integer LAST_ROW = V_DISP-1;
localparam integer WORDS_IMG = WPR*V_DISP;
localparam integer WORDS     = WORDS_IMG + 16;   // 末尾 16 字是调色板
`ifdef REAL_800
localparam integer RUN_FRAMES = 2;
`elsif REAL_480
localparam integer RUN_FRAMES = 2;
`else
localparam integer RUN_FRAMES = 4;
`endif

reg pclk = 0;
reg reset_n = 0;
reg [31:0] cfg = 32'h0000_0010;    // bit4=1 → 显示帧缓存

wire [17:0] bram_addr;
wire [31:0] bram_dout;
wire lcd_de;
wire [23:0] lcd_rgb_o;
wire [10:0] h_cnt_o, v_cnt_o;
wire [31:0] dbg_tick;

// ---- 行为 BRAM：地址 → 数据延迟由 LAT 决定 ----
// 地址口径必须跟真实 IP 一致：BD 里的 blk_mem_gen 处于字节写使能模式
// （C_USE_BYTE_WEA/WEB=1），addrb 是**字节地址**，内部右移 2 才是字地址。
// 以前这里直接按 mem[bram_addr] 建模，等于假设字地址，于是仿真全绿、
// 上板却整屏 4 倍放大（读成 mem[addr>>2]）——这个台子现在能抓住那类错误。
reg [31:0] mem [0:WORDS-1];
reg [31:0] dout_pipe [0:1];
reg [31:0] rd_word;
integer LAT = 1;
integer k;

always @(*) rd_word = ((bram_addr >> 2) < WORDS) ? mem[bram_addr >> 2] : 32'd0;

always @(posedge pclk) begin
    dout_pipe[0] <= rd_word;
    dout_pipe[1] <= dout_pipe[0];
end
assign bram_dout = (LAT == 1) ? dout_pipe[0] : dout_pipe[1];

lcd_probe_core #(
    .H_DISP(H_DISP), .V_DISP(V_DISP),
    .H_TOTAL(H_TOTAL), .V_TOTAL(V_TOTAL),
    .H_ACT0(H_ACT0), .V_ACT0(V_ACT0),
    .BPP(4), .WPR(WPR), .LAST_ROW(LAST_ROW)
) dut (
    .pclk(pclk), .reset_n(reset_n), .cfg(cfg),
    .bram_addr(bram_addr), .bram_dout(bram_dout),
    .lcd_de(lcd_de), .lcd_rgb_o(lcd_rgb_o),
    .h_cnt_o(h_cnt_o), .v_cnt_o(v_cnt_o), .dbg_tick(dbg_tick)
);

always #2 pclk = ~pclk;   // 250 MHz，只为跑得快

// ---- 调色板（TB 侧与 mem 末尾 16 字同值）----
reg [23:0] pal [0:15];

// ---- 黄金期望：行 r 的第 c 个像素 ----
// 4bpp：每字 8 像素，最低 4 位 = 最左像素；像素值 = 调色板索引
function [23:0] exp_px(input integer r, input integer c);
    reg [31:0] w;
    reg [3:0]  idx;
    begin
        w   = mem[r*WPR + c/8];
        idx = w[4*(c%8) +: 4];
        exp_px = pal[idx];
    end
endfunction

// ---- 帧计数：v_cnt 回卷 ----
integer frame = 0;
integer de_in_frame = 0;
integer de_count [0:8];
integer errors = 0;
integer checked = 0;
integer quiet = 1;          // 只在第 2 帧起比对
reg [10:0] h_d, v_d;
reg de_d;
integer col, row, f;

initial for (f = 0; f < 9; f = f + 1) de_count[f] = 0;

always @(posedge pclk) begin
    h_d <= h_cnt_o;
    v_d <= v_cnt_o;
    de_d <= lcd_de;
    if (reset_n) begin
        // 帧边界：v_cnt 回卷
        if (v_cnt_o == V_TOTAL-1 && h_cnt_o == H_TOTAL-1) begin
            if (frame < 8) de_count[frame] = de_in_frame;
            frame = frame + 1;
            de_in_frame = 0;
            quiet = (frame >= 1);          // 第 0 帧允许未初始化
        end
        if (lcd_de) begin
            col = h_cnt_o - 1 - H_ACT0;
            row = v_cnt_o - V_ACT0;          // 行号在同一拍不变，只有 h 前进一格
            if (!quiet) begin
                // 第 0 帧不比对，只计数
            end else if (col >= 0 && col < H_DISP && row >= 0 && row < V_DISP) begin
                if (lcd_rgb_o !== exp_px(row, col)) begin
                    if (errors < 10)
                        $display("MISMATCH frame=%0d r=%0d c=%0d got=%06h exp=%06h",
                                 frame, row, col, lcd_rgb_o, exp_px(row, col));
                    errors = errors + 1;
                end
                checked = checked + 1;
            end else begin
                if (errors < 10)
                    $display("BAD_XY frame=%0d h=%0d v=%0d c=%0d r=%0d",
                             frame, h_cnt_o, v_cnt_o, col, row);
                errors = errors + 1;
            end
            de_in_frame = de_in_frame + 1;
        end
    end
end

// ---- 测试流程 ----
task run_case(input integer lat);
    begin
        LAT = lat;
        for (k = 0; k < WORDS_IMG; k = k + 1)
            mem[k] = {8'(k/WPR), 8'(k%WPR), 16'hA5A5} ^ (k * 32'h0101_0101);
        // 调色板：16 个互不相同、且低 4 位也不重复的颜色，装载错一位就会比对失败
        for (k = 0; k < 16; k = k + 1) begin
            pal[k] = 24'h10_20_30 + k * 24'h01_03_05;   // 16 个互不相同
            mem[WORDS_IMG + k] = {8'h00, pal[k]};
        end
        reset_n = 0; frame = 0; de_in_frame = 0; errors = 0; checked = 0; quiet = 0;
        for (f = 0; f < 9; f = f + 1) de_count[f] = 0;
        repeat (4) @(posedge pclk);
        reset_n = 1;
        repeat ((RUN_FRAMES + 1) * H_TOTAL * V_TOTAL + 2 * V_TOTAL * H_TOTAL) @(posedge pclk);
        $display("CASE lat=%0d frames=%0d checked_px=%0d errors=%0d", lat, frame, checked, errors);
        for (f = 1; f < 5; f = f + 1)
            if (de_count[f] != H_DISP*V_DISP)
                $display("BAD_DE_COUNT frame=%0d de=%0d expect=%0d",
                         f, de_count[f], H_DISP*V_DISP);
        if (errors != 0)
            $display("LCD_PROBE_CORE_TB_FAIL lat=%0d", lat);
        else
            $display("LCD_PROBE_CORE_TB_OK lat=%0d", lat);
    end
endtask

initial begin
    run_case(1);
    run_case(2);
    $finish;
end

endmodule
