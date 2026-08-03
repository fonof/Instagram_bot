@echo off
chcp 65001 >nul
setlocal EnableExtensions

REM ============================================================
REM  Облегчение ПК перед эмулятором (БЕЗОПАСНО, без убийства системы)
REM  - НЕ закрывает Explorer, драйверы, антивирус целиком
REM  - Раскомментируй только то, что сам готов закрыть
REM ============================================================

title Облегчение ПК перед эмулятором

echo.
echo === Режим высокой производительности (нужен запуск ОТ ИМЕНИ АДМИНИСТРАТОРА) ===
REM Раскомментируй одну строку с GUID схемы "Высокая производительность" под твою систему:
REM powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c

echo.
echo === Закрытие выбранных приложений (раскомментируй нужные строки ниже) ===
REM taskkill /F /IM chrome.exe 2>nul
REM taskkill /F /IM msedge.exe 2>nul
REM taskkill /F /IM firefox.exe 2>nul
REM taskkill /F /IM Telegram.exe 2>nul
REM taskkill /F /IM Discord.exe 2>nul
REM taskkill /F /IM Spotify.exe 2>nul

echo.
echo === OneDrive: пауза синхронизации (если OneDrive установлен) ===
if exist "%LOCALAPPDATA%\Microsoft\OneDrive\OneDrive.exe" (
  start "" /MIN "%LOCALAPPDATA%\Microsoft\OneDrive\OneDrive.exe" /shutdown
  timeout /t 2 /nobreak >nul
)

echo.
echo === Xbox Game Bar (часто мешает оверлеем) — остановка фонового процесса ===
taskkill /F /IM GameBarPresenceWriter.exe 2>nul
taskkill /F /IM GameBar.exe 2>nul

echo.
echo Готово. Запускай эмулятор / start_all.bat
echo.
pause
endlocal
