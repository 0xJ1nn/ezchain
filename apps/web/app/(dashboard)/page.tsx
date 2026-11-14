import Link from "next/link";
import { ModeBadge } from "@/components/ops";
import { AppShell } from "@/components/shell";
import { Badge, ButtonLink, Meter, Panel, Stat, TokenAvatar } from "@/components/ui";
import {
	getMarketOverview,
	getPaperAlerts,
	getPaperLimits,
	getPaperOrders,
} from "@/lib/account-api";
import { asApiError, getChains, getLaunches, getMarkets, LAUNCH_WINDOW_MINUTES } from "@/lib/api";
import { killSwitchLevels, killSwitchScopeSummary } from "@/lib/kill-switch-copy";
import {
	getExecutionMode,
	type ExecutionMode,
	type SystemResult,
} from "@/lib/system-api";
import {
	ageLabel,
	chainInitial,
	changeText,
	changeTone,
	formatAmount,
	formatUsd,
	formatUsdPrice,
	initials,
	pairAgeLabel,
	shortAddress,
	sourceLabel,
} from "@/lib/live-format";
import {
	fixturePaperAccountComplete,
	fixturePaperAccountFlat,
	fixturePaperAccountMany,
	fixturePaperAccountSingle,
	fixturePaperAccountUnpricedSource,
	getKillSwitch,
	getPaperSnapshot,
	getStrategies,
} from "@/lib/paper-api";
import { DashboardKillSwitchCard } from "./kill-switch-card";

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

function SectionBadge({ mode }: { mode: SystemResult<ExecutionMode> }) {
	if (!mode.ok) {
		return (
			<Badge
				tone="warn"
				title={`Execution mode unavailable · ${mode.error.code}: ${mode.error.message}`}
			>
				UNKNOWN
			</Badge>
		);
	}
	return (
		<Badge
			tone={mode.data === "live" ? "info" : "ghost"}
			title={`Execution mode · ${mode.data} · derived by the API`}
		>
			{mode.data.toUpperCase()}
		</Badge>
	);
}

// Prose uses the same server value the badges render, and says nothing when the
// server reported no mode: a sentence must not assert a mode the page cannot know.
function modeNote(mode: SystemResult<ExecutionMode>) {
	return mode.ok ? ` · ${mode.data}` : "";
}

function money(value: string | null | undefined, signed = false) {
	if (value === null || value === undefined || value === "") return "—";
	const amount = Number(value);
	if (!Number.isFinite(amount)) return "—";
	return signed && amount > 0 ? `+${formatUsd(String(amount))}` : formatUsd(String(amount));
}

