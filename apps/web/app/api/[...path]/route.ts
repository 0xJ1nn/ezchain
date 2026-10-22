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
	"x-request-id",
] as const;

function apiBase(): string {
	return (process.env.EZCHAIN_API_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
}

async function proxy(request: NextRequest, context: RouteContext): Promise<Response> {
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
