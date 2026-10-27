# bd_hdmi_video.tcl —— HDMI 视频环出（1280x720@30，带检测框的视频）
#
# 与 bd_hdmi_status.tcl（1bpp 文字面板）并列的第二个 HDMI 方案：
#   PS 每帧画好带框画面（32bpp BGR888 + 1 字节填充）写进 DDR 帧缓存，
#   PL 的 fb_scanout 经 **S_AXI_HP0** 把 DDR 读出来 → video_driver → TMDS。
#
# 关键约束（都是踩过的坑，别改）：
#   · S_AXI_HP0_ACLK 必须与 pclk(74.25 MHz) 同源 —— fb_scanout 内部不做 AXI 跨时钟桥
#   · ARID 固定 0（单突发在飞），因此 HP0 上不需要 ID 路由
#   · 寄存器用两个 AXI GPIO：cfg（双通道输出）+ status（输入），不手写 AXI-Lite 从机
#
# 产物：board/overlay/hdmi_video.bit/.hwh
# 判据：末行 HDMI_VIDEO_BD_OK
set script_dir [file normalize [file dirname [info script]]]
set root       [file normalize [file join $script_dir .. ..]]
set part_name  "xc7z020clg400-2"
set proj_dir   [file join $root build vivado hdmi_video_bd]
set overlay    [file join $root board overlay]
set rtl_dir    [file join $root src rtl io hdmi]
set rep_timing [file join $root build reports timing]
set rep_util   [file join $root build reports resource]
set xdc        [file join $script_dir hdmi_status.xdc]

foreach d [list $overlay $rep_timing $rep_util] { file mkdir $d }
create_project -force hdmi_video_bd $proj_dir -part $part_name
add_files -norecurse [list \
    [file join $rtl_dir asyn_rst_syn.v] \
    [file join $rtl_dir dvi_encoder.v] \
    [file join $rtl_dir dvi_transmitter_top.v] \
    [file join $rtl_dir serializer_10_to_1.v] \
    [file join $rtl_dir video_driver.v] \
    [file join $rtl_dir fb_scanout.v] \
    [file join $rtl_dir hdmi_video_top.v] \
]
add_files -fileset constrs_1 -norecurse $xdc
update_compile_order -fileset sources_1

create_bd_design "system"
source [file join $root build scripts ps7_config.tcl]
set_property -dict [list \
    CONFIG.PCW_USE_S_AXI_HP0 {1} \
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

set pclk  [get_bd_pins clk_wiz/clk_out1]
set pclk5 [get_bd_pins clk_wiz/clk_out2]
set rst_pix [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_pix]
connect_bd_net $pclk [get_bd_pins rst_pix/slowest_sync_clk]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_RESET0_N] [get_bd_pins rst_pix/ext_reset_in]
connect_bd_net [get_bd_pins clk_wiz/locked] [get_bd_pins rst_pix/dcm_locked]

# ---------------- 配置/状态寄存器（AXI GPIO） ----------------
set gpio_cfg [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 gpio_cfg]
set_property -dict [list \
    CONFIG.C_GPIO_WIDTH {32} \
    CONFIG.C_IS_DUAL {1} \
    CONFIG.C_GPIO2_WIDTH {32} \
    CONFIG.C_ALL_OUTPUTS {1} \
    CONFIG.C_ALL_OUTPUTS_2 {1} \
] $gpio_cfg

set gpio_st [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 gpio_st]
set_property -dict [list \
    CONFIG.C_GPIO_WIDTH {32} \
    CONFIG.C_IS_DUAL {0} \
    CONFIG.C_ALL_INPUTS {1} \
] $gpio_st

set axi_smc [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 axi_smc]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {2}] $axi_smc
connect_bd_intf_net [get_bd_intf_pins processing_system7_0/M_AXI_GP0] [get_bd_intf_pins axi_smc/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_smc/M00_AXI] [get_bd_intf_pins gpio_cfg/S_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_smc/M01_AXI] [get_bd_intf_pins gpio_st/S_AXI]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins axi_smc/aclk]
connect_bd_net [get_bd_pins rst_ps/peripheral_aresetn] [get_bd_pins axi_smc/aresetn]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins processing_system7_0/M_AXI_GP0_ACLK]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins gpio_cfg/s_axi_aclk]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins gpio_st/s_axi_aclk]
connect_bd_net [get_bd_pins rst_ps/peripheral_aresetn] [get_bd_pins gpio_cfg/s_axi_aresetn]
connect_bd_net [get_bd_pins rst_ps/peripheral_aresetn] [get_bd_pins gpio_st/s_axi_aresetn]

