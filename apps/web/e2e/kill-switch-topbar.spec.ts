import { createServer, type Server } from "node:http";
import { expect, test } from "./test";
import { PROXY_TEST_API_URL } from "./proxy-env";

const control = (page: import("@playwright/test").Page) =>
	page.locator(".topbar").getByRole("button", { name: /Kill switch:/ });

const label = (page: import("@playwright/test").Page) =>
	page.getByTestId("topbar-kill-label");

function path(page: import("@playwright/test").Page) {
	return new URL(page.url()).pathname;
}

// Fixture-mode tests render the sample state the shell reads. The server-GET tests below
// need the web server itself to read an API answer, which fixture mode never does, so
// they run when the gate starts with EZCHAIN_WEB_MODE=live and are skipped otherwise.
const live = process.env.EZCHAIN_WEB_MODE === "live";
const apiPort = Number(
	new URL(process.env.EZCHAIN_API_URL ?? PROXY_TEST_API_URL).port || 80,
);

const fixtureOnly =
	"drives the fixture sample state; the server-read cases at the end run under EZCHAIN_WEB_MODE=live";

test("the top bar shows the persisted state and arms in place", async ({ page }) => {
	test.skip(live, fixtureOnly);
	const posts: Array<{ active: boolean; reason: string }> = [];
	await page.route("**/api/v1/system/kill-switch", async (route) => {
		if (route.request().method() !== "POST") {
			await route.continue();
			return;
		}
		const body = route.request().postDataJSON() as { active: boolean; reason: string };
		posts.push(body);
		await route.fulfill({
			status: 200,
			contentType: "application/json",
			body: JSON.stringify({
				active: body.active,
				reason: body.reason,
				updated_at: "2026-11-13T12:00:00Z",
				scope: "global",
				scopes: ["global", "strategy"],
			}),
		});
	});

	await page.goto("/");

	const button = control(page);
	await expect(button).toBeVisible();
	await expect(label(page)).toContainText("RELEASED");
	await expect(button).toHaveAttribute("aria-label", /RELEASED/);
	await expect(button).toHaveAttribute("aria-label", /global and strategy/);

	await button.click();
	expect(path(page)).toBe("/");

	const dialog = page.getByRole("dialog", { name: "Arm the kill switch?" });
	await expect(dialog).toBeVisible();
	await expect(dialog.getByLabel("Reason for the operator log")).toBeFocused();
	await expect(dialog).toContainText("global and strategy");

	await dialog.getByLabel("Reason for the operator log").fill("e2e arm");
	await dialog.getByRole("button", { name: "Confirm arm" }).click();

	await expect(dialog).toHaveCount(0);
	await expect(label(page)).toContainText("ARMED");
	await expect(button).toHaveAttribute("aria-label", /ARMED/);
	// The notice names the scopes the response carried, not the ones the page loaded with.
	await expect(page.locator(".topbar").getByRole("status")).toContainText(
		"Armed · global and strategy",
	);
	expect(path(page)).toBe("/");
	expect(posts).toEqual([{ active: true, reason: "e2e arm" }]);
});

// The topbar is rendered by the shell from a server snapshot. A mutation on the risk page
// changes the same persisted flag, so the launcher must follow it in place instead of
// showing the RELEASED state the page loaded with.
test("a risk-page mutation updates the top bar without a reload", async ({ page }) => {
	test.skip(live, fixtureOnly);
	await page.route("**/api/v1/system/kill-switch", async (route) => {
		if (route.request().method() !== "POST") {
			await route.continue();
			return;
		}
		const body = route.request().postDataJSON() as { active: boolean; reason: string };
		await route.fulfill({
			status: 200,
			contentType: "application/json",
			body: JSON.stringify({
				active: body.active,
				reason: body.reason,
				updated_at: "2026-11-13T12:05:00Z",
				scope: "global",
				scopes: ["global", "strategy"],
			}),
		});
	});

	await page.goto("/risk");
	const topbar = control(page);
	await expect(label(page)).toContainText("RELEASED");

	await page.evaluate(() => {
		(window as unknown as { __killSwitchPage?: number }).__killSwitchPage = 1;
	});

	const card = page.locator("#main");
	await card.getByRole("button", { name: "Arm kill switch" }).click();
	await card.getByLabel("Reason for the operator log").fill("risk card arm");
	await card.getByRole("button", { name: "Confirm arm" }).click();

	await expect(card.getByRole("status")).toContainText("Armed · persisted at");
	await expect(label(page)).toContainText("ARMED");
	await expect(topbar).toHaveAttribute("aria-label", /ARMED/);
	expect(path(page)).toBe("/risk");
	const marker = await page.evaluate(
		() => (window as unknown as { __killSwitchPage?: number }).__killSwitchPage,
	);
	expect(marker, "the page was not reloaded").toBe(1);
});

