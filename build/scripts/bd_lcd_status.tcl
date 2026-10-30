# bd_lcd_status.tcl —— LCD 800x480 状态面板（1bpp BRAM + RGB LCD）
set script_dir [file normalize [file dirname [info script]]]
set root       [file normalize [file join $script_dir .. ..]]
set part_name  "xc7z020clg400-2"
set proj_dir   [file join $root build vivado lcd_status_bd]
set overlay    [file join $root board overlay]
set rtl_dir    [file join $root src rtl io lcd]
set rep_timing [file join $root build reports timing]
set rep_util   [file join $root build reports resource]
set xdc        [file join $script_dir lcd_status.xdc]

foreach d [list $overlay $rep_timing $rep_util] { file mkdir $d }
create_project -force lcd_status_bd $proj_dir -part $part_name
add_files -norecurse [list \
    [file join $rtl_dir lcd_timing.v] \
    [file join $rtl_dir lcd_fb_reader.v] \
    [file join $rtl_dir lcd_status_top.v] \
]
add_files -fileset constrs_1 -norecurse $xdc
update_compile_order -fileset sources_1

create_bd_design "system"
source [file join $root build scripts ps7_config.tcl]
set_property -dict [list \
    CONFIG.PCW_USE_S_AXI_HP0 {0} \
    CONFIG.PCW_USE_S_AXI_HP1 {0} \
    CONFIG.PCW_USE_S_AXI_HP2 {0} \
    CONFIG.PCW_USE_S_AXI_HP3 {0} \
    CONFIG.PCW_USE_M_AXI_GP0 {1} \
    CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ {83.333} \
] $processing_system7_0

set rst_ps [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_ps]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins rst_ps/slowest_sync_clk]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_RESET0_N] [get_bd_pins rst_ps/ext_reset_in]

create_bd_port -dir I -type clk sys_clk
set_property CONFIG.FREQ_HZ {50000000} [get_bd_ports sys_clk]

set clk_wiz [create_bd_cell -type ip -vlnv xilinx.com:ip:clk_wiz:6.0 clk_wiz]
set_property -dict [list \
    CONFIG.PRIM_IN_FREQ {50.000} \
    CONFIG.CLKOUT1_REQUESTED_OUT_FREQ {30.000} \
    CONFIG.USE_LOCKED {true} \
    CONFIG.USE_RESET {true} \
    CONFIG.RESET_PORT {reset} \
    CONFIG.RESET_TYPE {ACTIVE_HIGH} \
] $clk_wiz
connect_bd_net [get_bd_ports sys_clk] [get_bd_pins clk_wiz/clk_in1]
connect_bd_net [get_bd_pins rst_ps/peripheral_reset] [get_bd_pins clk_wiz/reset]
set pclk [get_bd_pins clk_wiz/clk_out1]

set rst_pix [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_pix]
connect_bd_net $pclk [get_bd_pins rst_pix/slowest_sync_clk]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_RESET0_N] [get_bd_pins rst_pix/ext_reset_in]
## dcm_locked 必须显式接 locked：悬空时 proc_sys_reset 会 tie 成常量，
## 若 tie 到 0 则 rst_pix 永远处于复位、lcd_de 恒 0、屏幕全黑（session 013 的解析）
connect_bd_net [get_bd_pins clk_wiz/locked] [get_bd_pins rst_pix/dcm_locked]

set axi_smc [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 axi_smc]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {2}] $axi_smc
set bram_ctrl [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_bram_ctrl:4.1 bram_ctrl]
set_property -dict [list \
    CONFIG.DATA_WIDTH {32} \
    CONFIG.SINGLE_PORT_BRAM {1} \
    CONFIG.ECC_TYPE {0} \
    CONFIG.PROTOCOL {AXI4LITE} \
] $bram_ctrl
connect_bd_intf_net [get_bd_intf_pins processing_system7_0/M_AXI_GP0] [get_bd_intf_pins axi_smc/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_smc/M00_AXI] [get_bd_intf_pins bram_ctrl/S_AXI]

## 调试读回：AXI GPIO 双通道，把 lcd_status_0 的 dbg1/dbg2 拉回 PS
## dbg1 = {7'b0, lcd_de, pixel_data}（看 fb_reader 到底吐了什么）
## dbg2 = {10'b0, v_cnt, h_cnt}（看 pclk 与复位是否活着：h_cnt 应在 0..1055 循环）
set gpio_dbg [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 gpio_dbg]
set_property -dict [list \
    CONFIG.C_IS_DUAL {1} \
    CONFIG.C_GPIO_WIDTH {32} \
    CONFIG.C_ALL_INPUTS {1} \
    CONFIG.C_GPIO2_WIDTH {32} \
    CONFIG.C_ALL_INPUTS_2 {1} \
] $gpio_dbg
connect_bd_intf_net [get_bd_intf_pins axi_smc/M01_AXI] [get_bd_intf_pins gpio_dbg/S_AXI]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins gpio_dbg/s_axi_aclk]
connect_bd_net [get_bd_pins rst_ps/peripheral_aresetn] [get_bd_pins gpio_dbg/s_axi_aresetn]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins axi_smc/aclk]
connect_bd_net [get_bd_pins rst_ps/peripheral_aresetn] [get_bd_pins axi_smc/aresetn]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins bram_ctrl/s_axi_aclk]
connect_bd_net [get_bd_pins rst_ps/peripheral_aresetn] [get_bd_pins bram_ctrl/s_axi_aresetn]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins processing_system7_0/M_AXI_GP0_ACLK]

