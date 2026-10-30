# bd_lcd_probe.tcl —— LCD 双分辨率探针（800x480@33.333MHz + 480x272@9MHz 同一 bit）
#
# 产物：board/overlay/lcd_probe.bit + .hwh
# 地址：
#   0x40000000  BRAM 800x480 4bpp 帧缓存（192K x 8=48K x 32，用 48,000 字 + 16 调色板字；AXI 段给 256K）
#   0x40080000  BRAM 480x272 4bpp 帧缓存（16K x 32，用 16,320 字 + 16 调色板字）
#               —— 挪到 0x40080000：assign_bd_address 的 range 必须是 2 的幂，
#                  800 那块要 192 KB 只能开 256K，正好占满 0x40000000..0x4003FFFF
#   0x41260000  按键 key[1:0]（PL KEY0=L14 / KEY1=K16，赛前 UI 输入）
#   0x41200000  GPIO 输入 ch1=id_level ch2=id_toggle
#   0x41210000  GPIO 输入 ch1=dbg_timing ch2=dbg_tick
#   0x41220000  GPIO 输出 cfg
set script_dir [file normalize [file dirname [info script]]]
set root       [file normalize [file join $script_dir .. ..]]
set part_name  "xc7z020clg400-2"
set proj_dir   [file join $root build vivado lcd_probe_bd]
set overlay    [file join $root board overlay]
set rtl_dir    [file join $root src rtl io lcd]
set rep_timing [file join $root build reports timing]
set rep_util   [file join $root build reports resource]
set xdc        [file join $script_dir lcd_probe.xdc]

foreach d [list $overlay $rep_timing $rep_util] { file mkdir $d }
create_project -force lcd_probe_bd $proj_dir -part $part_name
add_files -norecurse [list \
    [file join $rtl_dir lcd_probe_core.v] \
    [file join $rtl_dir lcd_probe_top.v] \
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

# 50 MHz -> VCO 900 MHz -> /27 = 33.333 MHz, /100 = 9.000 MHz
set clk_wiz [create_bd_cell -type ip -vlnv xilinx.com:ip:clk_wiz:6.0 clk_wiz]
set_property -dict [list \
    CONFIG.PRIM_IN_FREQ {50.000} \
    CONFIG.NUM_OUT_CLKS {2} \
    CONFIG.CLKOUT1_REQUESTED_OUT_FREQ {33.333} \
    CONFIG.CLKOUT2_USED {true} \
    CONFIG.CLKOUT2_REQUESTED_OUT_FREQ {9.000} \
    CONFIG.USE_LOCKED {true} \
    CONFIG.USE_RESET {true} \
    CONFIG.RESET_PORT {reset} \
    CONFIG.RESET_TYPE {ACTIVE_HIGH} \
] $clk_wiz
connect_bd_net [get_bd_ports sys_clk] [get_bd_pins clk_wiz/clk_in1]
set zero [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 zero]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {0}] $zero
connect_bd_net [get_bd_pins zero/dout] [get_bd_pins clk_wiz/reset]
set pclk33 [get_bd_pins clk_wiz/clk_out1]
set pclk9  [get_bd_pins clk_wiz/clk_out2]

# 每个像素时钟一个复位域；dcm_locked 必须显式接 locked
set rst_pix33 [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_pix33]
connect_bd_net $pclk33 [get_bd_pins rst_pix33/slowest_sync_clk]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_RESET0_N] [get_bd_pins rst_pix33/ext_reset_in]
connect_bd_net [get_bd_pins clk_wiz/locked] [get_bd_pins rst_pix33/dcm_locked]

set rst_pix9 [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_pix9]
connect_bd_net $pclk9 [get_bd_pins rst_pix9/slowest_sync_clk]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_RESET0_N] [get_bd_pins rst_pix9/ext_reset_in]
connect_bd_net [get_bd_pins clk_wiz/locked] [get_bd_pins rst_pix9/dcm_locked]

set axi_smc [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 axi_smc]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {9}] $axi_smc
connect_bd_intf_net [get_bd_intf_pins processing_system7_0/M_AXI_GP0] [get_bd_intf_pins axi_smc/S00_AXI]

