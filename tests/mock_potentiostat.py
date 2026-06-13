"""
Mock Potentiostat — Simulates firmware serial protocol on a virtual COM port.

Responds to:
  C [params]  → Butler-Volmer CV (duck-curve shaped)
  D [params]  → 4 Gaussian DPV peaks (Cd, Pb, Cu, Hg)
  !           → Abort current scan
  Z           → Report zero offset
  I           → Firmware identity string

Usage (WSL dev):     python mock_potentiostat.py --port /tmp/vcom1
Usage (integration): python mock_potentiostat.py --port /tmp/vcom1 --verbose
"""

import serial
import time
import math
import argparse
import sys


class MockPotentiostat:
    def __init__(self, port_name, baud=115200, verbose=False):
        self.ser = serial.Serial(port_name, baud, timeout=0.1)
        self.abort = False
        self.verbose = verbose

    def log(self, msg):
        if self.verbose:
            print(f"[MOCK] {msg}", flush=True)

    def listen(self):
        """Main loop: wait for commands, respond with simulated data."""
        print(f"Mock potentiostat listening on {self.ser.port}", flush=True)
        while True:
            try:
                line = self.ser.readline().decode('utf-8', errors='ignore').strip()
                if not line:
                    continue

                self.log(f"RX: {line!r}")

                if line == '!':
                    self.abort = True
                    self.log("Abort flag set")
                    continue
                if line in ('Z', 'z'):
                    self.ser.write(b"Z: offset=1.6502mV\n")
                    self.log("Sent zero offset")
                    continue
                if line in ('I', 'i'):
                    self.ser.write(b"POTENTIOSTAT v1.0 SAMD21 ADS1115\n")
                    self.log("Sent identity")
                    continue
                if line[0] in ('C', 'c'):
                    self.simulate_cv(line)
                elif line[0] in ('D', 'd'):
                    self.simulate_dpv(line)
                elif line[0] in ('L', 'l'):
                    self.simulate_linearity(line)
                elif line[0] in ('T', 't'):
                    self.simulate_step(line)
                else:
                    self.ser.write(f"E: Unknown command '{line[0]}'\n".encode())

            except serial.SerialException as e:
                print(f"[MOCK] Serial error: {e}", flush=True)
                break
            except KeyboardInterrupt:
                break

        self.ser.close()
        print("[MOCK] Stopped.", flush=True)

    def simulate_cv(self, cmd):
        """Simulate CV with ferricyanide-like Butler-Volmer duck curve."""
        self.abort = False
        v_start = -1.0
        v_end = 1.0
        scan_rate = 0.010  # 10ms in dev mode (accelerated)

        self.ser.write(b"*\n")
        self.log(f"CV start: {v_start} to {v_end}")

        n_steps = 624
        step = (v_end - v_start) / n_steps

        # Forward scan
        v = v_start
        for _ in range(n_steps + 1):
            if self.abort:
                break
            # Butler-Volmer-ish current model
            eta = v - 0.22  # formal potential of ferricyanide
            i_fwd = 50.0 * (math.exp(eta / 0.059) - math.exp(-eta / 0.059))
            i_fwd = max(-200.0, min(200.0, i_fwd))
            # Add some noise
            noise = 0.5 * (hash(f"f{v:.6f}") % 100 - 50) / 50.0
            # Simulate RE voltage: VRE ≈ V_applied + small drift (~10 mV noise)
            re_noise = 0.01 * (hash(f"re_f{v:.6f}") % 100 - 50) / 50.0
            re_v = v + re_noise
            self.ser.write(f"{v:.4f},{i_fwd + noise:.4f},{re_v:.4f}\n".encode())
            time.sleep(scan_rate)
            v += step
            # Check for abort between points
            if self.ser.in_waiting:
                c = self.ser.read(self.ser.in_waiting).decode('utf-8', errors='ignore')
                if '!' in c:
                    self.abort = True

        # Reverse scan
        v = v_end
        for _ in range(n_steps + 1):
            if self.abort:
                break
            eta = v - 0.18  # slightly shifted for reverse (hysteresis)
            i_rev = 40.0 * (math.exp(eta / 0.059) - math.exp(-eta / 0.059))
            i_rev = max(-200.0, min(200.0, i_rev))
            noise = 0.5 * (hash(f"r{v:.6f}") % 100 - 50) / 50.0
            # Simulate RE voltage: VRE ≈ V_applied + small drift (~10 mV noise)
            re_noise = 0.01 * (hash(f"re_r{v:.6f}") % 100 - 50) / 50.0
            re_v = v + re_noise
            self.ser.write(f"{v:.4f},{i_rev + noise:.4f},{re_v:.4f}\n".encode())
            time.sleep(scan_rate)
            v -= step
            if self.ser.in_waiting:
                c = self.ser.read(self.ser.in_waiting).decode('utf-8', errors='ignore')
                if '!' in c:
                    self.abort = True

        self.ser.write(b"#\n")
        self.log(f"CV complete (aborted={self.abort})")

    def simulate_dpv(self, cmd):
        """Simulate DPV with 4 Gaussian peaks for heavy metals."""
        self.abort = False
        v_start = -1.0
        v_end = 1.0
        step_e_mv = 15.0

        self.ser.write(b"*\n")
        actual_step = step_e_mv * 1.073  # simulate 7.3% quantization error
        self.ser.write(f"# stepE_actual={actual_step:.1f}mV\n".encode())
        self.log(f"DPV start: {v_start} to {v_end}, step={step_e_mv}mV")

        step = step_e_mv / 1000.0
        v = v_start
        point_count = 0

        while v <= v_end + 0.0001:
            if self.abort:
                break

            # Superposition of 4 Gaussian peaks (simulated DPV response)
            di = 0.0
            di += 2.5 * math.exp(-(v + 0.80) ** 2 / 0.002)   # Cd²⁺ at -0.8V
            di += 3.2 * math.exp(-(v + 0.40) ** 2 / 0.002)   # Pb²⁺ at -0.4V
            di += 1.8 * math.exp(-(v - 0.00) ** 2 / 0.002)   # Cu²⁺ at 0.0V
            di += 1.1 * math.exp(-(v - 0.35) ** 2 / 0.002)   # Hg²⁺ at +0.35V

            # Add realistic noise (±12 nA level)
            noise = 0.012 * (hash(f"dpv{v:.6f}") % 100 - 50) / 50.0
            di += noise

            # Simulate RE voltage: VRE ≈ V_applied + small drift (~10 mV noise)
            re_noise = 0.01 * (hash(f"re_dpv{v:.6f}") % 100 - 50) / 50.0
            re_v = v + re_noise
            self.ser.write(f"{v:.4f},{di:.4f},{re_v:.4f}\n".encode())
            time.sleep(0.010)  # 10ms in dev mode (accelerated from 100ms real)
            v += step
            point_count += 1

            # Check for abort
            if self.ser.in_waiting:
                c = self.ser.read(self.ser.in_waiting).decode('utf-8', errors='ignore')
                if '!' in c:
                    self.abort = True

        self.ser.write(b"$\n")
        self.log(f"DPV complete: {point_count} points (aborted={self.abort})")

    def simulate_linearity(self, cmd):
        """Simulate DAC linearity sweep: 'dac_count,measured_volts' per step.

        Models the U3.1 level shifter as V = (dac - 512)/312 plus a small
        sinusoidal INL bow (~1.5 mV) and ADC noise (~0.5 mV).
        """
        self.abort = False
        step = 1
        args = cmd[1:].strip()
        if args:
            try:
                step = max(1, int(float(args.split(',')[0])))
            except ValueError:
                step = 1

        self.ser.write(b"L*\n")
        self.log(f"Linearity sweep start: step={step}")
        dac = 0
        while dac <= 1023:
            if self.abort:
                break
            v_ideal = (dac - 512) / 312.0
            inl = 0.0015 * math.sin(dac / 1023.0 * math.pi)      # ~1.5 mV bow
            noise = 0.0005 * (hash(f"lin{dac}") % 100 - 50) / 50.0
            v_meas = v_ideal + inl + noise
            self.ser.write(f"{dac},{v_meas:.5f}\n".encode())
            time.sleep(0.002)
            dac += step
            if self.ser.in_waiting:
                c = self.ser.read(self.ser.in_waiting).decode('utf-8', errors='ignore')
                if '!' in c:
                    self.abort = True

        self.ser.write(b"L#\n")
        self.log(f"Linearity sweep complete (aborted={self.abort})")

    def simulate_step(self, cmd):
        """Simulate step response: 'elapsed_us,current_uA' (Randles RC decay).

        i(t) = Iss + (I0 - Iss) * exp(-t/tau), modelling a 100 Ω series /
        10 kΩ || 1 µF dummy cell (tau = 10 ms).
        """
        self.abort = False
        dac_before, dac_after, n = 512, 574, 64
        args = cmd[1:].strip()
        if args:
            try:
                vals = [int(float(x)) for x in args.split(',')]
                if len(vals) >= 1:
                    dac_before = vals[0]
                if len(vals) >= 2:
                    dac_after = vals[1]
                if len(vals) >= 3:
                    n = max(1, vals[2])
            except ValueError:
                pass

        self.ser.write(b"T*\n")
        self.log(f"Step response start: {dac_before}->{dac_after}, n={n}")
        dv = (dac_after - dac_before) / 312.0    # applied voltage step (V)
        tau_ms = 10.0
        i0 = max(-200.0, min(200.0, dv / 100.0 * 1e6))    # peak via Rs=100Ω (µA)
        iss = dv / 10000.0 * 1e6                          # steady state via Rct=10k (µA)
        for k in range(n):
            if self.abort:
                break
            elapsed_us = int(k * 1160)   # ~1.16 ms per sample at 860 SPS
            i = iss + (i0 - iss) * math.exp(-(elapsed_us / 1000.0) / tau_ms)
            noise = 0.5 * (hash(f"step{k}") % 100 - 50) / 50.0
            self.ser.write(f"{elapsed_us},{i + noise:.4f}\n".encode())
            time.sleep(0.0012)
            if self.ser.in_waiting:
                c = self.ser.read(self.ser.in_waiting).decode('utf-8', errors='ignore')
                if '!' in c:
                    self.abort = True

        self.ser.write(b"T#\n")
        self.log(f"Step response complete (aborted={self.abort})")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Mock Potentiostat Firmware Simulator')
    parser.add_argument('--port', default='/tmp/vcom1',
                        help='Serial port to listen on (default: /tmp/vcom1)')
    parser.add_argument('--baud', type=int, default=115200,
                        help='Baud rate (default: 115200)')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Print debug messages')
    args = parser.parse_args()

    mock = MockPotentiostat(args.port, args.baud, args.verbose)
    try:
        mock.listen()
    except KeyboardInterrupt:
        print("\n[MOCK] Interrupted by user.", flush=True)
        sys.exit(0)
