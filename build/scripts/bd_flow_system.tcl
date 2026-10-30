# bd_flow_system.tcl —— 全流程固化 overlay（2026-10-30）
#
# 为什么要有它
# ------------
# 在此之前，「整条链路」散在三个 bitstream 里：
#   lcd_probe.bit   LCD 4bpp 帧缓存 + 按键 + 逐行自证抓取   （面板 / 赛前 UI）
#   hdmi_video.bit  HDMI 32bpp DDR 扫描输出（HP0）          （带框视频环出）
# 换功能就要 `Overlay()` 重新下配置、PS 侧的 GPIO 句柄全部重建，赛场上「先看面板、
# 再看大屏」还得切两次片。这个 overlay 把两者放进同一个 PL 配置，一次加载之后
# **面板、按键、HDMI 大屏同时可用**，寄存器地址也和既有工具完全一致。
#
# 地址表（D006 契约的合并版；改动须立新决策）
#   0x40000000  256K  LCD 800x480 4bpp 帧缓存（48,000 图像字 + 16 调色板字）
#   0x40080000   64K  LCD 480x272 4bpp 帧缓存
#   0x41200000   64K  HDMI cfg   ch1=帧缓存物理地址 ch2={en[27],width,stride}
#   0x41210000   64K  HDMI status {frames[31:16], under[15:0]}
#   0x41220000   64K  LCD cfg    [4]=帧缓存 [5]=480x272 [1]=背光关 [3:2]=图案
#   0x41230000   64K  LCD dbg0   lb0 / lbmid
#   0x41240000   64K  LCD dbg1   st0 / stmid
#   0x41250000   64K  LCD dbg2   cnt
#   0x41260000   64K  keys      key[1:0]
#   0x41270000   64K  LCD id     ← 原 0x41200000，被 HDMI cfg 占用后下移
#   0x41280000   64K  LCD timing ← 原 0x41210000，同上
#
# 时钟
#   sys_clk(50 MHz, U18) → clk_wiz      → 33.333 / 9.000 MHz    （LCD，两个像素域）
#   FCLK_CLK0(83.333)    → clk_wiz_hdmi → 74.255954 / 371.279770（HDMI，像素 + TMDS 5x）
#   两个 clk_wiz 的输入来自不同源，不违反「同一引脚只能插一个 IBUF」。
#
# 为什么要 axi_clock_converter
#   HDMI 的 GPIO 必须挂在 pclk（74.25）域 —— 上一版把它挂在 83.33 MHz 时，
#   GPIO→fb_scanout 的跨域路径被当成同步路径收敛，实测 WNS -4.573 ns / 60 个失败端点。
#   合并后 M_AXI_GP0 只能有一个从设备分支，所以先在 FCLK 域做 smartconnect，
#   再由 M09 走 axi_clock_converter 进 pclk 域。
#
# 产物：board/overlay/flow_system.bit + .hwh
# 判据：末行 FLOW_SYSTEM_BD_OK
set script_dir [file normalize [file dirname [info script]]]
set root       [file normalize [file join $script_dir .. ..]]
set part_name  "xc7z020clg400-2"
set proj_dir   [expr {[info exists ::env(DB_PROJ)] ? $::env(DB_PROJ) : [file join $root build vivado flow_system_bd]}]
set overlay    [file join $root board overlay]
set rtl_lcd    [file join $root src rtl io lcd]
set rtl_hdmi   [file join $root src rtl io hdmi]
set rep_timing [file join $root build reports timing]
set rep_util   [file join $root build reports resource]
set xdc        [file join $script_dir flow_system.xdc]

