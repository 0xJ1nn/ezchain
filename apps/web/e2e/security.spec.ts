import { expect, test } from "./test";

// The policy lives in one place (apps/web/lib/csp.ts) and next.config.ts attaches it to every
// response. This asserts the headers on a real document response, because a config block that
// compiles is not evidence the browser received it.
test("every document response carries the security headers", async ({ page }) => {
	const response = await page.goto("/markets");
	expect(response?.status()).toBe(200);
	const headers = response?.headers() ?? {};

	expect(headers["content-security-policy"]).toContain("default-src 'self'");
	expect(headers["content-security-policy"]).toContain("frame-ancestors 'none'");
	expect(headers["content-security-policy"]).toContain("object-src 'none'");
	expect(headers["x-content-type-options"]).toBe("nosniff");
	expect(headers["referrer-policy"]).toBe("same-origin");
	expect(headers["x-frame-options"]).toBe("DENY");
});

test("the API proxy rejects a cross-site mutation before injecting the token", async ({
	request,
}) => {
	const response = await request.post("/api/v1/system/kill-switch", {
		headers: {
			origin: "http://evil.example",
			"sec-fetch-site": "cross-site",
		},
		data: { active: true, reason: "csrf probe" },
	});

	expect(response.status()).toBe(403);
	const body = (await response.json()) as { detail?: { code?: string } };
	expect(body.detail?.code).toBe("CROSS_ORIGIN_DENIED");
});
