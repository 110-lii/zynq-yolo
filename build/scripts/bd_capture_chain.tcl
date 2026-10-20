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
#     FCLK_CLK1   25 MHz  → pclk 域（图样发生器 / 采集 / FIFO 写侧）
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
    CONFIG.PCW_EN_CLK1_PORT {1} \
    CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ {100} \
    CONFIG.PCW_FPGA1_PERIPHERAL_FREQMHZ {25} \
    CONFIG.PCW_USE_FABRIC_INTERRUPT {1} \
    CONFIG.PCW_IRQ_F2P_INTR {1} \
] $processing_system7_0
puts "PS7: HP0 打开，FCLK0=100 MHz（AXI 域），FCLK1=25 MHz（pclk 域）"

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

# ---- ④ 时钟与复位（两个域各一个 proc_sys_reset）----
set rst100 [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_100m]
set rst25  [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_25m]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins rst_100m/slowest_sync_clk]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK1] [get_bd_pins rst_25m/slowest_sync_clk]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_RESET0_N] [get_bd_pins rst_100m/ext_reset_in]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_RESET0_N] [get_bd_pins rst_25m/ext_reset_in]

# ★ RTL 模块的复位是**同步、高有效**，proc_sys_reset 给的是 aresetn（低有效）⇒ 取反
#   为什么要三个反相器而不是让 RTL 改成低有效：RTL 的复位语义（高有效同步复位）
#   已经在 TB 里验证过，上板前不再改动被验证过的接口。
foreach {tag cell pin} {cap cap_0 rst pat pat_0 rst fifo_w fifo_0 s_rst} {
    set inv [create_bd_cell -type ip -vlnv xilinx.com:ip:util_vector_logic:2.0 inv_$tag]
    set_property -dict [list CONFIG.C_OPERATION {not} CONFIG.C_SIZE {1}] $inv
    connect_bd_net [get_bd_pins rst_25m/peripheral_aresetn] [get_bd_pins inv_$tag/Op1]
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
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK1] [get_bd_pins pat_0/clk]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK1] [get_bd_pins cap_0/clk]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK1] [get_bd_pins fifo_0/s_clk]

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

# ---- ⑧ AXI-Stream 三段串联 ----
# ★ pick_intf 要传**单元对象**（$fifo），不能传名字字符串 "fifo_0"：
#   get_bd_intf_pins -of_objects 只认对象，传字符串会报
#   [Common 17-161] Invalid option value 'fifo_0' specified for 'of_objects'（实测踩到）
connect_bd_intf_net [get_bd_intf_pins cap_0/m_axis] [pick_intf $fifo {"/s"}]
connect_bd_intf_net [pick_intf $fifo {"/m"}] [get_bd_intf_pins axi_dma_0/S_AXIS_S2MM]

# ---- ⑨ DVP 并行信号 + 状态 ----
connect_bd_net [get_bd_pins pat_0/vsync_n] [get_bd_pins cap_0/vsync_n]
connect_bd_net [get_bd_pins pat_0/href]    [get_bd_pins cap_0/href]
connect_bd_net [get_bd_pins pat_0/dvp_d]   [get_bd_pins cap_0/dvp_d]
connect_bd_net [get_bd_pins axi_gpio_0/gpio_io_o] [get_bd_pins slice_run/Din]
connect_bd_net [get_bd_pins slice_run/Dout]      [get_bd_pins pat_0/run]
connect_bd_net [get_bd_pins cap_0/stat_overflow] [get_bd_pins axi_gpio_0/gpio2_io_i]

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

# 地址表：JTAG 脚本要按它去找 DMA/GPIO 寄存器（不硬编码，避免改了 BD 忘了脚本）
# ★ 取值方式：get_bd_addr_segs -of_objects <单元>，逐个拿 OFFSET。
#   （第一版用 `get_bd_addr_segs` 全表 + string match 名字，实测一条都没匹配上，
#    生成的 addr 文件里 dma_base/gpio_base 是空字符串 —— 已改成按单元取。）
set dma_base "?"
set gpio_base "?"
foreach pair [list [list axi_dma_0 dma_base] [list axi_gpio_0 gpio_base]] {
    set inst [lindex $pair 0]
    set key  [lindex $pair 1]
    set cell [get_bd_cells $inst]
    if {$cell eq ""} { continue }
    foreach seg [get_bd_addr_segs -of_objects $cell] {
        set off [get_property OFFSET $seg]
        if {$off ne ""} { puts "  ADDR_SEG $inst $seg = $off" }
        if {$key eq "dma_base"} { set dma_base $off }
        if {$key eq "gpio_base"} { set gpio_base $off }
        break
    }
}
if {$dma_base eq "" || $dma_base eq "?" || $gpio_base eq "" || $gpio_base eq "?"} {
    puts "BD_FAIL 地址表没取到（dma=$dma_base gpio=$gpio_base）—— 检查 get_bd_addr_segs 取法"
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