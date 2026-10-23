// Lightweight liveness endpoint for the desktop supervisor. It must not render a page or
// touch the API; the watchdog polls it while the UI is busy fetching market data.
export const dynamic = "force-static";

export function GET() {
	return Response.json({ status: "ok" });
}
