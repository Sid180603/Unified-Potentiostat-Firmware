# Unified Potentiostat — Build & Test Orchestration
#
# SPLIT BY ENVIRONMENT:
#   firmware-* targets → run from Windows terminal (PlatformIO on Windows)
#   ui-*, mock-*, test-* → run from WSL terminal (Flask + socat + Playwright)
#   Hardware testing (Phase 6) → use 'pio device monitor' from Windows, not Flask

.PHONY: firmware-build firmware-upload firmware-test firmware-monitor firmware-clean \
        socat-start socat-stop ui-install ui-dev ui-prod ui-test \
        mock-start integration-test test-e2e test-all dev help

# ============================================================
# FIRMWARE (Windows terminal — PlatformIO)
# ============================================================

firmware-build:
	cd firmware && pio run -e seeed_xiao

firmware-upload:
	cd firmware && pio run -e seeed_xiao -t upload

firmware-test:
	cd firmware && pio test -e native

firmware-monitor:
	cd firmware && pio device monitor -b 115200

firmware-clean:
	cd firmware && pio run -t clean

# ============================================================
# VIRTUAL COM PAIR (WSL terminal — run before mock-start and ui-dev)
# ============================================================

socat-start:
	@echo "Starting socat PTY pair..."
	socat pty,raw,echo=0,link=/tmp/vcom0 pty,raw,echo=0,link=/tmp/vcom1 &
	@sleep 0.5
	@ls -la /tmp/vcom0 /tmp/vcom1
	@echo "Virtual pair ready: /tmp/vcom0 <-> /tmp/vcom1"

socat-stop:
	@pkill -f "socat pty.*vcom" || true
	@echo "socat stopped"

# ============================================================
# UI BACKEND (WSL terminal)
# ============================================================

ui-install:
	python3 -m venv ui/.venv
	ui/.venv/bin/pip install --upgrade pip
	ui/.venv/bin/pip install -r ui/requirements.txt
	ui/.venv/bin/playwright install chromium
	ui/.venv/bin/playwright install-deps chromium
	@echo "UI environment ready."

ui-dev:
	POTENTIOSTAT_DEV=1 FLASK_DEBUG=1 SERIAL_URL=/tmp/vcom0 ui/.venv/bin/python ui/app.py

ui-prod:
	POTENTIOSTAT_DEV=0 ui/.venv/bin/python ui/app.py

ui-test:
	ui/.venv/bin/pytest ui/tests/ -v

# ============================================================
# MOCK & INTEGRATION (WSL terminal)
# ============================================================

mock-start:
	ui/.venv/bin/python tests/mock_potentiostat.py --port /tmp/vcom1 --verbose

integration-test:
	ui/.venv/bin/pytest tests/test_integration.py -v --timeout=60

test-e2e:
	ui/.venv/bin/pytest tests/test_e2e_browser.py --headed -v

# ============================================================
# CONVENIENCE
# ============================================================

test-all: firmware-test ui-test integration-test test-e2e
	@echo "=== All tests passed ==="

dev:
	@echo ""
	@echo "=== Development Mode (WSL) ==="
	@echo "Terminal 1: make socat-start"
	@echo "Terminal 2: make mock-start"
	@echo "Terminal 3: make ui-dev"
	@echo "Browser:    http://localhost:5000"
	@echo ""
	@echo "=== Frontend-only (no mock needed) ==="
	@echo "Terminal 1: make ui-dev"
	@echo "Browser:    http://localhost:5000/?test"
	@echo ""
	@echo "=== Hardware mode (Phase 6+, Windows terminal) ==="
	@echo "Flash:      make firmware-upload"
	@echo "Monitor:    make firmware-monitor"
	@echo "UI:         run_hardware.bat"
	@echo ""

help:
	@echo "FIRMWARE (Windows terminal):"
	@echo "  firmware-build    Build for Seeeduino XIAO (SAMD21)"
	@echo "  firmware-upload   Build and flash to board"
	@echo "  firmware-test     Native unit tests (no hardware needed)"
	@echo "  firmware-monitor  Serial monitor at 115200 baud"
	@echo "  firmware-clean    Clean build artifacts"
	@echo ""
	@echo "UI/TEST (WSL terminal):"
	@echo "  socat-start       Create /tmp/vcom0 <-> /tmp/vcom1 PTY pair"
	@echo "  socat-stop        Kill socat process"
	@echo "  ui-install        Create venv + install deps + Playwright"
	@echo "  ui-dev            Flask dev mode (POTENTIOSTAT_DEV=1)"
	@echo "  ui-prod           Flask production mode"
	@echo "  ui-test           pytest backend tests (Layer 2)"
	@echo "  mock-start        Mock potentiostat on /tmp/vcom1"
	@echo "  integration-test  Full integration tests (Layer 4)"
	@echo "  test-e2e          Playwright browser tests (Layer 3)"
	@echo "  test-all          All test layers"
	@echo "  dev               Show development workflow"
