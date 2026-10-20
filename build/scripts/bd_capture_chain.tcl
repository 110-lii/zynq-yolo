# =============================================================================
# bd_capture_chain.tcl —— 采集链（M1）block design + bitstream
#
# 目标（M1）：在**没有摄像头、PS 也不跑任何软件**的前提下，证明
#   「PL 采集链能把像素搬进 DDR，且像素值一个字节都不错」。
#   手段：片内 DVP 图样发生器当“假摄像头”，走**和真摄像头完全相同的
#         采集路径**（vid_dvp_capture → 跨时钟 FIFO → AXI DMA → DDR），
#         然后用 JTAG 读 DDR 逐字比对（board/scripts/jtag_capture_test.tcl）。
#
# 结构
#   processing_system7_0
#     FCLK_CLK0  100 MHz  → AXI 域（DMA / GPIO / SmartConnect / FIFO 读侧）
#     pclk 25 MHz  ← clk_wiz（由 FCLK0 派生）→ 图样发生器 / 采集 / FIFO 写侧
#                          ★ 上板时这一路是“假 pclk”。真摄像头的 pclk 来自
#                            OV5640 的 PCLK 引脚，要过 BUFG 进 PL —— 那是 M2
#                            （SCCB 配好摄像头才有输出）。M1 只证明数据通路对。
#     M_AXI_GP0 → ctrl_smc →┬ axi_dma_0/S_AXI_LITE
#                            └ axi_gpio_0/S_AXI
#     S_AXI_HP0 ← axi_dma_0/M_AXI_S2MM
#
#   数据面（AXI-Stream）：
#     vid_test_pattern ──DVP 8bit──> vid_dvp_capture ──AXIS 32bit──>
#       inf_axis_async_fifo（pclk → aclk 跨时钟）──AXIS──> axi_dma_0 S2MM
#
#   控制面（JTAG 可直接读写，不需要 PS 软件）：
#     axi_gpio_0 通道1 输出 bit0 = run（图样发生器启动）
#     axi_gpio_0 通道2 输入 bit0 = cap_stat_overflow（粘滞）
#
# ★ 为什么用 GPIO 而不是把 run 变量接死
#   必须保证「DMA 已经武装好，像素才开始流」，否则 DDR 里的第一帧是半截帧，
#   逐字节比对就没有意义。JTAG 的顺序是：配置 DMA → 再写 GPIO run=1。
#
# ★ 为什么像素出口是 32 位（高 8 位补零）
#   AXI DMA 的 stream data width 只支持 8/16/32/64/128…，不支持 24；
#   axis_dwidth_converter 也不做 24→32。见 src/rtl/video/vid_dvp_capture.v 文件头。
#
# 运行
#   vivado -mode batch -source build/scripts/bd_capture_chain.tcl
#   BD_STOP_AFTER_BD=1 → 只建 BD 并校验，不跑综合实现（快速试错）
#
# 判据：BD_DONE_OK / BD_FAIL（**不看退出码**，见 ref/pitfalls_from_oi.md P3）
# =============================================================================

set script_dir [file normalize [file dirname [info script]]]
set root       [file normalize [file join $script_dir .. ..]]
set part_name  "xc7z020clg400-2"
set proj_dir   [file join $root build vivado capture_chain_bd]
set overlay    [file join $root board overlay]
set rep_timing [file join $root build reports timing]
set rep_util   [file join $root build reports resource]
set rep_drc    [file join $root build reports drc]

# 采集参数：VGA 640x480，行/场总长按标准 VGA（800x525）——
# 图样发生器的 H_TOTAL/V_TOTAL 必须 >= H_ACTIVE/V_ACTIVE，这里留足消隐。
set H_ACTIVE 640
set V_ACTIVE 480
set H_TOTAL  800
set V_TOTAL  525

puts "ROOT     = $root"
puts "PROJ_DIR = $proj_dir"
puts "VIDEO    = ${H_ACTIVE}x${V_ACTIVE}, total ${H_TOTAL}x${V_TOTAL}"

foreach d [list $overlay $rep_timing $rep_util $rep_drc] { file mkdir $d }

# ---- 工程 ----
create_project -force capture_chain_bd $proj_dir -part $part_name

# ---- RTL 源（以 module reference 方式进 BD）----
set rtl_files [list \
    [file join $root src rtl video vid_test_pattern.v] \
    [file join $root src rtl video vid_dvp_capture.v] \
    [file join $root src rtl common inf_axis_async_fifo.v] \
]
foreach f $rtl_files {
    if {![file exists $f]} {
        puts "BD_FAIL 找不到 RTL 源：$f"
        return
    }
}
add_files -norecurse $rtl_files
update_compile_order -fileset sources_1

