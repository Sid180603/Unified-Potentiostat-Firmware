"""
Unified Potentiostat — Flask + SocketIO Backend

Phase 7: Complete serial reader implementation.

Architecture:
  Browser <- WebSocket (Socket.IO) -> Flask/SocketIO <- pyserial -> COM port (or mock)

Threading model:
  - async_mode='threading' + simple-websocket (NOT eventlet -- maintenance mode, py3.12 issues)
  - One background reader thread per open serial port, started at connect_port
  - Thread exits naturally when port is closed (SerialException or conn.is_open == False)
  - socketio.emit() (module-level) used from thread, NOT flask_socketio.emit()
  - serial_write_lock (threading.Lock) protects concurrent writes from socket handlers

Serial protocol (firmware output -> events emitted):
  '*'           CV or DPV start  -> scan_started {mode}
  'L*'          Linearity start  -> scan_started {mode:'LINEARITY'}
  'T*'          Step start       -> scan_started {mode:'STEP'}
  'Q*'          Query start      -> scan_started {mode:'QUERY'}
  'Q dac=...'   Query metadata   -> (accumulated into query buffer)
  'v,i,re'      CV/DPV point     -> new_datapoint {voltage, current, re}
  'dac,volts'   Linearity point  -> linearity_point {dac, voltage}
  'us,current'  Step point       -> step_point {elapsed_us, current}
  'AINn=...'    Query channel    -> (accumulated)
  'DIFF_x_y=..' Query diff       -> (accumulated)
  '#'           CV end           -> scan_complete {type:'CV', points}
  '$'           DPV end          -> peaks_detected {peaks} + scan_complete {type:'DPV', points}
  'L#'          Linearity end    -> scan_complete {type:'LINEARITY', points}
  'T#'          Step end         -> scan_complete {type:'STEP', points}
  'Q#'          Query end        -> query_result {dac, vin_theoretical, channels} + scan_complete
  'E: ...'      Error            -> scan_error {message}
  '# ...'       Info header      -> scan_info {message}
"""

import os
import threading
from enum import Enum, auto

import serial
from flask import Flask, render_template
from flask_socketio import SocketIO, emit

socketio = SocketIO()


class ScanState(Enum):
    """Reader thread state machine states."""
    IDLE = auto()
    CV = auto()
    DPV = auto()
    LINEARITY = auto()
    STEP = auto()
    QUERY = auto()


