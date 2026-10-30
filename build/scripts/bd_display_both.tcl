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
set proj_dir   [expr {[info exists ::env(DB_PROJ)] ? $::env(DB_PROJ) : [file join $root build vivado display_both_bd]}]
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
    [file join $rtl_lcd lcd_timing.v] \
    [file join $rtl_lcd lcd_fb_reader.v] \
    [file join $rtl_lcd lcd_status_top.v] \
]
add_files -fileset constrs_1 -norecurse $xdc
update_compile_order -fileset sources_1

## ⚠️ Vivado 2020.2 的 ipintegrator 初始化按 **cwd** source 自己的 tcl 库
##    （init.tcl → utils.tcl / clkrst.tcl / board_utils.tcl …）。本机实测 create_bd_design
##    会**间歇性**报 `couldn't read file "clkrst.tcl": no such file or directory` /
##    `invalid argument` / `Failed to load feature 'ipintegrator'`，而文件其实存在且可读
##    （Python 逐文件读 15/15 成功、手动 source 12/12 RC=0）。
##    对策：**在 create_bd_design 之前把这些库按绝对路径预加载**，proc 已在全局命名空间里，
##    Vivado 内部那次 source 就不再是唯一依赖。连续 3 次实测 BD_OK（曾用 cd 法只 2/2 后失效）。
set _ipdir "D:/Xilinx/Vivado/2020.2/scripts/ipintegrator"
foreach _f {utils.tcl mig_utils.tcl board_utils.tcl utils_dbg.tcl clkrst.tcl replace_bd_cell.tcl \
            addr.tcl testbench.tcl sc_cosim_util.tcl utils_timing.tcl sdsoc_pfm.tcl gt_utils.tcl} {
    if {[catch {source -notrace [file join $_ipdir $_f]} _e]} { puts "IP_PRELOAD_FAIL $_f $_e" }
}
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

## ---- 像素时钟：clk_wiz 出 HDMI 75/375 MHz + 一路 50 MHz 供给 LCD 的第二个 MMCM ----
## 关键约束：**同一个时钟引脚只能插一个输入缓冲**（oi 项目踩过：两个 clk_wiz 各插一个
## IBUF 会综合通过、实现报 Place 30-602）。所以 sys_clk 只进 clk_wiz #1，
## clk_wiz_lcd 的输入取 #1 的一个 50 MHz 输出（内部网络，不再插 IBUF）。
## 50 MHz → VCO 750 MHz（M=15, D=1）→ ÷10=75（HDMI 像素）/ ÷2=375（TMDS 5x）/ ÷15=50（喂 LCD MMCM）
set clk_wiz [create_bd_cell -type ip -vlnv xilinx.com:ip:clk_wiz:6.0 clk_wiz]
set_property -dict [list \
    CONFIG.PRIM_IN_FREQ {50.000} \
    CONFIG.CLKOUT1_REQUESTED_OUT_FREQ {75.000} \
    CONFIG.CLKOUT2_USED {true} \
    CONFIG.CLKOUT2_REQUESTED_OUT_FREQ {375.000} \
    CONFIG.CLKOUT3_USED {true} \
    CONFIG.CLKOUT3_REQUESTED_OUT_FREQ {50.000} \
    CONFIG.USE_LOCKED {true} \
    CONFIG.USE_RESET {true} \
    CONFIG.RESET_PORT {reset} \
    CONFIG.RESET_TYPE {ACTIVE_HIGH} \
] $clk_wiz
connect_bd_net [get_bd_ports sys_clk] [get_bd_pins clk_wiz/clk_in1]
connect_bd_net [get_bd_pins rst_ps/peripheral_reset] [get_bd_pins clk_wiz/reset]

