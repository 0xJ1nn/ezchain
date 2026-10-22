import Link from "next/link";
import { AppShell } from "@/components/shell";
import { EmptyState, FixtureBadge, ModeBadge, Unavailable } from "@/components/ops";
import { Badge, ButtonLink, Panel, Stat } from "@/components/ui";
import { ageSeconds, formatUsd, formatUsdPrice, quoteKindLabel, shortAddress } from "@/lib/live-format";
import {
	getKillSwitch,
	getPaperOrders,
	getPaperSnapshot,
	type PaperOrderRecord,
} from "@/lib/paper-api";
import { KillSwitchControl } from "./kill-switch";

export const dynamic = "force-dynamic";

const STALE_AFTER_SECONDS = 60;

function isStale(observedAt: string | null | undefined) {
	const age = ageSeconds(observedAt);
	return age === null || age > STALE_AFTER_SECONDS;
}

/** Rejected attempts are the only risk-decision records this build exposes. */
function rejectionSummary(orders: PaperOrderRecord[]) {
	const rejected = orders.filter((order) => order.status === "rejected");
	const reasons = new Map<string, number>();
	for (const order of rejected) {
		const key = order.reason ?? "UNKNOWN_REASON";
		reasons.set(key, (reasons.get(key) ?? 0) + 1);
	}
	return { rejected, reasons: [...reasons.entries()] };
}