def create_app(testing=False):
    """App factory -- allows pytest to create fresh instances."""
    app = Flask(__name__)

    dev_mode = testing or os.environ.get('POTENTIOSTAT_DEV', '0') == '1'

    app.config['SECRET_KEY'] = 'potentiostat-dev-key'
    app.config['DEV_MODE'] = dev_mode
    # SERIAL_URL: 'loop://' for unit tests (no virtual port), env-override for dev socat PTY
    app.config['SERIAL_URL'] = os.environ.get('SERIAL_URL', 'loop://' if dev_mode else '')

    cors_origins = '*' if dev_mode else None

    socketio.init_app(
        app,
        async_mode='threading',       # plain threads + simple-websocket, no monkey-patching
        cors_allowed_origins=cors_origins,
        logger=dev_mode,
        engineio_logger=dev_mode,
    )

    # --- Shared application state ---
    app.serial_conn = None
    app.serial_write_lock = threading.Lock()   # guards serial writes + serial_conn mutation
    app.scan_state = ScanState.IDLE
    app.next_mode = ScanState.CV               # expected mode on next '*' marker
    app.current_scan = []                      # server-side point buffer (dicts, shape per mode)
    app._query_buf = {'channels': {}}          # accumulates Q key=value lines until Q#

    # --- Routes ---

    @app.route('/')
    def index():
        return render_template('index.html', dev_mode=dev_mode)

    # --- SocketIO Events ---

    @socketio.on('connect')
    def handle_connect():
        emit('port_list', get_available_ports())
        # Resync a reconnecting client that missed part of an active scan.
        # Condition: check scan_state (not current_scan) -- scan may be live with 0 points yet.
        if app.scan_state != ScanState.IDLE:
            emit('scan_resync', {
                'points': list(app.current_scan),
                'state': app.scan_state.name,
            })

    @socketio.on('connect_port')
    def handle_connect_port(data):
        port = data.get('port') or app.config['SERIAL_URL']
        baud = data.get('baud', 115200)
        try:
            conn = serial.serial_for_url(port, baudrate=baud, timeout=0.1)
            with app.serial_write_lock:
                app.serial_conn = conn
            emit('port_connected', {'port': port})
            # Start background reader -- it exits when the port is closed
            socketio.start_background_task(_serial_reader, app)
        except Exception as e:
            emit('scan_error', {'message': str(e)})

    @socketio.on('disconnect_port')
    def handle_disconnect_port():
        with app.serial_write_lock:
            conn = app.serial_conn
            if conn and conn.is_open:
                conn.close()
            app.serial_conn = None
            app.scan_state = ScanState.IDLE
        emit('port_disconnected')

    @socketio.on('start_scan')
    def handle_start_scan(data):
        command = data.get('command', 'D')
        with app.serial_write_lock:
            conn = app.serial_conn
            if not conn or not conn.is_open:
                emit('scan_error', {'message': 'Not connected'})
                return
            # Set expected mode so the '*' marker can be routed correctly.
            # L/T/Q set their own state on their distinct start markers, but setting
            # next_mode here keeps handle_start_scan <-> reader contract explicit.
            cmd_char = command.strip()[0].upper() if command.strip() else 'D'
            app.next_mode = {
                'C': ScanState.CV,
                'D': ScanState.DPV,
                'L': ScanState.LINEARITY,
                'T': ScanState.STEP,
                'Q': ScanState.QUERY,
            }.get(cmd_char, ScanState.CV)
            conn.write((command.strip() + '\n').encode())

    @socketio.on('abort_scan')
    def handle_abort_scan():
        with app.serial_write_lock:
            conn = app.serial_conn
            if conn and conn.is_open:
                conn.write(b'!\n')

    return app


# ---------------------------------------------------------------------------
# Serial reader -- runs as a background task (one per open port)
# ---------------------------------------------------------------------------

def _serial_reader(app):
    """Background serial reader thread.

    Reads lines from the open serial port and dispatches each through
    the ScanState machine. Exits when the port is closed.
    Called via socketio.start_background_task() so it cooperates with threading mode.
    """
    while True:
        # Check liveness outside the lock -- reader is the only readline() caller
        conn = app.serial_conn
        if conn is None or not conn.is_open:
            break

        try:
            raw = conn.readline()
        except serial.SerialException as e:
            socketio.emit('scan_error', {'message': f'Serial disconnected: {e}'})
            with app.serial_write_lock:
                app.serial_conn = None
                app.scan_state = ScanState.IDLE
            socketio.emit('port_disconnected')
            break

        if not raw:
            continue  # 0.1 s timeout with no data -- loop, check liveness

        line = raw.decode('utf-8', errors='ignore').strip()
        if not line:
            continue

        _process_line(app, line)


