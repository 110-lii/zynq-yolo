@echo off
setlocal
cd /d D:\zynq-yolo
if not exist build\vivado\logs mkdir build\vivado\logs
echo === flow_system build: LCD 800x480/480x272 + HDMI 1280x720 + keys (one bitstream) ===
call "D:\Xilinx\Vivado\2020.2\bin\vivado.bat" -mode batch -source build\scripts\bd_flow_system.tcl -log build\vivado\logs\flow_system_build.log -journal build\vivado\logs\flow_system_build.jou
echo.
echo === EXIT CODE %ERRORLEVEL% ===
if exist board\overlay\flow_system.bit (
  echo OK board\overlay\flow_system.bit
  for %%F in (board\overlay\flow_system.bit) do echo SIZE %%~zF
) else (
  echo FAIL no board\overlay\flow_system.bit
)
pause