create_bd_design "system"

# 小工具：按候选名找接口引脚（自适应 Vivado 的接口推断命名）
proc pick_intf {cell candidates} {
    set all [get_bd_intf_pins -of_objects $cell]
    foreach name $candidates {
        foreach i $all {
            if {[string match "*$name" $i]} { return $i }
        }
    }
    puts "BD_FAIL 在 $cell 上找不到候选接口 $candidates；实际有：$all"
    return ""
}

# ---- ① PS7 ----
source [file join $root build scripts ps7_config.tcl]
set_property -dict [list \
    CONFIG.PCW_USE_S_AXI_HP0 {1} \
    CONFIG.PCW_USE_CR_FABRIC {1} \
    CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ {100} \
    CONFIG.PCW_USE_FABRIC_INTERRUPT {1} \
    CONFIG.PCW_IRQ_F2P_INTR {1} \
] $processing_system7_0
puts "PS7: HP0 打开，FCLK0=100 MHz（v5：全链单时钟，无 MMCM）"

# ★ v5 为什么把 clk_wiz 整个去掉（2026-10-20 硅上排障结论）
#   v4 用 clk_wiz 从 FCLK0 分 25 MHz 给 pat/cap/fifo.s_clk。上板实测到**矛盾现象**：
#     cap.m_axis_tvalid 有活动（37% 忙读采样命中）⇒ 采集在 25 MHz 时钟上确实出数；
#     fifo.s_tready 恒 1、wfull 恒 0、rempty 恒 1；
#     可是 fifo 的写指针一次都没动（dbg_wr_hit=0，等价于整个 run 期间没有任何一笔写进去）。
#   三者同时成立在逻辑上不可能（tvalid&tready ⇒ wr_en ⇒ wbin 必增），所以最可疑的是
#   「25 MHz 那一路在 FIFO 的时钟脚上并没有真正翻转」，而不是 FIFO 的指针逻辑。
#   做法：pat/cap/fifo.s_clk 全部改用 FCLK0(100 MHz)，两路时钟变成同一张网。
#   → 如果链路立刻通，问题就锁定在 MMCM 那一路；如果仍然不通，就是 FIFO 在硅上的问题。
#   代价：这一版不再验证跨时钟搬运（M2 用真 OV5640 pclk 再验）。

# ---- ② 采集/图样/FIFO（module reference，接口自动推断）----
set pat [create_bd_cell -type module -reference vid_test_pattern pat_0]
set_property -dict [list \
    CONFIG.H_ACTIVE $H_ACTIVE \
    CONFIG.V_ACTIVE $V_ACTIVE \
    CONFIG.H_TOTAL  $H_TOTAL \
    CONFIG.V_TOTAL  $V_TOTAL \
] $pat

set cap [create_bd_cell -type module -reference vid_dvp_capture cap_0]
set_property -dict [list CONFIG.H_ACTIVE $H_ACTIVE CONFIG.V_ACTIVE $V_ACTIVE] $cap

set fifo [create_bd_cell -type module -reference inf_axis_async_fifo fifo_0]
set_property -dict [list \
    CONFIG.DATA_W 32 \
    CONFIG.USER_W 1 \
    CONFIG.DEPTH_LOG2 9 \
] $fifo

# ---- ③ 控制/数据面 IP ----
set dma [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_dma:7.1 axi_dma_0]
set_property -dict [list \
    CONFIG.c_include_sg {0} \
    CONFIG.c_sg_include_stscntrl_strm {0} \
    CONFIG.c_include_mm2s {0} \
    CONFIG.c_include_s2mm {1} \
    CONFIG.c_m_axi_s2mm_data_width {32} \
    CONFIG.c_s_axis_s2mm_tdata_width {32} \
    CONFIG.c_s2mm_burst_size {64} \
    CONFIG.c_addr_width {32} \
    CONFIG.c_include_s2mm_dre {0} \
] $dma
# ★ 内存侧位宽用 32 位（不是 64）：SmartConnect 会把 M_AXI 宽度按上游 32 位流
#   传播覆盖掉（实测 WARNING [BD 41-702] 会提示），所以这里直接写 32，不留"以为设了 64"的假象。
#   带宽核对：采集侧 640x480x4B@30fps ≈ 37 MB/s，32 位@100 MHz 理论上限 ≈ 400 MB/s，余量足够。
puts "DMA: 仅 S2MM，简单模式（无 SG），流 32 位 → 内存 32 位"

