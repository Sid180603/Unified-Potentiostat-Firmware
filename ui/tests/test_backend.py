"""
SocketIO event handler tests for Phase 7 backend.

Layer 2 tests -- run with: cd ui && pytest tests/test_backend.py -v
Uses flask-socketio test client + mock serial (no real port).

Coverage:
  - connect: port_list emitted, scan_resync emitted if scan in progress
  - connect_port: opens port, emits port_connected; error on bad port
  - disconnect_port: closes port, emits port_disconnected, resets state
  - start_scan: writes command, sets next_mode; scan_error if not connected
  - abort_scan: writes '!\\n' to serial; no-op if not connected
"""

import sys
import os
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app import create_app, socketio, ScanState


# ---------------------------------------------------------------------------
# Fixtures (supplement conftest.py)
# ---------------------------------------------------------------------------

@pytest.fixture
def app():
    a = create_app(testing=True)
    a.config['SERIAL_URL'] = 'loop://'
    return a


@pytest.fixture
def client(app):
    return socketio.test_client(app)


@pytest.fixture
def mock_serial():
    """Patch serial_for_url so no real port is opened."""
    with patch('app.serial.serial_for_url') as mock_factory:
        port = MagicMock()
        port.is_open = True
        port.readline.return_value = b''
        mock_factory.return_value = port
        yield port


@pytest.fixture
def connected_client(app, client, mock_serial):
    """Client with a simulated open port."""
    client.emit('connect_port', {'port': '/tmp/vcom0', 'baud': 115200})
    return client


# ---------------------------------------------------------------------------
# connect event
# ---------------------------------------------------------------------------

class TestConnect:
    def test_port_list_emitted_on_connect(self, client):
        received = client.get_received()
        events = {e['name']: e['args'] for e in received}
        assert 'port_list' in events

    def test_port_list_is_a_list(self, client):
        received = client.get_received()
        port_list_events = [e for e in received if e['name'] == 'port_list']
        assert len(port_list_events) == 1
        assert isinstance(port_list_events[0]['args'][0], list)

    def test_no_scan_resync_when_idle(self, app, client):
        assert app.scan_state == ScanState.IDLE
        received = client.get_received()
        names = [e['name'] for e in received]
        assert 'scan_resync' not in names

    def test_scan_resync_emitted_when_scan_in_progress(self, app, client):
        # Simulate an active scan: set state before a second client connects
        app.scan_state = ScanState.CV
        app.current_scan = [{'voltage': -0.5, 'current': 1.2, 're': -0.49}]
        client2 = socketio.test_client(app)
        received = client2.get_received()
        resync_events = [e for e in received if e['name'] == 'scan_resync']
        assert len(resync_events) == 1
        payload = resync_events[0]['args'][0]
        assert payload['state'] == 'CV'
        assert len(payload['points']) == 1

    def test_scan_resync_emitted_even_with_zero_points(self, app, client):
        """Scan just started (marker seen, no data yet) -- resync must still fire."""
        app.scan_state = ScanState.DPV
        app.current_scan = []
        client2 = socketio.test_client(app)
        received = client2.get_received()
        resync_events = [e for e in received if e['name'] == 'scan_resync']
        assert len(resync_events) == 1
        assert resync_events[0]['args'][0]['state'] == 'DPV'


# ---------------------------------------------------------------------------
# connect_port event
# ---------------------------------------------------------------------------

class TestConnectPort:
    def test_port_connected_emitted(self, app, client, mock_serial):
        client.emit('connect_port', {'port': '/tmp/vcom0', 'baud': 115200})
        received = client.get_received()
        events = {e['name']: e['args'] for e in received}
        assert 'port_connected' in events
        assert events['port_connected'][0]['port'] == '/tmp/vcom0'

    def test_serial_conn_set(self, app, client, mock_serial):
        client.emit('connect_port', {'port': '/tmp/vcom0', 'baud': 115200})
        assert app.serial_conn is not None

    def test_uses_serial_url_fallback(self, app, client, mock_serial):
        """If 'port' key is absent/empty, falls back to app.config['SERIAL_URL']."""
        import app as app_module
        with patch('app.serial.serial_for_url') as mock_factory:
            mock_factory.return_value = MagicMock(is_open=True, readline=MagicMock(return_value=b''))
            client.emit('connect_port', {})   # no 'port' key
            mock_factory.assert_called_once()
            # First positional arg is the port string
            called_port = mock_factory.call_args[0][0]
            assert called_port == app.config['SERIAL_URL']

    def test_scan_error_emitted_on_bad_port(self, app, client):
        with patch('app.serial.serial_for_url', side_effect=Exception('No such port')):
            client.emit('connect_port', {'port': '/dev/nonexistent'})
            received = client.get_received()
            error_events = [e for e in received if e['name'] == 'scan_error']
            assert len(error_events) == 1
            assert 'No such port' in error_events[0]['args'][0]['message']


