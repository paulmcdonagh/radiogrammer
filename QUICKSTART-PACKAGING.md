# Quick Start: Packaging Radiogrammer

## TL;DR - Build Standalone Executable

### 1. Install PyInstaller
```bash
pip install pyinstaller
```

### 2. Build
```bash
# Linux/macOS
./build.sh

# Windows
build.bat
```

### 3. Find your executable
- **Linux:** `dist/radiogrammer`
- **Windows:** `dist/radiogrammer.exe`
- **macOS:** `dist/Radiogrammer.app`

---

## Quick Test Run

```bash
# Run from source (no packaging needed)
python src/radiogrammer/radiogram_ui_relay.py
```

---

## Install as Python Package (Simpler)

If you just want to install for yourself without creating an executable:

```bash
# Install in development mode
pip install -e .

# Run from anywhere
radiogrammer
```

---

## Desktop Launcher (Linux)

After building with PyInstaller:

```bash
# 1. Copy executable
mkdir -p ~/.local/bin
cp dist/radiogrammer ~/.local/bin/

# 2. Install desktop file
mkdir -p ~/.local/share/applications
cp radiogrammer.desktop ~/.local/share/applications/
update-desktop-database ~/.local/share/applications/

# 3. Done! Find "Radiogrammer" in your application menu
```

---

## Create Distribution Archive

```bash
# Linux
cd dist
tar -czf ../radiogrammer-linux-$(uname -m).tar.gz radiogrammer
cd ..

# Now you have: radiogrammer-linux-x86_64.tar.gz
# Share this file with other Linux users
```

---

## Troubleshooting

### "command not found: pyinstaller"
```bash
pip install --user pyinstaller
# Or
pip install pyinstaller
```

### Build fails with "ModuleNotFoundError"
```bash
# Install dependencies first
pip install -r requirements.txt
```

### Executable won't run
```bash
# Make it executable
chmod +x dist/radiogrammer
```

### Want smaller executable?
Edit `radiogrammer.spec` and change:
```python
upx=True,  # Compress executable
```

---

## What Gets Packaged?

- Python interpreter
- All dependencies (pydantic, reportlab, PIL, etc.)
- Your application code
- RadiogramTemplate.pdf
- Total size: ~15-30 MB (standalone, no Python needed!)

---

## For More Details

See [PACKAGING.md](PACKAGING.md) for:
- Creating installers
- Cross-platform builds
- Creating icons
- Publishing releases
- Building wheels
