// ============================================================================
// fb_scanout.v —— DDR 帧缓存扫描输出（HDMI 视频环出：带检测框的视频）
//
// 与 fb_reader.v（1bpp 状态面板）的区别：
//   fb_reader 读 BRAM 里的 1bpp 面板（28,800 字）；
//   本模块读 DDR 里的 **32bpp 真彩帧**（1280x720x4 B = 3.5 MB），所以走 AXI4 读主机
//   + 行缓存（BRAM），不是直连 BRAM 控制器。
//
// 像素格式：PS 每像素写 32-bit：bit[23:0] = **RGB888**（bit[23:16]=R，与 dvi_encoder 一致），
//   bit[31:24] 忽略。行跨距 cfg_stride 字节（= width*4 时最省带宽，但不强制）。
//
// ★ 行缓存用**一个**数组（4 行 × LBUF 字，索引 = {行号低 2 位, 列}）而不是 4 个数组：
//   4 个独立数组 + 4 路读 + 4 选 1 的写法，Vivado 2020.2 **推不出 BRAM**，会全部摊成
//   触发器 —— 实测综合出 198,462 个 FDRE（器件只有 106,775），place 直接 DRC 报
//   UTLZ-1 失败。单数组、单读口、单写口是 BRAM 推断的教科书形态。
//
// 为什么是 4 行缓存 + 提前 3 行取：
//   扫描输出严格光栅顺序，只需要「当前行 + 后面 3 行」在缓存里。一行 1280 像素
//   @74.25 MHz = 17.2 us，取一行要 20 个 64 拍突发 + 握手间隙 ≈ 一行时间，
//   所以必须留 3 行提前量（首版只留 1 行，仿真里 8 行全部欠载）。
//   取不满的行用黑像素顶，并计 st_under（上板可观测，不静默）。
//
// 时钟：**整条链路跑 pclk**（74.25 MHz），S_AXI_HP0_ACLK 也接 pclk —— 单时钟域。
//   跨时钟 AXI 桥是这类设计最容易出错的地方，能不做就不做。
//   带宽核算：1280x720x4 B x 30 fps = 110 MB/s，远低于 HP 口能力。
//
// 帧同步：vs 上升沿锁存 cfg_addr（PS 改基址即双缓冲）、disp_line 归零、ready 清零。
// ============================================================================
module fb_scanout #(
    parameter integer H_ACTIVE = 1280,
    parameter integer V_ACTIVE = 720,
    parameter integer LBUF     = 1536,   // 行缓存深度（像素），须 >= H_ACTIVE
    parameter integer BURST    = 64      // 单次 AXI 突发拍数（<=255）
) (
    input               pclk,
    input               reset_n,

    // 视频时序（来自 video_driver）
    input               vs,
    input               de,
    input       [10:0]  xpos,
    input       [10:0]  ypos,

    // 配置（来自 AXI GPIO）
    input       [31:0]  cfg_addr,    // 帧缓存基地址（字节）
    input       [15:0]  cfg_stride,  // 行跨距（字节）
    input       [10:0]  cfg_width,   // 每行像素数
    input               cfg_en,

    // 像素输出（接 video_driver 的 pixel_data）
    output      [23:0]  pixel_data,

    // 状态（接 AXI GPIO 输入通道）
    output reg  [15:0]  st_frames,
    output reg  [15:0]  st_under,

    // 诊断（接 AXI GPIO 输入通道，**vs 沿锁存**，静态可读）
    //   [10:0]  上一帧里「行首行缓存未就绪」的行数（正确值 0）
    //   [21:11] 上一帧里「取数落后于显示」的行数（正确值 0）
    //   [31:22] 上一帧最后显示到的行号（正确值 719）
    // 为什么要有它：2026-10-30 上板，`frames` 30/s、`under=0`、DDR 读回的数据
    // 也逐位正确，画面却仍是一团乱 —— 说明问题在「哪一行被送到屏幕的哪一行」，
    // 而这个量此前**没有任何可观测输出**。三个数一读立刻分开：
    //   · under>0  —— 取数没跟上，行首没数据；
    //   · behind>0 —— 取数被显示甩在后面（ahead 回绕、取数冻结的老缺陷）；
    //   · 两者都 0 而行号是 719 —— 显示行与取数行都在正常推进，
    //     那错就只能在**地址**（stride/base）或**行缓存下标**上。
    output reg  [31:0]  st_dbg,

    // AXI4 读主机（32-bit）
    output reg  [31:0]  m_araddr,
    output reg  [7:0]   m_arlen,
    output wire [2:0]   m_arsize,
    output wire [1:0]   m_arburst,
    output reg          m_arvalid,
    input               m_arready,
    input       [31:0]  m_rdata,
    input               m_rvalid,
    output reg          m_rready,
    input               m_rlast,
    input       [1:0]   m_rresp
);