# ---------------------------------------------------------------------------
# disconnect_port event
# ---------------------------------------------------------------------------

class TestDisconnectPort:
    def test_port_disconnected_emitted(self, app, connected_client, mock_serial):
        connected_client.get_received()  # consume connect_port events
        connected_client.emit('disconnect_port')
        received = connected_client.get_received()
        names = [e['name'] for e in received]
        assert 'port_disconnected' in names

    def test_serial_conn_cleared(self, app, connected_client, mock_serial):
        connected_client.emit('disconnect_port')
        assert app.serial_conn is None

    def test_scan_state_reset_to_idle(self, app, connected_client, mock_serial):
        app.scan_state = ScanState.CV
        connected_client.emit('disconnect_port')
        assert app.scan_state == ScanState.IDLE

    def test_port_closed_on_disconnect(self, app, connected_client, mock_serial):
        connected_client.emit('disconnect_port')
        mock_serial.close.assert_called()

    def test_disconnect_port_when_not_connected_is_safe(self, app, client):
        """disconnect_port with no open port should not raise."""
        assert app.serial_conn is None
        client.emit('disconnect_port')
        received = client.get_received()
        names = [e['name'] for e in received]
        assert 'port_disconnected' in names


# ---------------------------------------------------------------------------
# start_scan event
# ---------------------------------------------------------------------------

class TestStartScan:
    def test_command_written_to_serial(self, app, connected_client, mock_serial):
        connected_client.get_received()
        connected_client.emit('start_scan', {'command': 'C -1.0,1.0,1,30'})
        mock_serial.write.assert_called_with(b'C -1.0,1.0,1,30\n')

    def test_cv_sets_next_mode_cv(self, app, connected_client, mock_serial):
        connected_client.emit('start_scan', {'command': 'C -1.0,1.0,1,30'})
        assert app.next_mode == ScanState.CV

    def test_dpv_sets_next_mode_dpv(self, app, connected_client, mock_serial):
        connected_client.emit('start_scan', {'command': 'D -1.0,1.0,-1.0,5,15,90,100,25'})
        assert app.next_mode == ScanState.DPV

    def test_linearity_sets_next_mode_linearity(self, app, connected_client, mock_serial):
        connected_client.emit('start_scan', {'command': 'L 5'})
        assert app.next_mode == ScanState.LINEARITY

    def test_step_sets_next_mode_step(self, app, connected_client, mock_serial):
        connected_client.emit('start_scan', {'command': 'T 512,574,64'})
        assert app.next_mode == ScanState.STEP

    def test_query_sets_next_mode_query(self, app, connected_client, mock_serial):
        connected_client.emit('start_scan', {'command': 'Q 512'})
        assert app.next_mode == ScanState.QUERY

    def test_scan_error_when_not_connected(self, app, client):
        assert app.serial_conn is None
        client.get_received()
        client.emit('start_scan', {'command': 'D'})
        received = client.get_received()
        error_events = [e for e in received if e['name'] == 'scan_error']
        assert len(error_events) == 1
        assert error_events[0]['args'][0]['message'] == 'Not connected'

    def test_command_stripped_before_write(self, app, connected_client, mock_serial):
        """Extra whitespace in command is stripped."""
        connected_client.emit('start_scan', {'command': '  D  '})
        mock_serial.write.assert_called_with(b'D\n')


# ---------------------------------------------------------------------------
# abort_scan event
# ---------------------------------------------------------------------------

class TestAbortScan:
    def test_abort_writes_bang(self, app, connected_client, mock_serial):
        connected_client.emit('abort_scan')
        mock_serial.write.assert_called_with(b'!\n')

    def test_abort_when_not_connected_is_safe(self, app, client):
        """abort_scan with no open port should not raise."""
        assert app.serial_conn is None
        client.emit('abort_scan')  # should not throw
