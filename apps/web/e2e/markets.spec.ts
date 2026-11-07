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
	await expect(marketTable.locator("tbody tr")).toHaveCount(14);
	await expect(marketTable.locator('thead th[scope="col"]')).toHaveCount(10);

	// Every row carries its source and age, and safety is unknown (not a pass).
	const firstRow = marketTable.locator("tbody tr").first();
	await expect(firstRow).toContainText("BONK");
	await expect(firstRow.locator("td").nth(7)).toHaveText(/Fixture · \d+s/);
	await expect(firstRow.locator("td").nth(9)).toHaveText("unknown");
	await expect(marketTable.locator("tbody tr td:nth-child(10)")).toHaveCount(14);

	// All-chain trending is an honest per-chain round robin, not a global ranking.
	await expect(firstRow).toContainText("BONK");
	await expect(page.getByText("PER-CHAIN ROUND ROBIN", { exact: true })).toBeVisible();
	const secondRow = marketTable.locator("tbody tr").nth(1);
	await expect(secondRow).toContainText("PEPE");
	const thirdRow = marketTable.locator("tbody tr").nth(2);
	await expect(thirdRow).toContainText("AERO");

	// Pair age is a listing age; a fresh pair says so instead of pretending to be old.
	const mochiRow = marketTable.locator("tbody tr").filter({ hasText: "MOCHI" });
	await expect(mochiRow.locator("td").nth(8)).toHaveText(/new · \d+m/);

	// All rows link to the canonical token address route.
	const bonkRow = marketTable.locator("tbody tr").filter({ hasText: "BONK" });
	await expect(bonkRow.getByRole("link")).toHaveAttribute(
		"href",
		`/tokens/solana/${BONK_MINT}`,
	);
	await expect(marketTable.locator("tbody tr td:first-child a")).toHaveCount(14);

	await expect(page.getByText(/14 of 14 markets · showing 1–14/)).toBeVisible();
	await expect(page.getByText(/page 1 of 1/)).toBeVisible();
	await expect(page.getByText(/limit 50 · source Fixture/)).toBeVisible();

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

	// Sort is a URL query param. All-chain liquidity stays per-chain round robin.
	await page.goto("/markets?sort=liquidity");
	await expect(page.getByLabel("Sort markets")).toHaveValue("liquidity");
	await expect(marketTable.locator("tbody tr").first()).toContainText("WIF");
	await page.goto("/markets?sort=liquidity&chain=ethereum");
	await expect(page.getByText("GLOBAL SORT", { exact: true })).toBeVisible();
	await expect(marketTable.locator("tbody tr").first()).toContainText("UNI");

	// Empty state is a reason code, not a zero row.
	await page.goto("/markets?q=zzzz");
	await expect(page.getByText("EMPTY_RESULT")).toBeVisible();
	await expect(page.getByText(/No rows matched: Search: “zzzz”/)).toBeVisible();
	await expect(page.getByRole("link", { name: "Reset filters" }).first()).toBeVisible();

	// API failures print the reason code; they never fall back to fixtures.
	await page.goto("/markets?chain=nope");
	await expect(page.getByText("CHAIN_NOT_FOUND")).toBeVisible();
	await expect(page.getByText(/unknown chain nope/)).toBeVisible();
	await expect(page.locator("table")).toHaveCount(0);

	expect(external, `unexpected external requests: ${external.join(", ")}`).toEqual([]);
});

