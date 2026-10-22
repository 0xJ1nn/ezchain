import Link from "next/link";
import { notFound } from "next/navigation";
import { AppShell } from "@/components/shell";
import { Badge, ButtonLink, Panel } from "@/components/ui";
import { usd, signedUsd } from "@/lib/format";
import { getStrategy, getStrategyRuns, webMode } from "@/lib/paper-api";
import {
	strategyKindMeta,
	strategyParamFields,
	type StrategyParams,
} from "@/lib/strategy-fixtures";
import styles from "../strategies.module.css";

export const dynamic = "force-dynamic";

function price(value: number) {
	if (value !== 0 && Math.abs(value) < 0.01) return `$${value.toPrecision(4)}`;
	return usd(value);
}

function pnl(value: string | null | undefined) {
	if (value === null || value === undefined) return "—";
	const amount = Number(value);
	if (!Number.isFinite(amount)) return "—";
	return amount === 0 ? usd(0) : signedUsd(amount);
}

function paramLabel(key: string) {
	return strategyParamFields.find((field) => field.name === key)?.label ?? key;
}

function paramValue(key: string, value: string | number) {
	if (key.endsWith("_usd")) {
		const amount = Number(value);
		if (Number.isFinite(amount)) return price(amount);
	}
	if (key.endsWith("_pct")) return `${value}%`;
	if (key === "interval_minutes") return `${value} minutes`;
	return String(value);
}

function rows(params: StrategyParams) {
	return Object.entries(params).sort(([a], [b]) => {
		const ai = strategyParamFields.findIndex((field) => field.name === a);
		const bi = strategyParamFields.findIndex((field) => field.name === b);
		return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi);
	});
}