foreach d [list $overlay $rep_timing $rep_util] { file mkdir $d }
create_project -force flow_system_bd $proj_dir -part $part_name
add_files -norecurse [list \
    [file join $rtl_lcd  lcd_probe_core.v] \
    [file join $rtl_lcd  lcd_probe_top.v] \
    [file join $rtl_hdmi asyn_rst_syn.v] \
    [file join $rtl_hdmi dvi_encoder.v] \
    [file join $rtl_hdmi dvi_transmitter_top.v] \
    [file join $rtl_hdmi serializer_10_to_1.v] \
    [file join $rtl_hdmi video_driver.v] \
    [file join $rtl_hdmi fb_scanout.v] \
    [file join $rtl_hdmi hdmi_video_top.v] \
]
add_files -fileset constrs_1 -norecurse $xdc
update_compile_order -fileset sources_1

## ⚠️ 与 bd_lcd_probe/bd_hdmi_video 相同：2020.2 的 ipintegrator 按 cwd source 自己的
##    tcl 库，本机实测会间歇性读不到。`bd_flow_system` 首次出片就是在
##    `couldn't read file "init.tcl": No error` 上挂掉的（init.tcl 自己又 source 了
##    utils/clkrst/... 一串相对路径）。所以这里比旧脚本多做两步：
##      1) 按绝对路径预加载（含 init.tcl 本身）——proc 已在全局命名空间；
##      2) create_bd_design 期间把 cwd 切到该目录，让任何残留的相对 source 都能落地。
set _ipdir "D:/Xilinx/Vivado/2020.2/scripts/ipintegrator"
foreach _f {init.tcl utils.tcl mig_utils.tcl board_utils.tcl utils_dbg.tcl clkrst.tcl \
            replace_bd_cell.tcl addr.tcl testbench.tcl sc_cosim_util.tcl utils_timing.tcl \
            sdsoc_pfm.tcl gt_utils.tcl} {
    if {[catch {source -notrace [file join $_ipdir $_f]} _e]} { puts "IP_PRELOAD_FAIL $_f $_e" }
}
puts "IP_PRELOAD_OK"
set _cwd [pwd]
if {[catch {cd $_ipdir} _e]} { puts "IP_CD_FAIL $_e" }
if {[catch {create_bd_design "system"} _e]} {
    puts "BD_CREATE_RETRY $_e"
    after 2000
    create_bd_design "system"
}
if {[catch {cd $_cwd} _e]} { puts "CWD_RESTORE_FAIL $_e" }
puts "BD_CREATED cwd=[pwd]"
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

set zero [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 zero]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {0}] $zero

# ---------------- 时钟 #1：LCD（外部 50 MHz） ----------------
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
connect_bd_net [get_bd_pins zero/dout] [get_bd_pins clk_wiz/reset]
set pclk33 [get_bd_pins clk_wiz/clk_out1]
set pclk9  [get_bd_pins clk_wiz/clk_out2]

# ---------------- 时钟 #2：HDMI（PS FCLK_CLK0 83.333 MHz） ----------------
set clk_wiz_hdmi [create_bd_cell -type ip -vlnv xilinx.com:ip:clk_wiz:6.0 clk_wiz_hdmi]
set_property -dict [list \
    CONFIG.PRIM_IN_FREQ {83.333336} \
    CONFIG.CLKOUT1_REQUESTED_OUT_FREQ {74.25} \
    CONFIG.CLKOUT2_USED {true} \
    CONFIG.CLKOUT2_REQUESTED_OUT_FREQ {371.25} \
    CONFIG.USE_LOCKED {true} \
    CONFIG.USE_RESET {true} \
    CONFIG.RESET_PORT {reset} \
    CONFIG.RESET_TYPE {ACTIVE_HIGH} \
] $clk_wiz_hdmi
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins clk_wiz_hdmi/clk_in1]
connect_bd_net [get_bd_pins rst_ps/peripheral_reset] [get_bd_pins clk_wiz_hdmi/reset]
set pclk_h  [get_bd_pins clk_wiz_hdmi/clk_out1]
set pclk_h5 [get_bd_pins clk_wiz_hdmi/clk_out2]

# ---------------- 复位域 ----------------
set rst_pix33 [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_pix33]
connect_bd_net $pclk33 [get_bd_pins rst_pix33/slowest_sync_clk]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_RESET0_N] [get_bd_pins rst_pix33/ext_reset_in]
connect_bd_net [get_bd_pins clk_wiz/locked] [get_bd_pins rst_pix33/dcm_locked]

