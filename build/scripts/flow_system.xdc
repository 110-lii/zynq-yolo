######################################################################
# flow_system.xdc —— 全流程固化 overlay 的引脚约束（2026-10-30）
#
# = lcd_probe.xdc（LCD 800x480 + 480x272 + 按键 + sys_clk）
#   + hdmi_video.xdc 的 TMDS 输出脚。两份原文件都来自
#   ref/board/board_pinout_navigator_zynq_io_v3.7.xdc，引脚无冲突。
#
# 只做「相加」，不改任何既有约束 —— 合并 overlay 的时序风险应当由
# 设计本身承担，不能靠在 XDC 里放宽来掩盖。
######################################################################

# lcd_probe.xdc —— LCD 双分辨率探针（板卡 V3.7 引脚）
# 引脚来源：ref/board/board_pinout_navigator_zynq_io_v3.7.xdc（与 display_both.xdc 一致）
set_property -dict {PACKAGE_PIN U18 IOSTANDARD LVCMOS33} [get_ports sys_clk]
create_clock -period 20.000 -name sys_clk [get_ports sys_clk]

set_property -dict {PACKAGE_PIN W18 IOSTANDARD LVCMOS33} [get_ports {lcd_rgb[0]}]
set_property -dict {PACKAGE_PIN W19 IOSTANDARD LVCMOS33} [get_ports {lcd_rgb[1]}]
set_property -dict {PACKAGE_PIN R16 IOSTANDARD LVCMOS33} [get_ports {lcd_rgb[2]}]
set_property -dict {PACKAGE_PIN R17 IOSTANDARD LVCMOS33} [get_ports {lcd_rgb[3]}]
set_property -dict {PACKAGE_PIN W20 IOSTANDARD LVCMOS33} [get_ports {lcd_rgb[4]}]
set_property -dict {PACKAGE_PIN V20 IOSTANDARD LVCMOS33} [get_ports {lcd_rgb[5]}]
set_property -dict {PACKAGE_PIN P18 IOSTANDARD LVCMOS33} [get_ports {lcd_rgb[6]}]
set_property -dict {PACKAGE_PIN N17 IOSTANDARD LVCMOS33} [get_ports {lcd_rgb[7]}]
set_property -dict {PACKAGE_PIN V17 IOSTANDARD LVCMOS33} [get_ports {lcd_rgb[8]}]
set_property -dict {PACKAGE_PIN V18 IOSTANDARD LVCMOS33} [get_ports {lcd_rgb[9]}]
set_property -dict {PACKAGE_PIN T17 IOSTANDARD LVCMOS33} [get_ports {lcd_rgb[10]}]
set_property -dict {PACKAGE_PIN R18 IOSTANDARD LVCMOS33} [get_ports {lcd_rgb[11]}]
set_property -dict {PACKAGE_PIN Y18 IOSTANDARD LVCMOS33} [get_ports {lcd_rgb[12]}]
set_property -dict {PACKAGE_PIN Y19 IOSTANDARD LVCMOS33} [get_ports {lcd_rgb[13]}]
set_property -dict {PACKAGE_PIN P15 IOSTANDARD LVCMOS33} [get_ports {lcd_rgb[14]}]
set_property -dict {PACKAGE_PIN P16 IOSTANDARD LVCMOS33} [get_ports {lcd_rgb[15]}]
set_property -dict {PACKAGE_PIN V16 IOSTANDARD LVCMOS33} [get_ports {lcd_rgb[16]}]
set_property -dict {PACKAGE_PIN W16 IOSTANDARD LVCMOS33} [get_ports {lcd_rgb[17]}]
set_property -dict {PACKAGE_PIN T14 IOSTANDARD LVCMOS33} [get_ports {lcd_rgb[18]}]
set_property -dict {PACKAGE_PIN T15 IOSTANDARD LVCMOS33} [get_ports {lcd_rgb[19]}]
set_property -dict {PACKAGE_PIN Y17 IOSTANDARD LVCMOS33} [get_ports {lcd_rgb[20]}]
set_property -dict {PACKAGE_PIN Y16 IOSTANDARD LVCMOS33} [get_ports {lcd_rgb[21]}]
set_property -dict {PACKAGE_PIN T16 IOSTANDARD LVCMOS33} [get_ports {lcd_rgb[22]}]
set_property -dict {PACKAGE_PIN U17 IOSTANDARD LVCMOS33} [get_ports {lcd_rgb[23]}]