set gpio [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 axi_gpio_0]
set_property -dict [list \
    CONFIG.C_GPIO_WIDTH {32} \
    CONFIG.C_GPIO2_WIDTH {32} \
    CONFIG.C_IS_DUAL {1} \
    CONFIG.C_ALL_OUTPUTS {0} \
    CONFIG.C_ALL_INPUTS_2 {1} \
    CONFIG.C_TRI_DEFAULT {0x00000000} \
] $gpio
puts "GPIO: ch1 输出（bit0=run），ch2 输入（bit0=stat_overflow）"

# 取 GPIO 输出的 bit0 当 run（32→1 位）
set slices [create_bd_cell -type ip -vlnv xilinx.com:ip:xlslice:1.0 slice_run]
set_property -dict [list CONFIG.DIN_WIDTH {32} CONFIG.DIN_FROM {0} CONFIG.DIN_TO {0} \
                         CONFIG.DOUT_WIDTH {1}] $slices

# ---- ④ 时钟与复位（v5：只剩一个 100 MHz 域）----
set rst100 [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_100m]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins rst_100m/slowest_sync_clk]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_RESET0_N] [get_bd_pins rst_100m/ext_reset_in]

# ★ RTL 模块的复位是**同步、高有效**，proc_sys_reset 给的是 aresetn（低有效）⇒ 取反
#   为什么要三个反相器而不是让 RTL 改成低有效：RTL 的复位语义（高有效同步复位）
#   已经在 TB 里验证过，上板前不再改动被验证过的接口。
foreach {tag cell pin} {cap cap_0 rst pat pat_0 rst fifo_w fifo_0 s_rst} {
    set inv [create_bd_cell -type ip -vlnv xilinx.com:ip:util_vector_logic:2.0 inv_$tag]
    set_property -dict [list CONFIG.C_OPERATION {not} CONFIG.C_SIZE {1}] $inv
    connect_bd_net [get_bd_pins rst_100m/peripheral_aresetn] [get_bd_pins inv_$tag/Op1]
    connect_bd_net [get_bd_pins inv_$tag/Res] [get_bd_pins $cell/$pin]
}
set inv_fifo_r [create_bd_cell -type ip -vlnv xilinx.com:ip:util_vector_logic:2.0 inv_fifo_r]
set_property -dict [list CONFIG.C_OPERATION {not} CONFIG.C_SIZE {1}] $inv_fifo_r
connect_bd_net [get_bd_pins rst_100m/peripheral_aresetn] [get_bd_pins inv_fifo_r/Op1]
connect_bd_net [get_bd_pins inv_fifo_r/Res] [get_bd_pins fifo_0/m_rst]

# ---- ⑤ 时钟接线 ----
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins axi_dma_0/s_axi_lite_aclk]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins axi_dma_0/m_axi_s2mm_aclk]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins axi_gpio_0/s_axi_aclk]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins fifo_0/m_clk]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins pat_0/clk]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins cap_0/clk]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins fifo_0/s_clk]

# ---- ⑥ 控制面：GP0 → SmartConnect → DMA_LITE + GPIO ----
set ctrl_smc [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 ctrl_smc]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {2}] $ctrl_smc
connect_bd_intf_net [get_bd_intf_pins processing_system7_0/M_AXI_GP0] [get_bd_intf_pins ctrl_smc/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins ctrl_smc/M00_AXI] [get_bd_intf_pins axi_dma_0/S_AXI_LITE]
connect_bd_intf_net [get_bd_intf_pins ctrl_smc/M01_AXI] [get_bd_intf_pins axi_gpio_0/S_AXI]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins ctrl_smc/aclk]
connect_bd_net [get_bd_pins rst_100m/peripheral_aresetn] [get_bd_pins ctrl_smc/aresetn]

# ---- ⑦ 数据面：DMA S2MM 主口 → SmartConnect → HP0 ----
# ★ 必须过一级 SmartConnect：axi_dma 的 M_AXI_S2MM 是 **AXI4**，而 PS7 的
#   S_AXI_HP0 是 **AXI3**，直连会被 [BD 41-1285] 拒绝（实测报错原文：
#   "The protocols of the interfaces ... (AXI4) and ... (AXI3) are incompatible"）。
#   SmartConnect 负责协议转换；1x1 配置即可，不引入多余带宽损失。
set data_smc [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 data_smc]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1}] $data_smc
connect_bd_intf_net [get_bd_intf_pins axi_dma_0/M_AXI_S2MM] [get_bd_intf_pins data_smc/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins data_smc/M00_AXI] [get_bd_intf_pins processing_system7_0/S_AXI_HP0]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins data_smc/aclk]
connect_bd_net [get_bd_pins rst_100m/peripheral_aresetn] [get_bd_pins data_smc/aresetn]