set rst_pix9 [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_pix9]
connect_bd_net $pclk9 [get_bd_pins rst_pix9/slowest_sync_clk]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_RESET0_N] [get_bd_pins rst_pix9/ext_reset_in]
connect_bd_net [get_bd_pins clk_wiz/locked] [get_bd_pins rst_pix9/dcm_locked]

set rst_pix_h [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_pix_h]
connect_bd_net $pclk_h [get_bd_pins rst_pix_h/slowest_sync_clk]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_RESET0_N] [get_bd_pins rst_pix_h/ext_reset_in]
connect_bd_net [get_bd_pins clk_wiz_hdmi/locked] [get_bd_pins rst_pix_h/dcm_locked]

# ---------------- AXI（FCLK 域）：9 个 LCD 从设备 + 1 个通往 pclk 域的桥 ----------------
set axi_smc [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 axi_smc]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {10}] $axi_smc
connect_bd_intf_net [get_bd_intf_pins processing_system7_0/M_AXI_GP0] [get_bd_intf_pins axi_smc/S00_AXI]

set axi_cc [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_clock_converter:2.1 axi_cc]
connect_bd_intf_net [get_bd_intf_pins axi_smc/M09_AXI] [get_bd_intf_pins axi_cc/S_AXI]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins axi_cc/s_axi_aclk]
connect_bd_net [get_bd_pins rst_ps/peripheral_aresetn]   [get_bd_pins axi_cc/s_axi_aresetn]
connect_bd_net $pclk_h [get_bd_pins axi_cc/m_axi_aclk]
connect_bd_net [get_bd_pins rst_pix_h/peripheral_aresetn] [get_bd_pins axi_cc/m_axi_aresetn]

set hdmi_smc [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 hdmi_smc]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {2}] $hdmi_smc
set_property -dict [list CONFIG.ADVANCED_PROPERTIES {__experimental_features__ {disable_low_area_mode 1}}] $hdmi_smc
connect_bd_intf_net [get_bd_intf_pins axi_cc/M_AXI] [get_bd_intf_pins hdmi_smc/S00_AXI]
connect_bd_net $pclk_h [get_bd_pins hdmi_smc/aclk]
connect_bd_net [get_bd_pins rst_pix_h/peripheral_aresetn] [get_bd_pins hdmi_smc/aresetn]

connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins axi_smc/aclk]
connect_bd_net [get_bd_pins rst_ps/peripheral_aresetn] [get_bd_pins axi_smc/aresetn]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins processing_system7_0/M_AXI_GP0_ACLK]

# ---------------- LCD：两块帧缓存 ----------------
# Use_Byte_Write_Enable 显式钉成 true：该模式下 addra/addrb 都是字节地址，
# PL 读侧必须送 {字地址, 2'b00}（2026-10-29 上板踩过，见 bd_lcd_probe.tcl 的注释）。
set bram_ctrl_800 [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_bram_ctrl:4.1 bram_ctrl_800]
set_property -dict [list CONFIG.DATA_WIDTH {32} CONFIG.SINGLE_PORT_BRAM {1} \
    CONFIG.ECC_TYPE {0} CONFIG.PROTOCOL {AXI4LITE}] $bram_ctrl_800
connect_bd_intf_net [get_bd_intf_pins axi_smc/M00_AXI] [get_bd_intf_pins bram_ctrl_800/S_AXI]

set bram_800 [create_bd_cell -type ip -vlnv xilinx.com:ip:blk_mem_gen:8.4 bram_800]
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

connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins bram_ctrl_800/s_axi_aclk]
connect_bd_net [get_bd_pins rst_ps/peripheral_aresetn] [get_bd_pins bram_ctrl_800/s_axi_aresetn]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins bram_ctrl_480/s_axi_aclk]
connect_bd_net [get_bd_pins rst_ps/peripheral_aresetn] [get_bd_pins bram_ctrl_480/s_axi_aresetn]

