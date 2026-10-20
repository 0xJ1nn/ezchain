// Minimal static server for the wireframe: node tools/serve-wireframe.mjs [port]
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, join, normalize, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(fileURLToPath(new URL("../design/wireframe", import.meta.url)));
const port = Number(process.argv[2] || 8181);
const types = { ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".json": "application/json", ".svg": "image/svg+xml" };

createServer(async (req, res) => {
	const url = new URL(req.url || "/", "http://localhost");
	let path = decodeURIComponent(url.pathname);
	if (path.endsWith("/")) path += "index.html";
	const file = normalize(join(root, path));
	if (!file.startsWith(root)) {
		res.writeHead(403).end("forbidden");
		return;
	}
	try {
		const body = await readFile(file);
		res.writeHead(200, { "content-type": types[extname(file)] || "application/octet-stream" });
		res.end(body);
	} catch {
		res.writeHead(404, { "content-type": "text/plain" }).end("not found");
	}
}).listen(port, "127.0.0.1", () => {
	console.log(`wireframe: http://127.0.0.1:${port}/`);
});
