@echo off
REM Double-click this on Windows.
REM Sets up the virtualenv on first run, then launches syllabus_cal.

cd /d "%~dp0"

echo === syllabus_cal ===
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo Python isn't installed.
    echo Get it from https://www.python.org/downloads/ then run this again.
    echo Tick "Add Python to PATH" in the installer.
    echo.
    pause
    exit /b 1
)

if not exist ".venv" (
    echo First run - setting up. This takes a minute.
    python -m venv .venv
    if errorlevel 1 goto setupfail
    .venv\Scripts\python.exe -m pip install --quiet --upgrade pip
    .venv\Scripts\python.exe -m pip install --quiet -r requirements.txt
    if errorlevel 1 goto setupfail
    echo Setup done.
    echo.
)

if not exist ".env" (
    copy .env.example .env >nul
    echo Created .env - open it in Notepad and paste your Anthropic API key
    echo after ANTHROPIC_API_KEY= , then run this again.
    echo.
    pause
    exit /b 1
)

findstr /r "ANTHROPIC_API_KEY=." .env >nul
if errorlevel 1 (
    echo Your .env has no Anthropic API key yet.
    echo Open .env, paste your key after ANTHROPIC_API_KEY= , then run this again.
    echo.
    pause
    exit /b 1
)

if "%~1"=="" (
    .venv\Scripts\python.exe -m syllabus_cal
    echo.
    echo To read a screenshot, type this then drag the image onto this window:
    echo     start.bat parse
) else (
    .venv\Scripts\python.exe -m syllabus_cal %*
)

echo.
pause
exit /b 0

:setupfail
echo Setup failed. Check your internet connection and try again.
echo.
pause
exit /b 1
