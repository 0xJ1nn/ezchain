import Link from "next/link";
import { AppShell } from "@/components/shell";
import { MarketSpark } from "@/components/market-spark";
import { Badge, Panel } from "@/components/ui";
import { chainTabs, marketRows, newPairs, type MarketRow } from "@/lib/markets-fixtures";
import styles from "./markets.module.css";

function TokenCell({ row }: { row: MarketRow }) {
	const content = (
		<>
			<span className="token" style={{ width: 22, height: 22, fontSize: 8 }}>
				{row.avatar}
			</span>
			<span>
				<span className={`strong${row.symbolTone ? ` ${row.symbolTone}` : ""}`}>
					{row.symbol}
				</span>
				<span className="sub" style={{ display: "block" }}>
					{row.sub}
				</span>
			</span>
		</>
	);

	// Only tokens with a built detail route render as links; everything else stays text.
	return row.tokenHref ? (
		<Link className="row" href={row.tokenHref}>
			{content}
		</Link>
	) : (
		<span className="row">{content}</span>
	);
}

export default function MarketsPage() {
	return (
		<AppShell active="markets">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">Overview / Markets</div>
					<h1>Markets</h1>
					<p className="sub">
						Screen venues by what you can actually trade. This page renders the M1
						fixture snapshot: market cap, liquidity and holder values are
						pre-formatted fixture strings, not a live feed. A token with no executable
						sell route is labelled, not ranked.
					</p>
				</div>
				<div className="page-actions">
					<span className="chip">Fixture snapshot</span>
					<Link className="btn" href="/signals">
						Signal feed
					</Link>
					<Link className="btn primary" href="/tokens/solana/bonk">
						Open BONK
					</Link>
				</div>
			</header>

			<div className="tabs mb-12" aria-label="Market chains" style={{ whiteSpace: "nowrap" }}>
				{chainTabs.map((tab) =>
					tab.active ? (
						<span
							key={tab.id}
							className="tab on"
							title="Showing every chain in the fixture snapshot"
						>
							{tab.label} <span className="count">{tab.count}</span>
						</span>
					) : (
						<button
							key={tab.id}
							className={`tab ${styles.disabledTab}`}
							type="button"
							disabled
							aria-disabled="true"
							title="Filter not wired in M1"
						>
							{tab.label} <span className="count">{tab.count}</span>
						</button>
					),
				)}
				<Link className="tab" href="/chains">
					Tiers &amp; RPC →
				</Link>
			</div>

			<Panel
				flush
				footer={
					<span>
						Fixture snapshot: no live feed in M1 · freshness not tracked · values are a
						snapshot, not a quote.
					</span>
				}
			>
				<div
					className="table-toolbar"
					style={{
						position: "sticky",
						top: 52,
						zIndex: 4,
						background: "var(--surface)",
						borderRadius: "var(--r) var(--r) 0 0",
					}}
				>
					<span className="field" style={{ minWidth: 118 }}>
						<select
							className={`select ${styles.disabledField}`}
							disabled
							aria-label="Minimum liquidity"
							defaultValue="Liq ≥ $500K"
							title="Filter not wired in M1"
						>
							<option>Liq ≥ $500K</option>
							<option>Liq ≥ $100K</option>
							<option>Liq ≥ $1M</option>
							<option>Any liquidity</option>
						</select>
					</span>
					<span className="field" style={{ minWidth: 112 }}>
						<select
							className={`select ${styles.disabledField}`}
							disabled
							aria-label="Minimum market cap"
							defaultValue="MCap ≥ $1M"
							title="Filter not wired in M1"
						>
							<option>MCap ≥ $1M</option>
							<option>MCap ≥ $10M</option>
							<option>MCap ≥ $100M</option>
							<option>Any mcap</option>
						</select>
					</span>
					<span className="field" style={{ minWidth: 100 }}>
						<select
							className={`select ${styles.disabledField}`}
							disabled
							aria-label="Age"
							defaultValue="Age: any"
							title="Filter not wired in M1"
						>
							<option>Age: any</option>
							<option>Age &lt; 1h</option>
							<option>Age &lt; 24h</option>
							<option>Age &gt; 7d</option>
						</select>
					</span>
					<span className="field" style={{ minWidth: 126 }}>
						<select
							className={`select ${styles.disabledField}`}
							disabled
							aria-label="Safety"
							defaultValue="Safety: pass only"
							title="Filter not wired in M1"
						>
							<option>Safety: pass only</option>
							<option>Safety: pass + warn</option>
							<option>Safety: any</option>
						</select>
					</span>
					<span className="field" style={{ minWidth: 132 }}>
						<select
							className={`select ${styles.disabledField}`}
							disabled
							aria-label="Sort"
							defaultValue="Sort: 24h volume"
							title="Filter not wired in M1"
						>
							<option>Sort: 24h volume</option>
							<option>Sort: 24h change</option>
							<option>Sort: liquidity</option>
							<option>Sort: mcap</option>
							<option>Sort: holders</option>
						</select>
					</span>
					<span className="small muted">Filters are preview-only in M1</span>
					<span className="spacer" style={{ flex: 1 }} />
					<input
						className={`input mono ${styles.disabledField}`}
						type="search"
						placeholder="Search symbol / mint"
						aria-label="Search symbol or mint"
						style={{ width: 150 }}
						disabled
						title="Search not wired in M1"
					/>
					<span className="row">
						<span
							className="toggle"
							role="switch"
							aria-checked="false"
							aria-disabled="true"
							aria-label="Watchlist only"
							title="Filter not wired in M1"
						/>
						<span className="small">Watchlist</span>
					</span>
					<Link
						className="btn sm"
						href="/tokens/solana/bonk"
						title="Opens BONK; the trade ticket is not wired in M1"
					>
						Trade BONK
					</Link>
				</div>
				<div className="table-wrap" style={{ maxHeight: 560 }}>
					<table className="table">
						<thead>
							<tr>
								<th>Token</th>
								<th>Chain</th>
								<th className="num">Price</th>
								<th className="num">1h</th>
								<th className="num">24h</th>
								<th className="num">MCap</th>
								<th className="num">Liq</th>
								<th className="num col-hide-sm">Vol 24h</th>
								<th className="num col-hide-sm">Holders</th>
								<th>Safety</th>
								<th className="col-hide-sm">7d</th>
								<th className="col-hide-sm">Your position</th>
							</tr>
						</thead>
						<tbody>
							{marketRows.map((row) => (
								<tr key={row.id}>
									<td>
										<TokenCell row={row} />
									</td>
									<td>
										<span className="chain">
											<span className="sq">{row.chainInitial}</span>
											{row.chain}
										</span>
									</td>
									<td className="num mono">{row.price}</td>
									<td className={`num ${row.change1h.tone}`}>{row.change1h.value}</td>
									<td className={`num ${row.change24h.tone}`}>
										{row.change24h.value}
									</td>
									<td className="num">{row.mcap}</td>
									<td className={`num${row.liqTone ? ` ${row.liqTone}` : ""}`}>
										{row.liq}
									</td>
									<td className="num col-hide-sm">{row.vol}</td>
									<td className="num col-hide-sm">{row.holders}</td>
									<td>
										<Badge tone={row.safetyTone}>{row.safety}</Badge>
									</td>
									<td className="col-hide-sm">
										<MarketSpark
											values={row.spark}
											tone={row.sparkTone}
											label={row.sparkLabel}
										/>
									</td>
									<td className="col-hide-sm">
										{row.position.kind === "open" && (
											<Link className="row" href={row.position.href}>
												<Badge tone={row.position.badgeTone}>
													{row.position.badge}
												</Badge>
												{row.position.value && (
													<span
														className={`mono small${row.position.valueTone ? ` ${row.position.valueTone}` : ""}`}
													>
														{row.position.value}
													</span>
												)}
											</Link>
										)}
										{row.position.kind === "closed" && (
											<span className="small muted">
												closed · {row.position.value}
											</span>
										)}
										{row.position.kind === "none" && (
											<span className="small muted">—</span>
										)}
									</td>
								</tr>
							))}
						</tbody>
					</table>
				</div>
				<div className="table-foot">
					<span>{marketRows.length} fixture rows · filters preview-only in M1</span>
					<span>Fixture order · page 1/1</span>
				</div>
			</Panel>

			<div className="split mt-16">
				<Panel
					title="New pairs"
					sub="last 60m · safety gate runs before a row is listed"
					action={<Badge tone="ghost">fixture snapshot</Badge>}
					flush
					footer={
						<span>
							A new pair is listed with whatever is known. Unknown liquidity, market
							cap or holders prints <span className="mono">—</span>, never a guess.
						</span>
					}
				>
					<div className="table-wrap">
						<table className="table compact">
							<thead>
								<tr>
									<th>Age</th>
									<th>Token</th>
									<th>Chain</th>
									<th className="num col-hide-sm">Liq</th>
									<th className="num col-hide-sm">MCap</th>
									<th>Gate</th>
									<th>Your call</th>
								</tr>
							</thead>
							<tbody>
								{newPairs.map((pair) => (
									<tr key={pair.symbol}>
										<td className="mono">{pair.age}</td>
										<td>
											{pair.symbol} <span className="sub">{pair.sub}</span>
										</td>
										<td>{pair.chain}</td>
										<td className="num col-hide-sm">{pair.liq}</td>
										<td className="num col-hide-sm" title={pair.mcapTitle}>
											{pair.mcap}
										</td>
										<td>
											<Badge tone={pair.gateTone}>{pair.gate}</Badge>
										</td>
										<td>
											{pair.call.kind === "quote" ? (
												<button
													className="btn sm"
													type="button"
													disabled
													title="Quote ticket not wired in M1"
												>
													Quote
												</button>
											) : (
												<span className="small muted">{pair.call.label}</span>
											)}
										</td>
									</tr>
								))}
							</tbody>
						</table>
					</div>
				</Panel>

				<Panel
					title="Screen definition"
					sub="what the table is allowed to claim"
					footer={
						<div className="between" style={{ flexWrap: "wrap" }}>
							<span>Watchlist: not wired in M1</span>
							<Link className="small" href="/alerts">
								Price alerts →
							</Link>
						</div>
					}
				>
					<div className="stack-sm small muted">
						<div>
							• <span className="ink">Price</span> is the last executable mid at ≤
							$1,000 size, not a headline ticker.
						</div>
						<div>
							• <span className="ink">Safety</span> = mint/freeze authority, honeypot
							simulation, LP lock, top-10 concentration. Failing tokens stay listed
							with a reason.
						</div>
						<div>
							• <span className="ink">Your position</span> links to the position,
							never to a new order.
						</div>
						<div>
							• A market with no sell route is not tradeable; the trade button in the
							token page is disabled with that reason.
						</div>
						<div>
							• Tier-3 chains are read-only: prices shown, execution disabled.{" "}
							<Link href="/chains">Chain matrix →</Link>
						</div>
					</div>
				</Panel>
			</div>
		</AppShell>
	);
}
