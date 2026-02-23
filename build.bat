@echo off
REM Build script for Radiogrammer (Windows)

echo === Radiogrammer Build Script ===
echo.

REM Check if pyinstaller is installed
pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo PyInstaller not found. Installing...
    pip install pyinstaller
)

REM Clean previous builds
echo Cleaning previous builds...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

REM Build the application
echo Building Radiogrammer...
pyinstaller radiogrammer.spec

echo.
echo === Build Complete ===
echo Executable: dist\radiogrammer.exe
echo.
echo To create a distributable archive:
echo   Right-click dist folder and "Send to > Compressed (zipped) folder"
echo   Or use: tar -czf radiogrammer-windows-x64.zip dist/radiogrammer.exe RadiogramTemplate.pdf
echo.
pause
