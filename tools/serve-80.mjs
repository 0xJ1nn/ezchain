#!/usr/bin/env node
// Serve the dev app on a privileged port (80 by default) by forwarding to `next dev`.
//
// A raw TCP proxy, not an HTTP one: `next dev`'s HMR socket is a WebSocket upgrade, and a
// byte-for-byte pipe carries it without knowing anything about HTTP. No dependencies, so it
// runs under `sudo` from a checkout without installing a proxy server.
//
// Binding a port below 1024 needs root on macOS and Linux. The server it forwards to must
// not be root, so this stays a thin forwarder and nothing runs with elevated privileges
// except the listener itself.
//
//   pnpm --filter @ezchain/web dev        # terminal 1, listens on :3000
//   sudo node tools/serve-80.mjs          # terminal 2
//   open http://localhost
//
// Usage: node tools/serve-80.mjs [--listen 80] [--target 3000] [--host 127.0.0.1]

import net from "node:net";

function flag(name, fallback) {
	const index = process.argv.indexOf(`--${name}`);
	if (index === -1) return fallback;
	const value = process.argv[index + 1];
	if (value === undefined || value.startsWith("--")) {
		throw new Error(`--${name} needs a value`);
	}
	return value;
}

const listenPort = Number(flag("listen", "80"));
const targetPort = Number(flag("target", "3000"));
const targetHost = flag("host", "127.0.0.1");

if (!Number.isInteger(listenPort) || listenPort < 1 || listenPort > 65535) {
	throw new Error("--listen must be a port number");
}
if (!Number.isInteger(targetPort) || targetPort < 1 || targetPort > 65535) {
	throw new Error("--target must be a port number");
}

const server = net.createServer((client) => {
	const upstream = net.connect({ host: targetHost, port: targetPort });

	// One socket closing must not leave the other half open, or a browser retry leaks a
	// connection per request until the process runs out of descriptors.
	const close = () => {
		client.destroy();
		upstream.destroy();
	};
	client.on("error", close);
	upstream.on("error", close);
	client.on("close", close);
	upstream.on("close", close);

	client.pipe(upstream);
	upstream.pipe(client);
});

server.on("error", (error) => {
	if (error.code === "EADDRINUSE") {
		console.error(`port ${listenPort} is already in use`);
	} else if (error.code === "EACCES") {
		console.error(`port ${listenPort} needs root: sudo node tools/serve-80.mjs`);
	} else {
		console.error(`forwarder failed: ${error.message}`);
	}
	process.exit(1);
});

server.listen(listenPort, () => {
	console.log(`http://localhost:${listenPort} → http://${targetHost}:${targetPort}`);
	console.log("ctrl-c to stop");
});

for (const signal of ["SIGINT", "SIGTERM"]) {
	process.on(signal, () => {
		server.close(() => process.exit(0));
	});
}
