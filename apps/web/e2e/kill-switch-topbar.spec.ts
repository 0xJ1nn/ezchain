import { expect, test } from "./test";

const control = (page: import("@playwright/test").Page) =>
	page.locator(".topbar").getByRole("button", { name: /Kill switch:/ });

const label = (page: import("@playwright/test").Page) =>
	page.getByTestId("topbar-kill-label");

function path(page: import("@playwright/test").Page) {
	return new URL(page.url()).pathname;
}

test("the top bar shows the persisted state and arms in place", async ({ page }) => {
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
	expect(path(page)).toBe("/");
	expect(posts).toEqual([{ active: true, reason: "e2e arm" }]);
});

test("a refused arm keeps the old state and shows the API reason", async ({ page }) => {
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
