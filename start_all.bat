@echo off
setlocal EnableExtensions

REM ============================================================
REM  Подстрой под свой ПК (имя AVD из Android Studio Device Manager)
REM ============================================================
set "AVD_NAME=Pixel_6_API_34"
set "APPIUM_PORT=4723"

REM Папка проекта = папка, где лежит этот bat
set "PROJ=%~dp0"
if "%PROJ:~-1%"=="\" set "PROJ=%PROJ:~0,-1%"

set "VENV_ACT=%PROJ%\.venv\Scripts\activate.bat"
set "EMULATOR=%LOCALAPPDATA%\Android\Sdk\emulator\emulator.exe"
set "WAIT_EMULATOR_SEC=45"

title Instagram Story Bot — запуск

where appium >nul 2>&1
if errorlevel 1 (
  echo [ОШИБКА] appium не в PATH. Установи: npm i -g appium
  echo         и открой окно cmd заново.
  pause
  exit /b 1
)

if not exist "%EMULATOR%" (
  echo [ОШИБКА] Не найден emulator.exe:
  echo          %EMULATOR%
  echo Установи Android SDK Emulator или поправь путь в этом bat.
  pause
  exit /b 1
)

if not exist "%VENV_ACT%" (
  echo [ОШИБКА] Нет venv: %VENV_ACT%
  echo Создай: python -m venv .venv
  echo Затем:  pip install -r requirements.txt
  pause
  exit /b 1
)

echo [1/3] Запуск Appium на порту %APPIUM_PORT%...
start "Appium" cmd /k "appium -p %APPIUM_PORT%"

timeout /t 5 /nobreak >nul

echo [2/3] Запуск эмулятора AVD "%AVD_NAME%"...
start "Android Emulator" "%EMULATOR%" -avd %AVD_NAME% -no-snapshot-save

echo Ожидание загрузки эмулятора ~%WAIT_EMULATOR_SEC% сек...
timeout /t %WAIT_EMULATOR_SEC% /nobreak >nul

echo [3/3] Запуск бота...
start "Instagram Story Bot" cmd /k "cd /d \"%PROJ%\" && call \"%VENV_ACT%\" && python main.py"

echo.
echo Готово. Три окна: Appium, Emulator, Bot.
echo Проверка: adb devices
echo.
pause
endlocal
