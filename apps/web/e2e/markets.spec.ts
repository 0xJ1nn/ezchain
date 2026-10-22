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

	// Chain tabs derive their counts from the 12 fixture rows. Only the active tab is
	// interactive; the rest are disabled previews.
	await expect(page.locator(".tabs .tab.on")).toHaveText(/All chains\s+12/);
	for (const [chain, count] of [
		["Solana", "5"],
		["Base", "4"],
		["Ethereum", "2"],
		["BNB", "1"],
	] as const) {
		const tab = page.getByRole("button", { name: new RegExp(`${chain}\\s+${count}`) });
		await expect(tab).toBeVisible();
		await expect(tab).toBeDisabled();
		await expect(tab).toHaveAttribute("title", "Filter not wired in M1");
	}
	await expect(page.getByRole("link", { name: /Tiers & RPC/ })).toHaveAttribute(
		"href",
		"/chains",
	);

	const marketTable = page.locator("table").first();
	await expect(marketTable.locator("tbody tr")).toHaveCount(12);

	// Only BONK and SCAM have a token route; every other token cell is plain text.
	const tokenLinks = marketTable.locator("tbody tr td:first-child a");
	await expect(tokenLinks).toHaveCount(2);
	await expect(marketTable.getByRole("link", { name: /BONK/ })).toHaveAttribute(
		"href",
		"/tokens/solana/bonk",
	);
	await expect(marketTable.getByRole("link", { name: /SCAM/ })).toHaveAttribute(
		"href",
		"/tokens/solana/scam",
	);
	const wifRow = marketTable.locator("tbody tr").filter({ hasText: "WIF" });
	await expect(wifRow.locator("td").first().locator("a")).toHaveCount(0);

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
	await expect(marketTable.getByText("closed · −11.0%", { exact: true })).toBeVisible();
	await expect(marketTable.getByText("closed · +15.3%", { exact: true })).toBeVisible();
	await expect(marketTable.getByText("closed · 0.0%", { exact: true })).toBeVisible();
	await expect(
		marketTable.locator("a", { hasText: "closed · −11.0%" }),
	).toHaveCount(0);

	// A row with no position prints an em dash, never a fabricated zero.
	const degenRow = marketTable.locator("tbody tr").filter({ hasText: "DEGEN" });
	await expect(degenRow.locator("td").last()).toHaveText("—");

	// The SCAM row keeps the safety failure and unsellable state visible.
	await expect(marketTable.getByText("fail · honeypot")).toBeVisible();
	await expect(marketTable.getByText("thin liq")).toBeVisible();

	// SCAM has no holder count in the fixture; unknown prints an em dash.
	const scamRow = marketTable.locator("tbody tr").filter({ hasText: "SCAM" });
	await expect(scamRow.locator("td").nth(8)).toHaveText("—");

	// Page actions.
	await expect(page.getByRole("link", { name: "Signal feed" })).toHaveAttribute(
		"href",
		"/signals",
	);
	await expect(page.getByRole("link", { name: "Open BONK" })).toHaveAttribute(
		"href",
		"/tokens/solana/bonk",
	);
	await expect(page.getByRole("link", { name: "Trade BONK", exact: true })).toHaveAttribute(
		"href",
		"/tokens/solana/bonk",
	);

	// Filters are preview-only in M1: every control is disabled and says so.
	await expect(page.getByText("Filters are preview-only in M1")).toBeVisible();
	const selects = page.locator("select");
	await expect(selects).toHaveCount(5);
	for (let i = 0; i < 5; i += 1) {
		await expect(selects.nth(i)).toBeDisabled();
	}
	await expect(page.getByRole("searchbox", { name: "Search symbol or mint" })).toBeDisabled();
	await expect(page.getByRole("switch", { name: "Watchlist only" })).toHaveAttribute(
		"aria-checked",
		"false",
	);
	await expect(page.getByRole("switch", { name: "Watchlist only" })).toHaveAttribute(
		"aria-disabled",
		"true",
	);
	await expect(
		page.getByRole("searchbox", { name: "Search symbol or mint" }),
	).toHaveAttribute("title", "Search not wired in M1");

	// The footer states the fixture contract instead of claiming a live feed.
	await expect(page.getByText(/no live feed in M1/)).toBeVisible();
	await expect(page.getByText(/freshness not tracked/)).toBeVisible();
	await expect(page.getByText(/Sources: Jupiter v6/)).toHaveCount(0);
	await expect(page.getByText(/Freshness: quotes/)).toHaveCount(0);
	await expect(page.locator(".panel .dot-live")).toHaveCount(0);

	// The table foot counts fixture rows, not a market universe or pagination.
	const tableFoot = page.locator(".table-foot");
	await expect(tableFoot).toContainText("12 fixture rows · filters preview-only in M1");
	await expect(tableFoot).toContainText("Fixture order · page 1/1");
	await expect(page.getByText(/4,182 markets/)).toHaveCount(0);

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
		"mcap unknown in fixture",
	);
	await expect(newPairsPanel.getByText("fixture snapshot", { exact: true })).toBeVisible();
	await expect(newPairsPanel.getByText(/market\s+cap or holders prints/)).toBeVisible();

	const definitionPanel = page.locator("section.panel").filter({
		has: page.getByRole("heading", { name: "Screen definition" }),
	});
	await expect(definitionPanel.locator(".stack-sm > div")).toHaveCount(5);
	await expect(definitionPanel.getByText(/mint\/freeze authority/)).toBeVisible();
	await expect(definitionPanel.getByRole("link", { name: /Chain matrix/ })).toHaveAttribute(
		"href",
		"/chains",
	);
	await expect(definitionPanel.getByText("Watchlist: not wired in M1")).toBeVisible();
	await expect(definitionPanel.getByText(/3 tokens/)).toHaveCount(0);
});

test("markets screenshot at desktop and mobile widths", async ({ page }) => {
	await page.goto("/markets");
	await page.screenshot({ path: ".shots/markets-1440.png", fullPage: true });

	await page.setViewportSize({ width: 390, height: 844 });
	await page.goto("/markets");
	await expect(page.getByRole("navigation", { name: "Primary mobile navigation" })).toBeVisible();

	// The mobile rule hides secondary columns instead of forcing a horizontal min-width.
	const marketTable = page.locator("table").first();
	const newPairsTable = page.locator("table").nth(1);
	await expect(
		marketTable.locator("thead th").filter({ hasText: "Vol 24h" }),
	).toBeHidden();
	await expect(marketTable.locator("thead th").filter({ hasText: "Holders" })).toBeHidden();
	await expect(newPairsTable.locator("thead th").filter({ hasText: "Liq" })).toBeHidden();
	await expect(newPairsTable.locator("thead th").filter({ hasText: "MCap" })).toBeHidden();
	await expect(
		marketTable.locator("thead th").filter({ hasText: "Token" }).first(),
	).toBeVisible();
	await page.screenshot({ path: ".shots/markets-390.png", fullPage: true });
});
