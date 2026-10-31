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
    //
    // ★★ 2026-10-31 晚：**临时诊断版**。上板照片实测「屏幕第 c 列 = 源第
    //    (c+1024) mod 1280 列」，可 DDR 内容逐像素正确、整帧求和又是一一对应
    //    （既不是线性流错位，也不是丢重复），说明旋转发生在「缓存里的行本身」——
    //    而这一路此前**没有任何可观测输出**。这里把 4 个相位量都做成寄存器，
    //    用 cfg_pack[31:29] 选哪一路上 st_dbg（bit31/30/29 以前恒 0，工具从不写）：
    //      000 原样 {disp, behind, under}（仿真 TB 用的就是这一路，默认）
    //      001 {d_cnt@vs, ar_ptr@vs}       帧末描述符队列深度（应 0）、请求游标（应 0）
    //      010 rd_idx（屏幕第 0 列那次读）   应 = dbuf*LBUF + 1
    //      011 beats（帧内写入拍数）         应 = 720*1280 = 921600
    //      100 帧内第一条 AR 地址 − cfg_addr（应 0）
    //      101 帧首写入 row0 第 0 列的那一拍数据（ramp 图：值 − 1 = 取数流起点源列）
    //      110/111 屏幕第 512 / 1024 列**实际送出的像素**（ramp 图：值 − 1 = 该列源列）
    //    后三个是决定性的一刀：读回源列号 0/512/1024 ⇒ 一一对应；1024/256/0 ⇒ 每行循环
    //    移位。累计和看不见它，人眼只能给出「整除 128」，这是第一次让机器直接报出来。
    input       [2:0]   dbg_sel,
    output wire [31:0]  st_dbg,

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

// ★★ 行号标签（2026-10-31 第三次上板后补）★★
//   实测：屏第 719 行显示的是源第 591 行 —— 行偏 128，正好是 4 的整数倍，
//   而 `ready[]` 只按「行号低 2 位」索引 ⇒ 这种滞后对 ready[] **完全隐形**：
//   under=0、frames 正常、每帧写入拍数一拍不差，屏上却整体错位。
//   每槽再记一个「最后一次整行写完的源行号」，显示侧只认 tag == cur_line。
//   取数万一错行，屏幕会继续显示**同号行**的上一帧内容（位置一定是对的），
//   而不是把别的行的内容贴上来 —— 位置正确性由构造保证，新鲜度可以退让。
reg [10:0] ltag0, ltag1, ltag2, ltag3;
reg [10:0] ltag_c;
always @(*) begin
    case (dbuf)
        2'd0:    ltag_c = ltag0;
        2'd1:    ltag_c = ltag1;
        2'd2:    ltag_c = ltag2;
        default: ltag_c = ltag3;
    endcase
end
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
//   新结构：请求游标（ar_line/ar_ptr）与写入落点（AR 描述符队列）解耦，
//   允许最多 MAX_OUT 个读突发同时在路上 ⇒ 每行的固定开销从「20 × 延迟」降到「≈1 × 延迟」。
//   ★★ 2026-10-31 16:50 上板实测后的第二次重写：写入位置**不再由拍数推** ★★
//   诊断版读数（st 第二通道 sel1/3/4/5/6）：帧末游标 0/0、帧内写入 921600 拍（一拍不差）、
//   首条 AR 地址 = 基址；可**帧首写进缓存的第一个字是源(行719, 列897)**，
//   屏第 511 列显示的是源列 255，且它来自哪一行**每帧都在跳**。
//   ⇒ 取数地址对、数据没丢没重，错的是「这一拍数据落到行缓存的哪个位置」。
//   旧写法把 R 通道当成「一条连续的、正好等于整帧数据的流」，用全局拍数切 1280 一行。
//   只要帧首那一拍不是帧首那个字（相位偏了），整帧就被整体切歪：行跨行混、列整体旋转，
//   而叠加和（与顺序无关）、under、frames 全都看不出来 —— 这就是「修不好」的真正原因。
//   新写法：每条 AR 握手时把 {行号, 起始列, 长度} 压进描述符队列，R 通道每收一拍就按
//   「队首描述符 + 突发内偏移」落点 ⇒ 相位、丢拍、突发边界都改变不了落点。
localparam integer MAX_OUT = 8;   // 在飞读突发上限（Zynq HP0 支持 8；4 时余量只剩 ~100 拍/行）
// 队列深度必须是 2 的幂：d_wr/d_rd 是 4 位、按 16 回绕，深度写 12 会让指针走到 12..15
// 这些不存在的项上（读出来全 X，d_cnt 跟着变 X，取数当场冻结 —— 2026-10-31 仿真抓到）。
localparam integer QDEP    = 16;  // 描述符队列深度（> 在飞上限，留出「帧界残留」的余量）

