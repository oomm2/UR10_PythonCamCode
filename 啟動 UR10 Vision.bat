@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "VENV=.venv\Scripts\python.exe"
set "APP=ur10_vision_qt_app.py"

if not exist "%APP%" (
    echo [ERROR] 找不到應用程式：%APP%
    pause
    exit /b 1
)

if not exist "%VENV%" (
    echo [SETUP] 正在建立 Python 虛擬環境...
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3.11 -m venv .venv
    ) else (
        where python >nul 2>nul
        if errorlevel 1 (
            echo [ERROR] 找不到 Python 3。請先安裝 Python 3.11。
            pause
            exit /b 1
        )
        python -m venv .venv
    )
    if errorlevel 1 (
        echo [ERROR] 建立虛擬環境失敗。
        pause
        exit /b 1
    )
)

rem Always resolve the pinned requirements. Existing matching packages are reused.
rem Do not use a stale marker that misses requirements.txt updates.
echo [SETUP] 正在核對套件版本...
"%VENV%" -m pip install -r requirements.txt
if errorlevel 1 goto :install_failed

echo 正在啟動 UR10 Vision Control...
"%VENV%" "%APP%"
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
    echo.
    echo [ERROR] 應用程式已結束，錯誤碼：%EXIT_CODE%
    pause
)
endlocal
exit /b %EXIT_CODE%

:install_failed
echo [ERROR] Python 依賴安裝失敗，請檢查網絡連線和 requirements.txt。
pause
endlocal
exit /b 1
