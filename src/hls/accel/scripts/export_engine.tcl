# ============================================================================
# export_engine.tcl —— 把 conv_engine.cpp 导出成 Vivado 可用的 IP（IP-XACT 包）
#
# 与 csynth_engine.tcl 的关系：
#   csynth_engine.tcl 只做综合，用来量资源/时序（**不产出 IP**）。
#   本脚本在 csynth_design 之后加 `export_design -format ip_catalog`，
#   产出 `<outdir>/conv_engine_<tag>.zip`，可以 add_ip 进 Vivado 工程。
#
# 沿用已踩过的坑（别改回去）：
#   1. Tcl 注释只能 `#`
#   2. open_project 不接受含 `:` 的绝对路径 ⇒ 先 cd 再相对路径
#   3. 不用 -tclargs ⇒ 参数走环境变量指向的 tcl 片段
#   4. 导出必须给 -vendor/-library/-version，否则 Vivado 的 IP 目录里同版本冲突
#
# 参数文件需提供：cfg_tag cfg_par cfg_tile_h cfg_tile_w [cfg_acc_full]
# 环境变量：HLS_EXPORT_PARAMS 指向该文件
#
# 判据：`EXPORT_DONE <tag> IP=<zip 绝对路径> DSP=.. LUT=.. FF=.. BRAM18=..`
#       失败：`EXPORT_FAIL <tag> : <原因>`
# ============================================================================

if {![info exists ::env(HLS_EXPORT_PARAMS)]} {
    puts "EXPORT_FAIL noenv : 需要环境变量 HLS_EXPORT_PARAMS 指向参数 tcl"
    exit 1
}
set paramfile $::env(HLS_EXPORT_PARAMS)
if {![file exists $paramfile]} {
    puts "EXPORT_FAIL noparams : 找不到 $paramfile"
    exit 1
}
source $paramfile

set tag $cfg_tag
set par $cfg_par
set th  $cfg_tile_h
set tw  $cfg_tile_w
if {[info exists cfg_acc_full]} { set accfull $cfg_acc_full } else { set accfull 0 }

set root     [file normalize [file join [file dirname [info script]] .. .. .. ..]]
set srcdir   [file join $root src hls accel src]
set workroot [file join $root build hls_export]
set ipdir    [file join $root build ip_repo]
set part     "xc7z020clg400-2"
set clock_ns 10.0

file mkdir $workroot
file mkdir $ipdir
cd $workroot

set proj  "exp_$tag"
set cpp   [file join $srcdir conv_engine.cpp]
set ipnam "conv_engine_$tag"

puts "==============================================================="
puts "== EXPORT ENGINE: tag=$tag PAR_OUT=$par TILE_H=$th TILE_W=$tw ACC_FULL=$accfull"
puts "==============================================================="

if {[file exists $proj]} { file delete -force $proj }

if {[catch {
    open_project -reset $proj
    set_top conv_engine
    add_files -cflags "-I$srcdir -DPAR_OUT=$par -DTILE_H=$th -DTILE_W=$tw -DACC_FULL=$accfull" $cpp
    open_solution -reset "sol_$tag"
    set_part $part
    create_clock -period $clock_ns -name default
    # 导出用不着 cosim，省一半时间
    csynth_design
} err]} {
    puts "EXPORT_FAIL $tag : csynth \{$err\}"
    exit 1
}

if {[catch {
    config_export -format ip_catalog -rtl verilog -vendor zynq-yolo -library hls \
        -version "1.0" -display_name "conv_engine_$tag" \
        -description "INT8 3x3/1x1 conv engine, runtime geometry, line-buffered" \
        -output [file join $ipdir $ipnam]
    export_design -format ip_catalog
} err]} {
    puts "EXPORT_FAIL $tag : export \{$err\}"
    exit 1
}

# ---- 抓资源数字（字段序同 csynth_engine.tcl）----
set rpt [file join $proj "sol_$tag" "syn" "report" "csynth.rpt"]
set dsp "-"; set bram "-"; set lut "-"; set ff "-"
if {[file exists $rpt]} {
    set fh [open $rpt r]; set body [read $fh]; close $fh
    foreach line [split $body "\n"] {
        if {[regexp {^\s*\|\+ conv_engine\s*\|} $line]} {
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

# IP 目录：config_export -output 会建 `<ipdir>/<ipnam>/`，里面应有 component.xml
set comp [file join $ipdir $ipnam "component.xml"]
if {![file exists $comp]} {
    puts "EXPORT_FAIL $tag : 导出后找不到 $comp"
    exit 1
}
puts "EXPORT_DONE $tag IP=$ipdir DSP=$dsp LUT=$lut FF=$ff BRAM18=$bram"
close_project
exit 0
