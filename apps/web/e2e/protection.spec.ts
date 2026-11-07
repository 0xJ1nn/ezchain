import { expect, test } from "@playwright/test";

// Position protection arms: arming from the position row, editing and removing an armed
// arm, and the automations list that mirrors the same records. Fixture mode keeps
// session mutations in the browser session, so each test starts from the fixture armory
// and proves the state it creates end to end.

const positionsTable = (page: import("@playwright/test").Page) =>
	page
		.locator("section.panel")
		.filter({ hasText: "Server positions" })
		.locator("table");

const protectionPanel = (page: import("@playwright/test").Page) =>
	page.locator("section.panel").filter({ hasText: "Position protection arms" });

test("a stop-loss can be armed from the position row and reaches the automations list", async ({
	page,
}) => {
	await page.goto("/positions");

	const table = positionsTable(page);
	const mochi = table.locator("tbody tr").filter({ hasText: "MOCHI" });
	// MOCHI has no arm yet: the row offers arming, not a fabricated state.
	await expect(mochi.getByRole("button", { name: "Protect" })).toBeVisible();

	await mochi.getByRole("button", { name: "Protect" }).click();
	const form = mochi.locator("form");
	await expect(form).toBeVisible();

	// A per-chain profile overrides the global default in the picker.
	const profile = form.getByLabel("Profile");
	await expect(profile).toHaveValue("prof-solana-degen");

	await form.getByLabel("Kind").selectOption("stop_loss");
	await form.getByLabel("Drop from avg cost").fill("12");
	await form.getByRole("button", { name: "Arm protection" }).click();

	// The armed arm is rendered from the record the API returned: kind, parameters,
	// status and version. Nothing is invented while the request is in flight.
	await expect(mochi.locator(".badge", { hasText: "armed" })).toBeVisible();
	await expect(mochi).toContainText("Stop loss");
	await expect(mochi).toContainText("−12% from cost");
	await expect(mochi).toContainText("v1");
	await expect(mochi.getByRole("button", { name: "Edit" })).toBeVisible();
	await expect(mochi.getByRole("button", { name: "Remove" })).toBeVisible();

	// The automations list reads the same records: a full page load later the session
	// arm is listed next to the fixture arms.
	await page.goto("/automations");
	const panel = protectionPanel(page);
	const row = panel
		.locator("tbody tr")
		.filter({ hasText: "MOCHI" })
		.filter({ hasText: "Stop loss" });
	await expect(row).toBeVisible();
	await expect(row.locator(".badge", { hasText: "armed" })).toBeVisible();
	await expect(row).toContainText("−12% from cost");
	await expect(panel).toContainText("6 recorded");
	await expect(panel.locator(".badge", { hasText: "PAPER" })).toBeVisible();
});

test("an armed stop can be edited and the version moves with the change", async ({ page }) => {
	await page.goto("/positions");

	const bonk = positionsTable(page).locator("tbody tr").filter({ hasText: "BONK" });
	await expect(bonk.locator(".badge", { hasText: "armed" })).toBeVisible();
	await expect(bonk).toContainText("−15% from cost");
	await expect(bonk).toContainText("v1");

	await bonk.getByRole("button", { name: "Edit" }).click();
	const form = bonk.locator("form");
	await expect(form).toBeVisible();
	await expect(form.getByLabel("Drop from avg cost")).toHaveValue("15");
	await form.getByLabel("Drop from avg cost").fill("18");
	await form.getByRole("button", { name: "Save changes" }).click();

	await expect(bonk).toContainText("−18% from cost");
	await expect(bonk).toContainText("v2");
	// The optimistic local state is gone; the record the API returned is shown.
	await expect(bonk.locator("form")).toHaveCount(0);
});

test("removing an armed arm cancels it instead of hiding it", async ({ page }) => {
	await page.goto("/positions");

	const aero = positionsTable(page).locator("tbody tr").filter({ hasText: "AERO" });
	const trailing = aero.locator(".protection-arm").filter({ hasText: "Trailing stop" });
	await expect(trailing.locator(".badge", { hasText: "armed" })).toBeVisible();

	await trailing.getByRole("button", { name: "Remove" }).click();

	// A disarm is a state change, not a deletion: the arm stays listed as cancelled.
	await expect(trailing.locator(".badge", { hasText: "cancelled" })).toBeVisible();
	await expect(trailing.getByRole("button")).toHaveCount(0);
	// The take-profit on the same position is untouched.
	await expect(
		aero.locator(".protection-arm").filter({ hasText: "Take profit" }).locator(".badge", {
			hasText: "triggered",
		}),
	).toBeVisible();
});

test("automations lists arms with honest states, including EXPIRED and unknowns", async ({
	page,
}) => {
	await page.goto("/automations");

	const panel = protectionPanel(page);
	await expect(panel.locator("tbody tr")).toHaveCount(5);

	// Every state the API can persist is rendered as persisted: nothing is renamed,
	// nothing is dropped, and an unknown value prints — instead of a guess or zero.
	await expect(panel.locator(".badge", { hasText: "armed" })).toHaveCount(2);
	await expect(panel.locator(".badge", { hasText: "triggered" })).toHaveCount(1);
	await expect(panel.locator(".badge", { hasText: "cancelled" })).toHaveCount(1);
	const expired = panel.locator("tbody tr").filter({ hasText: "EXPIRED" });
	await expect(expired).toHaveCount(1);
	await expect(expired).toContainText("ladder 2 rungs");
	// The expired arm has no profile and no update timestamp: both print —, never 0.
	await expect(expired.locator("td").nth(4)).toHaveText("—");
	await expect(expired.locator("td").nth(7)).toHaveText("—");

	// A trailing stop shows its real activation state from the API record.
	const aero = panel.locator("tbody tr").filter({ hasText: "Trailing stop" });
	await expect(aero).toContainText("activated · peak $1.41");
});
