import { expect, test } from "./test";

// The wallet detail route reads server fields only. These cases pin the three states the
// field rule cares about: populated from paper rows, empty (unallocated bucket), and a
// failed read with its typed reason. No key material is ever part of the rendered page.

test("the wallet list links to detail rendered from server fields", async ({ page }) => {
	await page.goto("/wallets");
	const list = page.getByRole("table", { name: "Paper wallets" });
	await expect(list.locator("tbody tr")).toHaveCount(4);
	await expect(list).toContainText("Solana paper wallet");
	await expect(list.getByRole("link", { name: "Open" }).first()).toHaveAttribute(
		"href",
		"/wallets/wallet_solana",
	);

	await page.goto("/wallets/wallet_solana");
	await expect(page.getByRole("heading", { name: "Solana paper wallet", level: 1 })).toBeVisible();
	const band = page.locator('section.kpi-band[aria-label="Wallet balances"]');
	await expect(band.locator(".stat").filter({ hasText: "Settled cash" }).locator(".value")).toHaveText(
		"$10.00",
	);
	await expect(band).toContainText("wallet_solana");
	await expect(page.getByRole("table", { name: "Wallet positions" })).toBeVisible();

	// No key material of any shape reaches the client: the page names what it does not have.
	await expect(page.getByText(/no key, seed, address or signing secret/i)).toBeVisible();
	const body = await page.locator("main, body").first().innerText();
	expect(body.toLowerCase()).not.toContain("private key");
	expect(body.toLowerCase()).not.toContain("seed phrase");
});

test("an empty wallet and an unknown id are stated, not filled with samples", async ({ page }) => {
	await page.goto("/wallets/unallocated");
	await expect(page.getByRole("heading", { name: "Unallocated", level: 1 }).or(
		page.getByRole("heading", { name: /unallocated/i }),
	)).toBeVisible();
	await expect(page.getByText("No unallocated positions")).toBeVisible();

	const response = await page.goto("/wallets/does-not-exist");
	expect(response?.status()).toBe(404);
});

test("a failed wallet read renders its code and no invented balance", async ({ page }) => {
	await page.goto("/wallets/wallet_solana?state=error");
	await expect(page.getByRole("heading", { name: "Wallet unavailable", level: 1 })).toBeVisible();
	await expect(page.getByText("FIXTURE_DEMO_WALLET_ERROR")).toBeVisible();
	await expect(page.getByText("Fixture demo: the paper wallet request failed.")).toBeVisible();
	await expect(page.locator('section.kpi-band[aria-label="Wallet balances"]')).toHaveCount(0);
	await expect(page.getByRole("link", { name: "Retry →" })).toHaveAttribute("href", "/wallets");
});
