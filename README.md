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
pio test -e native              # Unit tests — no hardware needed
pio run -e seeed_xiao           # Compile for Seeeduino XIAO (SAMD21)
pio run -e seeed_xiao -t upload # Flash
```

### UI with mock firmware (WSL terminal — three terminals)
```bash
make ui-install    # First time only: venv + deps + Playwright
make socat-start   # Terminal 1: virtual COM pair /tmp/vcom0 <-> /tmp/vcom1
make mock-start    # Terminal 2: firmware simulator on /tmp/vcom1
make ui-dev        # Terminal 3: Flask server at http://localhost:5000
```

### UI with real hardware (Windows terminal)
```bash
# Flash firmware first, then in WSL:
SERIAL_URL=/dev/ttyACM0 ui/.venv/bin/python ui/app.py
# Or on Windows: set SERIAL_URL=COM5 and run ui/app.py directly
```

> **Important:** always start the backend with `python app.py` (or `make ui-dev`).  
> `flask run` does **not** support WebSocket and will silently drop Socket.IO connections.

## Project Structure

```
.
├── firmware/                  PlatformIO project (C++)
│   ├── src/main.cpp               Hardware layer (Arduino + ADS1115)
│   ├── include/config.h           Constants and pin definitions
│   ├── lib/Electrochemistry/      Portable scan logic (no Arduino.h)
│   └── test/                      Unity tests (native env, no hardware)
│       ├── test_math/
│       ├── test_algorithms/
│       └── test_abort/
├── ui/                        Flask + Socket.IO backend
│   ├── app.py                     Serial bridge, ScanState machine, socket handlers
│   ├── templates/
│   │   └── index.html             Browser UI (Plotly.js + Socket.IO + Tailwind)
│   ├── static/
│   │   ├── potentiostat-core.js   Pure-JS utilities shared with webserial.html
│   │   │                          (peak detection, CSV export/import, species hints)
│   │   └── webserial.html         Web Serial standalone page — Phase 9, Chrome/Edge only
│   ├── requirements.txt           Python deps
│   ├── .env.development           DEV env vars (POTENTIOSTAT_DEV=1, loop:// serial)
│   ├── .env.production            Production env vars
│   └── tests/
│       ├── conftest.py            Shared fixtures (app factory, mock serial)
│       ├── test_serial_parser.py  Parser + ScanState machine unit tests (80 tests)
│       └── test_backend.py        Socket.IO event handler tests (24 tests)
├── tests/                     Integration & E2E (run from ui/.venv)
│   ├── mock_potentiostat.py       Firmware protocol simulator (socat PTY)
│   └── mock_backend.js            Node.js Socket.IO mock (Phase 10, no-Python dev)
└── Makefile                   Build & test orchestration (see `make help`)
```

## Backend Architecture

- **Threading model:** `async_mode='threading'` + `simple-websocket`. Eventlet was dropped — it is in maintenance mode and broken on Python 3.12+.
- **One reader thread per open port**, started by `connect_port`. Exits when the port closes.
- **`socketio.emit()` (module-level)** used from the reader thread — not `flask_socketio.emit()`, which requires a request context.
- **`serial_write_lock`** (threading.Lock) guards all serial writes and `serial_conn` mutation.

## Serial Protocol

| Command | Description | Stream markers / output |
| --- | --- | --- |
| `C [Vstart,Vstop,cycles,rate]` | Cyclic Voltammetry | `*` … `voltage,current,re` … `#` |
| `D [Vstart,Vstop,Veq,teq,stepE,pulseAmp,period,width]` | Differential Pulse Voltammetry | `*` … `voltage,current,re` … `$` |
| `L [stepSize]` | DAC linearity sweep (diagnostic) | `L*` … `dac,volts` … `L#` |
| `T [dacBefore,dacAfter,nSamples]` | Step response (diagnostic) | `T*` … `elapsed_us,current_uA` … `T#` |
| `Q [dac]` | Channel-query: all ADS1115 channels at one DAC | `Q*` … `Q dac=… Vin_theoretical=…` … `AINn=…` / `DIFF_x_y=…` … `Q#` |
| `!` | Abort current scan | — |
| `Z` | Auto-zero (measure TIA offset) | `Z: offset=…mV` |
| `I` | Firmware identity | identity string |

**Data columns:** CV/DPV stream three columns — `voltage,current,re` — where `re` is the measured
reference-electrode voltage. The `L`, `T`, and `Q` diagnostics emit their own distinct column shapes
and must not be parsed as voltammetry data (the ScanState machine ensures this).

**Tip for Q:** run twice at two different DAC values. The channel whose reading changes between runs
carries Vin. The UI query table highlights changed channels automatically.

## WebSocket Event Reference

### Server → Client

| Event | Payload | When |
| --- | --- | --- |
| `port_list` | `["/dev/ttyACM0", …]` | On every new socket connection |
| `port_connected` | `{port}` | Serial port opened successfully |
| `port_disconnected` | — | Port closed (user request or serial error) |
| `scan_started` | `{mode}` | Start marker received (`CV`, `DPV`, `LINEARITY`, `STEP`, `QUERY`) |
| `new_datapoint` | `{voltage, current, re}` | Each CV/DPV data line |
| `linearity_point` | `{dac, voltage}` | Each L-command data line |
| `step_point` | `{elapsed_us, current}` | Each T-command data line |
| `query_result` | `{dac, vin_theoretical, channels}` | At `Q#` — full accumulated result |
| `peaks_detected` | `{peaks: [{voltage, current}]}` | At DPV `$` end marker, scipy peaks |
| `scan_complete` | `{type, points}` | End of any scan (`type` = mode name) |
| `scan_error` | `{message}` | Firmware `E:` line or serial exception |
| `scan_info` | `{message}` | Firmware `# …` info header (e.g. `stepE_actual=16.1mV`) |
| `scan_resync` | `{state, points}` | Reconnecting client catches up to an active scan |

### Client → Server

| Event | Payload | Action |
| --- | --- | --- |
| `connect_port` | `{port?, baud?}` | Open port; falls back to `SERIAL_URL` if `port` absent/empty |
| `disconnect_port` | — | Close port, reset state to IDLE |
| `start_scan` | `{command}` | Write command string + `\n` to serial |
| `abort_scan` | — | Write `!\n` to serial |

## Test Layers

```bash
make ui-test           # 104 pytest tests (no hardware, no browser)
make integration-test  # Full mock potentiostat → Flask → parser round-trip
make test-e2e          # Playwright browser tests (Phase 10)
make test-all          # All layers
```

| Layer | File | Count | Covers |
| --- | --- | --- | --- |
| Parser unit | `ui/tests/test_serial_parser.py` | 80 | `parse_data_line`, `parse_linearity_line`, `parse_step_line`, `detect_peaks`, all `_process_line` state transitions |
| Socket handler | `ui/tests/test_backend.py` | 24 | `connect`, `connect_port`, `disconnect_port`, `start_scan`, `abort_scan` |
| Integration | `tests/test_integration.py` | planned | mock_potentiostat.py → Flask → full event stream |
| E2E browser | `tests/test_e2e_browser.py` | planned | Playwright, Phase 10 |

## Environment Variables

| Variable | Default | Description |
| --- | --- | --- |
| `POTENTIOSTAT_DEV` | `0` | `1` enables CORS `*`, Socket.IO logging, DEV badge in UI |
| `FLASK_DEBUG` | `0` | `1` enables Flask debug mode (auto-reload) |
| `SERIAL_URL` | `''` (prod) / `loop://` (dev/test) | pyserial URL — use `loop://` for unit tests, `/tmp/vcom0` for mock, `COM5` or `/dev/ttyACM0` for hardware |

`make ui-dev` sets `POTENTIOSTAT_DEV=1 FLASK_DEBUG=1 SERIAL_URL=/tmp/vcom0`.

## Thesis Figure Numbering (canonical)

| Figure | Plot | Source command |
| --- | --- | --- |
| **Fig 1** | DAC linearity | `L` |
| **Fig 2** | RE monitoring (commanded V vs measured RE) | `C`/`D` (RE column) |
| **Fig 3** | Step response | `T` |
| **Fig 4** | CV attempt / WE fault diagnosis | `C` |

These are the agreed, single source of truth for figure numbers. If earlier thesis chapters consume
figure numbers first, renumber **all four together** — never mix two schemes across planning and prose.