## ---- LCD 像素时钟：30.000 MHz（按 ATK-MD0430R-800480 手册 typ）----
## 依据：ATK-MD0430R 手册 800×480 档：DCLK typ 30 MHz、Th 928
## （800 + 40 hbp + 88 hfp）、Tv 525，30e6/(928×525)=61.6 Hz。
## 当前实物 ID 读回仍为 000（480×272），本档按用户要求配置为 800×480，
## 待接 800×480 屏后做最终目视；480×272 档参数保留在 git 历史/会话 020。
## 25 MHz 与 75/375 MHz 无公共 VCO 能达到 30.000，所以 LCD 单独一个 MMCM：
## 50 MHz → VCO 900（M=18, D=1）→ ÷30 = 30 MHz。
set clk_wiz_lcd [create_bd_cell -type ip -vlnv xilinx.com:ip:clk_wiz:6.0 clk_wiz_lcd]
set_property -dict [list \
    CONFIG.PRIM_IN_FREQ {50.000} \
    CONFIG.CLKOUT1_REQUESTED_OUT_FREQ {30.000} \
    CONFIG.USE_LOCKED {true} \
    CONFIG.USE_RESET {true} \
    CONFIG.RESET_PORT {reset} \
    CONFIG.RESET_TYPE {ACTIVE_HIGH} \
] $clk_wiz_lcd
connect_bd_net [get_bd_pins clk_wiz/clk_out3] [get_bd_pins clk_wiz_lcd/clk_in1]
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
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {4}] $axi_smc
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
create_bd_port -dir IO -from 23 -to 0 -type data lcd_rgb
connect_bd_net [get_bd_ports lcd_rgb] [get_bd_pins lcd_status_0/lcd_rgb]

## ---- debug: AXI GPIO reads back pixel_xpos / {de, pixel_ypos} from hdmi_status_0 ----
set dbg_gpio [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 dbg_gpio]
set_property -dict [list CONFIG.C_IS_DUAL {1} CONFIG.C_ALL_INPUTS {1} CONFIG.C_ALL_INPUTS_2 {1} CONFIG.C_GPIO_WIDTH {32} CONFIG.C_GPIO2_WIDTH {32}] $dbg_gpio
connect_bd_intf_net [get_bd_intf_pins dbg_gpio/S_AXI] [get_bd_intf_pins axi_smc/M02_AXI]
connect_bd_net [get_bd_pins dbg_gpio/s_axi_aclk] [get_bd_pins processing_system7_0/FCLK_CLK0]
connect_bd_net [get_bd_pins dbg_gpio/s_axi_aresetn] [get_bd_pins rst_ps/peripheral_aresetn]
connect_bd_net [get_bd_pins hdmi_status_0/dbg_x] [get_bd_pins dbg_gpio/gpio_io_i]
connect_bd_net [get_bd_pins hdmi_status_0/dbg_y] [get_bd_pins dbg_gpio/gpio2_io_i]

## ---- debug: 同一个套路给 LCD（dbg1={de,pixel_data} / dbg2={v_cnt,h_cnt}）----
## 屏上几何可疑时，读这四个数就能分开「时钟/复位死了」「DE 窗口错」「读侧无数据」，
## 不必再靠拍照猜。lcd_status_top 早就有这两个口，此前只接了 HDMI 那一路。
set dbg_gpio_lcd [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 dbg_gpio_lcd]
set_property -dict [list CONFIG.C_IS_DUAL {1} CONFIG.C_ALL_INPUTS {1} CONFIG.C_ALL_INPUTS_2 {1} CONFIG.C_GPIO_WIDTH {32} CONFIG.C_GPIO2_WIDTH {32}] $dbg_gpio_lcd
connect_bd_intf_net [get_bd_intf_pins dbg_gpio_lcd/S_AXI] [get_bd_intf_pins axi_smc/M03_AXI]
connect_bd_net [get_bd_pins dbg_gpio_lcd/s_axi_aclk] [get_bd_pins processing_system7_0/FCLK_CLK0]
connect_bd_net [get_bd_pins dbg_gpio_lcd/s_axi_aresetn] [get_bd_pins rst_ps/peripheral_aresetn]
connect_bd_net [get_bd_pins lcd_status_0/dbg1] [get_bd_pins dbg_gpio_lcd/gpio_io_i]
connect_bd_net [get_bd_pins lcd_status_0/dbg2] [get_bd_pins dbg_gpio_lcd/gpio2_io_i]

assign_bd_address -offset 0x40000000 -range 128K [get_bd_addr_segs {hdmi_ctrl/S_AXI/Mem0}]
assign_bd_address -offset 0x40020000 -range 64K  [get_bd_addr_segs {lcd_ctrl/S_AXI/Mem0}]
assign_bd_address -offset 0x41200000 -range 64K  [get_bd_addr_segs {dbg_gpio/S_AXI/Reg}]
assign_bd_address -offset 0x41210000 -range 64K  [get_bd_addr_segs {dbg_gpio_lcd/S_AXI/Reg}]
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