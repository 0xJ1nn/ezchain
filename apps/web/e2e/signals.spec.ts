import { expect, test } from "./test";
import { createServer, type Server } from "node:http";
import { PROXY_TEST_API_URL } from "./proxy-env";

// The signals page renders the rows the read model returned. In fixture mode the answer
// comes from the local sample; in live mode it comes from the API the web server talks
// to. The live half runs against a mock API so the page is exercised with a real
// payload, including the empty answer and its reason code.

const live = process.env.EZCHAIN_WEB_MODE === "live";
const apiUrl = new URL(process.env.EZCHAIN_API_URL ?? PROXY_TEST_API_URL);
const apiPort = Number(apiUrl.port || 80);

const MOCK_ROWS = [
	{
		rank: 1,
		chain: "solana",
		token_address: "So11111111111111111111111111111111111111112",
		pair_address: "mock-pair-1",
		dex: "raydium",
		symbol: "SRVONLY",
		name: "Server only",
		price_usd: "1.25",
		liquidity_usd: "4200000",
		volume_24h_usd: "1200000",
		change_pct: "12.5",
		source: {
			name: "dexscreener",
			url: "https://dexscreener.com",
			observed_at: new Date().toISOString(),
		},
		observed_at: new Date().toISOString(),
		age_seconds: 3,
	},
	{
		rank: 2,
		chain: "base",
		token_address: "0x940181a94A35A4569E4529A3CDfB74e38FD98631",
		pair_address: "mock-pair-2",
		dex: "aerodrome",
		symbol: "SRVSECOND",
		name: "Server second",
		price_usd: "0.5",
		liquidity_usd: "18000000",
		volume_24h_usd: "900000",
		change_pct: "4.25",
		source: {
			name: "dexscreener",
			url: "https://dexscreener.com",
			observed_at: new Date().toISOString(),
		},
		observed_at: new Date().toISOString(),
		age_seconds: 5,
	},
];

function mockPayload(window: string, direction: string) {
	const empty = window === "h24" && direction === "down";
	const observed = new Date().toISOString();
	return {
		window,
		direction,
		as_of: observed,
		rows: empty ? [] : MOCK_ROWS,
		total: empty ? 0 : MOCK_ROWS.length,
		limit: 10,
		truncated: false,
		reason: empty ? "NO_QUALIFIED_MOVERS" : null,
		rows_read: 14,
		exclusions: {
			unknown_token: 0,
			duplicate_token: 0,
			stale: 0,
			unknown_change: 2,
			unknown_liquidity: 0,
			below_liquidity_floor: 1,
			wrong_direction: empty ? 11 : 4,
		},
		applied: {
			chain: "all",
			window,
			direction,
			min_liquidity_usd: "10000",
			max_age_seconds: 60,
			candidate_rows: 200,
			candidate_sort: window === "h24" ? "change_h24" : "trending",
			limit: 10,
		},
		source: { name: "dexscreener", url: "https://dexscreener.com", observed_at: observed },
		stale: false,
	};
}

let server: Server | null = null;
const seen: string[] = [];

test.beforeAll(async () => {
	if (!live) return;
	server = createServer((request, response) => {
		const url = new URL(request.url ?? "/", "http://127.0.0.1");
		seen.push(`${request.method ?? "GET"} ${url.pathname}${url.search}`);
		response.setHeader("content-type", "application/json");
		if (url.pathname === "/api/v1/chains") {
			response.end(JSON.stringify([]));
			return;
		}
		if (url.pathname === "/api/v1/signals/movers") {
			response.end(
				JSON.stringify(
					mockPayload(
						url.searchParams.get("window") ?? "h1",
						url.searchParams.get("direction") ?? "up",
					),
				),
			);
			return;
		}
		response.statusCode = 404;
		response.end(JSON.stringify({ detail: { code: "NOT_FOUND", message: "no mock route" } }));
	});
	await new Promise<void>((resolve, reject) => {
		server?.once("error", reject);
		server?.listen(apiPort, "127.0.0.1", resolve);
	});
});

test.afterAll(async () => {
	if (!server) return;
	await new Promise<void>((resolve) => server?.close(() => resolve()));
});

