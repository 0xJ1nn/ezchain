import { expect, test } from "@playwright/test";

const BONK_MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263";
const SOLSCAN_URL = `https://solscan.io/token/${BONK_MINT}`;

test("token page renders the bonk wireframe from fixtures", async ({ page }) => {
	await page.goto("/tokens/solana/bonk");

	await expect(page.getByRole("heading", { name: /Bonk/, level: 1 })).toBeVisible();
	await expect(page.locator(".page-actions .mono")).toHaveText("$0.00003142");
	await expect(page.locator(".page-actions .delta.up")).toHaveText("+6.2% 1h");
	await expect(page.locator(".page-actions .delta.down")).toHaveText("−3.4% 24h");

	// Shell: markets is the active section, crumbs link back to markets.
	await expect(page.locator(".nav-item.active")).toHaveText(/Markets/);
	await expect(page.locator(".crumbs a")).toHaveAttribute("href", "/markets");

	// Badge strip + external explorer link (must not leak referrer or open unsafely).
	await expect(page.getByText("safety pass")).toBeVisible();
	const solscan = page.locator(`a[href="${SOLSCAN_URL}"]`);
	await expect(solscan).toHaveAttribute("target", "_blank");
	await expect(solscan).toHaveAttribute("rel", "noopener noreferrer");
	// The visible label must be a truncation of the real mint, not a different string.
	await expect(solscan).toHaveText(/DezX…B263/);

	// Chart panel with levels and the last-trade caption. Range buttons are preview-only.
	await expect(page.locator("#chart polyline.series")).toBeVisible();
	await expect(page.getByText("last trade $0.00003138 · 11:01:02 · spread 0.4bp")).toBeVisible();
	await expect(page.locator("#chart .seg button[disabled]").first()).toHaveAttribute(
		"title",
		"Preview only — ranges are fixed in M1",
	);
	await expect(page.locator("#chart .seg button[disabled]")).toHaveCount(5);

	// Tab counts derive from the table rows.
	await expect(
		page.locator(".tabs .tab", { hasText: "Safety" }).locator(".count"),
	).toHaveText("7");
	await expect(
		page.locator(".tabs .tab", { hasText: "Holders" }).locator(".count"),
	).toHaveText("10");
	await expect(
		page.locator(".tabs .tab", { hasText: "Liquidity" }).locator(".count"),
	).toHaveText("3");

	// Safety: 7 rows, each naming source and age.
	await expect(page.locator("#safety tbody tr")).toHaveCount(7);
	await expect(page.locator("#safety tbody tr").first()).toHaveText(/RPC · 4s/);

	// Holders: 10 rows; liquidity: 3 pools with real sell-size impact.
	await expect(page.locator("#holders tbody tr")).toHaveCount(10);
	await expect(page.locator("#liquidity tbody tr")).toHaveCount(3);
	await expect(page.locator("#liquidity tbody tr").first()).toHaveText(/−0\.3%/);
	await expect(page.locator("#liquidity tbody tr").first()).toHaveText(/−2\.9%/);

	// My position: combined exposure note uses the reconciled numbers.
	await expect(page.locator("#mine")).toHaveText(/banked \$46\.20/);
	await expect(page.locator("#mine .note")).toHaveText(/\$164\.20 \(4\.7% of equity\)/);

	// Recent trades: 6 rows, two own fills, banked sale is $46.20 (not the wireframe's $46.28).
	// Scoped to #main so recent-trades rows cannot be confused with quote-panel rows.
	await expect(page.locator("#main .badge", { hasText: "you · Main" })).toHaveCount(2);
	await expect(page.locator("#main tr", { hasText: "10:53:44" })).toHaveText(/\$46\.20/);
	await expect(page.getByText("$46.28")).toHaveCount(0);

	// Quote panel: route, out amounts and source freshness.
	// The panel holds two tables — route/out details, then the risk preview — scope each
	// to its own table so values like "no route" in a risk row can't shadow a route row.
	const routeRows = page.locator("#quote > section > .panel-body > table > tbody > tr");
	const riskRows = page.locator("#quote .table-wrap > table > tbody > tr");
	await expect(routeRows.filter({ hasText: "Route" })).toHaveText(/Jupiter v6 → Raydium CLMM/);
	await expect(routeRows.filter({ hasText: "Expected out" })).toHaveText(/1,403,571 BONK/);
	await expect(routeRows.filter({ hasText: "Minimum out" })).toHaveText(/1,389,535/);
	await expect(routeRows.filter({ hasText: "Minimum out" })).toHaveText(/1\.00% slip/);
	await expect(routeRows.filter({ hasText: "Quote source · age" })).toHaveText(/jupiter · 1s · fixture/);
	// No green "live" dot on a fixture quote: the source claim stays honest.
	await expect(routeRows.locator(".dot-live")).toHaveCount(0);
	await expect(page.getByText("ttl 12s")).toBeVisible();

	// Risk preview reconciles with the shared position fixtures.
	await expect(riskRows.filter({ hasText: "Size after caps" })).toHaveText(/\$36\.60/);
	await expect(riskRows.filter({ hasText: "Size after caps" })).toHaveText(/from \$44\.29/);
	await expect(riskRows.filter({ hasText: "Daily spend after" })).toHaveText(
		/\$250\.00 \/ \$250\.00/,
	);
	await expect(riskRows.filter({ hasText: "Daily spend after" }).locator(".badge")).toHaveText(
		"at cap",
	);
	// Renamed so it cannot be read as the risk page's daily loss limit.
	await expect(riskRows.filter({ hasText: "Daily used after" })).toHaveCount(0);
	await expect(riskRows.filter({ hasText: "Position after fill" })).toHaveText(
		/\$164\.20 · 4\.7% equity/,
	);
	await expect(riskRows.filter({ hasText: "Worst case (stop −35%)" })).toHaveText(/−\$12\.81/);
	// Full-size sell check is its own sell-side measurement, not the buy impact reused.
	await expect(riskRows.filter({ hasText: "Full-size sell check" })).toHaveText(
		/OK · 1 SOL sell quote −0\.3% \(covers the 0\.248 SOL clip\) · Raydium · fixture/,
	);
	await expect(riskRows.filter({ hasText: "Full-size sell check" })).not.toHaveText(/0\.42%/);

	// No contradictory wireframe values survive.
	await expect(page.getByText("$118.00")).toHaveCount(0);
	await expect(page.getByText("$125.69")).toHaveCount(0);

	// Confirm is preview-only in M1; the gate reason is visible text, described-by the button.
	const confirm = page.getByRole("button", { name: "Review & confirm" });
	await expect(confirm).toBeDisabled();
	await expect(confirm).toHaveAttribute("aria-describedby", "confirm-block");
	await expect(page.locator("#confirm-block")).toHaveText("Execution is not wired in M1");
	await expect(page.locator("#confirm-block")).toBeVisible();

	// Preview controls: the amount input and the Buy/Sell toggle cannot be edited.
	await expect(page.getByLabel("Amount in SOL (preview only)")).toHaveAttribute("readOnly", "");
	await expect(page.locator("#quote .seg button[disabled][aria-pressed]")).toHaveCount(2);
	await expect(page.locator("#quote .seg button[disabled]", { hasText: "Buy" })).toHaveAttribute(
		"title",
		"Execution is not wired in M1",
	);

	await expect(page.locator("a[href='/risk'].btn.lg")).toHaveText("Limits");
	await expect(page.locator(".note.warn a[href='/risk']")).toBeVisible();

	await page.screenshot({ path: ".shots/token-1440.png", fullPage: true });
});

