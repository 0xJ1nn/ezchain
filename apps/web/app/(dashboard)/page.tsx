import Link from "next/link";
import { AppShell } from "@/components/shell";
import { Badge, ButtonLink, Panel, Stat, TokenAvatar } from "@/components/ui";
import {
	getMarketOverview,
	getPaperAlerts,
	getPaperLimits,
	getPaperOrders,
} from "@/lib/account-api";
import { asApiError, getChains, getMarkets } from "@/lib/api";
import {
	ageLabel,
	changeText,
	changeTone,
	formatAmount,
	formatUsd,
	formatUsdPrice,
	initials,
	shortAddress,
	sourceLabel,
} from "@/lib/live-format";
import {
	fixturePaperAccountComplete,
	fixturePaperAccountFlat,
	getPaperSnapshot,
	getStrategies,
} from "@/lib/paper-api";

type Failure = {
	ok: false;
	code?: string;
	message?: string;
	error?: { code: string; message: string };
};

function failure(result: Failure) {
	const code = result.code ?? result.error?.code ?? "UNAVAILABLE";
	const message = result.message ?? result.error?.message ?? "The read is unavailable.";
	return (
		<div className="note">
			<Badge tone="warn">{code}</Badge> {message}
		</div>
	);
}

function SectionBadge({ live }: { live: boolean }) {
	return <Badge tone="ghost">{live ? "PAPER" : "FIXTURE"}</Badge>;
}

function money(value: string | null | undefined, signed = false) {
	if (value === null || value === undefined || value === "") return "—";
	const amount = Number(value);
	if (!Number.isFinite(amount)) return "—";
	return signed && amount > 0 ? `+${formatUsd(String(amount))}` : formatUsd(String(amount));
}

function markCountLabel(count: number) {
	return `${count} open mark${count === 1 ? "" : "s"} unknown`;
}

