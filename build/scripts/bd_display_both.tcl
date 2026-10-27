# bd_display_both.tcl —— HDMI + LCD 同时显示（D023）
#
# 两个 1bpp BRAM：
#   HDMI: 0x40000000, 32,768 x 32-bit（用 28,800）
#   LCD : 0x40020000, 16,384 x 32-bit（用 12,000）
# 两个 AXI BRAM Controller 都是 SINGLE_PORT_BRAM=1（Port A=AXI 读写），
# 显示读各自的 BRAM Port B，因此可以同时刷新。
set script_dir [file normalize [file dirname [info script]]]
set root       [file normalize [file join $script_dir .. ..]]
set part_name  "xc7z020clg400-2"
set proj_dir   [file join $root build vivado display_both_bd]
set overlay    [file join $root board overlay]
set rtl_hdmi   [file join $root src rtl io hdmi]
set rtl_lcd    [file join $root src rtl io lcd]
set rep_timing [file join $root build reports timing]
set rep_util   [file join $root build reports resource]
set xdc        [file join $script_dir display_both.xdc]

foreach d [list $overlay $rep_timing $rep_util] { file mkdir $d }
create_project -force display_both_bd $proj_dir -part $part_name
add_files -norecurse [list \
    [file join $rtl_hdmi asyn_rst_syn.v] \
    [file join $rtl_hdmi dvi_encoder.v] \
    [file join $rtl_hdmi dvi_transmitter_top.v] \
    [file join $rtl_hdmi serializer_10_to_1.v] \
    [file join $rtl_hdmi video_driver.v] \
    [file join $rtl_hdmi fb_reader.v] \
    [file join $rtl_hdmi hdmi_status_top.v] \
    [file join $rtl_lcd lcd_driver.v] \
    [file join $rtl_lcd lcd_fb_reader.v] \
    [file join $rtl_lcd lcd_status_top.v] \
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

## ---- HDMI 侧像素时钟：与已验证的 hdmi_status.bit 一致（输入取 PS FCLK_CLK0 83.333）----
set clk_wiz [create_bd_cell -type ip -vlnv xilinx.com:ip:clk_wiz:6.0 clk_wiz]
set_property -dict [list \
    CONFIG.PRIM_IN_FREQ {83.333336} \
    CONFIG.CLKOUT1_REQUESTED_OUT_FREQ {74.25} \
    CONFIG.CLKOUT2_USED {true} \
    CONFIG.CLKOUT2_REQUESTED_OUT_FREQ {371.25} \
    CONFIG.USE_LOCKED {true} \
    CONFIG.USE_RESET {true} \
    CONFIG.RESET_PORT {reset} \
    CONFIG.RESET_TYPE {ACTIVE_HIGH} \
] $clk_wiz
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins clk_wiz/clk_in1]
connect_bd_net [get_bd_pins rst_ps/peripheral_reset] [get_bd_pins clk_wiz/reset]

## ---- LCD 侧像素时钟：与已验证的 lcd_status.bit 一致（外部 50 MHz → 25 MHz）----
## 为什么不和 HDMI 共用一个 clk_wiz：74.25 / 371.25 与 25 没有公共 VCO，
## 合在一起只能出近似频率；25 MHz 是 4384 屏的官方值（oi 项目实测通过），必须准确。
set clk_wiz_lcd [create_bd_cell -type ip -vlnv xilinx.com:ip:clk_wiz:6.0 clk_wiz_lcd]
set_property -dict [list \
    CONFIG.PRIM_IN_FREQ {50.000} \
    CONFIG.CLKOUT1_REQUESTED_OUT_FREQ {25.000} \
    CONFIG.USE_LOCKED {true} \
    CONFIG.USE_RESET {true} \
    CONFIG.RESET_PORT {reset} \
    CONFIG.RESET_TYPE {ACTIVE_HIGH} \
] $clk_wiz_lcd
connect_bd_net [get_bd_ports sys_clk] [get_bd_pins clk_wiz_lcd/clk_in1]
connect_bd_net [get_bd_pins rst_ps/peripheral_reset] [get_bd_pins clk_wiz_lcd/reset]

set pclk_hdmi [get_bd_pins clk_wiz/clk_out1]
set pclk5 [get_bd_pins clk_wiz/clk_out2]
set pclk_lcd [get_bd_pins clk_wiz_lcd/clk_out1]

