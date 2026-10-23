import { expect, test, type Page } from "@playwright/test";

/** The fixture policy every settings page starts from (labelled FIXTURE SAMPLE). */
const FIXTURE_POLICY = {
	default_profile: "mid" as const,
	min_amount_usd: "10",
	min_pct_equity: "0.10",
	max_amount_usd: "1000",
	max_pct_equity: "5",
	profiles: {
		low: {
			min_amount_usd: "10",
			min_pct_equity: "0.10",
			max_amount_usd: "50",
			max_pct_equity: "1",
		},
		mid: {
			min_amount_usd: "10",
			min_pct_equity: "0.10",
			max_amount_usd: "150",
			max_pct_equity: "2",
		},
		high: {
			min_amount_usd: "10",
			min_pct_equity: "0.10",
			max_amount_usd: "500",
			max_pct_equity: "5",
		},
	},
};

function previewFor(profile: string, amount = "123.45") {
	return {
		profile,
		amount_usd: amount,
		reason: null,
		manual: false,
		equity_usd: "10000",
		limits: FIXTURE_POLICY.profiles[profile as keyof typeof FIXTURE_POLICY.profiles],
	};
}

function responseFor(
	policy: typeof FIXTURE_POLICY,
	{
		mid_amount = "150.00",
	}: { mid_amount?: string } = {},
) {
	return {
		...policy,
		source: "persisted" as const,
		updated_at: "2026-10-23T12:00:00Z",
		updated_by: "operator",
		equity_usd: "10000",
		previews: {
			low: previewFor("low", "50.00"),
			mid: previewFor("mid", mid_amount),
			high: previewFor("high", "500.00"),
		},
	};
}

async function stubPreview(page: Page, seen: string[]): Promise<void> {
	await page.route(/\/api\/v1\/settings\/trade-policy\/preview\?/, async (route) => {
		const url = new URL(route.request().url());
		seen.push(url.toString());
		const profile = url.searchParams.get("profile") ?? "mid";
		await route.fulfill({
			status: 200,
			contentType: "application/json",
			body: JSON.stringify(previewFor(profile)),
		});
	});
}

test("saving PUTs the exact policy JSON and shows the committed response", async ({ page }) => {
	const previews: string[] = [];
	let putBody: unknown = null;
	let putCount = 0;
	await stubPreview(page, previews);
	await page.route(/\/api\/v1\/settings\/trade-policy$/, async (route) => {
		if (route.request().method() !== "PUT") {
			await route.continue();
			return;
		}
		putCount += 1;
		putBody = route.request().postDataJSON();
		const saved = {
			...FIXTURE_POLICY,
			profiles: {
				...FIXTURE_POLICY.profiles,
				mid: { ...FIXTURE_POLICY.profiles.mid, max_amount_usd: "200" },
			},
		};
		await route.fulfill({
			status: 200,
			contentType: "application/json",
			body: JSON.stringify(responseFor(saved, { mid_amount: "200.00" })),
		});
	});

	await page.goto("/settings");
	await expect(page.getByRole("heading", { name: "Settings", level: 1 })).toBeVisible();
	await page.getByLabel("mid profile maximum amount USD").fill("200");
	await page.getByRole("button", { name: "Save trade policy" }).click();

	await expect(page.getByRole("status")).toContainText(
		"Saved · 2026-10-23T12:00:00Z · by operator",
	);
	expect(putCount).toBe(1);
	expect(putBody).toEqual({
		...FIXTURE_POLICY,
		profiles: {
			...FIXTURE_POLICY.profiles,
			mid: { ...FIXTURE_POLICY.profiles.mid, max_amount_usd: "200" },
		},
	});
	await expect(page.getByText("persisted · API")).toBeVisible();
});

