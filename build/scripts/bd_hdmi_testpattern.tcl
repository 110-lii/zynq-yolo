# bd_hdmi_testpattern.tcl —— 纯 PL 测试图案 → HDMI（不碰 BRAM）
#
# 目的：把「显示链路」与「帧缓存读出」切开。像素由 hdmi_testpattern_top 组合逻辑生成。
# 时钟：外部 50 MHz（U18）→ clk_wiz → 75.000 MHz（pclk）+ 375.000 MHz（pclk_x5）
#       —— 与正点原子 23_hdmi_colorbar 逐参数一致。
# 产物：board/overlay/hdmi_testpattern.bit/.hwh
# 判据：末行 HDMI_TESTPATTERN_BD_OK
set script_dir [file normalize [file dirname [info script]]]
set root       [file normalize [file join $script_dir .. ..]]
set part_name  "xc7z020clg400-2"
set proj_dir   [expr {[info exists ::env(TP_PROJ)] ? $::env(TP_PROJ) : [file join $root build vivado hdmi_testpattern_bd]}]
set overlay    [file join $root board overlay]
set rtl_dir    [file join $root src rtl io hdmi]
set rep_timing [file join $root build reports timing]
set rep_util   [file join $root build reports resource]
set xdc        [file join $script_dir hdmi_testpattern.xdc]

foreach d [list $overlay $rep_timing $rep_util] { file mkdir $d }
create_project -force hdmi_testpattern_bd $proj_dir -part $part_name
add_files -norecurse [list \
    [file join $rtl_dir asyn_rst_syn.v] \
    [file join $rtl_dir dvi_encoder.v] \
    [file join $rtl_dir dvi_transmitter_top.v] \
    [file join $rtl_dir serializer_10_to_1.v] \
    [file join $rtl_dir video_driver.v] \
    [file join $rtl_dir hdmi_testpattern_top.v] \
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

create_bd_port -dir I -type clk sys_clk
set_property CONFIG.FREQ_HZ {50000000} [get_bd_ports sys_clk]

set clk_wiz [create_bd_cell -type ip -vlnv xilinx.com:ip:clk_wiz:6.0 clk_wiz]
set_property -dict [list \
    CONFIG.PRIM_IN_FREQ {50.000} \
    CONFIG.CLKOUT1_REQUESTED_OUT_FREQ {75.000} \
    CONFIG.CLKOUT2_USED {true} \
    CONFIG.CLKOUT2_REQUESTED_OUT_FREQ {375.000} \
    CONFIG.USE_LOCKED {true} \
    CONFIG.USE_RESET {true} \
    CONFIG.RESET_PORT {reset} \
    CONFIG.RESET_TYPE {ACTIVE_HIGH} \
] $clk_wiz
connect_bd_net [get_bd_ports sys_clk] [get_bd_pins clk_wiz/clk_in1]

set pclk  [get_bd_pins clk_wiz/clk_out1]
set pclk5 [get_bd_pins clk_wiz/clk_out2]

set rst_pix [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_pix]
connect_bd_net $pclk [get_bd_pins rst_pix/slowest_sync_clk]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_RESET0_N] [get_bd_pins rst_pix/ext_reset_in]
connect_bd_net [get_bd_pins clk_wiz/locked] [get_bd_pins rst_pix/dcm_locked]

## ⚠️ clk_wiz/reset 不能取 rst_pix/peripheral_reset：那会形成死锁环
##    （clk_wiz.reset ← pix 复位；pix 复位的 dcm_locked ← clk_wiz.locked）
##    ⇒ MMCM 永不锁定、像素时钟不存在、屏幕完全无信号。
##    这里直接接常量 0（与正点原子 23_hdmi_colorbar 一致），MMCM 上电自行锁定；
##    像素域复位仍由 rst_pix 负责，只是它不再参与 MMCM 的复位。
set zero_rst [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 zero_rst]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {0}] $zero_rst
connect_bd_net [get_bd_pins zero_rst/dout] [get_bd_pins clk_wiz/reset]

set tp [create_bd_cell -type module -reference hdmi_testpattern_top tp_0]
connect_bd_net $pclk  [get_bd_pins tp_0/pclk]
connect_bd_net $pclk5 [get_bd_pins tp_0/pclk_x5]
connect_bd_net [get_bd_pins rst_pix/peripheral_aresetn] [get_bd_pins tp_0/reset_n]
connect_bd_net [get_bd_pins clk_wiz/locked] [get_bd_pins tp_0/locked]

