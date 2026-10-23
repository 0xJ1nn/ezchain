import { createServer, type IncomingMessage, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { expect, test } from "@playwright/test";
import {
	getKillSwitch,
	getPaperOrders,
	getReconciliation,
	setKillSwitch,
} from "../lib/paper-api";
import { getLiveness } from "../lib/system-api";

interface Seen {
	method: string;
	url: string;
	body: string;
}

let server: Server;
let seen: Seen[] = [];
let port = 0;

async function readBody(request: IncomingMessage): Promise<string> {
	const chunks: Buffer[] = [];
	for await (const chunk of request) chunks.push(Buffer.from(chunk));
	return Buffer.concat(chunks).toString("utf8");
}

test.beforeAll(async () => {
	server = createServer(async (request, response) => {
		const url = request.url ?? "";
		seen.push({
			method: request.method ?? "",
			url,
			body: await readBody(request),
		});
		response.setHeader("content-type", "application/json");
		if (url.startsWith("/api/v1/paper/orders")) {
			response.end(
				JSON.stringify([
					{
						id: "live-order-1",
						chain: "solana",
						token_address: "So11111111111111111111111111111111111111112",
						side: "buy",
						amount_usd: "5.00",
						status: "rejected",
						reason: "QUOTE_STALE",
						quote_kind: "jupiter",
						created_at: "2026-10-23T10:00:00Z",
						filled_at: null,
					},
				]),
			);
			return;
		}
		if (url.startsWith("/api/v1/system/kill-switch")) {
			const body = request.method === "POST" ? JSON.parse(seen.at(-1)?.body ?? "{}") : {};
			response.end(
				JSON.stringify({
					active: request.method === "POST" ? Boolean(body.active) : true,
					reason: request.method === "POST" ? (body.reason ?? null) : "live reason",
					updated_at: "2026-10-23T10:00:00Z",
					scope: "strategy",
				}),
			);
			return;
		}
		if (url.startsWith("/api/v1/paper/reconciliation")) {
			response.statusCode = 503;
			response.end(
				JSON.stringify({
					detail: {
						code: "DB_DOWN",
						message: "reconciliation could not read the database",
						retryable: true,
					},
				}),
			);
			return;
		}
		if (url === "/healthz") {
			response.end(JSON.stringify({ status: "ok" }));
			return;
		}
		response.statusCode = 404;
		response.end(JSON.stringify({ detail: { code: "NOT_FOUND", message: "not found" } }));
	});
	await new Promise<void>((resolve, reject) => {
		server.once("error", reject);
		server.listen(0, "127.0.0.1", resolve);
	});
	port = (server.address() as AddressInfo).port;
});

test.afterAll(async () => {
	await new Promise<void>((resolve) => server.close(() => resolve()));
});

test.beforeEach(() => {
	seen = [];
});

async function withMode<T>(mode: "fixture" | "live", run: () => Promise<T>): Promise<T> {
	const previousMode = process.env.EZCHAIN_WEB_MODE;
	const previousUrl = process.env.EZCHAIN_API_URL;
	process.env.EZCHAIN_WEB_MODE = mode;
	process.env.EZCHAIN_API_URL = `http://127.0.0.1:${port}`;
	const restore = () => {
		if (previousMode === undefined) delete process.env.EZCHAIN_WEB_MODE;
		else process.env.EZCHAIN_WEB_MODE = previousMode;
		if (previousUrl === undefined) delete process.env.EZCHAIN_API_URL;
		else process.env.EZCHAIN_API_URL = previousUrl;
	};
	return run().finally(restore);
}

test("paper reads in live mode hit the API and keep its reason codes", async () => {
	await withMode("live", async () => {
		const orders = await getPaperOrders(5);
		expect(orders.ok).toBe(true);
		if (!orders.ok) return;
		expect(orders.data[0]?.id).toBe("live-order-1");
		expect(orders.data[0]?.reason).toBe("QUOTE_STALE");
		expect(seen[0]?.url).toBe("/api/v1/paper/orders?limit=5");

		const reconciliation = await getReconciliation();
		expect(reconciliation.ok).toBe(false);
		if (reconciliation.ok) return;
		expect(reconciliation.error.code).toBe("DB_DOWN");
		expect(reconciliation.error.message).toContain("could not read the database");
	});
});

test("kill switch state and mutation use the API, never a fixture", async () => {
	await withMode("live", async () => {
		const state = await getKillSwitch();
		expect(state.ok).toBe(true);
		if (!state.ok) return;
		expect(state.data.active).toBe(true);

		const updated = await setKillSwitch(false, "e2e release");
		expect(updated.active).toBe(false);
		const post = seen.find((entry) => entry.method === "POST");
		expect(post?.url).toBe("/api/v1/system/kill-switch");
		expect(JSON.parse(post?.body ?? "{}")).toEqual({
			active: false,
			reason: "e2e release",
		});
	});
});

test("fixture mode does not invent a reconciliation or a process probe", async () => {
	await withMode("fixture", async () => {
		const reconciliation = await getReconciliation();
		expect(reconciliation.ok).toBe(false);
		if (reconciliation.ok) return;
		expect(reconciliation.error.code).toBe("FIXTURE_NOT_PROBED");

		const liveness = await getLiveness();
		expect(liveness.ok).toBe(false);
		if (liveness.ok) return;
		expect(liveness.error.code).toBe("FIXTURE_NOT_PROBED");
	});
});

test("liveness reads the API root, not the proxied /api path", async () => {
	await withMode("live", async () => {
		const liveness = await getLiveness();
		expect(liveness.ok).toBe(true);
		if (!liveness.ok) return;
		expect(liveness.data.status).toBe("ok");
		expect(seen[0]?.url).toBe("/healthz");
	});
});