# ---- 两块帧缓存，各自独立时钟，避免跨域读 ----
# 注意：Use_Byte_Write_Enable 显式钉成 true。这个模式下 addra/addrb 都是**字节地址**
# （IP 内部右移 2 才取字），axi_bram_ctrl 的 bram_addr_a[15:0] 也是字节地址；
# 于是 PL 读侧必须送 {字地址, 2'b00}，否则整屏会读成 mem[addr>>2]——2026-10-29 上板
# 就是踩了这个坑（PS 回环走端口 A、读写一起移位，回环测试永远自洽，抓不到）。
set bram_ctrl_800 [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_bram_ctrl:4.1 bram_ctrl_800]
set_property -dict [list CONFIG.DATA_WIDTH {32} CONFIG.SINGLE_PORT_BRAM {1} \
    CONFIG.ECC_TYPE {0} CONFIG.PROTOCOL {AXI4LITE}] $bram_ctrl_800
connect_bd_intf_net [get_bd_intf_pins axi_smc/M00_AXI] [get_bd_intf_pins bram_ctrl_800/S_AXI]

set bram_800 [create_bd_cell -type ip -vlnv xilinx.com:ip:blk_mem_gen:8.4 bram_800]
# 49152 字 = 192 KB：4bpp 800x480 要 48,000 图像字 + 16 调色板字。
# 字节写使能模式下 addrb 是字节地址，宽度 = ceil(log2(49152))+2 = 18 位（对应端口 0x40000000，AXI range 256K）
set_property -dict [list \
    CONFIG.Memory_Type {True_Dual_Port_RAM} \
    CONFIG.Write_Width_A {32} CONFIG.Read_Width_A {32} CONFIG.Write_Depth_A {49152} \
    CONFIG.Operating_Mode_A {READ_FIRST} CONFIG.Enable_A {Use_ENA_Pin} \
    CONFIG.Use_Byte_Write_Enable {true} \
    CONFIG.Write_Width_B {32} CONFIG.Read_Width_B {32} \
    CONFIG.Operating_Mode_B {READ_FIRST} CONFIG.Enable_B {Use_ENB_Pin} \
    CONFIG.Register_PortB_Output_of_Memory_Primitives {false} \
] $bram_800
connect_bd_intf_net [get_bd_intf_pins bram_ctrl_800/BRAM_PORTA] [get_bd_intf_pins bram_800/BRAM_PORTA]

set bram_ctrl_480 [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_bram_ctrl:4.1 bram_ctrl_480]
set_property -dict [list CONFIG.DATA_WIDTH {32} CONFIG.SINGLE_PORT_BRAM {1} \
    CONFIG.ECC_TYPE {0} CONFIG.PROTOCOL {AXI4LITE}] $bram_ctrl_480
connect_bd_intf_net [get_bd_intf_pins axi_smc/M01_AXI] [get_bd_intf_pins bram_ctrl_480/S_AXI]

set bram_480 [create_bd_cell -type ip -vlnv xilinx.com:ip:blk_mem_gen:8.4 bram_480]
set_property -dict [list \
    CONFIG.Memory_Type {True_Dual_Port_RAM} \
    CONFIG.Write_Width_A {32} CONFIG.Read_Width_A {32} CONFIG.Write_Depth_A {16384} \
    CONFIG.Operating_Mode_A {READ_FIRST} CONFIG.Enable_A {Use_ENA_Pin} \
    CONFIG.Use_Byte_Write_Enable {true} \
    CONFIG.Write_Width_B {32} CONFIG.Read_Width_B {32} \
    CONFIG.Operating_Mode_B {READ_FIRST} CONFIG.Enable_B {Use_ENB_Pin} \
    CONFIG.Register_PortB_Output_of_Memory_Primitives {false} \
] $bram_480
connect_bd_intf_net [get_bd_intf_pins bram_ctrl_480/BRAM_PORTA] [get_bd_intf_pins bram_480/BRAM_PORTA]

