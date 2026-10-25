# bd_preproc.tcl —— preproc 单独出片（gmem_in->HP0, gmem_out->HP1, 控制走 GP0）
set script_dir [file normalize [file dirname [info script]]]
set root       [file normalize [file join $script_dir .. ..]]
set part_name  "xc7z020clg400-2"
set proj_dir   [file join $root build vivado preproc_bd]
set ip_repo    [file join $root build ip_repo]
set overlay    [file join $root board overlay]
set rep_timing [file join $root build reports timing]
set rep_util   [file join $root build reports resource]

if {![file exists [file join $ip_repo preproc component.xml]]} {
    puts "BD_FAIL 找不到 preproc IP"
    return
}
foreach d [list $overlay $rep_timing $rep_util] { file mkdir $d }

create_project -force preproc_bd $proj_dir -part $part_name
set_property ip_repo_paths $ip_repo [current_project]
update_ip_catalog -rebuild

create_bd_design "system"
source [file join $root build scripts ps7_config.tcl]
set_property -dict [list \
    CONFIG.PCW_USE_S_AXI_HP0 {1} \
    CONFIG.PCW_USE_S_AXI_HP1 {1} \
    CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ {83.333333} \
] $processing_system7_0

set pp [create_bd_cell -type ip -vlnv zynq-yolo:hls:preproc:1.0 preproc_0]

set ctrl_smc [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 ctrl_smc]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {2}] $ctrl_smc
connect_bd_intf_net [get_bd_intf_pins processing_system7_0/M_AXI_GP0] [get_bd_intf_pins ctrl_smc/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins ctrl_smc/M00_AXI] [get_bd_intf_pins preproc_0/s_axi_control]
connect_bd_intf_net [get_bd_intf_pins ctrl_smc/M01_AXI] [get_bd_intf_pins preproc_0/s_axi_control_r]

set rst [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_ps7]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins rst_ps7/slowest_sync_clk]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_RESET0_N] [get_bd_pins rst_ps7/ext_reset_in]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins preproc_0/ap_clk]
connect_bd_net [get_bd_pins rst_ps7/peripheral_aresetn] [get_bd_pins preproc_0/ap_rst_n]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins ctrl_smc/aclk]
connect_bd_net [get_bd_pins rst_ps7/peripheral_aresetn] [get_bd_pins ctrl_smc/aresetn]

foreach {port hp} {gmem_in S_AXI_HP0 gmem_out S_AXI_HP1} {
    set sc [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 sc_$port]
    set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1}] $sc
    connect_bd_intf_net [get_bd_intf_pins preproc_0/m_axi_$port] [get_bd_intf_pins sc_$port/S00_AXI]
    connect_bd_intf_net [get_bd_intf_pins sc_$port/M00_AXI] [get_bd_intf_pins processing_system7_0/$hp]
    connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins sc_$port/aclk]
    connect_bd_net [get_bd_pins rst_ps7/peripheral_aresetn] [get_bd_pins sc_$port/aresetn]
}

connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins processing_system7_0/M_AXI_GP0_ACLK]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins processing_system7_0/S_AXI_HP0_ACLK]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins processing_system7_0/S_AXI_HP1_ACLK]

assign_bd_address
validate_bd_design
save_bd_design

set bd_file [get_files system.bd]
make_wrapper -files $bd_file -top
add_files -norecurse [file join $proj_dir preproc_bd.gen sources_1 bd system hdl system_wrapper.v]
set_property top system_wrapper [current_fileset]
update_compile_order -fileset sources_1
generate_target all [get_files system.bd]

set hwh [file join $proj_dir preproc_bd.gen sources_1 bd system hw_handoff system.hwh]
if {[file exists $hwh]} {
    file copy -force $hwh [file join $overlay preproc.hwh]
} else {
    puts "BD_FAIL 没找到 .hwh"; return
}

launch_runs impl_1 -to_step write_bitstream -jobs 8
wait_on_run impl_1
set st [get_property STATUS [get_runs impl_1]]
if {[string first "write_bitstream Complete" $st] < 0} { puts "BD_FAIL $st"; return }

open_run impl_1
report_timing_summary -file [file join $rep_timing preproc_bd_timing.rpt]
report_utilization   -file [file join $rep_util   preproc_bd_util.rpt]

set bit [file join $proj_dir preproc_bd.runs impl_1 system_wrapper.bit]
if {![file exists $bit]} {
    set cand [glob -nocomplain [file join $proj_dir preproc_bd.runs impl_1 *.bit]]
    if {[llength $cand] > 0} { set bit [lindex $cand 0] }
}
file copy -force $bit [file join $overlay preproc.bit]
set wns "?"
if {![catch { set wns [get_property SLACK [get_timing_paths -delay_type max]] }]} {}
puts "BD_DONE_OK BIT=[file join $overlay preproc.bit] WNS=$wns"