test("markets filters round-trip through chips and the pager is real", async ({ page }) => {
	const marketTable = page.locator("table").first();

	// A filter submitted from the bar is URL-encoded and appears as a removable chip.
	await page.goto("/markets");
	await page.getByLabel("Minimum liquidity in USD").fill("10000000");
	await page.getByLabel("Minimum 24h volume in USD").fill("20000000");
	await page.getByLabel("Minimum 24h change percent").fill("-10");
	await page.getByLabel("Maximum 24h change percent").fill("20");
	await page.getByLabel("Minimum pair age in minutes").fill("60");
	await page.getByLabel("Maximum pair age in minutes").fill("10000000");
	await page.getByLabel("DEX").fill("uniswap");
	await page.getByRole("button", { name: "Apply" }).click();

	await expect(page).toHaveURL(/min_liquidity_usd=10000000/);
	await expect(page).toHaveURL(/min_volume_24h_usd=20000000/);
	await expect(page).toHaveURL(/min_change_h24=-10/);
	await expect(page).toHaveURL(/max_change_h24=20/);
	await expect(page).toHaveURL(/min_pair_age_minutes=60/);
	await expect(page).toHaveURL(/max_pair_age_minutes=10000000/);
	await expect(page).toHaveURL(/dex=uniswap/);
	await expect(page.getByRole("link", { name: /Remove filter Liquidity/ })).toBeVisible();
	await expect(page.getByRole("link", { name: /Remove filter DEX/ })).toBeVisible();

	await page.getByRole("link", { name: /Remove filter Liquidity/ }).click();
	await expect(page).not.toHaveURL(/min_liquidity_usd/);
	await expect(page.getByRole("link", { name: /Remove filter Liquidity/ })).toHaveCount(0);

	// First/middle/last pages: totals stay the filtered count, no hardcoded 1/1.
	await page.goto("/markets?limit=5");
	await expect(marketTable.locator("tbody tr")).toHaveCount(5);
	await expect(page.getByText(/5 of 14 markets · showing 1–5/)).toBeVisible();
	await expect(page.getByText(/showing 1–5/)).toBeVisible();
	await expect(page.getByText(/page 1 of 3/)).toBeVisible();
	await expect(page.getByText("← Prev")).toBeVisible();

	await page.getByRole("link", { name: /Next/ }).click();
	await expect(page).toHaveURL(/offset=5/);
	await expect(marketTable.locator("tbody tr")).toHaveCount(5);
	await expect(page.getByText(/showing 6–10/)).toBeVisible();
	await expect(page.getByText(/page 2 of 3/)).toBeVisible();

	await page.getByRole("link", { name: /Next/ }).click();
	await expect(page).toHaveURL(/offset=10/);
	await expect(marketTable.locator("tbody tr")).toHaveCount(4);
	await expect(page.getByText(/4 of 14 markets · showing 11–14/)).toBeVisible();
	await expect(page.getByText(/page 3 of 3/)).toBeVisible();
	await expect(page.getByText("Next →")).toBeVisible();

	await page.getByRole("link", { name: /Prev/ }).click();
	await expect(page).toHaveURL(/offset=5/);
	await expect(page.getByText(/page 2 of 3/)).toBeVisible();

	// An empty result names the filter that removed every row.
	await page.goto("/markets?min_liquidity_usd=5000000000");
	await expect(page.getByText("EMPTY_RESULT")).toBeVisible();
	await expect(page.getByText(/No rows matched: Liquidity ≥ \$5\.00B/)).toBeVisible();
	await expect(page.getByRole("link", { name: "Reset filters" }).first()).toBeVisible();

	// A page past the end with active filters is not blamed on the filters. The range is
	// omitted for an empty page instead of printing "0–10".
	await page.goto("/markets?min_liquidity_usd=10000000&limit=5&offset=10");
	await expect(page.getByText("EMPTY_RESULT")).toBeVisible();
	await expect(page.getByText("Page 3 is past the last result.")).toBeVisible();
	await expect(page.getByText(/7 rows matched the active filters/)).toBeVisible();
	await expect(page.getByText(/No rows matched:/)).toHaveCount(0);
	await expect(
		page.getByText(/0 of 7 markets · no rows on this page · page 3 of 2/),
	).toBeVisible();
	await expect(page.getByText(/showing 0–10/)).toHaveCount(0);

	// Prev from that page reaches the last page that has rows.
	await page.getByRole("link", { name: "← Prev", exact: true }).click();
	await expect(page).toHaveURL(/offset=5/);
	await expect(page.getByText(/2 of 7 markets · showing 6–7/)).toBeVisible();
});

test("markets error retry preserves filters and a nonzero page", async ({ page }) => {
	await page.goto("/markets?chain=nope&offset=10&min_liquidity_usd=1000&dex=uniswap");
	await expect(page.getByText("CHAIN_NOT_FOUND")).toBeVisible();
	const retry = page.getByRole("link", { name: "Retry →" });
	await expect(retry).toHaveAttribute(
		"href",
		"/markets?chain=nope&offset=10&min_liquidity_usd=1000&dex=uniswap",
	);
});

test("markets drops unbounded decimal filters before they reach the API", async ({ page }) => {
	for (const value of ["1e1000000", "1e-1000000"]) {
		await page.goto(`/markets?min_liquidity_usd=${value}&max_change_h24=${value}`);
		await expect(page.getByRole("link", { name: /Remove filter Liquidity/ })).toHaveCount(0);
		await expect(page.getByRole("link", { name: /Remove filter 24h change/ })).toHaveCount(0);
		await expect(page.getByText(/14 of 14 markets · showing 1–14/).first()).toBeVisible();
	}
});

