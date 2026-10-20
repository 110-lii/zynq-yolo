// =============================================================================
// inf_axis_async_fifo —— 跨时钟域 AXI4-Stream 异步 FIFO（FWFT + 格雷码指针）
// -----------------------------------------------------------------------------
// 职责：把写侧时钟域（s_clk）的 AXI4-Stream 数据搬到读侧时钟域（m_clk），
//       两侧时钟**没有**固定相位/频率关系（典型场景：PS 的 AXI-Lite 时钟域
//       ↔ 视频像素时钟域，或 100 MHz 采集域 ↔ 150 MHz 处理域）。
//       本模块只做「跨域搬运 + 深度缓冲」，不做位宽转换、不做数据加工。
//
// 设计来源
//   结构照 Clifford E. Cummings, "Simulation and Synthesis Techniques for
//   Asynchronous FIFO Design"（SNUG 2002）的经典双时钟 FIFO：双口存储体 +
//   二进制/格雷码双指针 + 每侧两级触发器同步 + 「格雷码最高两位取反」的满判断。
//   工程化取舍见下面第 3、4 条。
//
// 为什么这么做（逐条）
//   1. 指针用格雷码：相邻两个指针值只差 1 位，跨域采样最多采到一个「旧值」或
//      「新值」，不会采到中间乱码；于是满/空判断只会**保守**（晚报），绝不会
//      出现「以为没满其实满了 → 覆盖未读数据」或「以为非空其实空 → 读到垃圾」。
//      这就是格雷码在异步 FIFO 里的全部意义。
//   2. 跨域指针各走**两级触发器**，并标 (* ASYNC_REG = "TRUE" *)，让 Vivado
//      把这两级放进同一个 slice、用同一套时钟布线，减少亚稳态传播到组合逻辑
//      的概率（提高 MTBF）。第一级可能进亚稳态，第二级再采一次即可把 MTBF
//      拉到可接受量级。
//   3. 满判断（组合、比较**当前** wbin）：
//        full = ( gray(wbin) == { ~gray(rbin_sync)[PW-1],
//                                 ~gray(rbin_sync)[PW-2],
//                                  gray(rbin_sync)[PW-3:0] } )
//      格雷码是二进制的一一映射，而「二进制加 DEPTH(=2**(PW-1))」恰好等价于
//      「格雷码最高两位同时取反」，所以上式 ⇔ (wbin - rbin) mod 2**PW == DEPTH
//      ⇔ 里面已经有 DEPTH 个条目 ⇒ 满。
//      **刻意用组合满（比较当前 wbin，而不是 wbin+1）**：这样 s_tready 直接等于
//      !full，既不引入一拍延迟，也不需要把 tready 反馈进 wbin_next（那会形成
//      tready → full → tready 的组合环）。代价是 s_tready 的组合路径从同步链
//      触发器出发——和 Xilinx AXIS FIFO IP 的做法一致，≤150 MHz 没问题。
//      深度因此正好是 2**DEPTH_LOG2：第 DEPTH 个条目仍能写进去，写满后
//      s_tready 立刻拉低。
//   4. 空判断：读域比较自己的 rgray 与同步过来的 wgray，相等即空。
//      同步链有 2~3 拍延迟 ⇒ 空是**保守**的（数据可能晚 2 拍才看见 tvalid），
//      但绝不会「tvalid=1 却没有数据」。
//
// FWFT（first-word fall-through）
//   m_tvalid = !empty；m_tdata/m_tuser/m_tlast 是 mem[rbin] 的**组合读出**。
//   一旦 FIFO 非空，tvalid 立刻为 1，无需预取握手；下游只要 tready 一高就当场
//   吃掉这一条。只有 m_tvalid && m_tready 同拍为 1 才推进 rbin（弹出）。
//   因为数据与旁带（tuser/tlast）在同一个存储字里、同一拍读出，不存在「数据先
//   跨域、旁带后跨域」造成的错位。
//
// 存储体
//   reg [DATA_W+USER_W:0] mem [0:DEPTH-1]，一个字 = {tlast, tuser, tdata}；
//   写侧在 s_clk 上升沿用 wbin 的低位当地址整条写入，读侧用 rbin 的低位组合读出。
//   mem 不参与复位（省复位扇出）；**mem 里的内容只在 tvalid=1 时有意义**，
//   上电后未写过的位置是 x —— 这是本设计唯一允许的 x 来源，且被 tvalid 挡住。
//
// 复位（两侧各自独立、同步、高有效）
//   s_rst 清写指针 wbin/wgray 以及「读指针同步链」；m_rst 清读指针 rbin/rgray
//   以及「写指针同步链」。复位值**一律为 0**：上电时指针绝不能是 x，否则
//   x 会顺着格雷码比较一路传播，表现为 s_tready/m_tvalid 恒 x 或恒 0、FIFO
//   永远满/永远空——本项目的上一个模块就踩过这个坑（复位只清了计数器、没清
//   指针，仿真出 x，上板表现为偶发死锁）。
//   用法约束：两侧复位要「成对出现」——整机里由 inf_reset 之类把异步复位分别
//   同步到两个时钟域后同时给进来；**只复位一侧而另一侧还在跑属于非法用法**
//   （另一侧指针位置未知，FIFO 里的数据语义不再成立）。此情形下本模块只保证
//   「不会卡死、不会永远满/永远空」，不保证数据连续。TB 的 e 项就是按这个契约
//   检查的。
//
// 时序/CDC 假设（上板前提）
//   * s_clk 与 m_clk 异步，不要求同频、整数比或固定相位；
//   * 格雷码指针每拍最多变 1 位 ⇒ 跨域最多错一个码字，只影响满/空的保守性；
//   * 两级同步是 MTBF 与延迟的折中。若两侧时钟都 > 200 MHz 或要求更高 MTBF，
//     可把同步级数做成参数加到 3 级；本工程目标 ≤150 MHz，2 级足够；
//   * 复位释放不需要跨域握手（两侧各自同步复位，符合 Xilinx 复位规范）；
//   * 综合/实现时不需要额外约束这两个跨域路径为 false_path —— ASYNC_REG 属性
//     只是让工具把它们当同步链处理；若跑 CDC 检查，这两条路径应被识别为
//     2-flop synchronizer。
//
// 可综合性
//   纯 RTL、Verilog-2001 可综合子集 + $clog2（-g2012 可编译），无 initial、
//   无延时、无 $display；不依赖 XPM / FIFO IP。
// -----------------------------------------------------------------------------
// 参数：DATA_W=tdata 位宽，USER_W=tuser 位宽，DEPTH_LOG2=深度以 2 为底的对数
//       （深度 = 2**DEPTH_LOG2，必须是 ≥1 的整数；DEPTH_LOG2=0 无意义）
// =============================================================================
`timescale 1ns / 1ps
`default_nettype none

