import Link from "next/link";
import { AppShell } from "@/components/shell";
import { EmptyState, ModeBadge, Unavailable, FixtureBadge } from "@/components/ops";
import { Badge, Panel, Stat } from "@/components/ui";
import { getPaperAttribution, getPaperPerformance, type AttributionRow, type PaperPerformance } from "@/lib/account-api";
import { formatAmount, formatUsd, formatUsdPrice, shortAddress, ageLabel } from "@/lib/live-format";
import {
	getPaperFills,
	getPaperOrders,
	getPaperSnapshot,
	type PaperAccount,
	type PaperFillRecord,
	type PaperOrderRecord,
	type PaperResult,
	type PaperSnapshot,
} from "@/lib/paper-api";

export const dynamic = "force-dynamic";

function demoError<T>(mode: PaperSnapshot["mode"], message: string): PaperResult<T> {
	return { ok: false, mode, error: { code: "FIXTURE_DEMO_ERROR", message } };
}

export default async function PerformancePage({
	searchParams,
}: {
	searchParams: Promise<{ state?: string }>;
}) {
	const { state } = await searchParams;
	const snapshot = await getPaperSnapshot();
	const mode = snapshot.mode;
	const [ordersResult, fillsResult, performanceResult, attributionResult] = await Promise.all([
		getPaperOrders(50),
		getPaperFills(50),
		getPaperPerformance(),
		getPaperAttribution(),
	]);
	const performance: PaperPerformance | null = performanceResult.ok ? performanceResult.data : null;
	const attribution: AttributionRow[] = attributionResult.ok ? attributionResult.data : [];
	const demo = mode === "fixture" ? state : undefined;

	const accountResult =
		demo === "error"
			? demoError<PaperAccount>(mode, "Fixture demo: the paper account request failed.")
			: snapshot.account;
	const orderResult =
		demo === "error"
			? demoError<PaperOrderRecord[]>(mode, "Fixture demo: the orders request failed.")
			: demo === "empty"
				? { ok: true as const, mode, data: [] as PaperOrderRecord[] }
				: ordersResult;
	const fillResult =
		demo === "error"
			? demoError<PaperFillRecord[]>(mode, "Fixture demo: the fills request failed.")
			: demo === "empty"
				? { ok: true as const, mode, data: [] as PaperFillRecord[] }
				: fillsResult;

	const account = accountResult.ok ? accountResult.data : null;
	const fills = fillResult.ok ? fillResult.data : [];
	const error = !accountResult.ok
		? accountResult.error
		: !orderResult.ok
			? orderResult.error
			: !fillResult.ok
				? fillResult.error
				: !performanceResult.ok
					? { code: performanceResult.code, message: performanceResult.message }
					: null;

	return (
		<AppShell active="performance">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">Overview / Performance</div>
					<h1>Performance</h1>
					<p className="sub">
						Paper account results exactly as the API returns them. Realized and unrealized
						values are server numbers; this page does not compute PnL, time-weight returns or
						an equity curve.
					</p>
				</div>
				<div className="page-actions">
					<ModeBadge mode={mode} />
					<Link className="btn" href="/ledger">
						Activity
					</Link>
					<Link className="btn" href="/reports">
						Export
					</Link>
				</div>
			</header>

			{error && (
				<div className="mb-12">
					<Unavailable
						title="Performance data unavailable"
						code={error.code}
						message={error.message}
						action={
							<Link className="small" href="/performance">
								Retry →
							</Link>
						}
					>
						Missing values stay <span className="mono">—</span>. No fixture rows replace a
						failed live read.
						{demo === "error" && " Fixture demo state ?state=error."}
					</Unavailable>
				</div>
			)}

			<section className="kpi-band mb-12" aria-label="Paper account performance">
				<Stat
					label="Equity"
					value={performance?.equity_usd ? formatUsd(performance.equity_usd) : account ? formatUsd(account.equity_usd) : "—"}
					meta={performance?.equity_usd === null ? "unknown mark excluded · never zero" : "PAPER · cash + known marks"}
				/>
				<Stat
					label="Cash"
					value={performance ? formatUsd(performance.cash_usd) : account ? formatUsd(account.cash_usd) : "—"}
					meta={performance ? "PAPER · ledger projection" : "account unavailable"}
				/>
				<Stat
					label="Realized"
					value={performance ? formatUsd(performance.realized_usd) : account ? formatUsd(account.realized_usd) : "—"}
					tone={performance && Number(performance.realized_usd) < 0 ? "err" : "ok"}
					meta={performance ? "PAPER · after fees" : "account unavailable"}
				/>
				<Stat
					label="Unrealized"
					value={performance?.unrealized_usd ? formatUsd(performance.unrealized_usd) : account ? formatUsd(account.unrealized_usd) : "—"}
					tone={performance && performance.unrealized_usd && Number(performance.unrealized_usd) < 0 ? "err" : "ok"}
					meta={performance?.unrealized_usd === null ? "unknown mark excluded" : "PAPER · known marks only"}
				/>
				<Stat
					label="Trades / win rate"
					value={performance ? `${performance.trades} / ${performance.win_rate_pct ?? "—"}%` : "—"}
					meta={performance ? `${performance.wins}W · ${performance.losses}L` : "performance unavailable"}
				/>
				<Stat
					label="Fees"
					value={performance ? formatUsd(performance.fees_usd) : "—"}
					meta={performance ? "PAPER · included in realized" : "performance unavailable"}
				/>
			</section>

			<div className="split">
				<div className="stack">
					<Panel
						title="Realized by position"
						sub={performance ? `${performance.by_token.length} token rows · server projection` : "unavailable"}
						action={mode === "fixture" ? <FixtureBadge /> : <Badge tone="ghost">PAPER</Badge>}
						flush
						footer="Realized and unrealized values are carried by /api/v1/paper/performance; the UI only formats them."
					>
						{performance && performance.by_token.length > 0 ? (
							<div className="table-wrap">
								<table className="table compact">
									<thead>
										<tr>
											<th>Token</th>
											<th className="num">Realized</th>
											<th className="num">Unrealized</th>
											<th className="num col-hide-sm">Mark</th>
											<th className="num col-hide-sm">Value</th>
										</tr>
									</thead>
									<tbody>
										{performance.by_token.map((position) => (
											<tr key={`${position.chain}:${position.token_address}`}>
												<td>
													<Link href={`/tokens/${position.chain}/${position.token_address}`}>
														<span className="strong">{position.symbol}</span>
													</Link>
													<span className="sub" style={{ display: "block" }}>
														{position.chain} · {shortAddress(position.token_address)}
													</span>
												</td>
												<td className={`num ${Number(position.realized_usd) < 0 ? "err" : ""}`}>
													{formatUsd(position.realized_usd)}
												</td>
												<td
													className={`num ${
														position.unrealized_usd === null
															? "muted"
															: Number(position.unrealized_usd) < 0
																? "err"
																: ""
													}`}
												>
													{formatUsd(position.unrealized_usd)}
												</td>
												<td className="num col-hide-sm">{formatUsdPrice(position.mark_usd)}</td>
												<td className="num col-hide-sm">{formatUsd(position.market_value_usd)}</td>
											</tr>
										))}
									</tbody>
								</table>
							</div>
						) : (
							<div className="panel-body">
								<EmptyState
									title={performance ? "No token results" : "Performance unavailable"}
									links={[{ href: "/positions", label: "Open positions" }]}
								>
									{performance
										? "No token has a realized or open result yet. Unknown values are never printed as zero."
										: "No token rows are rendered while the performance read fails."}
								</EmptyState>
							</div>
						)}
					</Panel>

					<Panel
						title="Recent fills"
						sub={fillResult.ok ? `${fills.length} · last 50` : "unavailable"}
						action={mode === "fixture" ? <FixtureBadge /> : undefined}
						flush
					>
						{fillResult.ok ? (
							fills.length > 0 ? (
								<div className="table-wrap">
									<table className="table compact">
										<thead>
											<tr>
												<th>Time</th>
												<th>Token</th>
												<th>Side</th>
												<th className="num">Quantity</th>
												<th className="num col-hide-sm">Notional</th>
												<th className="num col-hide-sm">Fee</th>
												<th className="col-hide-sm">Quote</th>
											</tr>
										</thead>
										<tbody>
											{fills.slice(0, 10).map((fill) => (
												<tr key={fill.id}>
													<td className="small mono">{fill.created_at.slice(0, 19)}Z</td>
													<td>
														<Link href={`/tokens/${fill.chain}/${fill.token_address}`}>
															{shortAddress(fill.token_address)}
														</Link>
													</td>
													<td>
														<Badge tone={fill.side === "buy" ? "ok" : "warn"}>{fill.side}</Badge>
													</td>
													<td className="num">{formatAmount(fill.quantity)}</td>
													<td className="num col-hide-sm">{formatUsd(fill.notional_usd)}</td>
													<td className="num col-hide-sm">{formatUsd(fill.fee_usd)}</td>
													<td className="col-hide-sm small">{fill.quote_kind}</td>
												</tr>
											))}
										</tbody>
									</table>
								</div>
							) : (
								<div className="panel-body">
									<EmptyState
										title="No fills yet"
										links={[{ href: "/markets", label: "Browse markets", primary: true }]}
									>
										Fills appear here after a paper order is accepted. Rejected orders stay in
										Activity with their reason codes.
									</EmptyState>
								</div>
							)
						) : (
							<div className="panel-body">
								<div className="note err">{fillResult.error.message}</div>
							</div>
						)}
					</Panel>
				</div>

				<div className="stack">
					<Panel
						title="Daily realized"
						sub="ledger groups · UTC days"
						action={<Badge tone="ghost">PAPER</Badge>}
						flush
					>
						{performance && performance.daily_realized.length > 0 ? (
							<div className="table-wrap">
								<table className="table compact">
									<thead><tr><th>Date</th><th className="num">Realized</th><th className="num">Fees</th></tr></thead>
									<tbody>
										{performance.daily_realized.map((day) => (
											<tr key={day.date}>
												<td className="mono small">{day.date}</td>
												<td className={`num ${Number(day.realized_usd) < 0 ? "err" : ""}`}>{formatUsd(day.realized_usd)}</td>
												<td className="num">{formatUsd(day.fees_usd)}</td>
											</tr>
										))}
									</tbody>
								</table>
							</div>
						) : (
							<div className="panel-body"><div className="note">No realized journal groups yet. The API does not store an equity snapshot series, so no equity curve is drawn.</div></div>
						)}
					</Panel>

					<Panel
						title="Attribution"
						sub="per token · share of absolute realized"
						action={<Badge tone="ghost">PAPER</Badge>}
						flush
					>
						{attribution.length > 0 ? (
							<div className="table-wrap">
								<table className="table compact">
									<thead><tr><th>Token</th><th className="num">Realized</th><th className="num">Share</th></tr></thead>
									<tbody>
										{attribution.map((row) => (
											<tr key={`${row.chain}:${row.token_address}`}>
												<td><Link href={`/tokens/${row.chain}/${row.token_address}`}>{row.symbol}</Link><span className="sub" style={{ display: "block" }}>{row.chain}</span></td>
												<td className={`num ${Number(row.realized_usd) < 0 ? "err" : ""}`}>{formatUsd(row.realized_usd)}</td>
												<td className="num">{row.share_pct === null ? "—" : `${row.share_pct}%`}</td>
											</tr>
										))}
									</tbody>
								</table>
							</div>
						) : (
							<div className="panel-body"><div className="note">No token attribution yet. Per-strategy attribution is not exposed by this API build; this table is per token, never invented.</div></div>
						)}
					</Panel>

					<Panel title="Population" sub="what the numbers above cover" action={mode === "fixture" ? <FixtureBadge /> : undefined}>
						<div className="small muted">
							<div>
								Account: <span className="mono">/api/v1/paper/account</span>
							</div>
							<div>
								Performance: <span className="mono">/api/v1/paper/performance</span> · attribution:{" "}
								<span className="mono">/api/v1/paper/attribution</span>
							</div>
							<div>
								Orders: <span className="mono">/api/v1/paper/orders?limit=50</span>
							</div>
							<div>
								Fills: <span className="mono">/api/v1/paper/fills?limit=50</span>
							</div>
							<div className="mt-8">
								Mode: <span className="mono">{mode === "fixture" ? "FIXTURE sample" : "live API"}</span>
							</div>
							<div>
								Latest fill:{" "}
								<span className="mono">
									{fills[0] ? `${ageLabel(fills[0].created_at)} ago` : "—"}
								</span>
							</div>
						</div>
					</Panel>
				</div>
			</div>
		</AppShell>
	);
}
