import { createServer, type IncomingMessage, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { expect, test } from "./test";
import {
	getKillSwitch,
	getPaperOrders,
	getReconciliation,
	setKillSwitch,
} from "../lib/paper-api";
import { getExecutionMode, getLiveness } from "../lib/system-api";

interface Seen {
	method: string;
	url: string;
	body: string;
}

let server: Server;
let seen: Seen[] = [];
let port = 0;
// The healthz answer is per test: the execution-mode cases switch it between a derived
// mode, a payload that omits one and an upstream failure.
let modeReply: unknown = { status: "ok" };
let modeStatus = 200;

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
					scope: "global",
					scopes: ["global", "strategy"],
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
			response.statusCode = modeStatus;
			response.end(JSON.stringify(modeReply));
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
	modeReply = { status: "ok" };
	modeStatus = 200;
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

// EZ-258: the execution mode is server-derived (ADR-0035), so the shell reads it and
// never states one. A read that fails or answers without a mode is unknown, not PAPER.
test("the execution mode comes from the API health read", async () => {
	await withMode("live", async () => {
		modeReply = { status: "ok", mode: { execution: "paper", live_available: false } };
		const result = await getExecutionMode();
		expect(result.ok).toBe(true);
		if (!result.ok) return;
		expect(result.data).toBe("paper");
		expect(seen.at(-1)?.url).toBe("/healthz");
	});

	await withMode("live", async () => {
		modeReply = { status: "ok", mode: { execution: "live", live_available: true } };
		const result = await getExecutionMode();
		expect(result.ok).toBe(true);
		if (!result.ok) return;
		expect(result.data).toBe("live");
	});
});

test("the execution mode is unknown when the API fails or omits it", async () => {
	await withMode("live", async () => {
		modeReply = { detail: { code: "DB_DOWN", message: "health is down" } };
		modeStatus = 503;
		const result = await getExecutionMode();
		expect(result.ok).toBe(false);
		if (result.ok) return;
		expect(result.error.code).toBe("DB_DOWN");
	});

	await withMode("live", async () => {
		modeStatus = 200;
		modeReply = { status: "ok" };
		const result = await getExecutionMode();
		expect(result.ok).toBe(false);
		if (result.ok) return;
		expect(result.error.code).toBe("MODE_NOT_REPORTED");
	});

	await withMode("live", async () => {
		modeStatus = 200;
		modeReply = { status: "ok", mode: { execution: "turbo" } };
		const result = await getExecutionMode();
		expect(result.ok).toBe(false);
		if (result.ok) return;
		expect(result.error.code).toBe("MODE_NOT_REPORTED");
	});
});

test("fixture mode never invents an execution mode", async () => {
	await withMode("fixture", async () => {
		const result = await getExecutionMode();
		expect(result.ok).toBe(false);
		if (result.ok) return;
		expect(result.error.code).toBe("FIXTURE_NOT_PROBED");
	});
});
