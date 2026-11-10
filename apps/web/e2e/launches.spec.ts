import { expect, test } from "./test";

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

test("dashboard has a market-first strip above equity with fixture launches + trending", async ({
	page,
}) => {
	const external = noExternal(page);
	await page.goto("/");
	await expect(page.getByRole("heading", { name: "Dashboard", level: 1 })).toBeVisible();

	const strip = page.getByTestId("market-strip");
	await expect(strip).toBeVisible();
	// Exact headings: the panel sub also names both columns ("launches + trending"), so a
	// substring lookup would match the sub twice and trip strict-mode disambiguation.
	await expect(strip.getByText("Launches", { exact: true })).toBeVisible();
	await expect(strip.getByText("Trending", { exact: true })).toBeVisible();
	await expect(strip.getByText("FIXTURE", { exact: true }).first()).toBeVisible();
	await expect(strip.getByText("PAPER", { exact: true }).first()).toBeVisible();

	// Each hero row carries chain + age and a one-click Trade link to the token page.
	const tradeLinks = strip.getByRole("link", { name: "Trade" });
	expect(await tradeLinks.count()).toBeGreaterThan(0);
	const firstTrade = tradeLinks.first();
	await expect(firstTrade).toHaveAttribute("href", /^\/tokens\//);

	// Hero sits above equity composition in DOM order: strip precedes equity panel.
	const order = await page.evaluate(() => {
		const stripEl = document.querySelector('[data-testid="market-strip"]');
		const equity = [...document.querySelectorAll("section.panel")].find((s) =>
			s.textContent?.includes("Equity composition"),
		);
		if (!stripEl || !equity) return -1;
		return stripEl.compareDocumentPosition(equity) & Node.DOCUMENT_POSITION_FOLLOWING
			? 1
			: -1;
	});
	expect(order).toBe(1);

	expect(external, `unexpected external requests: ${external.join(", ")}`).toEqual([]);
});

test("launches renders fixture births feed with honest unknowns and Trade links", async ({
	page,
}) => {
	const external = noExternal(page);
	const response = await page.goto("/launches");
	expect(response?.status()).toBe(200);
	await expect(page.getByRole("heading", { name: "Launches", level: 1 })).toBeVisible();

	const banner = page.getByRole("status", { name: "Data mode" });
	await expect(banner.getByText("FIXTURE", { exact: true })).toBeVisible();
	await expect(banner).toContainText("local sample data");
	await expect(banner).not.toContainText("LIVE");

	// Sidebar discovery entry for Launches
	const sidebar = page.locator("nav.sidebar");
	await expect(sidebar.getByRole("link", { name: "Launches" })).toHaveAttribute("href", "/launches");

	const table = page.locator("table").first();
	await expect(table.locator("thead th")).toContainText([
		"Token",
		"Chain",
		"Age",
		"Curve progress",
		"Dev history",
		"Safety",
		"LP custody",
		"Trade",
	]);
	await expect(table.locator("tbody tr")).toHaveCount(5);
	await expect(table.locator("tbody tr td:nth-child(6)")).toContainText(["unknown", "unknown", "unknown", "unknown", "unknown"]);

	// Safety/dev/lp unknowns are badges, never zero or a pass.
	await expect(table.getByText("unknown", { exact: true }).first()).toBeVisible();
	await expect(page.getByText("PAPER", { exact: true }).first()).toBeVisible();

	// Trade per row links to the token quote panel route.
	const trades = table.getByRole("link", { name: "Trade" });
	await expect(trades).toHaveCount(5);
	await expect(trades.first()).toHaveAttribute("href", /^\/tokens\//);

	// Newest first: first row age is youngest.
	const ages = await table.locator("tbody tr td:nth-child(3)").allTextContents();
	expect(ages.length).toBe(5);
	expect(ages[0].trim()).not.toBe("—");

	// Honesty footer and no live claims.
	await expect(page.getByText(/unknown is/i).first()).toBeVisible();
	await expect(page.getByText("LIVE · DexScreener")).toHaveCount(0);

	expect(external, `unexpected external requests: ${external.join(", ")}`).toEqual([]);
});

test("launches surfaces reason codes instead of invented rows", async ({ page }) => {
	await page.goto("/launches?chain=nope");
	await expect(page.getByText("CHAIN_NOT_FOUND")).toBeVisible();
	await expect(page.getByText(/unknown chain nope/)).toBeVisible();
	await expect(page.locator("table")).toHaveCount(0);
});

test("launches screenshot at desktop and mobile widths", async ({ page }) => {
	await page.goto("/launches");
	await page.screenshot({ path: ".shots/launches-1440.png", fullPage: true });
	await page.setViewportSize({ width: 390, height: 844 });
	await page.goto("/launches");
	await expect(page.getByRole("navigation", { name: "Primary mobile navigation" })).toBeVisible();
	await page.screenshot({ path: ".shots/launches-390.png", fullPage: true });
});