# ---- 调试/控制 GPIO ----
set gpio_id [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 gpio_id]
set_property -dict [list CONFIG.C_IS_DUAL {1} CONFIG.C_GPIO_WIDTH {32} CONFIG.C_ALL_INPUTS {1} \
    CONFIG.C_GPIO2_WIDTH {32} CONFIG.C_ALL_INPUTS_2 {1}] $gpio_id
connect_bd_intf_net [get_bd_intf_pins axi_smc/M02_AXI] [get_bd_intf_pins gpio_id/S_AXI]

set gpio_t [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 gpio_t]
set_property -dict [list CONFIG.C_IS_DUAL {1} CONFIG.C_GPIO_WIDTH {32} CONFIG.C_ALL_INPUTS {1} \
    CONFIG.C_GPIO2_WIDTH {32} CONFIG.C_ALL_INPUTS_2 {1}] $gpio_t
connect_bd_intf_net [get_bd_intf_pins axi_smc/M03_AXI] [get_bd_intf_pins gpio_t/S_AXI]

set gpio_cfg [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 gpio_cfg]
set_property -dict [list CONFIG.C_GPIO_WIDTH {32} CONFIG.C_ALL_OUTPUTS {1}] $gpio_cfg
connect_bd_intf_net [get_bd_intf_pins axi_smc/M04_AXI] [get_bd_intf_pins gpio_cfg/S_AXI]

foreach g {gpio_id gpio_t gpio_cfg} {
    connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins $g/s_axi_aclk]
    connect_bd_net [get_bd_pins rst_ps/peripheral_aresetn] [get_bd_pins $g/s_axi_aresetn]
}
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins axi_smc/aclk]
connect_bd_net [get_bd_pins rst_ps/peripheral_aresetn] [get_bd_pins axi_smc/aresetn]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins bram_ctrl_800/s_axi_aclk]
connect_bd_net [get_bd_pins rst_ps/peripheral_aresetn] [get_bd_pins bram_ctrl_800/s_axi_aresetn]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins bram_ctrl_480/s_axi_aclk]
connect_bd_net [get_bd_pins rst_ps/peripheral_aresetn] [get_bd_pins bram_ctrl_480/s_axi_aresetn]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins processing_system7_0/M_AXI_GP0_ACLK]

set lcd [create_bd_cell -type module -reference lcd_probe_top lcd_probe_0]
connect_bd_net $pclk33 [get_bd_pins lcd_probe_0/pclk33]
connect_bd_net $pclk9  [get_bd_pins lcd_probe_0/pclk9]
connect_bd_net [get_bd_pins rst_pix33/peripheral_aresetn] [get_bd_pins lcd_probe_0/reset_n33]
connect_bd_net [get_bd_pins rst_pix9/peripheral_aresetn]  [get_bd_pins lcd_probe_0/reset_n9]
connect_bd_net [get_bd_pins gpio_cfg/gpio_io_o] [get_bd_pins lcd_probe_0/cfg]

connect_bd_net [get_bd_pins lcd_probe_0/bram_addr_800] [get_bd_pins bram_800/addrb]
connect_bd_net [get_bd_pins bram_800/doutb] [get_bd_pins lcd_probe_0/bram_dout_800]
connect_bd_net [get_bd_pins lcd_probe_0/bram_addr_480] [get_bd_pins bram_480/addrb]
connect_bd_net [get_bd_pins bram_480/doutb] [get_bd_pins lcd_probe_0/bram_dout_480]

connect_bd_net $pclk33 [get_bd_pins bram_800/clkb]
connect_bd_net $pclk9  [get_bd_pins bram_480/clkb]

# BRAM port B：rstb 高有效，不能接 peripheral_aresetn（低有效，平时恒 1=一直复位）
set one [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 one]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {1}] $one
foreach b {bram_800 bram_480} {
    connect_bd_net [get_bd_pins zero/dout] [get_bd_pins $b/rstb]
    connect_bd_net [get_bd_pins one/dout]  [get_bd_pins $b/enb]
}

