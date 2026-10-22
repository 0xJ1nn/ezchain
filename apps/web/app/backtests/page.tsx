import Link from "next/link";
import { AppShell } from "@/components/shell";
import { EmptyState, ModeBadge, Unavailable } from "@/components/ops";
import { Badge, Panel, Stat } from "@/components/ui";
import { getBacktestRuns } from "@/lib/backtest-api";
import { formatUsd } from "@/lib/live-format";

export const dynamic = "force-dynamic";

const DOCS = "https://github.com/0xJ1nn/ezchain/blob/main/docs";

export default async function BacktestsPage() {
	const result = await getBacktestRuns();
	const mode = result.mode;
	const runs = result.ok ? result.data : [];

	return (
		<AppShell active="backtests">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">Automate / Backtests</div>
					<h1>Backtests</h1>
					<p className="sub">
						Replayable research, when the backtest service exists. This page renders the API
						result and never simulates a run, a fill or a return in the browser.
					</p>
				</div>
				<div className="page-actions">
					<ModeBadge mode={mode} />
					<a
						className="btn"
						href={`${DOCS}/STRATEGY-GRAPH.md`}
						rel="noreferrer"
					>
						Strategy graph docs
					</a>
					<Link className="btn" href="/strategies">
						Strategies
					</Link>
				</div>
			</header>

			{!result.ok && (
				<div className="mb-12">
					<Unavailable
						title="Backtest service: not part of this build"
						code={result.error.code}
						message={result.error.message}
						action={
							<a
								className="small"
								href={`${DOCS}/STRATEGY-GRAPH.md`}
								rel="noreferrer"
							>
								Read docs/STRATEGY-GRAPH.md →
							</a>
						}
					>
						The page is ready to render <span className="mono">GET /api/v1/backtests/runs</span>{" "}
						when that read model lands. Until then it shows no runs and no metrics. A backtest
						claim would need its fee, slippage and fill model, which this build cannot name.
					</Unavailable>
				</div>
			)}

			<section className="kpi-band mb-12" aria-label="Backtest summary">
				<Stat
					label="Runs"
					value={result.ok ? String(runs.length) : "—"}
					meta={result.ok ? "from the API" : "service unavailable"}
				/>
				<Stat label="Fill model" value="—" meta="named per run when available" />
				<Stat label="Fees" value="—" meta="no default claimed in the UI" />
				<Stat label="Net PnL" value="—" meta="never computed in the browser" />
			</section>

			{result.ok ? (
				<Panel
					title="Runs"
					sub={`${runs.length} · API`}
					flush
					footer="Every result must name its fee, slippage and fill model. A run without that metadata is not a comparable result."
				>
					{runs.length > 0 ? (
						<div className="table-wrap">
							<table className="table">
								<thead>
									<tr>
										<th>Run</th>
										<th>Strategy</th>
										<th>Status</th>
										<th>Fill model</th>
										<th className="num col-hide-sm">Fee bps</th>
										<th className="num col-hide-sm">Slippage bps</th>
										<th className="num col-hide-sm">Trades</th>
										<th className="num">Net PnL</th>
									</tr>
								</thead>
								<tbody>
									{runs.map((run) => (
										<tr key={run.id}>
											<td>
												<span className="strong">{run.name}</span>
												<span className="sub" style={{ display: "block" }}>
													{run.id}
												</span>
											</td>
											<td className="small">{run.strategy_id ?? "—"}</td>
											<td>
												<Badge tone={run.status === "completed" ? "ok" : "warn"}>
													{run.status}
												</Badge>
											</td>
											<td className="small">{run.fill_model ?? "—"}</td>
											<td className="num col-hide-sm">{run.fee_bps ?? "—"}</td>
											<td className="num col-hide-sm">{run.slippage_bps ?? "—"}</td>
											<td className="num col-hide-sm">{run.trades ?? "—"}</td>
											<td className="num">{formatUsd(run.net_pnl_usd)}</td>
										</tr>
									))}
								</tbody>
							</table>
						</div>
					) : (
						<div className="panel-body">
							<EmptyState title="No backtest runs" links={[{ href: "/strategies", label: "Strategies" }]}>
								The backtest endpoint returned an empty list. No result is fabricated.
							</EmptyState>
						</div>
					)}
				</Panel>
			) : (
				<div className="split">
					<Panel title="What is missing" sub="exact blockers">
						<div className="list">
							<div className="list-row">
								<Badge tone="warn">NO ENDPOINT</Badge>
								<span className="grow">
									<span className="title">Run contract</span>
									<span className="sub">
										There is no backtest run list or run-detail route in this API build.
									</span>
								</span>
							</div>
							<div className="list-row">
								<Badge tone="warn">NO STORAGE</Badge>
								<span className="grow">
									<span className="title">Artifacts</span>
									<span className="sub">
										No run artifact table, storage location or retention policy is exposed.
									</span>
								</span>
							</div>
							<div className="list-row">
								<Badge tone="warn">NO CLAIM</Badge>
								<span className="grow">
									<span className="title">Performance numbers</span>
									<span className="sub">
										Without a fee, slippage and fill model a return would be unlabelled and
										uncomparable. None is shown.
									</span>
								</span>
							</div>
						</div>
					</Panel>

					<Panel title="What is ready" sub="the page contract">
						<p className="small muted">
							When the leader adds <span className="mono">GET /api/v1/backtests/runs</span>{" "}
							returning the run shape used by <span className="mono">lib/backtest-api.ts</span>,
							this page renders the table above with no further page change. The UI still
							formats only.
						</p>
						<div className="row mt-12">
							<a className="btn sm" href={`${DOCS}/STRATEGY-GRAPH.md`} rel="noreferrer">
								Strategy graph
							</a>
							<a className="btn sm" href={`${DOCS}/STRATEGY-SDK.md`} rel="noreferrer">
								Strategy SDK
							</a>
							<a className="btn sm" href={`${DOCS}/ARCHITECTURE.md`} rel="noreferrer">
								Architecture
							</a>
						</div>
					</Panel>
				</div>
			)}

			<Panel title="No simulated results" sub="CONTRACT I12" className="mt-16">
				<p className="small muted">
					Backtests are labelled with fees, slippage and fill model at the point they are shown.
					This build has no backtest service, so there is nothing to label and nothing is
					labelled. The absence is the honest state.
				</p>
			</Panel>
		</AppShell>
	);
}