# ---- ⑦-b 调试用 AXI 监视器（system_ila）：回答"PL 写 DDR 到底走没走" ----
# 为什么加：2026-10-20 实测 S2MM 只吃进 4 拍就再不拉 tready、DDR 里一个字节没写，
#   而 DMASR 没有任何错误位、rd_cnt 卡在 4、CURDESC=0（都是"正常无错但不前进"）。
#   此时只剩两种可能：① DMA 根本没发 AW/W；② 发了但 HP0 不应答。
#   两个监控槽一次抓齐：
#     slot0 = DMA 的 AXI4-32 主口  → 看它有没有发 awvalid/wvalid
#     slot1 = SmartConnect 的 AXI3-64 从口(=HP0 侧) → 看有没有 awready/wready/bvalid 回来
#   ★ C_MON_TYPE 只有 NATIVE / INTERFACE / MIX 三个值（写 "AXI" 会报
#     [IP_Flow 19-3461] Value 'AXI' is out of the range）；监视整条 AXI 接口用 INTERFACE。
set ila_axi [create_bd_cell -type ip -vlnv xilinx.com:ip:system_ila ila_axi]
set_property -dict [list \
    CONFIG.C_MON_TYPE {INTERFACE} \
    CONFIG.C_NUM_MONITOR_SLOTS {2} \
    CONFIG.C_DATA_DEPTH {4096} \
    CONFIG.C_SLOT_0_INTF_TYPE {xilinx.com:interface:aximm_rtl:1.0} \
    CONFIG.C_SLOT_0_AXI_PROTOCOL {AXI4} \
    CONFIG.C_SLOT_1_INTF_TYPE {xilinx.com:interface:aximm_rtl:1.0} \
    CONFIG.C_SLOT_1_AXI_PROTOCOL {AXI3} \
] $ila_axi
connect_bd_intf_net [get_bd_intf_pins axi_dma_0/M_AXI_S2MM] [get_bd_intf_pins ila_axi/SLOT_0_AXI]
connect_bd_intf_net [get_bd_intf_pins data_smc/M00_AXI] [get_bd_intf_pins ila_axi/SLOT_1_AXI]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins ila_axi/clk]

# ---- ⑧ AXI-Stream 三段串联 ----
# ★ pick_intf 要传**单元对象**（$fifo），不能传名字字符串 "fifo_0"：
#   get_bd_intf_pins -of_objects 只认对象，传字符串会报
#   [Common 17-161] Invalid option value 'fifo_0' specified for 'of_objects'（实测踩到）
#
# 采 → FIFO 用**接口对接口**连（两侧都有 TUSER/TLAST，按名字一一对应，实测映射正确）。
connect_bd_intf_net [get_bd_intf_pins cap_0/m_axis] [pick_intf $fifo {"/s"}]

# ★★ FIFO → DMA **不能**用接口对接口（2026-10-20 血泪，代价一次出片）：
#   我们的 AXIS 有 TUSER 没有 TKEEP，AXI DMA 的 S_AXIS_S2MM 有 TKEEP 没有 TUSER。
#   两侧引脚集合不一致时，Vivado 会把"多出来"的脚按**位置**乱配，实测结果：
#       dma.s_axis_s2mm_tkeep  ← fifo.m_tvalid   （tkeep 成了 0/1，DMA 拿不到合法字节）
#       dma.s_axis_s2mm_tlast  ← fifo.m_tuser    （tlast 成了帧首 SOF ⇒ DMA 立刻 Internal Error）
#       fifo.m_tuser           ← 悬空
#   而 tdata/tvalid/tready 三个同名脚倒是连对了 —— 所以波形看起来"数据在动"，
#   但 DMA 永远收不完一帧。这类错误**没有任何报错**，只能靠事后核对网表发现。
#   ⇒ 解法：同名脚逐一用 connect_bd_net 连，TKEEP 用常量 0xF 补齐，TUSER 不接。
#   ⇒ 配套：下面 ⑧b 有连线的机器自查（assert_same_net），BD 一生成就核对映射。
foreach {fpin dpin} {
    m_tdata   s_axis_s2mm_tdata
    m_tvalid  s_axis_s2mm_tvalid
    m_tlast   s_axis_s2mm_tlast
    m_tready  s_axis_s2mm_tready
} {
    connect_bd_net [get_bd_pins fifo_0/$fpin] [get_bd_pins axi_dma_0/$dpin]
}
set tkeep_one [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 tkeep_one]
set_property -dict [list CONFIG.CONST_WIDTH {4} CONFIG.CONST_VAL {0xF}] $tkeep_one
connect_bd_net [get_bd_pins tkeep_one/dout] [get_bd_pins axi_dma_0/s_axis_s2mm_tkeep]

