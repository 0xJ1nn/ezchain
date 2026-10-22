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
	await expect(page.getByText("unknown mark excluded").first()).toBeVisible();
	await expect(page.getByRole("link", { name: "KILL SWITCH", exact: true })).toBeVisible();
	await expect(page.getByText("One screen to answer")).toBeVisible();

	expect(external, `unexpected external requests: ${external.join(", ")}`).toEqual([]);
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
