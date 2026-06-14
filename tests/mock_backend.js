/**
 * Mock SocketIO Backend — Node.js
 *
 * Emits synthetic potentiostat data for frontend-only development, mirroring the
 * Python firmware mock (tests/mock_potentiostat.py) at the SOCKET-EVENT level
 * (it speaks the Phase 8 browser API, not raw serial lines).
 *
 * Optional: the ?test URL parameter in Flask covers most of the same ground.
 *
 * Socket contract (matches the locked Phase 7/8 design):
 *   port_list        ['/tmp/vcom0', 'COM5', ...]
 *   port_connected   { port }
 *   port_disconnected
 *   scan_started     { mode }                          (on every scan start)
 *   new_datapoint    { voltage, current, re }          (CV / DPV — 3 columns incl. RE)
 *   linearity_point  { dac, voltage }                  (L command)
 *   step_point       { elapsed_us, current }           (T command)
 *   query_result     { dac, vin_theoretical, channels }(Q command — one-shot)
 *   peaks_detected   { peaks: [{ voltage, current }] } (after DPV)
 *   scan_complete    { type, points }
 *   scan_error       { message }
 *
 * Usage: node mock_backend.js
 * Then open http://localhost:5000 in browser (frontend connects to this instead of Flask).
 */

const { Server } = require("socket.io");
const http = require("http");

const server = http.createServer();
const io = new Server(server, { cors: { origin: "*" } });

// Deterministic pseudo-noise so runs are reproducible (mirrors the Python hash() trick).
function noise(seed, amp) {
    let h = 0;
    for (let i = 0; i < seed.length; i++) {
        h = (h * 31 + seed.charCodeAt(i)) | 0;
    }
    return (amp * (((h % 100) + 100) % 100 - 50)) / 50.0;
}

const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));

