import path from "node:path";
import type { NextConfig } from "next";

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
};

export default nextConfig;
