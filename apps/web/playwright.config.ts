import { defineConfig } from "@playwright/test";
import { browserChannel } from "./e2e/browser-channel";
import { PROXY_TEST_API_URL, PROXY_TEST_TOKEN } from "./e2e/proxy-env";

export default defineConfig({
	testDir: "./e2e",
	testIgnore: "**/live-paper.spec.ts",
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
		// Installed Chrome when present, bundled Chromium otherwise. EZCHAIN_PW_CHANNEL
		// overrides the detection.
		channel: browserChannel(),
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