export default async function RiskPage() {
	const [snapshot, killSwitchResult, ordersResult] = await Promise.all([
		getPaperSnapshot(),
		getKillSwitch(),
		getPaperOrders(100),
	]);
	const mode = snapshot.mode;
	const account = snapshot.account.ok ? snapshot.account.data : null;
	const positions = snapshot.positions.ok ? snapshot.positions.data : [];
	const orders = ordersResult.ok ? ordersResult.data : [];
	const rejected = rejectionSummary(orders);
	const killSwitch = killSwitchResult.ok ? killSwitchResult.data : null;
	const killSwitchError = killSwitchResult.ok ? null : killSwitchResult.error.message;
	const error = !snapshot.account.ok
		? snapshot.account.error
		: !snapshot.positions.ok
			? snapshot.positions.error
			: null;
	const stale = positions.filter((position) => isStale(position.source?.observed_at));
	const unknown = positions.filter((position) => position.mark_usd === null);

	return (
		<AppShell active="risk">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">Protect / Risk</div>
					<h1>Risk</h1>
					<p className="sub">
						The persisted operator switch, the paper account as the API returns it, and the
						rejected order attempts that are actually recorded. No limit is invented here; the
						API owns the numbers and this build exposes no limit read endpoint.
					</p>
				</div>
				<div className="page-actions">
					<ModeBadge mode={mode} />
					<Link className="btn" href="/alerts">
						Alerts
					</Link>
					<Link className="btn" href="/ledger">
						Activity
					</Link>
				</div>
			</header>

			{error && (
				<div className="mb-12">
					<Unavailable
						title="Risk inputs unavailable"
						code={error.code}
						message={error.message}
						action={
							<Link className="small" href="/risk">
								Retry →
							</Link>
						}
					>
						The kill switch control is separate and still reads its own persisted state.
					</Unavailable>
				</div>
			)}

			<Panel
				title="Kill switch"
				sub="persisted · strategy scope"
				action={mode === "fixture" ? <FixtureBadge /> : <Badge tone="ghost">/api/v1/system/kill-switch</Badge>}
				className="mb-12"
			>
				<KillSwitchControl initial={killSwitch} initialError={killSwitchError} />
				<div className="hr" />
				<div className="grid cols-2">
					<div className="note">
						<strong>What arming does:</strong> new strategy-attributed orders are rejected
						before a quote, with reason <span className="mono">KILL_SWITCH_ACTIVE</span>. The
						state is persisted in the API database and survives a restart.
					</div>
					<div className="note warn">
						<strong>What it does not do:</strong> it does not sell, close or mark a position.
						It does not block manual paper orders placed from Markets or a token page, because
						those carry no strategy attribution. Scope is{" "}
						<span className="mono">strategy</span>.
					</div>
				</div>
				{mode === "fixture" && (
					<p className="small muted mt-8">
						Fixture mode shows a sample released state. The arm/release action is still sent
						to the API through the Next proxy and never fabricates a persisted result.
					</p>
				)}
			</Panel>

			<section className="kpi-band mb-12" aria-label="Paper risk inputs">
				<Stat
					label="Equity"
					value={account ? formatUsd(account.equity_usd) : "—"}
					meta={account ? "PAPER · server snapshot" : "unavailable"}
				/>
				<Stat
					label="Cash"
					value={account ? formatUsd(account.cash_usd) : "—"}
					meta={account ? "PAPER · ledger cash" : "unavailable"}
				/>
				<Stat
					label="Open positions"
					value={snapshot.positions.ok ? String(positions.length) : "—"}
					meta="server PositionView"
				/>
				<Stat
					label="Stale marks"
					value={snapshot.positions.ok ? String(stale.length) : "—"}
					tone={stale.length > 0 ? "err" : undefined}
					meta={`older than ${STALE_AFTER_SECONDS}s`}
				/>
				<Stat
					label="Unknown marks"
					value={snapshot.positions.ok ? String(unknown.length) : "—"}
					tone={unknown.length > 0 ? "err" : undefined}
					meta="value not known · never zero"
				/>
				<Stat
					label="Rejected orders"
					value={ordersResult.ok ? String(rejected.rejected.length) : "—"}
					meta="last 100 attempts"
				/>
			</section>

			<div className="split">
				<div className="stack">
					<Panel
						title="Exposure by position"
						sub={snapshot.positions.ok ? `${positions.length} open` : "unavailable"}
						action={mode === "fixture" ? <FixtureBadge /> : undefined}
						flush
						footer="These are server marks and values. There is no aggregate exposure endpoint in this build, so the page does not add them up itself."
					>
						{snapshot.positions.ok ? (
							positions.length > 0 ? (
								<div className="table-wrap">
									<table className="table">
										<thead>
											<tr>
												<th>Token</th>
												<th>Chain</th>
												<th className="num">Quantity</th>
												<th className="num">Mark</th>
												<th className="num">Market value</th>
												<th className="num">Unrealized</th>
												<th>Mark source</th>
											</tr>
										</thead>
										<tbody>
											{positions.map((position) => {
												const age = ageSeconds(position.source?.observed_at);
												const staleMark = age === null || age > STALE_AFTER_SECONDS;
												return (
													<tr key={`${position.chain}:${position.token_address}`}>
														<td>
															<Link href={`/tokens/${position.chain}/${position.token_address}`}>
																<span className="strong">{position.symbol}</span>
															</Link>
															<span className="sub" style={{ display: "block" }}>
																{shortAddress(position.token_address)}
															</span>
														</td>
														<td>{position.chain}</td>
														<td className="num mono">{position.quantity}</td>
														<td className="num">{formatUsdPrice(position.mark_usd)}</td>
														<td className="num">{formatUsd(position.market_value_usd)}</td>
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
														<td>
															{position.source ? (
																<Badge tone={staleMark ? "warn" : "ghost"}>
																	{staleMark ? "stale" : "fresh"} · {position.source.name}
																</Badge>
															) : (
																<Badge tone="warn">unknown</Badge>
															)}
														</td>
													</tr>
												);
											})}
										</tbody>
									</table>
								</div>
							) : (
								<div className="panel-body">
									<EmptyState title="Flat paper account" links={[{ href: "/markets", label: "Markets" }]}>
										No open position carries exposure. That is a real zero-position state,
										not an unknown value.
									</EmptyState>
								</div>
							)
						) : (
							<div className="panel-body">
								<div className="note err">{snapshot.positions.error.message}</div>
							</div>
						)}
					</Panel>

					<Panel
						title="Recent rejected attempts"
						sub={ordersResult.ok ? `${rejected.rejected.length} of the last 100 orders` : "unavailable"}
						flush
						footer="A rejection is a stored order with its reason codes. No decision is reconstructed from a log."
					>
						{!ordersResult.ok ? (
							<div className="panel-body">
								<div className="note err">{ordersResult.error.message}</div>
							</div>
						) : rejected.rejected.length > 0 ? (
							<div className="table-wrap">
								<table className="table compact">
									<thead>
										<tr>
											<th>Time</th>
											<th>Order</th>
											<th>Token</th>
											<th>Side</th>
											<th className="num col-hide-sm">Amount</th>
											<th>Reason</th>
											<th>Quote</th>
										</tr>
									</thead>
									<tbody>
										{rejected.rejected.slice(0, 10).map((order) => (
											<tr key={order.id}>
												<td className="small mono">{order.created_at.slice(0, 19)}Z</td>
												<td className="mono small">{order.id}</td>
												<td>
													<Link href={`/tokens/${order.chain}/${order.token_address}`}>
														{shortAddress(order.token_address)}
													</Link>
												</td>
												<td>{order.side}</td>
												<td className="num col-hide-sm">{formatUsd(order.amount_usd)}</td>
												<td className="small err mono">{order.reason ?? "—"}</td>
												<td className="small muted">{quoteKindLabel(order.quote_kind)}</td>
											</tr>
										))}
									</tbody>
								</table>
							</div>
						) : (
							<div className="panel-body">
								<EmptyState title="No rejected orders in the last 100">
									A clean rejection history is a real result. The page does not manufacture a
									risk event to fill the panel.
								</EmptyState>
							</div>
						)}
					</Panel>
				</div>

				<div className="stack">
					<Panel title="Limit ownership" sub="server-side numbers">
						<div className="note">
							<strong>No limit read endpoint.</strong> The API enforces order size, position
							size, open-position count, daily loss, quote age, pair age, liquidity and price
							impact. The values live in the API configuration (one home, CONTRACT I7) and are
							not exposed to this page. Nothing is printed as a limit from the web bundle.
						</div>
						<div className="mt-12 stack-sm small muted">
							<div className="between">
								<span>Order / position caps</span>
								<span className="mono">API config</span>
							</div>
							<div className="between">
								<span>Quote and pair freshness</span>
								<span className="mono">API config</span>
							</div>
							<div className="between">
								<span>Daily loss gate</span>
								<span className="mono">API config</span>
							</div>
							<div className="between">
								<span>Circuit breakers</span>
								<span className="mono">not exposed</span>
							</div>
						</div>
					</Panel>

					<Panel title="Rejection reasons" sub="observed in this history">
						{ordersResult.ok ? (
							rejected.reasons.length > 0 ? (
								<div className="stack-sm">
									{rejected.reasons.map(([reason, count]) => (
										<div className="between small" key={reason}>
											<span className="mono">{reason}</span>
											<span>{count}</span>
										</div>
									))}
								</div>
							) : (
								<div className="small muted">No reason codes observed in the last 100 orders.</div>
							)
						) : (
							<div className="small muted">Order history unavailable.</div>
						)}
						<p className="note mt-12">
							Fail-closed codes such as <span className="mono">QUOTE_STALE</span>,{" "}
							<span className="mono">LIQUIDITY_UNKNOWN</span> and{" "}
							<span className="mono">NO_SELL_ROUTE</span> are recorded by the API, not
							generated by this page.
						</p>
					</Panel>

					<Panel title="Operate safely" sub="links">
						<div className="row">
							<ButtonLink href="/operations" size="sm">
								Operations
							</ButtonLink>
							<ButtonLink href="/security" size="sm">
								Security
							</ButtonLink>
							<ButtonLink href="/ledger" size="sm">
								Activity
							</ButtonLink>
						</div>
					</Panel>
				</div>
			</div>
		</AppShell>
	);
}
