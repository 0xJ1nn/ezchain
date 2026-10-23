import Link from "next/link";
import { AppShell } from "@/components/shell";
import { ModeBadge, Unavailable } from "@/components/ops";
import { Badge, ButtonLink, Panel } from "@/components/ui";
import {
	asApiError,
	getChains,
	getMarkets,
	getMarketsOverview,
	type Chain,
	type MarketList,
} from "@/lib/api";
import { ageLabel, sourceLabel } from "@/lib/live-format";

export const dynamic = "force-dynamic";

const PROVIDER_TEXT: Record<string, { title: string; role: string; boundary: string }> = {
	dexscreener: {
		title: "DexScreener",
		role: "Market discovery: pairs, price, liquidity, volume and 1h/24h change.",
		boundary: "Discovery data is not an executable quote.",
	},
	geckoterminal: {
		title: "GeckoTerminal",
		role: "Closed OHLCV candles for token and position charts, served through the API cache.",
		boundary: "Only chains with the candles capability are served.",
	},
	jupiter: {
		title: "Jupiter",
		role: "Solana swap quote ladder. Paper fills use the same quote shape as live would.",
		boundary: "Paper only in this build; no signing path exists.",
	},
	lifi: {
		title: "LI.FI",
		role: "Aggregator quote provider registered for EVM and selected non-EVM chains.",
		boundary: "Quote capability only; no live execution.",
	},
	kyberswap: {
		title: "KyberSwap",
		role: "Aggregator quote provider in the EVM quote ladder.",
		boundary: "Quote capability only; no live execution.",
	},
	velora: {
		title: "Velora",
		role: "Aggregator quote provider in the EVM quote ladder.",
		boundary: "Quote capability only; no live execution.",
	},
	cetus: {
		title: "Cetus",
		role: "Sui quote provider in the registry.",
		boundary: "Quote capability only; no live execution.",
	},
	stonfi: {
		title: "STON.fi",
		role: "TON quote provider in the registry.",
		boundary: "Quote capability only; no live execution.",
	},
	osmosis_sqs: {
		title: "Osmosis SQS",
		role: "Osmosis quote provider in the registry.",
		boundary: "Quote capability only; no live execution.",
	},
	paper_mark: {
		title: "Paper mark",
		role: "Fallback paper valuation when a chain has no executable quote route.",
		boundary: "A mark is not a route. Sells may be unavailable and are recorded as NO_SELL_ROUTE.",
	},
};

function providerText(id: string) {
	return (
		PROVIDER_TEXT[id] ?? {
			title: id,
			role: "Quote provider registered by the API for this chain.",
			boundary: "Capability only; no live execution.",
		}
	);
}

function providerRows(chains: Chain[]) {
	const rows = new Map<string, string[]>();
	for (const chain of chains) {
		const providers = chain.capabilities.quote_providers ?? [chain.capabilities.quote_kind];
		for (const provider of providers) {
			const current = rows.get(provider) ?? [];
			current.push(chain.name);
			rows.set(provider, current);
		}
	}
	return [...rows.entries()]
		.sort(([left], [right]) => left.localeCompare(right))
		.map(([id, names]) => ({ id, chains: names }));
}