set rst_hdmi [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_hdmi]
connect_bd_net $pclk_hdmi [get_bd_pins rst_hdmi/slowest_sync_clk]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_RESET0_N] [get_bd_pins rst_hdmi/ext_reset_in]
## dcm_locked 必须显式接 locked：悬空时 Vivado 会 tie 成常量，若 tie 到 0 则复位永不释放
## （session 013/014：lcd_de 与 TMDS 恒 0、屏幕对写全 1 毫无反应）
connect_bd_net [get_bd_pins clk_wiz/locked] [get_bd_pins rst_hdmi/dcm_locked]
set rst_lcd [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_lcd]
connect_bd_net $pclk_lcd [get_bd_pins rst_lcd/slowest_sync_clk]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_RESET0_N] [get_bd_pins rst_lcd/ext_reset_in]
connect_bd_net [get_bd_pins clk_wiz_lcd/locked] [get_bd_pins rst_lcd/dcm_locked]

set axi_smc [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 axi_smc]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {2}] $axi_smc
connect_bd_intf_net [get_bd_intf_pins processing_system7_0/M_AXI_GP0] [get_bd_intf_pins axi_smc/S00_AXI]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins axi_smc/aclk]
connect_bd_net [get_bd_pins rst_ps/peripheral_aresetn] [get_bd_pins axi_smc/aresetn]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins processing_system7_0/M_AXI_GP0_ACLK]

proc make_bram {name depth range} {
    set ctrl [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_bram_ctrl:4.1 ${name}_ctrl]
    set_property -dict [list \
        CONFIG.DATA_WIDTH {32} \
        CONFIG.SINGLE_PORT_BRAM {1} \
        CONFIG.ECC_TYPE {0} \
        CONFIG.PROTOCOL {AXI4LITE} \
    ] $ctrl
    set mem [create_bd_cell -type ip -vlnv xilinx.com:ip:blk_mem_gen:8.4 ${name}_mem]
    set_property -dict [list \
        CONFIG.Memory_Type {True_Dual_Port_RAM} \
        CONFIG.Write_Width_A {32} \
        CONFIG.Read_Width_A {32} \
        CONFIG.Write_Depth_A $depth \
        CONFIG.Operating_Mode_A {READ_FIRST} \
        CONFIG.Enable_A {Use_ENA_Pin} \
        CONFIG.Write_Width_B {32} \
        CONFIG.Read_Width_B {32} \
        CONFIG.Operating_Mode_B {READ_FIRST} \
        CONFIG.Enable_B {Use_ENB_Pin} \
        CONFIG.Register_PortB_Output_of_Memory_Primitives {false} \
    ] $mem
    connect_bd_intf_net [get_bd_intf_pins ${name}_ctrl/BRAM_PORTA] [get_bd_intf_pins ${name}_mem/BRAM_PORTA]
    connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins ${name}_ctrl/s_axi_aclk]
    connect_bd_net [get_bd_pins rst_ps/peripheral_aresetn] [get_bd_pins ${name}_ctrl/s_axi_aresetn]
    return [list $ctrl $mem]
}

lassign [make_bram hdmi 32768 128K] hdmi_ctrl hdmi_mem
lassign [make_bram lcd 16384 64K] lcd_ctrl lcd_mem
connect_bd_intf_net [get_bd_intf_pins axi_smc/M00_AXI] [get_bd_intf_pins $hdmi_ctrl/S_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_smc/M01_AXI] [get_bd_intf_pins $lcd_ctrl/S_AXI]

set one [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 one]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {1}] $one
## BRAM 的 rstb 是高电平有效，peripheral_aresetn 是低有效：直连等于读口常复位、doutb 恒 0。
## 两块屏的读口都不需要复位，接常量 0（session 013/014 实证）。
set zero [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 zero]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {0}] $zero

