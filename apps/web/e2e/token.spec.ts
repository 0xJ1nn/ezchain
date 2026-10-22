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

	// Chart panel with levels and the last-trade caption.
	await expect(page.locator("#chart polyline.series")).toBeVisible();
	await expect(page.getByText("last trade $0.00003138 · 11:01:02 · spread 0.4bp")).toBeVisible();

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
	await expect(page.locator("#main .badge", { hasText: "you · Main" })).toHaveCount(2);
	await expect(page.locator("tr", { hasText: "10:53:44" })).toHaveText(/\$46\.20/);
	await expect(page.getByText("$46.28")).toHaveCount(0);

	// Quote panel: route, out amounts and source freshness.
	await expect(page.locator("tr", { hasText: "Route" })).toHaveText(/Jupiter v6 → Raydium CLMM/);
	await expect(page.locator("tr", { hasText: "Expected out" })).toHaveText(/1,403,571 BONK/);
	await expect(page.locator("tr", { hasText: "Minimum out" })).toHaveText(/1,389,535/);
	await expect(page.locator("tr", { hasText: "Minimum out" })).toHaveText(/1\.00% slip/);
	await expect(page.locator("tr", { hasText: "Quote source · age" })).toHaveText(/jupiter · 1s/);
	await expect(page.getByText("ttl 12s")).toBeVisible();

	// Risk preview reconciles with the shared position fixtures.
	await expect(page.locator("tr", { hasText: "Size after caps" })).toHaveText(/\$36\.60/);
	await expect(page.locator("tr", { hasText: "Size after caps" })).toHaveText(/from \$44\.29/);
	await expect(page.locator("tr", { hasText: "Daily used after" })).toHaveText(
		/\$250\.00 \/ \$250\.00/,
	);
	await expect(page.locator("tr", { hasText: "Daily used after" }).locator(".badge")).toHaveText(
		"at cap",
	);
	await expect(page.locator("tr", { hasText: "Position after fill" })).toHaveText(
		/\$164\.20 · 4\.7% equity/,
	);
	await expect(page.locator("tr", { hasText: "Worst case (stop −35%)" })).toHaveText(/−\$12\.81/);
	await expect(page.locator("tr", { hasText: "Full-size sell check" })).toHaveText(
		/OK · 0\.42% impact/,
	);

	// No contradictory wireframe values survive.
	await expect(page.getByText("$118.00")).toHaveCount(0);
	await expect(page.getByText("$125.69")).toHaveCount(0);

	// Confirm is preview-only in M1; limits link to risk.
	const confirm = page.getByRole("button", { name: "Review & confirm" });
	await expect(confirm).toBeDisabled();
	await expect(confirm).toHaveAttribute("title", "Execution is not wired in M1");
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
	await expect(page.locator("tr", { hasText: "Route" })).toHaveText(/no route/);
	await expect(page.locator("tr", { hasText: "Expected out" })).toHaveText(/—/);
	await expect(page.locator("tr", { hasText: "Full-size sell check" })).toHaveText(/FAIL · no route/);
	await expect(page.locator("tr", { hasText: "Quote source · age" }).locator(".dot-live.dead")).toBeVisible();

	// No fabricated route, no confirmable order.
	await expect(page.getByText("Jupiter v6")).toHaveCount(0);
	await expect(page.getByText("1,403,571")).toHaveCount(0);
	const confirm = page.getByRole("button", { name: "Review & confirm" });
	await expect(confirm).toBeDisabled();
	await expect(confirm).toHaveAttribute("title", "Execution is not wired in M1");

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
	await page.screenshot({ path: ".shots/token-390.png", fullPage: true });
});
