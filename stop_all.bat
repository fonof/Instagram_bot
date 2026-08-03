@echo off
setlocal EnableExtensions

title Instagram Story Bot — остановка

echo Останавливаю процессы бота / Appium / эмулятора...

REM Python (бот)
taskkill /F /IM python.exe >nul 2>&1
taskkill /F /IM pythonw.exe >nul 2>&1

REM Node (Appium)
taskkill /F /IM node.exe >nul 2>&1

REM Эмулятор QEMU / AVD
taskkill /F /IM qemu-system-x86_64.exe >nul 2>&1
taskkill /F /IM qemu-system-x86_64w.exe >nul 2>&1
taskkill /F /IM emulator.exe >nul 2>&1

echo Готово.
pause
endlocal
