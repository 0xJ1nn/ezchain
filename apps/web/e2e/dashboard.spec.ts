import { createServer, type Server } from "node:http";
import { expect, test } from "./test";
import { PROXY_TEST_API_URL } from "./proxy-env";

// Fixture-mode cases render the sample shell. The mode chip cases need the web server to
// read an API answer, which fixture mode never does, so they run under
// EZCHAIN_WEB_MODE=live and are skipped otherwise — the same gate the topbar kill-switch
// cases use.
const live = process.env.EZCHAIN_WEB_MODE === "live";
const apiPort = Number(new URL(process.env.EZCHAIN_API_URL ?? PROXY_TEST_API_URL).port || 80);

test("dashboard renders from fixtures with no external requests", async ({ page }) => {
	const external: string[] = [];
	page.on("request", (request) => {
		const url = new URL(request.url());
		if (url.protocol === "http:" || url.protocol === "https:") {
			if (url.hostname !== "127.0.0.1" && url.hostname !== "localhost") {
				external.push(request.url());
			}
		}
	});

	await page.goto("/");
	await expect(page.getByRole("heading", { name: "Dashboard", level: 1 })).toBeVisible();
	await expect(page.locator(".hero-stat .value")).toHaveText("—");
	await expect(page.getByText(/equity unavailable — 1 open mark unknown/i).first()).toBeVisible();
	await expect(
		page.locator(".topbar").getByRole("button", { name: /Kill switch: RELEASED/ }),
	).toBeVisible();
	await expect(page.getByText("One screen to answer")).toBeVisible();

	// Equity is a composition, not a single understated number: the unknown asset is
	// preserved with nulls, cash is its own settlement row, and no share is invented.
	const composition = page
		.locator("section.panel")
		.filter({ has: page.getByRole("heading", { name: "Equity composition" }) });
	await expect(composition).toBeVisible();
	const assets = page.getByRole("table", { name: "Composition by asset" });
	await expect(assets.locator("tbody tr")).toHaveCount(5);
	const mochi = assets.locator("tbody tr").filter({ hasText: "MOCHI" });
	await expect(mochi.locator("td").nth(2)).toHaveText("—");
	await expect(mochi.locator("td").nth(3)).toHaveText("—");
	await expect(mochi.locator("td").nth(4)).toHaveText("—");
	await expect(mochi.getByText("unknown mark")).toBeVisible();
	const cash = assets.locator("tbody tr").filter({ hasText: "USD" });
	await expect(cash).toContainText("ledger");
	await expect(cash.locator("td").nth(4)).toHaveText("—");
	const chains = page.getByRole("table", { name: "Composition by chain" });
	await expect(chains.locator("tbody tr")).toHaveCount(3);
	await expect(chains.getByText("1 unpriced")).toBeVisible();
	await expect(page.getByText("$9,870.80")).toHaveCount(0);
	await expect(page.getByText("unknown mark excluded")).toHaveCount(0);

	expect(external, `unexpected external requests: ${external.join(", ")}`).toEqual([]);
});

test("dashboard renders a complete server composition with shares", async ({ page }) => {
	await page.goto("/?state=complete");

	await expect(page.locator(".hero-stat .value")).toHaveText("$9,845.80");
	await expect(page.getByText(/equity unavailable/i)).toHaveCount(0);
	await expect(page.getByRole("heading", { name: "Equity composition" })).toBeVisible();

	const assets = page.getByRole("table", { name: "Composition by asset" });
	await expect(assets.locator("tbody tr")).toHaveCount(4);
	await expect(
		assets.locator("tbody tr").filter({ hasText: "BONK" }).locator("td").nth(4),
	).toHaveText("0.64%");
	await expect(
		assets.locator("tbody tr").filter({ hasText: "USD" }).locator("td").nth(4),
	).toHaveText("98.61%");

	const chains = page.getByRole("table", { name: "Composition by chain" });
	await expect(chains.locator("tbody tr")).toHaveCount(3);
	await expect(
		chains.locator("tbody tr").filter({ hasText: "settlement" }).locator("td").nth(3),
	).toHaveText("98.61%");

	// The 125s fixture mark is stale, not a fresh "known" row; its chain aggregates stale.
	const scam = assets.locator("tbody tr").filter({ hasText: "SCAM" });
	await expect(scam).toContainText("stale · DexScreener");
	await expect(scam).not.toContainText("fresh");
	await expect(
		chains.locator("tbody tr").filter({ hasText: "solana" }).locator(".badge"),
	).toHaveText("stale");
});