# ---------------- LCD：调试/控制 GPIO ----------------
proc mk_gpio {name is_dual all_out m_index} {
    upvar axi_smc axi_smc
    set g [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 $name]
    set cfg [list CONFIG.C_GPIO_WIDTH {32}]
    if {$is_dual} {
        lappend cfg CONFIG.C_IS_DUAL {1} CONFIG.C_GPIO2_WIDTH {32}
    }
    if {$all_out} {
        lappend cfg CONFIG.C_ALL_OUTPUTS {1}
        if {$is_dual} { lappend cfg CONFIG.C_ALL_OUTPUTS_2 {1} }
    } else {
        lappend cfg CONFIG.C_ALL_INPUTS {1}
        if {$is_dual} { lappend cfg CONFIG.C_ALL_INPUTS_2 {1} }
    }
    set_property -dict $cfg $g
    connect_bd_intf_net [get_bd_intf_pins axi_smc/${m_index}_AXI] [get_bd_intf_pins $name/S_AXI]
    connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins $name/s_axi_aclk]
    connect_bd_net [get_bd_pins rst_ps/peripheral_aresetn] [get_bd_pins $name/s_axi_aresetn]
    return $g
}
mk_gpio gpio_id   1 0 M02
mk_gpio gpio_t    1 0 M03
mk_gpio gpio_cfg  0 1 M04
mk_gpio gpio_d0   1 0 M05
mk_gpio gpio_d1   1 0 M06
mk_gpio gpio_d2   0 0 M07

# 按键：PL 只有 key[0]=L14、key[1]=K16；UI 约定 key[0]=下一项、key[1]=换选项。
set gpio_keys [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 gpio_keys]
set_property -dict [list CONFIG.C_GPIO_WIDTH {2} CONFIG.C_ALL_INPUTS {1}] $gpio_keys
connect_bd_intf_net [get_bd_intf_pins axi_smc/M08_AXI] [get_bd_intf_pins gpio_keys/S_AXI]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins gpio_keys/s_axi_aclk]
connect_bd_net [get_bd_pins rst_ps/peripheral_aresetn] [get_bd_pins gpio_keys/s_axi_aresetn]

# ---------------- LCD 顶层 ----------------
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

# BRAM port B：rstb 高有效，不能接 peripheral_aresetn（低有效，平时恒 1 = 一直复位）
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
connect_bd_net [get_bd_pins lcd_probe_0/dbg_lb0]   [get_bd_pins gpio_d0/gpio_io_i]
connect_bd_net [get_bd_pins lcd_probe_0/dbg_lbmid] [get_bd_pins gpio_d0/gpio2_io_i]
connect_bd_net [get_bd_pins lcd_probe_0/dbg_st0]   [get_bd_pins gpio_d1/gpio_io_i]
connect_bd_net [get_bd_pins lcd_probe_0/dbg_stmid] [get_bd_pins gpio_d1/gpio2_io_i]
connect_bd_net [get_bd_pins lcd_probe_0/dbg_cnt]   [get_bd_pins gpio_d2/gpio_io_i]

# ---------------- HDMI：寄存器 + 扫描输出 ----------------
set gpio_hdmi_cfg [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 gpio_hdmi_cfg]
set_property -dict [list CONFIG.C_GPIO_WIDTH {32} CONFIG.C_IS_DUAL {1} CONFIG.C_GPIO2_WIDTH {32} \
    CONFIG.C_ALL_OUTPUTS {1} CONFIG.C_ALL_OUTPUTS_2 {1}] $gpio_hdmi_cfg
connect_bd_intf_net [get_bd_intf_pins hdmi_smc/M00_AXI] [get_bd_intf_pins gpio_hdmi_cfg/S_AXI]

