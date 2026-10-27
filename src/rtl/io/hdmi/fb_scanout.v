// ============================================================================
// fb_scanout.v —— DDR 帧缓存扫描输出（HDMI 视频环出：带检测框的视频）
//
// 与 fb_reader.v（1bpp 状态面板）的区别：
//   fb_reader 读 BRAM 里的 1bpp 面板（28,800 字）；
//   本模块读 DDR 里的 **32bpp 真彩帧**（1280x720x4 B = 3.5 MB），所以走 AXI4 读主机
//   + 4 条行缓存（BRAM），不是直连 BRAM 控制器。
//
// 像素格式：PS 每像素写 32-bit：bit[23:0] = **BGR888**（低字节 B，OpenCV 习惯），
//   bit[31:24] 忽略。行跨距 cfg_stride 字节（= width*4 时最省带宽，但不强制）。
//
// 为什么是 4 条行缓存 + 提前 2 行取：
//   扫描输出严格光栅顺序，只需要「当前行 + 后面 3 行」在缓存里。一行 1280 像素
//   @74.25 MHz = 17.2 us，取一行要 20 个 64 拍突发 + 握手间隙 ≈ 一行时间，所以
//   必须留 3 行提前量（首版只留 1 行，仿真里 8 行全部欠载）。取不满的行用黑像素顶，并计 st_under（上板可观测，不静默）。
//
// 时钟：**整条链路跑 pclk**（74.25 MHz），S_AXI_HP0_ACLK 也接 pclk —— 单时钟域。
//   跨时钟 AXI 桥是这类设计最容易出错的地方，能不做就不做。
//   带宽核算：1280x720x4 B x 30 fps = 110 MB/s，远低于 HP 口能力。
//
// 帧同步：vs 上升沿锁存 cfg_addr（PS 改基址即双缓冲）、disp_line 归零、ready 清零。
//
// 已知偏移：BRAM 读延迟 1 拍，画面相对时序有 **1 像素**固定水平偏移（fb_reader 是 2 像素），
//   整幅平移 1 像素，肉眼不可见；不做补偿是为了省掉一套移位寄存器。
// ============================================================================
module fb_scanout #(
    parameter integer H_ACTIVE = 1280,
    parameter integer V_ACTIVE = 720,
    parameter integer LBUF     = 2048,   // 行缓存深度（像素），须 >= H_ACTIVE
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
// 4 条行缓存：写口在取数引擎，读口在显示侧（同钟，简单双口）
// ---------------------------------------------------------------------------
reg [31:0] lbuf0 [0:LBUF-1];
reg [31:0] lbuf1 [0:LBUF-1];
reg [31:0] lbuf2 [0:LBUF-1];
reg [31:0] lbuf3 [0:LBUF-1];

reg [3:0]  ready;            // bit[k] = 第 k 条缓存已装满一行
reg [10:0] fetch_line;
reg [10:0] fetch_ptr;
reg [10:0] disp_line;
reg [31:0] line_addr;

wire [1:0] fbuf = fetch_line[1:0];
wire [1:0] dbuf = disp_line[1:0];

wire [10:0] width_use = (cfg_width == 11'd0) ? H_ACTIVE[10:0] : cfg_width;
wire [10:0] local_x   = (xpos >= 11'd2) ? (xpos - 11'd2) : 11'd0;
wire [10:0] ahead     = fetch_line - disp_line;

// 显示侧：BRAM 1 拍延迟。★ 行缓存号必须**与数据同拍寄存**（dbuf_q）：
// 行尾那一像素的地址在 disp_line 递增的同一沿发出，若用当前 dbuf 选路，
// 会把下一行的缓存接到这一像素上（首版仿真里每行最后一个像素错成下一行）。
reg [31:0] rd_q0, rd_q1, rd_q2, rd_q3;
reg [1:0]  dbuf_q;
always @(posedge pclk) begin
    rd_q0  <= lbuf0[local_x];
    rd_q1  <= lbuf1[local_x];
    rd_q2  <= lbuf2[local_x];
    rd_q3  <= lbuf3[local_x];
    dbuf_q <= dbuf;
end

reg [31:0] rd_sel;
always @(*) begin
    case (dbuf_q)
        2'd0:    rd_sel = rd_q0;
        2'd1:    rd_sel = rd_q1;
        2'd2:    rd_sel = rd_q2;
        default: rd_sel = rd_q3;
    endcase
end

wire line_ready = ready[dbuf_q];
wire in_range   = cfg_en && de && (local_x < width_use) && line_ready;
assign pixel_data = in_range ? rd_sel[23:0] : 24'h000000;

// ---------------------------------------------------------------------------
// vs 边沿 + 显示行推进
// ---------------------------------------------------------------------------
reg vs_d;
always @(posedge pclk or negedge reset_n) begin
    if (!reset_n) vs_d <= 1'b0;
    else          vs_d <= vs;
end
wire vs_rise = vs & ~vs_d;

always @(posedge pclk or negedge reset_n) begin
    if (!reset_n) begin
        disp_line <= 11'd0;
    end else if (vs_rise) begin
        disp_line <= 11'd0;
    end else if (de && (local_x == (width_use - 11'd1))) begin
        disp_line <= disp_line + 11'd1;
    end
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
            // 最多提前 3 行取（4 条缓存的上限）：提前量就是 DDR 抖动/握手间隙的余量。
            // 只提前 1 行时，取一行与显示一行同速，握手间隙会让取数永久落后 —— 这正是
            // 首版仿真里 8 行全部欠载的原因。
            if ((fetch_line < V_ACTIVE[10:0]) && (ahead <= 11'd3)) begin
                // 开始取一行时先把该缓存的 ready 清掉：否则 DDR 抖动导致本行没取完时，
                // 显示会拿到这一条缓存里**上一轮**的旧行数据（比黑屏更糟：错行）。
                if ((fetch_ptr == 11'd0) && (fetch_line != disp_line)) begin
                    case (fbuf)
                        2'd0:    ready[0] <= 1'b0;
                        2'd1:    ready[1] <= 1'b0;
                        2'd2:    ready[2] <= 1'b0;
                        default: ready[3] <= 1'b0;
                    endcase
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
                case (fbuf)
                    2'd0:    lbuf0[fetch_ptr] <= m_rdata;
                    2'd1:    lbuf1[fetch_ptr] <= m_rdata;
                    2'd2:    lbuf2[fetch_ptr] <= m_rdata;
                    default: lbuf3[fetch_ptr] <= m_rdata;
                endcase
                fetch_ptr  <= fetch_ptr + 11'd1;
                beats_left <= beats_left - 8'd1;
                if (m_rlast || beats_left == 8'd1) begin
                    // ★ rready 不在这里撤销：撤销而 R 通道还有最后一拍没握手完，
                    //   主机等 ARREADY、从机等 RREADY —— 双向死锁（首版仿真就死在这）。
                    if (fetch_ptr + 11'd1 >= width_use) begin
                        // 本行取完
                        case (fbuf)
                            2'd0:    ready[0] <= 1'b1;
                            2'd1:    ready[1] <= 1'b1;
                            2'd2:    ready[2] <= 1'b1;
                            default: ready[3] <= 1'b1;
                        endcase
                        fetch_line <= fetch_line + 11'd1;
                        fetch_ptr  <= 11'd0;
                        line_addr  <= line_addr + {16'd0, cfg_stride};
                        st         <= S_AR;
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

endmodule