module inf_axis_async_fifo #(
    parameter integer DATA_W     = 32,   // tdata 位宽
    parameter integer USER_W     = 1,    // tuser 位宽
    parameter integer DEPTH_LOG2 = 9     // 深度 = 2**DEPTH_LOG2 个条目（默认 512）
) (
    input  wire              s_clk,
    input  wire              s_rst,      // 写侧复位，同步、高有效
    input  wire              s_tvalid,
    output wire              s_tready,
    input  wire [DATA_W-1:0] s_tdata,
    input  wire [USER_W-1:0] s_tuser,
    input  wire              s_tlast,
    input  wire              m_clk,
    input  wire              m_rst,      // 读侧复位，同步、高有效
    output wire              m_tvalid,
    input  wire              m_tready,
    output wire [DATA_W-1:0] m_tdata,
    output wire [USER_W-1:0] m_tuser,
    output wire              m_tlast,
    // ---- 上板排障观测口（悬空不影响功能；2026-10-20 PYNQ 上「FIFO 无输出」用）----
    output wire              dbg_wfull,   // 写侧已满
    output wire              dbg_rempty,  // 读侧已空
    output wire              dbg_wr_hit,  // 写指针动过（|wbin）
    output wire              dbg_rd_hit,  // 读指针动过（|rbin）
    output wire              dbg_m_tready,// 下游有没有在拉（= m_tready 直通）
    output wire [7:0]        dbg_rd_cnt    // 读侧成功握手次数（低 8 位，只看动没动）
);

    // --------------------------------------------------------------- 参数推导
    localparam integer DEPTH  = (1 << DEPTH_LOG2);  // 条目数
    localparam integer ADDR_W = DEPTH_LOG2;         // mem 地址位宽
    localparam integer PW     = DEPTH_LOG2 + 1;     // 指针位宽（多一位当回卷位）
    localparam integer DW     = DATA_W + USER_W + 1;// 一个存储字的宽度

    // --------------------------------------------------------------- 存储体
    // 写侧写、读侧组合读，双口 RAM 的推断写法（Vivado 会推成 distributed RAM
    // 或 block RAM，取决于深度/位宽；本工程深度 ≤512、宽度 ≤65，走 BRAM）。
    reg [DW-1:0] mem [0:DEPTH-1];

    // --------------------------------------------------------------- 两侧指针
    // 二进制指针：给 mem 当地址、方便 +1；格雷码指针：跨域用。
    reg [PW-1:0] wbin, wgray;   // 写域
    reg [PW-1:0] rbin, rgray;   // 读域

    // ------------------------------------------------------- 跨域同步链（2 级）
    // 方向：读指针 rgray → 写域（算满用）；写指针 wgray → 读域（算空用）。
    // ASYNC_REG 让工具把两级摆到一起，避免被优化/重定时拆散。
    (* ASYNC_REG = "TRUE" *) reg [PW-1:0] rgray_ws1, rgray_ws2;
    (* ASYNC_REG = "TRUE" *) reg [PW-1:0] wgray_rs1, wgray_rs2;

    // --------------------------------------------------------------- 纯函数
    // 二进制 → 格雷码：g = b ^ (b >> 1)
    function [PW-1:0] bin2gray;
        input [PW-1:0] b;
        begin
            bin2gray = b ^ (b >> 1);
        end
    endfunction

    // 「满」的比较模板：把读指针格雷码的最高两位取反
    function [PW-1:0] gray_full_cmp;
        input [PW-1:0] g;
        begin
            gray_full_cmp    = g;
            gray_full_cmp[PW-1] = ~g[PW-1];
            gray_full_cmp[PW-2] = ~g[PW-2];
        end
    endfunction

    // =======================================================================
    // 写侧（s_clk 域）
    // =======================================================================
    wire [PW-1:0] wgray_cur = bin2gray(wbin);

    // 满：当前写指针与「同步过来的读指针 + DEPTH」在格雷码上相等。
    // 组合逻辑，输入只有 wbin（写域寄存器）和 rgray_ws2（同步链输出），无环。
    wire wfull = (wgray_cur == gray_full_cmp(rgray_ws2));

    assign s_tready = ~wfull;                 // AXI：tready = 非满
    wire   wr_en    = s_tvalid & ~wfull;      // 只有同拍 tvalid&tready 才写

    always @(posedge s_clk) begin
        if (s_rst) begin
            // 复位值必须是 0（不能留 x，见文件头「复位」一节）
            wbin  <= {PW{1'b0}};
            wgray <= {PW{1'b0}};
        end else if (wr_en) begin
            // 一拍连 tdata/tuser/tlast 一起存，读侧组合读
            mem[wbin[ADDR_W-1:0]] <= {s_tlast, s_tuser, s_tdata};
            wbin                  <= wbin + 1'b1;
            wgray                 <= bin2gray(wbin + 1'b1);
        end
    end

    // 读指针同步进写域：两级，复位清 0（与 wbin 同域，同属写侧复位）
    always @(posedge s_clk) begin
        if (s_rst) begin
            rgray_ws1 <= {PW{1'b0}};
            rgray_ws2 <= {PW{1'b0}};
        end else begin
            rgray_ws1 <= rgray;
            rgray_ws2 <= rgray_ws1;
        end
    end

    // =======================================================================
    // 读侧（m_clk 域）
    // =======================================================================
    // 空：读指针与同步过来的写指针在格雷码上相等
    wire rempty = (rgray == wgray_rs2);

    assign m_tvalid = ~rempty;                         // FWFT：非空就 valid
    wire   rd_pop   = ~rempty & m_tready;              // 同拍 tvalid&tready 才弹

    // FWFT 数据通路：组合读出，不经寄存器、不需要预取握手
    wire [DW-1:0] rd_line = mem[rbin[ADDR_W-1:0]];
    assign m_tdata = rd_line[DATA_W-1:0];
    assign m_tuser = rd_line[DATA_W +: USER_W];
    assign m_tlast = rd_line[DATA_W+USER_W];

    always @(posedge m_clk) begin
        if (m_rst) begin
            rbin  <= {PW{1'b0}};
            rgray <= {PW{1'b0}};
        end else if (rd_pop) begin
            rbin  <= rbin + 1'b1;
            rgray <= bin2gray(rbin + 1'b1);
        end
    end

    // ------------------------------------------------------- 调试观测（只读）
    // 目的：把「有没有写进去」「读侧看见没有」「满/空标志对不对」变成可读位。
    // 只做观测量，不参与任何控制，悬空也不影响功能。
    assign dbg_wfull  = wfull;
    assign dbg_rempty = rempty;
    assign dbg_wr_hit = |wbin;
    assign dbg_rd_hit = |rbin;

    // 下游到底有没有在拉：m_tready 直通 + 成功读走的拍数计数。
    // 2026-10-20 板上现象是「FIFO 满着不动、DMA 说自己在传、却既不完成也不报错」，
    // 只有把「拉没拉」和「走了几拍」变成可读数字，才能分清
    //   ① DMA 根本没拉（tready 一直 0）  ② 拉了但 FIFO 不弹（读逻辑问题）。
    assign dbg_m_tready = m_tready;
    reg [7:0] rd_cnt;
    always @(posedge m_clk) begin
        if (m_rst) begin
            rd_cnt <= 8'd0;
        end else if (rd_pop) begin
            rd_cnt <= rd_cnt + 8'd1;
        end
    end
    assign dbg_rd_cnt = rd_cnt;

    // 写指针同步进读域：两级，复位清 0（与 rbin 同域，同属读侧复位）
    always @(posedge m_clk) begin
        if (m_rst) begin
            wgray_rs1 <= {PW{1'b0}};
            wgray_rs2 <= {PW{1'b0}};
        end else begin
            wgray_rs1 <= wgray;
            wgray_rs2 <= wgray_rs1;
        end
    end

endmodule

`default_nettype wire