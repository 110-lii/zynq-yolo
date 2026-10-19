# =============================================================================
# bd_conv_engine.tcl —— 为 conv_engine HLS IP 建 Vivado block design 并出 bitstream
#
# 目标（M4a）：PS 能通过 AXI-Lite 配置 conv_engine 并把 DDR 当输入/权重/输出缓冲区。
#
# 结构
#   processing_system7_0            PS7（配置逐字来自 build/scripts/ps7_config.tcl）
#     M_AXI_GP0 ── ctrl_smc ──┬── conv_engine_0/s_axi_control     （11 个标量参数）
#                             └── conv_engine_0/s_axi_control_r   （5 个指针基址，64 位）
#     S_AXI_HP0 ←─ prot_smc_r ←── conv_engine_0/m_axi_gmem_r      （读 in/w/params/lut）
#     S_AXI_HP1 ←─ prot_smc_w ←── conv_engine_0/m_axi_gmem_w      （写 out）
#     FCLK_CLK0 ── 100 MHz ──> conv_engine_0/ap_clk + proc_sys_reset
#
# ★ 为什么是两个 AXI-Lite 从口而不是一个
#   Vitis HLS 把「11 个标量」和「5 个 m_axi 的 offset=slave 基址（各 64 位）」放进了
#   **两个** AXI-Lite 从口：s_axi_control 与 s_axi_control_r。这不是我们能选的，
#   是 HLS 定的（见 build/ip_repo/.../component.xml 的 busInterface 列表）。
#   地址映射权威出处是 IP 自带的 drivers/.../xconv_engine_hw.h。
#
# ★ 为什么数据面只用两个 AXI 主口
#   HLS 源码里原本按语义分了 5 个 bundle（in/w/params/lut/out）。但 Zynq-7020 只有
#   4 个 AXI HP 口，5 个主口接不下，且 BD 里要塞 5 组互联。已在 conv_engine.cpp 里
#   合并成 gmem_r（读）与 gmem_w（写）两个 —— 读写能在不同 HP 口上并发。
#
# ★ 为什么不用 apply_bd_automation
#   那条规则一次只在「一个主口 ↔ 一个从口」之间插互联；GP0 要驱动两个从口时它会
#   插出两个都挂在 GP0 上的互联，直接冲突。所以这里手工建 SmartConnect。
#
# 运行
#   vivado -mode batch -source build/scripts/bd_conv_engine.tcl
#   （或用 Makefile 的 bit 目标）
#
# 判据：末尾令牌 BD_DONE_OK / BD_FAIL，**不看退出码**（Vivado 的退出码不可信，
#       见 ref/pitfalls_from_oi.md P3）。
# =============================================================================

set script_dir [file normalize [file dirname [info script]]]
set root       [file normalize [file join $script_dir .. ..]]
set part_name  "xc7z020clg400-2"
set proj_dir   [file join $root build vivado conv_engine_bd]
set ip_repo    [file join $root build ip_repo]
set overlay    [file join $root board overlay]
set rep_timing [file join $root build reports timing]
set rep_util   [file join $root build reports resource]

puts "ROOT      = $root"
puts "PROJ_DIR  = $proj_dir"
puts "IP_REPO   = $ip_repo"

if {![file exists [file join $ip_repo conv_engine_p8_1_40 component.xml]]} {
    puts "BD_FAIL 找不到已打包的 IP：$ip_repo/conv_engine_p8_1_40"
    puts "        先跑 src/hls/accel/scripts/export_engine.py"
    return
}
foreach d [list $overlay $rep_timing $rep_util] {
    file mkdir $d
}

# ---- 工程 ----
create_project -force conv_engine_bd $proj_dir -part $part_name
set_property ip_repo_paths $ip_repo [current_project]
update_ip_catalog -rebuild

# ---- BD ----
create_bd_design "system"

