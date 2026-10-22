import Link from "next/link";
import { AppShell } from "@/components/shell";
import { EquityChart } from "@/components/equity-chart";
import { Badge, ButtonLink, LiveDot, Meter, Panel, Stat, TokenAvatar } from "@/components/ui";
import {
	activity,
	attribution,
	automations,
	chains,
	equity,
	openPositions,
	risk,
	signals,
	wallets,
} from "@/lib/dashboard-fixtures";

export default function DashboardPage() {
	return (
		<AppShell active="dashboard">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">Overview / Dashboard</div>
					<h1>Dashboard</h1>
					<p className="sub">
						One screen to answer: am I safe, am I up, and is anything broken? Every number is
						labelled with its mode and source.
					</p>
				</div>
				<div className="page-actions">
					<span className="chip">
						Fixture snapshot
					</span>
					<Link className="btn" href="/ledger">
						Export
					</Link>
					<Link className="btn primary" href="/markets">
						New order
					</Link>
				</div>
			</header>

			<section className="kpi-band mb-12">
				<Stat
					hero
					label="Equity"
					value={equity.value}
					meta={
						<>
							<span className="delta up">{equity.delta}</span> 24h ·{" "}
							<span className="mono">{equity.sol}</span>
						</>
					}
				/>
				<Stat
					label="Realized (7d)"
					value={equity.realized}
					tone="ok"
					meta={equity.realizedMeta}
				/>
				<Stat
					label="Unrealized"
					value={equity.unrealized}
					tone={equity.unrealizedTone}
					meta={equity.unrealizedMeta}
				/>
				<Stat label="Drawdown" value={equity.drawdown} meta={equity.drawdownMeta} />
				<Stat
					label="Exposure"
					value={equity.exposure}
					meta={equity.exposureMeta}
					title="Includes wallet balances, not only the four open positions"
				/>
				<Stat
					label="Mode"
					value={equity.mode}
					meta={<Badge>{equity.modeMeta}</Badge>}
				/>
			</section>

			<div className="split">
				<div className="stack">
					<Panel
						title="Equity"
						sub="USD · after fees · paper"
						action={
							<div className="seg">
								<button type="button">1D</button>
								<button type="button" aria-pressed="true">
									1W
								</button>
								<button type="button">1M</button>
								<button type="button">ALL</button>
							</div>
						}
					>
						<div className="chart-scroll">
							<EquityChart />
						</div>
						<div className="between mt-8">
							<div className="chart-legend">
								<span>
									<i style={{ borderColor: "var(--ink)" }} />
									Equity
								</span>
								<span>
									<i style={{ borderColor: "var(--line-2)" }} />
									Cost basis
								</span>
								<span>
									<i style={{ borderColor: "var(--err)" }} />
									Max drawdown −6.2%
								</span>
							</div>
							<span className="small muted">Marks: 1s · snapshots hourly</span>
						</div>
					</Panel>

					<Panel
						title="Open positions"
						sub="4 · sorted by next action"
						action={<ButtonLink href="/positions" size="sm">All positions</ButtonLink>}
						flush
						footer={
							<div className="between">
								<span>* last executable value; not marked to zero.</span>
								<Link href="/positions">Open the tile grid →</Link>
							</div>
						}
					>
						<div className="table-wrap">
							<table className="table">
								<thead>
									<tr>
										<th>Token</th>
										<th>State</th>
										<th className="num" title="Realized + unrealized for this position">PnL</th>
										<th className="num">Value</th>
										<th className="col-hide-sm">Next</th>
										<th className="col-hide-sm">Mark</th>
										<th />
									</tr>
								</thead>
								<tbody>
									{openPositions.map((p) => (
										<tr className="rowlink" key={p.id}>
											<td>
												<span className="row">
													<TokenAvatar size="sm">{p.symbol}</TokenAvatar>
													<span>
														<span className="strong">{p.name}</span>
														<span className="sub" style={{ display: "block" }}>
															{p.chain} · {p.strategy}
														</span>
													</span>
												</span>
											</td>
											<td>
												<Badge tone={p.stateTone}>{p.state}</Badge>
											</td>
											<td
												className={`num ${
													p.pnlPct.startsWith("+")
														? "ok"
														: p.pnlPct.startsWith("−")
															? "err"
															: "muted"
												}`}
											>
												{p.pnlPct}
												{p.pnlUsd && <span className="sub pnl-sub">{p.pnlUsd}</span>}
											</td>
											<td className={`num ${p.valueTone === "muted" ? "muted" : ""}`}>
												{p.value}
											</td>
											<td className={`small col-hide-sm ${p.nextTone === "err" ? "err" : ""}`}>
												{p.next}
											</td>
											<td className="col-hide-sm">
												<span className={`small ${p.markTone === "dead" ? "err" : ""}`}>
													<LiveDot tone={p.markTone} />
													{p.markAge}
												</span>
											</td>
											<td className="right">
												<Link className="btn sm" href={`/positions/${p.id}`}>
													Open
												</Link>
											</td>
										</tr>
									))}
								</tbody>
							</table>
						</div>
					</Panel>

					<Panel
						title="PnL attribution"
						sub="7 days · after fees · paper"
						action={<ButtonLink href="/ledger" size="sm">Ledger</ButtonLink>}
						flush
						footer="Attribution uses realized cash PnL; unrealized is excluded. Share is net PnL ÷ total net PnL and can exceed 100% when another strategy loses."
					>
						<div className="table-wrap">
							<table className="table compact">
								<thead>
									<tr>
										<th>Strategy</th>
										<th className="col-hide-sm">Mode</th>
										<th className="num col-hide-sm">Trades</th>
										<th className="num col-hide-sm">Win rate</th>
										<th className="num col-hide-sm">Fees</th>
										<th className="num">Net PnL</th>
										<th className="num">Share</th>
									</tr>
								</thead>
								<tbody>
									{attribution.map((row) => (
										<tr key={row.strategy}>
											<td>{row.strategy}</td>
											<td className="col-hide-sm">
												<Badge tone={row.strategy === "Manual" ? "ghost" : "default"}>
													{row.mode}
												</Badge>
											</td>
											<td className="num col-hide-sm">{row.trades}</td>
											<td className="num col-hide-sm">{row.winRate}</td>
											<td className="num col-hide-sm">{row.fees}</td>
											<td className={`num ${row.netTone}`}>{row.netPnl}</td>
											<td className="num">{row.share}</td>
										</tr>
									))}
								</tbody>
							</table>
						</div>
					</Panel>
				</div>

				<div className="stack">
					<Panel
						title="Risk"
						sub="live checks"
						action={<ButtonLink href="/risk" size="sm">Limits</ButtonLink>}
					>
						<div className="stack-sm">
							<div className="between small">
								<span>Daily loss</span>
								<span className="mono">{risk.dailyLoss}</span>
							</div>
							<Meter width={risk.dailyLossWidth} tone="warn" label="Daily loss" valueText="$42 of $250" />
							<div className="between small">
								<span>Exposure</span>
								<span
									className="mono"
									title="Includes wallet balances, not only the four open positions"
								>
									{risk.exposure}
								</span>
							</div>
							<Meter width={risk.exposureWidth} label="Exposure" valueText="38% of the 60% cap, including wallet balances" />
							<div className="between small">
								<span>Single-token cap</span>
								<span className="mono">{risk.singleToken}</span>
							</div>
							<Meter width={risk.singleTokenWidth} tone="ok" label="Single-token cap" valueText="Bonk 3.7% of the 10% cap" />
							<div className="hr" />
							<div className="list-row">
								<Badge tone="ok">OK</Badge>
								<span className="grow">
									<span className="title">All 6 breakers closed</span>
									<span className="sub">fixture snapshot</span>
								</span>
							</div>
							<div className="list-row">
								<Badge tone="warn">1</Badge>
								<span className="grow">
									<span className="title">Token without a sell route</span>
									<span className="sub">Scamcoin · blocks new intents for the token</span>
								</span>
								<ButtonLink href="/positions" size="sm">Review</ButtonLink>
							</div>
							<Link className="btn danger block mt-8" href="/risk">
								Open kill switch
							</Link>
							<div className="micro center">halts new risk · does not auto-sell</div>
						</div>
					</Panel>

					<Panel
						title="Automations"
						sub="3 active"
						action={<ButtonLink href="/automations" size="sm">All</ButtonLink>}
						flush
					>
						{automations.map((a) => (
							<div className="list-row" style={{ padding: "9px 12px" }} key={a.title}>
								<Badge tone={a.tone}>{a.status}</Badge>
								<span className="grow">
									<span className="title">{a.title}</span>
									<span className="sub">{a.sub}</span>
								</span>
								<ButtonLink href={a.href} size="sm">{a.action}</ButtonLink>
							</div>
						))}
					</Panel>

					<Panel
						title="Signals"
						sub="routed"
						action={<ButtonLink href="/signals" size="sm">Feed</ButtonLink>}
						flush
					>
						{signals.map((s) => (
							<div className="list-row" style={{ padding: "9px 12px" }} key={s.title}>
								<Badge tone={s.tone}>{s.tag}</Badge>
								<span className="grow">
									<span className="title">{s.title}</span>
									<span className="sub">{s.sub}</span>
								</span>
							</div>
						))}
					</Panel>

					<Panel
						title="Chains"
						sub="3 connected"
						action={<ButtonLink href="/chains" size="sm">Health</ButtonLink>}
						flush
					>
						{chains.map((c) => (
							<div className="list-row" style={{ padding: "9px 12px" }} key={c.name}>
								<span className="chain">
									<span className="sq">{c.initial}</span>
									{c.name}
								</span>
								<span className="grow small muted">{c.detail}</span>
								<Badge tone={c.tone}>{c.status}</Badge>
							</div>
						))}
					</Panel>
				</div>
			</div>

			<section className="grid cols-2 mt-16">
				<Panel
					title="Wallets"
					sub="4 wallets · 2 custody tiers"
					action={<ButtonLink href="/wallets" size="sm">Manage</ButtonLink>}
					flush
				>
					{wallets.map((w) => (
						<div className="wallet-row" key={w.name}>
							<span className="avatar-lg">{w.initial}</span>
							<span className="grow">
								<span className="title strong">{w.name}</span>
								<span className="sub">{w.chains}</span>
							</span>
							<Badge tone={w.badgeTone}>{w.badge}</Badge>
							<span className="bal">
								<span className="v">{w.value}</span>
								<span className="sub">{w.sync}</span>
							</span>
						</div>
					))}
				</Panel>

				<Panel
					title="Recent activity"
					sub="ledger view"
					action={<ButtonLink href="/ledger" size="sm">All</ButtonLink>}
					flush
				>
					{activity.map((a) => (
						<div className="list-row" style={{ padding: "9px 12px" }} key={a.title}>
							<Badge tone={a.actionTone}>{a.action}</Badge>
							<span className="grow">
								<span className="title">{a.title}</span>
								<span className="sub">{a.sub}</span>
							</span>
							<span className={`mono small ${a.amountTone === "ok" ? "ok" : ""}`}>
								{a.amount}
							</span>
						</div>
					))}
				</Panel>
			</section>
		</AppShell>
	);
}
