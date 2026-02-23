.PHONY: help build clean run install test package desktop-install

help:
	@echo "Radiogrammer Build Targets:"
	@echo ""
	@echo "  make run              - Run from source (no build needed)"
	@echo "  make install          - Install as Python package"
	@echo "  make build            - Build standalone executable with PyInstaller"
	@echo "  make package          - Build and create distribution archive"
	@echo "  make desktop-install  - Install desktop launcher (Linux)"
	@echo "  make clean            - Remove build artifacts"
	@echo "  make test             - Run tests (when implemented)"
	@echo ""

run:
	python src/radiogrammer/radiogram_ui_relay.py

install:
	pip install -e .

build:
	@echo "Installing PyInstaller if needed..."
	@pip show pyinstaller > /dev/null 2>&1 || pip install pyinstaller
	@echo "Building..."
	pyinstaller radiogrammer.spec
	@echo ""
	@echo "Build complete! Executable at: dist/radiogrammer"

package: build
	@echo "Creating distribution archive..."
	cd dist && tar -czf ../radiogrammer-linux-$$(uname -m).tar.gz radiogrammer
	@echo ""
	@echo "Archive created: radiogrammer-linux-$$(uname -m).tar.gz"

desktop-install: build
	@echo "Installing desktop launcher..."
	mkdir -p ~/.local/bin
	cp dist/radiogrammer ~/.local/bin/
	mkdir -p ~/.local/share/applications
	cp radiogrammer.desktop ~/.local/share/applications/
	update-desktop-database ~/.local/share/applications/
	@echo ""
	@echo "Desktop launcher installed!"
	@echo "Look for 'Radiogrammer' in your application menu"

clean:
	rm -rf build/ dist/ *.spec.bak
	rm -f radiogrammer-*.tar.gz
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete

test:
	@echo "Tests not yet implemented"
	@echo "Run: pytest tests/"
