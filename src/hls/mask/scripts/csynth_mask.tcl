# csynth_mask.tcl —— mask_preproc 的资源/时序综合
set script_dir [file dirname [info script]]
set root   [file normalize [file join $script_dir .. .. .. ..]]
set srcdir [file join $root src hls mask src]
set work   [file join $root build hls_mask]

file mkdir $work
cd $work

open_project -reset mask_csynth
set_top mask_preproc
add_files [file join $srcdir mask_preproc.cpp]
open_solution -reset sol_mask
set_part xc7z020clg400-2
create_clock -period 10.0 -name default
csynth_design
puts "MASK_CSYNTH_DONE"
exit