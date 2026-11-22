import path from "node:path";
import type { NextConfig } from "next";
import { securityHeaders } from "./lib/csp";

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
	// Every response, route handlers and the API proxy included, carries the shared
	// policy in lib/csp.ts. The Electron shell enforces the same directives separately.
	async headers() {
		return [
			{
				source: "/:path*",
				headers: securityHeaders({ dev: process.env.NODE_ENV === "development" }),
			},
		];
	},
};

export default nextConfig;
