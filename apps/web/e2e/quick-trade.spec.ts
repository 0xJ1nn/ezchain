import { expect, test, type Page } from "./test";

// One ticket replaces the token page's separate Quick trade and Paper trade panels.
// These checks keep the old surface's intent: presets re-quote on the server, the
// cost sheet is shown before sending, a fill reaches the API exactly once,
// protection is armed by an explicit click, and a missing route fails closed.

const BONK_MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263";
const SCAM_MINT = "Scam1111111111111111111111111111111111111111";

const noExternal = (page: Page) => {
	const external: string[] = [];
	page.on("request", (request) => {
		const url = new URL(request.url());
		if (url.protocol === "http:" || url.protocol === "https:") {
			if (url.hostname !== "127.0.0.1" && url.hostname !== "localhost") {
				external.push(request.url());
			}
		}
	});
	return external;
};

async function hold(page: Page) {
	const submit = page.getByTestId("trade-submit");
	await expect(submit).toBeEnabled();
	await submit.scrollIntoViewIfNeeded();
	const box = await submit.boundingBox();
	if (!box) throw new Error("no submit box");
	await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
	await page.mouse.down();
	await page.waitForTimeout(760);
	await page.mouse.up();
}

/** Arming exits is a held action on the trade screen. */
async function holdExits(page: Page) {
	const arm = page.getByTestId("exits-save");
	await arm.scrollIntoViewIfNeeded();
	const box = await arm.boundingBox();
	if (!box) throw new Error("no exits arm box");
	await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
	await page.mouse.down();
	await page.waitForTimeout(780);
	await page.mouse.up();
}

function fillResponse(id: string, side: "buy" | "sell", amount: string) {
	return {
		id,
		chain: "solana",
		token_address: BONK_MINT,
		side,
		amount_usd: amount,
		status: "filled",
		reason: null,
		quote_kind: "jupiter",
		created_at: "2026-11-09T10:00:00Z",
		filled_at: "2026-11-09T10:00:01Z",
	};
}

test("a preset re-quotes on the server and shows the landed cost sheet before submit", async ({
	page,
}) => {
	const external = noExternal(page);
	await page.goto(`/tokens/solana/${BONK_MINT}`);
	const ticket = page.getByTestId("trade-ticket");
	await expect(ticket).toBeVisible();

	await ticket.getByTestId("ticket-chip-50").click();
	await expect(page).toHaveURL(/[?&]amount=\d+(\.\d+)?(&|$)/, { timeout: 60_000 });
	const sized = Number(await ticket.getByTestId("ticket-amount").inputValue());
	expect(sized, "the 50% chip must size from buying power").toBeGreaterThan(0);
	const expectedOut = (
		await page.locator("#quote tr", { hasText: "Expected out" }).locator("td").last().textContent()
	)?.trim().replace(/^PAPER\s*/, "");
	expect(expectedOut, "the server rendered an expected out").toBeTruthy();
	await expect(ticket).toContainText(expectedOut ?? "");
	await expect(ticket.getByTestId("cost-sheet")).toBeVisible();
	await expect(ticket.getByTestId("cost-sheet")).toContainText("Platform fee");
	await expect(ticket.getByTestId("cost-sheet")).toContainText("Venue fee");
	await expect(ticket.getByTestId("cost-sheet")).toContainText("Estimated impact");
	await expect(ticket.getByTestId("cost-sheet")).toContainText("Receiver's worst case");
	await expect(page.locator("#quote")).toContainText("Jupiter v6 → Raydium");
	await expect(page.getByTestId("trade-submit")).toBeEnabled();
	await expect(ticket.getByTestId("ticket-amount")).toHaveValue(sized.toFixed(2));

	expect(external, `unexpected external requests: ${external.join(", ")}`).toEqual([]);
});