connect_bd_net [get_bd_pins lcd_probe_0/dbg_id_level]  [get_bd_pins gpio_id/gpio_io_i]
connect_bd_net [get_bd_pins lcd_probe_0/dbg_id_toggle] [get_bd_pins gpio_id/gpio2_io_i]
connect_bd_net [get_bd_pins lcd_probe_0/dbg_timing]    [get_bd_pins gpio_t/gpio_io_i]
connect_bd_net [get_bd_pins lcd_probe_0/dbg_tick]      [get_bd_pins gpio_t/gpio2_io_i]

# ---- 逐行自证抓取（0x4123/0x4124/0x4125） ----
# 读侧到底送出了什么，以前只能靠拍照反推；这里把「面板第 cfg[15:6] 行实际
# 要显示的 32 位」直接抓成寄存器，PS 读数字即可，不再依赖人眼/照片。
set gpio_d0 [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 gpio_d0]
set_property -dict [list CONFIG.C_IS_DUAL {1} CONFIG.C_GPIO_WIDTH {32} CONFIG.C_ALL_INPUTS {1} \
    CONFIG.C_GPIO2_WIDTH {32} CONFIG.C_ALL_INPUTS_2 {1}] $gpio_d0
connect_bd_intf_net [get_bd_intf_pins axi_smc/M05_AXI] [get_bd_intf_pins gpio_d0/S_AXI]

set gpio_d1 [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 gpio_d1]
set_property -dict [list CONFIG.C_IS_DUAL {1} CONFIG.C_GPIO_WIDTH {32} CONFIG.C_ALL_INPUTS {1} \
    CONFIG.C_GPIO2_WIDTH {32} CONFIG.C_ALL_INPUTS_2 {1}] $gpio_d1
connect_bd_intf_net [get_bd_intf_pins axi_smc/M06_AXI] [get_bd_intf_pins gpio_d1/S_AXI]

set gpio_d2 [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 gpio_d2]
set_property -dict [list CONFIG.C_GPIO_WIDTH {32} CONFIG.C_ALL_INPUTS {1}] $gpio_d2
connect_bd_intf_net [get_bd_intf_pins axi_smc/M07_AXI] [get_bd_intf_pins gpio_d2/S_AXI]

# ---- 按键（0x41260000）：赛前 UI 的输入 ----
# PL 只有两颗键：key[0]=L14、key[1]=K16（见 board_pinout_navigator_zynq_io_v3.7.xdc
# 的 PL_KEY 段）。UI 约定：key[0] = 下一项，key[1] = 换选项。
set gpio_keys [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 gpio_keys]
set_property -dict [list CONFIG.C_GPIO_WIDTH {2} CONFIG.C_ALL_INPUTS {1}] $gpio_keys
connect_bd_intf_net [get_bd_intf_pins axi_smc/M08_AXI] [get_bd_intf_pins gpio_keys/S_AXI]

foreach g {gpio_d0 gpio_d1 gpio_d2 gpio_keys} {
    connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins $g/s_axi_aclk]
    connect_bd_net [get_bd_pins rst_ps/peripheral_aresetn] [get_bd_pins $g/s_axi_aresetn]
}
connect_bd_net [get_bd_pins lcd_probe_0/dbg_lb0]   [get_bd_pins gpio_d0/gpio_io_i]
connect_bd_net [get_bd_pins lcd_probe_0/dbg_lbmid] [get_bd_pins gpio_d0/gpio2_io_i]
connect_bd_net [get_bd_pins lcd_probe_0/dbg_st0]   [get_bd_pins gpio_d1/gpio_io_i]
connect_bd_net [get_bd_pins lcd_probe_0/dbg_stmid] [get_bd_pins gpio_d1/gpio2_io_i]
connect_bd_net [get_bd_pins lcd_probe_0/dbg_cnt]   [get_bd_pins gpio_d2/gpio_io_i]