# ---- ⑧b 连线自查：接口成员脚必须按名字连对 ----
# 为什么要有这一段：上面那个 TKEEP/TUSER 错配在 BD 里是**静默**的（validate_bd_design
# 也可能给 pass），只在上板时表现为"DMA 不动"。所以每次生成完 BD 都用 get_bd_nets
# 反查一遍：两边必须落在同一张网上，否则直接 BD_FAIL 退出，不许出片。
proc assert_same_net {pa pb} {
    set na [get_bd_nets -quiet -of_objects [get_bd_pins $pa]]
    set nb [get_bd_nets -quiet -of_objects [get_bd_pins $pb]]
    if {$na eq "" || $nb eq "" || $na ne $nb} {
        puts "BD_FAIL 连线自查失败：$pa (net='$na') 与 $pb (net='$nb') 不在同一张网上"
        return 0
    }
    return 1
}
set net_checks 0
set net_bad 0
# (a) 采 → FIFO 是**接口对接口**连的，成员脚没有独立 net，要用 get_bd_intf_nets 核对
set intf_a [get_bd_intf_nets -quiet -of_objects [get_bd_intf_pins cap_0/m_axis]]
set intf_b [get_bd_intf_nets -quiet -of_objects [get_bd_intf_pins fifo_0/s]]
set net_checks [expr {$net_checks + 1}]
if {$intf_a eq "" || $intf_b eq "" || $intf_a ne $intf_b} {
    puts "BD_FAIL 连线自查失败：cap_0/m_axis (intf_net='$intf_a') 与 fifo_0/s (intf_net='$intf_b') 不是同一条接口网"
    set net_bad [expr {$net_bad + 1}]
}
# (b) FIFO → DMA 是逐脚连的，逐对核对 net 相同
foreach pair {
    {fifo_0/m_tdata       axi_dma_0/s_axis_s2mm_tdata}
    {fifo_0/m_tvalid      axi_dma_0/s_axis_s2mm_tvalid}
    {fifo_0/m_tlast       axi_dma_0/s_axis_s2mm_tlast}
    {fifo_0/m_tready      axi_dma_0/s_axis_s2mm_tready}
    {tkeep_one/dout       axi_dma_0/s_axis_s2mm_tkeep}
} {
    set net_checks [expr {$net_checks + 1}]
    if {![assert_same_net [lindex $pair 0] [lindex $pair 1]]} {
        set net_bad [expr {$net_bad + 1}]
    }
}
# 反向自查：这几个脚**不该**和别的网连在一起（错配的另一半特征）
foreach bogus {
    {axi_dma_0/s_axis_s2mm_tkeep  fifo_0/m_tvalid}
    {axi_dma_0/s_axis_s2mm_tlast  fifo_0/m_tuser}
} {
    set na [get_bd_nets -quiet -of_objects [get_bd_pins [lindex $bogus 0]]]
    set nb [get_bd_nets -quiet -of_objects [get_bd_pins [lindex $bogus 1]]]
    if {$na ne "" && $na eq $nb} {
        puts "BD_FAIL 连线自查失败：已知错配又出现了（[lindex $bogus 0] 连到了 [lindex $bogus 1]）"
        set net_bad [expr {$net_bad + 1}]
    }
}
puts "NETCHECK $net_checks checks, $net_bad bad"
if {$net_bad != 0} {
    puts "BD_FAIL netcheck $net_bad/$net_checks"
    error "netcheck failed"
}

# ---- ⑨ DVP 并行信号 + 状态 ----
connect_bd_net [get_bd_pins pat_0/vsync_n] [get_bd_pins cap_0/vsync_n]
connect_bd_net [get_bd_pins pat_0/href]    [get_bd_pins cap_0/href]
connect_bd_net [get_bd_pins pat_0/dvp_d]   [get_bd_pins cap_0/dvp_d]
connect_bd_net [get_bd_pins axi_gpio_0/gpio_io_o] [get_bd_pins slice_run/Din]
connect_bd_net [get_bd_pins slice_run/Dout]      [get_bd_pins pat_0/run]