set gpio_hdmi_st [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 gpio_hdmi_st]
set_property -dict [list CONFIG.C_GPIO_WIDTH {32} CONFIG.C_IS_DUAL {0} CONFIG.C_ALL_INPUTS {1}] $gpio_hdmi_st
connect_bd_intf_net [get_bd_intf_pins hdmi_smc/M01_AXI] [get_bd_intf_pins gpio_hdmi_st/S_AXI]

# 寄存器 AXI 域跟 pclk 走（理由见文件头）；gpio 输出直接进 fb_scanout，全同钟。
connect_bd_net $pclk_h [get_bd_pins gpio_hdmi_cfg/s_axi_aclk]
connect_bd_net $pclk_h [get_bd_pins gpio_hdmi_st/s_axi_aclk]
connect_bd_net [get_bd_pins rst_pix_h/peripheral_aresetn] [get_bd_pins gpio_hdmi_cfg/s_axi_aresetn]
connect_bd_net [get_bd_pins rst_pix_h/peripheral_aresetn] [get_bd_pins gpio_hdmi_st/s_axi_aresetn]

set hdmi [create_bd_cell -type module -reference hdmi_video_top hdmi_video_0]
# 模块引用推出来的 AXI 接口默认标 100 MHz，与实际像素钟 74.255954 MHz 不符 → BD 41-237
set_property CONFIG.FREQ_HZ {74255954} [get_bd_intf_pins hdmi_video_0/M_AXI]
connect_bd_net $pclk_h  [get_bd_pins hdmi_video_0/pclk]
connect_bd_net $pclk_h5 [get_bd_pins hdmi_video_0/pclk_x5]
connect_bd_net [get_bd_pins rst_pix_h/peripheral_aresetn] [get_bd_pins hdmi_video_0/reset_n]
connect_bd_net [get_bd_pins gpio_hdmi_cfg/gpio_io_o]  [get_bd_pins hdmi_video_0/cfg_addr]
connect_bd_net [get_bd_pins gpio_hdmi_cfg/gpio2_io_o] [get_bd_pins hdmi_video_0/cfg_pack]
connect_bd_net [get_bd_pins hdmi_video_0/st_status]   [get_bd_pins gpio_hdmi_st/gpio_io_i]

# AXI4 读主机 → S_AXI_HP0（AXI3）：用 smartconnect 做协议/位宽转换。
# HP0 的 ACLK 必须与 pclk 同源 —— fb_scanout 内部没有跨时钟桥。
if {[catch {associate_bd_net $pclk_h [get_bd_intf_pins hdmi_video_0/M_AXI]} _e]} {
    puts "FLOW_SYSTEM_NOTE associate_bd_net skipped: $_e"
}
set hp_smc [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 hp_smc]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1}] $hp_smc
connect_bd_intf_net [get_bd_intf_pins hdmi_video_0/M_AXI] [get_bd_intf_pins hp_smc/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins hp_smc/M00_AXI] [get_bd_intf_pins processing_system7_0/S_AXI_HP0]
connect_bd_net $pclk_h [get_bd_pins hp_smc/aclk]
connect_bd_net [get_bd_pins rst_pix_h/peripheral_aresetn] [get_bd_pins hp_smc/aresetn]
connect_bd_net $pclk_h [get_bd_pins processing_system7_0/S_AXI_HP0_ACLK]

# ---------------- 外部端口 ----------------
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

foreach {p} {tmds_clk_p tmds_clk_n} { create_bd_port -dir O -type data $p }
create_bd_port -dir O -from 2 -to 0 -type data tmds_data_p
create_bd_port -dir O -from 2 -to 0 -type data tmds_data_n
connect_bd_net [get_bd_ports tmds_clk_p]  [get_bd_pins hdmi_video_0/tmds_clk_p]
connect_bd_net [get_bd_ports tmds_clk_n]  [get_bd_pins hdmi_video_0/tmds_clk_n]
connect_bd_net [get_bd_ports tmds_data_p] [get_bd_pins hdmi_video_0/tmds_data_p]
connect_bd_net [get_bd_ports tmds_data_n] [get_bd_pins hdmi_video_0/tmds_data_n]