# ---------------- 视频顶层 ----------------
set hdmi [create_bd_cell -type module -reference hdmi_video_top hdmi_video_0]
# 模块引用推出来的 AXI 接口默认按 100 MHz 标注，与 clk_wiz 实际输出的 74.255954 MHz
# 对不上 → validate 报 BD 41-237。把该接口的 FREQ_HZ 显式改成实际像素时钟。
set_property CONFIG.FREQ_HZ {74255954} [get_bd_intf_pins hdmi_video_0/M_AXI]
connect_bd_net $pclk  [get_bd_pins hdmi_video_0/pclk]
connect_bd_net $pclk5 [get_bd_pins hdmi_video_0/pclk_x5]
connect_bd_net [get_bd_pins rst_pix/peripheral_aresetn] [get_bd_pins hdmi_video_0/reset_n]

connect_bd_net [get_bd_pins gpio_cfg/gpio_io_o]   [get_bd_pins hdmi_video_0/cfg_addr]
connect_bd_net [get_bd_pins gpio_cfg/gpio2_io_o]  [get_bd_pins hdmi_video_0/cfg_pack]
connect_bd_net [get_bd_pins hdmi_video_0/st_status] [get_bd_pins gpio_st/gpio_io_i]

# AXI4 读主机 → S_AXI_HP0。
# ★ Zynq-7000 的 HP 口是 **AXI3**，我们的主机是 AXI4：直接连会被 BD 拒绝
#   （"protocols are incompatible"）。用 smartconnect 做协议与位宽转换，
#   与本仓库其它 HP 接线（bd_track_preproc.tcl / bd_axi_bw.tcl）保持一致。
# ★ 关键：HP0 的 ACLK 与 smartconnect 的 aclk 都用 pclk —— fb_scanout 内部没有跨时钟桥。
set hp_smc [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 hp_smc]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1}] $hp_smc
connect_bd_intf_net [get_bd_intf_pins hdmi_video_0/M_AXI] [get_bd_intf_pins hp_smc/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins hp_smc/M00_AXI] [get_bd_intf_pins processing_system7_0/S_AXI_HP0]
connect_bd_net $pclk [get_bd_pins hp_smc/aclk]
connect_bd_net [get_bd_pins rst_pix/peripheral_aresetn] [get_bd_pins hp_smc/aresetn]
connect_bd_net $pclk [get_bd_pins processing_system7_0/S_AXI_HP0_ACLK]

foreach {p} {tmds_clk_p tmds_clk_n} { create_bd_port -dir O -type data $p }
create_bd_port -dir O -from 2 -to 0 -type data tmds_data_p
create_bd_port -dir O -from 2 -to 0 -type data tmds_data_n
connect_bd_net [get_bd_ports tmds_clk_p]  [get_bd_pins hdmi_video_0/tmds_clk_p]
connect_bd_net [get_bd_ports tmds_clk_n]  [get_bd_pins hdmi_video_0/tmds_clk_n]
connect_bd_net [get_bd_ports tmds_data_p] [get_bd_pins hdmi_video_0/tmds_data_p]
connect_bd_net [get_bd_ports tmds_data_n] [get_bd_pins hdmi_video_0/tmds_data_n]

assign_bd_address -offset 0x41200000 -range 64K [get_bd_addr_segs {gpio_cfg/S_AXI/Reg}]
assign_bd_address -offset 0x41210000 -range 64K [get_bd_addr_segs {gpio_st/S_AXI/Reg}]
assign_bd_address
validate_bd_design
save_bd_design

set bd_file [get_files system.bd]
make_wrapper -files $bd_file -top
add_files -norecurse [file join $proj_dir hdmi_video_bd.gen sources_1 bd system hdl system_wrapper.v]
set_property top system_wrapper [current_fileset]
update_compile_order -fileset sources_1
generate_target all [get_files system.bd]

launch_runs impl_1 -to_step write_bitstream -jobs 8
wait_on_run impl_1
set st [get_property STATUS [get_runs impl_1]]
if {[string first "write_bitstream Complete" $st] < 0} { puts "HDMI_VIDEO_BD_FAIL $st"; return }

set hwh [file join $proj_dir hdmi_video_bd.gen sources_1 bd system hw_handoff system.hwh]
if {![file exists $hwh]} { puts "HDMI_VIDEO_BD_FAIL no_hwh"; return }
file copy -force $hwh [file join $overlay hdmi_video.hwh]

open_run impl_1
report_timing_summary -file [file join $rep_timing hdmi_video_bd_timing.rpt]
report_utilization   -file [file join $rep_util hdmi_video_bd_util.rpt]

set bit [file join $proj_dir hdmi_video_bd.runs impl_1 system_wrapper.bit]
if {![file exists $bit]} {
    set cand [glob -nocomplain [file join $proj_dir hdmi_video_bd.runs impl_1 *.bit]]
    if {[llength $cand] > 0} { set bit [lindex $cand 0] }
}
file copy -force $bit [file join $overlay hdmi_video.bit]
puts "HDMI_VIDEO_BD_OK BIT=[file join $overlay hdmi_video.bit]"
