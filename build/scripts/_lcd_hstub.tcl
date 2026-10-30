# LCD 硬件自证 bit（会话 017）：不接 PS、不接 BRAM，只证明屏/排线/引脚/时钟
set root D:/zynq-yolo
set out  [file join $root build vivado lcd_hstub]
file mkdir $out

create_project -force lcd_hstub [file join $out proj] -part xc7z020clg400-2
add_files -norecurse [file join $root src rtl io lcd lcd_hstub.v]
add_files -norecurse "D:/FPGA/A/4_Source_Code/1_FPGA_Design/ZYNQ_7020_FPGA/ZYNQ_7020_FPGA/21_lcd_rgb_colorbar/rtl/lcd_driver.v"
set_property top lcd_hstub [current_fileset]
add_files -fileset constrs_1 -norecurse [file join $out pins.xdc]
set_property target_language Verilog [current_project]

launch_runs impl_1 -to_step write_bitstream -jobs 8
wait_on_run impl_1
set st [get_property STATUS [get_runs impl_1]]
puts "IMPL_STATUS $st"
if {[string first "write_bitstream Complete" $st] < 0} {
    puts "LCD_HSTUB_FAIL $st"
    return
}
set bit [glob -nocomplain [file join $out proj lcd_hstub.runs impl_1 *.bit]]
file copy -force [lindex $bit 0] [file join $out lcd_hstub.bit]
puts "LCD_HSTUB_BIT_OK [file join $out lcd_hstub.bit]"