# ---------------- 地址表（见文件头；改动须立新决策） ----------------
assign_bd_address -offset 0x40000000 -range 256K [get_bd_addr_segs {bram_ctrl_800/S_AXI/Mem0}]
assign_bd_address -offset 0x40080000 -range 64K  [get_bd_addr_segs {bram_ctrl_480/S_AXI/Mem0}]
assign_bd_address -offset 0x41200000 -range 64K  [get_bd_addr_segs {gpio_hdmi_cfg/S_AXI/Reg}]
assign_bd_address -offset 0x41210000 -range 64K  [get_bd_addr_segs {gpio_hdmi_st/S_AXI/Reg}]
assign_bd_address -offset 0x41220000 -range 64K  [get_bd_addr_segs {gpio_cfg/S_AXI/Reg}]
assign_bd_address -offset 0x41230000 -range 64K  [get_bd_addr_segs {gpio_d0/S_AXI/Reg}]
assign_bd_address -offset 0x41240000 -range 64K  [get_bd_addr_segs {gpio_d1/S_AXI/Reg}]
assign_bd_address -offset 0x41250000 -range 64K  [get_bd_addr_segs {gpio_d2/S_AXI/Reg}]
assign_bd_address -offset 0x41260000 -range 64K  [get_bd_addr_segs {gpio_keys/S_AXI/Reg}]
assign_bd_address -offset 0x41270000 -range 64K  [get_bd_addr_segs {gpio_id/S_AXI/Reg}]
assign_bd_address -offset 0x41280000 -range 64K  [get_bd_addr_segs {gpio_t/S_AXI/Reg}]
validate_bd_design
save_bd_design

set bd_file [get_files system.bd]
make_wrapper -files $bd_file -top
add_files -norecurse [file join $proj_dir flow_system_bd.gen sources_1 bd system hdl system_wrapper.v]
set_property top system_wrapper [current_fileset]
update_compile_order -fileset sources_1
generate_target all [get_files system.bd]

# -jobs 2：本机 14 GB 内存，8 路并行读时序库会 OOM（2026-10-29 实测），不是设计错误
launch_runs impl_1 -to_step write_bitstream -jobs 2
# 不用 wait_on_run：batch 模式下它有时在子任务还停在 "Scripts Generated" 时返回
for {set i 0} {$i < 300} {incr i} {
    after 10000
    set _st [get_property STATUS [get_runs impl_1]]
    if {[string first "write_bitstream Complete" $_st] >= 0} { break }
    if {[string first "ERROR" $_st] >= 0 || [string first "Failed" $_st] >= 0} {
        puts "FLOW_SYSTEM_BD_FAIL $_st"
        return
    }
    if {$i % 6 == 0} { puts "WAIT $i [get_property PROGRESS [get_runs impl_1]] $_st" }
}
set st [get_property STATUS [get_runs impl_1]]
if {[string first "write_bitstream Complete" $st] < 0} { puts "FLOW_SYSTEM_BD_FAIL $st"; return }

set hwh [file join $proj_dir flow_system_bd.gen sources_1 bd system hw_handoff system.hwh]
if {![file exists $hwh]} { puts "FLOW_SYSTEM_BD_FAIL no_hwh"; return }
file copy -force $hwh [file join $overlay flow_system.hwh]

open_run impl_1
report_timing_summary -file [file join $rep_timing flow_system_bd_timing.rpt]
report_utilization   -file [file join $rep_util flow_system_bd_util.rpt]

set bit [file join $proj_dir flow_system_bd.runs impl_1 system_wrapper.bit]
if {![file exists $bit]} {
    set cand [glob -nocomplain [file join $proj_dir flow_system_bd.runs impl_1 *.bit]]
    if {[llength $cand] > 0} { set bit [lindex $cand 0] }
}
file copy -force $bit [file join $overlay flow_system.bit]
puts "FLOW_SYSTEM_BD_OK BIT=[file join $overlay flow_system.bit]"
