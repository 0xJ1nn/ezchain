import Link from "next/link";
import { notFound } from "next/navigation";
import { AppShell } from "@/components/shell";
import { EmptyState, ModeBadge, Unavailable } from "@/components/ops";
import { Badge, Panel, Stat } from "@/components/ui";
import { formatUsd, formatUsdPrice, shortAddress } from "@/lib/live-format";
import {
	getPaperSnapshot,
	getPaperWalletCompositions,
	getPaperWallets,
	type PaperResult,
	type PaperWallet,
} from "@/lib/paper-api";

export const dynamic = "force-dynamic";

// Everything on this page comes from a server field. Allowances, live activity, custody
// keys and sweep are deliberately described as unbuilt: the API exposes no such field and
// inventing one would put a number on screen with no source.

function demoError<T>(message: string): PaperResult<T> {
	return {
		ok: false,
		mode: "fixture",
		error: { code: "FIXTURE_DEMO_WALLET_ERROR", message },
	};
}

const UNBUILT = [
	{
		label: "Allowances",
		detail:
			"No allowance or approval field is returned by the paper API. Nothing is shown as unlimited by default.",
	},
	{
		label: "Activity",
		detail:
			"Wallet-attributed activity is not exposed. The ledger's order and fill history is the full record.",
	},
	{
		label: "Policy",
		detail:
			"Per-wallet limits are not a server field here. Risk limits apply to the workspace, not to a paper wallet.",
	},
	{
		label: "Key custody",
		detail:
			"No key, seed, address or signing secret reaches this page. The executor is the only process that could hold one.",
	},
	{
		label: "Sweep",
		detail:
			"There is no sweep or withdrawal path in this build. A paper cash balance is a ledger sum, not a spendable chain balance.",
	},
];