def _process_line(app, line):
    """Route one decoded firmware output line through the state machine.

    All state mutations go through this function so tests can call it directly
    with a controlled app fixture and assert on emitted events.

    Marker priority:
      End markers checked BEFORE start markers (Q# before Q-prefix collision).
      Exact matches checked before startswith() to avoid false positives.
    """
    state = app.scan_state

    # -----------------------------------------------------------------------
    # End markers (checked first so 'Q#' beats startswith('Q '))
    # -----------------------------------------------------------------------
    if line == 'Q#':
        _finalize_query(app)
        return

    if line == '#':
        socketio.emit('scan_complete', {'type': 'CV', 'points': len(app.current_scan)})
        app.scan_state = ScanState.IDLE
        return

    if line == '$':
        peaks = []
        if app.current_scan:
            voltages = [p['voltage'] for p in app.current_scan]
            currents = [p['current'] for p in app.current_scan]
            peaks = detect_peaks(voltages, currents)
        socketio.emit('peaks_detected', {'peaks': peaks})
        socketio.emit('scan_complete', {'type': 'DPV', 'points': len(app.current_scan)})
        app.scan_state = ScanState.IDLE
        return

    if line == 'L#':
        socketio.emit('scan_complete', {'type': 'LINEARITY', 'points': len(app.current_scan)})
        app.scan_state = ScanState.IDLE
        return

    if line == 'T#':
        socketio.emit('scan_complete', {'type': 'STEP', 'points': len(app.current_scan)})
        app.scan_state = ScanState.IDLE
        return

    # -----------------------------------------------------------------------
    # Start markers
    # -----------------------------------------------------------------------
    if line == '*':
        # Mode was set in handle_start_scan when the C/D command was written
        new_state = app.next_mode if app.next_mode in (ScanState.CV, ScanState.DPV) else ScanState.CV
        app.scan_state = new_state
        app.current_scan = []
        socketio.emit('scan_started', {'mode': new_state.name})
        return

    if line == 'L*':
        app.scan_state = ScanState.LINEARITY
        app.current_scan = []
        socketio.emit('scan_started', {'mode': 'LINEARITY'})
        return

    if line == 'T*':
        app.scan_state = ScanState.STEP
        app.current_scan = []
        socketio.emit('scan_started', {'mode': 'STEP'})
        return

    # Q start: real firmware sends 'Q*'; mock also sends it before 'Q dac=...'
    if line == 'Q*':
        app.scan_state = ScanState.QUERY
        app._query_buf = {'channels': {}}
        socketio.emit('scan_started', {'mode': 'QUERY'})
        return

    # -----------------------------------------------------------------------
    # Error and info lines (before data parsing to avoid ',' false-positive)
    # -----------------------------------------------------------------------
    if line.startswith('E:'):
        socketio.emit('scan_error', {'message': line[2:].strip()})
        if state != ScanState.IDLE:
            app.scan_state = ScanState.IDLE
        return

    if line.startswith('# '):
        # Informational header line, e.g. '# stepE_actual=16.1mV'
        socketio.emit('scan_info', {'message': line[2:].strip()})
        return

    # -----------------------------------------------------------------------
    # Data lines -- routed by current state (NEVER by comma heuristic)
    # -----------------------------------------------------------------------
    if state in (ScanState.CV, ScanState.DPV):
        parsed = parse_data_line(line)
        if parsed:
            v, i, re = parsed
            point = {'voltage': v, 'current': i, 're': re}
            app.current_scan.append(point)
            socketio.emit('new_datapoint', point)
        return

    if state == ScanState.LINEARITY:
        parsed = parse_linearity_line(line)
        if parsed:
            dac, voltage = parsed
            point = {'dac': dac, 'voltage': voltage}
            app.current_scan.append(point)
            socketio.emit('linearity_point', point)
        return

    if state == ScanState.STEP:
        parsed = parse_step_line(line)
        if parsed:
            elapsed_us, current = parsed
            point = {'elapsed_us': elapsed_us, 'current': current}
            app.current_scan.append(point)
            socketio.emit('step_point', point)
        return

    if state == ScanState.QUERY:
        # 'Q dac=512 Vin_theoretical=0.0000' -- arrives after Q* marker
        if line.startswith('Q dac='):
            _parse_query_header(app, line)
        elif '=' in line and (line.startswith('AIN') or line.startswith('DIFF_')):
            key, _, val_str = line.partition('=')
            try:
                app._query_buf['channels'][key] = float(val_str)
            except ValueError:
                pass
        return

    # IDLE state or unknown line -- silently ignore
    # (e.g. identity string from 'I' command, 'Z: offset=...' from 'Z' command)


def _parse_query_header(app, line):
    """Parse 'Q dac=512 Vin_theoretical=0.0000' into the query buffer."""
    for token in line.split()[1:]:   # skip the leading 'Q'
        k, _, v = token.partition('=')
        try:
            if k == 'dac':
                app._query_buf['dac'] = int(v)
            elif k == 'Vin_theoretical':
                app._query_buf['vin_theoretical'] = float(v)
        except ValueError:
            pass


