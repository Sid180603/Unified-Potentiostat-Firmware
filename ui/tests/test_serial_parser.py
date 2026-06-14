"""
Unit tests for Phase 7 serial parsers and state machine transitions.

Layer 2 tests -- run with: cd ui && pytest tests/test_serial_parser.py -v
No serial port or real hardware required.

Coverage:
  - parse_data_line: valid 2-col, 3-col, marker rejection, malformed input
  - parse_linearity_line: valid, int dac, malformed
  - parse_step_line: valid, int elapsed_us, malformed
  - detect_peaks: Gaussian peaks at known voltages
  - ScanState machine via _process_line: all markers, error/info, data routing,
    QUERY accumulation, wrong-state data rejection
"""

import sys
import os
import pytest
from unittest.mock import MagicMock, patch, call

# Make the ui/ package importable from ui/tests/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app import (
    create_app, socketio, ScanState,
    parse_data_line, parse_linearity_line, parse_step_line, detect_peaks,
    _process_line, _finalize_query,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def app_instance():
    """Fresh app with IDLE state, no serial connection."""
    a = create_app(testing=True)
    a.scan_state = ScanState.IDLE
    a.next_mode = ScanState.CV
    a.current_scan = []
    a._query_buf = {'channels': {}}
    return a


# ---------------------------------------------------------------------------
# parse_data_line
# ---------------------------------------------------------------------------

class TestParseDataLine:
    def test_two_column_valid(self):
        assert parse_data_line('-0.5000,1.2300') == (-0.5, 1.23, None)

    def test_three_column_valid(self):
        assert parse_data_line('-0.5000,1.2300,-0.4900') == (-0.5, 1.23, -0.49)

    def test_three_column_with_spaces(self):
        assert parse_data_line('  -0.5000, 1.2300, -0.4900  ') == (-0.5, 1.23, -0.49)

    def test_scientific_notation(self):
        result = parse_data_line('1.0e-3,2.5e1,-9.9e-2')
        assert result is not None
        assert abs(result[0] - 0.001) < 1e-9
        assert abs(result[1] - 25.0) < 1e-9

    def test_star_marker_rejected(self):
        assert parse_data_line('*') is None

    def test_cv_end_marker_rejected(self):
        assert parse_data_line('#') is None

    def test_dpv_end_marker_rejected(self):
        assert parse_data_line('$') is None

    def test_linearity_start_rejected(self):
        assert parse_data_line('L*') is None

    def test_linearity_end_rejected(self):
        assert parse_data_line('L#') is None

    def test_step_start_rejected(self):
        assert parse_data_line('T*') is None

    def test_step_end_rejected(self):
        assert parse_data_line('T#') is None

    def test_query_start_rejected(self):
        assert parse_data_line('Q*') is None

    def test_query_end_rejected(self):
        assert parse_data_line('Q#') is None

    def test_error_line_rejected(self):
        assert parse_data_line('E: Vstart exceeds +/-1.65V') is None

    def test_info_header_rejected(self):
        assert parse_data_line('# stepE_actual=16.1mV') is None

    def test_query_header_rejected(self):
        assert parse_data_line('Q dac=512 Vin_theoretical=0.0000') is None

    def test_empty_string_rejected(self):
        assert parse_data_line('') is None

    def test_none_rejected(self):
        assert parse_data_line(None) is None

    def test_no_comma_rejected(self):
        assert parse_data_line('POTENTIOSTAT v1.0 SAMD21 ADS1115') is None

    def test_one_column_rejected(self):
        assert parse_data_line('-0.5000') is None

    def test_four_columns_rejected(self):
        assert parse_data_line('-0.5,1.2,3.4,5.6') is None

    def test_non_numeric_rejected(self):
        assert parse_data_line('abc,def') is None

    def test_partial_non_numeric_rejected(self):
        assert parse_data_line('-0.5,abc') is None

    def test_linearity_data_with_int_dac(self):
        # Linearity data '512,0.00000' -- two commas -> parses as (512.0, 0.0, None)
        # parse_data_line should parse it (it's valid 2-col), but the state machine
        # ensures it is never called in LINEARITY state. This test just documents that
        # the parser itself is data-agnostic -- routing is the state machine's job.
        result = parse_data_line('512,0.00000')
        assert result == (512.0, 0.0, None)


# ---------------------------------------------------------------------------
# parse_linearity_line
# ---------------------------------------------------------------------------

