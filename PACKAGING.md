# Radiogrammer Packaging Guide

This guide covers how to package Radiogrammer for distribution.

## Option 1: PyInstaller (Standalone Executable)

**Best for:** End users who don't have Python installed

### Prerequisites

```bash
pip install pyinstaller
```

### Building

**Linux/macOS:**
```bash
chmod +x build.sh
./build.sh
```

**Windows:**
```cmd
build.bat
```

### Output

- **Linux/macOS:** `dist/radiogrammer` (executable)
- **Windows:** `dist/radiogrammer.exe`

### Creating Distribution Archives

**Linux:**
```bash
cd dist
tar -czf radiogrammer-linux-x64.tar.gz radiogrammer
```

**macOS:**
```bash
# Creates .app bundle
cp -r dist/Radiogrammer.app ~/Applications/
# Or create DMG (requires create-dmg)
create-dmg dist/Radiogrammer.app
```

**Windows:**
```cmd
# Create zip file
cd dist
tar -czf radiogrammer-windows-x64.zip radiogrammer.exe
```

---

## Option 2: Python Wheel (Requires Python)

**Best for:** Developers and users with Python installed

### Install from source

```bash
pip install -e .
```

### Build wheel

```bash
pip install build
python -m build
```

### Output

`dist/radiogrammer-<version>-py3-none-any.whl`

### Install wheel

```bash
pip install dist/radiogrammer-*.whl
```

### Run

```bash
radiogrammer
# Or
python -m radiogrammer
```

---

## Desktop Integration

### Linux

1. **Copy executable:**
   ```bash
   sudo cp dist/radiogrammer /usr/local/bin/
   # Or user-only:
   mkdir -p ~/.local/bin
   cp dist/radiogrammer ~/.local/bin/
   ```

2. **Install desktop file:**
   ```bash
   sudo cp radiogrammer.desktop /usr/share/applications/
   sudo update-desktop-database
   # Or user-only:
   mkdir -p ~/.local/share/applications
   cp radiogrammer.desktop ~/.local/share/applications/
   update-desktop-database ~/.local/share/applications/
   ```

3. **Add icon (optional):**
   ```bash
   # If you have a radiogrammer.png icon
   sudo cp radiogrammer.png /usr/share/icons/hicolor/256x256/apps/
   sudo gtk-update-icon-cache /usr/share/icons/hicolor/
   ```

### Windows

1. **Create Start Menu shortcut:**
   - Right-click `radiogrammer.exe`
   - Select "Create shortcut"
   - Move shortcut to: `%APPDATA%\Microsoft\Windows\Start Menu\Programs\`

2. **Create Desktop shortcut:**
   - Right-click `radiogrammer.exe`
   - Select "Create shortcut"
   - Move to Desktop

3. **Add icon (optional):**
   - Right-click shortcut → Properties → Change Icon
   - Browse to `radiogrammer.ico` (if you create one)

### macOS

1. **Copy app bundle:**
   ```bash
   cp -r dist/Radiogrammer.app /Applications/
   ```

2. **Create dock shortcut:**
   - Open Applications folder
   - Drag Radiogrammer.app to Dock

---

## Creating Icons

### Linux (PNG)

Create a 256x256 PNG icon:

```bash
# Example using ImageMagick
convert -size 256x256 xc:blue -pointsize 72 -fill white \
        -gravity center -annotate +0+0 "RG" radiogrammer.png
```

### Windows (ICO)

Convert PNG to ICO:

```bash
# Using ImageMagick
convert radiogrammer.png -define icon:auto-resize=256,128,64,48,32,16 radiogrammer.ico
```

Update `radiogrammer.spec`:
```python
icon='radiogrammer.ico',
```

### macOS (ICNS)

```bash
# Create iconset
mkdir radiogrammer.iconset
sips -z 16 16     radiogrammer.png --out radiogrammer.iconset/icon_16x16.png
sips -z 32 32     radiogrammer.png --out radiogrammer.iconset/icon_16x16@2x.png
sips -z 32 32     radiogrammer.png --out radiogrammer.iconset/icon_32x32.png
sips -z 64 64     radiogrammer.png --out radiogrammer.iconset/icon_32x32@2x.png
sips -z 128 128   radiogrammer.png --out radiogrammer.iconset/icon_128x128.png
sips -z 256 256   radiogrammer.png --out radiogrammer.iconset/icon_128x128@2x.png
sips -z 256 256   radiogrammer.png --out radiogrammer.iconset/icon_256x256.png
sips -z 512 512   radiogrammer.png --out radiogrammer.iconset/icon_256x256@2x.png
sips -z 512 512   radiogrammer.png --out radiogrammer.iconset/icon_512x512.png
sips -z 1024 1024 radiogrammer.png --out radiogrammer.iconset/icon_512x512@2x.png

# Convert to icns
iconutil -c icns radiogrammer.iconset
```

Update `radiogrammer.spec`:
```python
icon='radiogrammer.icns',
```

---

## Distribution Best Practices

### Version Numbering

Update version in `pyproject.toml`:
```toml
[project]
version = "1.0.0"
```

### Release Checklist

- [ ] Update version number
- [ ] Update CHANGELOG.md
- [ ] Test on all target platforms
- [ ] Build executables
- [ ] Create distribution archives
- [ ] Create GitHub release with binaries
- [ ] Update documentation

### GitHub Release Example

```bash
# Tag release
git tag -a v1.0.0 -m "Release version 1.0.0"
git push origin v1.0.0

# Upload binaries to GitHub release:
# - radiogrammer-linux-x64.tar.gz
# - radiogrammer-windows-x64.zip
# - radiogrammer-macos-universal.dmg
# - radiogrammer-<version>-py3-none-any.whl (optional)
```

---

## Troubleshooting

### PyInstaller Issues

**Missing modules:**
Add to `hiddenimports` in `radiogrammer.spec`:
```python
hiddenimports=['tkinter', 'PIL._tkinter_finder'],
```

**Large executable size:**
- Use `upx=True` for compression
- Exclude unused modules in `excludes`

**Tkinter not found:**
```bash
# Linux
sudo apt install python3-tk

# macOS (should be included)
brew install python-tk

# Windows (should be included with Python installer)
```

### Desktop File Not Showing

```bash
# Validate desktop file
desktop-file-validate radiogrammer.desktop

# Check for errors
journalctl -f | grep radiogrammer
```

### Permission Denied (Linux)

```bash
chmod +x dist/radiogrammer
```

---

## File Size Comparison

| Method | Approximate Size |
|--------|------------------|
| PyInstaller (Linux) | ~15-25 MB |
| PyInstaller (Windows) | ~20-30 MB |
| PyInstaller (macOS) | ~20-30 MB |
| Python Wheel | ~50 KB (requires Python) |
| Source | ~100 KB |

---

## Support

For packaging issues, check:
- PyInstaller documentation: https://pyinstaller.org/
- Platform-specific packaging guides
- GitHub Issues: https://github.com/yourusername/radiogrammer/issues
