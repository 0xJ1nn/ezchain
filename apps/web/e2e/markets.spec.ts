import { expect, test } from "@playwright/test";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import {
	asApiError,
	getMarkets,
	getQuote,
	postPaperOrder,
	webMode,
	type ApiError,
} from "../lib/api";

const BONK_MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263";

const noExternal = (page: import("@playwright/test").Page) => {
	const external: string[] = [];
	page.on("request", (request) => {
		const url = new URL(request.url());
		if (url.protocol === "http:" || url.protocol === "https:") {
			if (url.hostname !== "127.0.0.1" && url.hostname !== "localhost") {
				external.push(request.url());
			}
		}
	});
	return external;
};

test("markets renders the fixture screener with source, age and real filters", async ({
	page,
}) => {
	const external = noExternal(page);

	await page.goto("/markets");
	await expect(page.getByRole("heading", { name: "Markets", level: 1 })).toBeVisible();
	const modeBanner = page.getByRole("status", { name: "Data mode" });
	await expect(modeBanner.getByText("FIXTURE", { exact: true })).toBeVisible();
	await expect(page.getByText("LIVE · DexScreener")).toHaveCount(0);
	await expect(modeBanner).toContainText("local sample data · no API request");

	// Chain tabs come from the registry endpoint, not from hardcoded labels.
	const chainTabGroup = page.getByRole("group", { name: "Market chains" });
	await expect(chainTabGroup).toBeVisible();
	await expect(chainTabGroup.locator("a")).toHaveCount(9);
	await expect(chainTabGroup.getByRole("link", { name: "All chains" })).toHaveAttribute(
		"aria-current",
		"page",
	);
	for (const chain of ["Solana", "Base", "Ethereum", "BNB Chain", "Arbitrum One"]) {
		await expect(chainTabGroup.getByRole("link", { name: chain })).toBeVisible();
	}

	const marketTable = page.locator("table").first();
	await expect(marketTable.locator("tbody tr")).toHaveCount(12);
	await expect(marketTable.locator('thead th[scope="col"]')).toHaveCount(10);

	// Every row carries its source and age, and safety is unknown (not a pass).
	const firstRow = marketTable.locator("tbody tr").first();
	await expect(firstRow).toContainText("BONK");
	await expect(firstRow.locator("td").nth(7)).toHaveText(/Fixture · \d+s/);
	await expect(firstRow.locator("td").nth(9)).toHaveText("unknown");
	await expect(marketTable.locator("tbody tr td:nth-child(10)")).toHaveCount(12);

	// Trending is the API's 24h-volume order.
	await expect(firstRow).toContainText("BONK");
	const pepeRow = marketTable.locator("tbody tr").nth(1);
	await expect(pepeRow).toContainText("PEPE");

	// Pair age is a listing age; a fresh pair says so instead of pretending to be old.
	const mochiRow = marketTable.locator("tbody tr").filter({ hasText: "MOCHI" });
	await expect(mochiRow.locator("td").nth(8)).toHaveText(/new · \d+m/);

	// All rows link to the canonical token address route.
	const bonkRow = marketTable.locator("tbody tr").filter({ hasText: "BONK" });
	await expect(bonkRow.getByRole("link")).toHaveAttribute(
		"href",
		`/tokens/solana/${BONK_MINT}`,
	);
	await expect(marketTable.locator("tbody tr td:first-child a")).toHaveCount(12);

	await expect(page.getByText(/12 rows · limit 50 · source Fixture/)).toBeVisible();

	// Chain filter is a URL query param and refetches server-side.
	await chainTabGroup.getByRole("link", { name: "Base" }).click();
	await expect(page).toHaveURL(/chain=base/);
	await expect(marketTable.locator("tbody tr")).toHaveCount(4);
	await expect(marketTable.locator("tbody tr td:nth-child(2)")).toContainText([
		"Base",
		"Base",
		"Base",
		"Base",
	]);

	// Search form round-trips through the URL.
	await page.goto("/markets");
	await page.getByRole("searchbox", { name: "Search symbol, name or address" }).fill("bonk");
	await page.getByRole("button", { name: "Apply" }).click();
	await expect(page).toHaveURL(/q=bonk/);
	await expect(marketTable.locator("tbody tr")).toHaveCount(1);
	await expect(marketTable.locator("tbody tr").first()).toContainText("BONK");

	// Sort is a URL query param; liquidity order puts UNI (largest pool) first.
	await page.goto("/markets?sort=liquidity");
	await expect(page.getByLabel("Sort markets")).toHaveValue("liquidity");
	await expect(marketTable.locator("tbody tr").first()).toContainText("UNI");

	// Empty state is a reason code, not a zero row.
	await page.goto("/markets?q=zzzz");
	await expect(page.getByText("EMPTY_RESULT")).toBeVisible();
	await expect(page.getByText(/Nothing matched/)).toBeVisible();
	await expect(page.getByRole("link", { name: "Reset filters" })).toBeVisible();

	// API failures print the reason code; they never fall back to fixtures.
	await page.goto("/markets?chain=nope");
	await expect(page.getByText("CHAIN_NOT_FOUND")).toBeVisible();
	await expect(page.getByText(/unknown chain nope/)).toBeVisible();
	await expect(page.locator("table")).toHaveCount(0);

	expect(external, `unexpected external requests: ${external.join(", ")}`).toEqual([]);
});

