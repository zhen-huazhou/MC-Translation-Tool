@echo off
setlocal
title MC Hanhua Tool - Quick Start

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
%PYTHON_CMD% -c "import tkinter" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] This Python installation does not include tkinter.
    pause
    exit /b 1
)

%PYTHON_CMD% -c "import requests" >nul 2>&1
if errorlevel 1 (
    echo [INSTALL] Installing runtime dependencies...
    %PYTHON_CMD% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Failed to install dependencies.
        pause
        exit /b 1
    )
)

echo [START] Launching MC Hanhua Tool...
%PYTHON_CMD% src\main.py
if errorlevel 1 (
    echo.
    echo [ERROR] The program exited with an error.
    pause
    exit /b 1
)

exit /b 0