# 状态位：通道 2 输入接一个 32 位 concat。
# ★ 为什么要这么多位：2026-10-20 在 PYNQ 路径上遇到「DMA 武装了但永远等不到数据」，
#   只有 stat_overflow 一位根本分不清是「时钟没起来」「图样没跑」「采集没出数」还是
#   「FIFO 没读」。把关键内部信号引出来当观测量，比再上 JTAG 猜快得多。
#
# ★★ 血泪规矩（2026-10-20 实测踩到，代价两次出片）：**绝对不能把 AXIS 接口的成员脚
#   （tvalid/tready/tdata…）单独接出去当观测**。BD 会认为该脚「被用户覆盖」并把它从
#   接口网里摘掉 —— 报 [BD 41-1271] "connection to the pin ... has been overridden by
#   the user. This pin will not be connected as a part of the interface connection"，
#   然后 [BD 41-759] 把另一侧悬空的输入 tie 成 0。当时接走了 cap.m_axis_tvalid /
#   fifo.s_tready / fifo.m_tvalid / dma.s_axis_tready 四根，结果 fifo_0/s_tvalid 被
#   tie 0 ⇒ 写指针永不动、rempty 恒 1、DMA 永远超时，而源侧 tvalid 照样在翻转，
#   看上去像「FIFO 坏了」，实际是**观测把被测对象切断了**。
#   ⇒ 只接「非接口」的标量脚（stat_overflow / vsync_n / href / 常量），
#     接口内部状态由 RTL 自己导出专用 dbg_* 端口（见 inf_axis_async_fifo）。
#   ⇒ 另外：m_tvalid = ~rempty(bit10)、s_tready = ~wfull(bit9)，可由已有位推出。
#   bit0 = cap.stat_overflow（粘滞）
#   bit1 = rst_100m.peripheral_aresetn（1 = 复位已释放；v5 起不再是 MMCM locked）
#   bit2 = pat.vsync_n         （图样场同步，低有效）
#   bit3 = pat.href            （图样行有效）
set dbg [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconcat:2.1 dbg_concat]
set_property -dict [list CONFIG.NUM_PORTS {32}] $dbg
foreach {idx pin} {0 cap_0/stat_overflow 1 rst_100m/peripheral_aresetn 2 pat_0/vsync_n \
                   3 pat_0/href} {
    connect_bd_net [get_bd_pins $pin] [get_bd_pins dbg_concat/In$idx]
}
# 复位实况：s_rst / m_rst 到底是不是一直有效（这是「FIFO 无输出」最可能的原因）
connect_bd_net [get_bd_pins inv_fifo_w/Res] [get_bd_pins dbg_concat/In7]
connect_bd_net [get_bd_pins inv_fifo_r/Res] [get_bd_pins dbg_concat/In8]
# bit9..bit12：FIFO 内部状态（2026-10-20 排「读侧恒空」用）
#   bit9  = fifo.wfull      bit10 = fifo.rempty
#   bit11 = fifo.wr_hit(|wbin)  bit12 = fifo.rd_hit(|rbin)
#   （m_tvalid = ~bit10、s_tready = ~bit9，不再单独接接口脚）
connect_bd_net [get_bd_pins fifo_0/dbg_wfull]  [get_bd_pins dbg_concat/In9]
connect_bd_net [get_bd_pins fifo_0/dbg_rempty] [get_bd_pins dbg_concat/In10]
connect_bd_net [get_bd_pins fifo_0/dbg_wr_hit] [get_bd_pins dbg_concat/In11]
connect_bd_net [get_bd_pins fifo_0/dbg_rd_hit] [get_bd_pins dbg_concat/In12]
# bit15 = fifo.m_tready（下游到底拉不拉）
# bit16..bit23 = fifo 读侧成功握手计数低 8 位（走没走、走多少）
#   ★2026-10-20 板上：FIFO 满着不动、DMA 说自己正在传、却既不完成也不报错。
#     只靠 wfull/rempty 分不清是「DMA 不拉」还是「FIFO 拉了不弹」，所以加这两组。
connect_bd_net [get_bd_pins fifo_0/dbg_m_tready] [get_bd_pins dbg_concat/In15]
for {set i 0} {$i < 8} {incr i} {
    set sl [create_bd_cell -type ip -vlnv xilinx.com:ip:xlslice:1.0 dbg_rdcnt_slice$i]
    set_property -dict [list CONFIG.DIN_WIDTH {8} CONFIG.DIN_FROM $i CONFIG.DIN_TO $i \
                             CONFIG.DOUT_WIDTH {1}] $sl
    connect_bd_net [get_bd_pins fifo_0/dbg_rd_cnt] [get_bd_pins dbg_rdcnt_slice$i/Din]
    connect_bd_net [get_bd_pins dbg_rdcnt_slice$i/Dout] [get_bd_pins dbg_concat/In[expr 16 + $i]]
}
# bit14：恒 1 的 sanity 位 —— 若读出来不是 1，说明 GPIO 通道 2 的高位读法本身有问题，
#        不能把它下面的位当作 RTL 的真实状态（避免把测量故障当成设计故障）
set dbg_one [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 dbg_one]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {1}] $dbg_one
connect_bd_net [get_bd_pins dbg_one/dout] [get_bd_pins dbg_concat/In14]
connect_bd_net [get_bd_pins dbg_concat/dout] [get_bd_pins axi_gpio_0/gpio2_io_i]