test("markets shows the live/fixture mode banner honestly", async ({ page }) => {
	await page.goto("/markets");
	const banner = page.getByRole("status", { name: "Data mode" });
	await expect(banner).toBeVisible();
	await expect(banner.locator(".badge").first()).toHaveText("FIXTURE");
	await expect(banner).toContainText("local sample data · no API request");
	await expect(banner).not.toContainText("LIVE");
});

test("markets screenshot at desktop and mobile widths", async ({ page }) => {
	await page.goto("/markets");
	await page.screenshot({ path: ".shots/markets-1440.png", fullPage: true });

	await expect(page.getByText("Swipe to see more columns")).toBeHidden();

	await page.setViewportSize({ width: 390, height: 844 });
	await page.goto("/markets");
	await expect(page.getByRole("navigation", { name: "Primary mobile navigation" })).toBeVisible();
	await expect(
		page.getByRole("status", { name: "Data mode" }).getByText("FIXTURE", { exact: true }),
	).toBeVisible();

	// Secondary columns are hidden at 390px; the rest scrolls inside the table.
	const marketTable = page.locator("table").first();
	await expect(marketTable.locator("thead th").filter({ hasText: "Vol 24h" })).toBeHidden();
	await expect(marketTable.locator("thead th").filter({ hasText: "Pair age" })).toBeHidden();
	await expect(marketTable.locator("thead th").filter({ hasText: "Token" }).first()).toBeVisible();
	await expect(marketTable.locator("thead th").filter({ hasText: "Source · age" })).toBeVisible();

	const overflow = await page
		.locator(".table-wrap")
		.first()
		.evaluate((el) => el.scrollWidth - el.clientWidth);
	expect(overflow).toBeGreaterThan(0);
	await expect(page.getByText("Swipe to see more columns")).toBeVisible();

	await page.screenshot({ path: ".shots/markets-390.png", fullPage: true });
});

// ---------------------------------------------------------------------------
// API client contract. These run in Node (no page) against a local mock API so the
// live branch, reason codes and no-fallback rule are covered without a live feed.
// ---------------------------------------------------------------------------

interface MockApi {
	server: Server;
	port: number;
	requests: string[];
}

function json(res: import("node:http").ServerResponse, status: number, body: unknown) {
	res.statusCode = status;
	res.setHeader("content-type", "application/json");
	res.end(JSON.stringify(body));
}

