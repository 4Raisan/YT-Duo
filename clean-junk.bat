@echo off
chcp 65001 >nul 2>&1
title YT-Duo - remove leftover test folders

:: =====================================================================
::  Removes the empty downy-test-* / tmp-* folders left behind by early
::  test runs. Their permissions were stamped by tempfile.mkdtemp() before
::  the test helper changed, and only an administrator can take them back.
::
::  Right-click this file -> "Run as administrator".
::
::  Safe: it only removes folders that are EMPTY.
:: =====================================================================

cd /d "%~dp0"

net session >nul 2>&1
if errorlevel 1 (
    echo.
    echo   This must run as administrator.
    echo.
    echo   Right-click clean-junk.bat and choose
    echo   "Run as administrator".
    echo.
    pause
    exit /b 1
)

echo.
echo   Removing leftover test folders...
echo.

set "REMOVED=0"
set "KEPT=0"

for /d %%D in (downy-test-* tmp-*) do (
    if exist "%%D" (
        :: Only remove if it is empty - never touch real data.
        dir /b /a "%%D" 2>nul | findstr /r "^" >nul
        if errorlevel 1 (
            takeown /f "%%D" >nul 2>&1
            icacls "%%D" /grant "%USERNAME%:(OI)(CI)F" >nul 2>&1
            rmdir /s /q "%%D" >nul 2>&1
            if exist "%%D" (
                echo   [X] could not remove %%D
            ) else (
                set /a REMOVED+=1
            )
        ) else (
            echo   [!] kept %%D ^(not empty^)
            set /a KEPT+=1
        )
    )
)

echo.
echo   Removed: %REMOVED%
if not "%KEPT%"=="0" echo   Kept (had files): %KEPT%
echo.
pause
exit /b 0
