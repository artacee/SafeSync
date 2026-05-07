@echo off

:: -- Keep window open no matter what ------------------------------------------
:: If already relaunched, skip straight to the script body
if "%~1"=="KEEP" goto :main
:: First run: relaunch inside cmd /k so window never auto-closes
cmd /k "%~f0" KEEP
exit /b

:main

setlocal
title Photo Organiser
color 0A

echo.
echo  ============================================================
echo               PHOTO / VIDEO ORGANISER
echo  ============================================================
echo.

:: -- 1. Check Python ----------------------------------------------------------
python --version >nul 2>&1
if errorlevel 1 (
    color 0C
    echo  [ERROR] Python is not installed or not on your PATH.
    echo.
    echo  Please download Python from:
    echo    https://www.python.org/downloads/
    echo.
    echo  During install, tick "Add Python to PATH"  before clicking Install.
    echo.
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo  [OK] %%v found.
echo.

:: -- 2. Check pip and install libraries ---------------------------------------
echo  Installing required libraries (Pillow, exifread)...
echo  (This only downloads once; subsequent runs skip it.)
echo.
pip install --quiet --upgrade Pillow exifread pillow-heif
if errorlevel 1 (
    color 0C
    echo.
    echo  [ERROR] Could not install libraries. Check your internet connection.
    pause
    exit /b 1
)
echo  [OK] Libraries ready.
echo.

:: -- 3. Check script file -----------------------------------------------------
if not exist "%~dp0photo_organiser.py" (
    color 0C
    echo  [ERROR] photo_organiser.py not found next to this .bat file.
    echo  Make sure both files are in the same folder.
    echo.
    pause
    exit /b 1
)
echo.

:: -- 6. Menu ------------------------------------------------------------------
echo  ============================================================
echo   CHOOSE HOW TO RUN:
echo.
echo   [1]  DRY RUN first  (safe preview - nothing is copied)
echo.
echo   [2]  LIVE RUN       (copies files to destination)
echo.
echo   [3]  SCAN FOR CORRUPT FILES  (checks existing folders for broken files)
echo.
echo   [4]  EXIT
echo  ============================================================
echo.
set /p choice=" Enter 1, 2, 3 or 4: "

if "%choice%"=="1" goto dryrun
if "%choice%"=="2" goto liverun
if "%choice%"=="3" goto corruptscan
if "%choice%"=="4" exit /b 0

echo  Invalid choice. Please re-run and enter 1, 2, 3 or 4.
pause
exit /b 1

:: -- Dry run ------------------------------------------------------------------
:dryrun
echo.
echo  Starting DRY RUN (safe preview - nothing is copied)...
echo.
python "%~dp0photo_organiser.py" --dry-run
goto done

:: -- Live run -----------------------------------------------------------------
:liverun
echo.
echo  LIVE RUN selected - files will be COPIED.
echo.
echo  Originals in the Source folder are NOT touched.
echo  Press Ctrl+C at any time to stop safely.
echo  The script can be re-run and will skip already-copied files.
echo.
pause

python "%~dp0photo_organiser.py"
goto done

:: -- Corrupt scan -------------------------------------------------------------
:corruptscan
echo.
echo  CORRUPT SCAN selected - checking an existing folder for broken files...
echo.
python "%~dp0photo_organiser.py" --check-corrupt
goto done

:: -- Done ---------------------------------------------------------------------
:done
echo.
echo  ============================================================
echo   Finished!
echo  ============================================================
echo.
pause