async function startMockApi(): Promise<MockApi> {
	const requests: string[] = [];
	const server = createServer((req, res) => {
		const url = new URL(req.url ?? "/", "http://127.0.0.1");
		requests.push(`${req.method ?? "GET"} ${url.pathname}${url.search}`);
		if (req.method === "POST" && url.pathname === "/api/v1/paper/orders") {
			json(res, 502, {
				detail: {
					code: "NO_SELL_ROUTE",
					message: "buy has no full-size sell route",
					retryable: false,
				},
			});
			return;
		}
		if (url.pathname === "/api/v1/chains") {
			json(res, 200, []);
			return;
		}
		if (url.pathname === "/api/v1/quotes") {
			if (url.searchParams.get("amount_usd") === "9999") {
				json(res, 502, {
					detail: { code: "QUOTE_STALE", message: "quote older than the budget", retryable: true },
				});
				return;
			}
			json(res, 200, {
				chain: "base",
				token_in: "USDC",
				token_out: "0x940181a94A35A4569E4529A3CDfB74e38FD98631",
				side: "buy",
				amount_in: "25",
				amount_out: "19.47",
				price: "1.284",
				price_impact_pct: null,
				fee_usd: "0",
				kind: "paper_mark",
				route: null,
				source: { name: "dexscreener", url: null, observed_at: new Date().toISOString() },
			});
			return;
		}
		if (url.pathname === "/api/v1/markets") {
			const chain = url.searchParams.get("chain");
			const q = url.searchParams.get("q");
			if (chain === "unknown") {
				json(res, 404, {
					detail: { code: "CHAIN_NOT_FOUND", message: "unknown chain unknown", retryable: false },
				});
				return;
			}
			if (q === "ratelimit") {
				json(res, 429, {
					detail: { code: "RATE_LIMITED", message: "slow down", retryable: true },
				});
				return;
			}
			if (q === "boom") {
				json(res, 500, { detail: { code: "UPSTREAM_ERROR", message: "upstream exploded" } });
				return;
			}
			json(res, 200, {
				rows: [
					{
						chain: "solana",
						pair_address: "pair-live-only",
						dex: "raydium",
						base_token: "LiveOnlyMint1111111111111111111111111111",
						quote_token: "So11111111111111111111111111111111111111112",
						symbol: "LIVEONLY",
						name: "Live only",
						price_usd: "1.5",
						liquidity_usd: "100000",
						volume_24h_usd: "200000",
						price_change_m5: null,
						price_change_h1: "1.0",
						price_change_h6: null,
						price_change_h24: "2.0",
						pair_created_at: new Date().toISOString(),
						url: null,
						source: {
							name: "dexscreener",
							url: "https://dexscreener.com",
							observed_at: new Date().toISOString(),
						},
					},
				],
				source: {
					name: "dexscreener",
					url: "https://dexscreener.com",
					observed_at: new Date().toISOString(),
				},
				stale: false,
			});
			return;
		}
		json(res, 404, { detail: { code: "NOT_FOUND", message: "no mock route" } });
	});
	await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
	const address = server.address() as AddressInfo;
	return { server, port: address.port, requests };
}

async function closeMockApi(api: MockApi) {
	await new Promise<void>((resolve, reject) =>
		api.server.close((error) => (error ? reject(error) : resolve())),
	);
}

function withMode<T>(mode: "fixture" | "live", base: string | undefined, run: () => Promise<T>) {
	const previousMode = process.env.EZCHAIN_WEB_MODE;
	const previousUrl = process.env.EZCHAIN_API_URL;
	process.env.EZCHAIN_WEB_MODE = mode;
	if (base === undefined) delete process.env.EZCHAIN_API_URL;
	else process.env.EZCHAIN_API_URL = base;
	const restore = () => {
		if (previousMode === undefined) delete process.env.EZCHAIN_WEB_MODE;
		else process.env.EZCHAIN_WEB_MODE = previousMode;
		if (previousUrl === undefined) delete process.env.EZCHAIN_API_URL;
		else process.env.EZCHAIN_API_URL = previousUrl;
	};
	return run().finally(restore);
}

