# csim_mask.tcl —— mask_preproc 的 C 仿真（逐位比对）
set script_dir [file dirname [info script]]
set root   [file normalize [file join $script_dir .. .. .. ..]]
set srcdir [file join $root src hls mask src]
set tbdir  [file join $root src hls mask tb]
set work   [file join $root build hls_mask]

file mkdir $work
cd $work

open_project -reset mask_csim
set_top mask_preproc
add_files [file join $srcdir mask_preproc.cpp]
add_files -tb [file join $tbdir tb_mask_preproc.cpp]
open_solution -reset sol_mask
set_part xc7z020clg400-2
create_clock -period 10.0 -name default
csim_design -clean
exit