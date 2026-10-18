@echo off
chcp 65001 >nul 2>&1
title DownY
echo.
echo   DownY - Initializing
echo   ====================
echo.

setlocal EnableDelayedExpansion
set "NEED_RESTART=0"
set "FAILED=0"

cd /d "%~dp0"

:: --- Check winget ---
winget --version >nul 2>&1
if errorlevel 1 (
    echo   [X] winget not available
    echo       Install "App Installer" from the Microsoft Store first.
    echo.
    pause
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
    pause
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
    pause
    exit /b 0
)

:: --- Install / update dependencies ---
echo.
echo   Updating yt-dlp...
%PY% -m pip install --upgrade --quiet -r "%~dp0requirements.txt" >nul 2>&1
if errorlevel 1 (
    echo   [!] yt-dlp update failed ^(offline or permission^); using installed copy.
) else (
    echo   [OK] yt-dlp
)

if not "%~1"=="" goto :run
echo.
echo   All ready!
timeout /t 2 /nobreak >nul
cls

:run
echo.
%PY% -m downy %*
set "EXITCODE=%errorlevel%"
echo.
if "%EXITCODE%"=="0" (
    echo   Finished successfully.
) else (
    echo   Finished with errors ^(exit %EXITCODE%^).
)
pause
exit /b %EXITCODE%
