import { expect, test } from "./test";
import fs from "node:fs";
import path from "node:path";

const ROOT = path.resolve(__dirname, "..");
const SEGMENTS = {
	// The strategies list page and its skeleton live in a route group, so the loading
	// boundary wraps the list and not the [id] route, whose notFound() must still be
	// able to answer 404. The error boundary stays at the segment root and covers both.
	"/strategies": { loading: path.join("strategies", "(list)"), error: "strategies" },
	"/ledger": { loading: "ledger", error: "ledger" },
	"/operations": { loading: "operations", error: "operations" },
	"/security": { loading: "security", error: "security" },
} as const;
const ROUTES = Object.keys(SEGMENTS) as Array<keyof typeof SEGMENTS>;

for (const route of ROUTES) {
	const segment = SEGMENTS[route];

	test(`${route} ships a loading skeleton and an error boundary`, async () => {
		const loadingPath = path.join(ROOT, "app", segment.loading, "loading.tsx");
		const errorPath = path.join(ROOT, "app", segment.error, "error.tsx");

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

test("/strategies/nope still answers 404 under the loading boundary", async ({ page }) => {
	// The regression this guards: a loading boundary above [id] streams the shell with a
	// committed 200, so an unknown id renders the not-found page with the wrong status.
	const response = await page.goto("/strategies/nope");
	expect(response?.status()).toBe(404);
	await expect(page.getByRole("heading", { name: "Not found", level: 1 })).toBeVisible();
});
