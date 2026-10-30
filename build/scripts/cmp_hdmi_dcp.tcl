######################################################################
# cmp_hdmi_dcp.tcl —— 在两个已布线的 checkpoint 上抽出 HDMI 段的可比事实
#
# 用途：flow_system.bit 的 HDMI 无画面，而 hdmi_video.bit 有画面；两者
# RTL 与 BD 连线逐行比对都一致，所以差异只能在「实现结果」里。
# 本脚本对两个 DCP 各输出同一组问题，再人工比对：
#   · TMDS 四个输出口的驱动网络是否存在
#   · 像素时钟 / 5x 时钟的网络上到底挂了多少负载（串行器在不在上面）
#   · dvi_transmitter_top 里的 clock mux / ODDR 是否被优化掉
# 只读，不改设计。
######################################################################

set root [file normalize [file join [file dirname [info script]] .. ..]]
set pairs [list \
    flow_system [file join $root build vivado flow_system_bd flow_system_bd.runs impl_1 system_wrapper_routed.dcp] \
    hdmi_video  [file join $root build vivado hdmi_video_bd  hdmi_video_bd.runs  impl_1 system_wrapper_routed.dcp] \
]

foreach {tag dcp} $pairs {
    puts "########## DCP $tag $dcp"
    if {[catch {open_checkpoint $dcp} _e]} { puts "OPEN_FAIL $tag $_e"; continue }
    puts "DESIGN_CELLS [llength [get_cells -hier -quiet]]"

    # ---- 1. TMDS 输出口 ----
    foreach p {tmds_clk_p tmds_clk_n tmds_data_p tmds_data_n} {
        set pt [get_ports -quiet $p]
        if {[llength $pt] == 0} { puts "PORT $p MISSING"; continue }
        set ns [get_nets -quiet -of_objects $pt]
        puts "PORT $p NET=[get_property NAME $ns]"
        set drv [get_pins -quiet -of_objects $ns]
        foreach d $drv {
            set ref [get_property REF_NAME $d]
            if {$ref eq "" } { continue }
            if {[string match OBUF* $ref] || [string match OSERDES* $ref] || [string match OBUFT* $ref] || [string match ODDR* $ref]} {
                puts "   SRC $d ($ref)"
            }
        }
    }

    # ---- 2. HDMI 时钟网络负载 ----
    foreach pat {*clk_out1_system_clk_wiz_hdmi* *clk_out2_system_clk_wiz_hdmi*} {
        foreach n [get_nets -hier -quiet -filter "NAME =~ $pat"] {
            set loads [get_pins -quiet -of_objects $n]
            set k 0
            set refs [list]
            foreach l $loads {
                set r [get_property REF_NAME $l]
                if {$r eq ""} { continue }
                incr k
                lappend refs $r
            }
            puts "CLKNET [get_property NAME $n] LOADS=$k"
            foreach r [lsort -unique $refs] { puts "   LOADREF $r x[llength [lsearch -all -exact $refs $r]]" }
        }
    }

    # ---- 3. clock mux / ODDR 是否还在 ----
    foreach pat {*u_clk_mux* *u_oddr_clk* *serializer_10_to_1*} {
        set cs [get_cells -hier -quiet -filter "NAME =~ $pat"]
        puts "CELLS $pat N=[llength $cs]"
        foreach c $cs { puts "   [get_property NAME $c] [get_property REF_NAME $c]" }
    }

    # ---- 4. hdmi_video_0 下的时序单元总数（有没有被裁掉） ----
    puts "HDMI_REG [llength [get_cells -hier -quiet -filter {NAME =~ system_i/hdmi_video_0* && IS_SEQUENTIAL}]]"

    close_design
}
puts "CMP_DCP_DONE"
