# export_preproc.tcl —— 导出 preproc IP
set root     [file normalize [file join [file dirname [info script]] .. .. .. ..]]
set srcdir   [file join $root src hls preproc src]
set workroot [file join $root build hls_preproc]
set ipdir    [file join $root build ip_repo]
set part     "xc7z020clg400-2"
set clock_ns 10.0

file mkdir $workroot
file mkdir $ipdir
cd $workroot

set proj  "exp_preproc"
set cpp   [file join $srcdir preproc.cpp]

if {[file exists $proj]} { file delete -force $proj }

if {[catch {
    open_project -reset $proj
    set_top preproc
    add_files -cflags "-I$srcdir" $cpp
    open_solution -reset "sol_preproc"
    set_part $part
    create_clock -period $clock_ns -name default
    csynth_design
} err]} {
    puts "EXPORT_FAIL : csynth \{$err\}"
    exit 1
}

if {[catch {
    config_export -format ip_catalog -rtl verilog -vendor zynq-yolo -library hls \
        -version "1.0" -display_name "preproc" \
        -description "BGR frame bilinear resize to 320x320 RGB planes" \
        -output [file join $ipdir preproc]
    export_design -format ip_catalog
} err]} {
    puts "EXPORT_FAIL : export \{$err\}"
    exit 1
}

set rpt [file join $proj "sol_preproc" "syn" "report" "csynth.rpt"]
set dsp "-"; set bram "-"; set lut "-"; set ff "-"
if {[file exists $rpt]} {
    set fh [open $rpt r]; set body [read $fh]; close $fh
    foreach line [split $body "\n"] {
        if {[regexp {^\s*\|\+ preproc\s*\|} $line]} {
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
