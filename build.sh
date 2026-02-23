#!/bin/bash
# Build script for Radiogrammer

set -e  # Exit on error

echo "=== Radiogrammer Build Script ==="
echo ""

# Check if pyinstaller is installed
if ! command -v pyinstaller &> /dev/null; then
    echo "PyInstaller not found. Installing..."
    pip install pyinstaller
fi

# Clean previous builds
echo "Cleaning previous builds..."
rm -rf build/ dist/

# Build the application
echo "Building Radiogrammer..."
pyinstaller radiogrammer.spec

echo ""
echo "=== Build Complete ==="
echo "Executable: dist/radiogrammer"
echo ""
echo "To create a distributable archive:"
echo "  tar -czf radiogrammer-linux-x64.tar.gz -C dist radiogrammer RadiogramTemplate.pdf"
echo ""