# ① PS7：逐字套用旧项目实测可用的配置，然后只改我们真正需要改的四项
source [file join $root build scripts ps7_config.tcl]
set_property -dict [list \
    CONFIG.PCW_USE_S_AXI_HP0 {1} \
    CONFIG.PCW_USE_S_AXI_HP1 {1} \
    CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ {83.333333} \
    CONFIG.PCW_USE_FABRIC_INTERRUPT {1} \
    CONFIG.PCW_IRQ_F2P_INTR {1} \
] $processing_system7_0
# ★ FCLK0 = 83.33 MHz，不是 100 MHz —— 这是**实测逼出来的**，不是随手选的：
#   第一次按 100 MHz 跑完实现，实测 WNS = **-1.247 ns**（`build/reports/timing/conv_engine_bd_timing.rpt`），
#   失败端点 226/77469，最差路径在 conv_engine 的 requant 乘法器里
#   （`mul_64s_32s_64_5_1_U14/... -> p_mid1_reg[45]`，数据路径 10.952 ns、22 级逻辑、route 占 49.8%），
#   对应真实 Fmax ≈ 1/(10+1.247-0.354) ≈ 88.5 MHz。
#   ⚠️ HLS 自己的 csynth 估计说 136.99 MHz —— 那个数**不准**，布线延迟它不管。
#   83.33 MHz（1000/12，Zynq 的标准分频点）给最差路径留约 +0.75 ns 余量，且保住 83% 的算力。
#   要把主频提回 100 MHz，正确做法是重构 requant 的 48x48 乘法（拆成 18x18 DSP 组合），
#   而不是把时钟硬拉高。
puts "PS7: HP0/HP1 打开，FCLK0 = 83.33 MHz（100 MHz 实测 WNS -1.247 ns，见注释）"

# ② HLS 引擎
set ce [create_bd_cell -type ip -vlnv zynq-yolo:hls:conv_engine:1.0 conv_engine_0]
puts "conv_engine 接口：[get_bd_intf_pins -of_objects $ce]"

# ③ 控制面：GP0 -> SmartConnect -> 两个 AXI-Lite 从口
set ctrl_smc [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 ctrl_smc]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {2}] $ctrl_smc
connect_bd_intf_net [get_bd_intf_pins processing_system7_0/M_AXI_GP0] [get_bd_intf_pins ctrl_smc/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins ctrl_smc/M00_AXI] [get_bd_intf_pins conv_engine_0/s_axi_control]
connect_bd_intf_net [get_bd_intf_pins ctrl_smc/M01_AXI] [get_bd_intf_pins conv_engine_0/s_axi_control_r]

# ④ 时钟与复位（**必须先建**：下面数据面的 aresetn 要连它）
set rst [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_ps7_100M]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins rst_ps7_100M/slowest_sync_clk]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_RESET0_N] [get_bd_pins rst_ps7_100M/ext_reset_in]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins conv_engine_0/ap_clk]
connect_bd_net [get_bd_pins rst_ps7_100M/peripheral_aresetn] [get_bd_pins conv_engine_0/ap_rst_n]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins ctrl_smc/aclk]
connect_bd_net [get_bd_pins rst_ps7_100M/peripheral_aresetn] [get_bd_pins ctrl_smc/aresetn]

# ⑤ 数据面：两个 1x1 SmartConnect，读走 HP0、写走 HP1（互不争用）
foreach {tag si mi hp} {r S00_AXI M00_AXI S_AXI_HP0 w S00_AXI M00_AXI S_AXI_HP1} {
    set sc [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 prot_smc_$tag]
    set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1}] $sc
    connect_bd_intf_net [get_bd_intf_pins conv_engine_0/m_axi_gmem_$tag] [get_bd_intf_pins prot_smc_$tag/$si]
    connect_bd_intf_net [get_bd_intf_pins prot_smc_$tag/$mi] [get_bd_intf_pins processing_system7_0/$hp]
    connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins prot_smc_$tag/aclk]
    connect_bd_net [get_bd_pins rst_ps7_100M/peripheral_aresetn] [get_bd_pins prot_smc_$tag/aresetn]
}

# ⑥ PS7 的 AXI 口时钟（默认要靠 apply_bd_automation 接，我们手工建就得自己接，
#    否则 validate 报 [BD 41-758] 这三个时钟脚没接）
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins processing_system7_0/M_AXI_GP0_ACLK]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins processing_system7_0/S_AXI_HP0_ACLK]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins processing_system7_0/S_AXI_HP1_ACLK]

# ⑦ 中断（本阶段驱动用轮询，接上是为以后留路）
set concat [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconcat:2.1 irq_concat]
set_property -dict [list CONFIG.NUM_PORTS {1}] $concat
connect_bd_net [get_bd_pins conv_engine_0/interrupt] [get_bd_pins irq_concat/In0]
connect_bd_net [get_bd_pins irq_concat/dout] [get_bd_pins processing_system7_0/IRQ_F2P]

# ⑦ 地址分配
assign_bd_address
puts "---- 地址映射 ----"
foreach seg [get_bd_addr_segs] { puts "  $seg" }

