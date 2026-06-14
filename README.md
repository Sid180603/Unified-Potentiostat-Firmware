# Unified Potentiostat

Rewritten firmware + web UI for the XIAO-based potentiostat (SAMD21 + ADS1115 + LM324 TIA).

Repository: https://github.com/Sid180603/Unified-Potentiostat-Firmware

## Quick Start

### Clone
```bash
git clone https://github.com/Sid180603/Unified-Potentiostat-Firmware.git
cd Unified-Potentiostat-Firmware
```

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
.
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

| Command | Description | Stream markers / output |
| --- | --- | --- |
| `C [Vstart,Vstop,cycles,rate]` | Cyclic Voltammetry | `*` … `voltage,current,re` … `#` |
| `D [Vstart,Vstop,Veq,teq,stepE,pulseAmp,period,width]` | Differential Pulse Voltammetry | `*` … `voltage,current,re` … `$` |
| `L [stepSize]` | DAC linearity sweep (diagnostic) | `L*` … `dac,volts` … `L#` |
| `T [dacBefore,dacAfter,nSamples]` | Step response (diagnostic) | `T*` … `elapsed_us,current_uA` … `T#` |
| `Q [dac]` | Channel-query: all ADS1115 channels at one DAC (diagnostic) | `Q dac=… Vin_theoretical=…` … `AINn=…` / `DIFF_x_y=…` … `Q#` |
| `!` | Abort current scan | — |
| `Z` | Auto-zero (measure TIA offset) | `Z: offset=…mV` |
| `I` | Firmware identity | identity string |

**Data columns:** CV/DPV stream three columns — `voltage,current,re` — where `re` is the measured
reference-electrode voltage (3rd column added for RE monitoring). The `L`, `T`, and `Q` diagnostics
emit their own distinct column shapes (see markers above) and must NOT be parsed as voltammetry.

## Thesis figure numbering (canonical — keep consistent in code, README, and the document)

| Figure | Plot | Source command |
| --- | --- | --- |
| **Fig 1** | DAC linearity | `L` |
| **Fig 2** | RE monitoring (commanded V vs measured RE) | `C`/`D` (RE column) |
| **Fig 3** | Step response | `T` |
| **Fig 4** | CV attempt / WE fault diagnosis | `C` |

These are the agreed, single source of truth for figure numbers. If earlier thesis chapters consume
figure numbers first, renumber **all four together** — never mix two schemes across planning and prose.
