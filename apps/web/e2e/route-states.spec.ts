import { expect, test } from "./test";
import fs from "node:fs";
import path from "node:path";

const ROOT = path.resolve(__dirname, "..");
const ROUTES = ["/strategies", "/ledger", "/operations", "/security"] as const;
const SEGMENTS = {
	"/strategies": "strategies",
	"/ledger": "ledger",
	"/operations": "operations",
	"/security": "security",
} as const;

for (const route of ROUTES) {
	const segment = SEGMENTS[route];

	test(`${route} ships a loading skeleton and an error boundary`, async () => {
		const loadingPath = path.join(ROOT, "app", segment, "loading.tsx");
		const errorPath = path.join(ROOT, "app", segment, "error.tsx");

		expect(fs.existsSync(loadingPath), `${loadingPath} must exist`).toBe(true);
		expect(fs.existsSync(errorPath), `${errorPath} must exist`).toBe(true);

		const loading = fs.readFileSync(loadingPath, "utf8");
		const error = fs.readFileSync(errorPath, "utf8");

		expect(loading).toContain("Skeleton");
		expect(loading).toContain("AppShell");
		expect(error).toContain('"use client"');
		expect(error).toContain("reset");
		expect(error).toContain("Try again");
		expect(error).toContain("RENDER_FAILED");
	});

	test(`${route} renders fixture data without an error boundary`, async ({ page }) => {
		await page.goto(route);
		await expect(page.locator("h1")).toBeVisible();
		await expect(page.locator(".route-error")).toHaveCount(0);
	});
}
