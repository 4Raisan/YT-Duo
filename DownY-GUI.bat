@echo off
chcp 65001 >nul 2>&1
title DownY

:: =====================================================================
::  DownY - desktop window launcher
::
::  Double-click this file to open the GUI. No console window stays open
::  behind the app: it relaunches through pythonw and exits.
::
::  If Python is missing, run runner.bat once to install everything.
:: =====================================================================

setlocal EnableDelayedExpansion

cd /d "%~dp0"

:: --- Find a usable interpreter -------------------------------------
:: pythonw.exe is what keeps the console hidden. Note the single quotes
:: around pythonw.exe inside the -c expression: double quotes would end
:: the for /f command string early and the detection would silently fail.
set "PYW="
set "PY="

py -3 --version >nul 2>&1
if not errorlevel 1 (
    set "PY=py -3"
    for /f "delims=" %%p in ('py -3 -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))" 2^>^&1') do (
        if exist "%%p" if not defined PYW set "PYW=%%p"
    )
)

if not defined PYW (
    python --version >nul 2>&1
    if not errorlevel 1 (
        python -c "import sys" >nul 2>&1
        if not errorlevel 1 (
            set "PY=python"
            for /f "delims=" %%p in ('python -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))" 2^>^&1') do (
                if exist "%%p" if not defined PYW set "PYW=%%p"
            )
        )
    )
)

:: Last resort: pythonw somewhere on PATH.
if not defined PYW (
    where pythonw >nul 2>&1
    if not errorlevel 1 (
        for /f "delims=" %%p in ('where pythonw 2^>nul') do (
            if not defined PYW set "PYW=%%p"
        )
    )
)

if not defined PYW goto :no_python
if not defined PY set "PY=python"

:: --- Check the window toolkit is present ---------------------------
%PY% -c "import tkinter" >nul 2>&1
if errorlevel 1 goto :no_tkinter

:: --- First-run setup, but never block the window ---------------------
:: The GUI reports a missing ffmpeg or a broken yt-dlp itself, with a
:: readable dialog. So setup is best-effort: a slow or offline pip must not
:: leave you staring at nothing. Only run it when yt-dlp is actually absent.
%PY% -c "import yt_dlp" >nul 2>&1
if errorlevel 1 (
    echo   Installing dependencies, this may take a minute...
    %PY% -m pip --version >nul 2>&1
    if errorlevel 1 (
        %PY% -m ensurepip --upgrade >nul 2>&1
    )
    %PY% -m pip install --upgrade --quiet -r "%~dp0requirements.txt" >nul 2>&1
)

:: --- Launch ---------------------------------------------------------
:: The empty "" is the window title start requires. This window closes on
:: its own right away; the GUI carries on without a console behind it.
start "" "%PYW%" -m downy --gui
exit /b 0

:: =====================================================================
:no_python
echo.
echo   Python was not found, so the window cannot open.
echo.
echo   Run runner.bat once - it installs Python, ffmpeg and yt-dlp -
echo   then double-click this file again.
echo.
pause
exit /b 1

:no_tkinter
echo.
echo   Python is installed but its tkinter module is missing,
echo   so the window cannot open.
echo.
echo   Re-run the Python installer and enable "tcl/tk and IDLE".
echo   Meanwhile runner.bat still works from the terminal.
echo.
pause
exit /b 1
