import { expect, test } from "./test";

// The chain identity primitives are pinned against the board's exported assets and
// sizes. Fixture strings only; the gallery is the reference surface for this field.

test("coin uses the exported token logo and a 14 px chain badge", async ({ page }) => {
	await page.goto("/components");
	const identity = page.getByTestId("chain-identity");
	await expect(identity).toBeVisible();

	const coins = identity.getByTestId("chain-coins");
	await expect(coins).toHaveCount(1);
	const btc = coins.locator('[data-testid="coin"][data-chain="bitcoin"]');
	await expect(btc).toBeVisible();
	await expect(btc.locator('.coin.img img')).toHaveAttribute("src", "/icons/tokens/BTC.svg");
	await expect(btc.locator(".chb img")).toHaveAttribute("src", "/icons/chains/bitcoin.svg");
	await expect(btc.locator(".chb")).toHaveCSS("width", "14px");
	await expect(btc.locator(".chb")).toHaveCSS("box-shadow", "rgb(19, 22, 26) 0px 0px 0px 2px");
});

test("small and large coins scale the badge to 10 and 18 px", async ({ page }) => {
	await page.goto("/components");
	const coins = page.getByTestId("chain-identity").getByTestId("chain-coins");
	const small = coins.locator('[data-testid="coin"][data-chain="solana"].sm');
	const large = coins.locator('[data-testid="coin"][data-chain="base"].lg');
	await expect(small.locator(".chb")).toHaveCSS("width", "10px");
	await expect(large.locator(".chb")).toHaveCSS("width", "18px");
});

test("a coin without an exported logo falls back to its first letter", async ({ page }) => {
	await page.goto("/components");
	const unknown = page
		.getByTestId("chain-identity")
		.getByTestId("chain-coins")
		.locator('[data-testid="coin"][data-chain=""]');
	await expect(unknown).toBeVisible();
	await expect(unknown.locator(".coin")).toHaveText("X");
	await expect(unknown.locator(".coin img")).toHaveCount(0);
	await expect(unknown.locator(".chb")).toHaveCount(0);
});

test("asset label shows SYM · chain chip, venue and bridged badge", async ({ page }) => {
	await page.goto("/components");
	const labels = page.getByTestId("chain-identity").getByTestId("chain-labels");
	await expect(labels.getByTestId("asset-label")).toHaveCount(4);

	const sol = labels.locator('[data-testid="asset-label"]').nth(0);
	await expect(sol.locator(".nm")).toHaveText("Solana");
	await expect(sol.getByTestId("asset-where")).toContainText("SOL");
	await expect(sol.getByTestId("asset-where")).toContainText("Solana");
	await expect(sol.locator(".where .chimg")).toHaveAttribute("src", "/icons/chains/solana.svg");

	const btc = labels.locator('[data-testid="asset-label"]').nth(1);
	await expect(btc.locator(".where .venue")).toHaveText("Coinbase");
	await expect(btc.locator(".where .chain")).toHaveCount(0);

	const bridged = labels.locator('[data-testid="asset-label"]').nth(2);
	await expect(bridged.locator(".where .bridged")).toHaveText("Bridged · Wormhole");
	await expect(bridged.locator(".where .bridged")).toHaveCSS("color", "rgb(243, 181, 63)");
});

test("an unknown chain is shown as text, never as a guessed mark", async ({ page }) => {
	await page.goto("/components");
	const where = page.getByTestId("chain-identity").getByTestId("chain-where");
	const mystery = where.locator('[data-testid="asset-where"]').last();
	await expect(mystery).toContainText("mystery-chain");
	await expect(mystery.locator("img")).toHaveCount(0);
	await expect(mystery.locator(".bridged")).toHaveCount(1);
});
