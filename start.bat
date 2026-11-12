@echo off
setlocal
title Roomwalk
cd /d "%~dp0"

rem Find a Python between 3.10 and 3.14 (3.15 is too new for the camera-tracking part).
set "PY="
for %%V in (3.14 3.13 3.12 3.11 3.10) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>nul && set "PY=py -%%V"
  )
)
if not defined PY (
  python -c "import sys; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] <= (3, 14) else 1)" >nul 2>nul && set "PY=python"
)
if not defined PY goto :nopython

if exist ".venv\installed.txt" goto :run
echo.
echo  Setting up Roomwalk for the first time.
echo  This downloads a few hundred MB and takes a few minutes. Please wait...
echo.
if not exist ".venv\Scripts\python.exe" %PY% -m venv .venv
if errorlevel 1 goto :failed
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :failed
echo ok> ".venv\installed.txt"

:run
".venv\Scripts\python.exe" -m roomwalk %*
if errorlevel 1 pause
goto :eof

:nopython
echo.
echo  Roomwalk needs Python 3.10 to 3.14, and did not find one.
echo  (Python 3.15 is too new for one of the parts Roomwalk uses.)
echo.
echo  1. Go to https://www.python.org/downloads/windows/
echo  2. Download the latest "Python 3.14" Windows installer (64-bit) and run it.
echo     Tick "Add python.exe to PATH" on its first screen.
echo  3. Double-click start.bat again.
echo.
pause
goto :eof

:failed
echo.
echo  Setup did not finish. Check your internet connection and run start.bat again.
echo  If it keeps failing, delete the ".venv" folder next to this file and retry.
echo.
pause
