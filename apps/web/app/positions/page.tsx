import Link from "next/link";
import { AppShell } from "@/components/shell";
import { PositionTile } from "@/components/position-tile";
import { Badge, ButtonLink, Panel, Stat, TokenAvatar } from "@/components/ui";
import { closedPositions, positionsSummary, positionTiles } from "@/lib/position-fixtures";

export default function PositionsPage() {
	return (
		<AppShell active="positions">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">Overview / Positions</div>
					<h1>Positions</h1>
					<p className="sub">
						Every open position as one tile. PnL, mark freshness, ladder state and
						executable value come from the server view model — the tile never recomputes
						money. Actions are preview-only in M1.
					</p>
				</div>
				<div className="page-actions">
					<div className="seg" role="group" aria-label="Density">
						<button type="button" aria-pressed="true">
							Comfortable
						</button>
						<button type="button" aria-pressed="false">
							Compact
						</button>
					</div>
					<Link className="btn" href="/ledger">
						Closed trades
					</Link>
					<Link className="btn primary" href="/markets">
						New order
					</Link>
				</div>
			</header>

			<section className="kpi-band mb-12">
				<Stat label="Open positions" value={positionsSummary.open} meta={positionsSummary.openMeta} />
				<Stat
					label="Executable value"
					value={positionsSummary.executableValue}
					meta={positionsSummary.executableMeta}
				/>
				<Stat
					label="Unrealized"
					value={positionsSummary.unrealized}
					tone="err"
					meta={positionsSummary.unrealizedMeta}
				/>
				<Stat label="Exposure" value={positionsSummary.exposure} meta={positionsSummary.exposureMeta} />
				<Stat label="Realized today" value={positionsSummary.realizedToday} tone="ok" meta={positionsSummary.realizedMeta} />
				<Stat
					label="Needs attention"
					value={positionsSummary.needsAttention}
					tone="err"
					meta={positionsSummary.needsAttentionMeta}
				/>
			</section>

			<div className="tabs mb-12" role="tablist" aria-label="Position views">
				<button className="tab on" type="button" role="tab" aria-selected="true">
					Open <span className="count">4</span>
				</button>
				<button className="tab" type="button" role="tab" aria-selected="false" title="Filter not wired in M1">
					Unsellable <span className="count">1</span>
				</button>
				<button className="tab" type="button" role="tab" aria-selected="false" title="Filter not wired in M1">
					Closed <span className="count">28</span>
				</button>
				<button className="tab" type="button" role="tab" aria-selected="false" title="Filter not wired in M1">
					Watchlist <span className="count">6</span>
				</button>
			</div>

			<section className="panel mb-12">
				<div className="table-toolbar">
					<span className="field" style={{ minWidth: 120 }}>
						<select className="select" aria-label="Filter by chain" defaultValue="All chains">
							<option>All chains</option>
							<option>Solana</option>
							<option>Base</option>
							<option>Ethereum</option>
						</select>
					</span>
					<span className="field" style={{ minWidth: 150 }}>
						<select className="select" aria-label="Filter by strategy" defaultValue="All strategies">
							<option>All strategies</option>
							<option>Momentum v1.2</option>
							<option>DCA weekly</option>
							<option>Manual</option>
						</select>
					</span>
					<span className="field" style={{ minWidth: 130 }}>
						<select className="select" aria-label="Filter by wallet" defaultValue="All wallets">
							<option>All wallets</option>
							<option>Main (hot)</option>
							<option>Ledger (hw)</option>
						</select>
					</span>
					<span className="field" style={{ minWidth: 130 }}>
						<select className="select" aria-label="Sort positions" defaultValue="Sort: PnL %">
							<option>Sort: PnL %</option>
							<option>Sort: value</option>
							<option>Sort: age</option>
							<option>Sort: next level</option>
						</select>
					</span>
					<span className="spacer" style={{ flex: 1 }} />
					<span className="micro">Group by</span>
					<div className="seg" role="group" aria-label="Group positions">
						<button type="button" aria-pressed="true">
							None
						</button>
						<button type="button" aria-pressed="false">
							Strategy
						</button>
						<button type="button" aria-pressed="false">
							Wallet
						</button>
						<button type="button" aria-pressed="false">
							Chain
						</button>
					</div>
					<span className="badge ghost">fixture snapshot</span>
				</div>
				<div className="table-foot">
					<span>4 open · 1 needs attention · sample marks</span>
					<span>
						USD totals use the sample SOL/USD mark <span className="mono">$147.62</span>
					</span>
				</div>
			</section>

			<section className="grid cols-2 tiles" id="open">
				{positionTiles.map((position) => (
					<PositionTile position={position} key={position.id} />
				))}
			</section>

			<section className="panel mt-16" id="closed">
				<div className="panel-head">
					<h2>Recently closed</h2>
					<span className="sub">28 in the last 7 days</span>
					<div className="right">
						<ButtonLink href="/ledger" size="sm">
							Open ledger
						</ButtonLink>
					</div>
				</div>
				<div className="table-wrap">
					<table className="table">
						<thead>
							<tr>
								<th>Closed</th>
								<th>Token</th>
								<th className="col-hide-sm">Chain</th>
								<th className="col-hide-sm">Strategy</th>
								<th className="num col-hide-sm">In</th>
								<th className="num col-hide-sm">Out</th>
								<th className="num">PnL</th>
								<th className="num col-hide-sm">Hold</th>
								<th>Exit reason</th>
							</tr>
						</thead>
						<tbody>
							{closedPositions.map((row) => (
								<tr className="rowlink" key={row.closed + row.symbol}>
									<td className="mono">{row.closed}</td>
									<td>
										<span className="row">
											<TokenAvatar size="sm">{row.symbol}</TokenAvatar>
											{row.name}
										</span>
									</td>
									<td className="col-hide-sm">{row.chain}</td>
									<td className="col-hide-sm">{row.strategy}</td>
									<td className="num col-hide-sm">{row.inUsd}</td>
									<td className="num col-hide-sm">{row.outUsd}</td>
									<td className={`num ${row.pnlTone}`}>
										{row.pnl} <span className="sub">{row.pnlPct}</span>
									</td>
									<td className="num col-hide-sm">{row.hold}</td>
									<td>
										<Badge tone={row.exitTone}>{row.exit}</Badge>
									</td>
								</tr>
							))}
						</tbody>
					</table>
				</div>
				<div className="panel-foot between">
					<span>Exit reasons are machine codes; hover for the rule that fired.</span>
					<span className="row">
						<Badge tone="ghost">PAPER</Badge>
						<Badge tone="ghost">fees included</Badge>
					</span>
				</div>
			</section>

			<section className="grid cols-3 mt-16">
				<Panel title="What the tile must never do">
					<div className="stack-sm small muted">
						<div>
							• Show <span className="mono">0</span> for an unknown value. Unknown is{" "}
							<span className="mono">—</span>.
						</div>
						<div>• Present an expired quote as a live mark.</div>
						<div>• Recompute PnL on the client.</div>
						<div>• Hide an unsellable position behind an unrealized number.</div>
					</div>
				</Panel>
				<Panel title="Tile states">
					<div className="row small">
						<Badge>entering</Badge>
						<Badge>pre-bank</Badge>
						<Badge>trail-armed</Badge>
						<Badge tone="info">banked</Badge>
						<Badge tone="info">riding</Badge>
						<Badge>laddered</Badge>
						<Badge tone="err">unsellable</Badge>
						<Badge tone="warn">dust</Badge>
						<Badge tone="ghost">closed</Badge>
					</div>
				</Panel>
				<Panel title="Reference">
					<div className="stack-sm small">
						<Link href="/components">Component anatomy →</Link>
						<Link href="/states">All states →</Link>
						<Link href="/positions/bonk">Position detail →</Link>
					</div>
				</Panel>
			</section>
		</AppShell>
	);
}
