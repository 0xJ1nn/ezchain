import { defineConfig } from "@playwright/test";
import { PROXY_TEST_API_URL, PROXY_TEST_TOKEN } from "./e2e/proxy-env";

export default defineConfig({
	testDir: "./e2e",
	globalSetup: "./e2e/global-setup.ts",
	// `next dev` compiles each route on first hit; under host load a cold compile alone can
	// exceed 30s, so the budget is generous and parallelism is capped to half the cores.
	timeout: 90_000,
	expect: { timeout: 15_000 },
	fullyParallel: true,
	workers: "50%",
	reporter: [["list"]],
	use: {
		baseURL: "http://127.0.0.1:3100",
		// Installed Chrome by default; EZCHAIN_PW_CHANNEL=chromium uses the bundled build
		// (Linux hosts without /opt/google/chrome).
		channel: process.env.EZCHAIN_PW_CHANNEL || "chrome",
		viewport: { width: 1440, height: 900 },
		trace: "retain-on-failure",
	},
	webServer: {
		command: "NEXT_TELEMETRY_DISABLED=1 pnpm dev --port 3100",
		url: "http://127.0.0.1:3100",
		reuseExistingServer: false,
		timeout: 120_000,
		env: {
			EZCHAIN_WEB_MODE: "fixture",
			EZCHAIN_API_URL: PROXY_TEST_API_URL,
			EZCHAIN_API_TOKEN: PROXY_TEST_TOKEN,
		},
	},
});