## ---- 调试回读：双通道 AXI GPIO（全输入）----
## 为什么现在就加：前面几轮全是「改一版→等人看屏」，一次只能排除一件事。
## 有了这块 GPIO，locked / de / xpos,ypos 能直接在 PS 侧读出来，
## 「MMCM 没锁」「像素时钟没跑」「DE 没拉」「数据侧不对」当场能分开。
set rst_ps [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_ps]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins rst_ps/slowest_sync_clk]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_RESET0_N] [get_bd_pins rst_ps/ext_reset_in]

set axi_smc [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 axi_smc]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1}] $axi_smc
connect_bd_intf_net [get_bd_intf_pins processing_system7_0/M_AXI_GP0] [get_bd_intf_pins axi_smc/S00_AXI]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins axi_smc/aclk]
connect_bd_net [get_bd_pins rst_ps/peripheral_aresetn] [get_bd_pins axi_smc/aresetn]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins processing_system7_0/M_AXI_GP0_ACLK]

set gpio [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 gpio_dbg]
set_property -dict [list \
    CONFIG.C_IS_DUAL {1} \
    CONFIG.C_ALL_INPUTS {1} \
    CONFIG.C_ALL_INPUTS_2 {1} \
    CONFIG.C_GPIO_WIDTH {32} \
    CONFIG.C_GPIO2_WIDTH {32} \
] $gpio
connect_bd_intf_net [get_bd_intf_pins axi_smc/M00_AXI] [get_bd_intf_pins gpio_dbg/S_AXI]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins gpio_dbg/s_axi_aclk]
connect_bd_net [get_bd_pins rst_ps/peripheral_aresetn] [get_bd_pins gpio_dbg/s_axi_aresetn]
connect_bd_net [get_bd_pins tp_0/dbg_a] [get_bd_pins gpio_dbg/gpio_io_i]
connect_bd_net [get_bd_pins tp_0/dbg_b] [get_bd_pins gpio_dbg/gpio2_io_i]
assign_bd_address -offset 0x41200000 -range 8K [get_bd_addr_segs {gpio_dbg/S_AXI/Reg}]

create_bd_port -dir O -type data tmds_clk_p
create_bd_port -dir O -type data tmds_clk_n
create_bd_port -dir O -from 2 -to 0 -type data tmds_data_p
create_bd_port -dir O -from 2 -to 0 -type data tmds_data_n
connect_bd_net [get_bd_ports tmds_clk_p]  [get_bd_pins tp_0/tmds_clk_p]
connect_bd_net [get_bd_ports tmds_clk_n]  [get_bd_pins tp_0/tmds_clk_n]
connect_bd_net [get_bd_ports tmds_data_p] [get_bd_pins tp_0/tmds_data_p]
connect_bd_net [get_bd_ports tmds_data_n] [get_bd_pins tp_0/tmds_data_n]

validate_bd_design
save_bd_design

set bd_file [get_files system.bd]
make_wrapper -files $bd_file -top
add_files -norecurse [file join $proj_dir hdmi_testpattern_bd.gen sources_1 bd system hdl system_wrapper.v]
set_property top system_wrapper [current_fileset]
update_compile_order -fileset sources_1
generate_target all [get_files system.bd]

launch_runs impl_1 -to_step write_bitstream -jobs 8
wait_on_run impl_1
set st [get_property STATUS [get_runs impl_1]]
if {[string first "write_bitstream Complete" $st] < 0} { puts "HDMI_TESTPATTERN_BD_FAIL $st"; return }

set hwh [file join $proj_dir hdmi_testpattern_bd.gen sources_1 bd system hw_handoff system.hwh]
if {![file exists $hwh]} { puts "HDMI_TESTPATTERN_BD_FAIL no_hwh"; return }
file copy -force $hwh [file join $overlay hdmi_testpattern.hwh]

open_run impl_1
report_timing_summary -file [file join $rep_timing hdmi_testpattern_bd_timing.rpt]
report_utilization   -file [file join $rep_util hdmi_testpattern_bd_util.rpt]

set bit [file join $proj_dir hdmi_testpattern_bd.runs impl_1 system_wrapper.bit]
if {![file exists $bit]} {
    set cand [glob -nocomplain [file join $proj_dir hdmi_testpattern_bd.runs impl_1 *.bit]]
    if {[llength $cand] > 0} { set bit [lindex $cand 0] }
}
file copy -force $bit [file join $overlay hdmi_testpattern.bit]
puts "HDMI_TESTPATTERN_BD_OK BIT=[file join $overlay hdmi_testpattern.bit]"
