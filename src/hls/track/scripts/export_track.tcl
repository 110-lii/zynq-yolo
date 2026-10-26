# export_track.tcl —— 导出 track_preproc IP（csynth + 打包）
set root     [file normalize [file join [file dirname [info script]] .. .. .. ..]]
set srcdir   [file join $root src hls track src]
set workroot [file join $root build hls_track]
set ipdir    [file join $root build ip_repo]
set part     "xc7z020clg400-2"
set clock_ns 10.0

file mkdir $workroot
file mkdir $ipdir
cd $workroot

set proj  "exp_track"
set cpp   [file join $srcdir track_preproc.cpp]
if {[file exists $proj]} { file delete -force $proj }

if {[catch {
    open_project -reset $proj
    set_top track_preproc
    add_files -cflags "-I$srcdir" $cpp
    open_solution -reset "sol_track"
    set_part $part
    create_clock -period $clock_ns -name default
    csynth_design
} err]} {
    puts "EXPORT_FAIL : csynth \{$err\}"
    exit 1
}

if {[catch {
    config_export -format ip_catalog -rtl verilog -vendor zynq-yolo -library hls \
        -version "1.0" -display_name "track_preproc" \
        -description "BGR->320 RGB + white/motion mask for tracking assist" \
        -output [file join $ipdir track_preproc]
    export_design -format ip_catalog
} err]} {
    puts "EXPORT_FAIL : export \{$err\}"
    exit 1
}

set rpt [file join $proj "sol_track" "syn" "report" "csynth.rpt"]
set dsp "-"; set bram "-"; set lut "-"; set ff "-"
if {[file exists $rpt]} {
    set fh [open $rpt r]; set body [read $fh]; close $fh
    foreach line [split $body "\n"] {
        if {[regexp {^\s*\|\+ track_preproc\s*\|} $line]} {
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
puts "EXPORT_DONE IP=$ipdir DSP=$dsp LUT=$lut FF=$ff BRAM18=$bram"
close_project
exit 0
