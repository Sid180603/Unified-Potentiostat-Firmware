# Unified Potentiostat

Rewritten firmware + web UI for the XIAO-based potentiostat (SAMD21 + ADS1115 + LM324 TIA).

## Quick Start

### Firmware (Windows terminal)
```bash
cd firmware
pio test -e native        # Run unit tests (no hardware)
pio run -e seeed_xiao     # Compile for board
pio run -e seeed_xiao -t upload  # Flash
```

### UI Development (WSL terminal)
```bash
make ui-install           # First time: create venv + install deps
make socat-start          # Create virtual COM pair
make mock-start           # (new terminal) Start firmware simulator
make ui-dev               # (new terminal) Start Flask server
# Open http://localhost:5000 in browser
```

### Frontend-only (no mock needed)
```bash
make ui-dev
# Open http://localhost:5000/?test
```

## Project Structure

```
unified/
├── firmware/          PlatformIO project (C++)
│   ├── src/main.cpp       Hardware layer (Arduino + ADS1115)
│   ├── include/config.h   Constants and pin definitions
│   ├── lib/Electrochemistry/  Portable logic (no Arduino.h)
│   └── test/               Unity tests (run on PC, native env)
│       ├── test_math/         Conversion / math tests
│       ├── test_algorithms/   CV/DPV/linearity/step logic
│       └── test_abort/        Abort-handling tests
├── ui/                Flask + SocketIO backend
│   ├── app.py             Web server with serial bridge
│   ├── templates/         HTML frontend
│   ├── static/            Web Serial standalone fallback
│   └── tests/             pytest backend tests
├── tests/             Integration & E2E tests
│   ├── mock_potentiostat.py  Firmware protocol simulator
│   └── mock_backend.js       Node.js SocketIO mock (optional)
└── Makefile           Build & test orchestration
```

## Serial Protocol

| Command | Description |
|---------|-------------|
| `C [Vstart,Vstop,cycles,rate]` | Cyclic Voltammetry |
| `D [Vstart,Vstop,Veq,teq,stepE,pulseAmp,period,width]` | Differential Pulse Voltammetry |
| `!` | Abort current scan |
| `Z` | Auto-zero (measure TIA offset) |
| `I` | Firmware identity |

Data stream: `*\n` start marker, CSV `voltage,current\n` lines, `#\n` (CV end) or `$\n` (DPV end).