def _finalize_query(app):
    """Emit query_result from the accumulated buffer and return to IDLE."""
    result = {
        'dac': app._query_buf.get('dac'),
        'vin_theoretical': app._query_buf.get('vin_theoretical'),
        'channels': dict(app._query_buf.get('channels', {})),
    }
    socketio.emit('query_result', result)
    socketio.emit('scan_complete', {'type': 'QUERY', 'points': len(result['channels'])})
    app.scan_state = ScanState.IDLE
    app._query_buf = {'channels': {}}


# ---------------------------------------------------------------------------
# Serial port discovery
# ---------------------------------------------------------------------------

def get_available_ports():
    """List available serial ports."""
    try:
        from serial.tools import list_ports
        return [p.device for p in list_ports.comports()]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Data parsers (pure functions -- importable for tests)
# ---------------------------------------------------------------------------

def parse_data_line(line):
    """
    Parse a CV/DPV CSV data line: 'voltage,current[,re_voltage]'
    Returns (voltage: float, current: float, re_voltage: float|None) or None.

    Returns None for marker lines, error lines, info headers, and malformed input.
    This function is a PARSER, not a router -- only call it when state in {CV, DPV}.
    """
    if not line or not line.strip():
        return None
    line = line.strip()
    # Reject all non-data lines (markers, errors, info headers, query lines)
    if line in ('*', '#', '$', 'L*', 'L#', 'T*', 'T#', 'Q*', 'Q#'):
        return None
    if line.startswith('# ') or line.startswith('E:') or line.startswith('Q '):
        return None
    if ',' not in line:
        return None
    try:
        parts = line.split(',')
        if len(parts) == 2:
            return (float(parts[0]), float(parts[1]), None)
        if len(parts) == 3:
            return (float(parts[0]), float(parts[1]), float(parts[2]))
        return None
    except (ValueError, IndexError):
        return None


def parse_linearity_line(line):
    """
    Parse a linearity sweep line: 'dac_count,measured_volts'
    Returns (dac: int, voltage: float) or None.
    Only call when state == LINEARITY.
    """
    if not line or ',' not in line:
        return None
    try:
        parts = line.split(',')
        if len(parts) < 2:
            return None
        return (int(parts[0]), float(parts[1]))
    except (ValueError, IndexError):
        return None


def parse_step_line(line):
    """
    Parse a step-response line: 'elapsed_us,current_uA'
    Returns (elapsed_us: int, current: float) or None.
    Only call when state == STEP.
    """
    if not line or ',' not in line:
        return None
    try:
        parts = line.split(',')
        if len(parts) < 2:
            return None
        return (int(parts[0]), float(parts[1]))
    except (ValueError, IndexError):
        return None


def detect_peaks(voltages, currents, height=0.05, distance_mv=150, prominence=0.05):
    """
    Detect peaks in DPV data using scipy.signal.find_peaks.
    Returns list of dicts: [{'voltage': v, 'current': i}, ...]
    """
    from scipy.signal import find_peaks
    import numpy as np

    if len(voltages) < 3:
        return []

    voltages = np.array(voltages)
    currents = np.array(currents)

    step_mv = abs(voltages[-1] - voltages[0]) / (len(voltages) - 1) * 1000
    distance_pts = max(1, int(distance_mv / step_mv))

    peaks, _ = find_peaks(currents, height=height,
                          distance=distance_pts, prominence=prominence)
    return [{'voltage': float(voltages[i]), 'current': float(currents[i])} for i in peaks]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    app = create_app()
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    # Always use socketio.run() -- `flask run` does NOT support WebSocket
    # Prints LAN URL so a tablet/phone on the same WiFi can connect
    import socket as _socket
    try:
        lan_ip = _socket.gethostbyname(_socket.gethostname())
    except Exception:
        lan_ip = '0.0.0.0'
    print(f'  * Potentiostat UI: http://localhost:5000')
    print(f'  * LAN access:      http://{lan_ip}:5000')
    socketio.run(app, host='0.0.0.0', port=5000, debug=debug)
