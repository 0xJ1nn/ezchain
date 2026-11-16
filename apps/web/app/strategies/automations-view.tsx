import Link from "next/link";
import { EmptyState, FixtureBadge, Unavailable } from "@/components/ops";
import { ProtectionList } from "@/components/protection-list";
import { Badge, ButtonLink, Panel } from "@/components/ui";
import { AutomationsTable } from "@/components/automations-table";
import { getAllStrategyRuns, getStrategies, webMode } from "@/lib/paper-api";
import { getProtectionArms } from "@/lib/protection-api";
import { getServiceHealth } from "@/lib/system-api";

// The scheduler view of the one strategy surface. Kept as its own module because it reads
// the health probe and the protection arms, which the strategy list does not need.

export async function AutomationsView({ demo }: { demo?: string } = {}) {
	const mode = webMode();
	const [requestedStrategies, runsResult, healthResult, armsResult] = await Promise.all([
		getStrategies(),
		getAllStrategyRuns(),
		getServiceHealth(),
		getProtectionArms(),
	]);
	// Fixture-only state demos, the same convention the positions page uses. A live
	// deployment never hides a real failure behind a demo flag.
	const strategiesResult =
		mode === "fixture" && demo === "error"
			? {
					ok: false as const,
					mode,
					error: { code: "FIXTURE_DEMO_AUTOMATION_ERROR", message: "Fixture demo: the strategy read failed." },
				}
			: mode === "fixture" && demo === "empty"
				? { ok: true as const, mode, data: [] }
				: requestedStrategies;
	const strategies = strategiesResult.ok ? strategiesResult.data : [];
	const runs = runsResult.ok ? runsResult.data : [];
	const active = strategies.filter((strategy) => strategy.status === "active");
	const paused = strategies.filter((strategy) => strategy.status === "paused");
	const health = healthResult.ok ? healthResult.data : null;
	const healthError = healthResult.ok ? null : healthResult.error;

	return (
		<>
			{!strategiesResult.ok && (
				<div className="mb-12">
					<Unavailable
						title="Automations unavailable"
						code={strategiesResult.error.code}
						message={strategiesResult.error.message}
						action={
							<Link className="small" href="/strategies?view=automations">
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
								<AutomationsTable
									mode={mode}
									initialStrategies={strategies}
									initialRuns={runs}
								/>
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
						<div
							className="table-wrap"
							tabIndex={0}
							role="region"
							aria-label="Automation vs strategy table"
						>
							<table className="table compact">
								<caption className="sr-only">Automation vs strategy</caption>
								<thead>
									<tr>
										<th scope="col">Concept</th>
										<th scope="col">In this build</th>
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
											Per-strategy <span className="mono">interval_minutes</span>; the
											engine decides whether a window elapsed.
										</td>
									</tr>
									<tr>
										<td>Run log</td>
										<td>
											The last decisions are in the table above; the full recorded log is on
											each <Link href="/strategies">strategy detail page</Link>. Every row is
											the engine&apos;s decision, not a browser timer.
										</td>
									</tr>
									<tr>
										<td>Trigger / action builder</td>
										<td>
											The <Link href="/strategies/builder">visual builder</Link> declares a
											typed graph: tick or candle nodes are the trigger, logic nodes are the
											conditions, action nodes are the actions, and the chain and token
											params are the scope. Its dry run is labelled <span className="mono">DRY_RUN</span>{" "}
											and places no order; there is no generic trigger-action automation object
											in this API and no simulated 30-day window is claimed here.
										</td>
									</tr>
								</tbody>
							</table>
						</div>
					</Panel>

					<ProtectionList mode={mode} initialArms={armsResult.ok ? armsResult.data : []} />
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
									<strong>
										{health.engine_inline ? "Inline scheduler." : "Separate scheduler."}
									</strong>{" "}
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
									<span className="title">Operator kill switch</span>
									<span className="sub">
										Armed, new risk-increasing orders are rejected before a quote;
										reduce-only exits still settle.
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
		</>
	);
}
