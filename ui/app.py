"""
Unified Potentiostat — Flask + SocketIO Backend

App factory pattern for testability.
Dev mode: POTENTIOSTAT_DEV=1, uses loop:// or socat PTY.
Prod mode: POTENTIOSTAT_DEV=0, uses real COM port.

Full implementation in Phase 7. This is the skeleton.
"""

import os
import threading
import serial
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit

socketio = SocketIO()


def create_app(testing=False):
    """App factory — allows pytest to create fresh instances."""
    app = Flask(__name__)

    dev_mode = testing or os.environ.get('POTENTIOSTAT_DEV', '0') == '1'

    app.config['SECRET_KEY'] = 'potentiostat-dev-key'
    app.config['DEV_MODE'] = dev_mode
    app.config['SERIAL_URL'] = os.environ.get('SERIAL_URL', 'loop://' if dev_mode else '')

    if dev_mode:
        cors_origins = '*'
    else:
        cors_origins = None

    socketio.init_app(app, cors_allowed_origins=cors_origins,
                      logger=dev_mode, engineio_logger=dev_mode)

    # --- State ---
    app.serial_conn = None
    app.serial_thread = None
    app.scanning = False

    # --- Routes ---

    @app.route('/')
    def index():
        return render_template('index.html', dev_mode=dev_mode)

    # --- SocketIO Events (Phase 7 will flesh these out) ---

    @socketio.on('connect')
    def handle_connect():
        emit('port_list', get_available_ports())

    @socketio.on('connect_port')
    def handle_connect_port(data):
        port = data.get('port', '')
        baud = data.get('baud', 115200)
        try:
            app.serial_conn = serial.serial_for_url(port, baudrate=baud, timeout=0.1)
            emit('port_connected', {'port': port})
        except Exception as e:
            emit('scan_error', {'message': str(e)})

    @socketio.on('disconnect_port')
    def handle_disconnect_port():
        if app.serial_conn and app.serial_conn.is_open:
            app.serial_conn.close()
            app.serial_conn = None
        emit('port_disconnected')

    @socketio.on('start_scan')
    def handle_start_scan(data):
        command = data.get('command', 'D')
        if app.serial_conn and app.serial_conn.is_open:
            app.serial_conn.write((command + '\n').encode())
            app.scanning = True
            # Phase 7: start serial reader thread here
        else:
            emit('scan_error', {'message': 'Not connected'})

    @socketio.on('abort_scan')
    def handle_abort_scan():
        if app.serial_conn and app.serial_conn.is_open:
            app.serial_conn.write(b'!\n')

    return app


def get_available_ports():
    """List available serial ports."""
    try:
        from serial.tools import list_ports
        return [p.device for p in list_ports.comports()]
    except Exception:
        return []


def parse_data_line(line):
    """
    Parse a CSV data line from the potentiostat.
    Returns (voltage, current, re_voltage) tuple or None if not a data line.
    re_voltage is None when the firmware sends legacy 2-column output.
    """
    if not line or not line.strip():
        return None
    line = line.strip()
    if line in ('*', '#', '$') or line.startswith('# ') or line.startswith('E:'):
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

    peaks, props = find_peaks(currents, height=height,
                              distance=distance_pts, prominence=prominence)
    return [{'voltage': float(voltages[i]), 'current': float(currents[i])} for i in peaks]


# --- Entry Point ---

if __name__ == '__main__':
    app = create_app()
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    socketio.run(app, host='0.0.0.0', port=5000, debug=debug)
