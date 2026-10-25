# export_mask.tcl —— 把 mask_preproc 打包成 IP（VLNV: zynq-yolo:hls:mask_preproc:1.0）
set script_dir [file dirname [info script]]
set root   [file normalize [file join $script_dir .. .. .. ..]]
set srcdir [file join $root src hls mask src]
set work   [file join $root build hls_export]
set ipdir  [file join $root build ip_repo]

file mkdir $work
file mkdir $ipdir
cd $work

if {[file exists exp_mask]} { file delete -force exp_mask }
open_project -reset exp_mask
set_top mask_preproc
add_files [file join $srcdir mask_preproc.cpp]
open_solution -reset sol_mask
set_part xc7z020clg400-2
create_clock -period 10.0 -name default
csynth_design
config_export -format ip_catalog -rtl verilog -vendor zynq-yolo -library hls \
    -version "1.0" -display_name "mask_preproc" \
    -description "streaming white/motion mask preprocessor (BGR in, 1B/px out)" \
    -output [file join $ipdir mask_preproc]
export_design -format ip_catalog
puts "MASK_EXPORT_DONE"
exit