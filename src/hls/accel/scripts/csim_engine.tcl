# ============================================================================
# csim_engine.tcl —— conv_engine 的 C 仿真（单用例）
#
# 与 csynth_engine.tcl 共用同一套参数文件机制（环境变量指向的 tcl 片段），
# 额外需要 cfg_case（用例目录绝对路径）。
#
# 判据：`CSIM_DONE <tag> OK` / `CSIM_FAIL <tag> : <原因>`
#       TB 自己会打印 `TB_CONV_OK ...` / `TB_CONV_FAIL ...`，两层都要看：
#       csim_design 成功 ≠ 比对通过（TB 返回非 0 时 HLS 也可能只当普通输出）。
#
# 沿用探针踩过的坑（别改回去）：
#   1. Tcl 注释只能 `#`
#   2. open_project 不接受含 `:` 的绝对路径 ⇒ 先 cd 再相对路径
#   3. 不用 -tclargs
# ============================================================================

if {![info exists ::env(HLS_ENGINE_PARAMS)]} {
    puts "CSIM_FAIL noenv : 需要环境变量 HLS_ENGINE_PARAMS"
    exit 1
}
set paramfile $::env(HLS_ENGINE_PARAMS)
if {![file exists $paramfile]} {
    puts "CSIM_FAIL noparams : 找不到 $paramfile"
    exit 1
}
source $paramfile

set tag   $cfg_tag
set par   $cfg_par
set th    $cfg_tile_h
set tw    $cfg_tile_w
set cased $cfg_case

if {![file isdirectory $cased]} {
    puts "CSIM_FAIL $tag : 用例目录不存在 $cased"
    exit 1
}

set root     [file normalize [file join [file dirname [info script]] .. .. .. ..]]
set srcdir   [file join $root src hls accel src]
set tbdir    [file join $root src hls accel tb]
set workroot [file join $root build hls_csim]
set part     "xc7z020clg400-2"

file mkdir $workroot
cd $workroot

set proj "sim_$tag"
set cpp  [file join $srcdir conv_engine.cpp]
set tb   [file join $tbdir  tb_conv_engine.cpp]

puts "==============================================================="
puts "== CSIM ENGINE: tag=$tag PAR_OUT=$par TILE_H=$th TILE_W=$tw"
puts "== case=$cased"
puts "==============================================================="

if {[file exists $proj]} { file delete -force $proj }

# 用例目录通过环境变量传给 TB（TB_CASE_DIR），不走 -cflags，避免 Windows 反斜杠转义
set ::env(TB_CASE_DIR) $cased

set cflags "-I$srcdir -I$tbdir -DPAR_OUT=$par -DTILE_H=$th -DTILE_W=$tw"

if {[catch {
    open_project -reset $proj
    set_top conv_engine
    add_files -cflags $cflags $cpp
    add_files -tb -cflags $cflags $tb
    open_solution -reset "sol_$tag"
    set_part $part
    create_clock -period 10.0 -name default
    csim_design
} err]} {
    puts "CSIM_FAIL $tag : $err"
    exit 1
}

puts "CSIM_DONE $tag OK"
close_project
exit 0
