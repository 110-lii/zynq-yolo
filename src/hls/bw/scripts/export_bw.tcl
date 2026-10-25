# ============================================================================
# export_bw.tcl —— 把 axi_bw.cpp 导出成 IP（Vitis HLS 2020.2）
#
# 判据：EXPORT_DONE <IP 目录> DSP=.. BRAM18=.. LUT=.. FF=.. II=..
#       失败：EXPORT_FAIL : <原因>
#
# 注意（同一套坑）：Tcl 注释只能 #；open_project 不能吃带 : 的绝对路径；
# 导出必须给 vendor/library/version。
# ============================================================================
set root     [file normalize [file join [file dirname [info script]] .. .. .. ..]]
set srcdir   [file join $root src hls bw src]
set workroot [file join $root build hls_bw]
set ipdir    [file join $root build ip_repo]
set part     "xc7z020clg400-2"
set clock_ns 10.0

file mkdir $workroot
file mkdir $ipdir
cd $workroot

set proj  "exp_axi_bw"
set cpp   [file join $srcdir axi_bw.cpp]
set ipnam "axi_bw"

puts "==============================================================="
puts "== EXPORT AXI_BW"
puts "==============================================================="

if {[file exists $proj]} { file delete -force $proj }

if {[catch {
    open_project -reset $proj
    set_top axi_bw
    add_files -cflags "-I$srcdir" $cpp
    open_solution -reset "sol_bw"
    set_part $part
    create_clock -period $clock_ns -name default
    csynth_design
} err]} {
    puts "EXPORT_FAIL : csynth \{$err\}"
    exit 1
}

if {[catch {
    config_export -format ip_catalog -rtl verilog -vendor zynq-yolo -library hls \
        -version "1.0" -display_name "axi_bw" \
        -description "pure AXI master bandwidth benchmark" \
        -output [file join $ipdir $ipnam]
    export_design -format ip_catalog
} err]} {
    puts "EXPORT_FAIL : export \{$err\}"
    exit 1
}

set rpt [file join $proj "sol_bw" "syn" "report" "csynth.rpt"]
set dsp "-"; set bram "-"; set lut "-"; set ff "-"; set ii "-"
if {[file exists $rpt]} {
    set fh [open $rpt r]; set body [read $fh]; close $fh
    foreach line [split $body "\n"] {
        if {[regexp {^\s*\|\+ axi_bw\s*\|} $line]} {
            set cells [split $line "|"]
            if {[llength $cells] >= 14} {
                set bram [string trim [lindex $cells 10]]
                set dsp  [string trim [lindex $cells 11]]
                set ff   [string trim [lindex $cells 12]]
                set lut  [string trim [lindex $cells 13]]
            }
        }
    }
}

set comp [file join $ipdir $ipnam "component.xml"]
if {![file exists $comp]} {
    puts "EXPORT_FAIL : 导出后找不到 $comp"
    exit 1
}
puts "EXPORT_DONE IP=$ipdir DSP=$dsp LUT=$lut FF=$ff BRAM18=$bram"
close_project
exit 0
