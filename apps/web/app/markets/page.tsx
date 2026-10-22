import Link from "next/link";
import { AppShell } from "@/components/shell";
import { MarketSpark } from "@/components/market-spark";
import { Badge, LiveDot, Panel } from "@/components/ui";
import { chainTabs, marketRows, newPairs } from "@/lib/markets-fixtures";

export default function MarketsPage() {
	return (
		<AppShell active="markets">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">Overview / Markets</div>
					<h1>Markets</h1>
					<p className="sub">
						Screen venues by what you can actually trade. Liquidity, safety and holder
						counts carry their source and age; a token with no executable sell route is
						labelled, not ranked.
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
				{chainTabs.map((tab) => (
					<button
						key={tab.id}
						className={`tab${tab.active ? " on" : ""}`}
						type="button"
						aria-pressed={tab.active}
						title={tab.active ? "Showing every chain" : "Filter not wired in M1"}
					>
						{tab.label} <span className="count">{tab.count}</span>
					</button>
				))}
				<Link className="tab" href="/chains">
					Tiers &amp; RPC →
				</Link>
			</div>

			<Panel
				flush
				footer={
					<div className="between" style={{ flexWrap: "wrap" }}>
						<span>
							Sources: Jupiter v6 + Raydium pool reserves (Solana) · Uniswap v3 /
							Aerodrome (Base, Ethereum, BNB) · dexscreener indexer for mcap/vol ·
							Helius + Etherscan for holders.
						</span>
						<span className="small">
							Freshness: quotes ≤ 3s · pools ≤ 9s · holders ≤ 2m · <LiveDot />
							sweep ok
						</span>
					</div>
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
							className="select"
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
							className="select"
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
							className="select"
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
							className="select"
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
							className="select"
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
					<span className="spacer" style={{ flex: 1 }} />
					<input
						className="input mono"
						type="search"
						placeholder="Search symbol / mint"
						aria-label="Search symbol or mint"
						style={{ width: 150 }}
						disabled
						title="Search not wired in M1"
					/>
					<span className="row">
						<span
							className="toggle on"
							role="switch"
							aria-checked="true"
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
						Trade
					</Link>
				</div>
				<div className="table-wrap" style={{ maxHeight: 560 }}>
					<table className="table" style={{ minWidth: 960 }}>
						<thead>
							<tr>
								<th>Token</th>
								<th>Chain</th>
								<th className="num">Price</th>
								<th className="num">1h</th>
								<th className="num">24h</th>
								<th className="num">MCap</th>
								<th className="num">Liq</th>
								<th className="num">Vol 24h</th>
								<th className="num">Holders</th>
								<th>Safety</th>
								<th>7d</th>
								<th>Your position</th>
							</tr>
						</thead>
						<tbody>
							{marketRows.map((row) => (
								<tr key={row.id}>
									<td>
										<Link className="row" href={row.tokenHref}>
											<span
												className="token"
												style={{ width: 22, height: 22, fontSize: 8 }}
											>
												{row.avatar}
											</span>
											<span>
												<span
													className={`strong${row.symbolTone ? ` ${row.symbolTone}` : ""}`}
												>
													{row.symbol}
												</span>
												<span className="sub" style={{ display: "block" }}>
													{row.sub}
												</span>
											</span>
										</Link>
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
									<td className="num">{row.vol}</td>
									<td className="num">{row.holders}</td>
									<td>
										<Badge tone={row.safetyTone}>{row.safety}</Badge>
									</td>
									<td>
										<MarketSpark
											values={row.spark}
											tone={row.sparkTone}
											label={row.sparkLabel}
										/>
									</td>
									<td>
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
					<span>
						12 of 4,182 markets · filters: liq ≥ $500K, mcap ≥ $1M, safety pass · your
						positions and watchlist are pinned even when they fail a filter
					</span>
					<span>Sort: 24h volume ↓ · page 1/349</span>
				</div>
			</Panel>

			<div className="split mt-16">
				<Panel
					title="New pairs"
					sub="last 60m · safety gate runs before a row is listed"
					action={<Badge tone="ghost">auto-refresh 15s</Badge>}
					flush
					footer={
						<span>
							A new pair is listed with whatever is known. Unknown liquidity or holders
							prints <span className="mono">—</span>, never a guess.
						</span>
					}
				>
					<div className="table-wrap">
						<table className="table compact" style={{ minWidth: 620 }}>
							<thead>
								<tr>
									<th>Age</th>
									<th>Token</th>
									<th>Chain</th>
									<th className="num">Liq</th>
									<th className="num">MCap</th>
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
										<td className="num">{pair.liq}</td>
										<td className="num" title={pair.mcapTitle}>
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
							<span>Watchlist: 3 tokens · 1 with an active position</span>
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
