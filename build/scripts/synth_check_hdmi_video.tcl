# synth_check_hdmi_video.tcl —— 只跑 hdmi_video_top 的 out-of-context 综合（约 2 min）
#
# 用途：改 RTL 后先用它确认「BRAM 有没有被推断出来、资源有没有爆」，
# 不必每次都走完整 BD + impl（后者 10+ min，而且资源问题要到 place 才报 UTLZ-1）。
# 判据：打印 SYNTH_CHECK_OK + LUT/FF/BRAM/DSP 数字。
set root [file normalize [file join [file dirname [info script]] .. ..]]
set rtl  [file join $root src rtl io hdmi]
create_project -force -in_memory -part xc7z020clg400-2
add_files -norecurse [list \
    [file join $rtl asyn_rst_syn.v] \
    [file join $rtl dvi_encoder.v] \
    [file join $rtl dvi_transmitter_top.v] \
    [file join $rtl serializer_10_to_1.v] \
    [file join $rtl video_driver.v] \
    [file join $rtl fb_scanout.v] \
    [file join $rtl hdmi_video_top.v] \
]
synth_design -top hdmi_video_top -part xc7z020clg400-2 -mode out_of_context
report_utilization -return_string
set lut  [llength [get_cells -hier -filter {PRIMITIVE_GROUP == LUT}]]
set ff   [llength [get_cells -hier -filter {PRIMITIVE_GROUP == FD}]]
set bram [llength [get_cells -hier -filter {REF_NAME =~ RAMB*}]]
set dsp  [llength [get_cells -hier -filter {REF_NAME =~ DSP48*}]]
puts "SYNTH_CHECK_OK LUT_CELLS=$lut FF_CELLS=$ff BRAM=$bram DSP=$dsp"
if {$bram < 4} { puts "SYNTH_CHECK_WARN BRAM 只有 $bram 个 —— 行缓存可能又没推断成 BRAM" }