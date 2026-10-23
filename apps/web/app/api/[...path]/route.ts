// Server-side proxy for browser calls to the API.
//
// The browser only ever talks to this Next server. Any inbound Authorization header is
// discarded and the deployment token is injected here, so the token never enters the
// browser bundle, a page source, or a client-side request.

import { NextResponse, type NextRequest } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

type RouteContext = { params: Promise<{ path: string[] }> };

const FORWARDED_RESPONSE_HEADERS = [
	"content-type",
	"cache-control",
	"retry-after",
	"location",
	"www-authenticate",
	"x-request-id",
] as const;

function allowedOrigins(request: NextRequest): Set<string> {
	const allowed = new Set<string>();
	const configured = process.env.EZCHAIN_PUBLIC_URL;
	if (configured) allowed.add(configured.replace(/\/+$/, ""));
	// The desktop shell serves exactly one origin. Do not trust an arbitrary Host header:
	// a DNS-rebinding page must not reach the token-injecting proxy.
	if (process.env.EZCHAIN_DESKTOP === "1") return allowed;
	const host = request.headers.get("x-forwarded-host") ?? request.headers.get("host");
	const proto = request.headers.get("x-forwarded-proto") ?? "http";
	if (host) allowed.add(`${proto}://${host}`);
	return allowed;
}

function isCrossOrigin(request: NextRequest): boolean {
	// Browsers send Sec-Fetch-Site; a cross-site value is enough to reject. When the
	// header is absent, compare Origin against the edge host. curl/non-browser clients
	// send neither and remain usable for health and paper testing.
	if (request.headers.get("sec-fetch-site") === "cross-site") return true;
	const origin = request.headers.get("origin");
	if (!origin) return false;
	return !allowedOrigins(request).has(origin.replace(/\/+$/, ""));
}

function apiBase(): string {
	return (process.env.EZCHAIN_API_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
}

async function proxy(request: NextRequest, context: RouteContext): Promise<Response> {
	if (isCrossOrigin(request)) {
		return NextResponse.json(
			{
				detail: {
					code: "CROSS_ORIGIN_DENIED",
					message: "cross-origin API calls are not allowed through the web proxy",
					retryable: false,
				},
			},
			{ status: 403 },
		);
	}
	const { path } = await context.params;
	const target = new URL(`${apiBase()}/api/${path.map(encodeURIComponent).join("/")}`);
	target.search = request.nextUrl.search;

	const headers = new Headers();
	const contentType = request.headers.get("content-type");
	if (contentType) headers.set("content-type", contentType);
	const accept = request.headers.get("accept");
	if (accept) headers.set("accept", accept);

	// Default-deny: a browser-supplied credential is never forwarded.
	const token = process.env.EZCHAIN_API_TOKEN;
	if (token) headers.set("authorization", `Bearer ${token}`);

	const init: RequestInit = {
		method: request.method,
		headers,
		cache: "no-store",
		redirect: "manual",
	};
	if (request.method !== "GET" && request.method !== "HEAD") {
		init.body = await request.arrayBuffer();
	}

	let upstream: Response;
	try {
		upstream = await fetch(target, init);
	} catch {
		return NextResponse.json(
			{
				detail: {
					code: "UPSTREAM_UNREACHABLE",
					message: "the API could not be reached",
					retryable: true,
				},
			},
			{ status: 502 },
		);
	}

	const responseHeaders = new Headers();
	for (const name of FORWARDED_RESPONSE_HEADERS) {
		const value = upstream.headers.get(name);
		if (value) responseHeaders.set(name, value);
	}
	return new Response(upstream.body, {
		status: upstream.status,
		headers: responseHeaders,
	});
}

export const GET = proxy;
export const HEAD = proxy;
export const OPTIONS = proxy;
export const POST = proxy;
export const PATCH = proxy;
export const PUT = proxy;
export const DELETE = proxy;