reg [10:0] ar_line;          // 请求游标：下一个要取的源行
reg [10:0] ar_ptr;           //   行内下一拍
reg [31:0] ar_line_addr;     //   该行起始字节地址
reg [10:0] ar_len_q;         // 已发未握手突发的拍数（握手时用它推进游标）
reg [7:0]  ar_frame_q;       // 已发未握手突发属于哪一帧（跨 vs 的握手不许推进游标）
reg [3:0]  outstanding;      // 已发未回的突发数

// ★★ 2026-10-31 17:2x 第二次上板后的补丁：描述符**带帧号**，帧界不再「清队等干净」★★
//   上一版（清队在 vs 沿 + 帧界期间丢弃）上板读回：列整体移了 37 拍（写侧：写进第 0 列的是
//   源元素 1243；屏列 511 显示源 474），且换一次运行有时又正好对齐 —— 说明帧起点附近
//   有**上一帧尾巴的拍还在回**，它们被新帧第一个描述符吃掉了。清队解决不了这件事：
//   数据什么时候回由从机决定，与我们的队列状态无关。
//   现在的做法：每条突发在**发出时**就把 {行号,起始列,长度,帧号} 压进队列（帧号 = vs 计数），
//   R 通道每收一拍都按队首描述符记账、并且弹出，但**只有帧号等于当前帧的才写进缓存**。
//   于是上一帧的残留拍会被它自己的描述符吃干净、悄无声息地丢掉，既不会污染新帧，
//   也不需要停下取数等它们 —— 帧界零等待。
reg [7:0]  frame_id;         // vs 沿 +1
reg [10:0] d_line  [0:QDEP-1];
reg [10:0] d_col   [0:QDEP-1];
reg [10:0] d_len   [0:QDEP-1];
reg [7:0]  d_frame [0:QDEP-1];
reg [3:0]  d_wr, d_rd;       // QDEP = 12 ⇒ 索引必须 4 位（3 位只够 8 项）
reg [3:0]  d_cnt;
reg [10:0] b_i;              // 队首突发里已经写了几拍

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
wire [10:0] wr_col = d_col[d_rd] + b_i;                     // 队首突发内的目标列
wire [12:0] wr_idx = d_line[d_rd][1:0] * LBUF[12:0] + wr_col;
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
// beat_v = 这一拍属于队首描述符（无论哪一帧）；wr_en = 它属于**当前帧**才真正落盘。
// 上一帧的残留拍照样推进 b_i/弹队列（把它们「吃完」），但一个字节都不写。
wire       beat_v = m_rvalid && m_rready && (d_cnt != 4'd0);   // 与 m_rdata 同拍
wire       wr_en  = beat_v && (d_frame[d_rd] == frame_id);
always @(posedge pclk) begin
    if (wr_en) lbuf[wr_idx] <= m_rdata;
end

wire line_ready = (ltag_c == cur_line);    // 只有「这一槽真的是这一行」才放行
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

// ★★ 落后就**跳过去**，不再一路爬着追（2026-10-31 实测的直接结论）★★
//   旧写法 `can_fetch = behind | ahead<=3` 只「允许」落后时继续取 —— 可取数一旦被显示甩开，
//   它取的都是已经显示过的行，永远追不上（上板实测一行 5120 B、6 个 255 拍突发、
//   4 个在飞，行时间 1650 拍里余量只剩 ~100 拍，PS 一抢带宽就掉队）。
//   现在落后时把请求游标直接对准**正在显示的那一行**：位置立刻回正，
//   丢掉的几行由行号标签挡着（显示同号行的旧内容，不会错位）。
wire [31:0] cur_line_addr = cfg_addr + cur_line * {16'd0, cfg_stride};
wire        skip_now   = behind;                       // behind 见文件上方 ahead[12]
wire [10:0] iss_line   = skip_now ? cur_line : ar_line;
wire [10:0] iss_ptr    = skip_now ? 11'd0    : ar_ptr;
wire [31:0] iss_base   = skip_now ? cur_line_addr : ar_line_addr;
wire [10:0] iss_left   = width_use - iss_ptr;
wire [10:0] iss_burst  = (iss_left > BURST[10:0]) ? BURST[10:0] : iss_left;

wire        ar_hs      = m_arvalid && m_arready;
wire        r_hs       = m_rvalid  && m_rready;
wire        r_last_hs  = r_hs && m_rlast;
wire [3:0]  out_next   = outstanding + {3'b000, ar_hs} - {3'b000, r_last_hs};
// 与 vs 同一拍发出的那条 AR 属于上一帧 —— 描述符带帧号（见下），它会自动被当成残留丢掉，
// 所以这里**不需要**再拦 vs_rise。唯一要拦的是队列快满。
wire        may_issue  = !m_arvalid && cfg_en && (cfg_width != 11'd0)
                       && (iss_line < V_ACTIVE[10:0]) && can_fetch
                       && (outstanding < MAX_OUT[3:0])
                       && (d_cnt < QDEP[3:0]-4'd2);

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
        ar_frame_q   <= 8'd0;
        outstanding  <= 4'd0;
        ready        <= 4'b0000;
        ltag0 <= V_ACTIVE[10:0];   // 「空槽」= 不可能等于任何 cur_line
        ltag1 <= V_ACTIVE[10:0];
        ltag2 <= V_ACTIVE[10:0];
        ltag3 <= V_ACTIVE[10:0];
        st_frames    <= 16'd0;
        st_under     <= 16'd0;
    end else begin
        // ---- 帧边界 ----
        // 游标立即归零、新帧马上开始取（不等在飞的突发回完 —— 它们带的是上一帧的帧号，
        // 会在队列里被吃干净并丢弃）。ready[] 也一起清零：新帧的每一行都要重新写满才算就绪。
        if (vs_rise) begin
            st_frames    <= st_frames + 16'd1;
            ar_line_addr <= cfg_addr;
            ar_line      <= 11'd0;
            ar_ptr       <= 11'd0;
            ready        <= 4'b0000;
        end

        // ---- R 通道：落点由描述符决定（数据本身在上面那个无复位块里写进 BRAM）----
        //   ready[] 的摘除/置位也挂在「实际写到哪一列的哪一行」上，不再看拍数：
        //     某行第 0 列且不是正在显示的那条缓存 ⇒ 摘掉就绪位（这一行开始重写）
        //     写到该行最后一列 ⇒ 置就绪位
        if (wr_en) begin
            if ((wr_col == 11'd0) && (d_line[d_rd] != cur_line))
                ready[d_line[d_rd][1:0]] <= 1'b0;
            if (wr_col + 11'd1 >= width_use) begin
                ready[d_line[d_rd][1:0]] <= 1'b1;
                // 整行写完 ⇒ 这一槽从此代表「这一行」，显示侧才认它
                case (d_line[d_rd][1:0])
                    2'd0:    ltag0 <= d_line[d_rd];
                    2'd1:    ltag1 <= d_line[d_rd];
                    2'd2:    ltag2 <= d_line[d_rd];
                    default: ltag3 <= d_line[d_rd];
                endcase
            end
        end

        // ---- AR 通道：握手后推进请求游标 ----
        // ★ 跨 vs 的那次握手**不许推进游标**：它属于上一帧，而 vs 分支已经把游标归零。
        //   判据用帧号（不是 vs_rise）：握手晚于 vs 一两拍才回同样是老帧的（仿真里就是这样，
        //   用 vs_rise 拦不住，新帧第一次取数从行中间开始，帧首 32 列整段丢成 X）。
        //   这条突发的数据不会丢：它的描述符在发出时就已经在队列里了，会被帧号判掉。
        if (ar_hs) begin
            m_arvalid <= 1'b0;
            if ((ar_frame_q == frame_id) && (ar_ptr + ar_len_q >= width_use)) begin
                ar_line      <= ar_line + 11'd1;
                ar_ptr       <= 11'd0;
                ar_line_addr <= ar_line_addr + {16'd0, cfg_stride};
            end else if (ar_frame_q == frame_id) begin
                ar_ptr <= ar_ptr + ar_len_q;
            end
        end else if (skip_now) begin
            // 落后：游标就地跳到正在显示的行（见上方 skip_now 的说明）
            ar_line      <= cur_line;
            ar_ptr       <= 11'd0;
            ar_line_addr <= cur_line_addr;
        end

        // ---- 发起下一个突发（此时 iss_line/iss_ptr 都已稳定）----
        if (may_issue) begin
            m_araddr  <= iss_base + {20'd0, iss_ptr, 2'b00};
            m_arlen   <= iss_burst[7:0] - 8'd1;
            ar_len_q  <= iss_burst;
            ar_frame_q<= frame_id;
            m_arvalid <= 1'b1;
        end

        outstanding <= out_next;
    end
end

// 帧号：每个 vs 加一。描述符在**发出时**记下当时的帧号，于是「这条突发的数据算哪一帧」
// 在发出那一刻就定了，之后无论它什么时候回来都不会含糊。
always @(posedge pclk or negedge reset_n) begin
    if (!reset_n)     frame_id <= 8'd0;
    else if (vs_rise) frame_id <= frame_id + 8'd1;
end

// ---------------------------------------------------------------------------
// AR 描述符队列：每条 AR 握手压入 {行号, 起始列, 长度}，R 通道按队首描述符 + 突发内偏移落点
//
// 为什么要它：旧写法用「全局拍数 / 1280」算行、取余算列，等于假设 R 通道是一条
// 连续且首拍对齐的整帧数据流。实测帧首那一拍不是帧首那个字（相位偏 −383 拍），
// 于是整帧被切歪：行跨行混、列整体旋转，而累加和/under/frames 全都不报警。
// 有了描述符，落点只由「这条突发是哪一行哪一列发出的」决定 —— 相位、丢拍、
// 突发边界、AXI 把一条突发拆成几段回，都不改变落点。
//
// pop 条件用**拍数**而不是 m_rlast：AXI3 从机可能把一条突发拆开回（中间插 rlast），
// 用 rlast 会提前弹队列、后面几拍落到下一行去。拍数由我们自己发的 arlen 决定，说了算。
// ---------------------------------------------------------------------------
wire d_push = may_issue;                                  // 发出即入队（不等握手）
wire d_pop  = beat_v && (b_i + 11'd1 == d_len[d_rd]);      // 按拍数弹，不看 m_rlast

always @(posedge pclk or negedge reset_n) begin
    if (!reset_n) begin
        d_wr  <= 3'd0;
        d_rd  <= 3'd0;
        d_cnt <= 4'd0;
        b_i   <= 11'd0;
    end else begin
        if (d_push) begin
            d_line[d_wr]  <= iss_line;     // 发出这一拍 iss_line/iss_ptr 就是这条突发的起点
            d_col[d_wr]   <= iss_ptr;      // （落后跳行时它们已被 skip_now 改写，必须用 iss_*）
            d_len[d_wr]   <= iss_burst;
            d_frame[d_wr] <= frame_id;
            d_wr          <= d_wr + 3'd1;
        end
        if (d_pop) begin
            d_rd <= d_rd + 3'd1;
            b_i  <= 11'd0;
        end else if (beat_v) begin
            b_i <= b_i + 11'd1;
        end
        d_cnt <= d_cnt + {3'd0, d_push} - {3'd0, d_pop};
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
    end else if (vs_rise) begin
        // 先发布上一帧的快照，再清零本帧的计数 —— 非阻塞赋值，发布的是本帧的值
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

// ---------------------------------------------------------------------------
// 临时诊断（见端口注释）：相位量 + 屏幕若干列的真实像素，用 dbg_sel 选一路
//
// ★ 屏幕列一律用「本行第几个 de 拍」（de_x）定位，**不用 disp_x / xpos**：
//   xpos 本身就是待验的量之一，拿它当采样游标会把「读地址偏移」和「采样点偏移」
//   搅在一起 —— 真偏了 256 列时 `disp_x == 0` 整行一次都不成立，读数会静默变成 0，
//   而这恰好是最好的结果（说明 xpos 已经不在 1..1280 上了），但看着像「没采样到」。
//   用 de 拍计数就没有这个问题：de 每行恒 1280 拍，de_x 就是屏幕列号本身。
// ---------------------------------------------------------------------------
reg [10:0] de_x;
always @(posedge pclk or negedge reset_n) begin
    if (!reset_n)  de_x <= 11'd0;
    else if (!de)  de_x <= 11'd0;
    else           de_x <= de_x + 11'd1;
end

reg [3:0]  dbg_wr_ptr_vs;    // 帧末描述符队列深度（旧口径的 wr_ptr 已不存在）
reg [10:0] dbg_ar_ptr_vs;
reg [12:0] dbg_rd_col0;
reg [19:0] dbg_beats;
reg [19:0] dbg_beats_r;
reg [12:0] dbg_ar_off;
reg        dbg_got_ar;
reg [23:0] dbg_px512, dbg_px1024;
reg [23:0] dbg_wfirst;     // 帧首「写 row0 第 0 列」那一拍的数据 = 取数流的第一拍
reg        dbg_got_wf;

always @(posedge pclk or negedge reset_n) begin
    if (!reset_n) begin
        dbg_wr_ptr_vs <= 4'd0;
        dbg_ar_ptr_vs <= 11'd0;
        dbg_beats     <= 20'd0;
        dbg_beats_r   <= 20'd0;
        dbg_ar_off    <= 13'd0;
        dbg_got_ar    <= 1'b0;
        dbg_wfirst    <= 24'd0;
        dbg_got_wf    <= 1'b0;
    end else if (vs_rise) begin
        dbg_wr_ptr_vs <= {1'b0, d_cnt};
        dbg_ar_ptr_vs <= ar_ptr;
        dbg_beats_r   <= dbg_beats;
        dbg_beats     <= 20'd0;
        dbg_got_ar    <= 1'b0;
        dbg_got_wf    <= 1'b0;
    end else begin
        if (wr_en) dbg_beats <= dbg_beats + 20'd1;
        if (may_issue && !dbg_got_ar) begin
            dbg_ar_off <= (ar_line_addr + {20'd0, ar_ptr, 2'b00}) - cfg_addr;
            dbg_got_ar <= 1'b1;
        end
        // 取数流的第一拍落在哪个源列：ramp 图下这一拍的值减 1 就是源列号
        if (wr_en && (d_line[d_rd] == 11'd0) && (d_col[d_rd] == 11'd0)
                  && (b_i == 11'd0) && !dbg_got_wf) begin
            dbg_wfirst <= m_rdata[23:0];
            dbg_got_wf <= 1'b1;
        end
    end
end

always @(posedge pclk or negedge reset_n) begin
    if (!reset_n)                  dbg_rd_col0 <= 13'd0;
    else if (de && (de_x == 11'd0)) dbg_rd_col0 <= rd_idx;
end

// ★ 屏幕第 512 / 1024 列**真正送出去的那个像素**（sel 6/7）
//   为什么是这两个：整帧累加和对顺序瞎（本轮已三次踩到），人眼只能给出「整除 128」的
//   结论，而「每行循环移位」「取数跨行进位」「地址整体偏 4 KB」在人眼照片上长得一样。
//   把 pixel_data 在两个固定屏幕列上锁存下来，照片就不再是唯一量尺：
//   源图用「每列一个独一无二的 24 位字」（ramp：字 = 源列号 + 1），
//   读回来的值减 1 **就是源列号**，一一对应还是循环移位，一次读清。
always @(posedge pclk or negedge reset_n) begin
    if (!reset_n) begin
        dbg_px512 <= 24'd0; dbg_px1024 <= 24'd0;
    end else if (de) begin
        if (de_x == 11'd512)  dbg_px512  <= pixel_data;
        if (de_x == 11'd1024) dbg_px1024 <= pixel_data;
    end
end

assign st_dbg = (dbg_sel == 3'd0) ? {dbg_disp, behind_lines, under_lines}
              : (dbg_sel == 3'd1) ? {7'd0, dbg_wr_ptr_vs, dbg_ar_ptr_vs}
              : (dbg_sel == 3'd2) ? {6'd0, dbg_rd_col0, 13'd0}
              : (dbg_sel == 3'd3) ? {12'd0, dbg_beats_r}
              : (dbg_sel == 3'd4) ? {19'd0, dbg_ar_off}
              : (dbg_sel == 3'd5) ? {8'd0, dbg_wfirst}
              : (dbg_sel == 3'd6) ? {8'd0, dbg_px512}
              : (dbg_sel == 3'd7) ? {8'd0, dbg_px1024}
              :                     32'd0;

endmodule