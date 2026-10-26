# bd_hdmi_status.tcl —— HDMI 1280x720 状态面板
#
# PS 通过 AXI BRAM Controller 写 1bpp 帧缓存（28,800 x 32-bit），
# PL 的 fb_reader 读 Port B → video_driver → dvi_transmitter_top → TMDS。
#
# 产物：board/overlay/hdmi_status.bit/.hwh
# 判据：末行 HDMI_STATUS_BD_OK
set script_dir [file normalize [file dirname [info script]]]
set root       [file normalize [file join $script_dir .. ..]]
set part_name  "xc7z020clg400-2"
set proj_dir   [file join $root build vivado hdmi_status_bd]
set overlay    [file join $root board overlay]
set rtl_dir    [file join $root src rtl io hdmi]
set rep_timing [file join $root build reports timing]
set rep_util   [file join $root build reports resource]
set xdc        [file join $script_dir hdmi_status.xdc]

foreach d [list $overlay $rep_timing $rep_util] { file mkdir $d }
create_project -force hdmi_status_bd $proj_dir -part $part_name
add_files -norecurse [list \
    [file join $rtl_dir asyn_rst_syn.v] \
    [file join $rtl_dir dvi_encoder.v] \
    [file join $rtl_dir dvi_transmitter_top.v] \
    [file join $rtl_dir serializer_10_to_1.v] \
    [file join $rtl_dir video_driver.v] \
    [file join $rtl_dir fb_reader.v] \
    [file join $rtl_dir hdmi_status_top.v] \
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

set clk_wiz [create_bd_cell -type ip -vlnv xilinx.com:ip:clk_wiz:6.0 clk_wiz]
set_property -dict [list \
    CONFIG.PRIM_IN_FREQ {50.000} \
    CONFIG.CLKOUT1_REQUESTED_OUT_FREQ {74.25} \
    CONFIG.CLKOUT2_USED {true} \
    CONFIG.CLKOUT2_REQUESTED_OUT_FREQ {371.25} \
    CONFIG.USE_LOCKED {false} \
    CONFIG.USE_RESET {true} \
    CONFIG.RESET_PORT {reset} \
    CONFIG.RESET_TYPE {ACTIVE_HIGH} \
] $clk_wiz
create_bd_port -dir I -type clk sys_clk
set_property CONFIG.FREQ_HZ {50000000} [get_bd_ports sys_clk]
connect_bd_net [get_bd_ports sys_clk] [get_bd_pins clk_wiz/clk_in1]
connect_bd_net [get_bd_pins rst_ps/peripheral_reset] [get_bd_pins clk_wiz/reset]

set pclk [get_bd_pins clk_wiz/clk_out1]
set pclk5 [get_bd_pins clk_wiz/clk_out2]
set rst_pix [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_pix]
connect_bd_net $pclk [get_bd_pins rst_pix/slowest_sync_clk]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_RESET0_N] [get_bd_pins rst_pix/ext_reset_in]

set bram_ctrl [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_bram_ctrl:4.1 bram_ctrl]
set_property -dict [list \
    CONFIG.DATA_WIDTH {32} \
    CONFIG.SINGLE_PORT_BRAM {1} \
    CONFIG.ECC_TYPE {0} \
    CONFIG.PROTOCOL {AXI4} \
] $bram_ctrl
set axi_smc [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 axi_smc]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1}] $axi_smc
connect_bd_intf_net [get_bd_intf_pins processing_system7_0/M_AXI_GP0] [get_bd_intf_pins axi_smc/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_smc/M00_AXI] [get_bd_intf_pins bram_ctrl/S_AXI]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins axi_smc/aclk]
connect_bd_net [get_bd_pins rst_ps/peripheral_aresetn] [get_bd_pins axi_smc/aresetn]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins processing_system7_0/M_AXI_GP0_ACLK]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins bram_ctrl/s_axi_aclk]
connect_bd_net [get_bd_pins rst_ps/peripheral_aresetn] [get_bd_pins bram_ctrl/s_axi_aresetn]