test("Max sizes from the paper account and submits exactly one order", async ({ page }) => {
	const posted: { body: Record<string, unknown> | null; count: number } = { body: null, count: 0 };
	await page.route("**/api/v1/paper/orders", async (route) => {
		posted.count += 1;
		posted.body = route.request().postDataJSON() as Record<string, unknown>;
		await route.fulfill({
			status: 201,
			contentType: "application/json",
			body: JSON.stringify(fillResponse("ord_quick_01", "buy", posted.body?.amount_usd as string)),
		});
	});

	await page.goto(`/tokens/solana/${BONK_MINT}`);
	const ticket = page.getByTestId("trade-ticket");
	await ticket.getByTestId("ticket-chip-max").click();
	await expect(page).toHaveURL(/[?&]amount=/, { timeout: 60_000 });
	const amount = await ticket.getByTestId("ticket-amount").inputValue();
	expect(Number(amount)).toBeGreaterThan(0);
	await expect(ticket.getByTestId("cost-sheet")).toBeVisible();

	await hold(page);
	await expect(ticket.getByTestId("fill-stamp")).toContainText("PAPER FILLED");
	await expect(ticket).toContainText("ord_quick_01");
	expect(posted.count, "the ticket must POST exactly once").toBe(1);
	if (!posted.body) throw new Error("expected a POST body");
	expect(posted.body).toMatchObject({
		chain: "solana",
		token_address: BONK_MINT,
		side: "buy",
	});
	expect(String(posted.body.idempotency_key)).toMatch(/^[0-9a-f-]{16,}$/i);
});

test("exits are armed by an explicit hold, never automatically on a fill", async ({ page }) => {
	await page.route("**/api/v1/paper/orders", async (route) => {
		await route.fulfill({
			status: 201,
			contentType: "application/json",
			body: JSON.stringify(fillResponse("ord_quick_02", "buy", "100")),
		});
	});

	await page.goto(`/tokens/solana/${BONK_MINT}`);
	const exits = page.getByTestId("exits-panel");
	await expect(exits.getByTestId("exits-status")).toHaveCount(0);
	await hold(page);
	await expect(page.getByTestId("fill-stamp")).toBeVisible();
	await expect(exits.getByTestId("exits-status")).toHaveCount(0);

	await holdExits(page);
	await expect(exits.getByTestId("exits-status")).toContainText("Armed 3 exits on paper");
});

test("buy, arm exits, then sell the same token in paper", async ({ page }) => {
	const posted: Record<string, unknown>[] = [];
	await page.route("**/api/v1/paper/orders", async (route) => {
		const body = route.request().postDataJSON() as Record<string, unknown>;
		posted.push(body);
		const side = body.side === "sell" ? "sell" : "buy";
		await route.fulfill({
			status: 201,
			contentType: "application/json",
			body: JSON.stringify(fillResponse(side === "sell" ? "ord_path_sell" : "ord_path_buy", side, String(body.amount_usd))),
		});
	});

	await page.goto(`/tokens/solana/${BONK_MINT}`);
	await hold(page);
	await expect(page.getByTestId("fill-stamp")).toContainText("PAPER FILLED");
	await expect(page.locator("#ticket")).toContainText("ord_path_buy");

	await holdExits(page);
	await expect(page.getByTestId("exits-status")).toContainText("Armed 3 exits on paper");

	await page.getByTestId("ticket-side-sell").click();
	await expect(page).toHaveURL(/side=sell/);
	await hold(page);
	await expect(page.getByTestId("fill-stamp")).toContainText("PAPER SOLD");
	await expect(page.locator("#ticket")).toContainText("ord_path_sell");

	expect(posted.map((body) => body.side)).toEqual(["buy", "sell"]);
	expect(new Set(posted.map((body) => String(body.idempotency_key))).size).toBe(2);
});

test("the ticket fails closed when no route or price exists", async ({ page }) => {
	await page.goto(`/tokens/solana/${SCAM_MINT}`);
	const ticket = page.getByTestId("trade-ticket");
	await expect(ticket).toContainText("NO_SELL_ROUTE");
	await expect(page.getByTestId("trade-submit")).toBeDisabled();
	await expect(ticket.getByTestId("cost-sheet")).toHaveCount(0);
	await expect(ticket).not.toContainText("Jupiter v6");
	await expect(ticket).not.toContainText("3,182,686");

	await page.getByTestId("ticket-side-sell").click();
	await expect(page).toHaveURL(/[?&]side=sell(&|$)/, { timeout: 60_000 });
	await expect(ticket).toContainText("NO_SELL_ROUTE");
	await expect(page.getByTestId("trade-submit")).toBeDisabled();
	await expect(ticket.getByTestId("cost-sheet")).toHaveCount(0);
});
