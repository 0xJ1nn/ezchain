import { expect, test } from "@playwright/test";

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