test("an unmarked row never shows a source or age as its mark", async ({ page }) => {
	await page.goto("/?state=unpriced-source");

	const assets = page.getByRole("table", { name: "Composition by asset" });
	const mochi = assets.locator("tbody tr").filter({ hasText: "MOCHI" });
	await expect(mochi).toBeVisible();
	await expect(mochi.locator("td").nth(2)).toHaveText("—");
	await expect(mochi.locator("td").nth(3)).toHaveText("—");
	await expect(mochi.locator("td").nth(4)).toHaveText("—");
	await expect(mochi.locator("td").nth(5)).toHaveText("unknown mark");
	await expect(mochi).not.toContainText("dexscreener");
	await expect(mochi).not.toContainText("ago");
});

test("eleven positions across chains render every row without overflow", async ({ page }) => {
	await page.goto("/?state=many");

	await expect(page.getByText(/equity unavailable/i)).toHaveCount(0);
	const assets = page.getByRole("table", { name: "Composition by asset" });
	await expect(assets.locator("tbody tr")).toHaveCount(12); // eleven positions plus cash
	await expect(assets.locator("tbody tr a")).toHaveCount(12);
	for (const symbol of ["ABT", "SOLA", "ETHA", "BCP", "JUP", "UNI", "DEG", "PYT", "MKR", "TOS", "ORC", "USD"]) {
		await expect(assets.locator("tbody tr").filter({ hasText: symbol }).first()).toBeVisible();
	}
	await expect(assets.locator("tbody tr").filter({ hasText: "ABT" }).locator("td").nth(4)).toHaveText(
		"1.00%",
	);
	await expect(page.getByRole("table", { name: "Composition by chain" }).locator("tbody tr")).toHaveCount(4);

	const overflow = await page.evaluate(
		() => document.documentElement.scrollWidth - document.documentElement.clientWidth,
	);
	expect(overflow).toBeLessThanOrEqual(1);
});

test("a single position renders its row and settlement cash without overflow", async ({ page }) => {
	await page.goto("/?state=single");

	await expect(page.getByText(/equity unavailable/i)).toHaveCount(0);
	const assets = page.getByRole("table", { name: "Composition by asset" });
	await expect(assets.locator("tbody tr")).toHaveCount(2);
	await expect(assets.locator("tbody tr a")).toHaveCount(2);
	const aero = assets.locator("tbody tr").filter({ hasText: "AERO" });
	await expect(aero).toBeVisible();
	await expect(aero.locator("td").nth(4)).toHaveText("0.15%");
	await expect(assets.locator("tbody tr").filter({ hasText: "USD" })).toBeVisible();
	await expect(page.getByRole("table", { name: "Composition by chain" }).locator("tbody tr")).toHaveCount(2);

	const overflow = await page.evaluate(
		() => document.documentElement.scrollWidth - document.documentElement.clientWidth,
	);
	expect(overflow).toBeLessThanOrEqual(1);
});

test("a flat account has a cash-only composition and no invented total", async ({ page }) => {
	await page.goto("/?state=flat");

	await expect(page.locator(".hero-stat .value")).toHaveText("$10,000.00");
	await expect(page.getByText(/equity unavailable/i)).toHaveCount(0);
	await expect(page.getByRole("table", { name: "Composition by asset" }).locator("tbody tr")).toHaveCount(1);
	await expect(page.getByRole("table", { name: "Composition by asset" })).toContainText("USD");
	await expect(page.getByText("composition is settlement cash only")).toBeVisible();
});