assign m_arsize  = 3'b010;   // 4 字节 / 拍
assign m_arburst = 2'b01;    // INCR

// ---------------------------------------------------------------------------
// 行缓存：单数组，4 行，索引 {行号[1:0], 列}
// ---------------------------------------------------------------------------
reg [31:0] lbuf [0:4*LBUF-1];

reg [3:0]  ready;            // bit[k] = 第 k 条行缓存已装满一行
reg [10:0] disp_line;        // 调试用：最近一次 de 拍上的显示行号

// ★ 2026-10-31 上板后重写取数引擎：**两条游标 + 允许突发在飞**
//
//   旧写法每行发 20 个 64 拍突发，而且每个突发要**等它的数据全部回来**才发下一个，
//   于是一行的时间 = 1280 拍 + 20 × DDR 读延迟。屏是 720p60，一行只有 1650 个 pclk，
//   只要读延迟超过 18 拍，取数就**永远追不上**显示：缓冲被回头重写，屏上一段段黑横条、
//   画面在跳，而 `under` 照样 0（行首那一刻缓冲还就绪）、`frames` 照样涨。
//   2026-10-31 上板实测：四条横带基本对，但被 13 条黑横条切开、画面跳动；
//   仿真 `+LAT=40` 当场复现同一现象（under=180、98% 像素错），`+LAT=8` 则全绿。
//
//   新结构：请求游标（ar_line/ar_ptr）与写入游标（wr_line/wr_ptr）解耦，
//   允许最多 MAX_OUT 个读突发同时在路上 ⇒ 每行的固定开销从「20 × 延迟」降到「≈1 × 延迟」。
localparam integer MAX_OUT = 4;   // 在飞读突发上限（Zynq HP0 支持 8）

reg [10:0] ar_line;          // 请求游标：下一个要取的源行
reg [10:0] ar_ptr;           //   行内下一拍
reg [31:0] ar_line_addr;     //   该行起始字节地址
reg [10:0] ar_len_q;         // 已发未握手突发的拍数（握手时用它推进游标）
reg [10:0] wr_line;          // 写入游标：下一个要写进缓存的源行
reg [10:0] wr_ptr;
reg [3:0]  outstanding;      // 已发未回的突发数
reg        flush_pending;    // vs 沿时还有突发在飞 ⇒ 等它们回完再重置游标

