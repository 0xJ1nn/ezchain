import Link from "next/link";
import { AppShell } from "@/components/shell";
import { EmptyState, FixtureBadge, ModeBadge, Unavailable } from "@/components/ops";
import { Badge, ButtonLink, Panel } from "@/components/ui";
import { ageLabel } from "@/lib/live-format";
import { getStrategies } from "@/lib/paper-api";
import { getServiceHealth } from "@/lib/system-api";
import { strategyKindMeta } from "@/lib/strategy-fixtures";

export const dynamic = "force-dynamic";

function intervalLabel(params: Record<string, string | number>) {
	const raw = params.interval_minutes;
	const minutes = typeof raw === "number" ? raw : Number(raw);
	if (!Number.isFinite(minutes) || minutes <= 0) return "—";
	if (minutes >= 1440 && minutes % 1440 === 0) return `${minutes / 1440}d`;
	if (minutes >= 60 && minutes % 60 === 0) return `${minutes / 60}h`;
	return `${minutes}m`;
}

function stamp(iso: string | null) {
	if (!iso) return "never";
	return `${iso.slice(0, 10)} ${iso.slice(11, 19)}Z`;
}

export default async function AutomationsPage() {
	const [strategiesResult, healthResult] = await Promise.all([
		getStrategies(),
		getServiceHealth(),
	]);
	const mode = strategiesResult.mode;
	const strategies = strategiesResult.ok ? strategiesResult.data : [];
	const active = strategies.filter((strategy) => strategy.status === "active");
	const paused = strategies.filter((strategy) => strategy.status === "paused");
	const health = healthResult.ok ? healthResult.data : null;
	const healthError = healthResult.ok ? null : healthResult.error;

	return (
		<AppShell active="automations">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">Automate / Automations</div>
					<h1>Automations</h1>
					<p className="sub">
						Installed strategies are the automations this build actually schedules. Each row
						shows its persisted status, its configured interval and the last run timestamp the
						API returned. There is no separate automation builder in this branch.
					</p>
				</div>
				<div className="page-actions">
					<ModeBadge mode={mode} />
					<Link className="btn" href="/strategies">
						Strategies
					</Link>
					<Link className="btn" href="/strategies/builder">
						New graph strategy
					</Link>
				</div>
			</header>

			{!strategiesResult.ok && (
				<div className="mb-12">
					<Unavailable
						title="Automations unavailable"
						code={strategiesResult.error.code}
						message={strategiesResult.error.message}
						action={
							<Link className="small" href="/automations">
								Retry →
							</Link>
						}
					>
						No automation is invented when the strategy list cannot be read.
					</Unavailable>
				</div>
			)}

			<section className="kpi-band mb-12" aria-label="Automation summary">
				<div className="stat">
					<div className="label">Installed</div>
					<div className="value sm">{strategiesResult.ok ? strategies.length : "—"}</div>
					<div className="meta">strategies = automations</div>
				</div>
				<div className="stat">
					<div className="label">Active</div>
					<div className="value sm ok">{strategiesResult.ok ? active.length : "—"}</div>
					<div className="meta">scheduler may evaluate</div>
				</div>
				<div className="stat">
					<div className="label">Paused</div>
					<div className="value sm">{strategiesResult.ok ? paused.length : "—"}</div>
					<div className="meta">no new decision</div>
				</div>
				<div className="stat">
					<div className="label">Scheduler</div>
					<div className="value sm">
						{healthResult.ok ? (health?.engine_inline ? "INLINE" : "SEPARATE") : "—"}
					</div>
					<div className="meta">
						{healthResult.ok
							? health?.engine_inline
								? "engine_inline = true"
								: "engine_inline = false"
							: "not probed"}
					</div>
				</div>
			</section>

			<div className="split">
				<div className="stack">
					<Panel
						title="Scheduled strategies"
						sub={
							strategiesResult.ok
								? `${strategies.length} installed · ${active.length} active`
								: "unavailable"
						}
						action={mode === "fixture" ? <FixtureBadge /> : <Badge tone="ghost">LIVE API</Badge>}
						flush
						footer="Intervals are the strategy params the API stores. Last run is the engine's recorded timestamp, not a browser timer."
					>
						{strategiesResult.ok ? (
							strategies.length > 0 ? (
								<div className="table-wrap">
									<table className="table">
										<thead>
											<tr>
												<th>Strategy</th>
												<th>Status</th>
												<th>Kind</th>
												<th>Chain</th>
												<th>Interval</th>
												<th>Last run</th>
												<th />
											</tr>
										</thead>
										<tbody>
											{strategies.map((strategy) => (
												<tr key={strategy.id}>
													<td>
														<Link href={`/strategies/${strategy.id}`}>
															<span className="strong">{strategy.name}</span>
														</Link>
														<span className="sub" style={{ display: "block" }}>
															{strategy.token_symbol} · {strategy.id}
														</span>
													</td>
													<td>
														<Badge tone={strategy.status === "active" ? "ok" : "warn"}>
															{strategy.status}
														</Badge>
													</td>
													<td className="small">
														{strategyKindMeta[strategy.kind]?.label ?? strategy.kind}
													</td>
													<td>{strategy.chain}</td>
													<td className="mono small">{intervalLabel(strategy.params)}</td>
													<td className="small">
														{stamp(strategy.last_run_at)}
														<span className="sub" style={{ display: "block" }}>
															{strategy.last_run_at ? `${ageLabel(strategy.last_run_at)} ago` : "never"}
														</span>
													</td>
													<td className="right">
														<Link className="btn sm" href={`/strategies/${strategy.id}`}>
															Open
														</Link>
													</td>
												</tr>
											))}
										</tbody>
									</table>
								</div>
							) : (
								<div className="panel-body">
									<EmptyState
										title="No strategies installed"
										links={[
											{ href: "/strategies", label: "Strategies", primary: true },
											{ href: "/strategies/builder", label: "Open builder" },
										]}
									>
										There is nothing to schedule because the API returned no strategies.
										Automations are not separate objects in this build.
									</EmptyState>
								</div>
							)
						) : (
							<div className="panel-body">
								<div className="note err">{strategiesResult.error.message}</div>
							</div>
						)}
					</Panel>

					<Panel title="Automation vs strategy" sub="the actual model" flush>
						<div className="table-wrap">
							<table className="table compact">
								<thead>
									<tr>
										<th>Concept</th>
										<th>In this build</th>
									</tr>
								</thead>
								<tbody>
									<tr>
										<td>Strategy</td>
										<td>
											A declarative config (momentum, dip, DCA, threshold or graph) stored
											by the API and evaluated by the engine.
										</td>
									</tr>
									<tr>
										<td>Schedule</td>
										<td>
											Per-strategy <span className="mono">interval_minutes</span>; the engine
											decides whether a window elapsed.
										</td>
									</tr>
									<tr>
										<td>Run log</td>
										<td>Available on each strategy detail page from /strategies/[id]/runs.</td>
									</tr>
									<tr>
										<td>Trigger / action builder</td>
										<td>
											<span className="warn">Not built.</span> No generic trigger-action
											automation object exists in this API.
										</td>
									</tr>
								</tbody>
							</table>
						</div>
					</Panel>
				</div>

				<div className="stack">
					<Panel
						title="Scheduler state"
						sub={healthResult.ok ? "from /health" : "not probed"}
						action={
							healthResult.ok ? (
								<Badge tone={health?.db === "ok" ? "ok" : "err"}>{health?.status}</Badge>
							) : (
								<Badge tone="warn">FIXTURE</Badge>
							)
						}
					>
						{healthResult.ok && health ? (
							<div className="stack-sm">
								<div className="between small">
									<span className="muted">Database</span>
									<span className="mono">{health.db}</span>
								</div>
								<div className="between small">
									<span className="muted">Data mode</span>
									<span className="mono">{health.mode}</span>
								</div>
								<div className="between small">
									<span className="muted">Engine inline</span>
									<span className="mono">{String(health.engine_inline)}</span>
								</div>
								<div className="between small">
									<span className="muted">Market scan inline</span>
									<span className="mono">{String(health.market_scan_inline)}</span>
								</div>
								<div className="between small">
									<span className="muted">Market monitor</span>
									<span className="mono">{String(health.market_monitor)}</span>
								</div>
								<div className="between small">
									<span className="muted">API version</span>
									<span className="mono">{health.version}</span>
								</div>
								<p className="note mt-12">
									<strong>{health.engine_inline ? "Inline scheduler." : "Separate scheduler."}</strong>{" "}
									{health.engine_inline
										? "The engine loop runs in the API process; a restart pauses evaluation until the process is back."
										: "The API reports engine_inline=false; the scheduler process is separate and this UI cannot see it from /health alone."}
								</p>
							</div>
						) : (
							<div>
								<div className="note warn">
									{healthError?.code === "FIXTURE_NOT_PROBED"
										? "Fixture mode does not probe /health. Scheduler facts are unknown here rather than assumed healthy."
										: healthError?.message}
								</div>
								<p className="small muted mt-8">
									The strategies above still show their persisted status and last run. Set{" "}
									<span className="mono">EZCHAIN_WEB_MODE=live</span> to read scheduler state.
								</p>
							</div>
						)}
					</Panel>

					<Panel title="Guardrails" sub="what actually gates a run">
						<div className="list">
							<div className="list-row">
								<Badge tone="ghost">RISK</Badge>
								<span className="grow">
									<span className="title">Server-side order checks</span>
									<span className="sub">
										Price, liquidity, pair age, quote freshness and position caps fail closed.
									</span>
								</span>
							</div>
							<div className="list-row">
								<Badge tone="ghost">SWITCH</Badge>
								<span className="grow">
									<span className="title">Strategy-scoped kill switch</span>
									<span className="sub">
										Armed, strategy-attributed orders are rejected before a quote.
									</span>
								</span>
								<ButtonLink href="/risk" size="sm">
									Risk
								</ButtonLink>
							</div>
							<div className="list-row">
								<Badge tone="ghost">PAPER</Badge>
								<span className="grow">
									<span className="title">No live execution</span>
									<span className="sub">
										Every run in this build is paper; there is no signing path.
									</span>
								</span>
							</div>
						</div>
					</Panel>
				</div>
			</div>
		</AppShell>
	);
}
