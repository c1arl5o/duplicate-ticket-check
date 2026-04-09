@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "PY_CMD="
where py >nul 2>&1 && set "PY_CMD=py -3"
if not defined PY_CMD (
  where python >nul 2>&1 && set "PY_CMD=python"
)

if not defined PY_CMD (
  echo Python 3 was not found.
  echo.
  where winget >nul 2>&1
  if %ERRORLEVEL% EQU 0 (
    echo Attempting install via winget...
    winget install -e --id Python.Python.3.12
    if %ERRORLEVEL% NEQ 0 (
      echo winget install failed. Opening Python download page...
      start "" "https://www.python.org/downloads/windows/"
      pause
      exit /b 1
    )
    where py >nul 2>&1 && set "PY_CMD=py -3"
    if not defined PY_CMD (
      where python >nul 2>&1 && set "PY_CMD=python"
    )
  ) else (
    start "" "https://www.python.org/downloads/windows/"
    pause
    exit /b 1
  )
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  %PY_CMD% -m venv .venv
  if %ERRORLEVEL% NEQ 0 (
    echo Failed to create virtual environment.
    pause
    exit /b 1
  )
)

call ".venv\Scripts\activate.bat"
if %ERRORLEVEL% NEQ 0 (
  echo Failed to activate virtual environment.
  pause
  exit /b 1
)

echo Installing dependencies...
python -m pip install --upgrade pip
if %ERRORLEVEL% NEQ 0 (
  echo pip upgrade failed.
  pause
  exit /b 1
)

python -m pip install -r requirements.txt
if %ERRORLEVEL% NEQ 0 (
  echo Dependency installation failed.
  pause
  exit /b 1
)

echo Launching Jira Duplicate Finder...
python gui.py
set "EXIT_CODE=%ERRORLEVEL%"

if %EXIT_CODE% NEQ 0 (
  echo.
  echo Application exited with code %EXIT_CODE%.
  pause
)

exit /b %EXIT_CODE%