set bram [create_bd_cell -type ip -vlnv xilinx.com:ip:blk_mem_gen:8.4 bram]
set_property -dict [list \
    CONFIG.Memory_Type {True_Dual_Port_RAM} \
    CONFIG.Write_Width_A {32} \
    CONFIG.Read_Width_A {32} \
    CONFIG.Write_Depth_A {16384} \
    CONFIG.Operating_Mode_A {READ_FIRST} \
    CONFIG.Enable_A {Use_ENA_Pin} \
    CONFIG.Write_Width_B {32} \
    CONFIG.Read_Width_B {32} \
    CONFIG.Operating_Mode_B {READ_FIRST} \
    CONFIG.Enable_B {Use_ENB_Pin} \
    CONFIG.Register_PortB_Output_of_Memory_Primitives {false} \
] $bram
connect_bd_intf_net [get_bd_intf_pins bram_ctrl/BRAM_PORTA] [get_bd_intf_pins bram/BRAM_PORTA]

set lcd [create_bd_cell -type module -reference lcd_status_top lcd_status_0]
connect_bd_net $pclk [get_bd_pins lcd_status_0/pclk]
connect_bd_net [get_bd_pins rst_pix/peripheral_aresetn] [get_bd_pins lcd_status_0/reset_n]
connect_bd_net [get_bd_pins lcd_status_0/bram_addr] [get_bd_pins bram/addrb]
connect_bd_net [get_bd_pins bram/doutb] [get_bd_pins lcd_status_0/bram_dout]
## dbg 连接必须放在 lcd_status_0 创建之后（否则 get_bd_pins 找不到该 cell）
connect_bd_net [get_bd_pins lcd_status_0/dbg1] [get_bd_pins gpio_dbg/gpio_io_i]
connect_bd_net [get_bd_pins lcd_status_0/dbg2] [get_bd_pins gpio_dbg/gpio2_io_i]
connect_bd_net $pclk [get_bd_pins bram/clkb]
## BRAM 的 rstb 是高电平有效，而 proc_sys_reset 的 peripheral_aresetn 是低有效：
## 直接连过去等于「平时恒 1 = 一直复位」，doutb 永远是 0（session 013 网表实证）
## ⇒ fb_reader 恒读 BLACK。Port B 不需要复位，直接接常量 0。
set zero [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 zero]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {0}] $zero
connect_bd_net [get_bd_pins zero/dout] [get_bd_pins bram/rstb]

set one [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 one]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {1}] $one
connect_bd_net [get_bd_pins one/dout] [get_bd_pins bram/enb]

create_bd_port -dir O -type data lcd_de
create_bd_port -dir O -type data lcd_hs
create_bd_port -dir O -type data lcd_vs
create_bd_port -dir O -type data lcd_bl
create_bd_port -dir O -type data lcd_clk
create_bd_port -dir O -type data lcd_rst
create_bd_port -dir IO -from 23 -to 0 -type data lcd_rgb
foreach p {lcd_de lcd_hs lcd_vs lcd_bl lcd_clk lcd_rst lcd_rgb} {
    connect_bd_net [get_bd_ports $p] [get_bd_pins lcd_status_0/$p]
}

assign_bd_address -offset 0x40000000 -range 64K [get_bd_addr_segs {bram_ctrl/S_AXI/Mem0}]
assign_bd_address -offset 0x41200000 -range 64K [get_bd_addr_segs {gpio_dbg/S_AXI/Reg}]
validate_bd_design
save_bd_design

set bd_file [get_files system.bd]
make_wrapper -files $bd_file -top
add_files -norecurse [file join $proj_dir lcd_status_bd.gen sources_1 bd system hdl system_wrapper.v]
set_property top system_wrapper [current_fileset]
update_compile_order -fileset sources_1
generate_target all [get_files system.bd]

## 注：本设计的 MRAM 读口 EN 由 xlconstant 常 1 驱动，PwrOpt 的 COOOL gate 只影响
## Port A 的 wea，不是黑屏根因（根因是 fb_reader 的移位方向，已在 RTL 修）；
## 这里不再传 opt_design 的额外开关，保持默认流程。
launch_runs impl_1 -to_step write_bitstream -jobs 8
wait_on_run impl_1
set st [get_property STATUS [get_runs impl_1]]
if {[string first "write_bitstream Complete" $st] < 0} { puts "LCD_STATUS_BD_FAIL $st"; return }

set hwh [file join $proj_dir lcd_status_bd.gen sources_1 bd system hw_handoff system.hwh]
if {![file exists $hwh]} { puts "LCD_STATUS_BD_FAIL no_hwh"; return }
file copy -force $hwh [file join $overlay lcd_status.hwh]

open_run impl_1
report_timing_summary -file [file join $rep_timing lcd_status_bd_timing.rpt]
report_utilization   -file [file join $rep_util lcd_status_bd_util.rpt]

set bit [file join $proj_dir lcd_status_bd.runs impl_1 system_wrapper.bit]
if {![file exists $bit]} {
    set cand [glob -nocomplain [file join $proj_dir lcd_status_bd.runs impl_1 *.bit]]
    if {[llength $cand] > 0} { set bit [lindex $cand 0] }
}
file copy -force $bit [file join $overlay lcd_status.bit]
puts "LCD_STATUS_BD_OK BIT=[file join $overlay lcd_status.bit]"