@echo off
rem RTS-Arena build script (ASCII only - cmd.exe reads .bat files in the console code page)
setlocal
cd /d "%~dp0"

set "PY=python"
where python >nul 2>&1 || set "PY=py"

echo [1/3] Running tests...
%PY% -m unittest discover -s tests
if errorlevel 1 (
    echo.
    echo [ERROR] Tests failed. Build stopped.
    pause
    exit /b 1
)

echo [2/3] Checking PyInstaller...
%PY% -m PyInstaller --version >nul 2>&1
if errorlevel 1 %PY% -m pip install pyinstaller

echo [3/3] Building...
%PY% -m PyInstaller --noconfirm --clean RTSArena.spec
if errorlevel 1 (
    echo.
    echo [ERROR] PyInstaller build failed.
    pause
    exit /b 1
)

echo.
echo Done: dist\RTS-Arena.exe
certutil -hashfile "dist\RTS-Arena.exe" SHA256
for %%A in ("dist\RTS-Arena.exe") do echo Size: %%~zA bytes
echo Put the size and SHA256 above into arena.json (arena_size / arena_sha256) when publishing.
pause