test("a rejected save is an alert and never claims success", async ({ page }) => {
	const previews: string[] = [];
	await stubPreview(page, previews);
	await page.route(/\/api\/v1\/settings\/trade-policy$/, async (route) => {
		if (route.request().method() !== "PUT") {
			await route.continue();
			return;
		}
		await route.fulfill({
			status: 422,
			contentType: "application/json",
			body: JSON.stringify({
				detail: {
					code: "TRADE_POLICY_INVALID",
					message: "PROFILE_OUTSIDE_ENVELOPE",
					retryable: false,
				},
			}),
		});
	});

	await page.goto("/settings");
	await page.getByRole("button", { name: "Save trade policy" }).click();

	await expect(
		page.getByRole("alert").filter({ hasText: "TRADE_POLICY_INVALID" }),
	).toContainText("TRADE_POLICY_INVALID: PROFILE_OUTSIDE_ENVELOPE");
	await expect(page.getByRole("status")).toHaveCount(0);
	await expect(page.getByText("persisted · API")).toHaveCount(0);
});

test("an invalid draft is not sent at all", async ({ page }) => {
	const previews: string[] = [];
	let putCount = 0;
	await stubPreview(page, previews);
	await page.route(/\/api\/v1\/settings\/trade-policy$/, async (route) => {
		if (route.request().method() !== "PUT") {
			await route.continue();
			return;
		}
		putCount += 1;
		await route.continue();
	});

	await page.goto("/settings");
	// Mid minimum above its maximum: the form refuses locally, before any network write.
	await page.getByLabel("mid profile minimum amount USD").fill("500");
	await page.getByRole("button", { name: "Save trade policy" }).click();

	await expect(
		page.getByRole("alert").filter({ hasText: "not sent" }),
	).toContainText("The policy was not sent: fix the highlighted fields.");
	await expect(page.getByText("Max amount must be at least the minimum.")).toBeVisible();
	expect(putCount).toBe(0);
});

test("changing a field previews the exact unsaved draft against the server", async ({ page }) => {
	const previews: string[] = [];
	await stubPreview(page, previews);

	await page.goto("/settings");
	await page.getByLabel("mid profile maximum amount USD").fill("200");

	await expect
		.poll(() =>
			previews.some((url) => new URL(url).searchParams.get("max_amount_usd") === "200"),
		)
		.toBe(true);
	const request = previews.find(
		(url) => new URL(url).searchParams.get("max_amount_usd") === "200",
	);
	expect(request).toBeDefined();
	const params = new URL(request as string).searchParams;
	expect(Object.fromEntries(params)).toEqual({
		profile: "mid",
		min_amount_usd: "10",
		min_pct_equity: "0.10",
		max_amount_usd: "200",
		max_pct_equity: "2",
		envelope_min_amount_usd: "10",
		envelope_min_pct_equity: "0.10",
		envelope_max_amount_usd: "1000",
		envelope_max_pct_equity: "5",
	});
	await expect(page.getByText("$123.45")).toBeVisible();
});

test("an unknown-equity preview says why no size can be derived", async ({ page }) => {
	await page.route(/\/api\/v1\/settings\/trade-policy\/preview\?/, async (route) => {
		await route.fulfill({
			status: 200,
			contentType: "application/json",
			body: JSON.stringify({
				...previewFor("mid"),
				amount_usd: null,
				reason: "EQUITY_UNKNOWN",
			}),
		});
	});

	await page.goto("/settings");

	await expect(
		page.getByText(/Equity cannot be valued because an open position has no mark/),
	).toBeVisible();
});

test("settings renders at desktop and mobile widths", async ({ page }) => {
	const previews: string[] = [];
	await stubPreview(page, previews);
	await page.goto("/settings");
	await expect(page.getByRole("heading", { name: "Settings", level: 1 })).toBeVisible();
	await page.screenshot({ path: ".shots/settings-1440.png", fullPage: true });

	await page.setViewportSize({ width: 390, height: 844 });
	await page.reload();
	await expect(page.getByRole("button", { name: "Save trade policy" })).toBeVisible();
	await page.screenshot({ path: ".shots/settings-390.png", fullPage: true });
});
