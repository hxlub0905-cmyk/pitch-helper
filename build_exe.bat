@echo off
rem ==========================================================================
rem  build_exe.bat -- one click: pack the pitch helper into dist\PitchHelper.exe
rem
rem  Double-click it. It will
rem    1. find Python 3.9 or newer: "python" on PATH, else the "py" launcher
rem    2. make a private build environment in .venv-build\  - first run only
rem    3. install requirements.txt + PyInstaller into it. The first run needs
rem       internet; later runs reuse what is already installed.
rem    4. run tools\build_exe.py, which does the actual packing
rem
rem  Options are passed on to tools\build_exe.py:
rem    build_exe.bat --onedir     a folder instead of one .exe, starts faster
rem    build_exe.bat --console    keep a console window, to see a crash
rem
rem  RULES FOR EDITING THIS FILE - tests/test_build_exe.py checks them:
rem   * Pure ASCII only. cmd.exe reads a .bat in the console code page,
rem     cp950 on a Chinese Windows, not UTF-8.
rem   * No labels, no goto, no "call :name". The repo keeps every file with
rem     LF line endings - the text bundle requires it - and cmd.exe can miss
rem     a label in a file that has LF-only line endings.
rem   * No ( or ) in echo text inside a ( ... ) block: it ends the block.
rem ==========================================================================
setlocal
rem pushd, not cd: it also works when the repo sits on a network share.
pushd "%~dp0"

set "VENV=.venv-build"
set "VPY=%VENV%\Scripts\python.exe"
set "PYCHECK=import sys; sys.exit(0 if sys.version_info[:2] >= (3, 9) else 1)"

echo [1/4] Looking for Python 3.9 or newer ...
rem Run it rather than just look it up: on Windows 10/11 "python" can be the
rem Microsoft Store shortcut, which exists on PATH but is not a Python.
set "PY="
python -c "%PYCHECK%" >nul 2>nul
if not errorlevel 1 set "PY=python"
if not defined PY (
    py -3 -c "%PYCHECK%" >nul 2>nul
    if not errorlevel 1 set "PY=py -3"
)
if not defined PY (
    echo.
    echo [ERROR] Python 3.9 or newer was not found.
    echo         Install it from https://www.python.org/downloads/ and tick
    echo         "Add python.exe to PATH" in the installer, then run this again.
    echo.
    popd
    pause
    exit /b 1
)
echo         using: %PY%

echo [2/4] Preparing the build environment in %VENV% ...
if not exist "%VPY%" (
    %PY% -m venv "%VENV%"
    if errorlevel 1 (
        echo.
        echo [ERROR] Could not create %VENV%.
        echo.
        popd
        pause
        exit /b 1
    )
)

echo [3/4] Installing requirements.txt + PyInstaller ...
"%VPY%" -m pip install --disable-pip-version-check -r requirements.txt "pyinstaller>=6"
if errorlevel 1 (
    echo.
    echo [ERROR] pip install failed.
    echo   * The first run downloads packages, so it needs internet access.
    echo     Behind a proxy: set HTTPS_PROXY=http://host:port and run it again.
    echo   * If %VENV% is broken, delete that folder and run this again.
    echo   * No internet, but a Python that already has PyInstaller and the
    echo     requirements: run   that-python tools\build_exe.py
    echo.
    popd
    pause
    exit /b 1
)

echo [4/4] Building ...
"%VPY%" tools\build_exe.py %*
if errorlevel 1 (
    echo.
    echo [ERROR] The build failed - see the messages above.
    echo.
    popd
    pause
    exit /b 1
)

echo.
echo Done. The program is in the dist folder.
popd
pause
endlocal