export default async function StrategyDetailPage({
	params,
}: {
	params: Promise<{ id: string }>;
}) {
	const { id } = await params;
	const mode = webMode();
	const [strategy, runs] = await Promise.all([getStrategy(id), getStrategyRuns(id)]);

	if (!strategy.ok) {
		if (strategy.error.code === "STRATEGY_NOT_FOUND") notFound();
		return (
			<AppShell active="strategies">
				<header className="page-head">
					<div className="titles">
						<div className="crumbs">
							<Link href="/strategies">Strategies</Link> / Detail
						</div>
						<h1>Strategy unavailable</h1>
					</div>
				</header>
				<Panel title="Nothing is being guessed" action={<Badge tone="err">{strategy.error.code}</Badge>}>
					<div className={styles.empty}>
						<strong>{strategy.error.message}</strong>
						The detail view does not render a fabricated strategy when the request fails.
						<div className="row mt-12" style={{ justifyContent: "center" }}>
							<Link className="btn" href={`/strategies/${id}`}>
								Retry
							</Link>
							<Link className="btn" href="/strategies">
								All strategies
							</Link>
						</div>
					</div>
				</Panel>
			</AppShell>
		);
	}

	const row = strategy.data;
	const meta = strategyKindMeta[row.kind];
	const paused = row.status === "paused";

	return (
		<AppShell active="strategies">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">
						<Link href="/strategies">Strategies</Link> / {row.name}
					</div>
					<h1>{row.name}</h1>
					<p className="sub">
						{row.token_symbol} on {row.chain} · {meta.label} · {meta.description} Declarative
						configuration only; the engine evaluates it and the paper order service decides
						whether an intent becomes an order.
					</p>
				</div>
				<div className="page-actions">
					<Badge tone={mode === "fixture" ? "ghost" : "info"}>
						{mode === "fixture" ? "FIXTURE" : "LIVE API"}
					</Badge>
					<Badge tone={paused ? "warn" : "ok"}>{paused ? "paused" : "active"}</Badge>
					<Badge tone="ghost">PAPER</Badge>
					{row.kind === "graph" && (
						<ButtonLink href={`/strategies/${row.id}/builder`} size="sm">
							Open builder
						</ButtonLink>
					)}
					<ButtonLink href="/strategies" size="sm">
						All strategies
					</ButtonLink>
				</div>
			</header>

			<section className="kpi-band mb-12" aria-label="Strategy summary">
				<div className="stat">
					<div className="label">Kind</div>
					<div className="value sm">{meta.label}</div>
					<div className="meta">{meta.rule}</div>
				</div>
				<div className="stat">
					<div className="label">Status</div>
					<div className={`value sm ${paused ? "warn" : "ok"}`}>
						{paused ? "Paused" : "Active"}
					</div>
					<div className="meta">pause/resume from the list</div>
				</div>
				<div className="stat">
					<div className="label">Last run</div>
					<div className="value sm">{row.last_run_at ? row.last_run_at.slice(0, 16).replace("T", " ") : "never"}</div>
					<div className="meta">UTC · one decision per interval</div>
				</div>
				<div className="stat">
					<div className="label">
						PnL · 7d <Badge tone="ghost">PAPER</Badge>
					</div>
					<div
						className={`value sm ${row.pnl_usd && Number(row.pnl_usd) < 0 ? "err" : ""}`}
						title={mode === "fixture" ? "fixture sample" : "not returned by the API"}
					>
						{row.pnl_usd ? pnl(row.pnl_usd) : "—"}
					</div>
					<div className="meta">paper marks · fees included</div>
				</div>
			</section>

			<div className={styles.board}>
				<Panel
					flush
					title="Runs"
					sub={`${runs.ok ? runs.data.length : 0} decisions · newest first`}
					action={<Badge tone="ghost">PAPER</Badge>}
					footer="Every run stores inputs, action, reason and order id. A hold or skip is a decision, not a missing row."
				>
					{!runs.ok ? (
						<div className={`panel-body ${styles.empty}`}>
							<strong>Run log unavailable</strong>
							{runs.error.message}
						</div>
					) : runs.data.length === 0 ? (
						<div className="panel-body small muted">
							No runs yet. Run the strategy from the list to record the first decision.
						</div>
					) : (
						<div className={`table-wrap ${styles.logTable}`}>
							<table className="table compact">
								<thead>
									<tr>
										<th scope="col">Time</th>
										<th scope="col">Action</th>
										<th scope="col">Decision / reason</th>
										<th scope="col" className="col-hide-sm">
											Order
										</th>
									</tr>
								</thead>
								<tbody>
									{runs.data.map((run) => (
										<tr key={run.id}>
											<td className="mono" title={run.created_at}>
												{run.created_at.slice(0, 16).replace("T", " ")}
											</td>
											<td>
												<Badge
													tone={
														run.action === "enter" || run.action === "buy"
															? "ok"
															: run.action === "exit"
																? "info"
																: run.action === "denied" || run.action === "error"
																	? "err"
																	: "ghost"
													}
												>
													{run.action}
												</Badge>
											</td>
											<td>{run.reason}</td>
											<td className="col-hide-sm">
												{run.order_id ? (
													<code className="mono">{run.order_id}</code>
												) : (
													<span className="muted">—</span>
												)}
											</td>
										</tr>
									))}
								</tbody>
							</table>
						</div>
					)}
				</Panel>

				<div className="stack">
					<Panel
						flush
						title="Parameters"
						sub="declarative · applied on the next evaluation"
						action={<Badge tone="ghost">PAPER</Badge>}
					>
						<div className="table-wrap">
							<table className="table compact">
								<tbody>
									<tr>
										<td className="muted">Token</td>
										<td>
											{row.token_symbol}{" "}
											<span className={`mono ${styles.cardSub}`} title={row.token_address}>
												{row.token_address}
											</span>
										</td>
									</tr>
									<tr>
										<td className="muted">Chain</td>
										<td>{row.chain}</td>
									</tr>
									<tr>
										<td className="muted">Created</td>
										<td className="mono">{row.created_at.slice(0, 16).replace("T", " ")}</td>
									</tr>
									{rows(row.params).map(([key, value]) => (
										<tr key={key}>
											<td className="muted">{paramLabel(key)}</td>
											<td className="mono">{paramValue(key, value)}</td>
										</tr>
									))}
								</tbody>
							</table>
						</div>
					</Panel>

					<Panel title="Guardrails" sub="what this screen can and cannot do">
						<div className="stack-sm small muted">
							<div>• Config only. No strategy code, no arbitrary execution.</div>
							<div>• One decision per interval; paused strategies never run.</div>
							<div>• Every order goes through the paper order service and risk policy.</div>
							<div>• Unknown marks fail closed; they are never treated as zero.</div>
						</div>
					</Panel>
				</div>
			</div>
		</AppShell>
	);
}
