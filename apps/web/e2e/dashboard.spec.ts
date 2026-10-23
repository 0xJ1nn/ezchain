import { expect, test } from "@playwright/test";

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
	await expect(page.getByRole("link", { name: "KILL SWITCH", exact: true })).toBeVisible();
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

test("dashboard screenshot at desktop and mobile widths", async ({ page }) => {
	await page.goto("/");
	await page.screenshot({ path: ".shots/dashboard-1440.png", fullPage: true });

	await page.setViewportSize({ width: 390, height: 844 });
	await page.goto("/");
	await expect(page.getByRole("navigation", { name: "Primary mobile navigation" })).toBeVisible();
	await page.screenshot({ path: ".shots/dashboard-390.png", fullPage: true });
});
