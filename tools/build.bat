@echo off
setlocal
title MC Hanhua Tool - Build

cd /d "%~dp0.." >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Cannot enter the project root directory.
    pause
    exit /b 1
)

set "PYTHON_CMD="
where py >nul 2>&1
if errorlevel 1 goto try_python
py -3.12 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=py -3.12"
if defined PYTHON_CMD goto python_found
py -3 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=py -3"
if defined PYTHON_CMD goto python_found

:try_python
where python >nul 2>&1
if errorlevel 1 goto python_not_found
set "PYTHON_CMD=python"
goto python_found

:python_not_found
echo [ERROR] Python 3 was not found. Install Python 3.8 or newer first.
pause
exit /b 1

:python_found
echo ================================================
echo     MC Hanhua Tool - Build
echo ================================================
echo.
echo [INFO] Python command: %PYTHON_CMD%

%PYTHON_CMD% -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo [INSTALL] Installing build dependencies...
    %PYTHON_CMD% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Failed to install dependencies.
        pause
        exit /b 1
    )
)

echo [CLEAN] Removing previous build output...
if exist build rmdir /s /q build
if exist dist\MC_Hanhua_Tool.exe del /q dist\MC_Hanhua_Tool.exe

echo [BUILD] Building executable...
%PYTHON_CMD% -m PyInstaller --noconfirm --clean --distpath dist --workpath build MC_Hanhua_Tool.spec
if errorlevel 1 (
    echo.
    echo [ERROR] Build failed.
    pause
    exit /b 1
)

echo.
if exist dist\MC_Hanhua_Tool.exe (
    echo [SUCCESS] Build complete.
    echo Output: dist\MC_Hanhua_Tool.exe
    dir dist\MC_Hanhua_Tool.exe
    echo.
    pause
    exit /b 0
)

echo [ERROR] Build finished but the executable was not found.
pause
exit /b 1
