@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "FLOWER_SETUP=%~dp0app\windows_setup.py"
if not exist "%FLOWER_SETUP%" set "FLOWER_SETUP=%~dp0..\windows_setup.py"
set "FLOWER_PYTHON="
for /f "usebackq delims=" %%P in (`powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0find-python.ps1" 2^>nul`) do set "FLOWER_PYTHON=%%P"
if not defined FLOWER_PYTHON goto try_launcher
"%FLOWER_PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 goto try_launcher
"%FLOWER_PYTHON%" -X utf8 "%FLOWER_SETUP%" %*
set "FLOWER_RESULT=%ERRORLEVEL%"
goto done
:try_launcher
where py >nul 2>&1
if errorlevel 1 goto try_python
py -3 -X utf8 "%FLOWER_SETUP%" %*
set "FLOWER_RESULT=%ERRORLEVEL%"
goto done
:try_python
where python >nul 2>&1
if errorlevel 1 goto missing_python
python -X utf8 "%FLOWER_SETUP%" %*
set "FLOWER_RESULT=%ERRORLEVEL%"
goto done
:missing_python
echo Please install Python 3.11 or newer from https://www.python.org/downloads/windows/
echo Enable the Python launcher or add Python to PATH, then run this file again.
set "FLOWER_RESULT=1"
:done
echo.
if not "%FLOWER_RESULT%"=="0" echo The operation needs attention. Exit code: %FLOWER_RESULT%
pause
exit /b %FLOWER_RESULT%
