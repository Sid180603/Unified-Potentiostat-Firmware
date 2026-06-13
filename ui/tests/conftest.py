"""
Shared pytest fixtures for UI backend tests.
"""

import pytest
from unittest.mock import MagicMock, patch
import sys
import os

# Add ui/ to path so we can import app
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app import create_app, socketio


@pytest.fixture
def app():
    """Create test app instance."""
    app = create_app(testing=True)
    app.config['SERIAL_URL'] = 'loop://'
    return app


@pytest.fixture
def client(app):
    """Create SocketIO test client."""
    return socketio.test_client(app)


@pytest.fixture
def mock_serial():
    """Mock serial.Serial for tests that don't need a real port."""
    with patch('app.serial.serial_for_url') as mock:
        port = MagicMock()
        port.is_open = True
        port.readline.return_value = b''
        mock.return_value = port
        yield port
