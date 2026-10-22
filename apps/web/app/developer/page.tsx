import { AppShell } from "@/components/shell";
import { Fact, ModeBadge } from "@/components/ops";
import { Badge, ButtonLink, Panel } from "@/components/ui";
import { apiDocsHref, apiHostOnly, webMode } from "@/lib/runtime";

export const dynamic = "force-dynamic";

const DOCS = "https://github.com/0xJ1nn/ezchain/blob/main";

interface Endpoint {
	method: "GET" | "POST";
	path: string;
	auth: "read" | "bearer";
	purpose: string;
	used: string;
	missing?: boolean;
}

const ENDPOINTS: Endpoint[] = [
	{ method: "GET", path: "/api/v1/chains", auth: "read", purpose: "Chain registry and capabilities.", used: "/chains, /integrations" },
	{ method: "GET", path: "/api/v1/markets", auth: "read", purpose: "DexScreener discovery rows with source and coverage.", used: "/markets, /signals" },
	{ method: "GET", path: "/api/v1/markets/overview", auth: "read", purpose: "Per-chain coverage and observation age.", used: "/chains, /integrations" },
	{ method: "GET", path: "/api/v1/tokens/{chain}/{address}", auth: "read", purpose: "Token snapshot and pair ladder.", used: "/tokens/…" },
	{ method: "GET", path: "/api/v1/tokens/{chain}/{address}/candles", auth: "read", purpose: "Closed OHLCV candles for the token chart.", used: "/tokens/…" },
	{ method: "GET", path: "/api/v1/quotes", auth: "read", purpose: "Quote from the configured ladder; paper fill input.", used: "token order ticket" },
	{ method: "GET", path: "/api/v1/paper/account", auth: "read", purpose: "Point-in-time paper cash, equity, realized and unrealized.", used: "/, /positions, /performance, /wallets" },
	{ method: "GET", path: "/api/v1/paper/positions", auth: "read", purpose: "Server PositionView rows.", used: "/positions, /risk, /reports" },
	{ method: "GET", path: "/api/v1/paper/orders", auth: "read", purpose: "Order attempts, including rejected reason codes.", used: "/ledger, /performance, /reports, /alerts" },
	{ method: "GET", path: "/api/v1/paper/fills", auth: "read", purpose: "Execution fills with fees and quote kind.", used: "/ledger, /performance, /reports" },
	{ method: "GET", path: "/api/v1/paper/reconciliation", auth: "read", purpose: "Read-only journal, fill and order agreement checks.", used: "/operations, /alerts" },
	{ method: "GET", path: "/api/v1/strategies", auth: "read", purpose: "Installed strategy configurations.", used: "/strategies, /automations" },
	{ method: "GET", path: "/api/v1/strategies/{id}", auth: "read", purpose: "One strategy detail.", used: "/strategies/[id]" },
	{ method: "GET", path: "/api/v1/strategies/{id}/runs", auth: "read", purpose: "Engine decision log for a strategy.", used: "/strategies/[id]" },
	{ method: "POST", path: "/api/v1/paper/orders", auth: "bearer", purpose: "Submit a paper order through quote, risk, fill and journal.", used: "token order ticket" },
	{ method: "POST", path: "/api/v1/strategies", auth: "bearer", purpose: "Create a declarative or graph strategy.", used: "/strategies, builder" },
	{ method: "POST", path: "/api/v1/system/kill-switch", auth: "bearer", purpose: "Arm or release the persisted strategy switch.", used: "/risk" },
	{ method: "GET", path: "/api/v1/system/kill-switch", auth: "read", purpose: "Persisted switch state and reason.", used: "/risk, /alerts" },
	{ method: "GET", path: "/healthz", auth: "read", purpose: "Process liveness; does not touch the database.", used: "/operations" },
	{ method: "GET", path: "/health", auth: "read", purpose: "Database, data mode and scheduler flags.", used: "/automations" },
	{ method: "GET", path: "/readyz", auth: "read", purpose: "Database reachable and schema at the Alembic head.", used: "/operations" },
	{ method: "GET", path: "/metrics", auth: "read", purpose: "Prometheus text exposition.", used: "/operations" },
	{ method: "GET", path: "/api/v1/backtests/runs", auth: "read", purpose: "Backtest run list.", used: "/backtests", missing: true },
];

function authBadge(auth: Endpoint["auth"]) {
	return auth === "bearer" ? <Badge tone="warn">bearer</Badge> : <Badge tone="ghost">read</Badge>;
}

