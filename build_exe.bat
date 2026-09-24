@echo off
rem pitch-helper: one-click build of PitchHelper.exe. Double-click this file.
rem
rem All the work is done by tools\build_exe.py (read it for the why). This file
rem only finds a Python to run it with, and waits for a key at the end so the
rem window does not vanish before you can read it.
rem
rem   build_exe.bat              folder build + .zip (recommended)
rem   build_exe.bat --onefile    a single .exe (slower to start)
rem   build_exe.bat --help       every option
rem
rem Output: dist\PitchHelper\PitchHelper.exe and dist\PitchHelper-*.zip
rem
rem Keep this file ASCII-only and free of labels/goto. It is stored with LF line
rem endings (the text bundle refuses CR), and cmd.exe mis-reads labels in LF-only
rem batch files; plain lines and ( ) blocks are safe. ASCII because Notepad on a
rem Chinese Windows saves as cp950 and cmd.exe reads it in the OEM code page.
setlocal
cd /d "%~dp0"

rem "python" first (that is what PATH and the installer's checkbox control), but
rem only if it really runs: the Microsoft Store stub named python.exe does not.
set "PY="
python -c "import sys" >nul 2>nul && set "PY=python"
if not defined PY (
    py -3 -c "import sys" >nul 2>nul && set "PY=py -3"
)
if not defined PY (
    echo.
    echo [X] Python was not found.
    echo     Install Python 3.9 or newer from https://www.python.org/downloads/
    echo     and tick "Add python.exe to PATH" in the installer. Then run this again.
    echo.
    if not defined CI pause
    exit /b 2
)

%PY% tools\build_exe.py %*
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" echo [X] Build failed, exit code %RC%. The reason is printed above.
if not defined CI pause
exit /b %RC%
