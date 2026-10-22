import Link from "next/link";
import { AppShell } from "@/components/shell";
import { EmptyState, FixtureBadge, ModeBadge, Unavailable } from "@/components/ops";
import { Badge, ButtonLink, Panel, Stat } from "@/components/ui";
import { formatUsd, formatUsdPrice, shortAddress } from "@/lib/live-format";
import { getPaperSnapshot } from "@/lib/paper-api";

export const dynamic = "force-dynamic";

const custody = [
	{
		tier: "Paper ledger",
		status: "In use",
		tone: "ok" as const,
		detail: "Simulated USD cash and positions. No chain keys, no signing, no withdrawal.",
	},
	{
		tier: "Local hot wallet",
		status: "Not built",
		tone: "warn" as const,
		detail:
			"Only services/executor may decrypt key material. This build has no vault, key import or signing path.",
	},
	{
		tier: "Watch-only wallet",
		status: "Not built",
		tone: "warn" as const,
		detail: "No address book, balance sync or ownership proof is exposed by the API.",
	},
	{
		tier: "Hardware wallet",
		status: "Not built",
		tone: "warn" as const,
		detail: "No device bridge or approval flow exists in this build.",
	},
];

export default async function WalletsPage() {
	const snapshot = await getPaperSnapshot();
	const mode = snapshot.mode;
	const account = snapshot.account.ok ? snapshot.account.data : null;
	const positions = snapshot.positions.ok ? snapshot.positions.data : [];
	const error = !snapshot.account.ok
		? snapshot.account.error
		: !snapshot.positions.ok
			? snapshot.positions.error
			: null;

	return (
		<AppShell active="wallets">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">Trade / Wallets</div>
					<h1>Wallets</h1>
					<p className="sub">
						The paper account is the only balance this build can prove. Wallet custody,
						address sync and live balances are not built, so this page shows the paper truth
						and the exact custody status instead of an empty wallet list.
					</p>
				</div>
				<div className="page-actions">
					<ModeBadge mode={mode} />
					<Link className="btn" href="/security">
						Custody rules
					</Link>
				</div>
			</header>

			{error && (
				<div className="mb-12">
					<Unavailable
						title="Paper balances unavailable"
						code={error.code}
						message={error.message}
						action={
							<Link className="small" href="/wallets">
								Retry →
							</Link>
						}
					>
						No wallet balance is fabricated to fill the gap.
					</Unavailable>
				</div>
			)}

			<div className="note warn mb-12">
				<strong>No keys here.</strong> This page never receives key material. There is no
				backend to add, import or connect a wallet in this build, so there is deliberately no
				&quot;Add wallet&quot; control. Live custody is not available.
			</div>

			<section className="kpi-band mb-12" aria-label="Paper balances">
				<Stat
					label="Paper cash"
					value={account ? formatUsd(account.cash_usd) : "—"}
					meta={account ? "PAPER · ledger cash" : "unavailable"}
				/>
				<Stat
					label="Paper equity"
					value={account ? formatUsd(account.equity_usd) : "—"}
					meta={account ? "PAPER · cash + known marks" : "unavailable"}
				/>
				<Stat
					label="Open positions"
					value={snapshot.positions.ok ? String(positions.length) : "—"}
					meta="PAPER · server view model"
				/>
				<Stat
					label="On-chain wallets"
					value="0"
					meta="no custody backend in this build"
					tone="warn"
				/>
			</section>

			<div className="split">
				<div className="stack">
					<Panel
						title="Paper positions"
						sub={snapshot.positions.ok ? `${positions.length} · from the ledger` : "unavailable"}
						action={mode === "fixture" ? <FixtureBadge /> : <Badge tone="ghost">PAPER</Badge>}
						flush
						footer="Balances here are paper ledger state, not chain balances. Marks come from the server with their source and age."
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
												<th className="num col-hide-sm">Mark</th>
												<th className="num col-hide-sm">Value</th>
												<th>Mark source</th>
											</tr>
										</thead>
										<tbody>
											{positions.map((position) => (
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
													<td className="num col-hide-sm">{formatUsdPrice(position.mark_usd)}</td>
													<td className="num col-hide-sm">{formatUsd(position.market_value_usd)}</td>
													<td>
														{position.source ? (
															<Badge tone="ghost">{position.source.name}</Badge>
														) : (
															<Badge tone="warn">unknown mark</Badge>
														)}
													</td>
												</tr>
											))}
										</tbody>
									</table>
								</div>
							) : (
								<div className="panel-body">
									<EmptyState
										title="No paper positions"
										links={[{ href: "/markets", label: "Browse markets", primary: true }]}
									>
										The paper account is flat. A paper buy from a market or token page creates
										the first position.
									</EmptyState>
								</div>
							)
						) : (
							<div className="panel-body">
								<div className="note err">{snapshot.positions.error.message}</div>
							</div>
						)}
					</Panel>

					<Panel title="What is available today" sub="paper only">
						<div className="list">
							<div className="list-row">
								<Badge tone="ok">READY</Badge>
								<span className="grow">
									<span className="title">Paper trading</span>
									<span className="sub">
										Discovery, quotes, risk checks, fills and an append-only paper ledger.
									</span>
								</span>
								<ButtonLink href="/markets" size="sm">
									Markets
								</ButtonLink>
							</div>
							<div className="list-row">
								<Badge tone="ok">READY</Badge>
								<span className="grow">
									<span className="title">Execution history</span>
									<span className="sub">Orders, fills and rejected reason codes.</span>
								</span>
								<ButtonLink href="/ledger" size="sm">
									Activity
								</ButtonLink>
							</div>
							<div className="list-row">
								<Badge tone="ok">READY</Badge>
								<span className="grow">
									<span className="title">Local export</span>
									<span className="sub">
										CSV built in the browser from the rows the API already returned.
									</span>
								</span>
								<ButtonLink href="/reports" size="sm">
									Reports
								</ButtonLink>
							</div>
							<div className="list-row">
								<Badge tone="warn">NOT BUILT</Badge>
								<span className="grow">
									<span className="title">Live balances and custody</span>
									<span className="sub">
										No wallet sync, key import, signing, withdrawal or hardware approval.
									</span>
								</span>
							</div>
						</div>
					</Panel>
				</div>

				<div className="stack">
					<Panel title="Custody status" sub="what exists" action={<Badge tone="ghost">NO KEYS</Badge>}>
						<div className="list">
							{custody.map((row) => (
								<div className="list-row" key={row.tier}>
									<Badge tone={row.tone}>{row.status}</Badge>
									<span className="grow">
										<span className="title">{row.tier}</span>
										<span className="sub">{row.detail}</span>
									</span>
								</div>
							))}
						</div>
						<p className="note mt-12">
							Key material belongs only in the executor process. The web app, API responses
							and this page never hold a key, seed phrase or signing secret.
						</p>
					</Panel>

					<Panel title="Why there is no wallet list" sub="honest state">
						<p className="small muted">
							A wallet list needs an address model, ownership metadata, chain sync and a
							custody backend. None of those exist in this branch. Showing invented wallet
							names or balances would break the rule that every number has a source.
						</p>
						<div className="row mt-12">
							<ButtonLink href="/security" size="sm">
								Security posture
							</ButtonLink>
							<ButtonLink href="/settings" size="sm">
								Runtime settings
							</ButtonLink>
						</div>
					</Panel>
				</div>
			</div>
		</AppShell>
	);
}