test("markets mobile filter panel keeps the result count visible", async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await page.goto("/markets?chain=base");

	const panel = page.getByTestId("market-filters");
	const summary = panel.locator("div").first();
	await expect(summary).toBeVisible();
	await expect(summary).toContainText("Filters");
	await expect(summary).toContainText("1 active");
	await expect(summary).toContainText("4 of 4 markets");

	const search = panel.getByRole("searchbox", { name: "Search symbol, name or address" });
	await expect(search).toBeVisible();
	await expect(page.getByText(/4 of 4 markets/).first()).toBeVisible();
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
				coverage: null,
				ordered_by: "global_sort",
				sort: url.searchParams.get("sort") ?? "trending",
				sort_semantics: "global_sort",
				offset: Number(url.searchParams.get("offset") ?? "0"),
				limit: Number(url.searchParams.get("limit") ?? "50"),
				total: 1,
				has_more: false,
				next_offset: null,
				applied_filters: {
					chain: chain ?? "all",
					q,
					dex: url.searchParams.get("dex"),
					min_liquidity_usd: url.searchParams.get("min_liquidity_usd"),
					min_volume_24h_usd: url.searchParams.get("min_volume_24h_usd"),
					min_change_h24: url.searchParams.get("min_change_h24"),
					max_change_h24: url.searchParams.get("max_change_h24"),
					min_pair_age_minutes: null,
					max_pair_age_minutes: null,
					unknown_excluded: {},
				},
				population: {
					chains_scanned: null,
					addresses_requested: null,
					rows_discovered: 1,
					rows_after_filters: 1,
					rows_returned: 1,
					truncated: false,
					sort: url.searchParams.get("sort") ?? "trending",
					sort_semantics: "global_sort",
				},
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
		expect(list.total).toBe(4);
		expect(list.has_more).toBe(false);
		expect(list.next_offset).toBeNull();
		expect(list.sort_semantics).toBe("global_sort");
		expect(list.source.name).toBe("fixture");
		expect(list.stale).toBe(false);

		const searched = await getMarkets({ q: "bonk" });
		expect(searched.rows.map((row) => row.symbol)).toEqual(["BONK"]);

		// Fixture parity: filtering, totals and offset paging match the API contract.
		const first = await getMarkets({ limit: 5 });
		const second = await getMarkets({ limit: 5, offset: 5 });
		const third = await getMarkets({ limit: 5, offset: 10 });
		expect(first.total).toBe(14);
		expect(first.has_more).toBe(true);
		expect(first.next_offset).toBe(5);
		expect(first.rows).toHaveLength(5);
		expect(second.rows).toHaveLength(5);
		expect(third.rows).toHaveLength(4);
		expect(third.has_more).toBe(false);
		expect(third.next_offset).toBeNull();
		const paged = [...first.rows, ...second.rows, ...third.rows].map(
			(row) => `${row.chain}:${row.pair_address}`,
		);
		expect(new Set(paged).size).toBe(14);

		// Two fixture rows have null liquidity, volume, change and pair age. Unknown is
		// never zero, and when both values are null the pair address breaks the tie.
		const nullPairs = [
			"0xa1b2c3d4e5f60718293a4b5c6d7e8f901a2b3c4d",
			"0xb2c3d4e5f60718293a4b5c6d7e8f901a2b3c4d5e",
		];
		for (const sort of ["trending", "liquidity", "new", "change_h24", "pair_age"] as const) {
			const nulls = await getMarkets({ chain: "optimism", sort });
			expect(nulls.total).toBe(2);
			expect(nulls.rows.map((row) => row.pair_address)).toEqual(nullPairs);
			expect(
				nulls.rows.every(
					(row) =>
						row.liquidity_usd === null &&
						row.volume_24h_usd === null &&
						row.price_change_h24 === null &&
						row.pair_created_at === null,
				),
			).toBe(true);
		}

		const liquid = await getMarkets({ minLiquidityUsd: "10000000", chain: "solana" });
		expect(liquid.rows.map((row) => row.symbol)).toEqual(["WIF", "JUP"]);
		expect(liquid.total).toBe(2);
		expect(liquid.applied_filters.min_liquidity_usd).toBe("10000000");
		expect(liquid.applied_filters.unknown_excluded).toEqual({});

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
			expect(list.total).toBe(1);
			expect(list.source.name).toBe("dexscreener");
			expect(api.requests[0]).toContain("/api/v1/markets");
			expect(api.requests[0]).toContain("sort=trending");

			await getMarkets({
				chain: "base",
				offset: 10,
				minLiquidityUsd: "1000000",
				minVolume24hUsd: "2000000",
				minChangeH24: "-5",
				maxChangeH24: "25",
				minPairAgeMinutes: 60,
				maxPairAgeMinutes: 1440,
				dex: "uniswap",
			});
			const filteredRequest = api.requests.at(-1) ?? "";
			expect(filteredRequest).toContain("offset=10");
			expect(filteredRequest).toContain("min_liquidity_usd=1000000");
			expect(filteredRequest).toContain("min_volume_24h_usd=2000000");
			expect(filteredRequest).toContain("min_change_h24=-5");
			expect(filteredRequest).toContain("max_change_h24=25");
			expect(filteredRequest).toContain("min_pair_age_minutes=60");
			expect(filteredRequest).toContain("max_pair_age_minutes=1440");
			expect(filteredRequest).toContain("dex=uniswap");

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