// The same flag is shown twice on /risk. Arming from the topbar must move the card too,
// or the page shows two different states for one persisted switch.
test("a topbar mutation updates the risk card without a reload", async ({ page }) => {
	test.skip(live, fixtureOnly);
	await page.route("**/api/v1/system/kill-switch", async (route) => {
		if (route.request().method() !== "POST") {
			await route.continue();
			return;
		}
		const body = route.request().postDataJSON() as { active: boolean; reason: string };
		await route.fulfill({
			status: 200,
			contentType: "application/json",
			body: JSON.stringify({
				active: body.active,
				reason: body.reason,
				updated_at: "2026-11-13T12:15:00Z",
				scope: "global",
				scopes: ["global", "strategy"],
			}),
		});
	});

	await page.goto("/risk");
	const card = page.locator("#main");
	await expect(card.getByText("RELEASED", { exact: true })).toBeVisible();

	await page.evaluate(() => {
		(window as unknown as { __killSwitchCard?: number }).__killSwitchCard = 1;
	});

	await control(page).click();
	const dialog = page.getByRole("dialog", { name: "Arm the kill switch?" });
	await dialog.getByLabel("Reason for the operator log").fill("topbar arm");
	await dialog.getByRole("button", { name: "Confirm arm" }).click();

	await expect(dialog).toHaveCount(0);
	await expect(label(page)).toContainText("ARMED");
	await expect(card.getByText("ARMED", { exact: true })).toBeVisible();
	const marker = await page.evaluate(
		() => (window as unknown as { __killSwitchCard?: number }).__killSwitchCard,
	);
	expect(marker, "the page was not reloaded").toBe(1);
});

test("a refused arm keeps the old state and shows the API reason", async ({ page }) => {
	test.skip(live, fixtureOnly);
	let posts = 0;
	await page.route("**/api/v1/system/kill-switch", async (route) => {
		if (route.request().method() !== "POST") {
			await route.continue();
			return;
		}
		posts += 1;
		await route.fulfill({
			status: 400,
			contentType: "application/json",
			body: JSON.stringify({
				detail: {
					code: "KILL_SWITCH_REASON_REQUIRED",
					message: "a reason is required",
				},
			}),
		});
	});

	await page.goto("/");
	await control(page).click();

	const dialog = page.getByRole("dialog", { name: "Arm the kill switch?" });
	await dialog.getByRole("button", { name: "Confirm arm" }).click();
	await expect(dialog.getByRole("alert")).toContainText("A reason is required");
	expect(posts).toBe(0);

	await dialog.getByLabel("Reason for the operator log").fill("try anyway");
	await dialog.getByRole("button", { name: "Confirm arm" }).click();
	await expect(dialog.getByRole("alert")).toContainText(
		"KILL_SWITCH_REASON_REQUIRED: a reason is required",
	);

	await expect(dialog).toBeVisible();
	await expect(label(page)).toContainText("RELEASED");
	expect(posts).toBe(1);
});

test("the k shortcut opens the confirmation and stays out of text fields", async ({ page }) => {
	test.skip(live, fixtureOnly);
	await page.goto("/");

	await page.keyboard.press("k");
	const dialog = page.getByRole("dialog", { name: "Arm the kill switch?" });
	await expect(dialog).toBeVisible();
	expect(path(page)).toBe("/");

	await page.keyboard.press("Escape");
	await expect(dialog).toHaveCount(0);

	const search = page.locator('.topbar input[type="search"]');
	await search.click();
	await page.keyboard.type("k");
	await expect(dialog).toHaveCount(0);
	await expect(search).toHaveValue("k");

	await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
	await page.keyboard.press("k");
	await expect(dialog).toBeVisible();

	const reason = dialog.getByLabel("Reason for the operator log");
	await reason.click();
	await page.keyboard.type("kk");
	await expect(reason).toHaveValue("kk");
	await expect(page.getByRole("dialog")).toHaveCount(1);
});

