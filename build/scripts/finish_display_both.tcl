# finish_build.tcl —— 从已生成的工程接着出片：重跑失败的 IP 综合 → impl → 拷 bit/hwh
set root     D:/zynq-yolo
set proj_dir [file join $root build vivado display_both_bd]
set overlay  [file join $root board overlay]

open_project [file join $proj_dir display_both_bd.xpr]

# 上一轮 system_axi_smc_0_synth_1 因 ipshared 的 .vh 还没落盘而失败（竞态）：重跑一次即可
foreach r [get_runs -filter {NAME =~ "*synth*"}] {
    if {[get_property STATUS $r] ne "synth_design Complete!"} {
        puts "RETRY_RUN $r status=[get_property STATUS $r]"
        reset_run $r
        launch_runs $r -jobs 2
        wait_on_run $r
        puts "RETRY_RUN_DONE $r status=[get_property STATUS $r]"
    }
}

launch_runs impl_1 -to_step write_bitstream -jobs 8
wait_on_run impl_1
set st [get_property STATUS [get_runs impl_1]]
puts "IMPL_STATUS $st"
if {[string first "write_bitstream Complete" $st] < 0} {
    puts "DISPLAY_BOTH_BD_FAIL $st"
    return
}

set hwh [file join $proj_dir display_both_bd.gen sources_1 bd system hw_handoff system.hwh]
if {![file exists $hwh]} { puts "DISPLAY_BOTH_BD_FAIL no_hwh"; return }
file copy -force $hwh [file join $overlay display_both.hwh]

set bit [file join $proj_dir display_both_bd.runs impl_1 system_wrapper.bit]
if {![file exists $bit]} {
    set cand [glob -nocomplain [file join $proj_dir display_both_bd.runs impl_1 *.bit]]
    if {[llength $cand] > 0} { set bit [lindex $cand 0] }
}
file copy -force $bit [file join $overlay display_both.bit]

open_run impl_1
report_timing_summary -file [file join $root build reports timing display_both_bd_timing.rpt]
puts "DISPLAY_BOTH_BD_OK BIT=[file join $overlay display_both.bit]"
