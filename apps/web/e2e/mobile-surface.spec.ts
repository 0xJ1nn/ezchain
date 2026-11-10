import { expect, test } from "./test";

test("mobile surface renders server rows at 390px with no write control", async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await page.goto("/m");
	await expect(page.getByRole("heading", { name: "Mobile workspace", level: 1 })).toBeVisible();

	await expect(page.getByText("BONK")).toBeVisible();
	await expect(page.getByText("unknown mark", { exact: true })).toBeVisible();

	await expect(page.getByText(/paper-fill|buy/i).first()).toBeVisible();

	// Write controls are checked against the whole document, not just <main>: a mutation
	// rendered in a banner or footer must fail too. The only interactive chrome is the
	// shell's read-only navigation (market search, theme toggle, sidebar collapse, bottom
	// nav); nothing here submits, and there is no textarea or select at all.
	const SHELL_CHROME =
		"header.topbar, nav.sidebar, nav.mobile-nav, .fixture-banner, .live-banner";
	const controls = await page
		.locator("form, button, input, textarea, select")
		.evaluateAll(
			(nodes, chrome) =>
				nodes.map((node) => {
					const root = node.getRootNode();
					const inDevOverlay =
						root instanceof ShadowRoot &&
						root.host.tagName.toLowerCase() === "nextjs-portal";
					return {
						tag: node.tagName.toLowerCase(),
						type: node.getAttribute("type"),
						inShellChrome: Boolean(node.closest(chrome)),
						// The Next.js dev overlay is tooling, not a product control, and it is
						// absent from the production build.
						inDevOverlay,
						method: node.tagName === "FORM" ? (node as HTMLFormElement).method : null,
						action: node.tagName === "FORM" ? node.getAttribute("action") : null,
					};
				}),
			SHELL_CHROME,
		);

	expect(
		controls.filter((control) => !control.inShellChrome && !control.inDevOverlay),
	).toEqual([]);
	expect(controls.filter((control) => control.type === "submit")).toEqual([]);
	expect(controls.filter((control) => control.tag === "textarea" || control.tag === "select")).toEqual(
		[],
	);

	const forms = controls.filter((control) => control.tag === "form");
	expect(forms).toHaveLength(1);
	expect(forms[0].method).toBe("get");
	expect(forms[0].action).toBe("/markets");
});

test("fixture mode is labelled and a stale or unknown mark is never shown as fresh", async ({
	page,
}) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await page.goto("/m");

	// The badge comes from the snapshot mode, and the copy says the rows are samples.
	await expect(page.getByText("FIXTURE", { exact: true }).first()).toBeVisible();
	await expect(page.getByText(/rendered from fixture data/i)).toBeVisible();
	await expect(page.getByText(/not a server response/i)).toBeVisible();

	// The unmarked MOCHI row carries no age at all, and the stale SCAM mark says stale.
	const mochi = page.locator(".list-row", { hasText: "MOCHI" });
	await expect(mochi).toContainText("unknown mark");
	await expect(mochi).toContainText("unknown");

	const scam = page.locator(".list-row", { hasText: "SCAM" });
	await expect(scam).toContainText("stale");

	// Freshness is only asserted when the payload carries a timestamp.
	await expect(page.getByText("age current")).toHaveCount(0);
	await expect(page.getByText("age unknown age")).toHaveCount(0);

	// Multi-day fill ages read as days, not as a huge minute count.
	await expect(page.getByText(/paper_mark · \d+d/).first()).toBeVisible();
});