# ---- ⑩ 复位 ----
connect_bd_net [get_bd_pins rst_100m/peripheral_aresetn] [get_bd_pins axi_dma_0/axi_resetn]
connect_bd_net [get_bd_pins rst_100m/peripheral_aresetn] [get_bd_pins axi_gpio_0/s_axi_aresetn]

# ---- ⑪ 中断（本轮 JTAG 轮询，接上是为以后 PYNQ 驱动留路）----
set concat [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconcat:2.1 irq_concat]
set_property -dict [list CONFIG.NUM_PORTS {1}] $concat
connect_bd_net [get_bd_pins axi_dma_0/s2mm_introut] [get_bd_pins irq_concat/In0]
connect_bd_net [get_bd_pins irq_concat/dout] [get_bd_pins processing_system7_0/IRQ_F2P]

# ---- ⑫ PS7 的 AXI 口时钟 ----
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins processing_system7_0/M_AXI_GP0_ACLK]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins processing_system7_0/S_AXI_HP0_ACLK]

# ---- ⑬ 地址 ----
assign_bd_address
puts "---- 地址映射 ----"
foreach seg [get_bd_addr_segs] { puts "  $seg" }

validate_bd_design
save_bd_design

if {[info exists ::env(BD_STOP_AFTER_BD)] && $::env(BD_STOP_AFTER_BD) ne "0"} {
    puts "BD_ONLY_OK 已按 BD_STOP_AFTER_BD 停在 BD 校验后（未跑综合实现）"
    return
}

# ---- ⑭ wrapper + 综合实现 ----
set bd_file [get_files system.bd]
make_wrapper -files $bd_file -top
add_files -norecurse [file join $proj_dir capture_chain_bd.gen sources_1 bd system hdl system_wrapper.v]
set_property top system_wrapper [current_fileset]
update_compile_order -fileset sources_1
generate_target all [get_files system.bd]

set hwh [file join $proj_dir capture_chain_bd.gen sources_1 bd system hw_handoff system.hwh]
if {[file exists $hwh]} {
    file copy -force $hwh [file join $overlay capture_chain.hwh]
    puts "HWH  -> [file join $overlay capture_chain.hwh]"
} else {
    puts "BD_FAIL 没找到 hw_handoff 的 .hwh：$hwh"
    return
}

launch_runs impl_1 -to_step write_bitstream -jobs 8
wait_on_run impl_1
set st [get_property STATUS [get_runs impl_1]]
puts "impl_1 STATUS = $st"
if {[string first "write_bitstream Complete" $st] < 0} {
    puts "BD_FAIL 实现未完成：$st"
    return
}

open_run impl_1
report_timing_summary -file [file join $rep_timing capture_chain_timing.rpt]
report_utilization   -file [file join $rep_util   capture_chain_util.rpt]
report_drc           -file [file join $rep_drc    capture_chain_drc.rpt]

set bit [file join $proj_dir capture_chain_bd.runs impl_1 system_wrapper.bit]
if {![file exists $bit]} {
    set cand [glob -nocomplain [file join $proj_dir capture_chain_bd.runs impl_1 *.bit]]
    if {[llength $cand] > 0} { set bit [lindex $cand 0] }
}
if {![file exists $bit]} { puts "BD_FAIL 没找到 .bit"; return }
file copy -force $bit [file join $overlay capture_chain.bit]
puts "BIT  -> [file join $overlay capture_chain.bit]"

