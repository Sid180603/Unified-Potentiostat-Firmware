"""
check_contact.py — Quick working-electrode contact check via the Z offset.

Opens the port with DTR asserted (required for XIAO USB-CDC data path) and
sends a single Z (auto-zero), then reports the offset with a pass/fail verdict.
The XIAO SAMD21 (native USB) does not auto-reset on port open at 115200 baud —
bootloader entry requires the separate 1200-baud touch — so the board keeps
whatever state was established before this script opens the port.

Interpretation:
  |offset| < ~50 mV   -> WE connected, good contact          (PASS)
  |offset| 50-100 mV  -> marginal, reseat recommended         (WARN)
  |offset| > 100 mV   -> WE floating / not connected          (FAIL)

A floating LM324 TIA rests near -0.46 V, so a floating WE gives offset ~ -460 mV.
A connected cell at rest gives a few mV.

Usage:
  python check_contact.py --port COM5
"""

import argparse
import re
import sys
import time
import serial


def main():
    ap = argparse.ArgumentParser(description='Check WE contact via Z offset (no board reset)')
    ap.add_argument('--port', required=True)
    ap.add_argument('--baud', type=int, default=115200)
    ap.add_argument('--warn-mv', type=float, default=50.0)
    ap.add_argument('--fail-mv', type=float, default=100.0)
    args = ap.parse_args()

    # XIAO is native-USB SAMD21: opening at 115200 does NOT auto-reset it (bootloader
    # entry is via the separate 1200-bps touch). DTR must be asserted, though, or the
    # USB-CDC data path stays disabled and the board's output never reaches the host.
    ser = serial.Serial()
    ser.port = args.port
    ser.baudrate = args.baud
    ser.timeout = 1.0
    ser.dtr = True
    ser.rts = True
    ser.open()
    time.sleep(0.3)
    ser.reset_input_buffer()

    ser.write(b'Z\n')
    note = ''
    t0 = time.time()
    while time.time() - t0 < 5.0:
        ln = ser.readline().decode('utf-8', errors='ignore').strip()
        if ln.startswith('Z:'):
            note = ln
            break
    ser.close()

    if not note:
        print("[FAIL] No Z response — board not responding (wrong port? not flashed?)", file=sys.stderr)
        sys.exit(3)

    m = re.search(r'offset=(-?[\d.]+)mV', note)
    if not m:
        print(f"[?] Got '{note}' but could not parse offset", file=sys.stderr)
        sys.exit(3)

    off = float(m.group(1))
    a = abs(off)
    if a > args.fail_mv:
        print(f"[FAIL] Z offset {off:.2f} mV -> WE FLOATING / not connected. Reseat the WE clip.")
        sys.exit(1)
    elif a > args.warn_mv:
        print(f"[WARN] Z offset {off:.2f} mV -> marginal contact. Reseat recommended before capture.")
        sys.exit(0)
    else:
        print(f"[PASS] Z offset {off:.2f} mV -> WE connected, good contact. Ready to capture.")
        sys.exit(0)


if __name__ == '__main__':
    main()
