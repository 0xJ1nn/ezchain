import path from "node:path";
import type { NextConfig } from "next";
import { readProductVersion } from "./scripts/product-version.mjs";

// The footer shows the product version the API reports. Both come from the root VERSION
// file: the reader throws if it is missing or malformed, so a build that cannot say which
// product it is never ships with a guessed version.
const productVersion = readProductVersion(process.cwd());
if (!/^\d+\.\d+\.\d+$/.test(productVersion)) {
	throw new Error(`Invalid ezCHAIN product version from VERSION: ${productVersion}`);
}

const nextConfig: NextConfig = {
	reactStrictMode: true,
	// The desktop app ships the traced server bundle; it is never served from a public
	// web host. `outputFileTracingRoot` makes the monorepo packages part of that bundle.
	output: "standalone",
	outputFileTracingRoot: path.join(process.cwd(), "../.."),
	transpilePackages: ["@ezchain/ui"],
	// No telemetry, no dev badge overlapping the product UI.
	devIndicators: false,
	allowedDevOrigins: ["127.0.0.1", "localhost"],
	env: {
		EZCHAIN_PRODUCT_VERSION: productVersion,
	},
};

export default nextConfig;