set hdmi [create_bd_cell -type module -reference hdmi_status_top hdmi_status_0]
connect_bd_net $pclk_hdmi [get_bd_pins hdmi_status_0/pclk]
connect_bd_net $pclk5 [get_bd_pins hdmi_status_0/pclk_x5]
connect_bd_net [get_bd_pins rst_hdmi/peripheral_aresetn] [get_bd_pins hdmi_status_0/reset_n]
connect_bd_net [get_bd_pins hdmi_status_0/bram_addr] [get_bd_pins $hdmi_mem/addrb]
connect_bd_net [get_bd_pins $hdmi_mem/doutb] [get_bd_pins hdmi_status_0/bram_dout]
connect_bd_net $pclk_hdmi [get_bd_pins $hdmi_mem/clkb]
connect_bd_net [get_bd_pins zero/dout] [get_bd_pins $hdmi_mem/rstb]
connect_bd_net [get_bd_pins one/dout] [get_bd_pins $hdmi_mem/enb]

set lcd [create_bd_cell -type module -reference lcd_status_top lcd_status_0]
connect_bd_net $pclk_lcd [get_bd_pins lcd_status_0/pclk]
connect_bd_net [get_bd_pins rst_lcd/peripheral_aresetn] [get_bd_pins lcd_status_0/reset_n]
connect_bd_net [get_bd_pins lcd_status_0/bram_addr] [get_bd_pins $lcd_mem/addrb]
connect_bd_net [get_bd_pins $lcd_mem/doutb] [get_bd_pins lcd_status_0/bram_dout]
connect_bd_net $pclk_lcd [get_bd_pins $lcd_mem/clkb]
connect_bd_net [get_bd_pins zero/dout] [get_bd_pins $lcd_mem/rstb]
connect_bd_net [get_bd_pins one/dout] [get_bd_pins $lcd_mem/enb]

create_bd_port -dir O -type data tmds_clk_p
create_bd_port -dir O -type data tmds_clk_n
create_bd_port -dir O -from 2 -to 0 -type data tmds_data_p
create_bd_port -dir O -from 2 -to 0 -type data tmds_data_n
foreach p {tmds_clk_p tmds_clk_n tmds_data_p tmds_data_n} {
    connect_bd_net [get_bd_ports $p] [get_bd_pins hdmi_status_0/$p]
}
foreach p {lcd_de lcd_hs lcd_vs lcd_bl lcd_clk lcd_rst} {
    create_bd_port -dir O -type data $p
    connect_bd_net [get_bd_ports $p] [get_bd_pins lcd_status_0/$p]
}
create_bd_port -dir O -from 23 -to 0 -type data lcd_rgb
connect_bd_net [get_bd_ports lcd_rgb] [get_bd_pins lcd_status_0/lcd_rgb]

assign_bd_address -offset 0x40000000 -range 128K [get_bd_addr_segs {hdmi_ctrl/S_AXI/Mem0}]
assign_bd_address -offset 0x40020000 -range 64K  [get_bd_addr_segs {lcd_ctrl/S_AXI/Mem0}]
validate_bd_design
save_bd_design

set bd_file [get_files system.bd]
make_wrapper -files $bd_file -top
add_files -norecurse [file join $proj_dir display_both_bd.gen sources_1 bd system hdl system_wrapper.v]
set_property top system_wrapper [current_fileset]
update_compile_order -fileset sources_1
generate_target all [get_files system.bd]

launch_runs impl_1 -to_step write_bitstream -jobs 8
wait_on_run impl_1
set st [get_property STATUS [get_runs impl_1]]
if {[string first "write_bitstream Complete" $st] < 0} { puts "DISPLAY_BOTH_BD_FAIL $st"; return }

set hwh [file join $proj_dir display_both_bd.gen sources_1 bd system hw_handoff system.hwh]
if {![file exists $hwh]} { puts "DISPLAY_BOTH_BD_FAIL no_hwh"; return }
file copy -force $hwh [file join $overlay display_both.hwh]

open_run impl_1
report_timing_summary -file [file join $rep_timing display_both_bd_timing.rpt]
report_utilization   -file [file join $rep_util display_both_bd_util.rpt]

set bit [file join $proj_dir display_both_bd.runs impl_1 system_wrapper.bit]
if {![file exists $bit]} {
    set cand [glob -nocomplain [file join $proj_dir display_both_bd.runs impl_1 *.bit]]
    if {[llength $cand] > 0} { set bit [lindex $cand 0] }
}
file copy -force $bit [file join $overlay display_both.bit]
puts "DISPLAY_BOTH_BD_OK BIT=[file join $overlay display_both.bit]"