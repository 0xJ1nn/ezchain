import Link from "next/link";
import { AppShell } from "@/components/shell";
import { FixtureBadge, ModeBadge } from "@/components/ops";
import { Badge, ButtonLink, Panel, Stat } from "@/components/ui";
import { getReconciliation } from "@/lib/paper-api";
import {
	getLiveness,
	getMetrics,
	getReadiness,
	getServiceHealth,
} from "@/lib/system-api";
import { webMode } from "@/lib/runtime";

export const dynamic = "force-dynamic";

function toneForStatus(status: string | undefined): "ok" | "warn" | "err" | undefined {
	if (!status) return undefined;
	const normalized = status.toLowerCase();
	if (normalized === "ok" || normalized === "ready") return "ok";
	if (normalized === "degraded" || normalized === "not_ready") return "err";
	return undefined;
}

export default async function OperationsPage() {
	const mode = webMode();
	const [liveness, health, readiness, metrics, reconciliation] = await Promise.all([
		getLiveness(),
		getServiceHealth(),
		getReadiness(),
		getMetrics(),
		getReconciliation(),
	]);
	const healthData = health.ok ? health.data : null;
	const healthError = health.ok ? null : health.error;
	const readinessData = readiness.ok ? readiness.data : null;
	const readinessError = readiness.ok ? null : readiness.error;
	const metricsText = metrics.ok ? metrics.data.text : null;
	const metricsError = metrics.ok ? null : metrics.error;
	const metricsLines = metricsText ? metricsText.split("\n").slice(0, 60) : [];
	const metricsTruncated = metricsText ? metricsText.split("\n").length > 60 : false;

	return (
		<AppShell active="operations">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">System / Operations</div>
					<h1>Operations</h1>
					<p className="sub">
						Process health, readiness, migration state, Prometheus metrics and the paper
						reconciliation report, read on the server. Fixture mode does not probe a running
						API and says so.
					</p>
				</div>
				<div className="page-actions">
					<ModeBadge mode={mode} />
					<Link className="btn" href="/developer">
						Endpoints
					</Link>
					<Link className="btn" href="/risk">
						Risk
					</Link>
				</div>
			</header>

			<section className="kpi-band mb-12" aria-label="Service probes">
				<Stat
					label="Liveness /healthz"
					value={liveness.ok ? liveness.data.status : "—"}
					tone={liveness.ok ? "ok" : undefined}
					meta={liveness.ok ? "process up · no DB call" : liveness.error.code}
				/>
				<Stat
					label="Health /health"
					value={healthData ? healthData.status : "—"}
					tone={
						healthData
							? healthData.status === "ok"
								? "ok"
								: "err"
							: undefined
					}
					meta={healthData ? `db ${healthData.db}` : healthError?.code}
				/>
				<Stat
					label="Readiness /readyz"
					value={readinessData ? readinessData.status : "—"}
					tone={readinessData ? toneForStatus(readinessData.status) : undefined}
					meta={
						readinessData
							? `migration ${readinessData.migration}`
							: readinessError?.code
					}
				/>
				<Stat
					label="Metrics /metrics"
					value={metrics.ok ? "TEXT" : "—"}
					meta={metrics.ok ? `${metricsText?.split("\n").length ?? 0} lines` : metricsError?.code}
				/>
				<Stat
					label="Reconciliation"
					value={reconciliation.ok ? (reconciliation.data.ok ? "PASS" : "FAIL") : "—"}
					tone={
						reconciliation.ok
							? reconciliation.data.ok
								? "ok"
								: "err"
							: undefined
					}
					meta={reconciliation.ok ? "journal vs fills vs orders" : reconciliation.error.code}
				/>
			</section>

			<div className="split">
				<div className="stack">
					<Panel
						title="Service health"
						sub={health.ok ? "from /health" : "not probed"}
						action={
							healthData ? (
								<Badge tone={toneForStatus(healthData.status)}>{healthData.status}</Badge>
							) : (
								<Badge tone="warn">FIXTURE</Badge>
							)
						}
					>
						{healthData ? (
							<div className="stack-sm">
								<div className="between small">
									<span className="muted">Database</span>
									<span className="mono">{healthData.db}</span>
								</div>
								<div className="between small">
									<span className="muted">Data mode</span>
									<span className="mono">{healthData.mode}</span>
								</div>
								<div className="between small">
									<span className="muted">Engine inline</span>
									<span className="mono">{String(healthData.engine_inline)}</span>
								</div>
								<div className="between small">
									<span className="muted">Market scan inline</span>
									<span className="mono">{String(healthData.market_scan_inline)}</span>
								</div>
								<div className="between small">
									<span className="muted">Market monitor</span>
									<span className="mono">{String(healthData.market_monitor)}</span>
								</div>
								<div className="between small">
									<span className="muted">Version</span>
									<span className="mono">{healthData.version}</span>
								</div>
							</div>
						) : (
							<div className="note warn">{healthError?.message}</div>
						)}
					</Panel>

					<Panel
						title="Readiness and migration"
						sub={readiness.ok ? "from /readyz" : "not probed"}
						action={
							readinessData ? (
								<Badge tone={toneForStatus(readinessData.status)}>
									{readinessData.status}
								</Badge>
							) : undefined
						}
					>
						{readinessData ? (
							<div className="stack-sm">
								<div className="between small">
									<span className="muted">Database</span>
									<span className="mono">{readinessData.db}</span>
								</div>
								<div className="between small">
									<span className="muted">Migration state</span>
									<span className="mono">{readinessData.migration}</span>
								</div>
								<div className="between small">
									<span className="muted">Database revision</span>
									<span className="mono">{readinessData.revision ?? "—"}</span>
								</div>
								<div className="between small">
									<span className="muted">Script head</span>
									<span className="mono">{readinessData.head ?? "—"}</span>
								</div>
								<p className="note mt-8">
									{readinessData.status === "ready"
										? "The schema is at the Alembic head. Migrations are forward-only."
										: "Readiness is not ready. Do not treat writes as safe: the API reports the schema is not at head or the database is unreachable."}
								</p>
							</div>
						) : (
							<div className="note warn">{readinessError?.message}</div>
						)}
					</Panel>

					<Panel
						title="Prometheus metrics"
						sub={metrics.ok ? "text exposition" : "not probed"}
						action={metrics.ok ? <Badge tone="ok">TEXT</Badge> : <Badge tone="warn">—</Badge>}
					>
						{metrics.ok ? (
							<>
								<pre
									className="mono"
									style={{
										margin: 0,
										padding: "10px 12px",
										background: "var(--surface-2)",
										border: "1px solid var(--line)",
										borderRadius: "var(--r-sm)",
										fontSize: 10.5,
										lineHeight: 1.5,
										overflow: "auto",
										maxHeight: 360,
										whiteSpace: "pre-wrap",
									}}
								>
									{metricsLines.join("\n")}
								</pre>
								{metricsTruncated && (
									<p className="micro mt-8">Showing the first 60 lines.</p>
								)}
							</>
						) : (
							<div className="note warn">{metricsError?.message}</div>
						)}
					</Panel>
				</div>

				<div className="stack">
					<Panel
						title="Reconciliation"
						sub={reconciliation.ok ? "read-only database proof" : "not probed"}
						action={
							reconciliation.ok ? (
								<Badge tone={reconciliation.data.ok ? "ok" : "err"}>
									{reconciliation.data.ok ? "PASS" : "FAIL"}
								</Badge>
							) : (
								<Badge tone="warn">FIXTURE</Badge>
							)
						}
						flush
						footer={
							reconciliation.ok ? (
								<>
									As of <span className="mono">{reconciliation.data.as_of}</span>
								</>
							) : undefined
						}
					>
						{reconciliation.ok ? (
							<>
								<div className="table-wrap">
									<table className="table compact">
										<thead>
											<tr>
												<th>Check</th>
												<th>Result</th>
												<th>Detail</th>
											</tr>
										</thead>
										<tbody>
											{reconciliation.data.checks.map((check) => (
												<tr key={check.id}>
													<td className="mono small">{check.id}</td>
													<td>
														<Badge tone={check.ok ? "ok" : "err"}>
															{check.ok ? "ok" : "fail"}
														</Badge>
													</td>
													<td className="small muted">{check.detail}</td>
												</tr>
											))}
										</tbody>
									</table>
								</div>
								<div className="panel-body">
									<div className="grid cols-2 small">
										<div className="between">
											<span className="muted">Genesis</span>
											<span className="mono">{reconciliation.data.totals.genesis_usd}</span>
										</div>
										<div className="between">
											<span className="muted">Cash</span>
											<span className="mono">{reconciliation.data.totals.cash_usd}</span>
										</div>
										<div className="between">
											<span className="muted">Fills</span>
											<span className="mono">{reconciliation.data.totals.fills}</span>
										</div>
										<div className="between">
											<span className="muted">Open positions</span>
											<span className="mono">{reconciliation.data.totals.open_positions}</span>
										</div>
										<div className="between">
											<span className="muted">Orphan fills</span>
											<span className="mono">{reconciliation.data.totals.orphan_fills}</span>
										</div>
										<div className="between">
											<span className="muted">Negative positions</span>
											<span className="mono">
												{reconciliation.data.totals.negative_positions}
											</span>
										</div>
									</div>
								</div>
							</>
						) : (
							<div className="panel-body">
								<div className="note warn">{reconciliation.error.message}</div>
								<p className="small muted mt-8">
									A preview cannot prove the journal, so no pass is claimed.
								</p>
							</div>
						)}
					</Panel>

					<Panel title="Operator actions" sub="not in this UI">
						<div className="note warn">
							No restart, backup, restore, migration or queue control is exposed here.
							Backups and upgrades are documented operator actions; a browser button would
							need a server API that does not exist in this build.
						</div>
						<div className="row mt-12">
							<a
								className="btn sm"
								href="https://github.com/0xJ1nn/ezchain/blob/main/docs/RUNBOOK.md"
								rel="noreferrer"
							>
								RUNBOOK.md
							</a>
							<ButtonLink href="/settings" size="sm">
								Settings
							</ButtonLink>
						</div>
					</Panel>

					<Panel title="Probe visibility" sub="what is not covered">
						<div className="small muted">
							<div className="between">
								<span>API latency</span>
								<span className="mono">metrics</span>
							</div>
							<div className="between">
								<span>Queue depth</span>
								<span className="mono">not exposed</span>
							</div>
							<div className="between">
								<span>Backup age</span>
								<span className="mono">not exposed</span>
							</div>
							<div className="between">
								<span>Scheduler last tick</span>
								<span className="mono">not exposed</span>
							</div>
						</div>
						{mode === "fixture" && (
							<p className="note mt-12">
								<FixtureBadge /> Fixture mode does not call the API root endpoints. Live
								mode shows the real probe results.
							</p>
						)}
					</Panel>
				</div>
			</div>
		</AppShell>
	);
}