test("scamcoin token page fails closed with no fabricated route", async ({ page }) => {
	await page.goto("/tokens/solana/scam");

	await expect(page.getByRole("heading", { name: /Scamcoin/, level: 1 })).toBeVisible();
	await expect(page.locator(".page-actions .badge.err")).toHaveText("NO SELL ROUTE");
	await expect(page.locator(".page-actions")).toHaveText(/last exact quote \$0\.0041 · 2m/);

	// Same layout, fail-closed values: safety fails, route rows stay unknown.
	await expect(page.locator("#safety .panel-head .badge.err")).toHaveText("fail");
	await expect(page.locator("#safety tbody tr")).toHaveCount(7);
	const routeRows = page.locator("#quote > section > .panel-body > table > tbody > tr");
	const riskRows = page.locator("#quote .table-wrap > table > tbody > tr");
	await expect(routeRows.filter({ hasText: "Route" })).toHaveText(/no route/);
	await expect(routeRows.filter({ hasText: "Expected out" })).toHaveText(/—/);
	await expect(riskRows.filter({ hasText: "Full-size sell check" })).toHaveText(/FAIL · no route/);
	await expect(
		routeRows.filter({ hasText: "Quote source · age" }).locator(".dot-live.dead"),
	).toBeVisible();

	// PnL is mark-based and says so; the value cell is the executable last exact quote.
	await expect(page.locator("#mine")).toHaveText(/at mark · not executable · −\$142\.30/);
	await expect(page.locator("#mine")).toHaveText(/executable · last exact quote · 2m/);

	// The holders panel shows a stale snapshot, not data "without a snapshot"
	// while still rendering percentage rows.
	await expect(page.locator("#holders .panel-head .badge")).toHaveText("stale snapshot · 2m");

	// No fabricated route, no confirmable order; the gate reason is visible, not title-only.
	await expect(page.getByText("Jupiter v6")).toHaveCount(0);
	await expect(page.getByText("1,403,571")).toHaveCount(0);
	const confirm = page.getByRole("button", { name: "No route · nothing to confirm" });
	await expect(confirm).toBeDisabled();
	await expect(confirm).toHaveAttribute("aria-describedby", "confirm-block");
	await expect(page.locator("#confirm-block")).toHaveText("No sell route — nothing can be sent");
	await expect(page.locator("#confirm-block")).toBeVisible();

	// Preview controls are inert in M1 — and on SCAM that includes Buy, not just Sell.
	// SCAM has no pay/amount field at all: there is no quote to edit.
	await expect(page.locator("#quote input.input")).toHaveCount(0);
	await expect(
		page.locator("#quote .seg button[disabled]", { hasText: "Buy" }),
	).toHaveAttribute("title", "No sell route — nothing can be sent");

	await page.screenshot({ path: ".shots/token-scam-1440.png", fullPage: true });
});

