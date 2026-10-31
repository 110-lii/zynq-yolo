// ============================================================================
// hdmi_video_top.v —— HDMI 视频环出顶层（1280x720@30，带检测框的视频）
//
// 与 hdmi_status_top.v（1bpp 文字状态面板）并列的第二个 HDMI 顶层：
//   PS 每帧画好带框的画面（BGR888 → 32bpp 字）写进 DDR 帧缓存，
//   本模块用 fb_scanout 经 AXI4 读主机把 DDR 读出来，交 video_driver 出时序，
//   再经 dvi_transmitter_top 走 TMDS 到 HDMI 连接器。
//
// 端口分组：
//   · 视频时序侧：pclk / pclk_x5 / reset_n（与状态面板一致，74.25 / 371.25 MHz）
//   · 寄存器侧：cfg_addr / cfg_stride / cfg_width / cfg_en（AXI GPIO 输出）
//               st_frames / st_under（AXI GPIO 输入）
//   · AXI4 读主机：接 PS 的 S_AXI_HP0（**ACLK 必须与 pclk 同源**，见 fb_scanout 头注释）
//   · TMDS 输出：四对差分
//
// 注意：ARID/RID 固定为 0（单主设备、单突发在飞），因此不需要 ID 路由。
// ============================================================================
module hdmi_video_top (
    input               pclk,
    input               pclk_x5,
    input               reset_n,

    // 寄存器（AXI GPIO 输出通道 1 / 通道 2）
    //   cfg_pack[15:0]  = 行跨距（字节）
    //   cfg_pack[26:16] = 每行像素数
    //   cfg_pack[27]    = 使能
    input       [31:0]  cfg_addr,
    input       [31:0]  cfg_pack,
    // 状态（AXI GPIO 输入通道）：{st_frames[15:0], st_under[15:0]}
    output      [31:0]  st_status,
    // 诊断（AXI GPIO 输入通道 2）：**`cfg_pack[31:29]==0` 时是上一整帧像素累加和**（老口径，
    //   仿真 TB 用这一路）；`==7` 时是位置加权和 Σ(列号)×像素；其余值转发 fb_scanout 的 st_dbg。
    //   为什么挤在这一个口上：`gpio_hdmi_dbg @ 0x41290000` 实测读不到（见下方 ★★），
    //   而 0x41210008 这条读回路径是被实测验证过的。
    output      [31:0]  st_sum,
    // 诊断（另一个 GPIO）：{under_lines, fetch_line, disp_line}，vs 沿锁存。
    //   ★ 2026-10-31 16:05 实测这个 GPIO 的地址 0x41290000 **由 gpio_hdmi_st 应答**，
    //     所以本口目前读不到；诊断量请走 st_sum（见上）。
    output      [31:0]  st_dbg,

    // AXI4 读主机（接 S_AXI_HP0）
    output      [31:0]  m_axi_araddr,
    output      [7:0]   m_axi_arlen,
    output      [2:0]   m_axi_arsize,
    output      [1:0]   m_axi_arburst,
    output      [5:0]   m_axi_arid,
    output              m_axi_arvalid,
    input               m_axi_arready,
    input       [31:0]  m_axi_rdata,
    input       [5:0]   m_axi_rid,
    input       [1:0]   m_axi_rresp,
    input               m_axi_rlast,
    input               m_axi_rvalid,
    output              m_axi_rready,

    output              tmds_clk_p,
    output              tmds_clk_n,
    output      [2:0]   tmds_data_p,
    output      [2:0]   tmds_data_n
);

wire [10:0] pixel_xpos;
wire [10:0] pixel_ypos;
wire [23:0] pixel_data;
wire [23:0] video_rgb;
wire        video_hs;
wire        video_vs;
wire        video_de;
wire        data_req;

assign m_axi_arid = 6'd0;

