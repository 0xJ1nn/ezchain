// The one content-security policy for every ezCHAIN surface.
//
// The Next server sends these headers on every response (apps/web/next.config.ts) and the
// Electron shell applies the same policy to the app's own pages (apps/desktop/main.cjs via
// apps/desktop/sender.js). apps/desktop/test/sender-validation.mjs asserts the two copies
// are byte-identical, so a change here cannot silently reach only one deployment.
//
// What each directive protects:
//   default-src 'self'        nothing loads from another origin by default
//   script-src 'self'         only bundles this origin served; no eval in production
//   style-src 'self'          plus 'unsafe-inline': Next 15 injects its own <style> tags
//   img-src 'self' data:      inline data images; no remote pixels
//   font-src 'self'           the fonts are bundled local files
//   connect-src 'self'        fetches and streams stay on the origin that served the app
//   object-src 'none'         no plugin or embed execution
//   base-uri 'self'           an injected <base> cannot retarget relative URLs
//   form-action 'self'        a form cannot post off-site
//   frame-ancestors 'none'    the app cannot be framed (clickjacking)
//
// `script-src` also carries 'unsafe-inline'. That is required, not decoration: the App
// Router renders its bootstrap, the React Server Components payload and the theme/sidebar
// bootstraps as inline <script> tags. A policy of only 'self' blocks them, and the page
// never hydrates -- verified by loading the app with that policy and observing the
// "Executing inline script violates ..." errors and no client-side handlers. A nonce would
// fix it, but it requires every route to render dynamically, and the static shell routes
// are deliberately static. Production never allows 'unsafe-eval'; development does,
// because `next dev` compiles with eval-based source maps.

export type SecurityHeader = { key: string; value: string };

export function contentSecurityPolicy(options: { dev?: boolean } = {}): string {
	const scriptSrc = options.dev
		? "script-src 'self' 'unsafe-inline' 'unsafe-eval'"
		: "script-src 'self' 'unsafe-inline'";
	return [
		"default-src 'self'",
		scriptSrc,
		"style-src 'self' 'unsafe-inline'",
		"img-src 'self' data:",
		"font-src 'self'",
		"connect-src 'self'",
		"object-src 'none'",
		"base-uri 'self'",
		"form-action 'self'",
		"frame-ancestors 'none'",
	].join("; ");
}

/** The production policy. The Electron shell applies exactly this string. */
export const CONTENT_SECURITY_POLICY = contentSecurityPolicy();

/** The headers every web response carries. */
export function securityHeaders(options: { dev?: boolean } = {}): SecurityHeader[] {
	return [
		{ key: "Content-Security-Policy", value: contentSecurityPolicy(options) },
		{ key: "X-Content-Type-Options", value: "nosniff" },
		{ key: "Referrer-Policy", value: "same-origin" },
		{ key: "X-Frame-Options", value: "DENY" },
	];
}
