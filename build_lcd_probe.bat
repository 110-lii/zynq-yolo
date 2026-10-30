@echo off
setlocal
cd /d D:\zynq-yolo
if not exist build\vivado\logs mkdir build\vivado\logs
echo === LCD probe build: 800x480@33.333 + 480x272@9.000 ===
call "D:\Xilinx\Vivado\2020.2\bin\vivado.bat" -mode batch -source build\scripts\bd_lcd_probe.tcl -log build\vivado\logs\lcd_probe_build.log -journal build\vivado\logs\lcd_probe_build.jou
echo.
echo === EXIT CODE %ERRORLEVEL% ===
if exist board\overlay\lcd_probe.bit (
  echo OK board\overlay\lcd_probe.bit
  for %%F in (board\overlay\lcd_probe.bit) do echo SIZE %%~zF
) else (
  echo FAIL no board\overlay\lcd_probe.bit
)
pause
