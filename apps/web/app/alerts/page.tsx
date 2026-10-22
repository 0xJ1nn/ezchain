import Link from "next/link";
import { AppShell } from "@/components/shell";
import { EmptyState, FixtureBadge, ModeBadge, Unavailable } from "@/components/ops";
import { Badge, ButtonLink, Panel, Stat } from "@/components/ui";
import { getPaperAlerts } from "@/lib/account-api";
import { getKillSwitch, getPaperSnapshot, getReconciliation } from "@/lib/paper-api";

export const dynamic = "force-dynamic";

type Severity = "critical" | "warning" | "info";

interface DerivedAlert {
	id: string;
	severity: Severity;
	title: string;
	detail: string;
	source: string;
	href: string;
}

function tone(severity: Severity): "err" | "warn" | "ghost" {
	if (severity === "critical") return "err";
	if (severity === "warning") return "warn";
	return "ghost";
}

export default async function AlertsPage() {
	const [snapshot, alertsResult, reconciliationResult, switchResult] = await Promise.all([
		getPaperSnapshot(),
		getPaperAlerts(),
		getReconciliation(),
		getKillSwitch(),
	]);
	const mode = snapshot.mode;
	const serverAlerts = alertsResult.ok ? alertsResult.data.alerts : [];
	const reconciliation = reconciliationResult.ok ? reconciliationResult.data : null;
	const reconciliationError = reconciliationResult.ok ? null : reconciliationResult.error;
	const killSwitch = switchResult.ok ? switchResult.data : null;

	function hrefFor(code: string, resource: string | null): string {
		if (code.startsWith("mark.")) {
			const [chain, token] = (resource ?? "").split(":");
			return chain && token ? `/tokens/${chain}/${token}` : "/positions";
		}
		if (code.startsWith("orders.rejected")) return "/ledger";
		if (code.startsWith("kill_switch")) return "/risk";
		if (code.startsWith("reconciliation") || code.startsWith("cash.") || code.startsWith("positions.")) return "/operations";
		return "/alerts";
	}

	const alerts: DerivedAlert[] = serverAlerts.map((alert) => ({
		id: alert.id,
		severity: alert.severity,
		title: alert.title,
		detail: alert.detail,
		source: alert.resource ?? `/api/v1/paper/alerts · ${alert.code}`,
		href: hrefFor(alert.code, alert.resource),
	}));

	const critical = alerts.filter((alert) => alert.severity === "critical");
	const warnings = alerts.filter((alert) => alert.severity === "warning");
	const info = alerts.filter((alert) => alert.severity === "info");
	const error = !snapshot.positions.ok
		? snapshot.positions.error
		: !alertsResult.ok
			? { code: alertsResult.code, message: alertsResult.message }
			: null;

	return (
		<AppShell active="alerts">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">Protect / Alerts</div>
					<h1>Alerts</h1>
					<p className="sub">
						Alerts are server-derived from the paper read models: reconciliation, stale or
						unknown marks, rejected orders and the persisted kill switch. There is no
						notification channel in this build, so nothing is emailed or paged.
					</p>
				</div>
				<div className="page-actions">
					<ModeBadge mode={mode} />
					<Link className="btn" href="/risk">
						Risk
					</Link>
					<Link className="btn" href="/operations">
						Operations
					</Link>
				</div>
			</header>

			{error && (
				<div className="mb-12">
					<Unavailable
						title="Alert inputs incomplete"
						code={error.code}
						message={error.message}
						action={
							<Link className="small" href="/alerts">
								Retry →
							</Link>
						}
					>
						Alerts that depend on missing data are not emitted. Nothing is guessed.
					</Unavailable>
				</div>
			)}

			<section className="kpi-band mb-12" aria-label="Alert summary">
				<Stat
					label="Critical"
					value={String(critical.length)}
					tone={critical.length > 0 ? "err" : "ok"}
					meta="reconciliation · kill switch"
				/>
				<Stat
					label="Warnings"
					value={String(warnings.length)}
					tone={warnings.length > 0 ? "err" : "ok"}
					meta="marks · rejections"
				/>
				<Stat label="Info" value={String(info.length)} meta="derived notices" />
				<Stat
					label="Delivery"
					value="—"
					meta="no notification channels in this build"
				/>
			</section>

			{alerts.length === 0 ? (
				<Panel title="No derived alerts" sub="honest empty state" flush>
					<div className="panel-body">
						<EmptyState
							title="Nothing currently crosses a derived alert condition"
							links={[
								{ href: "/risk", label: "Risk" },
								{ href: "/operations", label: "Operations" },
							]}
						>
							That means reconciliation passed or was unavailable, every known mark is fresh,
							no recent order was rejected and the kill switch is released. It is not a claim
							that the system is safe; it is the current result of the checks below.
						</EmptyState>
					</div>
				</Panel>
			) : (
				<Panel
					title="Derived alerts"
					sub={`${alerts.length} conditions`}
					action={mode === "fixture" ? <FixtureBadge /> : <Badge tone="ghost">LIVE READ MODELS</Badge>}
					flush
					footer="Each alert names the read model that produced it. Severity is assigned by this view; the API does not return an alert object."
				>
					<div className="list" style={{ padding: "0 12px" }}>
						{alerts.map((alert) => (
							<div className="list-row" key={alert.id}>
								<Badge tone={tone(alert.severity)}>{alert.severity.toUpperCase()}</Badge>
								<span className="grow">
									<span className="title">{alert.title}</span>
									<span className="sub">{alert.detail}</span>
									<span className="micro">{alert.source}</span>
								</span>
								<Link className="btn sm" href={alert.href}>
									Open
								</Link>
							</div>
						))}
					</div>
				</Panel>
			)}

			<div className="grid cols-2 mt-16">
				<Panel
					title="Reconciliation"
					sub={reconciliation ? `${reconciliation.checks.length} checks` : "not available"}
					action={
						reconciliation ? (
							<Badge tone={reconciliation.ok ? "ok" : "err"}>
								{reconciliation.ok ? "PASS" : "FAIL"}
							</Badge>
						) : (
							<Badge tone="warn">NOT PROBED</Badge>
						)
					}
					flush
				>
					{reconciliation ? (
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
									{reconciliation.checks.map((check) => (
										<tr key={check.id}>
											<td className="mono small">{check.id}</td>
											<td>
												<Badge tone={check.ok ? "ok" : "err"}>{check.ok ? "ok" : "fail"}</Badge>
											</td>
											<td className="small muted">{check.detail}</td>
										</tr>
									))}
								</tbody>
							</table>
						</div>
					) : (
						<div className="panel-body">
							<div className="note warn">{reconciliationError?.message}</div>
							<p className="small muted mt-8">
								A preview does not prove the journal, so this panel emits no pass and no
								alert.
							</p>
						</div>
					)}
				</Panel>

				<Panel
					title="Delivery and rules"
					sub="not built"
					action={<Badge tone="warn">NO CHANNELS</Badge>}
				>
					<p className="small muted">
						This branch has no alert rule object, channel configuration, quiet hours, dedupe
						key or delivery log. Adding those would be a server-side feature, not a browser
						one. The conditions above are recomputed on every page load from the read models.
					</p>
					<div className="row mt-12">
						<ButtonLink href="/settings" size="sm">
							Runtime settings
						</ButtonLink>
						<ButtonLink href="/developer" size="sm">
							API overview
						</ButtonLink>
					</div>
				</Panel>
			</div>

			{!killSwitch && (
				<Panel title="Kill switch state" sub="unavailable" className="mt-16">
					<div className="note warn">
						{switchResult.ok ? "No state returned." : switchResult.error.message}
					</div>
				</Panel>
			)}
		</AppShell>
	);
}