export default function DeveloperPage() {
	const mode = webMode();
	const docs = apiDocsHref();

	return (
		<AppShell active="developer">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">System / Developer</div>
					<h1>Developer</h1>
					<p className="sub">
						The routes this UI actually calls, their auth posture and the local commands. There
						are no private paths, embedded token or generated client bundle here.
					</p>
				</div>
				<div className="page-actions">
					<ModeBadge mode={mode} />
					<a className="btn" href={docs} rel="noreferrer">
						API docs
					</a>
					<a className="btn" href={`${DOCS}/docs/ARCHITECTURE.md`} rel="noreferrer">
						Architecture
					</a>
				</div>
			</header>

			<div className="grid cols-2">
				<Panel title="Connection" sub="server-side">
					<div className="stack-sm">
						<Fact label="API host" value={<span className="mono">{apiHostOnly()}</span>} />
						<Fact label="OpenAPI UI" value={<span className="mono">/docs on the API origin</span>} />
						<Fact label="Schema" value={<span className="mono">/openapi.json</span>} />
						<Fact label="Web data mode" value={<span className="mono">{mode}</span>} />
					</div>
					<p className="note warn mt-12">
						The API docs link points at the API process. Depending on your reverse proxy,{" "}
						<span className="mono">/docs</span> may not be publicly exposed; the web app does
						not re-host the schema.
					</p>
				</Panel>

				<Panel title="Local development" sub="repository commands">
					<div className="stack-sm small">
						<div className="between">
							<span className="muted">Web dev server</span>
							<span className="mono">pnpm --filter @ezchain/web dev</span>
						</div>
						<div className="between">
							<span className="muted">Lint</span>
							<span className="mono">pnpm --filter @ezchain/web lint</span>
						</div>
						<div className="between">
							<span className="muted">Types</span>
							<span className="mono">pnpm --filter @ezchain/web typecheck</span>
						</div>
						<div className="between">
							<span className="muted">Build</span>
							<span className="mono">pnpm --filter @ezchain/web build</span>
						</div>
						<div className="between">
							<span className="muted">Browser tests</span>
							<span className="mono">pnpm --filter @ezchain/web test</span>
						</div>
						<div className="between">
							<span className="muted">Local gate</span>
							<span className="mono">bash tools/ci.sh</span>
						</div>
					</div>
					<p className="note mt-12">
						Fixture mode is explicit: <span className="mono">EZCHAIN_WEB_MODE=fixture</span>.
						Unset means live. The browser tests run fixture mode and never require a database
						for page renders.
					</p>
				</Panel>
			</div>

			<Panel
				title="Endpoint overview"
				sub={`${ENDPOINTS.length} routes used or planned`}
				flush
				className="mt-16"
				footer="Paths are the public API surface. The web app calls reads on the server or through /api; mutations go through the same-origin Next proxy, which adds the bearer token."
			>
				<div className="table-wrap">
					<table className="table">
						<thead>
							<tr>
								<th>Method</th>
								<th>Path</th>
								<th>Auth</th>
								<th>Purpose</th>
								<th>Used by</th>
							</tr>
						</thead>
						<tbody>
							{ENDPOINTS.map((endpoint) => (
								<tr key={`${endpoint.method}:${endpoint.path}`}>
									<td>
										<Badge tone={endpoint.method === "POST" ? "warn" : "ghost"}>
											{endpoint.method}
										</Badge>
									</td>
									<td className="mono small">
										{endpoint.path}
										{endpoint.missing && (
											<span className="sub" style={{ display: "block" }}>
												not in this build
											</span>
										)}
									</td>
									<td>{authBadge(endpoint.auth)}</td>
									<td className="small muted">{endpoint.purpose}</td>
									<td className="small">{endpoint.used}</td>
								</tr>
							))}
						</tbody>
					</table>
				</div>
			</Panel>

			<div className="grid cols-3 mt-16">
				<Panel title="Strategy graph" sub="documented contract">
					<p className="small muted">
						Graph strategies are a typed <span className="mono">ezchain.graph/1</span>{" "}
						document. The builder edits and validates the document; the engine evaluates it.
					</p>
					<div className="row mt-12">
						<a className="btn sm" href={`${DOCS}/docs/STRATEGY-GRAPH.md`} rel="noreferrer">
							STRATEGY-GRAPH.md
						</a>
						<ButtonLink href="/strategies/builder" size="sm">
							Builder
						</ButtonLink>
					</div>
				</Panel>

				<Panel title="Strategy SDK" sub="plugin contract">
					<p className="small muted">
						Strategies are external plugins. The platform ships no strategy code; the SDK is
						the public contract.
					</p>
					<div className="row mt-12">
						<a className="btn sm" href={`${DOCS}/docs/STRATEGY-SDK.md`} rel="noreferrer">
							STRATEGY-SDK.md
						</a>
					</div>
				</Panel>

				<Panel title="Operations" sub="runbooks">
					<p className="small muted">
						Health, readiness, migrations and reconciliation are shown live on Operations.
						Backup and upgrade remain documented operator actions.
					</p>
					<div className="row mt-12">
						<a className="btn sm" href={`${DOCS}/docs/RUNBOOK.md`} rel="noreferrer">
							RUNBOOK.md
						</a>
						<ButtonLink href="/operations" size="sm">
							Operations
						</ButtonLink>
					</div>
				</Panel>
			</div>
		</AppShell>
	);
}