class TestParseLinearityLine:
    def test_valid(self):
        assert parse_linearity_line('512,0.00000') == (512, 0.0)

    def test_first_col_is_int(self):
        dac, v = parse_linearity_line('0,-1.65000')
        assert dac == 0
        assert isinstance(dac, int)
        assert abs(v - (-1.65)) < 1e-9

    def test_max_dac(self):
        dac, v = parse_linearity_line('1023,1.64900')
        assert dac == 1023

    def test_no_comma_rejected(self):
        assert parse_linearity_line('512') is None

    def test_empty_rejected(self):
        assert parse_linearity_line('') is None

    def test_none_rejected(self):
        assert parse_linearity_line(None) is None

    def test_non_int_dac_rejected(self):
        # Float dac column -- int() of '512.5' raises ValueError
        assert parse_linearity_line('512.5,0.00000') is None

    def test_non_numeric_voltage_rejected(self):
        assert parse_linearity_line('512,abc') is None


# ---------------------------------------------------------------------------
# parse_step_line
# ---------------------------------------------------------------------------

class TestParseStepLine:
    def test_valid(self):
        assert parse_step_line('0,15.2300') == (0, 15.23)

    def test_elapsed_is_int(self):
        elapsed, _ = parse_step_line('1160,12.5000')
        assert isinstance(elapsed, int)
        assert elapsed == 1160

    def test_no_comma_rejected(self):
        assert parse_step_line('1160') is None

    def test_empty_rejected(self):
        assert parse_step_line('') is None

    def test_none_rejected(self):
        assert parse_step_line(None) is None

    def test_non_int_elapsed_rejected(self):
        assert parse_step_line('1160.5,12.5000') is None

    def test_non_numeric_current_rejected(self):
        assert parse_step_line('1160,abc') is None


# ---------------------------------------------------------------------------
# detect_peaks
# ---------------------------------------------------------------------------

class TestDetectPeaks:
    def _gaussian_series(self, v_start, v_end, n, peak_v, amplitude):
        import math
        vs = [v_start + i * (v_end - v_start) / (n - 1) for i in range(n)]
        cs = [amplitude * math.exp(-(v - peak_v) ** 2 / 0.002) for v in vs]
        return vs, cs

    def test_single_peak_found(self):
        vs, cs = self._gaussian_series(-1.0, 1.0, 200, -0.4, 3.0)
        peaks = detect_peaks(vs, cs, height=0.05, distance_mv=150, prominence=0.05)
        assert len(peaks) == 1
        assert abs(peaks[0]['voltage'] - (-0.4)) < 0.05

    def test_four_gaussian_peaks(self):
        import math
        n = 200
        vs = [-1.0 + i * 2.0 / (n - 1) for i in range(n)]
        cs = [
            2.5 * math.exp(-(v + 0.80) ** 2 / 0.002)
            + 3.2 * math.exp(-(v + 0.40) ** 2 / 0.002)
            + 1.8 * math.exp(-(v - 0.00) ** 2 / 0.002)
            + 1.1 * math.exp(-(v - 0.35) ** 2 / 0.002)
            for v in vs
        ]
        peaks = detect_peaks(vs, cs, height=0.05, distance_mv=150, prominence=0.05)
        peak_vs = [p['voltage'] for p in peaks]
        assert any(abs(v - (-0.80)) < 0.05 for v in peak_vs), "Cd peak missing"
        assert any(abs(v - (-0.40)) < 0.05 for v in peak_vs), "Pb peak missing"
        assert any(abs(v - 0.00) < 0.05 for v in peak_vs), "Cu peak missing"

    def test_too_few_points_returns_empty(self):
        assert detect_peaks([0.0, 0.1], [1.0, 2.0]) == []

    def test_flat_line_returns_empty(self):
        vs = [i * 0.01 for i in range(100)]
        cs = [0.1] * 100
        assert detect_peaks(vs, cs) == []


# ---------------------------------------------------------------------------
# State machine via _process_line
# ---------------------------------------------------------------------------