test("fixture mode is labelled and never claims live data", async ({ page }) => {
	await page.goto("/");
	await expect(page.getByText("FIXTURE", { exact: true }).first()).toBeVisible();
	await expect(page.getByText("Fixture preview")).toBeVisible();
	await expect(page.getByText("LIVE PAPER API")).toHaveCount(0);
	await expect(page.getByText("live checks")).toHaveCount(0);
});

test("unknown routes return 404 with the shell", async ({ page }) => {
	const response = await page.goto("/not-a-route");
	expect(response?.status()).toBe(404);
	await expect(page.getByRole("heading", { name: "Not found", level: 1 })).toBeVisible();
});

// EZ-258: the equity number is the hero of the KPI band, so it must stay on one line at
// the desktop gate width. A wrapped currency string reads as two numbers.
test("the equity value fits its tile on one line at 1440", async ({ page }) => {
	for (const state of ["many", "complete", "flat"]) {
		await page.goto(`/?state=${state}`);

		const equity = page.locator('section.kpi-band[aria-label="Paper account"] .hero-stat .value');
		await expect(equity).toBeVisible();

		const metrics = await equity.evaluate((node) => {
			const rect = node.getBoundingClientRect();
			const style = getComputedStyle(node);
			const band = node.closest(".kpi-band");
			const bandRect = band?.getBoundingClientRect();
			const statRect = node.parentElement?.getBoundingClientRect();
			return {
				lineHeight: Number.parseFloat(style.lineHeight),
				height: rect.height,
				text: node.textContent ?? "",
				statWidth: statRect?.width ?? 0,
				scrollWidth: node.scrollWidth,
				clientWidth: node.clientWidth,
				bandRight: bandRect?.right ?? 0,
			};
		});

		const lines = Math.round(metrics.height / metrics.lineHeight);
		expect(lines, `equity wraps: ${metrics.text} at state=${state}`).toBe(1);
		expect(metrics.height, "the value stayed on one line").toBeLessThan(
			metrics.lineHeight * 1.5,
		);
		// Fitting must not be bought by clipping the number.
		expect(metrics.scrollWidth, "the value is not clipped").toBeLessThanOrEqual(
			Math.ceil(metrics.clientWidth) + 1,
		);
		expect(metrics.bandRight).toBeLessThanOrEqual(1441);
	}

	const overflow = await page.evaluate(
		() => document.documentElement.scrollWidth - document.documentElement.clientWidth,
	);
	expect(overflow).toBeLessThanOrEqual(1);
});

// EZ-258: the strip calls the column "Age", which reads as quote freshness next to the
// trending table's age. It is pair age, and the panel promises a launch window, so only
// pairs born inside that window may be listed.
test("the launches column is pair age and only lists pairs inside the window", async ({
	page,
}) => {
	await page.goto("/");

	const strip = page.getByTestId("market-strip");
	const launches = strip.locator(".table-wrap").first();
	const headers = launches.locator("thead th");
	await expect(headers).toContainText(["Token", "Chain", "Pair age", "Curve progress", "Trade"]);
	await expect(headers.filter({ hasText: /^Age$/ })).toHaveCount(0);

	const windowSub = strip.getByText(/born in the last/i);
	await expect(windowSub).toBeVisible();
	await expect(windowSub).toContainText("24h");

	const ages = await launches.locator("tbody tr td:nth-child(3)").allTextContents();
	expect(ages.length).toBeGreaterThan(0);
	for (const age of ages) {
		// The fixture sample holds pairs older than the window; none may appear here.
		expect(age.trim(), "a pair older than the window is listed").not.toMatch(/^\d+d$/);
		expect(age.trim()).not.toBe("—");
	}
});

