"""
capture_scan.py — Capture a complete potentiostat scan to a CSV file.

Sends one command over serial, reads every emitted line until the scan's
end marker, and writes a clean CSV with an appropriate header. Designed for
reproducible data collection (thesis / sharing).

Usage (Windows, PlatformIO penv Python which has pyserial):
  python capture_scan.py --port COM5 --cmd "C -1.0,1.0,1,30" --out data.csv [--zero] [--label "..."]

Command -> end-marker mapping:
  C/c (CV)        -> "#"
  D/d (DPV)       -> "$"
  L/l (linearity) -> "L#"
  T/t (step)      -> "T#"

Data-line formats written to CSV:
  CV/DPV : voltage_V,current_uA,re_voltage_V   (3 columns from firmware)
  L      : dac_count,measured_volts_V
  T      : elapsed_us,current_uA
"""

import argparse
import re
import sys
import time
import serial


END_MARKERS = {
    'C': '#', 'D': '$', 'L': 'L#', 'T': 'T#',
}

HEADERS = {
    'C': 'voltage_V,current_uA,re_voltage_V',
    'D': 'voltage_V,current_uA,re_voltage_V',
    'L': 'dac_count,measured_volts_V',
    'T': 'elapsed_us,current_uA',
}


def is_data_line(line):
    """A data line has commas and starts with a digit or minus sign."""
    if ',' not in line:
        return False
    c = line[0]
    return c.isdigit() or c == '-'


def main():
    ap = argparse.ArgumentParser(description='Capture a potentiostat scan to CSV')
    ap.add_argument('--port', required=True, help='Serial port, e.g. COM5')
    ap.add_argument('--baud', type=int, default=115200)
    ap.add_argument('--cmd', required=True, help='Command to send, e.g. "C -1.0,1.0,1,30"')
    ap.add_argument('--out', required=True, help='Output CSV path')
    ap.add_argument('--zero', action='store_true', help='Send Z (auto-zero) before the scan')
    ap.add_argument('--label', default='', help='Optional comment line written at top of CSV')
    ap.add_argument('--timeout', type=float, default=120.0, help='Max seconds to wait for end marker')
    ap.add_argument('--max-offset-mv', type=float, default=100.0,
                    help='Abort if |Z offset| exceeds this (mV). High offset = likely floating WE. Use with --zero.')
    ap.add_argument('--force', action='store_true',
                    help='Capture even if the Z offset gate would abort (overrides --max-offset-mv).')
    ap.add_argument('--no-reset', action='store_true',
                    help='Accepted for compatibility. XIAO (native USB) does not reset on open at 115200, so captures already preserve board state.')
    args = ap.parse_args()

    cmd_char = args.cmd.strip()[0].upper()
    if cmd_char not in END_MARKERS:
        print(f"Unsupported command '{cmd_char}'. Use C, D, L, or T.", file=sys.stderr)
        sys.exit(2)
    end_marker = END_MARKERS[cmd_char]
    header = HEADERS[cmd_char]

    # XIAO is native-USB SAMD21: opening at 115200 does NOT auto-reset it, so the
    # board keeps whatever state (incl. WE contact) was established before launch.
    # DTR must be asserted or the USB-CDC data path stays disabled (no board output).
    ser = serial.Serial()
    ser.port = args.port
    ser.baudrate = args.baud
    ser.timeout = 1.0
    ser.dtr = True
    ser.rts = True
    ser.open()
    time.sleep(0.3)
    ser.reset_input_buffer()

    zero_offset_note = ''
    if args.zero:
        ser.write(b'Z\n')
        t0 = time.time()
        while time.time() - t0 < 5.0:
            ln = ser.readline().decode('utf-8', errors='ignore').strip()
            if ln.startswith('Z:'):
                zero_offset_note = ln
                print(f"[zero] {ln}")
                break
        # Go/no-go gate: a large offset means AIN0 is sitting at the floating-TIA
        # level, i.e. the working electrode is not connected. Capturing now would
        # produce a flat-zero CSV. Abort unless explicitly forced.
        if not zero_offset_note and not args.force:
            print("[GATE] Z sent but no response — board may be busy or disconnected. "
                  "Aborting (use --force to override).", file=sys.stderr)
            ser.close()
            sys.exit(3)
        m = re.search(r'offset=(-?[\d.]+)mV', zero_offset_note)
        if m:
            offset_mv = float(m.group(1))
            if abs(offset_mv) > args.max_offset_mv:
                msg = (f"[GATE] Z offset {offset_mv:.2f}mV exceeds +/-{args.max_offset_mv:.0f}mV "
                       f"-> working electrode likely FLOATING. Reseat the WE clip.")
                if args.force:
                    print(msg + " (continuing anyway: --force)", file=sys.stderr)
                else:
                    print(msg + " Aborting (use --force to override).", file=sys.stderr)
                    ser.close()
                    sys.exit(3)

    print(f"[send] {args.cmd}")
    ser.write((args.cmd + '\n').encode())

    rows = []
    extra = []           # non-data informational lines (e.g. stepE header)
    started = False
    t0 = time.time()
    while True:
        if time.time() - t0 > args.timeout:
            print(f"[warn] timeout after {args.timeout}s — wrote what was received", file=sys.stderr)
            break
        line = ser.readline().decode('utf-8', errors='ignore').strip()
        if not line:
            continue
        if line in ('*', 'L*', 'T*'):
            started = True
            continue
        if line == end_marker:
            break
        if line.startswith('E:'):
            print(f"[firmware error] {line}", file=sys.stderr)
            continue
        if is_data_line(line):
            rows.append(line)
        elif line.startswith('#') or line.startswith('Q') or line.startswith('POTENTIOSTAT'):
            extra.append(line)  # e.g. "# stepE_actual=16.1mV"

    ser.close()

    with open(args.out, 'w', encoding='utf-8', newline='') as f:
        if args.label:
            f.write(f"# {args.label}\n")
        f.write(f"# command: {args.cmd}\n")
        if zero_offset_note:
            f.write(f"# {zero_offset_note}\n")
        for e in extra:
            f.write(f"# {e}\n")
        f.write(f"# captured: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(header + '\n')
        for r in rows:
            f.write(r + '\n')

    print(f"[done] {len(rows)} data points -> {args.out}")


if __name__ == '__main__':
    main()