test("signals renders the ranked movers the read model returned", async ({ page }) => {
	await page.goto("/signals");

	await expect(page.getByRole("heading", { name: "Signals", level: 1 })).toBeVisible();

	const banner = page.getByRole("status", { name: "Data mode" });
	await expect(banner).toBeVisible();
	if (live) {
		await expect(banner.getByText("LIVE · DexScreener")).toBeVisible();
	} else {
		await expect(banner.getByText("FIXTURE", { exact: true })).toBeVisible();
		await expect(banner).toContainText("not a live feed");
	}

	// Every panel names the window it ranks.
	for (const title of ["Gainers · 1h", "Losers · 1h", "Gainers · 24h", "Losers · 24h"]) {
		await expect(page.getByRole("heading", { name: title })).toBeVisible();
	}

	const gainers = page
		.locator("section.panel")
		.filter({ has: page.getByRole("heading", { name: "Gainers · 1h" }) });
	const losers = page
		.locator("section.panel")
		.filter({ has: page.getByRole("heading", { name: "Losers · 24h" }) });

	if (live) {
		// The rows are the ones the API returned, in the order it ranked them.
		await expect(gainers.locator("tbody tr")).toHaveCount(2);
		await expect(gainers.locator("tbody tr").first()).toContainText("SRVONLY");
		await expect(gainers.locator("tbody tr").nth(1)).toContainText("SRVSECOND");
		await expect(gainers.locator("tbody tr").first().locator("td").nth(2)).toHaveText("+12.5%");
		await expect(gainers.locator("tbody tr").first().locator("td").nth(4)).toContainText(
			"DexScreener",
		);
		await expect(gainers.locator("tbody tr").first().locator("td").nth(4)).toContainText("ago");
		await expect(gainers.getByRole("link", { name: "Open" }).first()).toHaveAttribute(
			"href",
			"/tokens/solana/So11111111111111111111111111111111111111112",
		);

		// One request per window and direction, and no request to the unfiltered market list.
		const moverRequests = seen.filter((entry) => entry.includes("/api/v1/signals/movers"));
		expect(moverRequests.length).toBeGreaterThanOrEqual(4);
		for (const combo of [
			"window=h1&direction=up",
			"window=h1&direction=down",
			"window=h24&direction=up",
			"window=h24&direction=down",
		]) {
			expect(moverRequests.some((entry) => entry.includes(combo))).toBe(true);
		}
		expect(seen.some((entry) => entry.startsWith("GET /api/v1/markets"))).toBe(false);
	} else {
		await expect(gainers.locator("tbody tr")).toHaveCount(7);
		await expect(gainers.locator("tbody tr td:first-child")).toContainText([
			"BONK",
			"PEPE",
			"MOCHI",
			"BRETT",
			"WIF",
			"CAKE",
			"TOSHI",
		]);
		await expect(gainers.locator("tbody tr").first().locator("td").nth(4)).toContainText(
			"Fixture",
		);
		await expect(gainers.locator("tbody tr").first().locator("td").nth(4)).toContainText("ago");
		await expect(losers.locator("tbody tr")).toHaveCount(3);
		await expect(losers.locator("tbody tr").first()).toContainText("AERO");
	}

	// The population note names the rows that were read and the rows that were dropped.
	await expect(page.getByText("Population.").first()).toBeVisible();
	await expect(page.getByText(/market rows read for the first window/).first()).toBeVisible();
});

test("signals shows the reason code when the read model returns no mover", async ({ page }) => {
	if (live) {
		// The mock answers the 24-hour losers with an empty list and its reason.
		await page.goto("/signals");
		const losers = page
			.locator("section.panel")
			.filter({ has: page.getByRole("heading", { name: "Losers · 24h" }) });
		await expect(losers.getByText("NO_QUALIFIED_MOVERS")).toBeVisible();
		await expect(losers.getByText("No mover qualified")).toBeVisible();
		await expect(losers.locator("tbody tr")).toHaveCount(0);
		return;
	}
	await page.goto("/signals?state=empty");
	await expect(page.getByText("NO_QUALIFIED_MOVERS").first()).toBeVisible();
	await expect(page.getByText("No mover qualified").first()).toBeVisible();
	await expect(page.locator("table")).toHaveCount(0);
});

test("signals reports a failed read without inventing a mover", async ({ page }) => {
	test.skip(live, "the live read is covered by the mock route above");
	await page.goto("/signals?state=error");
	await expect(page.getByText("UPSTREAM_UNREACHABLE").first()).toBeVisible();
	await expect(page.getByText(/could not reach the market source/).first()).toBeVisible();
	await expect(page.locator("table")).toHaveCount(0);
});