test("unknown chain or token ids return 404", async ({ page }) => {
	for (const route of ["/tokens/solana/nope", "/tokens/ethereum/bonk", "/tokens/bonk"]) {
		const response = await page.goto(route);
		expect(response?.status()).toBe(404);
	}
	await expect(page.getByRole("heading", { name: "Not found", level: 1 })).toBeVisible();
});

test("token page has no external requests", async ({ page }) => {
	const external: string[] = [];
	page.on("request", (request) => {
		const url = new URL(request.url());
		if (url.protocol === "http:" || url.protocol === "https:") {
			if (url.hostname !== "127.0.0.1" && url.hostname !== "localhost") {
				external.push(request.url());
			}
		}
	});
	await page.goto("/tokens/solana/bonk");
	await expect(page.locator("#safety tbody tr")).toHaveCount(7);
	expect(external, `unexpected external requests: ${external.join(", ")}`).toEqual([]);
});

test("token page screenshot at mobile width", async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await page.goto("/tokens/solana/bonk");
	await expect(page.getByRole("navigation", { name: "Primary mobile navigation" })).toBeVisible();
	const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
	expect(scrollWidth, "page must not scroll sideways on a 390px viewport").toBeLessThanOrEqual(390);
	await page.screenshot({ path: ".shots/token-390.png", fullPage: true });
});