validate_bd_design
save_bd_design

# ★ 调试用：先只建 BD 校验，不跑十几分钟的综合实现。
#   `BD_STOP_AFTER_BD=1 vivado -mode batch -source ...`
if {[info exists ::env(BD_STOP_AFTER_BD)] && $::env(BD_STOP_AFTER_BD) ne "0"} {
    puts "BD_ONLY_OK 已按 BD_STOP_AFTER_BD 停在 BD 校验后（未跑综合实现）"
    return
}

# ⑧ 顶层 wrapper + 综合实现
set bd_file [get_files system.bd]
make_wrapper -files $bd_file -top
add_files -norecurse [file join $proj_dir conv_engine_bd.gen sources_1 bd system hdl system_wrapper.v]
set_property top system_wrapper [current_fileset]
update_compile_order -fileset sources_1

generate_target all [get_files system.bd]
# ★ BD 会把接口/地址信息写进 hw_handoff，PYNQ 靠它认寄存器 —— 必须留档
set hwh [file join $proj_dir conv_engine_bd.gen sources_1 bd system hw_handoff system.hwh]
if {[file exists $hwh]} {
    file copy -force $hwh [file join $overlay conv_engine.hwh]
    puts "HWH  -> [file join $overlay conv_engine.hwh]"
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
report_timing_summary -file [file join $rep_timing conv_engine_bd_timing.rpt]
report_utilization   -file [file join $rep_util   conv_engine_bd_util.rpt]
report_drc           -file [file join $root build reports drc conv_engine_bd_drc.rpt]

set bit [file join $proj_dir conv_engine_bd.runs impl_1 system_wrapper.bit]
if {![file exists $bit]} {
    set cand [glob -nocomplain [file join $proj_dir conv_engine_bd.runs impl_1 *.bit]]
    if {[llength $cand] > 0} { set bit [lindex $cand 0] }
}
if {![file exists $bit]} {
    puts "BD_FAIL 没找到 .bit"
    return
}
file copy -force $bit [file join $overlay conv_engine.bit]
puts "BIT  -> [file join $overlay conv_engine.bit]"

# 时序与资源摘要（放进令牌，方便机器读）
set wns "?"
set fmax "?"
if {![catch {
    set p [get_timing_paths -delay_type max]
    set wns [get_property SLACK $p]
}]} {}

# ★ 清理 Vivado 残留（**必须**，不是洁癖）
#   `tools/check_repo.py` 的检查 d) 是**扫磁盘**的（不看 git），而 Vivado 会在工程目录里
#   留一堆 `vivado.jou` / `*.log` / `.Xil/`。`build/vivado/` 虽然已被 `.gitignore` 排除，
#   但扫盘检查照样判 FAIL —— 与 `ai/tools/__pycache__` 完全同一个坑（那里的结论是
#   "不要靠 .gitignore、不要靠事后手删，要从源头/收尾消除"）。这里选收尾消除。
# 递归删除（Vivado 会在工程目录各处留下 vivado.jou / *.log / .Xil/，层数不定）
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

# ★ 必须先关工程再删：project 开着时文件被持有，file delete 静默失败。
#   第一次跑就因为清理块加进了「已在内存里运行的旧 Tcl」而没生效，磁盘留了 16 个 .jou/.log + 8 个 .Xil/。
catch {close_project}

set nclean [_rm_recursive $proj_dir {"*.jou" "*.log" "*.str" ".Xil"}]
# 外层 vivado.bat 的产物落在调用时的 cwd（仓库根），也要清
set repo_root [file normalize [file join $script_dir .. ..]]
foreach pat {"vivado.log" "vivado_*.backup.log" "vivado.jou" "vivado_*.jou"} {
    foreach f [glob -nocomplain -directory $repo_root -types f $pat] {
        if {![catch {file delete -force $f}]} { incr nclean }
    }
}
# ★ 仓库根的 `.Xil` 是**目录**不是文件：以 cwd=仓库根 调 vivado 时它落在 `$repo_root/.Xil`。
#   check d) 是扫盘的（不看 gitignore），漏掉它照样 `CHECK_REPO_FAIL` —— 实测已经踩过一次。
foreach d [glob -nocomplain -directory $repo_root -types d ".Xil"] {
    if {![catch {file delete -force $d}]} { incr nclean }
}
puts "CLEAN_DONE removed=$nclean"

puts "BD_DONE_OK BIT=[file join $overlay conv_engine.bit] HWH=[file join $overlay conv_engine.hwh] WNS=$wns"
