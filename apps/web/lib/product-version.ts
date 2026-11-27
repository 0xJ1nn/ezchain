// The product version is injected at build time by next.config.ts, which reads the
// repository-root VERSION file — the same source the API probes use. There is no
// fallback literal: a bundle that cannot say which product it is must not guess.

const injectedVersion = process.env.EZCHAIN_PRODUCT_VERSION;

if (!injectedVersion) {
	throw new Error(
		"ezCHAIN product version is unavailable: EZCHAIN_PRODUCT_VERSION was not injected " +
			"from the VERSION file at build time",
	);
}

// Narrow before exporting: an exported `process.env` read keeps its union type across
// module boundaries, so a guard after the export cannot make it a string for callers.
export const PRODUCT_VERSION: string = injectedVersion;
