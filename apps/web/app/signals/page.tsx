import Link from "next/link";
import { AppShell } from "@/components/shell";
import { EmptyState, ModeBadge, Unavailable } from "@/components/ops";
import { Badge, Panel } from "@/components/ui";
import {
	asApiError,
	getChains,
	getMarkets,
	type Chain,
	type MarketRow,
} from "@/lib/api";
import {
	ageLabel,
	chainInitial,
	changeText,
	changeTone,
	formatPrice,
	initials,
	sourceLabel,
} from "@/lib/live-format";

export const dynamic = "force-dynamic";

function numeric(value: string | null): number | null {
	if (value === null) return null;
	const parsed = Number(value);
	return Number.isFinite(parsed) ? parsed : null;
}

function rank(rows: MarketRow[], direction: "up" | "down", window: "h1" | "h24") {
	return rows
		.filter((row) => {
			const value = numeric(window === "h1" ? row.price_change_h1 : row.price_change_h24);
			return value !== null && (direction === "up" ? value > 0 : value < 0);
		})
		.sort((a, b) => {
			const left = numeric(window === "h1" ? a.price_change_h1 : a.price_change_h24) ?? 0;
			const right = numeric(window === "h1" ? b.price_change_h1 : b.price_change_h24) ?? 0;
			return direction === "up" ? right - left : left - right;
		})
		.slice(0, 10);
}

function MoverTable({
	rows,
	chains,
	window,
}: {
	rows: MarketRow[];
	chains: Chain[];
	window: "h1" | "h24";
}) {
	if (rows.length === 0) {
		return (
			<div className="panel-body">
				<div className="small muted">
					No row in this discovery sample moved in this direction over the selected window.
					That is an empty result, not a zero.
				</div>
			</div>
		);
	}
	return (
		<div className="table-wrap">
			<table className="table">
				<thead>
					<tr>
						<th>Token</th>
						<th>Chain</th>
						<th className="num">{window === "h1" ? "1h" : "24h"}</th>
						<th className="num col-hide-sm">Price</th>
						<th>Source · age</th>
						<th />
					</tr>
				</thead>
				<tbody>
					{rows.map((row) => {
						const value = window === "h1" ? row.price_change_h1 : row.price_change_h24;
						return (
							<tr key={`${row.chain}:${row.pair_address}`}>
								<td>
									<span className="row">
										<span className="token sm" aria-hidden="true">
											{initials(row.symbol)}
										</span>
										<span>
											<span className="strong">{row.symbol}</span>
											<span className="sub" style={{ display: "block" }}>
												{row.name} · {row.dex}
											</span>
										</span>
									</span>
								</td>
								<td>
									<span className="chain">
										<span className="sq">{chainInitial(chains.find((c) => c.id === row.chain)?.name ?? row.chain)}</span>
										{chains.find((c) => c.id === row.chain)?.name ?? row.chain}
									</span>
								</td>
								<td className={`num mono ${changeTone(value)}`}>{changeText(value)}</td>
								<td className="num col-hide-sm">{formatPrice(row.price_usd)}</td>
								<td className="small">
									{sourceLabel(row.source.name)}
									<span className="sub" style={{ display: "block" }}>
										{ageLabel(row.source.observed_at)} ago
									</span>
								</td>
								<td className="right">
									<Link className="btn sm" href={`/tokens/${row.chain}/${row.base_token}`}>
										Open
									</Link>
								</td>
							</tr>
						);
					})}
				</tbody>
			</table>
		</div>
	);
}

