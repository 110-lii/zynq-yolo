# colorbar: 官方 21_lcd_rgb_colorbar 原版（50 MHz sys_clk，25 MHz pclk，DE 模式）
set root  D:/zynq-yolo
set src   "D:/FPGA/A/4_Source_Code/1_FPGA_Design/ZYNQ_7020_FPGA/ZYNQ_7020_FPGA/21_lcd_rgb_colorbar"
set out   [file join $root build vivado colorbar]
file mkdir $out
read_verilog [list \
    [file join $src rtl clk_div.v] \
    [file join $src rtl rd_id.v] \
    [file join $src rtl lcd_display.v] \
    [file join $src rtl lcd_driver.v] \
    [file join $src rtl lcd_rgb_colorbar.v] ]
read_xdc [file join $src prj lcd_rgb_colorbar.srcs constrs_1 new lcd_rgb_colorbar.xdc]
synth_design -top lcd_rgb_colorbar -part xc7z020clg400-2
opt_design
place_design
route_design
report_timing_summary -file [file join $out timing.rpt]
write_checkpoint -force [file join $out colorbar_routed.dcp]
write_bitstream -force [file join $out colorbar_official.bit]
puts "COLORBAR_BIT_OK [file join $out colorbar_official.bit]"