class TestProcessLine:
    """Tests for _process_line: state transitions and emitted events.

    Strategy: patch socketio.emit at the module level so we can assert on
    every event emitted during a simulated firmware session.
    """

    @pytest.fixture(autouse=True)
    def patch_emit(self, monkeypatch):
        self.emitted = []

        def fake_emit(event, data=None):
            self.emitted.append((event, data))

        import app as app_module
        monkeypatch.setattr(app_module.socketio, 'emit', fake_emit)

    def _events(self, name):
        return [(e, d) for e, d in self.emitted if e == name]

    # -- CV flow --

    def test_cv_start_marker(self, app_instance):
        app_instance.next_mode = ScanState.CV
        _process_line(app_instance, '*')
        assert app_instance.scan_state == ScanState.CV
        assert self._events('scan_started') == [('scan_started', {'mode': 'CV'})]

    def test_dpv_start_marker(self, app_instance):
        app_instance.next_mode = ScanState.DPV
        _process_line(app_instance, '*')
        assert app_instance.scan_state == ScanState.DPV
        assert self._events('scan_started') == [('scan_started', {'mode': 'DPV'})]

    def test_star_with_no_next_mode_defaults_cv(self, app_instance):
        app_instance.next_mode = ScanState.LINEARITY  # not CV or DPV
        _process_line(app_instance, '*')
        assert app_instance.scan_state == ScanState.CV

    def test_cv_data_point_emitted(self, app_instance):
        app_instance.scan_state = ScanState.CV
        _process_line(app_instance, '-0.5000,1.2300,-0.4900')
        assert len(app_instance.current_scan) == 1
        pt = app_instance.current_scan[0]
        assert abs(pt['voltage'] - (-0.5)) < 1e-6
        assert abs(pt['current'] - 1.23) < 1e-6
        assert abs(pt['re'] - (-0.49)) < 1e-6
        assert self._events('new_datapoint')

    def test_cv_data_two_col_re_is_none(self, app_instance):
        app_instance.scan_state = ScanState.CV
        _process_line(app_instance, '-0.5000,1.2300')
        pt = app_instance.current_scan[0]
        assert pt['re'] is None

    def test_cv_end_marker(self, app_instance):
        app_instance.scan_state = ScanState.CV
        app_instance.current_scan = [{'voltage': 0.1, 'current': 0.2, 're': None}]
        _process_line(app_instance, '#')
        assert app_instance.scan_state == ScanState.IDLE
        complete_events = self._events('scan_complete')
        assert complete_events == [('scan_complete', {'type': 'CV', 'points': 1})]

    def test_cv_start_clears_buffer(self, app_instance):
        app_instance.scan_state = ScanState.IDLE
        app_instance.next_mode = ScanState.CV
        app_instance.current_scan = [{'stale': True}]
        _process_line(app_instance, '*')
        assert app_instance.current_scan == []

    # -- DPV flow + peaks --

    def test_dpv_end_emits_peaks_then_complete(self, app_instance):
        import math
        app_instance.scan_state = ScanState.DPV
        # Inject a Gaussian DPV dataset with one clear peak at -0.4 V
        vs = [-1.0 + i * 2.0 / 199 for i in range(200)]
        cs = [3.2 * math.exp(-(v + 0.40) ** 2 / 0.002) for v in vs]
        app_instance.current_scan = [{'voltage': v, 'current': c, 're': None}
                                     for v, c in zip(vs, cs)]
        _process_line(app_instance, '$')
        assert app_instance.scan_state == ScanState.IDLE
        peaks_events = self._events('peaks_detected')
        assert len(peaks_events) == 1
        peaks = peaks_events[0][1]['peaks']
        assert len(peaks) >= 1
        assert abs(peaks[0]['voltage'] - (-0.40)) < 0.05
        # scan_complete emitted AFTER peaks_detected
        event_names = [e for e, _ in self.emitted]
        assert event_names.index('peaks_detected') < event_names.index('scan_complete')

    def test_dpv_end_empty_scan_emits_empty_peaks(self, app_instance):
        app_instance.scan_state = ScanState.DPV
        app_instance.current_scan = []
        _process_line(app_instance, '$')
        peaks_events = self._events('peaks_detected')
        assert peaks_events[0][1]['peaks'] == []

    # -- Linearity flow --

    def test_linearity_start_marker(self, app_instance):
        _process_line(app_instance, 'L*')
        assert app_instance.scan_state == ScanState.LINEARITY
        assert self._events('scan_started') == [('scan_started', {'mode': 'LINEARITY'})]

    def test_linearity_data_point(self, app_instance):
        app_instance.scan_state = ScanState.LINEARITY
        _process_line(app_instance, '512,0.00123')
        assert len(app_instance.current_scan) == 1
        pt = app_instance.current_scan[0]
        assert pt['dac'] == 512
        assert abs(pt['voltage'] - 0.00123) < 1e-9
        assert self._events('linearity_point')

    def test_linearity_data_NOT_emitted_as_new_datapoint(self, app_instance):
        """Linearity data must never bleed into voltammogram events."""
        app_instance.scan_state = ScanState.LINEARITY
        _process_line(app_instance, '512,0.00123')
        assert not self._events('new_datapoint')

    def test_linearity_end_marker(self, app_instance):
        app_instance.scan_state = ScanState.LINEARITY
        app_instance.current_scan = [{'dac': i, 'voltage': 0.0} for i in range(5)]
        _process_line(app_instance, 'L#')
        assert app_instance.scan_state == ScanState.IDLE
        complete_events = self._events('scan_complete')
        assert complete_events == [('scan_complete', {'type': 'LINEARITY', 'points': 5})]

    # -- Step flow --

    def test_step_start_marker(self, app_instance):
        _process_line(app_instance, 'T*')
        assert app_instance.scan_state == ScanState.STEP
        assert self._events('scan_started') == [('scan_started', {'mode': 'STEP'})]

    def test_step_data_point(self, app_instance):
        app_instance.scan_state = ScanState.STEP
        _process_line(app_instance, '1160,12.5000')
        assert len(app_instance.current_scan) == 1
        pt = app_instance.current_scan[0]
        assert pt['elapsed_us'] == 1160
        assert abs(pt['current'] - 12.5) < 1e-9
        assert self._events('step_point')

    def test_step_data_NOT_emitted_as_new_datapoint(self, app_instance):
        app_instance.scan_state = ScanState.STEP
        _process_line(app_instance, '1160,12.5000')
        assert not self._events('new_datapoint')

    def test_step_end_marker(self, app_instance):
        app_instance.scan_state = ScanState.STEP
        app_instance.current_scan = [{'elapsed_us': 0, 'current': 5.0}] * 64
        _process_line(app_instance, 'T#')
        assert app_instance.scan_state == ScanState.IDLE
        complete_events = self._events('scan_complete')
        assert complete_events == [('scan_complete', {'type': 'STEP', 'points': 64})]

    # -- Query flow --

    def test_query_start_marker(self, app_instance):
        _process_line(app_instance, 'Q*')
        assert app_instance.scan_state == ScanState.QUERY
        assert self._events('scan_started') == [('scan_started', {'mode': 'QUERY'})]

    def test_query_header_parsed(self, app_instance):
        app_instance.scan_state = ScanState.QUERY
        app_instance._query_buf = {'channels': {}}
        _process_line(app_instance, 'Q dac=512 Vin_theoretical=0.0000')
        assert app_instance._query_buf['dac'] == 512
        assert app_instance._query_buf['vin_theoretical'] == 0.0

    def test_query_ain_channels_accumulated(self, app_instance):
        app_instance.scan_state = ScanState.QUERY
        app_instance._query_buf = {'channels': {}}
        for ch in range(4):
            _process_line(app_instance, f'AIN{ch}=1.{ch:04d}0')
        assert 'AIN0' in app_instance._query_buf['channels']
        assert 'AIN3' in app_instance._query_buf['channels']

    def test_query_diff_channels_accumulated(self, app_instance):
        app_instance.scan_state = ScanState.QUERY
        app_instance._query_buf = {'channels': {}}
        _process_line(app_instance, 'DIFF_0_1=0.12345')
        assert abs(app_instance._query_buf['channels']['DIFF_0_1'] - 0.12345) < 1e-9

    def test_query_end_emits_result(self, app_instance):
        app_instance.scan_state = ScanState.QUERY
        app_instance._query_buf = {
            'dac': 512,
            'vin_theoretical': 0.0,
            'channels': {'AIN0': 1.65, 'AIN1': 0.0},
        }
        _process_line(app_instance, 'Q#')
        assert app_instance.scan_state == ScanState.IDLE
        qr_events = self._events('query_result')
        assert len(qr_events) == 1
        result = qr_events[0][1]
        assert result['dac'] == 512
        assert result['channels']['AIN0'] == 1.65
        complete_events = self._events('scan_complete')
        assert len(complete_events) == 1
        assert complete_events[0][1] == {'type': 'QUERY', 'points': 2}

    def test_query_end_resets_buffer(self, app_instance):
        app_instance.scan_state = ScanState.QUERY
        app_instance._query_buf = {'dac': 512, 'vin_theoretical': 0.0, 'channels': {'AIN0': 1.65}}
        _process_line(app_instance, 'Q#')
        assert app_instance._query_buf == {'channels': {}}

    # -- Full mock session: Q* ... Q# sequence --

    def test_full_query_session(self, app_instance):
        """Replays the exact output sequence from mock_potentiostat.simulate_query(dac=512)."""
        lines = [
            'Q*',
            'Q dac=512 Vin_theoretical=0.0000',
            'AIN0=1.65000',
            'AIN1=0.00100',
            'AIN2=0.00050',
            'AIN3=1.65000',
            'DIFF_0_1=1.64900',
            'DIFF_0_3=0.00050',
            'DIFF_1_3=-1.64900',
            'DIFF_2_3=-1.65000',
            'Q#',
        ]
        for line in lines:
            _process_line(app_instance, line)

        qr_events = self._events('query_result')
        assert len(qr_events) == 1
        result = qr_events[0][1]
        assert result['dac'] == 512
        assert result['vin_theoretical'] == 0.0
        assert 'AIN0' in result['channels']
        assert 'DIFF_0_1' in result['channels']
        assert app_instance.scan_state == ScanState.IDLE
        complete_events = self._events('scan_complete')
        assert complete_events[0][1] == {'type': 'QUERY', 'points': 8}

    # -- Error and info lines --

    def test_error_line_emits_scan_error(self, app_instance):
        _process_line(app_instance, 'E: Vstart exceeds +/-1.65V')
        err_events = self._events('scan_error')
        assert err_events == [('scan_error', {'message': 'Vstart exceeds +/-1.65V'})]

    def test_error_line_resets_state(self, app_instance):
        app_instance.scan_state = ScanState.CV
        _process_line(app_instance, 'E: ADS1115 not found')
        assert app_instance.scan_state == ScanState.IDLE

    def test_error_in_idle_stays_idle(self, app_instance):
        _process_line(app_instance, 'E: Unknown command')
        assert app_instance.scan_state == ScanState.IDLE

    def test_info_header_emitted(self, app_instance):
        app_instance.scan_state = ScanState.DPV
        _process_line(app_instance, '# stepE_actual=16.1mV')
        info_events = self._events('scan_info')
        assert info_events == [('scan_info', {'message': 'stepE_actual=16.1mV'})]

    def test_info_header_does_NOT_change_state(self, app_instance):
        app_instance.scan_state = ScanState.DPV
        _process_line(app_instance, '# stepE_actual=16.1mV')
        assert app_instance.scan_state == ScanState.DPV

    # -- Cross-mode isolation --

    def test_linearity_data_ignored_in_cv_state(self, app_instance):
        """'512,0.00123' in CV state would pass parse_data_line (512.0, 0.00123, None).
        This test documents that the state machine routes correctly: it DOES parse
        '512,0.00123' as a CV datapoint (the parser is state-agnostic). The state machine
        is responsible for only being in CV state when firmware is running a CV scan.
        """
        app_instance.scan_state = ScanState.CV
        _process_line(app_instance, '512,0.00123')
        # Will be treated as a CV point -- this is expected and documents the contract:
        # state machine MUST be in correct state before data arrives.
        assert self._events('new_datapoint')

    def test_step_line_ignored_in_idle(self, app_instance):
        """Step data lines in IDLE state are silently discarded."""
        app_instance.scan_state = ScanState.IDLE
        _process_line(app_instance, '1160,12.5000')
        assert not self._events('new_datapoint')
        assert not self._events('step_point')
        assert len(app_instance.current_scan) == 0

    def test_cv_end_in_idle_transitions_back_to_idle(self, app_instance):
        """Spurious '#' in IDLE -- should not crash, stays IDLE."""
        app_instance.scan_state = ScanState.IDLE
        _process_line(app_instance, '#')
        assert app_instance.scan_state == ScanState.IDLE

    def test_q_hash_before_q_start_does_not_crash(self, app_instance):
        """Spurious 'Q#' in IDLE -- should emit empty query_result gracefully."""
        app_instance.scan_state = ScanState.IDLE
        app_instance._query_buf = {'channels': {}}
        _process_line(app_instance, 'Q#')
        assert app_instance.scan_state == ScanState.IDLE

    # -- scan_started emitted on every mode's start marker --

    @pytest.mark.parametrize('marker,expected_mode', [
        ('*', 'CV'),    # next_mode=CV by default in fixture
        ('L*', 'LINEARITY'),
        ('T*', 'STEP'),
        ('Q*', 'QUERY'),
    ])
    def test_scan_started_emitted_on_all_start_markers(self, marker, expected_mode, app_instance):
        app_instance.next_mode = ScanState.CV  # default
        _process_line(app_instance, marker)
        started_events = self._events('scan_started')
        assert len(started_events) == 1
        assert started_events[0][1]['mode'] == expected_mode
