@echo off
setlocal
title MC Hanhua Tool - Smoke Test

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
echo     MC Hanhua Tool - Smoke Test
echo ================================================
echo.
echo [TEST] Compiling Python sources...
%PYTHON_CMD% -m compileall -q src
if errorlevel 1 goto test_failed
echo [OK] Source compilation passed.

echo [TEST] Importing application modules...
%PYTHON_CMD% -c "import sys; sys.path.insert(0, 'src'); import main, gui, mod_scanner, translator, resourcepack, ftbquests; print('[OK] Import test passed')"
if errorlevel 1 goto test_failed

echo [TEST] Running FTB Quests self-test...
%PYTHON_CMD% -c "import sys; sys.path.insert(0, 'src'); import ftbquests; ftbquests.test_ftbquests(); print('[OK] FTB Quests test passed')"
if errorlevel 1 goto test_failed

echo [TEST] Validating the configuration template...
%PYTHON_CMD% -c "import json; data=json.load(open('config_template.json', encoding='utf-8')); assert data['translation_settings']['use_cache'] is True; print('[OK] Configuration test passed')"
if errorlevel 1 goto test_failed

echo.
echo [SUCCESS] All smoke tests passed.
pause
exit /b 0

:test_failed
echo.
echo [ERROR] Smoke test failed.
pause
exit /b 1