export default async function DashboardPage({
	searchParams,
}: {
	searchParams: Promise<{ state?: string }>;
}) {
	const { state } = await searchParams;
	const [snapshot, strategies, marketsOutcome, chainsOutcome, limits, alerts, orders, overview] =
		await Promise.all([
			getPaperSnapshot(),
			getStrategies(),
			getMarkets({ limit: 5, sort: "trending" })
				.then((list) => ({ ok: true as const, list }))
				.catch((error: unknown) => ({ ok: false as const, error: asApiError(error) })),
			getChains()
				.then((rows) => ({ ok: true as const, rows }))
				.catch((error: unknown) => ({ ok: false as const, error: asApiError(error) })),
			getPaperLimits(),
			getPaperAlerts(),
			getPaperOrders(5),
			getMarketOverview(),
		]);

	const live = snapshot.mode === "live";
	// Deterministic composition states for fixture screenshots and tests. Live mode always
	// renders the API's answer; a demo flag never masks a real outage.
	const demo = live ? undefined : state;
	const accountResult =
		demo === "complete"
			? { ok: true as const, mode: snapshot.mode, data: fixturePaperAccountComplete }
			: demo === "flat"
				? { ok: true as const, mode: snapshot.mode, data: fixturePaperAccountFlat }
				: snapshot.account;
	const account = accountResult.ok ? accountResult.data : null;
	const positions = account?.positions ?? [];
	const unpricedCount = account?.unpriced_positions.length ?? 0;
	const strategyRows = strategies.ok ? strategies.data : [];
	const marketRows = marketsOutcome.ok ? marketsOutcome.list.rows.slice(0, 5) : [];
	const marketSource = marketsOutcome.ok ? marketsOutcome.list.source : null;
	const marketStale = marketsOutcome.ok ? marketsOutcome.list.stale : false;
	const chainRows = chainsOutcome.ok ? chainsOutcome.rows : [];
	const limitRows = limits.ok ? limits.data : null;
	const alertRows = alerts.ok ? alerts.data.alerts : [];
	const orderRows = orders.ok ? orders.data : [];
	const chainTotal = overview.ok ? overview.data.totals.chains : chainRows.length;
	const chainCovered = overview.ok ? overview.data.totals.covered : null;
	const killSwitch = limitRows?.kill_switch.active ?? false;

	return (
		<AppShell active="dashboard">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">Overview / Dashboard</div>
					<h1>Dashboard</h1>
					<p className="sub">
						One screen to answer: am I safe, am I up, and is anything broken? Every number is
						labelled with its mode and source. Unknown values are —, never zero.
					</p>
				</div>
				<div className="page-actions">
					<Badge tone={live ? "info" : "ghost"}>{live ? "LIVE PAPER API" : "FIXTURE"}</Badge>
					<Link className="btn" href="/performance">
						Performance
					</Link>
					<Link className="btn primary" href="/markets">
						New order
					</Link>
				</div>
			</header>

			<section className="kpi-band mb-12" aria-label="Paper account">
				<Stat
					hero
					label="Equity"
					value={account ? money(account.equity_usd) : "—"}
					meta={
						account
							? account.marks_unknown
								? `equity unavailable — ${markCountLabel(unpricedCount)}`
								: `${positions.length} open position${positions.length === 1 ? "" : "s"} · paper`
							: "account unavailable"
					}
				/>
				<Stat label="Cash" value={account ? money(account.cash_usd) : "—"} meta="ledger cash · USD" />
				<Stat
					label="Realized"
					value={account ? money(account.realized_usd, true) : "—"}
					tone={account && Number(account.realized_usd) < 0 ? "err" : "ok"}
					meta="closed trades · fees included"
				/>
				<Stat
					label="Unrealized"
					value={account ? money(account.unrealized_usd, true) : "—"}
					tone={
						account?.unrealized_usd !== null &&
						account?.unrealized_usd !== undefined &&
						Number(account.unrealized_usd) < 0
							? "err"
							: "ok"
					}
					meta={
						account?.marks_unknown
							? `unrealized unavailable — ${markCountLabel(unpricedCount)}`
							: "known marks only"
					}
				/>
				<Stat
					label="Risk"
					value={killSwitch ? "HALTED" : limitRows ? "OPEN" : "—"}
					tone={killSwitch ? "err" : "ok"}
					meta={limitRows ? "kill switch · strategy scope" : "limits unavailable"}
				/>
				<Stat
					label="Mode"
					value={account ? account.mode.toUpperCase() : "—"}
					meta={<Badge tone="ghost">no keys · no live money</Badge>}
				/>
			</section>

			<Panel
				title="Equity composition"
				sub={
					account
						? account.complete
							? `${account.by_asset.length} asset rows · server shares of ${money(account.equity_usd)}`
							: `equity withheld · ${markCountLabel(unpricedCount)}`
						: "unavailable"
				}
				action={
					<>
						<SectionBadge live={live} />
						<Badge tone="ghost">PAPER</Badge>
					</>
				}
				footer={
					<div className="between" style={{ flexWrap: "wrap", gap: 8 }}>
						<span>
							Totals, quantities and shares are computed by{" "}
							<span className="mono">/api/v1/paper/account</span>. This page only formats
							them.
						</span>
						<ButtonLink href="/wallets" size="sm">
							Wallets
						</ButtonLink>
					</div>
				}
			>
				{!accountResult.ok ? (
					failure(accountResult)
				) : account ? (
					<div className="stack-sm">
						{account.marks_unknown && (
							<div className="note warn">
								<strong>
									Equity unavailable — {markCountLabel(unpricedCount)}.
								</strong>{" "}
								Known rows keep their own values; no share or total is computed from a
								partial portfolio. Cash is shown as the USD settlement asset.
							</div>
						)}
						<div className="grid cols-2">
							<div>
								<div className="strong mb-8">By chain</div>
								<div className="table-wrap">
									<table className="table compact" aria-label="Composition by chain">
										<thead>
											<tr>
												<th scope="col">Chain</th>
												<th scope="col" className="num">
													Assets
												</th>
												<th scope="col" className="num">
													Value
												</th>
												<th scope="col" className="num">
													Share
												</th>
												<th scope="col">Marks</th>
											</tr>
										</thead>
										<tbody>
											{account.by_chain.map((row) => (
												<tr key={row.chain}>
													<td>
														<Link
															href={
																row.chain === "settlement"
																	? "/wallets"
																	: `/markets?chain=${encodeURIComponent(row.chain)}`
															}
														>
															{row.chain}
														</Link>
													</td>
													<td className="num mono">{row.assets}</td>
													<td className="num mono">{money(row.market_value_usd)}</td>
													<td className="num mono">
														{row.share_pct === null ? "—" : `${row.share_pct}%`}
													</td>
													<td>
														{row.mark_status === "unknown" ? (
															<Badge tone="warn">{row.unpriced_assets} unpriced</Badge>
														) : (
															<Badge tone="ghost">known</Badge>
														)}
													</td>
												</tr>
											))}
										</tbody>
									</table>
								</div>
							</div>
							<div>
								<div className="strong mb-8">By asset</div>
								<div className="table-wrap">
									<table className="table compact" aria-label="Composition by asset">
										<thead>
											<tr>
												<th scope="col">Asset</th>
												<th scope="col" className="num">
													Quantity
												</th>
												<th scope="col" className="num">
													Mark
												</th>
												<th scope="col" className="num">
													Value
												</th>
												<th scope="col" className="num">
													Share
												</th>
												<th scope="col">Source · age</th>
											</tr>
										</thead>
										<tbody>
											{account.by_asset.map((row) => (
												<tr key={`${row.chain}:${row.token_address ?? "cash"}`}>
													<td>
														{row.kind === "cash" ? (
															<Link href="/wallets">
																<span className="strong">{row.symbol}</span>
																<span className="sub" style={{ display: "block" }}>
																	settlement · cash
																</span>
															</Link>
														) : (
															<Link href={`/tokens/${row.chain}/${row.token_address}`}>
																<span className="strong">{row.symbol}</span>
																<span className="sub" style={{ display: "block" }}>
																	{row.chain} · {shortAddress(row.token_address ?? "")}
																</span>
															</Link>
														)}
													</td>
													<td className="num mono">{formatAmount(row.quantity)}</td>
													<td className="num mono">{formatUsdPrice(row.mark_usd)}</td>
													<td className="num mono">{money(row.market_value_usd)}</td>
													<td className="num mono">
														{row.share_pct === null ? "—" : `${row.share_pct}%`}
													</td>
													<td className="small">
														{row.source ? (
															`${sourceLabel(row.source.name)} · ${ageLabel(row.source.observed_at)}`
														) : row.kind === "cash" ? (
															"ledger"
														) : (
															<Badge tone="warn">unknown mark</Badge>
														)}
													</td>
												</tr>
											))}
										</tbody>
									</table>
								</div>
							</div>
						</div>
						{positions.length === 0 && (
							<div className="note">
								The paper account is flat; the composition is settlement cash only.
							</div>
						)}
					</div>
				) : null}
			</Panel>

			<div className="split">
				<div className="stack">
					<Panel
						title="Open positions"
						sub={`${positions.length} from the ledger · paper`}
						action={<><SectionBadge live={live} /><ButtonLink href="/positions" size="sm">All positions</ButtonLink></>}
						flush
					>
						{!accountResult.ok ? (
							<div className="panel-body">{failure(accountResult)}</div>
						) : positions.length === 0 ? (
							<div className="panel-body">
								<div className="note">
									<strong>The paper account is flat.</strong> Browse a market and place a paper
									order to open the first position. Unknown values stay —.
								</div>
								<div className="row mt-12">
									<ButtonLink href="/markets" variant="primary">Browse markets</ButtonLink>
								</div>
							</div>
						) : (
							<div className="table-wrap">
								<table className="table">
									<thead>
										<tr>
											<th>Token</th>
											<th className="col-hide-sm">Chain</th>
											<th className="num">Quantity</th>
											<th className="num">Mark</th>
											<th className="num">Value</th>
											<th className="num">Unrealized</th>
											<th className="col-hide-sm">Source</th>
										</tr>
									</thead>
									<tbody>
										{positions.map((position) => (
											<tr key={`${position.chain}:${position.token_address}`}>
												<td>
													<Link className="row" href={`/tokens/${position.chain}/${position.token_address}`}>
														<TokenAvatar size="sm">{initials(position.symbol)}</TokenAvatar>
														<span>
															<span className="strong">{position.symbol}</span>
															<span className="sub" style={{ display: "block" }}>
																{shortAddress(position.token_address)}
															</span>
														</span>
													</Link>
												</td>
												<td className="col-hide-sm">{position.chain}</td>
												<td className="num mono">{formatAmount(position.quantity)}</td>
												<td className="num mono">{formatUsdPrice(position.mark_usd)}</td>
												<td className="num mono">{money(position.market_value_usd)}</td>
												<td className={`num mono ${position.unrealized_usd && Number(position.unrealized_usd) < 0 ? "err" : ""}`}>
													{position.unrealized_usd === null ? "—" : money(position.unrealized_usd, true)}
												</td>
												<td className="col-hide-sm small">
													{position.source ? `${sourceLabel(position.source.name)} · ${ageLabel(position.source.observed_at)}` : "—"}
												</td>
											</tr>
										))}
									</tbody>
								</table>
							</div>
						)}
					</Panel>

					<Panel
						title="Strategies"
						sub={`${strategyRows.filter((row) => row.status === "active").length} active · scheduler-owned`}
						action={<><SectionBadge live={live} /><ButtonLink href="/strategies" size="sm">All strategies</ButtonLink></>}
						flush
					>
						{!strategies.ok ? (
							<div className="panel-body">{failure(strategies)}</div>
						) : strategyRows.length === 0 ? (
							<div className="panel-body">
								<div className="note">
									No strategies yet. The platform ships no alpha; create a rule set or draw a
									graph, then watch it run in paper.
								</div>
								<div className="row mt-12">
									<ButtonLink href="/strategies" variant="primary">Create a strategy</ButtonLink>
								</div>
							</div>
						) : (
							<div className="stack-sm">
								{strategyRows.slice(0, 5).map((strategy) => (
									<div className="list-row" key={strategy.id} style={{ padding: "9px 12px" }}>
										<Badge tone={strategy.status === "active" ? "ok" : "warn"}>{strategy.status}</Badge>
										<span className="grow">
											<Link className="title" href={`/strategies/${strategy.id}`}>{strategy.name}</Link>
											<span className="sub">
												{strategy.kind} · {strategy.token_symbol} · {strategy.chain}
											</span>
										</span>
										<span className="small muted">
											{strategy.last_run_at ? `last ${ageLabel(strategy.last_run_at)}` : "never run"}
										</span>
									</div>
								))}
							</div>
						)}
					</Panel>

					<Panel
						title="Account history"
						sub="No equity snapshots are stored yet"
						action={<SectionBadge live={live} />}
					>
						<div className="note">
							The ledger is the source of truth, but ezCHAIN does not persist a periodic
							equity curve yet. The performance page shows realized PnL from the ledger rather
							than inventing a line.
						</div>
						<div className="row mt-12">
							<ButtonLink href="/performance" size="sm">Paper performance</ButtonLink>
							<ButtonLink href="/ledger" size="sm">Activity</ButtonLink>
						</div>
					</Panel>
				</div>

				<div className="stack">
					<Panel
						title="Risk"
						sub={limitRows ? "server limits · paper" : "limits unavailable"}
						action={<><SectionBadge live={live} /><ButtonLink href="/risk" size="sm">Risk</ButtonLink></>}
					>
						{!limits.ok ? (
							<div className="panel-body">{failure(limits)}</div>
						) : limitRows ? (
							<div className="stack-sm">
								<div className="between small"><span>Max order</span><span className="mono">{formatUsd(limitRows.max_order_usd)}</span></div>
								<div className="between small"><span>Max position</span><span className="mono">{formatUsd(limitRows.max_position_usd)}</span></div>
								<div className="between small"><span>Max open positions</span><span className="mono">{limitRows.max_open_positions}</span></div>
								<div className="between small"><span>Daily loss cap</span><span className="mono">{formatUsd(limitRows.max_daily_loss_usd)}</span></div>
								<div className="between small"><span>Paper fee</span><span className="mono">{limitRows.paper_fee_bps} bps</span></div>
								<div className="hr" />
								<div className="list-row">
									<Badge tone={killSwitch ? "err" : "ok"}>{killSwitch ? "ARMED" : "OFF"}</Badge>
									<span className="grow">
										<span className="title">Kill switch</span>
										<span className="sub">strategy-originated execution</span>
									</span>
									<ButtonLink href="/risk" size="sm">Open</ButtonLink>
								</div>
							</div>
						) : null}
					</Panel>

					<Panel
						title="Alerts"
						sub={alerts.ok ? `${alertRows.length} derived` : "unavailable"}
						action={<><SectionBadge live={live} /><ButtonLink href="/alerts" size="sm">All</ButtonLink></>}
						flush
					>
						{!alerts.ok ? (
							<div className="panel-body">{failure(alerts)}</div>
						) : alertRows.length === 0 ? (
							<div className="panel-body">
								<div className="note">No alerts. Reconciliation, marks and rejected orders are clean.</div>
							</div>
						) : (
							alertRows.slice(0, 4).map((alert) => (
								<div className="list-row" style={{ padding: "9px 12px" }} key={alert.id}>
									<Badge tone={alert.severity === "critical" ? "err" : alert.severity === "warning" ? "warn" : "ghost"}>
										{alert.severity}
									</Badge>
									<span className="grow">
										<span className="title">{alert.title}</span>
										<span className="sub">{alert.detail}</span>
									</span>
								</div>
							))
						)}
					</Panel>

					<Panel
						title="Market watch"
						sub={
							!marketsOutcome.ok
								? "markets unavailable"
								: marketStale
									? "stale source"
									: `${sourceLabel(marketSource?.name)} · ${ageLabel(marketSource?.observed_at)}`
						}
						action={<><SectionBadge live={live} /><ButtonLink href="/markets" size="sm">Markets</ButtonLink></>}
						flush
					>
						{!marketsOutcome.ok ? (
							<div className="panel-body">{failure(marketsOutcome)}</div>
						) : (
						<div className="table-wrap">
							<table className="table compact">
								<thead>
									<tr><th>Token</th><th className="num">Price</th><th className="num col-hide-sm">24h</th><th className="num col-hide-sm">Liquidity</th></tr>
								</thead>
								<tbody>
									{marketRows.map((row) => (
										<tr key={`${row.chain}:${row.pair_address}`}>
											<td>
												<Link href={`/tokens/${row.chain}/${row.base_token}`}>
													<span className="strong">{row.symbol}</span>
													<span className="sub" style={{ display: "block" }}>{row.chain}</span>
												</Link>
											</td>
											<td className="num mono">{formatUsdPrice(row.price_usd)}</td>
											<td className={`num mono col-hide-sm ${changeTone(row.price_change_h24)}`}>{changeText(row.price_change_h24)}</td>
											<td className="num mono col-hide-sm">{row.liquidity_usd ? formatUsd(row.liquidity_usd) : "—"}</td>
										</tr>
									))}
								</tbody>
							</table>
						</div>
						)}
					</Panel>

					<Panel
						title="Chains"
						sub={chainCovered === null ? `${chainTotal} registered` : `${chainCovered}/${chainTotal} with fresh data`}
						action={<><SectionBadge live={live} /><ButtonLink href="/chains" size="sm">Coverage</ButtonLink></>}
					>
						{!chainsOutcome.ok ? (
							failure(chainsOutcome)
						) : (
							<div className="note">
								Market observation, quote ladders and candle capability are per-chain. A chain
								with no data is reported as <span className="mono">no_data</span>, never zero.
								{overview.ok ? ` ${overview.data.totals.snapshots} persisted snapshots.` : ""}
							</div>
						)}
						<div className="row mt-12">
							<ButtonLink href="/chains" size="sm">Chain registry</ButtonLink>
							<ButtonLink href="/integrations" size="sm">Providers</ButtonLink>
						</div>
					</Panel>
				</div>
			</div>

			<Panel
				title="Recent paper orders"
				sub={orders.ok ? `${orderRows.length} newest` : "unavailable"}
				action={<><SectionBadge live={live} /><ButtonLink href="/ledger" size="sm">Activity</ButtonLink></>}
				flush
				footer="Orders are append-only evidence. A rejection carries its reason code; it is never shown as a fill."
			>
				{!orders.ok ? (
					<div className="panel-body">{failure(orders)}</div>
				) : orderRows.length === 0 ? (
					<div className="panel-body"><div className="note">No paper orders yet.</div></div>
				) : (
					<div className="table-wrap">
						<table className="table">
							<thead><tr><th>Side</th><th>Token</th><th className="num">Amount</th><th>Status</th><th className="col-hide-sm">Reason</th><th className="col-hide-sm">Age</th></tr></thead>
							<tbody>
								{orderRows.map((order) => (
									<tr key={order.id}>
										<td><Badge tone={order.side === "buy" ? "info" : "default"}>{order.side}</Badge></td>
										<td><Link href={`/tokens/${order.chain}/${order.token_address}`}>{order.chain} · {shortAddress(order.token_address)}</Link></td>
										<td className="num mono">{formatUsd(order.amount_usd)}</td>
										<td><Badge tone={order.status === "filled" ? "ok" : order.status === "rejected" ? "err" : "warn"}>{order.status}</Badge></td>
										<td className="col-hide-sm small">{order.reason ?? "—"}</td>
										<td className="col-hide-sm small">{ageLabel(order.created_at)}</td>
									</tr>
								))}
							</tbody>
						</table>
					</div>
				)}
			</Panel>
		</AppShell>
	);
}