io.on("connection", (socket) => {
    console.log("[MOCK] Client connected:", socket.id);

    let aborted = false;
    let running = null; // active interval handle (single source of truth)

    socket.emit("port_list", ["/tmp/vcom0", "COM5", "COM10"]);

    socket.on("connect_port", (data) => {
        console.log("[MOCK] connect_port:", data);
        socket.emit("port_connected", { port: (data && data.port) || "/tmp/vcom0" });
    });

    socket.on("disconnect_port", () => {
        console.log("[MOCK] disconnect_port");
        if (running) { clearInterval(running); running = null; }
        socket.emit("port_disconnected");
    });

    // Top-level (registered once) — no per-scan listener leak.
    socket.on("abort_scan", () => {
        aborted = true;
    });

    socket.on("start_scan", (data) => {
        if (running) clearInterval(running);
        aborted = false;
        const command = ((data && data.command) || "D").trim();
        const c = command[0].toUpperCase();
        console.log("[MOCK] start_scan:", command);

        if (c === "C") return runCV();
        if (c === "D") return runDPV();
        if (c === "L") return runLinearity(command);
        if (c === "T") return runStep(command);
        if (c === "Q") return runQuery(command);
        socket.emit("scan_error", { message: `Unknown command '${c}'` });
    });

    socket.on("disconnect", () => {
        if (running) { clearInterval(running); running = null; }
        console.log("[MOCK] Client disconnected:", socket.id);
    });

    // --- CV: Butler-Volmer duck curve, forward then reverse, with RE column ---
    function runCV() {
        socket.emit("scan_started", { mode: "CV" });
        const vStart = -1.0, vEnd = 1.0, nSteps = 624;
        const step = (vEnd - vStart) / nSteps;
        let v = vStart;
        let count = 0;
        let reverse = false;

        running = setInterval(() => {
            if (aborted || (reverse && count >= 2 * (nSteps + 1))) {
                clearInterval(running);
                running = null;
                socket.emit("scan_complete", { type: "CV", points: count });
                return;
            }
            const e0 = reverse ? 0.18 : 0.22; // slight hysteresis on reverse
            const k = reverse ? 40.0 : 50.0;
            const eta = v - e0;
            let i = clamp(k * (Math.exp(eta / 0.059) - Math.exp(-eta / 0.059)), -200, 200);
            i += noise(`cv${v.toFixed(6)}`, 0.5);
            const re = v + noise(`recv${v.toFixed(6)}`, 0.01);
            socket.emit("new_datapoint", { voltage: v, current: i, re: re });
            count++;
            if (!reverse) {
                v += step;
                if (count > nSteps) { reverse = true; v = vEnd; }
            } else {
                v -= step;
            }
        }, 5);
    }

    // --- DPV: 4 Gaussian peaks (Cd/Pb/Cu/Hg) + RE, then peaks_detected ---
    function runDPV() {
        socket.emit("scan_started", { mode: "DPV" });
        const vStart = -1.0, vEnd = 1.0, step = 0.015;
        let v = vStart;
        let count = 0;
        const peaks = [
            { voltage: -0.80, current: 2.5 }, // Cd²⁺
            { voltage: -0.40, current: 3.2 }, // Pb²⁺
            { voltage: 0.00, current: 1.8 }, // Cu²⁺
            { voltage: 0.35, current: 1.1 }, // Hg²⁺
        ];

        running = setInterval(() => {
            if (aborted || v > vEnd + 1e-4) {
                clearInterval(running);
                running = null;
                socket.emit("peaks_detected", { peaks });
                socket.emit("scan_complete", { type: "DPV", points: count });
                return;
            }
            let i = 0;
            i += 2.5 * Math.exp(-Math.pow(v + 0.80, 2) / 0.002); // Cd²⁺ @ -0.8V
            i += 3.2 * Math.exp(-Math.pow(v + 0.40, 2) / 0.002); // Pb²⁺ @ -0.4V
            i += 1.8 * Math.exp(-Math.pow(v - 0.00, 2) / 0.002); // Cu²⁺ @  0.0V
            i += 1.1 * Math.exp(-Math.pow(v - 0.35, 2) / 0.002); // Hg²⁺ @ +0.35V
            i += noise(`dpv${v.toFixed(6)}`, 0.012);
            const re = v + noise(`redpv${v.toFixed(6)}`, 0.01);
            socket.emit("new_datapoint", { voltage: v, current: i, re: re });
            count++;
            v += step;
        }, 10);
    }

    // --- L: DAC linearity sweep, 'dac, measured_volts' with ~1.5 mV INL bow ---
    function runLinearity(command) {
        socket.emit("scan_started", { mode: "LINEARITY" });
        let stepSize = 1;
        const args = command.slice(1).trim();
        if (args) {
            const parsed = parseInt(args.split(",")[0], 10);
            if (!Number.isNaN(parsed)) stepSize = Math.max(1, parsed);
        }
        let dac = 0;
        running = setInterval(() => {
            if (aborted || dac > 1023) {
                clearInterval(running);
                running = null;
                socket.emit("scan_complete", { type: "LINEARITY", points: dac });
                return;
            }
            const vIdeal = (dac - 512) / 312.0;
            const inl = 0.0015 * Math.sin((dac / 1023.0) * Math.PI); // ~1.5 mV bow
            const voltage = vIdeal + inl + noise(`lin${dac}`, 0.0005);
            socket.emit("linearity_point", { dac: dac, voltage: voltage });
            dac += stepSize;
        }, 2);
    }

    // --- T: step response, 'elapsed_us, current_uA' (Randles RC decay, tau=10ms) ---
    function runStep(command) {
        socket.emit("scan_started", { mode: "STEP" });
        let dacBefore = 512, dacAfter = 574, n = 64;
        const args = command.slice(1).trim();
        if (args) {
            const vals = args.split(",").map((x) => parseInt(x, 10));
            if (vals.length >= 1 && !Number.isNaN(vals[0])) dacBefore = vals[0];
            if (vals.length >= 2 && !Number.isNaN(vals[1])) dacAfter = vals[1];
            if (vals.length >= 3 && !Number.isNaN(vals[2])) n = Math.max(1, vals[2]);
        }
        const dv = (dacAfter - dacBefore) / 312.0; // applied voltage step (V)
        const tauMs = 10.0;
        const i0 = clamp((dv / 100.0) * 1e6, -200, 200); // peak via Rs=100Ω (µA)
        const iss = (dv / 10000.0) * 1e6;                // steady state via Rct=10k (µA)
        let k = 0;
        running = setInterval(() => {
            if (aborted || k >= n) {
                clearInterval(running);
                running = null;
                socket.emit("scan_complete", { type: "STEP", points: k });
                return;
            }
            const elapsedUs = k * 1160; // ~1.16 ms per sample at 860 SPS
            const i = iss + (i0 - iss) * Math.exp(-(elapsedUs / 1000.0) / tauMs)
                + noise(`step${k}`, 0.5);
            socket.emit("step_point", { elapsed_us: elapsedUs, current: i });
            k++;
        }, 2);
    }

    // --- Q: one-shot channel query — all ADS1115 channels at one DAC value ---
    function runQuery(command) {
        socket.emit("scan_started", { mode: "QUERY" });
        let dac = 512;
        const args = command.slice(1).trim();
        if (args) {
            const parsed = parseInt(args.split(",")[0], 10);
            if (!Number.isNaN(parsed)) dac = clamp(parsed, 0, 1023);
        }
        const vinTheoretical = (dac - 512) / 312.0;
        // AIN1 is the channel that actually carries Vin (single-ended clips negatives to 0).
        // DIFF pairs report the bipolar value so Vin shows even when single-ended clips.
        const ainVin = Math.max(0, vinTheoretical);
        const channels = {
            AIN0: 1.65 + noise(`q0_${dac}`, 0.001),       // TIA output (idle ~mid-rail)
            AIN1: ainVin + noise(`q1_${dac}`, 0.001),     // Vin readback (tracks DAC)
            AIN2: noise(`q2_${dac}`, 0.001),              // unused
            AIN3: 1.65 + noise(`q3_${dac}`, 0.001),       // reference rail
            DIFF_0_1: 1.65 - vinTheoretical + noise(`qd01_${dac}`, 0.001),
            DIFF_0_3: noise(`qd03_${dac}`, 0.001),
            DIFF_1_3: vinTheoretical - 1.65 + noise(`qd13_${dac}`, 0.001),
            DIFF_2_3: -1.65 + noise(`qd23_${dac}`, 0.001),
        };
        socket.emit("query_result", {
            dac: dac,
            vin_theoretical: vinTheoretical,
            channels: channels,
        });
        socket.emit("scan_complete", { type: "QUERY", points: 8 });
    }
});

const PORT = 5000;
server.listen(PORT, () => {
    console.log(`[MOCK] SocketIO backend listening on http://localhost:${PORT}`);
});