test("the kill switch label is centred at desktop and phone widths", async ({ page }) => {
	test.skip(live, fixtureOnly);
	for (const width of [1440, 390]) {
		await page.setViewportSize({ width, height: 900 });
		await page.goto("/");

		const button = control(page);
		await expect(button).toBeVisible();

		const box = await button.boundingBox();
		const text = await label(page).boundingBox();
		expect(box).not.toBeNull();
		expect(text).not.toBeNull();
		if (!box || !text) continue;

		const buttonCenter = box.x + box.width / 2;
		const textCenter = text.x + text.width / 2;
		expect(Math.abs(buttonCenter - textCenter)).toBeLessThanOrEqual(1);

		// The control must fit the viewport at the phone width too.
		expect(box.x).toBeGreaterThanOrEqual(0);
		expect(box.x + box.width).toBeLessThanOrEqual(width);
	}
});

// The shell reads the flag on the server, so these two cases start a mock API that the
// web server talks to and assert what that read renders. `reply` is switched per test;
// serial mode keeps both in one worker and one server on the configured API port.
test.describe("the state the server read renders", () => {
	test.describe.configure({ mode: "serial" });

	let api: Server | null = null;
	let reply: "armed" | "down" = "armed";

	test.beforeAll(async () => {
		if (!live) return;
		api = createServer((request, response) => {
			const url = new URL(request.url ?? "/", "http://127.0.0.1");
			response.setHeader("content-type", "application/json");
			if (url.pathname === "/api/v1/system/kill-switch") {
				if (reply === "down") {
					response.statusCode = 503;
					response.end(
						JSON.stringify({
							detail: {
								code: "DB_DOWN",
								message: "the switch store is unavailable",
								retryable: true,
							},
						}),
					);
					return;
				}
				response.end(
					JSON.stringify({
						active: true,
						reason: "server read",
						updated_at: "2026-11-13T12:10:00Z",
						scope: "global",
						scopes: ["global", "strategy"],
					}),
				);
				return;
			}
			response.statusCode = 404;
			response.end(JSON.stringify({ detail: { code: "NOT_FOUND", message: "no mock route" } }));
		});
		await new Promise<void>((resolve, reject) => {
			api?.once("error", reject);
			api?.listen(apiPort, "127.0.0.1", resolve);
		});
	});

	test.afterAll(async () => {
		if (!api) return;
		await new Promise<void>((resolve) => api?.close(() => resolve()));
	});

	test("a server read of ARMED renders ARMED in the top bar", async ({ page }) => {
		test.skip(!live, "needs EZCHAIN_WEB_MODE=live so the shell performs the GET");
		reply = "armed";
		await page.goto("/risk");

		const button = control(page);
		await expect(label(page)).toContainText("ARMED");
		await expect(button).toHaveAttribute("aria-label", /ARMED/);
		await expect(button).toHaveAttribute("aria-label", /global and strategy/);
	});

	test("a failed server read renders UNKNOWN and promises nothing", async ({ page }) => {
		test.skip(!live, "needs EZCHAIN_WEB_MODE=live so the shell performs the GET");
		reply = "down";
		await page.goto("/risk");

		const button = control(page);
		await expect(label(page)).toContainText("UNKNOWN");
		await expect(button).toHaveAttribute("aria-label", /UNKNOWN/);
		// No guessed state, no invented scope name and no promised action.
		await expect(button).not.toHaveAttribute("aria-label", /ARMED|RELEASED|global|strategy/);
		await expect(button).not.toHaveAttribute("title", /\barm\b|\brelease\b/i);

		await button.click();
		const dialog = page.getByRole("dialog", { name: "Kill switch state unavailable" });
		await expect(dialog).toBeVisible();
		await expect(dialog).toContainText("will not guess");
	});
});
