import { expect, test } from "@playwright/test";

// Every route the sidebar and the mobile "More" page link to. The list is explicit so a
// removed page fails here even if the sidebar is changed at the same time.
const ROUTES = [
	"/",
	"/positions",
	"/markets",
	"/performance",
	"/wallets",
	"/ledger",
	"/reports",
	"/automations",
	"/strategies",
	"/signals",
	"/backtests",
	"/risk",
	"/alerts",
	"/chains",
	"/integrations",
	"/settings",
	"/security",
	"/developer",
	"/operations",
	"/workspace",
	"/states",
	"/mobile",
	"/components",
	"/more",
];

const BANNED = [
	"not implemented",
	"coming soon",
	"this route is part of the wireframe",
	"implementation lands",
	"placeholder page",
];

for (const route of ROUTES) {
	test(`${route} is a real surface`, async ({ page }) => {
		const response = await page.goto(route);
		expect(response?.status()).toBe(200);
		await expect(page.locator("h1")).toHaveCount(1);
		await expect(page.locator("h1")).toBeVisible();

		const text = (await page.locator("body").innerText()).toLowerCase();
		for (const phrase of BANNED) {
			expect(text, `${route} contains banned placeholder text "${phrase}"`).not.toContain(
				phrase,
			);
		}
	});
}

test("every sidebar link resolves", async ({ page, request }) => {
	await page.goto("/");
	const hrefs = await page
		.locator("nav.sidebar a[href^='/']")
		.evaluateAll((links) => [...new Set(links.map((link) => link.getAttribute("href") ?? ""))]);
	expect(hrefs.length).toBeGreaterThan(10);
	for (const href of hrefs) {
		const response = await request.get(href);
		expect(response.status(), `sidebar link ${href} did not resolve`).toBe(200);
	}
});

test("an unknown route is a 404, not the old catch-all", async ({ page }) => {
	const response = await page.goto("/definitely-not-a-route");
	expect(response?.status()).toBe(404);
	await expect(page.getByRole("heading", { name: "Not found", level: 1 })).toBeVisible();
});
