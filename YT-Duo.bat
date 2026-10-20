@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion
title YT-Duo
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
set "YTDUO_HOME=%~dp0"
set "NEED_RESTART=0"
set "ARGS="
for %%a in (%*) do (
    if /i "%%a"=="--no-pause" (set "NO_PAUSE=1") else (set "ARGS=!ARGS! %%a")
)
echo.
echo   YT-Duo - Checking dependencies

goto :main
:hold
if defined NO_PAUSE exit /b 0
pause
exit /b 0

:winget_required
winget --version >nul 2>&1
if errorlevel 1 (
    echo   [X] Automatic setup needs App Installer from the Microsoft Store.
    echo       Or follow Releases\README.md for manual installation.
    exit /b 1
)
exit /b 0

:failed
echo   [X] Setup incomplete. Fix the error above and run again.
call :hold
exit /b 1

:main
set "PY="
py -3 -c "import sys; sys.exit(sys.version_info < (3,10))" >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if not defined PY (
    python -c "import sys; sys.exit(sys.version_info < (3,10))" >nul 2>&1
    if not errorlevel 1 set "PY=python"
)
if not defined PY (
    call :winget_required
    if errorlevel 1 goto :failed
    winget install --id Python.Python.3.12 --exact --accept-package-agreements --accept-source-agreements --silent
    if errorlevel 1 goto :failed
    echo   Python installed. Close this window and run YT-Duo.bat again.
    call :hold
    exit /b 0
)
%PY% -c "import tkinter" >nul 2>&1
if errorlevel 1 (
    echo   [X] Python needs Tkinter. Repair Python with Tcl/Tk enabled.
    goto :failed
)
%PY% -m pip --version >nul 2>&1
if errorlevel 1 (
    %PY% -m ensurepip --upgrade
    if errorlevel 1 goto :failed
)
%PY% -c "import yt_dlp.version, yt_dlp_ejs; assert tuple(map(int, yt_dlp.version.__version__.split('.'))) >= (2026,8,19)" >nul 2>&1
if errorlevel 1 (
    echo   Installing yt-dlp and YouTube JavaScript helpers...
    %PY% -m pip install -r "%~dp0requirements.txt"
    if errorlevel 1 goto :failed
)
%PY% -c "from ytduo.engine import find_ffmpeg, find_ffprobe; assert find_ffmpeg() and find_ffprobe()" >nul 2>&1
if errorlevel 1 (
    call :winget_required
    if errorlevel 1 goto :failed
    echo   Installing FFmpeg and FFprobe...
    winget install --id Gyan.FFmpeg --exact --accept-package-agreements --accept-source-agreements --silent
    if errorlevel 1 goto :failed
    set "NEED_RESTART=1"
)
%PY% -c "from ytduo.dependencies import find_deno; assert find_deno()" >nul 2>&1
if errorlevel 1 (
    call :winget_required
    if errorlevel 1 goto :failed
    echo   Installing Deno for YouTube challenges...
    winget install --id DenoLand.Deno --exact --accept-package-agreements --accept-source-agreements --silent
    if errorlevel 1 goto :failed
    set "NEED_RESTART=1"
)
if "%NEED_RESTART%"=="1" (
    echo   Tools installed. Close this window and run YT-Duo.bat again to refresh PATH.
    call :hold
    exit /b 0
)
%PY% -m ytduo --check-dependencies
if errorlevel 1 goto :failed

:: Dispatch on the cleaned argument list:
::   (nothing)      -> open the window
::   cli <args>     -> terminal version with <args>
::   <args>         -> terminal version (so flags like --version work)
set "FIRST=!ARGS!"
for /f "tokens=1 delims= " %%f in ("!FIRST!") do set "FIRST=%%f"

if "!ARGS!"=="" goto :run_gui
if /i "!FIRST!"=="cli" (
    set "ARGS=!ARGS: cli=!"
    set "ARGS=!ARGS:cli=!"
    goto :run_cli
)

:: Bare invocation means a person double-clicked the file.
echo.
goto :run_cli

:run_gui
echo.
echo   All ready!
echo.
echo   Opening the window. For the terminal version run:
echo       YT-Duo.bat cli
echo.
timeout /t 2 /nobreak >nul
cls
%PY% -m ytduo --gui
set "EXITCODE=!errorlevel!"
goto :done

:run_cli
%PY% -m ytduo!ARGS!
set "EXITCODE=!errorlevel!"

:done
echo.
if "%EXITCODE%"=="0" (
    echo   Finished successfully.
) else (
    echo   Finished with errors ^(exit %EXITCODE%^).
)
:: Hold the window only on a hands-on run. `set "NO_PAUSE=1"` before calling
:: (or passing --no-pause) keeps it non-interactive for scripts, tests and
:: scheduled jobs, where a keypress would never arrive.
if defined NO_PAUSE exit /b %EXITCODE%
call :hold
exit /b %EXITCODE%

