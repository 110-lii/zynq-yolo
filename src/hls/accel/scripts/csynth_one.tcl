# ============================================================================
# csynth_one.tcl —— HLS 资源/时序探针（**单配置**版）
#
# 为什么是"单配置 + 外部并行"：
#   第一版把 4 个配置写在一个脚本里串行跑，每个 csynth 3–6.5 分钟，四串 20 分钟；
#   且中途发现代码写错时前面全白跑。本机 16 物理核 ⇒ 每配置一个进程并行，
#   墙钟从"求和"变"取最大"，且任一失败不影响其它。
#
# ★ 参数传递的三个坑（都实测踩过，别改回去）：
#   1. Tcl 注释只能 `#`；C 风格 `//` ⇒ `invalid command name "//"`。
#   2. Vitis HLS 2020.2 的 open_project **不接受含 `:` 的绝对路径** ⇒ 先 cd 再用相对路径。
#   3. `vitis_hls -f SCRIPT -tclargs a b c` 会把 `-tclargs` **之后的所有 token**
#      （含再后面的 `-f`）都当成脚本参数 ⇒ 实测 tag 变成 "-f"；
#      改成 `-tclargs a b c -f SCRIPT` 又会被当成 source 的第一个参数 ⇒ "source p2" 打不开。
#      ⇒ **不用 -tclargs**：改为调用方把参数写进一个 tcl 片段，本脚本 `source` 它。
#        见 build/hls_probe/params_<tag>.tcl（由 run_probe.py 生成）。
#
# 判据：成功打印 `CSYNTH_DONE <tag> DSP=.. LUT=.. FF=.. BRAM18=.. II=.. Fmax=..`
#       失败打印 `CSYNTH_FAIL <tag> : <原因>`
# ============================================================================

# ---- 参数由调用方生成的片段提供 ----
# 片段里必须定义：cfg_tag / cfg_par / cfg_cin / cfg_cout / cfg_h / cfg_w
if {![info exists ::env(HLS_PROBE_PARAMS)]} {
    puts "CSYNTH_FAIL noenv : 需要环境变量 HLS_PROBE_PARAMS 指向参数 tcl"
    exit 1
}
set paramfile $::env(HLS_PROBE_PARAMS)
if {![file exists $paramfile]} {
    puts "CSYNTH_FAIL noparams : 找不到 $paramfile"
    exit 1
}
source $paramfile

set tag      $cfg_tag
set par      $cfg_par
set cin      $cfg_cin
set cout     $cfg_cout
set hin      $cfg_h
set win      $cfg_w

set root     [file normalize [file join [file dirname [info script]] .. .. .. ..]]
set srcdir   [file join $root src hls accel src]
set workroot [file join $root build hls_probe]
set part     "xc7z020clg400-2"
set clock_ns 10.0

file mkdir $workroot
cd $workroot

set proj "probe_$tag"
set cpp  [file join $srcdir acc_conv3x3.cpp]

puts "=============================================================="
puts "== CSYNTH ONE: tag=$tag PAR_OUT=$par C_IN=$cin C_OUT=$cout H=$hin W=$win"
puts "=============================================================="

if {[file exists $proj]} { file delete -force $proj }

if {[catch {
    open_project -reset $proj
    set_top acc_conv3x3
    # 可选第 7 个参数：TILE（行分块高度）。缺省 4。
    set tile 4
    if {[info exists ::env(HLS_PROBE_TILE)]} { set tile $::env(HLS_PROBE_TILE) }
    add_files -cflags "-I$srcdir -DC_IN=$cin -DC_OUT=$cout -DH_IN=$hin -DW_IN=$win -DPAR_OUT=$par -DTILE=$tile" $cpp
    open_solution -reset "sol_$tag"
    set_part $part
    create_clock -period $clock_ns -name default
    csynth_design
} err]} {
    puts "CSYNTH_FAIL $tag : $err"
    exit 1
}

# ---- 抓关键数字 ----
set rpt [file join $proj "sol_$tag" "syn" "report" "csynth.rpt"]
set dsp "-"; set bram "-"; set lut "-"; set ff "-"; set fmax "-"; set lat "-"; set ii "-"
if {[file exists $rpt]} {
    set fh [open $rpt r]; set body [read $fh]; close $fh

    # 顶层模块那一行（以 "+ acc_conv3x3" 开头）
    # 列序：Modules|Issue|Slack|Lat(cycles)|Lat(ns)|IterLat|Interval|Trip|Pipelined|BRAM|DSP|FF|LUT|URAM
    foreach line [split $body "\n"] {
        if {[regexp {^\s*\|\+ acc_conv3x3\s*\|} $line]} {
            set cells [split $line "|"]
            if {[llength $cells] >= 14} {
                set bram [string trim [lindex $cells 10]]
                set dsp  [string trim [lindex $cells 11]]
                set ff   [string trim [lindex $cells 12]]
                set lut  [string trim [lindex $cells 13]]
                set ii   [string trim [lindex $cells 6]]
                set lat  [string trim [lindex $cells 4]]
            }
        }
        if {[regexp {Estimated\s+Fmax\s*\(MHz\)[^0-9]*([0-9.]+)} $body -> n]} { set fmax $n }
    }
}

puts "CSYNTH_DONE $tag DSP=$dsp BRAM18=$bram LUT=$lut FF=$ff II=$ii Lat=$lat FmaxMHz=$fmax"
close_project
exit 0