set_property -dict {PACKAGE_PIN N18 IOSTANDARD LVCMOS33} [get_ports lcd_hs]
set_property -dict {PACKAGE_PIN T20 IOSTANDARD LVCMOS33} [get_ports lcd_vs]
set_property -dict {PACKAGE_PIN U20 IOSTANDARD LVCMOS33} [get_ports lcd_de]
set_property -dict {PACKAGE_PIN M20 IOSTANDARD LVCMOS33} [get_ports lcd_bl]
set_property -dict {PACKAGE_PIN P19 IOSTANDARD LVCMOS33} [get_ports lcd_clk]
set_property -dict {PACKAGE_PIN L17 IOSTANDARD LVCMOS33} [get_ports lcd_rst]

# ---- 跨时钟域 ----
# 本设计只有三个时钟，逐个说明每个交叉点凭什么可以按异步处理：
#   clk_fpga_0（PS 83.333）→ pclk33 / pclk9：
#       cfg 控制字，各域两级同步（cfg_s1/s2，第一级 ASYNC_REG）；准静态。
#   pclk33 ↔ pclk9：
#       de_sel 经 de_sync 两级同步后才被 ID 采样使用；另一条是 9 MHz 核心的
#       RGB/DE 经 inout 焊盘回到 pclk33 域的 ID 读回，本身就是异步采样。
#   pclk* → clk_fpga_0：
#       调试读回（id_level/id_toggle/dbg_timing/dbg_tick）由 GPIO 直接采样，
#       只做观测，不参与任何控制判断（可能读到撕裂值，已在记录里写明）。
# 因此三组时钟之间声明为异步；这不是「掩盖违例」，而是把这些交叉点明确定义掉。
set_clock_groups -asynchronous \
    -group [get_clocks clk_fpga_0] \
    -group [get_clocks clk_out1_system_clk_wiz_0] \
    -group [get_clocks clk_out2_system_clk_wiz_0]

# ---- HDMI 域（clk_wiz_hdmi 的 74.255954 / 371.27977）----
# 2026-10-30：HDMI 配置寄存器（gpio_hdmi_cfg）从 pclk 域挪到 FCLK 域，之后
#   clk_fpga_0 → pclk 这条路上只剩 cfg_addr / cfg_pack，两路都在 hdmi_video_top 里
#   过 2 拍同步（cfg_addr_s1/s2、cfg_pack_s1/s2），准静态配置字。
#   pclk → clk_fpga_0 方向：AXI 时钟转换器（axi_cc）自带握手，无需约束。
# ⚠️ 两个 HDMI 时钟必须放在**同一个 group**：serializer_10_to_1 在 pclk_x5 上采样 pclk
#   域的数据，那是真实的同步路径，拆成两组会把串行器的时序检查关掉。
# 时钟名以时序报告为准（BD 会自动加 system_ 前缀和 _0/_1 后缀，别手写）：
#   clk_out1_system_clk_wiz_hdmi_0 / clk_out2_system_clk_wiz_hdmi_0_1 …
set_clock_groups -asynchronous \
    -group [get_clocks clk_fpga_0] \
    -group [get_clocks {clk_out1_system_clk_wiz_hdmi_* clk_out2_system_clk_wiz_hdmi_*}]

# 显式写出的几处（时钟组已覆盖，保留是为了读约束的人不用回头猜）：
#   cfg[5] 选 BUFGMUX、cfg[0] 选 ODDR 相位 —— 准静态，切换瞬间可能有一拍抖动
set_false_path -to [get_pins -hier -filter {NAME =~ *u_clk_mux/S*}]
set_false_path -to [get_pins -hier -filter {NAME =~ *u_oddr_clk/D*}]

#---------------------- 赛前 UI 按键（PL KEY0/KEY1，出自 v3.7 XDC 的 PL_KEY 段）------
set_property -dict {PACKAGE_PIN L14 IOSTANDARD LVCMOS33} [get_ports {key[0]}]
set_property -dict {PACKAGE_PIN K16 IOSTANDARD LVCMOS33} [get_ports {key[1]}]

#---------------------- HDMI 输出（TMDS，板卡 V3.7）----------------------
# 只约束 P 端：IOSTANDARD 为差分标准，Vivado 自动从 P 脚推出 N 脚。
set_property -dict {PACKAGE_PIN J20 IOSTANDARD TMDS_33} [get_ports {tmds_data_p[2]}]
set_property -dict {PACKAGE_PIN K19 IOSTANDARD TMDS_33} [get_ports {tmds_data_p[1]}]
set_property -dict {PACKAGE_PIN G19 IOSTANDARD TMDS_33} [get_ports {tmds_data_p[0]}]
set_property -dict {PACKAGE_PIN J18 IOSTANDARD TMDS_33} [get_ports tmds_clk_p]