// The window is the query the panel sends, so the copy cannot drift from it.
function windowLabel(minutes: number) {
	return minutes % 60 === 0 ? `${minutes / 60}h` : `${minutes}m`;
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
	const [
		snapshot,
		strategies,
		marketsOutcome,
		launchesOutcome,
		chainsOutcome,
		limits,
		alerts,
		orders,
		overview,
		executionMode,
		killSwitchResult,
	] = await Promise.all([
			getPaperSnapshot(),
			getStrategies(),
			getMarkets({ limit: 5, sort: "trending" })
				.then((list) => ({ ok: true as const, list }))
				.catch((error: unknown) => ({ ok: false as const, error: asApiError(error) })),
			getLaunches({ limit: 3, maxPairAgeMinutes: LAUNCH_WINDOW_MINUTES })
				.then((list) => ({ ok: true as const, list }))
				.catch((error: unknown) => ({ ok: false as const, error: asApiError(error) })),
			getChains()
				.then((rows) => ({ ok: true as const, rows }))
				.catch((error: unknown) => ({ ok: false as const, error: asApiError(error) })),
			getPaperLimits(),
			getPaperAlerts(),
			getPaperOrders(5),
			getMarketOverview(),
			getExecutionMode(),
			getKillSwitch(),
		]);

	const live = snapshot.mode === "live";
	// Deterministic composition states for fixture screenshots and tests. Live mode always
	// renders the API's answer; a demo flag never masks a real outage.
	const demo = live ? undefined : state;
	const demoAccount =
		demo === "complete"
			? fixturePaperAccountComplete
			: demo === "flat"
				? fixturePaperAccountFlat
				: demo === "many"
					? fixturePaperAccountMany
					: demo === "single"
						? fixturePaperAccountSingle
						: demo === "unpriced-source"
							? fixturePaperAccountUnpricedSource
							: null;
	const accountResult =
		demoAccount === null
			? snapshot.account
			: { ok: true as const, mode: snapshot.mode, data: demoAccount };
	const account = accountResult.ok ? accountResult.data : null;
	const positions = account?.positions ?? [];
	const unpricedCount = account?.unpriced_positions.length ?? 0;
	const strategyRows = strategies.ok ? strategies.data : [];
	const marketRows = marketsOutcome.ok ? marketsOutcome.list.rows.slice(0, 5) : [];
	const marketSource = marketsOutcome.ok ? marketsOutcome.list.source : null;
	const marketStale = marketsOutcome.ok ? marketsOutcome.list.stale : false;
	const chainRows = chainsOutcome.ok ? chainsOutcome.rows : [];
	const launchRows = launchesOutcome.ok ? launchesOutcome.list.rows.slice(0, 3) : [];
	const trendingRows = marketRows.slice(0, 3);
	const labelFor = (id: string) => chainRows.find((chain) => chain.id === id)?.name ?? id;
	const limitRows = limits.ok ? limits.data : null;
	const alertRows = alerts.ok ? alerts.data.alerts : [];
	const orderRows = orders.ok ? orders.data : [];
	const chainTotal = overview.ok ? overview.data.totals.chains : chainRows.length;
	const chainCovered = overview.ok ? overview.data.totals.covered : null;
	// The switch is read on its own so the card keeps the API's level list even when the
	// limits read fails; the fixture build answers a released state, never a guess.
	const killSwitch = killSwitchResult.ok ? killSwitchResult.data : null;
	const killSwitchError = killSwitchResult.ok ? null : killSwitchResult.error.message;
	// The levels come from the response. An empty list stays empty so the copy says the
	// read did not report them instead of printing a dangling plural.
	const killSwitchScope = killSwitch ? killSwitchScopeSummary(killSwitch.scopes) : null;
	const killSwitchMeta = killSwitch
		? killSwitchLevels(killSwitch.scopes).length > 0
			? `${killSwitchScope} levels`
			: killSwitchScope
		: "switch state unavailable";

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
					{/* A data-source label, not a mode claim: it says where the rows come from. */}
					<ModeBadge mode={snapshot.mode} />
					<Link className="btn" href="/performance">
						Performance
					</Link>
					<Link className="btn primary" href="/markets">
						New order
					</Link>
				</div>
			</header>

			<section className="panel" data-testid="market-strip" aria-label="Market strip">
				<div className="panel-head">
					<h2>Market</h2>
					<span className="sub">
						launches + trending{modeNote(executionMode)} · every number carries source and age
					</span>
					<div className="right">
						<SectionBadge mode={executionMode} />
						<ModeBadge mode={snapshot.mode} />
					</div>
				</div>
				<div className="grid cols-2">
					<div>
						<div className="between mb-8">
							<div className="strong">Launches</div>
							<div className="small muted">
								born in the last {windowLabel(LAUNCH_WINDOW_MINUTES)} · pair age
							</div>
						</div>
						{!launchesOutcome.ok ? (
							<div className="note">{failure(launchesOutcome)}</div>
						) : launchRows.length === 0 ? (
							<div className="note">
								No pair was born inside the {windowLabel(LAUNCH_WINDOW_MINUTES)} window.
								Empty is not zero: the feed is reported empty rather than padded with
								older pairs.
							</div>
						) : (
							<div className="table-wrap">
								<table className="table compact">
									<thead>
										<tr>
											<th>Token</th>
											<th>Chain</th>
											<th className="num">Pair age</th>
											<th>Curve progress</th>
											<th>Trade</th>
										</tr>
									</thead>
									<tbody>
										{launchRows.map((row) => (
											<tr key={`launch:${row.chain}:${row.base_token}`}>
												<td>
													<span className="row">
														<TokenAvatar size="sm">{initials(row.symbol)}</TokenAvatar>
														<span>
															<span className="strong">{row.symbol}</span>
															<span className="sub" style={{ display: "block" }}>
																{shortAddress(row.base_token)}
															</span>
														</span>
													</span>
												</td>
												<td>
													<span className="chain">
														<span className="sq">{chainInitial(labelFor(row.chain))}</span>
														{labelFor(row.chain)}
													</span>
												</td>
												<td className="num mono">{pairAgeLabel(row.pair_created_at)}</td>
												<td>
													{row.curve_progress_pct === null ? (
														<span className="small muted mono">—</span>
													) : (
														<span>
															<Meter
																width={`${Math.max(0, Math.min(100, row.curve_progress_pct))}%`}
																tone={row.curve_progress_pct >= 70 ? "warn" : undefined}
																label="Curve progress"
																valueText={`${row.curve_progress_pct}%`}
															/>
															<span className="small muted mono">{row.curve_progress_pct}%</span>
														</span>
													)}
												</td>
												<td>
													<ButtonLink href={`/tokens/${row.chain}/${row.base_token}`} size="sm">
														Trade
													</ButtonLink>
												</td>
											</tr>
										))}
									</tbody>
								</table>
							</div>
						)}
						<div className="small muted mt-8">Newest births first · safety and dev history unknown in P0.</div>
						<div className="row mt-8">
							<ButtonLink href="/launches" size="sm">
								All launches
							</ButtonLink>
						</div>
					</div>
					<div>
						<div className="strong mb-8">Trending</div>
						{!marketsOutcome.ok ? (
							<div className="note">{failure(marketsOutcome)}</div>
						) : trendingRows.length === 0 ? (
							<div className="note">No trending rows in the fixture sample for this view.</div>
						) : (
							<div className="table-wrap">
								<table className="table compact">
									<thead>
										<tr>
											<th>Token</th>
											<th>Chain</th>
											<th className="num">Age</th>
											<th className="num">Price</th>
											<th>Trade</th>
										</tr>
									</thead>
									<tbody>
										{trendingRows.map((row) => (
											<tr key={`trending:${row.chain}:${row.pair_address}`}>
												<td>
													<span className="row">
														<TokenAvatar size="sm">{initials(row.symbol)}</TokenAvatar>
														<span>
															<span className="strong">{row.symbol}</span>
															<span className="sub" style={{ display: "block" }}>
																{shortAddress(row.base_token)}
															</span>
														</span>
													</span>
												</td>
												<td>
													<span className="chain">
														<span className="sq">{chainInitial(labelFor(row.chain))}</span>
														{labelFor(row.chain)}
													</span>
												</td>
												<td className="num mono">{ageLabel(row.pair_created_at)}</td>
												<td className="num mono">{formatUsdPrice(row.price_usd)}</td>
												<td>
													<ButtonLink href={`/tokens/${row.chain}/${row.base_token}`} size="sm">
														Trade
													</ButtonLink>
												</td>
											</tr>
										))}
									</tbody>
								</table>
							</div>
						)}
						<div className="small muted mt-8">Trending = 24h volume order · source on the Markets page.</div>
						<div className="row mt-8">
							<ButtonLink href="/markets" size="sm">
								All markets
							</ButtonLink>
						</div>
					</div>
				</div>
				<div className="panel-foot">
					Unknown is <span className="mono">—</span>, never zero.{" "}
					{executionMode.ok
						? `The server reports ${executionMode.data} execution.`
						: "The server did not report its execution mode."}{" "}
					{snapshot.mode === "fixture"
						? "Preview rows are fixture sample data and say so."
						: "Every number carries its source and age."}
				</div>
			</section>

			<section className="kpi-band mb-12" aria-label="Paper account">
				<Stat
					hero
					label="Equity"
					value={account ? money(account.equity_usd) : "—"}
					meta={
						account
							? account.marks_unknown
								? `equity unavailable — ${markCountLabel(unpricedCount)}`
								: `${positions.length} open position${positions.length === 1 ? "" : "s"}${modeNote(executionMode)}`
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
					label="Kill switch"
					value={killSwitch ? (killSwitch.active ? "ARMED" : "RELEASED") : "—"}
					tone={killSwitch?.active ? "err" : "ok"}
					meta={killSwitchMeta}
					title={
						killSwitch
							? `Persisted operator switch · ${killSwitchScope}. It halts new entries; it does not sell or close a position.`
							: `Kill switch unavailable · ${killSwitchError ?? "the API did not return it"}`
					}
				/>
				<Stat
					label="Mode"
					value={executionMode.ok ? executionMode.data.toUpperCase() : "UNKNOWN"}
					title={
						executionMode.ok
							? `Execution mode · ${executionMode.data} · derived by the API, never by this page`
							: `Execution mode unavailable · ${executionMode.error.code}: ${executionMode.error.message}`
					}
					meta={executionMode.ok ? "server-derived · no keys" : "mode not reported"}
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
				action={<SectionBadge mode={executionMode} />}
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
														) : row.mark_status === "fresh" ? (
															<Badge tone="ghost">fresh</Badge>
														) : row.mark_status === "stale" ? (
															<Badge tone="warn">stale</Badge>
														) : (
															<Badge tone="warn">future</Badge>
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
														{row.mark_status === "unknown" ? (
															<Badge tone="warn">unknown mark</Badge>
														) : row.source ? (
															`${row.mark_status} · ${sourceLabel(row.source.name)} · ${ageLabel(row.source.observed_at)}`
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
						sub={`${positions.length} from the ledger${modeNote(executionMode)}`}
						action={<><SectionBadge mode={executionMode} /><ButtonLink href="/positions" size="sm">All positions</ButtonLink></>}
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
						action={<><SectionBadge mode={executionMode} /><ButtonLink href="/strategies" size="sm">All strategies</ButtonLink></>}
						flush
					>
						{!strategies.ok ? (
							<div className="panel-body">{failure(strategies)}</div>
						) : strategyRows.length === 0 ? (
							<div className="panel-body">
								<div className="note">
									No strategies yet. The platform ships no alpha; create a rule set or draw a
									graph, then watch it run{modeNote(executionMode)}.
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
						action={<SectionBadge mode={executionMode} />}
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
						title="Server limits"
						sub={
							limitRows ? `configured${modeNote(executionMode)}` : "limits unavailable"
						}
						action={<><SectionBadge mode={executionMode} /><ButtonLink href="/risk" size="sm">Risk</ButtonLink></>}
					>
						{!limits.ok ? (
							failure(limits)
						) : limitRows ? (
							<div className="stack-sm">
								<div className="between small"><span>Max order</span><span className="mono">{formatUsd(limitRows.max_order_usd)}</span></div>
								<div className="between small"><span>Max position</span><span className="mono">{formatUsd(limitRows.max_position_usd)}</span></div>
								<div className="between small"><span>Max open positions</span><span className="mono">{limitRows.max_open_positions}</span></div>
								<div className="between small"><span>Daily loss cap</span><span className="mono">{formatUsd(limitRows.max_daily_loss_usd)}</span></div>
								<div className="between small"><span>Paper fee</span><span className="mono">{limitRows.paper_fee_bps} bps</span></div>
							</div>
						) : null}
					</Panel>

					<Panel
						title="Kill switch"
						sub="one persisted flag · two levels"
						action={<ButtonLink href="/risk" size="sm">Risk</ButtonLink>}
					>
						<DashboardKillSwitchCard initial={killSwitch} initialError={killSwitchError} />
					</Panel>

					<Panel
						title="Alerts"
						sub={alerts.ok ? `${alertRows.length} derived` : "unavailable"}
						action={<><SectionBadge mode={executionMode} /><ButtonLink href="/alerts" size="sm">All</ButtonLink></>}
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
						action={<><SectionBadge mode={executionMode} /><ButtonLink href="/markets" size="sm">Markets</ButtonLink></>}
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
						action={<><SectionBadge mode={executionMode} /><ButtonLink href="/chains" size="sm">Coverage</ButtonLink></>}
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
				action={<><SectionBadge mode={executionMode} /><ButtonLink href="/ledger" size="sm">Activity</ButtonLink></>}
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
