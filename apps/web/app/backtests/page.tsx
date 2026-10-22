import Link from "next/link";
import { AppShell } from "@/components/shell";
import { EmptyState, ModeBadge, Unavailable } from "@/components/ops";
import { Badge, Panel, Stat } from "@/components/ui";
import { getBacktests, type BacktestRow } from "@/lib/backtest-api";
import { formatUsd } from "@/lib/live-format";

export const dynamic = "force-dynamic";

function requestText(row: BacktestRow, key: string): string {
	const value = row.request[key];
	return typeof value === "string" || typeof value === "number" ? String(value) : "—";
}

function pct(value: string | null | undefined) {
	if (value === null || value === undefined) return "—";
	return `${value}%`;
}

export default async function BacktestsPage() {
	const result = await getBacktests();
	const mode = result.mode;
	const runs: BacktestRow[] = result.ok ? result.data : [];
	const net = runs.reduce((sum, row) => sum + Number(row.metrics.net_pnl_usd), 0);
	const trades = runs.reduce((sum, row) => sum + row.metrics.trades, 0);

	return (
		<AppShell active="backtests">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">Automate / Backtests</div>
					<h1>Backtests</h1>
					<p className="sub">
						Immutable graph replays from <span className="mono">/api/v1/backtests</span>. Every
						row is labelled <strong>BACKTEST</strong> and names its fee, slippage and fill
						model; the browser never computes a return.
					</p>
				</div>
				<div className="page-actions">
					<ModeBadge mode={mode} />
					<Link className="btn" href="/strategies/builder">
						Open builder
					</Link>
					<Link className="btn" href="/strategies">
						Strategies
					</Link>
				</div>
			</header>

			{!result.ok && (
				<div className="mb-12">
					<Unavailable
						title="Backtest list unavailable"
						code={result.error.code}
						message={result.error.message}
						action={
							<Link className="small" href="/backtests">
								Retry →
							</Link>
						}
					>
						No run is fabricated. A backtest result needs a stored row and a named fee,
						slippage and fill model.
					</Unavailable>
				</div>
			)}

			<section className="kpi-band mb-12" aria-label="Backtest summary">
				<Stat label="Runs" value={result.ok ? String(runs.length) : "—"} meta="immutable BACKTEST rows" />
				<Stat label="Net PnL" value={result.ok && runs.length > 0 ? formatUsd(String(net)) : "—"} meta="sum of server metrics" />
				<Stat label="Trades" value={result.ok && runs.length > 0 ? String(trades) : "—"} meta="fills across rows" />
				<Stat label="Label" value="BACKTEST" meta="never PAPER or LIVE" />
			</section>

			{result.ok ? (
				<Panel
					title="Runs"
					sub={`${runs.length} · newest first`}
					action={<Badge tone="ghost">BACKTEST</Badge>}
					flush
					footer="A result without fee, slippage and fill-model metadata is not comparable. The API stores those fields with every row."
				>
					{runs.length > 0 ? (
						<div className="table-wrap">
							<table className="table">
								<thead>
									<tr>
										<th>Created</th>
										<th>Run</th>
										<th>Target</th>
										<th className="col-hide-sm">Fill model</th>
										<th className="num col-hide-sm">Fee bps</th>
										<th className="num col-hide-sm">Slippage bps</th>
										<th className="num">Net PnL</th>
										<th className="num col-hide-sm">Return</th>
										<th className="num col-hide-sm">Trades</th>
									</tr>
								</thead>
								<tbody>
									{runs.map((row) => (
										<tr key={row.id}>
											<td className="small mono">{row.created_at.slice(0, 19)}Z</td>
											<td>
												<span className="mono small">{row.id.slice(0, 8)}…</span>
												<span className="sub" style={{ display: "block" }}>
													{row.label} · {row.data_source}
												</span>
											</td>
											<td className="small">
												{requestText(row, "chain")} · {requestText(row, "timeframe")}
												<span className="sub" style={{ display: "block" }}>
													{requestText(row, "token_address")}
												</span>
											</td>
											<td className="small col-hide-sm">{row.fill_model}</td>
											<td className="num col-hide-sm">{requestText(row, "fee_bps")}</td>
											<td className="num col-hide-sm">{requestText(row, "slippage_bps")}</td>
											<td className={`num ${Number(row.metrics.net_pnl_usd) < 0 ? "err" : "ok"}`}>
												{formatUsd(row.metrics.net_pnl_usd)}
											</td>
											<td className="num col-hide-sm">{pct(row.metrics.return_pct)}</td>
											<td className="num col-hide-sm">{row.metrics.trades}</td>
										</tr>
									))}
								</tbody>
							</table>
						</div>
					) : (
						<div className="panel-body">
							<EmptyState
								title="No backtest runs"
								links={[
									{ href: "/strategies/builder", label: "Build a graph", primary: true },
									{ href: "/strategies", label: "Strategies" },
								]}
							>
								The API returned an empty list. Creating a graph strategy and running a
								backtest stores the first immutable row here.
							</EmptyState>
						</div>
					)}
				</Panel>
			) : (
				<Panel title="What the page needs" sub="honest requirements">
					<div className="note">
						A live API with the <span className="mono">backtests</span> table at migration
						head. In fixture mode no backtest rows are fabricated: the builder and the
						backtester are live-only surfaces.
					</div>
					<div className="row mt-12">
						<Link className="btn" href="/operations">
							Operations
						</Link>
						<Link className="btn" href="/developer">
							Developer
						</Link>
					</div>
				</Panel>
			)}
		</AppShell>
	);
}
