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

localparam [2:0] S_IDLE  = 3'd0;
localparam [2:0] S_AR    = 3'd1;   // 发起突发
localparam [2:0] S_ARW   = 3'd2;   // 等 ARREADY（ARVALID 必须保持到握手完成）
localparam [2:0] S_RDATA = 3'd3;

assign m_arsize  = 3'b010;   // 4 字节 / 拍
assign m_arburst = 2'b01;    // INCR

// ---------------------------------------------------------------------------
// 行缓存：单数组，4 行，索引 {行号[1:0], 列}
// ---------------------------------------------------------------------------
reg [31:0] lbuf [0:4*LBUF-1];

reg [3:0]  ready;            // bit[k] = 第 k 条行缓存已装满一行
reg [10:0] fetch_line;
reg [10:0] fetch_ptr;
reg [10:0] disp_line;        // 调试用：最近一次 de 拍上的显示行号
reg [31:0] line_addr;

wire [1:0]  fbuf = fetch_line[1:0];

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
wire [10:0] local_x   = (xpos >= 11'd2) ? (xpos - 11'd2) : 11'd0;

// ★ 取数提前量必须用**有符号**比较。写成 11 位无符号减法时，
//   一旦取数落后于显示（DDR 抖动、主机抢带宽），`ahead` 会回绕成 2000 多，
//   `ahead <= 3` 永远不成立 ⇒ 取数**永久冻结**，屏幕上就是那 4 条旧行缓存反复显示，
//   而 ready[] 一直是 1，`under` 一直是 0 —— 所有机器判据都「通过」。
//   ahead[12] 置位 = 取数落在显示后面。写成 `wire signed` 再比较，在 iverilog 下
//   实测被当成无符号比，负数直接判成「超前很多」⇒ 冻结；改成显式判最高位，不靠记号的推断。
wire [12:0] ahead  = {2'b00, fetch_line} - {2'b00, cur_line};
wire        behind = ahead[12];
// 允许取数：只要不超前 3 行以上（4 条缓存的上限）。落后时同样放行 —— 它必须能追。
// 注意**不能**再加 `ahead != 0` 之类的守卫：帧首 fetch_line == cur_line == 0，
// 那条守卫会把第 0 行自己锁死，整帧一行都取不出来（2026-10-31 仿真里就是这么死的）。
// 取数正好落在当前显示行时，下面的 ready 清除判断 `fetch_line != cur_line` 会保护它。
wire        can_fetch = behind | (ahead <= 13'd3);

// 索引必须用 **fbuf*LBUF + 列** 而不是位拼接 {fbuf, 列}：
// 拼接隐含的步长是 2^11=2048，LBUF 取 1536/256 时会落到数组外（写丢、读回 X）。
wire [12:0] wr_idx = fbuf * LBUF[12:0] + fetch_ptr;
wire [12:0] rd_idx = dbuf * LBUF[12:0] + local_x;

// 显示侧：BRAM 1 拍延迟。读地址只用此刻的 cur_line（= ypos-1），天然对准本行；
// 行尾那一像素在 ypos 变化前发出，所以不会串到下一行的缓存。
reg  [31:0] rd_q;
always @(posedge pclk) begin
    rd_q <= lbuf[rd_idx];
end

// ★ 写端口必须**独立、无复位**：把数组写放在带异步复位的 FSM always 块里，
//   Vivado 会报 "Unable to infer a block/distributed RAM ... memory pattern not supported"。
wire       wr_en = (st == S_RDATA) && m_rvalid;   // 组合使能：与 m_rdata 同拍
always @(posedge pclk) begin
    if (wr_en) lbuf[wr_idx] <= m_rdata;
end

wire line_ready = ready[dbuf];
// ★ 这里**不能**再与 `de` 相与。显示侧最后一个像素（video_de=1 那一拍）对应的
//   `data_req` 已经是 0，与 `de` 相与就等于每行最右一列永远是黑——
//   1280 宽的屏上就是一条竖线；`tb_hdmi_scanout_sys` 每行抓到恰好 1 个错像素
//   （col=1279）才发现。消隐期输出什么无所谓：video_driver 那边
//   `video_rgb = video_de ? pixel_data : 0` 已经门控过。
wire in_range   = cfg_en && (local_x < width_use) && line_ready;
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
// 取数引擎（AXI4 读）
// ---------------------------------------------------------------------------
reg [2:0]  st;
reg [7:0]  beats_left;

always @(posedge pclk or negedge reset_n) begin
    if (!reset_n) begin
        st         <= S_IDLE;
        m_araddr   <= 32'd0;
        m_arlen    <= 8'd0;
        m_arvalid  <= 1'b0;
        m_rready   <= 1'b1;
        fetch_line <= 11'd0;
        fetch_ptr  <= 11'd0;
        line_addr  <= 32'd0;
        ready      <= 4'b0000;
        beats_left <= 8'd0;
        st_frames  <= 16'd0;
        st_under   <= 16'd0;
    end else if (vs_rise) begin
        // 帧边界：锁存基址、行号归零、丢在飞的突发（半行写入的缓存不能再用）
        st         <= (cfg_en && (cfg_width != 11'd0)) ? S_AR : S_IDLE;
        m_arvalid  <= 1'b0;
        m_rready   <= 1'b1;
        fetch_line <= 11'd0;
        fetch_ptr  <= 11'd0;
        line_addr  <= cfg_addr;
        ready      <= 4'b0000;
        beats_left <= 8'd0;
        st_frames  <= st_frames + 16'd1;
    end else begin
        case (st)
        S_IDLE: begin
            if (cfg_en && (cfg_width != 11'd0)) begin
                fetch_line <= 11'd0;
                fetch_ptr  <= 11'd0;
                line_addr  <= cfg_addr;
                ready      <= 4'b0000;
                st         <= S_AR;
            end
        end

        S_AR: begin
            // 最多提前 3 行取（4 条缓存的上限）：这个提前量就是 DDR 抖动/握手间隙的余量。
            // can_fetch 用有符号比较，取数落后时放行追、正好等于当前行时按住（见上方注释）
            if ((fetch_line < V_ACTIVE[10:0]) && can_fetch) begin
                // 开始取一行时先把该行的 ready 清掉：否则本行没取完时，
                // 显示会拿到这一条缓存里**上一轮**的旧行数据（比黑屏更糟：错行）
                if ((fetch_ptr == 11'd0) && (fetch_line != cur_line)) begin
                    ready[fbuf] <= 1'b0;
                end
                m_araddr <= line_addr + {20'd0, fetch_ptr, 2'b00};
                if (width_use - fetch_ptr > BURST[10:0]) begin
                    m_arlen    <= BURST[7:0] - 8'd1;
                    beats_left <= BURST[7:0];
                end else begin
                    m_arlen    <= (width_use - fetch_ptr) - 11'd1;
                    beats_left <= width_use - fetch_ptr;
                end
                m_arvalid <= 1'b1;
                st        <= S_ARW;
            end
        end

        S_ARW: begin
            // ARVALID 保持到 ARREADY —— 提前撤会丢突发（AXI4 协议）
            if (m_arready) begin
                m_arvalid <= 1'b0;
                st        <= S_RDATA;
            end
        end

        S_RDATA: begin
            m_rready <= 1'b1;
            if (m_rvalid) begin
                fetch_ptr  <= fetch_ptr + 11'd1;
                beats_left <= beats_left - 8'd1;
                if (m_rlast || beats_left == 8'd1) begin
                    // ★ rready 不在这里撤销：撤销而 R 通道还有最后一拍没握手完，
                    //   主机等 ARREADY、从机等 RREADY —— 双向死锁（首版仿真就死在这）
                    if (fetch_ptr + 11'd1 >= width_use) begin
                        ready[fbuf] <= 1'b1;
                        fetch_line  <= fetch_line + 11'd1;
                        fetch_ptr   <= 11'd0;
                        line_addr   <= line_addr + {16'd0, cfg_stride};
                        st          <= S_AR;
                    end else begin
                        st <= S_AR;   // 同一行还要再来一拍
                    end
                end
            end
        end

        default: st <= S_IDLE;
        endcase
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
    end else if (de && (local_x == 11'd0)) begin
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
    end else if (de && (local_x == 11'd1)) begin
        dbg_disp <= cur_line[9:0];
        // 两个独立口径：行首没数据（欠载）／取数被显示甩在后面（冻结风险）
        if (!line_ready && (under_lines != 11'd2047))
            under_lines <= under_lines + 11'd1;
        if (behind && (behind_lines != 11'd2047))
            behind_lines <= behind_lines + 11'd1;
    end
end

endmodule