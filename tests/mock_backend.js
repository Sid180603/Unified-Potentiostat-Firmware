/**
 * Mock SocketIO Backend — Node.js
 *
 * Emits synthetic potentiostat data for frontend-only development.
 * Optional: the ?test URL parameter in Flask covers most of the same ground.
 *
 * Usage: node mock_backend.js
 * Then open http://localhost:5000 in browser (frontend connects to this instead of Flask).
 */

const { Server } = require("socket.io");
const http = require("http");

const server = http.createServer();
const io = new Server(server, { cors: { origin: "*" } });

io.on("connection", (socket) => {
    console.log("[MOCK] Client connected:", socket.id);

    socket.emit("port_list", ["/tmp/vcom0", "COM5", "COM10"]);

    socket.on("connect_port", (data) => {
        console.log("[MOCK] connect_port:", data);
        socket.emit("port_connected", { port: data.port });
    });

    socket.on("start_scan", (data) => {
        console.log("[MOCK] start_scan:", data);
        const command = data.command || "D";
        const isDPV = command.startsWith("D") || command.startsWith("d");

        let v = -1.0;
        const step = isDPV ? 0.015 : (2.0 / 624);
        const totalPoints = isDPV ? 134 : 1248;
        let count = 0;
        let aborted = false;

        socket.on("abort_scan", () => {
            aborted = true;
        });

        const interval = setInterval(() => {
            if (aborted || count >= totalPoints) {
                clearInterval(interval);
                socket.emit("scan_complete", {
                    type: isDPV ? "DPV" : "CV",
                    points: count
                });
                return;
            }

            let current;
            if (isDPV) {
                // 4 Gaussian peaks
                current = 0;
                current += 2.5 * Math.exp(-Math.pow(v + 0.8, 2) / 0.002);
                current += 3.2 * Math.exp(-Math.pow(v + 0.4, 2) / 0.002);
                current += 1.8 * Math.exp(-Math.pow(v - 0.0, 2) / 0.002);
                current += 1.1 * Math.exp(-Math.pow(v - 0.35, 2) / 0.002);
                current += 0.01 * (Math.random() - 0.5);
            } else {
                // Butler-Volmer CV
                const eta = v - 0.22;
                current = 50 * (Math.exp(eta / 0.059) - Math.exp(-eta / 0.059));
                current = Math.max(-200, Math.min(200, current));
                current += 0.5 * (Math.random() - 0.5);

                // Reverse after halfway
                if (count >= totalPoints / 2) {
                    v -= step;
                } else {
                    v += step;
                }
                count++;
                socket.emit("new_datapoint", { voltage: v, current: current });
                return;
            }

            socket.emit("new_datapoint", { voltage: v, current: current });
            v += step;
            count++;
        }, 30);
    });

    socket.on("disconnect", () => {
        console.log("[MOCK] Client disconnected:", socket.id);
    });
});

const PORT = 5000;
server.listen(PORT, () => {
    console.log(`[MOCK] SocketIO backend listening on http://localhost:${PORT}`);
});
