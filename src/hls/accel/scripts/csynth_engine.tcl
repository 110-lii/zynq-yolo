# ============================================================================
# csynth_engine.tcl —— conv_engine.cpp 的资源/时序综合（**单配置**版）
#
# 与 csynth_one.tcl（探针 acc_conv3x3 专用）的区别：
#   探针把 C_IN/C_OUT/H/W 全做成编译期 #define；conv_engine 的几何是**运行期寄存器**
#   （64 种层形状不可能逐层综合），所以这里只传三个实现参数 PAR_OUT / TILE_H / TILE_W。
#
# 沿用探针已经踩过的三个坑（别改回去）：
#   1. Tcl 注释只能 `#`；C 风格 `//` ⇒ invalid command name "//"
#   2. open_project 不接受含 `:` 的绝对路径 ⇒ 先 cd 再用相对路径
#   3. 不用 -tclargs（会吃掉后续 token）⇒ 参数走环境变量指向的 tcl 片段
#
# 判据：`CSYNTH_DONE <tag> DSP=.. BRAM18=.. LUT=.. FF=.. II=.. Lat=.. FmaxMHz=..`
#       失败：`CSYNTH_FAIL <tag> : <原因>`
# ============================================================================

if {![info exists ::env(HLS_ENGINE_PARAMS)]} {
    puts "CSYNTH_FAIL noenv : 需要环境变量 HLS_ENGINE_PARAMS 指向参数 tcl"
    exit 1
}
set paramfile $::env(HLS_ENGINE_PARAMS)
if {![file exists $paramfile]} {
    puts "CSYNTH_FAIL noparams : 找不到 $paramfile"
    exit 1
}
source $paramfile

set tag   $cfg_tag
set par   $cfg_par
set th    $cfg_tile_h
set tw    $cfg_tile_w
# 可选第 5 个参数：累加器分区维度实验开关（0=只按 PAR_OUT 分区，1=全展开落寄存器）
if {[info exists cfg_acc_full]} { set accfull $cfg_acc_full } else { set accfull 0 }

set root     [file normalize [file join [file dirname [info script]] .. .. .. ..]]
set srcdir   [file join $root src hls accel src]
set workroot [file join $root build hls_engine]
set part     "xc7z020clg400-2"
set clock_ns 10.0

file mkdir $workroot
cd $workroot

set proj "eng_$tag"
set cpp  [file join $srcdir conv_engine.cpp]

puts "==============================================================="
puts "== CSYNTH ENGINE: tag=$tag PAR_OUT=$par TILE_H=$th TILE_W=$tw ACC_FULL=$accfull"
puts "==============================================================="

if {[file exists $proj]} { file delete -force $proj }

if {[catch {
    open_project -reset $proj
    set_top conv_engine
    add_files -cflags "-I$srcdir -DPAR_OUT=$par -DTILE_H=$th -DTILE_W=$tw -DACC_FULL=$accfull" $cpp
    open_solution -reset "sol_$tag"
    set_part $part
    create_clock -period $clock_ns -name default
    csynth_design
} err]} {
    puts "CSYNTH_FAIL $tag : $err"
    exit 1
}

# ---- 抓 CSYNTH 报告的关键数字 ----
# 列序（HLS 2020.2 csynth.rpt 顶层行）：
#   |+ Module | Issue | Slack | Lat(cyc) | Lat(ns) | IterLat | Interval | Trip | Pipelined | BRAM | DSP | FF | LUT | URAM |
set rpt [file join $proj "sol_$tag" "syn" "report" "csynth.rpt"]
set dsp "-"; set bram "-"; set lut "-"; set ff "-"; set fmax "-"; set lat "-"; set ii "-"
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
                # ★ 下面三行原先写成 `string trim $cells 13` —— Tcl 的 string trim
                #   第二个参数是**要剥掉的字符集**，不是下标。于是 II/Lat/LUT 被印成
                #   「整个字段列表去掉首尾的 1 和 3」，形如 `{    } {+ conv_engine ...}`。
                #   危害：这串值**不是空的**，DONE 令牌照样打印、看门狗照样认为成功,
                #   只有真去读那几列的人才会发现它根本不是数字。
                #   ⇒ 教训：令牌里的字段必须**能被程序直接 int() 解析**，否则等于没印。
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
