import Link from "next/link";
import { notFound } from "next/navigation";
import { AppShell } from "@/components/shell";
import { TokenChart } from "@/components/token-chart";
import { Badge, ButtonLink, LiveDot, Panel } from "@/components/ui";
import { tokenPages } from "@/lib/token-fixtures";
import type { DetailRow } from "@/lib/token-fixtures";

const CHART_RANGES = ["5m", "15m", "1h", "4h", "1d"];

// One row renderer for the quote, risk-preview and market-facts tables. All values are
// fixture strings; nothing here computes money (CONTRACT I3/I10/I12).
function DetailTableRow({ row }: { row: DetailRow }) {
	return (
		<tr>
			<td className="muted">{row.k}</td>
			<td className="right">
				{row.live && <LiveDot />}
				{row.dead && <LiveDot tone="dead" />}
				<span className={`${row.plain ? "small" : "mono"}${row.err ? " err" : ""}`}>
					{row.v}
					{row.sub && <span className="sub"> {row.sub}</span>}
					{row.badge && (
						<>
							{" "}
							<Badge tone={row.badgeTone}>{row.badge}</Badge>
						</>
					)}
				</span>
			</td>
		</tr>
	);
}

export default async function TokenPage({
	params,
}: {
	params: Promise<{ chain: string; id: string }>;
}) {
	const { chain, id } = await params;
	// Fail closed on routing: only known chain/id fixture pairs resolve; anything else 404s.
	const token = tokenPages[`${chain}/${id}`];
	if (!token) notFound();

	return (
		<AppShell active="markets">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">
						<Link href="/markets">Markets</Link> / {token.chainLabel} / {token.symbol}
					</div>
					<div className="row">
						<span className="avatar-lg" style={{ fontSize: 11 }}>
							{token.avatar}
						</span>
						<div>
							<h1>
								{token.name}{" "}
								<span className="muted" style={{ fontWeight: 400 }}>
									{token.symbol}
								</span>
							</h1>
							<p className="sub">{token.sub}</p>
						</div>
					</div>
				</div>
				<div className="page-actions">
					<div style={{ textAlign: "right" }}>
						<div
							className={`mono${token.markMuted ? " muted" : ""}`}
							style={{ fontSize: 20, fontWeight: 600 }}
						>
							{token.mark}
						</div>
						<div className="small">
							{token.deltas.map((delta, index) => (
								<span key={delta.text}>
									{index > 0 && " · "}
									<span className={`delta ${delta.tone}`}>{delta.text}</span>
								</span>
							))}
						</div>
						{token.execNote && (
							<div className="small muted">
								<LiveDot tone="dead" />
								{token.execNote}
							</div>
						)}
					</div>
					<span className="badge">PAPER</span>
					<Badge tone={token.execBadge.tone}>{token.execBadge.label}</Badge>
					<button className="btn" type="button" aria-pressed="true">
						★ Watchlist
					</button>
					<Link className="btn" href="/alerts">
						Alert
					</Link>
				</div>
			</header>

			<div className="row mb-12">
				{token.badges.map((badge) => (
					<Badge key={badge.label} tone={badge.tone}>
						{badge.label}
					</Badge>
				))}
				<span style={{ flex: 1 }} />
				{token.solscan ? (
					<a
						className="small muted"
						href={token.solscan.url}
						target="_blank"
						rel="noopener noreferrer"
					>
						{token.solscan.label} ↗
					</a>
				) : (
					<span className="small muted">mint not verified</span>
				)}
			</div>

			<div className="split">
				<div className="stack">
					<div id="chart">
						<Panel
							title={`${token.symbol} / USD`}
							sub={token.chartSub}
							action={
								<div className="seg">
									{CHART_RANGES.map((range) => (
										<button key={range} type="button" aria-pressed={range === "1h"}>
											{range}
										</button>
									))}
								</div>
							}
						>
							<div className="chart-scroll">
								<TokenChart chart={token.chart} />
							</div>
							<div className="between mt-8">
								<div className="chart-legend">
									{token.chartLegend.map((item) => (
										<span key={item.label} className={item.className}>
											<i style={item.borderColor ? { borderColor: item.borderColor } : undefined} />
											{item.label}
										</span>
									))}
								</div>
								<span className="small muted">{token.lastTradeCaption}</span>
							</div>
						</Panel>
					</div>

					<div className="tabs mb-12" aria-label="Token sections">
						{token.tabs.map((tab) => (
							<a className={`tab${tab.on ? " on" : ""}`} href={`#${tab.id}`} key={tab.id}>
								{tab.label}
								{tab.count && <span className="count">{tab.count}</span>}
							</a>
						))}
					</div>

					<div id="safety">
						<Panel
							title="Safety"
							sub={`checked ${token.safety.checkedAt} · sources named per row`}
							action={<Badge tone={token.safety.badgeTone}>{token.safety.badge}</Badge>}
							flush
							footer={token.safety.foot}
						>
							<div className="table-wrap">
								<table className="table compact">
									<thead>
										<tr>
											<th>Check</th>
											<th>Result</th>
											<th>Detail</th>
											<th>Source · age</th>
										</tr>
									</thead>
									<tbody>
										{token.safety.rows.map((row) => (
											<tr key={row.check}>
												<td>{row.check}</td>
												<td>
													<Badge tone={row.resultTone}>{row.result}</Badge>
												</td>
												<td className={row.detailMono ? "mono small" : "small"}>{row.detail}</td>
												<td className="small muted">{row.source}</td>
											</tr>
										))}
									</tbody>
								</table>
							</div>
						</Panel>
					</div>

					<div id="holders">
						<Panel
							title="Holders"
							sub={token.holders.sub}
							action={<Badge tone={token.holders.badgeTone}>{token.holders.badge}</Badge>}
							flush
							footer={
								<div className="between">
									<span>{token.holders.foot.left}</span>
									<span>{token.holders.foot.right}</span>
								</div>
							}
						>
							<div className="table-wrap">
								<table className="table compact">
									<thead>
										<tr>
											<th className="num">#</th>
											<th>Address</th>
											<th>Label</th>
											<th className="num">% supply</th>
											<th className="num">Balance</th>
											<th>Type</th>
										</tr>
									</thead>
									<tbody>
										{token.holders.rows.map((row) => (
											<tr key={row.address}>
												<td className="num mono">{row.rank}</td>
												<td className="mono small">{row.address}</td>
												<td>{row.label}</td>
												<td className="num">{row.supply}</td>
												<td className="num mono">{row.balance}</td>
												<td>
													<Badge tone={row.typeTone}>{row.type}</Badge>
												</td>
											</tr>
										))}
									</tbody>
								</table>
							</div>
						</Panel>
					</div>

					<div id="liquidity">
						<Panel title="Liquidity" sub={token.liquidity.sub} flush footer={token.liquidity.foot}>
							<div className="table-wrap">
								<table className="table compact">
									<thead>
										<tr>
											<th>Pool</th>
											<th>Venue</th>
											<th className="num">TVL</th>
											<th className="num">24h vol</th>
											<th className="num">Fee tier</th>
											<th className="num">Sell 1 SOL</th>
											<th className="num">Sell 10 SOL</th>
										</tr>
									</thead>
									<tbody>
										{token.liquidity.rows.map((row) => (
											<tr key={row.pool + row.venue}>
												<td>{row.pool}</td>
												<td>{row.venue}</td>
												<td className="num">{row.tvl}</td>
												<td className="num">{row.vol}</td>
												<td className="num">{row.fee}</td>
												<td className="num">{row.sell1}</td>
												<td className="num">{row.sell10}</td>
											</tr>
										))}
									</tbody>
								</table>
							</div>
						</Panel>
					</div>

					<div id="mine">
						<Panel
							title="My position"
							sub={token.myPosition.sub}
							action={
								<ButtonLink href={token.myPosition.detailHref} size="sm">
									Open detail
								</ButtonLink>
							}
						>
							<div className="grid cols-4">
								{token.myPosition.cells.map((cell) => (
									<div key={cell.k}>
										<span className="micro">{cell.k}</span>
										{cell.badge ? (
											<div style={{ marginTop: 6 }}>
												<Badge tone={cell.badgeTone}>{cell.badge}</Badge>
											</div>
										) : (
											<div
												className={`mono${cell.vTone ? ` ${cell.vTone}` : ""}`}
												style={{ fontSize: 16 }}
											>
												{cell.v}
											</div>
										)}
										<div className="small muted">{cell.sub}</div>
									</div>
								))}
							</div>
							<div className="note mt-12">
								<strong>Note:</strong> {token.myPosition.note}
							</div>
						</Panel>
					</div>

					<Panel
						title="Recent trades"
						sub="venue tape · 10 min"
						action={<Badge tone="ghost">your fills marked</Badge>}
						flush
					>
						<div className="table-wrap">
							<table className="table compact">
								<thead>
									<tr>
										<th>Time</th>
										<th>Side</th>
										<th className="num">Price</th>
										<th className="num">Size</th>
										<th>Venue</th>
										<th>Wallet</th>
									</tr>
								</thead>
								<tbody>
									{token.recentTrades.rows.map((row) => (
										<tr key={row.time}>
											<td className="mono">{row.time}</td>
											<td>
												<Badge tone={row.side === "buy" ? "ok" : "default"}>{row.side}</Badge>
											</td>
											<td className="num mono">{row.price}</td>
											<td className="num">{row.size}</td>
											<td>{row.venue}</td>
											<td>
												{row.you ? (
													<Badge tone="info">you · Main</Badge>
												) : (
													<span className="small muted">{row.wallet}</span>
												)}
											</td>
										</tr>
									))}
								</tbody>
							</table>
						</div>
					</Panel>
				</div>

				<div className="stack">
					{/* id="quote" exists so e2e tests can scope quote-panel row assertions uniquely. */}
					<div id="quote">
						<Panel title="Quote" action={<Badge tone="ghost">{token.quote.ttl}</Badge>}>
							<div className="seg mb-12" style={{ display: "flex" }}>
								<button type="button" style={{ flex: 1 }} aria-pressed={token.quote.side === "buy"}>
									Buy
								</button>
								<button type="button" style={{ flex: 1 }} aria-pressed={token.quote.side === "sell"}>
									Sell
								</button>
							</div>

							{token.quote.payWith && (
								<label className="field mb-12">
									<span>Pay with</span>
									<div className="row">
										<input
											className="input mono"
											defaultValue={token.quote.payWith.amount}
											style={{ flex: 1 }}
											aria-label="Amount in SOL"
										/>
										<span className="tag">SOL</span>
										<button className="btn sm" type="button">
											MAX
										</button>
									</div>
									<span className="hint">{token.quote.payWith.hint}</span>
								</label>
							)}

							<table className="table compact mb-12">
								<tbody>
									{token.quote.rows.map((row) => (
										<DetailTableRow row={row} key={row.k} />
									))}
								</tbody>
							</table>

							<div className="row mb-8">
								<span className="micro">Risk preview</span>
								<span style={{ flex: 1 }} />
								<Badge tone={token.quote.riskBadgeTone}>{token.quote.riskBadge}</Badge>
							</div>
							<div
								className="table-wrap"
								style={{ border: "1px solid var(--line)", borderRadius: "var(--r-sm)" }}
							>
								<table className="table compact">
									<tbody>
										{token.quote.riskRows.map((row) => (
											<DetailTableRow row={row} key={row.k} />
										))}
									</tbody>
								</table>
							</div>

							<div className={`note ${token.quote.note.tone} mt-12`}>
								{token.quote.note.parts.map((part, index) =>
									part.href ? (
										<Link href={part.href} key={index}>
											{part.text}
										</Link>
									) : (
										<span className={part.mono ? "mono" : undefined} key={index}>
											{part.text}
										</span>
									),
								)}
							</div>

							<div className="row mt-12">
								<button
									className="btn primary lg"
									style={{ flex: 1 }}
									type="button"
									disabled
									title="Execution is not wired in M1"
								>
									{token.quote.confirmLabel}
								</button>
								<Link className="btn lg" href={token.quote.limitsHref}>
									Limits
								</Link>
							</div>
							<div className="small muted mt-8">{token.quote.foot}</div>
						</Panel>
					</div>

					<Panel title="Market facts" flush footer={token.facts.foot}>
						<div className="table-wrap">
							<table className="table compact">
								<tbody>
									{token.facts.rows.map((row) => (
										<DetailTableRow row={row} key={row.k} />
									))}
								</tbody>
							</table>
						</div>
					</Panel>
				</div>
			</div>
		</AppShell>
	);
}