export default async function IntegrationsPage() {
	const [chainsOutcome, marketsOutcome, overviewOutcome] = await Promise.all([
		getChains()
			.then((chains) => ({ ok: true as const, chains }))
			.catch((error: unknown) => ({ ok: false as const, error: asApiError(error) })),
		getMarkets({ limit: 100 })
			.then((list) => ({ ok: true as const, list }))
			.catch((error: unknown) => ({ ok: false as const, error: asApiError(error) })),
		getMarketsOverview()
			.then((overview) => ({ ok: true as const, overview }))
			.catch((error: unknown) => ({ ok: false as const, error: asApiError(error) })),
	]);
	const mode = process.env.EZCHAIN_WEB_MODE === "fixture" ? "fixture" : "live";
	const chains = chainsOutcome.ok ? chainsOutcome.chains : [];
	const markets: MarketList | null = marketsOutcome.ok ? marketsOutcome.list : null;
	const overview = overviewOutcome.ok ? overviewOutcome.overview : null;
	const quotes = providerRows(chains);
	const candleChains = chains.filter((chain) => chain.capabilities.candles);
	const browseChains = chains.filter((chain) => chain.capabilities.browse);
	const error = !chainsOutcome.ok
		? chainsOutcome.error
		: !marketsOutcome.ok
			? marketsOutcome.error
			: null;

	return (
		<AppShell active="integrations">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">System / Integrations</div>
					<h1>Integrations</h1>
					<p className="sub">
						The data providers and quote ladders the API actually registers, with their
						capability per chain. Connections are configured server-side; this page has no
						toggle that pretends to change one.
					</p>
				</div>
				<div className="page-actions">
					<ModeBadge mode={mode} />
					<Link className="btn" href="/chains">
						Chains
					</Link>
					<Link className="btn" href="/developer">
						API overview
					</Link>
				</div>
			</header>

			{error && (
				<div className="mb-12">
					<Unavailable
						title="Provider status unavailable"
						code={error.code}
						message={error.message}
					>
						Capability rows below still come from the chain registry when it was read
						successfully.
					</Unavailable>
				</div>
			)}

			<div className="grid cols-2">
				<Panel
					title="DexScreener"
					sub="market discovery"
					action={
						markets ? (
							<Badge tone={markets.stale ? "warn" : "ok"}>
								{markets.stale ? "stale" : "available"}
							</Badge>
						) : (
							<Badge tone="err">unavailable</Badge>
						)
					}
				>
					<p className="small muted">{providerText("dexscreener").role}</p>
					<div className="hr" />
					<div className="stack-sm small">
						<div className="between">
							<span className="muted">Source name</span>
							<span className="mono">{markets ? sourceLabel(markets.source.name) : "—"}</span>
						</div>
						<div className="between">
							<span className="muted">Last observation</span>
							<span className="mono">
								{markets ? `${ageLabel(markets.source.observed_at)} ago` : "—"}
							</span>
						</div>
						<div className="between">
							<span className="muted">Rows in sample</span>
							<span className="mono">{markets ? markets.rows.length : "—"}</span>
						</div>
						<div className="between">
							<span className="muted">Chains browsable</span>
							<span className="mono">{chains.length > 0 ? browseChains.length : "—"}</span>
						</div>
					</div>
					<p className="note mt-12">{providerText("dexscreener").boundary}</p>
				</Panel>

				<Panel
					title="GeckoTerminal"
					sub="OHLCV candles"
					action={
						candleChains.length > 0 ? (
							<Badge tone="ok">available</Badge>
						) : (
							<Badge tone="ghost">not configured</Badge>
						)
					}
				>
					<p className="small muted">{providerText("geckoterminal").role}</p>
					<div className="hr" />
					<div className="small muted">
						{candleChains.length > 0 ? (
							<span className="row" style={{ gap: 4 }}>
								{candleChains.map((chain) => (
									<Badge tone="ghost" key={chain.id}>
										{chain.name}
									</Badge>
								))}
							</span>
						) : (
							"No chain in the registry advertises the candles capability."
						)}
					</div>
					<p className="note mt-12">{providerText("geckoterminal").boundary}</p>
				</Panel>
			</div>

			<Panel
				title="Quote ladders"
				sub={quotes.length > 0 ? `${quotes.length} providers registered` : "unavailable"}
				flush
				className="mt-16"
				footer="Order matters: the API asks rungs in preference order. paper_mark is a valuation fallback, not an executable route."
			>
				{quotes.length > 0 ? (
					<div className="table-wrap">
						<table className="table">
							<thead>
								<tr>
									<th>Provider</th>
									<th>Role</th>
									<th>Chains in ladder</th>
									<th>Boundary</th>
								</tr>
							</thead>
							<tbody>
								{quotes.map((provider) => {
									const text = providerText(provider.id);
									return (
										<tr key={provider.id}>
											<td>
												<span className="strong">{text.title}</span>
												<span className="sub" style={{ display: "block" }}>
													{provider.id}
												</span>
											</td>
											<td className="small muted">{text.role}</td>
											<td className="small">
												<span className="row" style={{ gap: 4 }}>
													{provider.chains.map((name) => (
														<Badge tone="ghost" key={name}>
															{name}
														</Badge>
													))}
												</span>
											</td>
											<td className="small muted">{text.boundary}</td>
										</tr>
									);
								})}
							</tbody>
						</table>
					</div>
				) : (
					<div className="panel-body">
						<div className="note warn">
							The registry returned no quote provider rows. No provider is listed without a
							real capability row.
						</div>
					</div>
				)}
			</Panel>

			<div className="grid cols-2 mt-16">
				<Panel title="Coverage by chain" sub="market monitor" flush>
					{overview ? (
						<div className="table-wrap">
							<table className="table compact">
								<thead>
									<tr>
										<th>Chain</th>
										<th>Status</th>
										<th className="num">Rows</th>
										<th>Age</th>
									</tr>
								</thead>
								<tbody>
									{overview.chains.map((chain) => (
										<tr key={chain.id}>
											<td>{chain.name}</td>
											<td>
												<Badge
													tone={
														chain.status === "ok"
															? "ok"
															: chain.status === "error"
																? "err"
																: chain.status === "stale"
																	? "warn"
																	: "ghost"
													}
												>
													{chain.status.replace("_", " ")}
												</Badge>
											</td>
											<td className="num">{chain.rows}</td>
											<td className="small">
												{chain.last_observed_at ? `${ageLabel(chain.last_observed_at)} ago` : "—"}
											</td>
										</tr>
									))}
								</tbody>
							</table>
						</div>
					) : (
						<div className="panel-body">
							<div className="note warn">
								{overviewOutcome.ok ? "No coverage rows." : overviewOutcome.error.message}
							</div>
						</div>
					)}
				</Panel>

				<Panel title="Not connected by this page" sub="honest boundary">
					<div className="list">
						<div className="list-row">
							<Badge tone="warn">NO TOGGLES</Badge>
							<span className="grow">
								<span className="title">Provider credentials and enablement</span>
								<span className="sub">
									Server-side configuration only. A browser switch could not change the API
									process safely.
								</span>
							</span>
						</div>
						<div className="list-row">
							<Badge tone="warn">NO WEBHOOKS</Badge>
							<span className="grow">
								<span className="title">Webhook endpoints and delivery log</span>
								<span className="sub">No outbound webhook surface exists in this build.</span>
							</span>
						</div>
						<div className="list-row">
							<Badge tone="warn">NO CEX</Badge>
							<span className="grow">
								<span className="title">Exchange account connections</span>
								<span className="sub">No CEX read-only adapter is registered here.</span>
							</span>
						</div>
					</div>
					<div className="row mt-12">
						<ButtonLink href="/settings" size="sm">
							Runtime settings
						</ButtonLink>
						<ButtonLink href="/security" size="sm">
							Secret handling
						</ButtonLink>
					</div>
				</Panel>
			</div>
		</AppShell>
	);
}