export default async function WalletDetailPage({
	params,
	searchParams,
}: {
	params: Promise<{ id: string }>;
	searchParams: Promise<{ state?: string }>;
}) {
	const [{ id: rawId }, query] = await Promise.all([params, searchParams]);
	const id = decodeURIComponent(rawId);
	const [snapshot, walletsResult, compositionsResult] = await Promise.all([
		getPaperSnapshot(),
		getPaperWallets(),
		getPaperWalletCompositions(),
	]);
	const mode = snapshot.mode;

	// Fixture-only demo of a failed read, the same convention the positions page uses.
	// A live deployment never masks a real failure with a demo flag.
	const wallets =
		mode === "fixture" && query.state === "error"
			? demoError<PaperWallet[]>("Fixture demo: the paper wallet request failed.")
			: walletsResult;

	if (!wallets.ok) {
		return (
			<AppShell active="wallets">
				<header className="page-head">
					<div className="titles">
						<div className="crumbs">Trade / Wallets / Detail</div>
						<h1>Wallet unavailable</h1>
						<p className="sub">
							The wallet read failed. No detail is rendered from a failed request and no
							balance is guessed.
						</p>
					</div>
					<div className="page-actions">
						<ModeBadge mode={mode} />
						<Link className="btn" href="/wallets">
							Back to wallets
						</Link>
					</div>
				</header>
				<Unavailable
					title="Wallet detail unavailable"
					code={wallets.error.code}
					message={wallets.error.message}
					action={
						<Link className="small" href="/wallets">
							Retry →
						</Link>
					}
				>
					The wallet list is hidden when its read fails; the link above returns to the list.
				</Unavailable>
			</AppShell>
		);
	}

	const wallet = wallets.data.find((row) => (row.id ?? "unallocated") === id);
	if (!wallet) notFound();

	const compositions = compositionsResult.ok ? compositionsResult.data : [];
	const composition =
		compositions.find((row) => (row.wallet_id ?? "unallocated") === id) ?? null;
	const declaredChains = new Set(wallets.data.flatMap((row) => (row.id ? [row.chain] : [])));
	const positions = snapshot.positions.ok
		? snapshot.positions.data.filter((position) =>
				id === "unallocated"
					? !declaredChains.has(position.chain)
					: position.chain === wallet.chain,
			)
		: [];

	return (
		<AppShell active="wallets">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">Trade / Wallets / {wallet.label}</div>
					<h1>{wallet.label}</h1>
					<p className="sub">
						A paper wallet is a ledger attribution. The figures below are the server&apos;s
						settled sum and its composition; this page never derives a balance, attaches a key or
						shows an address it was not given.
					</p>
				</div>
				<div className="page-actions">
					<ModeBadge mode={mode} />
					<Badge tone="ghost">PAPER</Badge>
					<Link className="btn" href="/wallets">
						All wallets
					</Link>
				</div>
			</header>

			<section className="kpi-band mb-12" aria-label="Wallet balances">
				<Stat
					label="Settled cash"
					value={formatUsd(wallet.balance_usd)}
					meta={`PAPER · ${wallet.chain} · ${wallet.asset}`}
				/>
				<Stat
					label="Positions value"
					value={composition?.positions_usd == null ? "—" : formatUsd(composition.positions_usd)}
					meta={
						composition
							? composition.mark_status === "unknown"
								? "withheld — a mark is unknown"
								: "server-composed · not executable"
							: "composition not returned"
					}
				/>
				<Stat
					label="Wallet value"
					value={composition?.market_value_usd == null ? "—" : formatUsd(composition.market_value_usd)}
					meta={composition?.share_pct == null ? "share unknown" : `${composition.share_pct}% of equity`}
				/>
				<Stat
					label="Wallet id"
					value={<span className="mono small">{wallet.id ?? "unallocated"}</span>}
					meta={mode === "fixture" ? "sample server row" : "from /api/v1/paper/wallets"}
				/>
			</section>

			{!compositionsResult.ok && (
				<div className="mb-12">
					<Unavailable
						title="Wallet composition unavailable"
						code={compositionsResult.error.code}
						message={compositionsResult.error.message}
					>
						Composition values stay —; the settled cash above is a separate server read.
					</Unavailable>
				</div>
			)}

			<div className="split">
				<div className="stack">
					<Panel
						title="Positions on this wallet"
						sub={snapshot.positions.ok ? `${positions.length} server rows` : "positions unavailable"}
						flush
						footer="Positions are the server's open rows for this wallet's chain. Values are the view model the API returned, not a client recomputation."
					>
						{snapshot.positions.ok ? (
							positions.length > 0 ? (
								<div className="table-wrap">
									<table className="table">
										<caption className="sr-only">Wallet positions</caption>
										<thead>
											<tr>
												<th>Token</th>
												<th className="num">Quantity</th>
												<th className="num">Mark</th>
												<th className="num">Value</th>
												<th>Source</th>
											</tr>
										</thead>
										<tbody>
											{positions.map((position) => (
												<tr key={`${position.chain}:${position.token_address}`}>
													<td>
														<Link
															href={`/tokens/${position.chain}/${position.token_address}`}
														>
															<span className="strong">{position.symbol}</span>
														</Link>
														<span className="sub" style={{ display: "block" }}>
															{shortAddress(position.token_address)}
														</span>
													</td>
													<td className="num mono">{position.quantity}</td>
													<td className="num mono">
														{formatUsdPrice(position.mark_usd)}
													</td>
													<td className="num mono">
														{formatUsd(position.market_value_usd)}
													</td>
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
										title={id === "unallocated" ? "No unallocated positions" : "No positions on this chain"}
										links={[{ href: "/markets", label: "Browse markets", primary: true }]}
									>
										The server returned no open position for this wallet. A paper order on
										this chain creates the first one.
									</EmptyState>
								</div>
							)
						) : (
							<div className="panel-body">
								<Unavailable
									title="Positions unavailable"
									code={snapshot.positions.error.code}
									message={snapshot.positions.error.message}
								/>
							</div>
						)}
					</Panel>
				</div>

				<div className="stack">
					<Panel title="What this wallet is" sub="server fields only">
						<div className="list">
							<div className="list-row">
								<Badge tone="ghost">CHAIN</Badge>
								<span className="grow">
									<span className="title mono">{wallet.chain}</span>
									<span className="sub">The chain this paper wallet settles.</span>
								</span>
							</div>
							<div className="list-row">
								<Badge tone="ghost">ASSET</Badge>
								<span className="grow">
									<span className="title mono">{wallet.asset}</span>
									<span className="sub">The ledger asset of the settled cash.</span>
								</span>
							</div>
							<div className="list-row">
								<Badge tone="ghost">MODE</Badge>
								<span className="grow">
									<span className="title mono">{wallet.mode}</span>
									<span className="sub">Every balance here is paper, never a live balance.</span>
								</span>
							</div>
						</div>
					</Panel>

					<Panel title="Not built" sub="no server field to show" action={<Badge tone="warn">NO KEYS</Badge>}>
						<div className="list">
							{UNBUILT.map((row) => (
								<div className="list-row" key={row.label}>
									<Badge tone="warn">NOT BUILT</Badge>
									<span className="grow">
										<span className="title">{row.label}</span>
										<span className="sub">{row.detail}</span>
									</span>
								</div>
							))}
						</div>
					</Panel>
				</div>
			</div>
		</AppShell>
	);
}
