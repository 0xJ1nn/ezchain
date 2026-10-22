import Link from "next/link";
import { AppShell } from "@/components/shell";
import { EmptyState, ModeBadge, Unavailable, FixtureBadge } from "@/components/ops";
import { Badge, ButtonLink, Panel, Stat } from "@/components/ui";
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
	const [ordersResult, fillsResult] = await Promise.all([getPaperOrders(50), getPaperFills(50)]);
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
	const orders = orderResult.ok ? orderResult.data : [];
	const fills = fillResult.ok ? fillResult.data : [];
	const error = !accountResult.ok
		? accountResult.error
		: !orderResult.ok
			? orderResult.error
			: !fillResult.ok
				? fillResult.error
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
					value={account ? formatUsd(account.equity_usd) : "—"}
					meta={account ? "PAPER · cash + known marks" : "account unavailable"}
				/>
				<Stat
					label="Cash"
					value={account ? formatUsd(account.cash_usd) : "—"}
					meta={account ? "PAPER · ledger cash" : "account unavailable"}
				/>
				<Stat
					label="Realized"
					value={account ? formatUsd(account.realized_usd) : "—"}
					tone={account && Number(account.realized_usd) < 0 ? "err" : "ok"}
					meta={account ? "PAPER · after fees" : "account unavailable"}
				/>
				<Stat
					label="Unrealized"
					value={account ? formatUsd(account.unrealized_usd) : "—"}
					tone={account && Number(account.unrealized_usd) < 0 ? "err" : "ok"}
					meta={account ? "PAPER · known marks only" : "account unavailable"}
				/>
				<Stat
					label="Orders"
					value={orderResult.ok ? String(orders.length) : "—"}
					meta={orderResult.ok ? "last 50 · API" : "orders unavailable"}
				/>
				<Stat
					label="Fills"
					value={fillResult.ok ? String(fills.length) : "—"}
					meta={fillResult.ok ? "last 50 · API" : "fills unavailable"}
				/>
			</section>

			<div className="split">
				<div className="stack">
					<Panel
						title="Realized by position"
						sub={account ? `${account.positions.length} open · server view model` : "unavailable"}
						action={mode === "fixture" ? <FixtureBadge /> : <Badge tone="ghost">PAPER</Badge>}
						flush
						footer="Realized and unrealized values are carried by /api/v1/paper/account; the UI only formats them."
					>
						{account && account.positions.length > 0 ? (
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
										{account.positions.map((position) => (
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
									title={account ? "No open positions" : "Account unavailable"}
									links={[{ href: "/positions", label: "Open positions" }]}
								>
									{account
										? "The paper account is flat, so there is no per-position result to show. Unknown values are never printed as zero."
										: "No position rows are rendered while the account read fails."}
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
						title="Equity series"
						sub="not available in this build"
						action={<Badge tone="warn">NO HISTORY</Badge>}
					>
						<div className="note warn">
							<strong>No equity curve is drawn.</strong> The paper API exposes a point-in-time
							account snapshot and fill history, not an equity-history series. Inventing one
							from the current balance would misstate the past.
						</div>
						<p className="small muted mt-8">
							Exact reason: there is no <span className="mono">/paper/equity</span> or
							<span className="mono"> /paper/history</span> endpoint in this build. When one
							exists, this panel can render it without changing the KPI contract.
						</p>
					</Panel>

					<Panel
						title="Attribution"
						sub="not computed in this build"
						action={<Badge tone="warn">NO ATTRIBUTION</Badge>}
					>
						<p className="small muted">
							The strategy list endpoint does not return per-strategy PnL, and the account
							read model does not attribute fills to a strategy. This page therefore shows no
							per-strategy return, win rate or fee total.
						</p>
						<ButtonLink href="/strategies" size="sm">
							Installed strategies
						</ButtonLink>
					</Panel>

					<Panel title="Population" sub="what the numbers above cover" action={mode === "fixture" ? <FixtureBadge /> : undefined}>
						<div className="small muted">
							<div>
								Account: <span className="mono">/api/v1/paper/account</span>
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