// ★ 跨时钟域：cfg_* 来自 PS 侧 AXI GPIO（clk_fpga_0, 83.33 MHz），本模块跑 pclk(74.25 MHz)。
//   两根时钟同出一个 MMCM 但频率不是整数倍，工具会当同步路径去收敛 —— 实测 WNS -8.0 ns、
//   166 个失败端点。这些值本质是**准静态配置**（赛前/换缓冲时写一次），所以做 2 拍同步，
//   并在 XDC 里把两组时钟声明为异步（见 build/scripts/hdmi_video.xdc）。
//   帧内一次性用的 cfg_addr 还会在 fb_scanout 的 vs 沿锁存，多比特撕裂不会落到画面上。
reg [31:0] cfg_addr_s1, cfg_addr_s2;
reg [31:0] cfg_pack_s1, cfg_pack_s2;
always @(posedge pclk or negedge reset_n) begin
    if (!reset_n) begin
        cfg_addr_s1 <= 32'd0; cfg_addr_s2 <= 32'd0;
        cfg_pack_s1 <= 32'd0; cfg_pack_s2 <= 32'd0;
    end else begin
        cfg_addr_s1 <= cfg_addr; cfg_addr_s2 <= cfg_addr_s1;
        cfg_pack_s1 <= cfg_pack; cfg_pack_s2 <= cfg_pack_s1;
    end
end

wire [31:0] cfg_addr_q = cfg_addr_s2;
wire [31:0] cfg_pack_q = cfg_pack_s2;

wire [15:0] cfg_stride = cfg_pack_q[15:0];
wire [10:0] cfg_width  = cfg_pack_q[26:16];
wire        cfg_en     = cfg_pack_q[27];
// ★ 诊断 2（2026-10-30）：cfg_pack[28] = 内置彩条。置 1 时送 TMDS 的是本模块
//   用 de 计出来的图案，**完全不碰 DDR**。它和「诊断 1」是正交的一刀：
//   彩条出得来 ⇒ TMDS/时钟/引脚链路好，坏的一定是 DDR 那一段；出不来 ⇒ 反过来。
//   bit28 不在任何工具写入的掩码里（工具只写 stride/width/en），默认 0。
wire        cfg_test   = cfg_pack_q[28];
wire [15:0] st_frames;
wire [15:0] st_under;

assign st_status = {st_frames, st_under};