export default async function SignalsPage() {
	const [marketsOutcome, chainsOutcome] = await Promise.all([
		getMarkets({ limit: 100, sort: "trending" })
			.then((list) => ({ ok: true as const, list }))
			.catch((error: unknown) => ({ ok: false as const, error: asApiError(error) })),
		getChains()
			.then((chains) => ({ ok: true as const, chains }))
			.catch((error: unknown) => ({ ok: false as const, error: asApiError(error) })),
	]);
	const mode = process.env.EZCHAIN_WEB_MODE === "fixture" ? "fixture" : "live";
	const list = marketsOutcome.ok ? marketsOutcome.list : null;
	const chains = chainsOutcome.ok ? chainsOutcome.chains : [];
	const rows = list?.rows ?? [];
	const error = marketsOutcome.ok ? null : marketsOutcome.error;
	const up1h = rank(rows, "up", "h1");
	const down1h = rank(rows, "down", "h1");
	const up24h = rank(rows, "up", "h24");
	const down24h = rank(rows, "down", "h24");

	return (
		<AppShell active="signals">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">Automate / Signals</div>
					<h1>Signals</h1>
					<p className="sub">
						Market movers derived from the discovery feed: the strongest positive and
						negative 1h and 24h changes in the current sample. This is a read-only view of
						price changes, not a strategy signal service and not a safety rating.
					</p>
				</div>
				<div className="page-actions">
					<ModeBadge mode={mode} />
					<Link className="btn" href="/markets">
						Markets
					</Link>
					<Link className="btn" href="/chains">
						Coverage
					</Link>
				</div>
			</header>

			{error && (
				<div className="mb-12">
					<Unavailable
						title="Signals unavailable"
						code={error.code}
						message={error.message}
						action={
							<Link className="small" href="/signals">
								Retry →
							</Link>
						}
					>
						No mover is invented when the discovery feed fails.
					</Unavailable>
				</div>
			)}

			{list && (
				<div className="note mb-12">
					<strong>Population.</strong> {rows.length} discovery rows from{" "}
					{sourceLabel(list.source.name)}, observed{" "}
					<span className="mono">{ageLabel(list.source.observed_at)}</span> ago. Ranking is by
					the feed&apos;s own price-change fields. Prices are discovery marks, not executable
					quotes, and the API returns no token-safety claim here.
					{list.stale && <span className="warn"> The feed marks this sample stale.</span>}
				</div>
			)}

			{marketsOutcome.ok && rows.length === 0 ? (
				<Panel title="No discovery rows" sub="empty" flush>
					<div className="panel-body">
						<EmptyState title="The feed returned no tokens" links={[{ href: "/markets", label: "Markets" }]}>
							An empty feed is shown as empty. No sample mover is substituted.
						</EmptyState>
					</div>
				</Panel>
			) : (
				<div className="grid cols-2">
					<Panel
						title="Gainers · 1h"
						sub={`${up1h.length} in sample`}
						action={<Badge tone="ok">UP</Badge>}
						flush
					>
						<MoverTable rows={up1h} chains={chains} window="h1" />
					</Panel>
					<Panel
						title="Losers · 1h"
						sub={`${down1h.length} in sample`}
						action={<Badge tone="err">DOWN</Badge>}
						flush
					>
						<MoverTable rows={down1h} chains={chains} window="h1" />
					</Panel>
					<Panel
						title="Gainers · 24h"
						sub={`${up24h.length} in sample`}
						action={<Badge tone="ok">UP</Badge>}
						flush
					>
						<MoverTable rows={up24h} chains={chains} window="h24" />
					</Panel>
					<Panel
						title="Losers · 24h"
						sub={`${down24h.length} in sample`}
						action={<Badge tone="err">DOWN</Badge>}
						flush
					>
						<MoverTable rows={down24h} chains={chains} window="h24" />
					</Panel>
				</div>
			)}

			<div className="split mt-16">
				<Panel title="What a signal is here" sub="no generic rules engine">
					<div className="list">
						<div className="list-row">
							<Badge tone="ghost">REAL</Badge>
							<span className="grow">
								<span className="title">Price-change ranking</span>
								<span className="sub">
									Sourced from /api/v1/markets with the row&apos;s chain, source and
									observation time.
								</span>
							</span>
						</div>
						<div className="list-row">
							<Badge tone="warn">NOT BUILT</Badge>
							<span className="grow">
								<span className="title">Routing, suppression and delivery</span>
								<span className="sub">
									There is no signal rule object, channel delivery or suppression log in this
									build.
								</span>
							</span>
						</div>
						<div className="list-row">
							<Badge tone="warn">NOT CLAIMED</Badge>
							<span className="grow">
								<span className="title">Safety assessment</span>
								<span className="sub">
									The discovery payload carries price, liquidity and volume only. This page
									does not label a token safe or unsafe.
								</span>
							</span>
						</div>
					</div>
				</Panel>

				<Panel title="How to use this" sub="operator note">
					<p className="small muted">
						Use the mover list to find what changed, then open the token page for the pair
						ladder and quote panel. A large change is not an order instruction: the order path
						still applies server-side price, liquidity, pair-age and quote-freshness gates.
					</p>
					<div className="row mt-12">
						<Link className="btn sm" href="/markets">
							Open markets
						</Link>
						<Link className="btn sm" href="/alerts">
							Alerts
						</Link>
					</div>
					{list && (
						<p className="micro mt-12">
							Source {list.source.name} · observed {list.source.observed_at}
						</p>
					)}
				</Panel>
			</div>
		</AppShell>
	);
}
