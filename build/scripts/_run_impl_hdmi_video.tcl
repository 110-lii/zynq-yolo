# _run_impl_hdmi_video.tcl —— 单独跑 hdmi_video 的 impl（BD 已由 bd_hdmi_video.tcl 生成）
#
# 为什么单独一个脚本：batch 模式下 launch_runs 之后 wait_on_run 有时在子任务还处于
# "Scripts Generated" 时就返回（父进程随即退出，子任务被杀）。这里改成
# 「launch → 轮询 STATUS/PROGRESS 直到 Complete」，避免假完成。
set root   [file normalize [file join [file dirname [info script]] .. ..]]
set proj   [file join $root build vivado hdmi_video_bd hdmi_video_bd.xpr]
set overlay [file join $root board overlay]
set rep_timing [file join $root build reports timing]
set rep_util   [file join $root build reports resource]

open_project $proj
set st [get_property STATUS [get_runs impl_1]]
puts "IMPL_STATUS_BEFORE $st"
if {[string first "write_bitstream Complete" $st] < 0} {
    reset_run impl_1
    launch_runs impl_1 -to_step write_bitstream -jobs 8
    for {set i 0} {$i < 180} {incr i} {
        after 10000
        set st [get_property STATUS [get_runs impl_1]]
        set pr [get_property PROGRESS [get_runs impl_1]]
        puts "POLL $i $pr $st"
        if {[string first "write_bitstream Complete" $st] >= 0} { break }
        if {[string first "ERROR" $st] >= 0 || [string first "Failed" $st] >= 0} {
            puts "HDMI_VIDEO_BD_FAIL $st"
            return
        }
    }
}
set st [get_property STATUS [get_runs impl_1]]
if {[string first "write_bitstream Complete" $st] < 0} {
    puts "HDMI_VIDEO_BD_FAIL timeout $st"
    return
}
open_run impl_1
report_timing_summary -file [file join $rep_timing hdmi_video_bd_timing.rpt]
report_utilization   -file [file join $rep_util hdmi_video_bd_util.rpt]
set bit [file join $root build vivado hdmi_video_bd hdmi_video_bd.runs impl_1 system_wrapper.bit]
if {![file exists $bit]} {
    set cand [glob -nocomplain [file join $root build vivado hdmi_video_bd hdmi_video_bd.runs impl_1 *.bit]]
    if {[llength $cand] > 0} { set bit [lindex $cand 0] }
}
file copy -force $bit [file join $overlay hdmi_video.bit]
set hwh [file join $root build vivado hdmi_video_bd hdmi_video_bd.gen sources_1 bd system hw_handoff system.hwh]
if {[file exists $hwh]} { file copy -force $hwh [file join $overlay hdmi_video.hwh] }
puts "HDMI_VIDEO_BD_OK BIT=[file join $overlay hdmi_video.bit]"
