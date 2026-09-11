@echo off
REM Double-click this on Windows.
REM Finds a working Python, then hands off to bootstrap.py.

setlocal enabledelayedexpansion
cd /d "%~dp0"

echo === syllabus_cal ===
echo.

REM `where python` is NOT good enough here. Windows ships stub python.exe and
REM python3.exe "App Execution Aliases" that sit on PATH but only print
REM "Python was not found" and point at the Microsoft Store. So actually run
REM the interpreter and require the output to start with "Python 3". The py
REM launcher is tried first because the stubs don't shadow it.

set "PYCMD="
set "PYOUT="

for /f "usebackq delims=" %%v in (`py -3 --version 2^>^&1`) do set "PYOUT=%%v"
echo !PYOUT! | findstr /b /c:"Python 3" >nul 2>nul
if not errorlevel 1 set "PYCMD=py -3"
if defined PYCMD goto run

for /f "usebackq delims=" %%v in (`python --version 2^>^&1`) do set "PYOUT=%%v"
echo !PYOUT! | findstr /b /c:"Python 3" >nul 2>nul
if not errorlevel 1 set "PYCMD=python"
if defined PYCMD goto run

echo Python 3 isn't installed, or Windows is intercepting it.
echo.
echo Install it from:  https://www.python.org/downloads/
echo.
echo IMPORTANT: on the first screen of the installer, tick the box
echo    "Add python.exe to PATH"
echo at the bottom before clicking "Install Now". Then run this again.
echo.
echo If you already installed Python and still see this, Windows' Microsoft
echo Store shortcut is shadowing it. Turn that off here:
echo    Settings ^> Apps ^> Advanced app settings ^> App execution aliases
echo then switch OFF both "python.exe" and "python3.exe", and run this again.
echo.
pause
exit /b 1

:run
echo Using !PYOUT!
echo.
!PYCMD! bootstrap.py %*
echo.
pause
exit /b 0