# 调试核探针映射（.ltx）：Vivado 抓 ILA 波形时必须配它；而脚本末尾 CLEAN_DONE 会把
# 工程目录整个删掉 ⇒ 必须在这里先拷到 board/overlay/，否则抓波时文件已经不存在。
set ltx [file join $proj_dir capture_chain_bd.runs impl_1 system_wrapper.ltx]
if {![file exists $ltx]} {
    set cand [glob -nocomplain [file join $proj_dir capture_chain_bd.runs impl_1 *.ltx]]
    if {[llength $cand] > 0} { set ltx [lindex $cand 0] }
}
if {[file exists $ltx]} {
    file copy -force $ltx [file join $overlay capture_chain.ltx]
    puts "LTX  -> [file join $overlay capture_chain.ltx]"
} else {
    puts "WARN 没找到 .ltx（BD 里没有调试核？）"
}

# 地址表：JTAG 脚本要按它去找 DMA/GPIO 寄存器（不硬编码，避免改了 BD 忘了脚本）
# ★ 取值方式：**从刚导出的 .hwh 里解析 <MEMRANGE INSTANCE=... BASEVALUE=...>**。
#   走过的两条弯路（都实测过，别再回去）：
#     ① `get_bd_addr_segs` 全表 + string match 名字 ⇒ 一条都没匹配上，文件里是空串；
#     ② `get_bd_addr_segs -of_objects <从单元>` ⇒ 取到的是该从单元**自己**的段
#        （axi_dma_0 返回 0x00000000、axi_gpio_0 返回空），不是 PS7 给它的 REG 映射。
#   .hwh 是 BD 导出的握手文件（PYNQ 也读它），MEMRANGE 里 INSTANCE/BASEVALUE 是权威值。
set hwh_file [file join $overlay capture_chain.hwh]
proc hwh_base {path inst} {
    if {![file exists $path]} { return "" }
    set fh [open $path r]
    set data [read $fh]
    close $fh
    foreach line [split $data "\n"] {
        if {[string first "MEMRANGE" $line] < 0} { continue }
        if {[string first "INSTANCE=\"$inst\"" $line] < 0} { continue }
        if {[regexp {BASEVALUE="(0x[0-9A-Fa-f]+)"} $line -> bv]} { return $bv }
    }
    return ""
}
set dma_base  [hwh_base $hwh_file axi_dma_0]
set gpio_base [hwh_base $hwh_file axi_gpio_0]
if {$dma_base eq "" || $gpio_base eq ""} {
    puts "BD_FAIL 地址表没取到（dma=$dma_base gpio=$gpio_base）—— 检查 hwh MEMRANGE"
    return
}
set addr_txt [file join $overlay capture_chain_addr.txt]
set fh [open $addr_txt w]
puts $fh "# 自动生成：build/scripts/bd_capture_chain.tcl（改了 BD 必须重新生成）"
puts $fh "dma_base=$dma_base"
puts $fh "gpio_base=$gpio_base"
puts $fh "h_active=$H_ACTIVE"
puts $fh "v_active=$V_ACTIVE"
puts $fh "pixel_bytes=4"
puts $fh "frame_bytes=[expr {$H_ACTIVE * $V_ACTIVE * 4}]"
close $fh
puts "ADDR -> $addr_txt (dma=$dma_base gpio=$gpio_base)"

set wns "?"
if {![catch { set wns [get_property SLACK [get_timing_paths -delay_type max]] }]} {}

# ---- ⑮ 清理 Vivado 残留（check_repo.py 的检查 d) 是扫盘的）----
proc _rm_recursive {dir patterns} {
    if {![file isdirectory $dir]} { return 0 }
    set n 0
    foreach pat $patterns {
        foreach f [glob -nocomplain -directory $dir -types f $pat] {
            if {![catch {file delete -force $f}]} { incr n }
        }
        foreach d [glob -nocomplain -directory $dir -types d $pat] {
            if {![catch {file delete -force $d}]} { incr n }
        }
    }
    foreach sub [glob -nocomplain -directory $dir -types d *] {
        incr n [_rm_recursive $sub $patterns]
    }
    return $n
}
catch {close_project}
set nclean [_rm_recursive $proj_dir {"*.jou" "*.log" "*.str" ".Xil"}]
set repo_root [file normalize [file join $script_dir .. ..]]
foreach pat {"vivado.log" "vivado_*.backup.log" "vivado.jou" "vivado_*.jou"} {
    foreach f [glob -nocomplain -directory $repo_root -types f $pat] {
        if {![catch {file delete -force $f}]} { incr nclean }
    }
}
foreach d [glob -nocomplain -directory $repo_root -types d ".Xil"] {
    if {![catch {file delete -force $d}]} { incr nclean }
}
puts "CLEAN_DONE removed=$nclean"

puts "BD_DONE_OK BIT=[file join $overlay capture_chain.bit] HWH=[file join $overlay capture_chain.hwh] WNS=$wns"