// ---------------------------------------------------------------------------
// 诊断 1：帧内像素累加和 —— 让「PL 从 DDR 到底读到了什么」变成机器可判的数
//
// 为什么需要它：`frames` 在涨只证明 vs 在跑，`under=0` 只证明行缓存**就绪**，
// 两者都不碰像素值（见 ai/sessions/022 §7.12 第 5 条与 §7.13）。上板时屏幕
// 一直是一个颜色、换帧缓存内容也不变，光靠人眼无法区分「DDR 读回的就不是我们写的」
// 和「读对了但 TMDS 送错了」。做法：把本帧显示用到的像素逐个累加（模 2^32），
// 在 vs 上升沿锁存。主机侧写完帧缓存后按同样的公式算一遍期望值即可逐位比对。
//
//   期望值 = Σ(pixel & 0xFFFFFF) mod 2^32，遍历 1280×720 个像素
//
// ★ 口径必须钉在 **video_de + video_rgb** 上，即「真正送进 TMDS 的那批像素」。
//   2026-10-31 仿真发现：原来累加的是 data_req 拍上的 pixel_data，比显示早一拍，
//   于是采样到的列是 `0,0,0,0,1,…,1276`，而屏幕上显示的是 `0,0,0,1,…,1277` ——
//   两者不是同一批像素，主机按屏幕口径算出来的期望值永远差一点（上板那 0.8% 里
//   至少有一部分是这么来的）。诊断量的口径必须和它声称测的东西逐像素一致。
// ---------------------------------------------------------------------------
reg [31:0] px_sum;
reg [31:0] st_sum_r;
reg        vs_d_sum;
always @(posedge pclk or negedge reset_n) begin
    if (!reset_n) begin
        px_sum    <= 32'd0;
        st_sum_r  <= 32'd0;
        vs_d_sum  <= 1'b0;
    end else begin
        vs_d_sum <= video_vs;
        if (video_vs & ~vs_d_sum) begin
            px_sum   <= 32'd0;
            st_sum_r <= px_sum;
        end else if (video_de) begin
            px_sum <= px_sum + {8'd0, video_rgb};
        end
    end
end
// ★★ 2026-10-31 16:05 实测：诊断口 `gpio_hdmi_dbg @ 0x41290000` **读出来的是 gpio_hdmi_st**。
//   证据（同一片 15:57 的 bit，板上三连测）：
//     0x41290000 = 0x41210000 = {frames, under}（frames 每 0.4 s 刚好 +24 = 60 fps × 0.4 s）；
//     0x41290008 = 0x41210008 = st_sum（换上 0x7F7F7F 的纯灰帧后两者同时变成 F0E9F000）；
//     写 cfg_pack[31:29] = 5（选「帧首写入数据」）后 0x41290000 照旧是 frames —— 选哪一路都没用。
//   而 BD 生成的 system.bd 里 `hdmi_video_0/st_dbg → gpio_hdmi_dbg/gpio_io_i` **是连对的**、
//   地址表也对（hwh: 0x41290000 / 64K），所以坏在地址解码这一段（hdmi_smc 的 M01 没人应答，
//   读到的是 M00 上 st 那一片），不是 RTL 连线。
//   教训：**诊断量不能只挂在一条没被验证过的读回路径上** —— 这一条此前从未被读出过，
//   之前几次「读 st_dbg 得到 719/0/0」的结论其实读的是 {frames, under}，是假的。
//   兜底：sel = 000 时这个口仍出累加和（老口径逐位不变，仿真 TB 用的就是这一路）；
//        sel ≠ 000 时改出 st_dbg 选中的那一路诊断。PS 侧：写 cfg_pack[31:29]=sel，再读 0x41210008。
assign st_sum = (cfg_pack_q[31:29] == 3'd0) ? st_sum_r
              : (cfg_pack_q[31:29] == 3'd7) ? st_pos_r
              :                               st_dbg;

// ---------------------------------------------------------------------------
// 诊断 1b（sel=111）：**位置加权和** Σ (屏幕列号) × 像素，模 2^32
//
// 为什么还缺这一味：整帧累加和 Σpixel 对任何「改变像素顺序」的映射都是瞎的
// （2026-10-31 三次踩到：屏上整幅转了 1024 列，累加和逐位相等）。把屏幕列号乘进去，
// 「第 c 列显示的是源第几列」就变成一个**与顺序有关、却只需读一个数**的量：
//   · 主机按 `Σ_c (c+1) × word[c]` 算期望，word[c] = 源帧第 c 列 → 一一对应时逐位相等；
//   · 若整行循环移了 k 列（word[c] = 源[(c+1024) mod 1280]），期望值随 k 单调变化，
//     主机把 1280 个 k 全算一遍就能**反解出 k**，不必再看屏。
// 权重取 11 位屏幕列号（1 起算），与 `pixel_xpos` 同源，不受任何行内计数推断影响。
// 乘加不在像素通路上（只挂一支旁路），74.25 MHz 下一拍乘是轻松的。
// ---------------------------------------------------------------------------
reg [31:0] px_pos;
reg [31:0] st_pos_r;
always @(posedge pclk or negedge reset_n) begin
    if (!reset_n) begin
        px_pos   <= 32'd0;
        st_pos_r <= 32'd0;
    end else if (video_vs & ~vs_d_sum) begin
        px_pos   <= 32'd0;
        st_pos_r <= px_pos;
    end else if (video_de) begin
        px_pos   <= px_pos + ({21'd0, pixel_xpos} * {8'd0, video_rgb});
    end
end

// ---------------------------------------------------------------------------
// 诊断 2：内置彩条（不碰 DDR）
//   bar_x 用「本行发过多少拍 de」推进 —— 与 fb_scanout 修好后的行推进同口径；
//   第 0 段故意做成**按行变化的灰阶**，这样即便「每行都读第 0 行」那类缺陷复现，
//   画面上也是一条纯色带而不是灰阶，肉眼一眼能分开。
// ---------------------------------------------------------------------------
wire [10:0] bar_w   = (cfg_width == 11'd0) ? 11'd1280 : cfg_width;
reg  [10:0] bar_x;
reg  [9:0]  bar_y;
always @(posedge pclk or negedge reset_n) begin
    if (!reset_n) begin
        bar_x <= 11'd0;
        bar_y <= 10'd0;
    end else if (video_vs & ~vs_d_sum) begin      // 复用上面那拍的 vs 沿
        bar_x <= 11'd0;
        bar_y <= 10'd0;
    end else if (video_de) begin
        if (bar_x + 11'd1 >= bar_w) begin
            bar_x <= 11'd0;
            bar_y <= bar_y + 10'd1;
        end else begin
            bar_x <= bar_x + 11'd1;
        end
    end
end

wire [2:0] bar_idx = bar_x[9:7];                 // 128 px 一段
reg  [23:0] bar_rgb;
always @(*) begin
    case (bar_idx)
        3'd0: bar_rgb = {bar_y[7:0], bar_y[7:0], bar_y[7:0]};  // 按行灰阶（测行推进）
        3'd1: bar_rgb = 24'hFFFF00;   // 黄
        3'd2: bar_rgb = 24'h00FFFF;   // 青
        3'd3: bar_rgb = 24'h00FF00;   // 绿
        3'd4: bar_rgb = 24'hFF00FF;   // 品红
        3'd5: bar_rgb = 24'hFF0000;   // 红
        3'd6: bar_rgb = 24'h0000FF;   // 蓝
        default: bar_rgb = 24'hFFFFFF; // 白
    endcase
end

wire [23:0] dvi_rgb = cfg_test ? bar_rgb : video_rgb;

video_driver u_video_driver(
    .pixel_clk  (pclk),
    .sys_rst_n  (reset_n),
    .video_hs   (video_hs),
    .video_vs   (video_vs),
    .video_de   (video_de),
    .video_rgb  (video_rgb),
    .data_req   (data_req),
    .pixel_data (pixel_data),
    .pixel_xpos (pixel_xpos),
    .pixel_ypos (pixel_ypos)
);

fb_scanout #(
    .H_ACTIVE (1280),
    .V_ACTIVE (720),
    .LBUF     (2048),
    // ★ BURST 255 → 128：AXI4 规定 INCR 突发**不许跨 4 KB 边界**，而一行 5120 字节的行基址
    //   加 4080 字节处的 1020 字节突发会跨 4096（每 4 行出现一次）。512 字节突发且行基址
    //   5120 = 512×10 天然 512 对齐 ⇒ 永不跨界。
    .BURST    (128)
) u_fb_scanout (
    .pclk       (pclk),
    .reset_n    (reset_n),
    .vs         (video_vs),
    .de         (data_req),
    .xpos       (pixel_xpos),
    .ypos       (pixel_ypos),
    .cfg_addr   (cfg_addr_q),
    .cfg_stride (cfg_stride),
    .cfg_width  (cfg_width),
    .cfg_en     (cfg_en),
    .pixel_data (pixel_data),
    .st_frames  (st_frames),
    .st_under   (st_under),
    .st_dbg     (st_dbg),
    .dbg_sel    (cfg_pack_q[31:29]),
    .m_araddr   (m_axi_araddr),
    .m_arlen    (m_axi_arlen),
    .m_arsize   (m_axi_arsize),
    .m_arburst  (m_axi_arburst),
    .m_arvalid  (m_axi_arvalid),
    .m_arready  (m_axi_arready),
    .m_rdata    (m_axi_rdata),
    .m_rvalid   (m_axi_rvalid),
    .m_rready   (m_axi_rready),
    .m_rlast    (m_axi_rlast),
    .m_rresp    (m_axi_rresp)
);

// 端口名以 dvi_transmitter_top.v 为准：video_din / video_hsync / video_vsync
dvi_transmitter_top u_dvi(
    .pclk        (pclk),
    .pclk_x5     (pclk_x5),
    .reset_n     (reset_n),
    .video_din   (dvi_rgb),
    .video_hsync (video_hs),
    .video_vsync (video_vs),
    .video_de    (video_de),
    .tmds_clk_p  (tmds_clk_p),
    .tmds_clk_n  (tmds_clk_n),
    .tmds_data_p (tmds_data_p),
    .tmds_data_n (tmds_data_n),
    .tmds_oen    ()
);

endmodule