test("fixture mode returns fixture rows without touching the network", async () => {
	await withMode("fixture", undefined, async () => {
		expect(webMode()).toBe("fixture");
		const list = await getMarkets({ chain: "base" });
		expect(list.rows).toHaveLength(4);
		expect(list.source.name).toBe("fixture");
		expect(list.stale).toBe(false);

		const searched = await getMarkets({ q: "bonk" });
		expect(searched.rows.map((row) => row.symbol)).toEqual(["BONK"]);

		await expect(getMarkets({ chain: "nope" })).rejects.toMatchObject({
			code: "CHAIN_NOT_FOUND",
			status: 404,
		});
	});
});

test("live mode fetches the API and never falls back to fixtures", async () => {
	const api = await startMockApi();
	try {
		await withMode("live", `http://127.0.0.1:${api.port}`, async () => {
			expect(webMode()).toBe("live");
			const list = await getMarkets({ chain: "all", sort: "trending", limit: 50 });
			expect(list.rows.map((row) => row.symbol)).toEqual(["LIVEONLY"]);
			expect(list.source.name).toBe("dexscreener");
			expect(api.requests[0]).toContain("/api/v1/markets");
			expect(api.requests[0]).toContain("sort=trending");

			// Upstream 500: a reason code, not fixture rows.
			await expect(getMarkets({ q: "boom" })).rejects.toMatchObject({
				code: "UPSTREAM_ERROR",
				status: 500,
			});

			// Rate limit: explicit 429 reason, never a stale cache.
			await expect(getMarkets({ q: "ratelimit" })).rejects.toMatchObject({
				code: "RATE_LIMITED",
				status: 429,
				retryable: true,
			});

			await expect(getMarkets({ chain: "unknown" })).rejects.toMatchObject({
				code: "CHAIN_NOT_FOUND",
				status: 404,
			});
		});
	} finally {
		await closeMockApi(api);
	}
});

test("live quotes pass the query through and preserve the API reason code", async () => {
	const api = await startMockApi();
	try {
		await withMode("live", `http://127.0.0.1:${api.port}`, async () => {
			const quote = await getQuote({
				chain: "base",
				token: "0x940181a94A35A4569E4529A3CDfB74e38FD98631",
				side: "buy",
				amountUsd: "25",
			});
			expect(quote.kind).toBe("paper_mark");
			expect(quote.amount_in).toBe("25");
			expect(api.requests.at(-1)).toContain("amount_usd=25");
			expect(api.requests.at(-1)).toContain("side=buy");

			let error: ApiError | null = null;
			try {
				await getQuote({
					chain: "base",
					token: "0x940181a94A35A4569E4529A3CDfB74e38FD98631",
					side: "buy",
					amountUsd: "9999",
				});
			} catch (caught) {
				error = asApiError(caught);
			}
			if (!error) throw new Error("expected a QUOTE_STALE failure");
			expect(error).toMatchObject({ code: "QUOTE_STALE", status: 502, retryable: true });
			expect(error.toReason()).toEqual({
				code: "QUOTE_STALE",
				message: "quote older than the budget",
				retryable: true,
			});
		});
	} finally {
		await closeMockApi(api);
	}
});

test("paper order writes always hit the API and surface its reason", async () => {
	const api = await startMockApi();
	try {
		await withMode("fixture", `http://127.0.0.1:${api.port}`, async () => {
			// Writes are not mode-switched: even a fixture page posts to the API.
			const error = await postPaperOrder({
				chain: "solana",
				token_address: BONK_MINT,
				side: "buy",
				amount_usd: "100",
				idempotency_key: "test-key-0001",
			}).catch((caught: unknown) => asApiError(caught));
			expect(error).toMatchObject({ code: "NO_SELL_ROUTE", status: 502 });
			expect(api.requests.at(-1)).toBe("POST /api/v1/paper/orders");
		});
	} finally {
		await closeMockApi(api);
	}
});
