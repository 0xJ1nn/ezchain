import { createServer, type IncomingMessage, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { expect, test } from "@playwright/test";
import { PROXY_TEST_PORT, PROXY_TEST_TOKEN } from "./proxy-env";

interface SeenRequest {
	method: string;
	url: string;
	authorization: string | undefined;
	contentType: string | undefined;
	body: string;
}

async function readBody(request: IncomingMessage): Promise<string> {
	const chunks: Buffer[] = [];
	for await (const chunk of request) chunks.push(Buffer.from(chunk));
	return Buffer.concat(chunks).toString("utf8");
}

let server: Server;
const seen: SeenRequest[] = [];

test.beforeAll(async () => {
	server = createServer(async (request, response) => {
		seen.push({
			method: request.method ?? "",
			url: request.url ?? "",
			authorization: request.headers.authorization,
			contentType: request.headers["content-type"],
			body: await readBody(request),
		});
		response.writeHead(200, {
			"content-type": "application/json",
			"x-request-id": "upstream-request-1",
		});
		response.end(JSON.stringify({ ok: true }));
	});
	await new Promise<void>((resolve, reject) => {
		server.once("error", reject);
		server.listen(PROXY_TEST_PORT, "127.0.0.1", resolve);
	});
	const address = server.address() as AddressInfo;
	expect(address.port).toBe(PROXY_TEST_PORT);
});

test.afterAll(async () => {
	await new Promise<void>((resolve) => server.close(() => resolve()));
});

test("the web proxy forwards mutations and drops the browser credential", async ({ request }) => {
	const payload = {
		chain: "solana",
		token_address: "So11111111111111111111111111111111111111112",
		side: "buy",
		amount_usd: "5.00",
		idempotency_key: "proxy-e2e-1",
	};

	const response = await request.post(
		"http://127.0.0.1:3100/api/v1/paper/orders?trace=1",
		{
			headers: { authorization: "Bearer browser-supplied-token" },
			data: payload,
		},
	);

	expect(response.status()).toBe(200);
	expect(response.headers()["x-request-id"]).toBe("upstream-request-1");
	expect(seen).toHaveLength(1);
	const forwarded = seen[0];
	expect(forwarded.method).toBe("POST");
	expect(forwarded.url).toBe("/api/v1/paper/orders?trace=1");
	expect(forwarded.authorization).toBe(`Bearer ${PROXY_TEST_TOKEN}`);
	expect(forwarded.authorization).not.toContain("browser-supplied-token");
	expect(forwarded.contentType).toContain("application/json");
	expect(JSON.parse(forwarded.body)).toEqual(payload);

	const read = await request.get("http://127.0.0.1:3100/api/v1/markets?chain=solana");
	expect(read.status()).toBe(200);
	expect(seen[1].method).toBe("GET");
	expect(seen[1].url).toBe("/api/v1/markets?chain=solana");
	expect(seen[1].authorization).toBe(`Bearer ${PROXY_TEST_TOKEN}`);
});
