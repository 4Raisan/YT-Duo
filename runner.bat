@echo off
chcp 65001 >nul 2>&1
title YT-Duo
echo.
echo   YT-Duo - Initializing
echo   ====================
echo.

setlocal EnableDelayedExpansion
set "NEED_RESTART=0"
set "FAILED=0"

:: A single place that decides whether to hold the window open. Set
:: NO_PAUSE=1 (or pass --no-pause) for scripted, piped or scheduled runs.
goto :main

:hold
if defined NO_PAUSE exit /b 0
pause
exit /b 0

:main

cd /d "%~dp0"

:: --no-pause makes the launcher scriptable: it never waits for a keypress.
:: It is stripped from the arguments so it cannot leak into the app's CLI.
set "ARGS="
set "NO_PAUSE="
for %%a in (%*) do (
    if /i "%%a"=="--no-pause" (
        set "NO_PAUSE=1"
    ) else (
        set "ARGS=!ARGS! %%a"
    )
)

:: --- Check winget ---
winget --version >nul 2>&1
if errorlevel 1 (
    echo   [X] winget not available
    echo       Install "App Installer" from the Microsoft Store first.
    echo.
    call :hold
    exit /b 1
)

:: --- Check Python (prefer the py launcher; the Store alias can lie) ---
set "PY="
py -3 --version >nul 2>&1
if not errorlevel 1 (
    set "PY=py -3"
    for /f "tokens=*" %%v in ('py -3 --version 2^>^&1') do echo   [OK] %%v
) else (
    python --version >nul 2>&1
    if not errorlevel 1 (
        python -c "import sys" >nul 2>&1
        if not errorlevel 1 (
            set "PY=python"
            for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo   [OK] %%v
        )
    )
)
if not defined PY (
    echo   [-] Python not found, installing...
    winget install Python.Python.3.12 --accept-package-agreements --accept-source-agreements --silent
    if errorlevel 1 (
        echo   [X] Python install FAILED.
        set "FAILED=1"
    ) else (
        echo   [+] Python installed
        set "NEED_RESTART=1"
    )
)

:: --- Check pip ---
if defined PY (
    %PY% -m pip --version >nul 2>&1
    if errorlevel 1 (
        echo   [-] pip not found, bootstrapping...
        %PY% -m ensurepip --upgrade >nul 2>&1
        if errorlevel 1 (
            echo   [X] pip bootstrap FAILED.
            set "FAILED=1"
        ) else (
            echo   [+] pip installed
        )
    ) else (
        echo   [OK] pip
    )
)

:: --- Check ffmpeg ---
where ffmpeg >nul 2>&1
if errorlevel 1 (
    if exist "%USERPROFILE%\ffmpeg.exe" (
        echo   [OK] ffmpeg  ^(user home^)
    ) else if exist "%~dp0ffmpeg.exe" (
        echo   [OK] ffmpeg  ^(project folder^)
    ) else (
        echo   [-] ffmpeg not found, installing...
        winget install Gyan.FFmpeg --accept-package-agreements --accept-source-agreements --silent
        if errorlevel 1 (
            echo   [X] ffmpeg install FAILED. Download it from https://www.gyan.dev/ffmpeg/builds/
            set "FAILED=1"
        ) else (
            echo   [+] ffmpeg installed
            set "NEED_RESTART=1"
        )
    )
) else (
    echo   [OK] ffmpeg
)

:: --- Check deno (yt-dlp JS runtime) ---
where deno >nul 2>&1
if errorlevel 1 (
    echo   [-] deno not found, installing...
    winget install DenoLand.Deno --accept-package-agreements --accept-source-agreements --silent
    if errorlevel 1 (
        echo   [!] deno install failed - downloads may fail YouTube's bot check.
    ) else (
        echo   [+] deno installed
        set "NEED_RESTART=1"
    )
) else (
    echo   [OK] deno
)

if "%FAILED%"=="1" (
    echo.
    echo   ==========================================
    echo    Setup incomplete. Fix the errors above.
    echo   ==========================================
    echo.
    call :hold
    exit /b 1
)

:: --- Restart needed? ---
if "%NEED_RESTART%"=="1" (
    echo.
    echo   ==========================================
    echo    New software was installed.
    echo    Close this window and run runner.bat again
    echo    so PATH changes take effect.
    echo   ==========================================
    echo.
    call :hold
    exit /b 0
)

:: --- Install / update dependencies ---
:: Only touch pip when yt-dlp is actually missing. An unconditional
:: --upgrade blocks on a slow or offline connection, which made this
:: launcher appear frozen for minutes at a time.
echo.
%PY% -c "import yt_dlp" >nul 2>&1
if errorlevel 1 (
    echo   Installing yt-dlp...
    %PY% -m pip install --upgrade --quiet -r "%~dp0requirements.txt" >nul 2>&1
    if errorlevel 1 (
        echo   [!] yt-dlp install failed ^(offline or permission^).
        echo       The app will start but downloads will not work.
    ) else (
        echo   [OK] yt-dlp
    )
) else (
    echo   [OK] yt-dlp
)

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
echo       runner.bat cli
echo.
timeout /t 2 /nobreak >nul
cls
%PY% -m downy --gui
set "EXITCODE=!errorlevel!"
goto :done

:run_cli
%PY% -m downy!ARGS!
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
