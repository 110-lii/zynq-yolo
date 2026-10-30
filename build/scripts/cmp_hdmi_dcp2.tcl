######################################################################
# cmp_hdmi_dcp2.tcl —— 第二轮：直接问「串行器还在不在、谁给它时钟」
#
# 第一轮（cmp_hdmi_dcp.tcl）按层级名搜 *serializer_10_to_1* 两边都是 0，
# 说明模块层级在 BD 综合里被打散了，得按 REF_NAME 搜原语。
# 本脚本对每个 DCP 输出：
#   · 所有 OSERDESE2 / ODDR / BUFG 的实例名
#   · OSERDESE2 的 CLK / CLKDIV 引脚上挂的网络名
#   · 全芯片 report_clock_networks 文本，便于逐时钟比对
######################################################################

set root [file normalize [file join [file dirname [info script]] .. ..]]
set out  [file join $root build vivado]
set pairs [list \
    flow_system [file join $root build vivado flow_system_bd flow_system_bd.runs impl_1 system_wrapper_routed.dcp] \
    hdmi_video  [file join $root build vivado hdmi_video_bd  hdmi_video_bd.runs  impl_1 system_wrapper_routed.dcp] \
]

foreach {tag dcp} $pairs {
    puts "########## DCP $tag"
    if {[catch {open_checkpoint $dcp} _e]} { puts "OPEN_FAIL $tag $_e"; continue }

    foreach ref {OSERDESE2 ODDR OBUFDS OBUF} {
        set cs [get_cells -hier -quiet -filter "REF_NAME == $ref"]
        puts "REF $ref N=[llength $cs]"
        foreach c $cs { puts "   CELL $c" }
    }

    foreach c [get_cells -hier -quiet -filter {REF_NAME == OSERDESE2}] {
        foreach pn {CLK CLKDIV T1 T2 T3 T4} {
            set p [get_pins -quiet $c/$pn]
            if {[llength $p] == 0} { continue }
            set n [get_nets -quiet -of_objects $p]
            if {[llength $n] == 0} { continue }
            set drv [get_pins -quiet -of_objects $n -filter {DIRECTION == OUT}]
            set dcell ""
            foreach d $drv { set dcell "[get_property NAME $d] "; break }
            puts "   $c/$pn NET=[get_property NAME $n] SRC=$dcell"
        }
    }

    catch { report_clock_networks -file [file join $out "${tag}_clknet.rpt"] }
    close_design
}
puts "CMP_DCP2_DONE"
