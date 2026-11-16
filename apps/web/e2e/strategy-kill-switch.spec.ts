import { expect, test } from "./test";

// One persisted flag has two scopes the API reports. Both the dashboard and the risk page
// must use the same scope words, name the switch rather than a deployment-wide risk verdict,
// and keep the manual-order caveat visible when armed and when released.

test("released: the card names the switch, its scopes and the manual-order caveat", async ({
	page,
}) => {
	await page.goto("/");
	const card = page.getByTestId("dashboard-kill-switch");
	await expect(card.getByText("RELEASED", { exact: true })).toBeVisible();
	await expect(card).toContainText("Kill switch");
	await expect(card).toContainText("global and strategy levels");
	await expect(card.getByTestId("kill-switch-level-global")).toContainText("Global");
	await expect(card.getByTestId("kill-switch-level-strategy")).toContainText("Strategy");
	await expect(card).toContainText("Manual paper orders are entries");
	await expect(card.getByRole("link", { name: /What it does not do/ })).toHaveAttribute(
		"href",
		"/risk",
	);
	// The whole-picture word is not used as a verdict for this one flag.
	await expect(page.getByText("HALTED", { exact: true })).toHaveCount(0);

	await page.goto("/risk");
	const risk = page.locator("section.panel").filter({ hasText: "Kill switch" }).first();
	await expect(risk).toContainText("global and strategy scope");
	await expect(risk).toContainText("Manual paper orders are entries");
});

test("armed: both scopes block while the manual-order caveat remains", async ({ page }) => {
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
				updated_at: "2026-11-13T12:09:00Z",
				scope: "global",
				scopes: ["global", "strategy"],
			}),
		});
	});

	await page.goto("/");
	const control = page.locator(".topbar").getByRole("button", { name: /Kill switch:/ });
	await control.click();
	const dialog = page.getByRole("dialog", { name: "Arm the kill switch?" });
	await dialog.getByLabel("Reason for the operator log").fill("e2e: strategy scope wording");
	await dialog.getByRole("button", { name: "Confirm arm" }).click();

	const card = page.getByTestId("dashboard-kill-switch");
	await expect(card.getByText("ARMED", { exact: true })).toBeVisible();
	for (const level of ["global", "strategy"]) {
		await expect(card.getByTestId(`kill-switch-level-${level}`).getByText("BLOCKS", { exact: true })).toBeVisible();
	}
	await expect(card).toContainText("Manual paper orders are entries");
	await expect(card).toContainText("exits, stops and reduce-only sells still settle");
	await expect(page.getByText("HALTED", { exact: true })).toHaveCount(0);
});