// EZ-258: the composition action row carried FIXTURE and a second, literal PAPER badge, so
// a fixture build showed two mode claims in one row and a live build showed PAPER twice.
// The row states the data mode once; the page header carries the rest.
test("the equity composition shows one mode badge", async ({ page }) => {
	await page.goto("/");

	const composition = page
		.locator("section.panel")
		.filter({ has: page.getByRole("heading", { name: "Equity composition" }) });
	const actions = composition.locator(".panel-head .right");
	await expect(actions.locator(".badge")).toHaveCount(1);
	// Fixture data must be labelled FIXTURE here, never a second PAPER claim.
	expect(await actions.locator(".badge").allTextContents()).toEqual(["FIXTURE"]);
	await expect(actions.getByText("PAPER", { exact: true })).toHaveCount(0);
});

// EZ-258: the mode chip was literal text. It must name the mode the API derives, and a
// read that fails must say unknown instead of falling back to a guess.
test("the topbar mode chip reads the execution mode from the API", async ({ page }) => {
	await page.goto("/");
	const chip = page.locator("header.topbar .mode");
	await expect(chip).toHaveCount(1);
	// The fixture gate has no API to derive the mode from, so it must not claim one.
	await expect(chip).toHaveText(/UNKNOWN/);
	await expect(chip).not.toHaveText(/PAPER|LIVE/);
	await expect(chip).toHaveAttribute("title", /unavailable/i);
});

// The chip is rendered from a server read, so this case needs the web server itself to
// call an API. It runs under EZCHAIN_WEB_MODE=live like the topbar kill-switch cases.
test.describe("the mode chip under a live server read", () => {
	test.describe.configure({ mode: "serial" });

	let api: Server | null = null;
	let reply: "paper" | "down" = "paper";

	test.beforeAll(async () => {
		if (!live) return;
		api = createServer((request, response) => {
			const url = new URL(request.url ?? "/", "http://127.0.0.1");
			response.setHeader("content-type", "application/json");
			if (url.pathname === "/healthz") {
				if (reply === "down") {
					response.statusCode = 503;
					response.end(
						JSON.stringify({ detail: { code: "DB_DOWN", message: "health is down" } }),
					);
					return;
				}
				response.end(
					JSON.stringify({
						status: "ok",
						mode: {
							execution: "paper",
							live_available: false,
							live_blocked_reason: "EZCHAIN_EXECUTION_MODE is paper",
						},
					}),
				);
				return;
			}
			response.statusCode = 404;
			response.end(JSON.stringify({ detail: { code: "NOT_FOUND", message: "no mock route" } }));
		});
		await new Promise<void>((resolve, reject) => {
			api?.once("error", reject);
			api?.listen(apiPort, "127.0.0.1", resolve);
		});
	});

	test.afterAll(async () => {
		if (!api) return;
		await new Promise<void>((resolve) => api?.close(() => resolve()));
	});

	test("the chip names the derived execution mode", async ({ page }) => {
		test.skip(!live, "needs EZCHAIN_WEB_MODE=live so the shell performs the GET");
		reply = "paper";
		await page.goto("/");
		const chip = page.locator("header.topbar .mode");
		await expect(chip).toHaveText(/PAPER/);
		await expect(chip).toHaveAttribute("title", /derived by the API/i);
	});

	test("a failed read renders unknown, not a guessed mode", async ({ page }) => {
		test.skip(!live, "needs EZCHAIN_WEB_MODE=live so the shell performs the GET");
		reply = "down";
		await page.goto("/");
		const chip = page.locator("header.topbar .mode");
		await expect(chip).toHaveText(/UNKNOWN/);
		await expect(chip).not.toHaveText(/PAPER|LIVE/);
		await expect(chip).toHaveAttribute("title", /unavailable/i);
	});
});

test("dashboard screenshot at desktop and mobile widths", async ({ page }) => {
	await page.goto("/");
	await page.screenshot({ path: ".shots/dashboard-1440.png", fullPage: true });

	await page.setViewportSize({ width: 390, height: 844 });
	await page.goto("/");
	await expect(page.getByRole("navigation", { name: "Primary mobile navigation" })).toBeVisible();
	await page.screenshot({ path: ".shots/dashboard-390.png", fullPage: true });
});
