import { expect, test, type Page } from "@playwright/test";

// Candle charts and the real safety object: honest renders from server series.
// Fixture mode only (the Playwright gate runs EZCHAIN_WEB_MODE=fixture).

const BONK_MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263";
const WIF_MINT = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm";
const JUP_MINT = "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN";
const SCAM_MINT = "Scam1111111111111111111111111111111111111111";

function noExternal(page: Page): string[] {
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
}

test("fixture candle series renders a chart with axes in the a11y tree, source and age", async ({
	page,
}) => {
	const external = noExternal(page);
	await page.goto(`/tokens/solana/${BONK_MINT}`);

	const panel = page.locator("#candles");
	await expect(panel).toBeVisible();
	await expect(page.getByText("FIXTURE", { exact: true })).toBeVisible();
	await expect(panel).toContainText("closed bars only");

	const chart = page.getByTestId("candle-chart");
	await expect(chart).toBeVisible();
	const svg = chart.locator("svg");
	await expect(svg).toHaveAttribute("role", "img");
	await expect(chart.getByText(/candles/i).first()).toBeVisible();

	// Axes are reachable in the a11y tree: price labels and time labels are SVG text.
	const axisTexts = svg.locator("text");
	await expect(axisTexts.first()).toBeVisible();
	expect(await axisTexts.count()).toBeGreaterThan(3);

	// At least one candle body and one volume bar are drawn.
	await expect(chart.locator("[data-candle]")).not.toHaveCount(0);
	await expect(chart.locator("[data-volume]")).not.toHaveCount(0);

	// Unknown volume is skipped, never drawn as zero, and counted in the footer.
	await expect(panel).toContainText(/volume unknown/);

	// Timeframe controls switch the server series via the URL.
	const nav = panel.getByRole("navigation", { name: /candle timeframe/i });
	await expect(nav.getByRole("link", { name: "5m" })).toHaveAttribute("aria-current", "true");
	await nav.getByRole("link", { name: "1h" }).click();
	await expect(page).toHaveURL(/tf=1h/);
	await expect(chart).toBeVisible();

	// Source and age of the series are shown.
	await expect(panel).toContainText(/Fixture ·/);

	expect(external, `unexpected external requests: ${external.join(", ")}`).toEqual([]);
});

test("missing series shows an explicit empty state, never a fabricated line", async ({
	page,
}) => {
	await page.goto(`/tokens/solana/${SCAM_MINT}`);
	const panel = page.locator("#candles");
	await expect(panel).toBeVisible();
	await expect(panel).toContainText("CANDLES_EMPTY");
	await expect(panel).toContainText(/no candle data|no closed bars/i);
	await expect(page.getByTestId("candle-chart")).toHaveCount(0);
	await expect(panel.locator("svg")).toHaveCount(0);

	// The demo empty state renders the same honest empty panel.
	await page.goto(`/tokens/solana/${BONK_MINT}?chart=empty`);
	await expect(page.locator("#candles")).toContainText("CANDLES_EMPTY");
	await expect(page.getByTestId("candle-chart")).toHaveCount(0);
});

test("candle error states carry their reason codes", async ({ page }) => {
	await page.goto(`/tokens/solana/${BONK_MINT}?chart=unsupported`);
	await expect(page.locator("#candles")).toContainText("CANDLES_UNSUPPORTED_CHAIN");
	await expect(page.getByTestId("candle-chart")).toHaveCount(0);

	await page.goto(`/tokens/solana/${BONK_MINT}?chart=no-pool`);
	const noPool = page.locator("#candles");
	await expect(noPool).toContainText("NO_POOL");
	await expect(noPool).toContainText(/no pool|no liquidity pair/i);

	await page.goto(`/tokens/solana/${BONK_MINT}?chart=error`);
	const err = page.locator("#candles");
	await expect(err).toContainText("UPSTREAM_UNREACHABLE");
	await expect(err).toContainText("retryable");
	await expect(page.getByTestId("candle-chart")).toHaveCount(0);

	await page.goto(`/tokens/solana/${BONK_MINT}?chart=stale`);
	const stale = page.locator("#candles");
	await expect(stale).toContainText("STALE");
	await expect(stale.getByTestId("candle-chart")).toBeVisible();
});

test("safety object renders unknown, pass and warn payloads; absent fields are em dashes", async ({
	page,
}) => {
	await page.goto(`/tokens/solana/${BONK_MINT}`);
	const safety = page.locator("#safety");
	await expect(safety.locator(".panel-head .badge")).toContainText("unknown");
	await expect(safety.locator("tbody tr")).toHaveCount(5);
	await expect(safety).toContainText("SAFETY_NOT_IMPLEMENTED");

	await page.goto(`/tokens/solana/${WIF_MINT}`);
	const pass = page.locator("#safety");
	await expect(pass.locator(".panel-head .badge")).toContainText(/pass/i);
	await expect(pass).toContainText("revoked");
	await expect(pass).not.toContainText("SAFETY_NOT_IMPLEMENTED");

	await page.goto(`/tokens/solana/${JUP_MINT}`);
	const warn = page.locator("#safety");
	await expect(warn.locator(".panel-head .badge")).toContainText(/warn/i);
	await expect(warn).toContainText(/partial|warn/i);
});

test("position detail page renders the candle chart and the server position summary", async ({
	page,
}) => {
	const external = noExternal(page);
	await page.goto(`/positions/solana/${BONK_MINT}`);

	await expect(page.getByRole("heading", { name: /Bonk/ })).toBeVisible();
	await expect(page.getByText("FIXTURE", { exact: true })).toBeVisible();
	await expect(page.getByText("PAPER")).toBeVisible();

	const chart = page.getByTestId("candle-chart");
	await expect(chart).toBeVisible();
	await expect(chart.locator("[data-candle]")).not.toHaveCount(0);

	// The position summary is the server view model, formatted only.
	await expect(page.locator("#position-meta")).toContainText("BONK");
	await expect(page.getByRole("link", { name: /token page/i })).toBeVisible();

	expect(external, `unexpected external requests: ${external.join(", ")}`).toEqual([]);
});

test("token chart page has no sideways scroll at mobile width", async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await page.goto(`/tokens/solana/${BONK_MINT}`);
	await expect(page.getByRole("navigation", { name: "Primary mobile navigation" })).toBeVisible();
	await expect(page.getByTestId("candle-chart")).toBeVisible();

	const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
	expect(scrollWidth, "page must not scroll sideways on a 390px viewport").toBeLessThanOrEqual(
		390,
	);
	await page.screenshot({ path: ".shots/candles-390.png", fullPage: true });
});
