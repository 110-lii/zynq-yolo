# rebuild_existing.tcl —— 复用已生成的 BD 工程，只重跑综合 + 实现 + 出片
# 用法：vivado -mode batch -source rebuild_existing.tcl -tclargs <输出bit名>
set root     D:/zynq-yolo
set proj_dir [file join $root build vivado display_both_bd]
set overlay  [file join $root board overlay]

set out_name "display_both.bit"
if {[llength $argv] > 0} { set out_name [lindex $argv 0] }

open_project [file join $proj_dir display_both_bd.xpr]

# ★★ 必须连 IP 的 OOC run 一起 reset ★★
# BD 里用 create_bd_cell -type module -reference 引入的模块（如 hdmi_status_top /
# lcd_status_top）会各自生成独立的 synth run（system_hdmi_status_0_0_synth_1 ...）。
# 只 reset 顶层 synth_1 时这些 run 保持 Complete，继续用旧网表 ⇒ 改了 RTL 却上旧片。
# 曾在 fb_reader 上连踩三次（位宽修复、两轮诊断全都没进 bit）。
foreach r [get_runs -quiet] {
    if {[string first "Not started" [get_property STATUS $r]] < 0} {
        puts "RESET_RUN $r ([get_property STATUS $r])"
        reset_run $r
    }
}

reset_run synth_1
launch_runs synth_1 -jobs 8
wait_on_run synth_1
set sst [get_property STATUS [get_runs synth_1]]
puts "SYNTH_STATUS $sst"
if {[string first "synth_design Complete" $sst] < 0} { puts "REBUILD_FAIL synth"; return }

# ★ impl_1 也必须 reset：否则它仍是 Complete 状态，launch_runs 直接跳过，
#   write_bitstream 拷出来的是上一轮的旧 bit（踩过一次：RTL 改了却上了旧片）。
reset_run impl_1
launch_runs impl_1 -to_step write_bitstream -jobs 8
wait_on_run impl_1
set ist [get_property STATUS [get_runs impl_1]]
puts "IMPL_STATUS $ist"
if {[string first "write_bitstream Complete" $ist] < 0} { puts "REBUILD_FAIL impl"; return }

set hwh [file join $proj_dir display_both_bd.gen sources_1 bd system hw_handoff system.hwh]
file copy -force $hwh [file join $overlay [string map {.bit .hwh} $out_name]]
set bit [file join $proj_dir display_both_bd.runs impl_1 system_wrapper.bit]
file copy -force $bit [file join $overlay $out_name]
puts "REBUILD_OK BIT=[file join $overlay $out_name]"