set bram [create_bd_cell -type ip -vlnv xilinx.com:ip:blk_mem_gen:8.4 bram]
set_property -dict [list \
    CONFIG.Memory_Type {True_Dual_Port_RAM} \
    CONFIG.Write_Width_A {32} \
    CONFIG.Read_Width_A {32} \
    CONFIG.Write_Depth_A {32768} \
    CONFIG.Operating_Mode_A {READ_FIRST} \
    CONFIG.Enable_A {Use_ENA_Pin} \
    CONFIG.Write_Width_B {32} \
    CONFIG.Read_Width_B {32} \
    CONFIG.Operating_Mode_B {READ_FIRST} \
    CONFIG.Enable_B {Use_ENB_Pin} \
    CONFIG.Register_PortB_Output_of_Memory_Primitives {false} \
] $bram
connect_bd_intf_net [get_bd_intf_pins bram_ctrl/BRAM_PORTA] [get_bd_intf_pins bram/BRAM_PORTA]

set hdmi [create_bd_cell -type module -reference hdmi_status_top hdmi_status_0]
connect_bd_net $pclk [get_bd_pins hdmi_status_0/pclk]
connect_bd_net $pclk5 [get_bd_pins hdmi_status_0/pclk_x5]
connect_bd_net [get_bd_pins rst_pix/peripheral_aresetn] [get_bd_pins hdmi_status_0/reset_n]
connect_bd_net [get_bd_pins hdmi_status_0/bram_addr] [get_bd_pins bram/addrb]
connect_bd_net [get_bd_pins bram/doutb] [get_bd_pins hdmi_status_0/bram_dout]
connect_bd_net $pclk [get_bd_pins bram/clkb]
connect_bd_net [get_bd_pins rst_pix/peripheral_aresetn] [get_bd_pins bram/rstb]

set one [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 one]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {1}] $one
connect_bd_net [get_bd_pins one/dout] [get_bd_pins bram/enb]

foreach {p n} {tmds_clk_p 1 tmds_clk_n 1} {
    create_bd_port -dir O -type data $p
}
create_bd_port -dir O -from 2 -to 0 -type data tmds_data_p
create_bd_port -dir O -from 2 -to 0 -type data tmds_data_n
connect_bd_net [get_bd_ports tmds_clk_p] [get_bd_pins hdmi_status_0/tmds_clk_p]
connect_bd_net [get_bd_ports tmds_clk_n] [get_bd_pins hdmi_status_0/tmds_clk_n]
connect_bd_net [get_bd_ports tmds_data_p] [get_bd_pins hdmi_status_0/tmds_data_p]
connect_bd_net [get_bd_ports tmds_data_n] [get_bd_pins hdmi_status_0/tmds_data_n]

assign_bd_address -offset 0x40000000 -range 128K [get_bd_addr_segs {bram_ctrl/S_AXI/Mem0}]
validate_bd_design
save_bd_design

set bd_file [get_files system.bd]
make_wrapper -files $bd_file -top
add_files -norecurse [file join $proj_dir hdmi_status_bd.gen sources_1 bd system hdl system_wrapper.v]
set_property top system_wrapper [current_fileset]
update_compile_order -fileset sources_1
generate_target all [get_files system.bd]

launch_runs impl_1 -to_step write_bitstream -jobs 8
wait_on_run impl_1
set st [get_property STATUS [get_runs impl_1]]
if {[string first "write_bitstream Complete" $st] < 0} { puts "HDMI_STATUS_BD_FAIL $st"; return }

set hwh [file join $proj_dir hdmi_status_bd.gen sources_1 bd system hw_handoff system.hwh]
if {![file exists $hwh]} { puts "HDMI_STATUS_BD_FAIL no_hwh"; return }
file copy -force $hwh [file join $overlay hdmi_status.hwh]

open_run impl_1
report_timing_summary -file [file join $rep_timing hdmi_status_bd_timing.rpt]
report_utilization   -file [file join $rep_util hdmi_status_bd_util.rpt]

set bit [file join $proj_dir hdmi_status_bd.runs impl_1 system_wrapper.bit]
if {![file exists $bit]} {
    set cand [glob -nocomplain [file join $proj_dir hdmi_status_bd.runs impl_1 *.bit]]
    if {[llength $cand] > 0} { set bit [lindex $cand 0] }
}
file copy -force $bit [file join $overlay hdmi_status.bit]
puts "HDMI_STATUS_BD_OK BIT=[file join $overlay hdmi_status.bit]"