create_bd_port -dir O -type data lcd_de
create_bd_port -dir O -type data lcd_hs
create_bd_port -dir O -type data lcd_vs
create_bd_port -dir O -type data lcd_bl
create_bd_port -dir O -type data lcd_clk
create_bd_port -dir O -type data lcd_rst
create_bd_port -dir IO -from 23 -to 0 -type data lcd_rgb
foreach p {lcd_de lcd_hs lcd_vs lcd_bl lcd_clk lcd_rst lcd_rgb} {
    connect_bd_net [get_bd_ports $p] [get_bd_pins lcd_probe_0/$p]
}

create_bd_port -dir I -from 1 -to 0 -type data key
connect_bd_net [get_bd_ports key] [get_bd_pins gpio_keys/gpio_io_i]

# 800 的帧缓存现在是 192 KB（4bpp + 调色板），480 那块只能往后挪，否则地址区重叠
assign_bd_address -offset 0x40000000 -range 256K [get_bd_addr_segs {bram_ctrl_800/S_AXI/Mem0}]
assign_bd_address -offset 0x40080000 -range 64K [get_bd_addr_segs {bram_ctrl_480/S_AXI/Mem0}]
assign_bd_address -offset 0x41200000 -range 64K [get_bd_addr_segs {gpio_id/S_AXI/Reg}]
assign_bd_address -offset 0x41210000 -range 64K [get_bd_addr_segs {gpio_t/S_AXI/Reg}]
assign_bd_address -offset 0x41220000 -range 64K [get_bd_addr_segs {gpio_cfg/S_AXI/Reg}]
assign_bd_address -offset 0x41230000 -range 64K [get_bd_addr_segs {gpio_d0/S_AXI/Reg}]
assign_bd_address -offset 0x41240000 -range 64K [get_bd_addr_segs {gpio_d1/S_AXI/Reg}]
assign_bd_address -offset 0x41250000 -range 64K [get_bd_addr_segs {gpio_d2/S_AXI/Reg}]
assign_bd_address -offset 0x41260000 -range 64K [get_bd_addr_segs {gpio_keys/S_AXI/Reg}]
validate_bd_design
save_bd_design

foreach p {MMCM_CLKFBOUT_MULT_F MMCM_DIVCLK_DIVIDE MMCM_CLKOUT0_DIVIDE_F MMCM_CLKOUT1_DIVIDE} {
    if {[catch {get_property CONFIG.$p [get_bd_cells clk_wiz]} v]} { set v "?" }
    puts "LCD_PROBE_CLK $p = $v"
}

set bd_file [get_files system.bd]
make_wrapper -files $bd_file -top
add_files -norecurse [file join $proj_dir lcd_probe_bd.gen sources_1 bd system hdl system_wrapper.v]
set_property top system_wrapper [current_fileset]
update_compile_order -fileset sources_1
generate_target all [get_files system.bd]

# -jobs 2：本机 14 GB 内存，8 路并行会在读时序库时 Out of memory（2026-10-29 实测），
# 不是设计错误 —— 看到 'Failed run(s)' 先看是不是 OOM 再用更小的 -jobs 重跑
launch_runs impl_1 -to_step write_bitstream -jobs 2
wait_on_run impl_1
set st [get_property STATUS [get_runs impl_1]]
if {[string first "write_bitstream Complete" $st] < 0} { puts "LCD_PROBE_BD_FAIL $st"; return }

set hwh [file join $proj_dir lcd_probe_bd.gen sources_1 bd system hw_handoff system.hwh]
if {![file exists $hwh]} { puts "LCD_PROBE_BD_FAIL no_hwh"; return }
file copy -force $hwh [file join $overlay lcd_probe.hwh]

open_run impl_1
report_timing_summary -file [file join $rep_timing lcd_probe_bd_timing.rpt]
report_utilization   -file [file join $rep_util lcd_probe_bd_util.rpt]

set bit [file join $proj_dir lcd_probe_bd.runs impl_1 system_wrapper.bit]
if {![file exists $bit]} {
    set cand [glob -nocomplain [file join $proj_dir lcd_probe_bd.runs impl_1 *.bit]]
    if {[llength $cand] > 0} { set bit [lindex $cand 0] }
}
file copy -force $bit [file join $overlay lcd_probe.bit]
puts "LCD_PROBE_BD_OK BIT=[file join $overlay lcd_probe.bit]"
