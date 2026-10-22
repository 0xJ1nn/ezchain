import { expect, test } from "@playwright/test";

test("markets renders the screener from fixtures with chain counts and position links", async ({
	page,
}) => {
	const external: string[] = [];
	page.on("request", (request) => {
		const url = new URL(request.url());
		if (url.protocol === "http:" || url.protocol === "https:") {
			if (url.hostname !== "127.0.0.1" && url.hostname !== "localhost") {
				external.push(request.url());
			}
		}
	});

	await page.goto("/markets");
	await expect(page.getByRole("heading", { name: "Markets", level: 1 })).toBeVisible();
	await expect(page.getByText("Fixture snapshot", { exact: true })).toBeVisible();
	await expect(page.getByText("Last sweep")).toHaveCount(0);

	// Chain tabs derive their counts from the 12 fixture rows.
	await expect(page.getByRole("button", { name: /All chains\s+12/ })).toHaveAttribute(
		"aria-pressed",
		"true",
	);
	await expect(page.getByRole("button", { name: /Solana\s+5/ })).toBeVisible();
	await expect(page.getByRole("button", { name: /Base\s+4/ })).toBeVisible();
	await expect(page.getByRole("button", { name: /Ethereum\s+2/ })).toBeVisible();
	await expect(page.getByRole("button", { name: /BNB\s+1/ })).toBeVisible();
	await expect(page.getByRole("link", { name: /Tiers & RPC/ })).toHaveAttribute(
		"href",
		"/chains",
	);

	const marketTable = page.locator("table").first();
	await expect(marketTable.locator("tbody tr")).toHaveCount(12);

	// Token cells route to the token screen.
	await expect(marketTable.getByRole("link", { name: /BONK/ })).toHaveAttribute(
		"href",
		"/tokens/solana/bonk",
	);
	await expect(marketTable.getByRole("link", { name: /SCAM/ })).toHaveAttribute(
		"href",
		"/tokens/solana/scam",
	);

	// Open positions link to their position; closed ones stay plain text.
	await expect(marketTable.getByRole("link", { name: /RIDING/ })).toHaveAttribute(
		"href",
		"/positions/bonk",
	);
	await expect(marketTable.getByRole("link", { name: /ENTERING/ })).toHaveAttribute(
		"href",
		"/positions/mochi",
	);
	await expect(marketTable.getByRole("link", { name: /PRE-BANK/ })).toHaveAttribute(
		"href",
		"/positions/aerodrome",
	);
	await expect(marketTable.getByRole("link", { name: /UNSELLABLE/ })).toHaveAttribute(
		"href",
		"/positions/scamcoin",
	);
	await expect(marketTable.getByText("closed · +15.0%", { exact: true })).toBeVisible();
	await expect(marketTable.getByText("closed · +15.3%", { exact: true })).toBeVisible();
	await expect(marketTable.getByText("closed · 0.0%", { exact: true })).toBeVisible();
	await expect(
		marketTable.locator("a", { hasText: "closed · +15.0%" }),
	).toHaveCount(0);

	// A row with no position prints an em dash, never a fabricated zero.
	const degenRow = marketTable.locator("tbody tr").filter({ hasText: "DEGEN" });
	await expect(degenRow.locator("td").last()).toHaveText("—");

	// The SCAM row keeps the safety failure and unsellable state visible.
	await expect(marketTable.getByText("fail · honeypot")).toBeVisible();
	await expect(marketTable.getByText("thin liq")).toBeVisible();

	// Page actions.
	await expect(page.getByRole("link", { name: "Signal feed" })).toHaveAttribute(
		"href",
		"/signals",
	);
	await expect(page.getByRole("link", { name: "Open BONK" })).toHaveAttribute(
		"href",
		"/tokens/solana/bonk",
	);
	await expect(page.getByRole("link", { name: "Trade", exact: true })).toHaveAttribute(
		"href",
		"/tokens/solana/bonk",
	);

	// Filters are preview-only in M1.
	await expect(page.locator("select")).toHaveCount(5);
	await expect(page.getByRole("searchbox", { name: "Search symbol or mint" })).toBeDisabled();
	await expect(page.getByRole("switch", { name: "Watchlist only" })).toHaveAttribute(
		"aria-checked",
		"true",
	);
	await expect(
		page.getByRole("searchbox", { name: "Search symbol or mint" }),
	).toHaveAttribute("title", "Search not wired in M1");

	// Sources and freshness budgets carry the live-dot state.
	await expect(page.getByText(/Sources: Jupiter v6/)).toBeVisible();
	await expect(page.getByText(/Freshness: quotes/)).toBeVisible();
	await expect(page.locator(".panel .dot-live").first()).toBeVisible();

	expect(external, `unexpected external requests: ${external.join(", ")}`).toEqual([]);
});

test("markets new pairs show unknown values and honest gate badges", async ({ page }) => {
	await page.goto("/markets");
	const newPairsPanel = page.locator("section.panel").filter({
		has: page.getByRole("heading", { name: "New pairs" }),
	});

	await expect(newPairsPanel.locator("tbody tr")).toHaveCount(5);
	await expect(newPairsPanel.getByText("mint auth active")).toBeVisible();
	await expect(newPairsPanel.getByText("LP unlocked")).toBeVisible();
	await expect(newPairsPanel.getByText("thin liq")).toBeVisible();
	await expect(newPairsPanel.getByText("blocked by rule")).toBeVisible();
	await expect(newPairsPanel.getByText("watch only")).toBeVisible();

	const quoteButtons = newPairsPanel.getByRole("button", { name: "Quote" });
	await expect(quoteButtons).toHaveCount(3);
	await expect(quoteButtons.first()).toBeDisabled();

	const basedRow = newPairsPanel.locator("tbody tr").filter({ hasText: "BASED" });
	await expect(basedRow.locator("td").nth(4)).toHaveText("—");
	await expect(basedRow.locator("td").nth(4)).toHaveAttribute(
		"title",
		"mcap unknown · indexer has no pair snapshot",
	);

	const definitionPanel = page.locator("section.panel").filter({
		has: page.getByRole("heading", { name: "Screen definition" }),
	});
	await expect(definitionPanel.locator(".stack-sm > div")).toHaveCount(5);
	await expect(definitionPanel.getByText(/mint\/freeze authority/)).toBeVisible();
	await expect(definitionPanel.getByRole("link", { name: /Chain matrix/ })).toHaveAttribute(
		"href",
		"/chains",
	);
});

test("markets screenshot at desktop and mobile widths", async ({ page }) => {
	await page.goto("/markets");
	await page.screenshot({ path: ".shots/markets-1440.png", fullPage: true });

	await page.setViewportSize({ width: 390, height: 844 });
	await page.goto("/markets");
	await expect(page.getByRole("navigation", { name: "Primary mobile navigation" })).toBeVisible();
	await page.screenshot({ path: ".shots/markets-390.png", fullPage: true });
});
