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
	await expect(page.locator(".hero-stat .value")).toHaveText("$3,482.16");
	await expect(page.getByRole("link", { name: "KILL SWITCH", exact: true })).toBeVisible();
	await expect(page.getByText("One screen to answer")).toBeVisible();

	expect(external, `unexpected external requests: ${external.join(", ")}`).toEqual([]);
});

test("every fixture dashboard panel carries an explicit FIXTURE badge", async ({ page }) => {
	await page.goto("/");
	const panelCount = await page.locator("section.panel").count();
	await expect(page.locator("section.panel .badge", { hasText: "FIXTURE" })).toHaveCount(
		panelCount,
	);
	await expect(page.getByText("fixture preview", { exact: true })).toBeVisible();
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