// ★ 显示行号**由 video_driver 的 ypos 决定**，不再自己数 de 拍。
//   2026-10-30 上板两轮都栽在这里：先是 `local_x == width_use-1` 永远不成立（卡第 0 行），
//   改成数 de 拍之后数量对得上、相位却要跟 data_req/pixel_xpos/video_en 三者互差一拍
//   的时序咬合；只要哪一处推错，行号就整体错位，而 `frames`/`under`/累加和全都不报警。
//   `pixel_ypos` 是 video_driver 自己的场计数（1 起算，有效行内恒定），
//   拿它减 1 就是屏幕行号——**由构造保证**，不依赖任何脉冲计数。
//   ypos 在场消隐期是 0（video_driver 的场计数只在有效行内更新），减 1 会下溢，
//   而 cur_line 一旦变成 2047 之类的大值，`ahead` 就成了很大的负数 ⇒ 取数被判「落后」
//   而全速跑飞（2026-10-31 仿真里 TB 忘了驱动消隐期 ypos，当场复现）。
//   所以这里两端都夹住：0 或越界一律当 0。
wire [10:0] cur_line = ((ypos == 11'd0) || (ypos > V_ACTIVE[10:0])) ? 11'd0 : (ypos - 11'd1);
wire [1:0]  dbuf     = cur_line[1:0];

wire [10:0] width_use = (cfg_width == 11'd0) ? H_ACTIVE[10:0] : cfg_width;

// ★★ 2026-10-31 上板人眼第二次改：读地址 = xpos **本身**，不是 xpos-2 ★★
//
// 旧写法 `local_x = (xpos>=2) ? xpos-2 : 0` 我是按「xpos 从 2 开始」推的，但真机上
// video_driver 给出来的是：
//     xpos(t) = cnt_h(t) - 259        （见 video_driver 里 pixel_xpos 的两级寄存）
//     data_req / video_de 有效拍 cnt_h ∈ [260, 1540)
//   ⇒ 在 de 拍 C（cnt_h = 260+C）上 xpos = C+1，即 xpos ∈ 1..1281。
// BRAM 读是**同步**的：pixel_data(C) = lbuf[ addr(C-1) ]，所以要让
//     pixel_data(C) = 源列 C
// 就必须 addr(C-1) = C，而 xpos(C-1) = C —— **addr 直接取 xpos**。
// 用 xpos-2 的真实后果：屏幕列 C 显示源列 max(C-2,0)，整幅图右移 2 像素，
// 最左 3 列重复源列 0，源列 1278/1279 永远不显示。上板现象正是
// 「白色/蓝色块不贴左边缘」「右边缘那一列的颜色出现在最左边」。
//
// 旧的 local_x 仍保留语义相近的 disp_x（= 屏幕列 C = xpos-1），只给欠载统计与
// 调试用；**取数地址另算**，两者不要再混用 —— 上一版就是把它们当成同一个量。
wire [10:0] disp_x = (xpos == 11'd0) ? 11'd0 : (xpos - 11'd1);   // 屏幕列 C
wire        rd_v   = (xpos < width_use);                         // 本拍读出的这一项是否有效
wire [10:0] rd_x   = rd_v ? xpos : 11'd0;

// ★ 取数提前量必须用**有符号**比较。写成 11 位无符号减法时，
//   一旦取数落后于显示（DDR 抖动、主机抢带宽），`ahead` 会回绕成 2000 多，
//   `ahead <= 3` 永远不成立 ⇒ 取数**永久冻结**，屏幕上就是那 4 条旧行缓存反复显示，
//   而 ready[] 一直是 1，`under` 一直是 0 —— 所有机器判据都「通过」。
//   ahead[12] 置位 = 取数落在显示后面。写成 `wire signed` 再比较，在 iverilog 下
//   实测被当成无符号比，负数直接判成「超前很多」⇒ 冻结；改成显式判最高位，不靠记号的推断。
wire [12:0] ahead  = {2'b00, ar_line} - {2'b00, cur_line};
wire        behind = ahead[12];
// 允许取数：只要不超前 3 行以上（4 条缓存的上限）。落后时同样放行 —— 它必须能追。
// 注意**不能**再加 `ahead != 0` 之类的守卫：帧首 ar_line == cur_line == 0，
// 那条守卫会把第 0 行自己锁死，整帧一行都取不出来（2026-10-31 仿真里就是这么死的）。
wire        can_fetch = behind | (ahead <= 13'd3);

// 索引必须用 **行号[1:0]*LBUF + 列** 而不是位拼接 {行号[1:0], 列}：
// 拼接隐含的步长是 2^11=2048，LBUF 取 1536/256 时会落到数组外（写丢、读回 X）。
wire [12:0] wr_idx = wr_line[1:0] * LBUF[12:0] + wr_ptr;
wire [12:0] rd_idx = dbuf * LBUF[12:0] + rd_x;

// 显示侧：BRAM 1 拍延迟。地址见文件上方 rd_x 的推导；
// `rd_valid` 与 rd_q 同拍寄存，于是「第 C 个 de 拍用的地址是否越界」跟着像素一起
// 传一拍，门控才不会错位一列（上一版就是门控与数据差一拍，最右一列永远黑）。
reg  [31:0] rd_q;
reg         rd_valid;
always @(posedge pclk) begin
    rd_q      <= lbuf[rd_idx];
    rd_valid  <= rd_v;
end

// ★ 写端口必须**独立、无复位**：把数组写放在带异步复位的 FSM always 块里，
//   Vivado 会报 "Unable to infer a block/distributed RAM ... memory pattern not supported"。
wire       wr_en = m_rvalid && m_rready && !flush_pending;   // 组合使能：与 m_rdata 同拍
always @(posedge pclk) begin
    if (wr_en) lbuf[wr_idx] <= m_rdata;
end

wire line_ready = ready[dbuf];
// ★ 这里**不能**再与 `de` 相与。显示侧最后一个像素（video_de=1 那一拍）对应的
//   `data_req` 已经是 0，与 `de` 相与就等于每行最右一列永远是黑——
//   1280 宽的屏上就是一条竖线；`tb_hdmi_scanout_sys` 每行抓到恰好 1 个错像素
//   （col=1279）才发现。消隐期输出什么无所谓：video_driver 那边
//   `video_rgb = video_de ? pixel_data : 0` 已经门控过。
wire in_range   = cfg_en && rd_valid && line_ready;
assign pixel_data = in_range ? rd_q[23:0] : 24'h000000;

// ---------------------------------------------------------------------------
// vs 边沿 + 显示行推进
// ---------------------------------------------------------------------------
reg vs_d;
always @(posedge pclk or negedge reset_n) begin
    if (!reset_n) vs_d <= 1'b0;
    else          vs_d <= vs;
end
wire vs_rise = vs & ~vs_d;

// 显示行号 = video_driver 的 ypos - 1（组合，见文件上方 cur_line）。
// 这里只留一个寄存副本供调试与层次化引用：记「最后一个 de 拍上的行号」。
//   旧写法有两种，都在 2026-10-30 上板翻过车：
//     ① `de && (local_x == width_use-1)` —— 这个等式在真机上永远不成立，行号卡 0；
//     ② 数 de 拍（px_adv）—— 数量对，但相位要和 data_req / pixel_xpos / video_en
//        三者互差一拍的时序咬合，推错一处整体错位，而所有机器判据都不报警。
//   ypos 由场计数直接给出，**不依赖任何脉冲计数**，这是第三种也是唯一由构造保证的写法。
always @(posedge pclk or negedge reset_n) begin
    if (!reset_n)     disp_line <= 11'd0;
    else if (vs_rise) disp_line <= 11'd0;
    else if (de)      disp_line <= cur_line;
end

// ---------------------------------------------------------------------------
// 取数引擎（AXI4 读）：请求游标与写入游标解耦，最多 MAX_OUT 个突发在飞
// ---------------------------------------------------------------------------
wire [10:0] line_left = width_use - ar_ptr;
wire [10:0] burst_now = (line_left > BURST[10:0]) ? BURST[10:0] : line_left;

wire        ar_hs      = m_arvalid && m_arready;
wire        r_hs       = m_rvalid  && m_rready;
wire        r_last_hs  = r_hs && m_rlast;
wire [3:0]  out_next   = outstanding + {3'b000, ar_hs} - {3'b000, r_last_hs};
wire        may_issue  = !m_arvalid && !flush_pending && cfg_en && (cfg_width != 11'd0)
                       && (ar_line < V_ACTIVE[10:0]) && can_fetch
                       && (outstanding < MAX_OUT[3:0]);

always @(posedge pclk or negedge reset_n) begin
    if (!reset_n) begin
        m_araddr     <= 32'd0;
        m_arlen      <= 8'd0;
        m_arvalid    <= 1'b0;
        m_rready     <= 1'b1;
        ar_line      <= 11'd0;
        ar_ptr       <= 11'd0;
        ar_line_addr <= 32'd0;
        ar_len_q     <= 11'd0;
        wr_line      <= 11'd0;
        wr_ptr       <= 11'd0;
        outstanding  <= 4'd0;
        flush_pending<= 1'b0;
        ready        <= 4'b0000;
        st_frames    <= 16'd0;
        st_under     <= 16'd0;
    end else begin
        // ---- 帧边界 ----
        // 还在飞的突发属于上一帧，它们的行号已经没意义：等它们全部回完（期间数据丢弃、
        // 不发新请求），再把两条游标一起归零。屏上这几行会是黑的，但只在「帧末还没追平」
        // 时发生；追平之后每个 vs 都是零等待。
        if (vs_rise) begin
            st_frames    <= st_frames + 16'd1;
            ar_line_addr <= cfg_addr;
            if ((outstanding != 4'd0) || m_arvalid) begin
                flush_pending <= 1'b1;
            end else begin
                ar_line <= 11'd0; ar_ptr <= 11'd0;
                wr_line <= 11'd0; wr_ptr <= 11'd0;
                ready   <= 4'b0000;
            end
        end

        // ---- R 通道：推进写入游标（数据本身在上面那个无复位块里写进 BRAM）----
        if (r_hs && !flush_pending) begin
            if ((wr_ptr == 11'd0) && (wr_line != cur_line))
                ready[wr_line[1:0]] <= 1'b0;      // 开始重写这一行：先摘掉就绪位
            if (wr_ptr + 11'd1 >= width_use) begin
                ready[wr_line[1:0]] <= 1'b1;
                wr_line <= wr_line + 11'd1;
                wr_ptr  <= 11'd0;
            end else begin
                wr_ptr <= wr_ptr + 11'd1;
            end
        end

        // ---- AR 通道：握手后推进请求游标 ----
        if (ar_hs) begin
            if (ar_ptr + ar_len_q >= width_use) begin
                ar_line      <= ar_line + 11'd1;
                ar_ptr       <= 11'd0;
                ar_line_addr <= ar_line_addr + {16'd0, cfg_stride};
            end else begin
                ar_ptr <= ar_ptr + ar_len_q;
            end
            m_arvalid <= 1'b0;
        end

        // ---- 发起下一个突发（此时 ar_line/ar_ptr 都已稳定）----
        if (may_issue) begin
            m_araddr  <= ar_line_addr + {20'd0, ar_ptr, 2'b00};
            m_arlen   <= burst_now[7:0] - 8'd1;
            ar_len_q  <= burst_now;
            m_arvalid <= 1'b1;
        end

        outstanding <= out_next;

        // ---- flush 完成：游标归零，从第 0 行重新取 ----
        if (flush_pending && (out_next == 4'd0) && !m_arvalid) begin
            flush_pending <= 1'b0;
            ar_line <= 11'd0; ar_ptr <= 11'd0;
            wr_line <= 11'd0; wr_ptr <= 11'd0;
            ready   <= 4'b0000;
        end
    end
end

// ---------------------------------------------------------------------------
// 欠载统计（按行计，不按像素刷屏）
// ---------------------------------------------------------------------------
reg under_seen;
always @(posedge pclk or negedge reset_n) begin
    if (!reset_n) begin
        under_seen <= 1'b0;
    end else if (vs_rise) begin
        under_seen <= 1'b0;
    end else if (de && (disp_x == 11'd0)) begin
        if (!line_ready && !under_seen) begin
            under_seen <= 1'b1;
            st_under   <= st_under + 16'd1;
        end else if (line_ready) begin
            under_seen <= 1'b0;
        end
    end
end

// ---------------------------------------------------------------------------
// 帧末快照（诊断，见端口注释）
// ---------------------------------------------------------------------------
reg [10:0] under_lines;     // 本帧行首未就绪的行数
reg [10:0] behind_lines;    // 本帧取数落后于显示的行数
reg [9:0]  dbg_disp;        // 本帧最后显示到的行号（0..719，10 位够）

always @(posedge pclk or negedge reset_n) begin
    if (!reset_n) begin
        under_lines  <= 11'd0;
        behind_lines <= 11'd0;
        dbg_disp     <= 10'd0;
        st_dbg       <= 32'd0;
    end else if (vs_rise) begin
        // 先发布上一帧的快照，再清零本帧的计数 —— 非阻塞赋值，发布的是本帧的值
        st_dbg       <= {dbg_disp, behind_lines, under_lines};
        behind_lines <= 11'd0;
        under_lines  <= 11'd0;
    end else if (de && (disp_x == 11'd1)) begin
        dbg_disp <= cur_line[9:0];
        // 两个独立口径：行首没数据（欠载）／取数被显示甩在后面（冻结风险）
        if (!line_ready && (under_lines != 11'd2047))
            under_lines <= under_lines + 11'd1;
        if (behind && (behind_lines != 11'd2047))
            behind_lines <= behind_lines + 11'd1;
    end
end

endmodule