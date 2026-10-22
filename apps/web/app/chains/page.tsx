import Link from "next/link";
import { AppShell } from "@/components/shell";
import { ModeBadge, Unavailable } from "@/components/ops";
import { Badge, ButtonLink, Panel } from "@/components/ui";
import {
	asApiError,
	getChains,
	getMarketsOverview,
	type Chain,
	type MarketChainOverview,
} from "@/lib/api";
import { ageLabel } from "@/lib/live-format";

export const dynamic = "force-dynamic";

function statusTone(status: MarketChainOverview["status"]): "ok" | "warn" | "err" | "ghost" {
	if (status === "ok") return "ok";
	if (status === "stale") return "warn";
	if (status === "error") return "err";
	return "ghost";
}

function statusLabel(status: MarketChainOverview["status"]) {
	return status.replace("_", " ");
}

function ladder(chain: Chain) {
	const providers = chain.capabilities.quote_providers ?? [chain.capabilities.quote_kind];
	return providers.map((provider) => (provider === "paper_mark" ? "paper mark" : provider));
}

export default async function ChainsPage() {
	const [chainsOutcome, overviewOutcome] = await Promise.all([
		getChains()
			.then((chains) => ({ ok: true as const, chains }))
			.catch((error: unknown) => ({ ok: false as const, error: asApiError(error) })),
		getMarketsOverview()
			.then((overview) => ({ ok: true as const, overview }))
			.catch((error: unknown) => ({ ok: false as const, error: asApiError(error) })),
	]);
	const mode = process.env.EZCHAIN_WEB_MODE === "fixture" ? "fixture" : "live";
	const chains = chainsOutcome.ok ? chainsOutcome.chains : [];
	const overview = overviewOutcome.ok ? overviewOutcome.overview : null;
	const byId = new Map((overview?.chains ?? []).map((chain) => [chain.id, chain]));
	const covered = overview?.totals.covered ?? null;

	return (
		<AppShell active="chains">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">System / Chains &amp; RPC</div>
					<h1>Chains &amp; RPC</h1>
					<p className="sub">
						The registered chain registry plus what the market monitor currently sees on each
						chain. Capabilities and quote ladders come from the API; this page does not probe
						an RPC or infer a chain&apos;s health.
					</p>
				</div>
				<div className="page-actions">
					<ModeBadge mode={mode} />
					<Link className="btn" href="/integrations">
						Providers
					</Link>
					<Link className="btn" href="/signals">
						Movers
					</Link>
				</div>
			</header>

			{!chainsOutcome.ok && (
				<div className="mb-12">
					<Unavailable
						title="Chain registry unavailable"
						code={chainsOutcome.error.code}
						message={chainsOutcome.error.message}
						action={
							<Link className="small" href="/chains">
								Retry →
							</Link>
						}
					>
						No chain row is shown when the registry read fails.
					</Unavailable>
				</div>
			)}

			{!overviewOutcome.ok && (
				<div className="mb-12">
					<Unavailable
						title="Market coverage unavailable"
						code={overviewOutcome.error.code}
						message={overviewOutcome.error.message}
					>
						The registry below is still the API answer. Coverage cells stay{" "}
						<span className="mono">—</span>; they are not filled with a guessed status.
					</Unavailable>
				</div>
			)}

			<section className="kpi-band mb-12" aria-label="Coverage summary">
				<div className="stat">
					<div className="label">Registry</div>
					<div className="value sm">{chainsOutcome.ok ? chains.length : "—"}</div>
					<div className="meta">registered chains</div>
				</div>
				<div className="stat">
					<div className="label">Covered</div>
					<div className="value sm ok">{covered ?? "—"}</div>
					<div className="meta">fresh persisted rows</div>
				</div>
				<div className="stat">
					<div className="label">Stale</div>
					<div className="value sm">{overview?.totals.stale ?? "—"}</div>
					<div className="meta">older than the scan window</div>
				</div>
				<div className="stat">
					<div className="label">No data</div>
					<div className="value sm">{overview?.totals.no_data ?? "—"}</div>
					<div className="meta">scan saw no pairs</div>
				</div>
				<div className="stat">
					<div className="label">Errors</div>
					<div className="value sm">{overview?.totals.error ?? "—"}</div>
					<div className="meta">latest attempt failed</div>
				</div>
				<div className="stat">
					<div className="label">Snapshots</div>
					<div className="value sm">{overview?.totals.snapshots ?? "—"}</div>
					<div className="meta">persisted token observations</div>
				</div>
			</section>

			<Panel
				title="Chain registry"
				sub={chainsOutcome.ok ? `${chains.length} chains · ${covered ?? "—"} covered` : "unavailable"}
				action={
					overview ? (
						<Badge tone="ghost" title="Overview observation time">
							as of {ageLabel(overview.as_of)} ago
						</Badge>
					) : undefined
				}
				flush
				footer="Status is the market monitor's latest observation, not an RPC ping. An error cell carries the API's error text; no status is inferred from the chain name."
			>
				{chainsOutcome.ok ? (
					chains.length > 0 ? (
						<div className="table-wrap">
							<table className="table">
								<thead>
									<tr>
										<th>Chain</th>
										<th>Family / tier</th>
										<th>Coverage</th>
										<th className="num col-hide-sm">Rows</th>
										<th className="col-hide-sm">Last observed</th>
										<th>Quote ladder</th>
										<th>Candles</th>
										<th className="col-hide-sm">Execution</th>
										<th>Last error</th>
									</tr>
								</thead>
								<tbody>
									{chains.map((chain) => {
										const state = byId.get(chain.id);
										return (
											<tr key={chain.id}>
												<td>
													<span className="strong">{chain.name}</span>
													<span className="sub" style={{ display: "block" }}>
														{chain.id} · {chain.native_asset}
													</span>
												</td>
												<td className="small">
													{chain.family}
													<span className="sub" style={{ display: "block" }}>
														{chain.tier}
													</span>
												</td>
												<td>
													{state ? (
														<Badge tone={statusTone(state.status)}>
															{statusLabel(state.status)}
														</Badge>
													) : (
														<Badge tone="ghost">—</Badge>
													)}
												</td>
												<td className="num col-hide-sm">{state?.rows ?? "—"}</td>
												<td className="small col-hide-sm">
													{state?.last_observed_at ? `${ageLabel(state.last_observed_at)} ago` : "—"}
												</td>
												<td className="small">
													<span className="row" style={{ gap: 4 }}>
														{ladder(chain).map((provider) => (
															<Badge tone="ghost" key={provider}>
																{provider}
															</Badge>
														))}
													</span>
													<span className="micro">
														{chain.capabilities.executable_quotes
															? "executable quotes"
															: "paper mark only"}
													</span>
												</td>
												<td>
													<Badge tone={chain.capabilities.candles ? "ok" : "ghost"}>
														{chain.capabilities.candles ? "OHLCV" : "not served"}
													</Badge>
												</td>
												<td className="col-hide-sm">
													<span className="row" style={{ gap: 4 }}>
														<Badge tone={chain.capabilities.paper_execute ? "info" : "ghost"}>
															paper
														</Badge>
														{chain.capabilities.live_execute ? (
															<Badge tone="err">live</Badge>
														) : (
															<span className="micro">no live</span>
														)}
													</span>
												</td>
												<td className="small muted">
													{state?.last_error ? (
														<span className="err">{state.last_error}</span>
													) : (
														"—"
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
							<div className="note warn">The API returned an empty chain registry.</div>
						</div>
					)
				) : (
					<div className="panel-body">
						<div className="note err">{chainsOutcome.error.message}</div>
					</div>
				)}
			</Panel>

			<div className="grid cols-2 mt-16">
				<Panel title="Capability meanings" sub="registry flags">
					<div className="list">
						<div className="list-row">
							<Badge tone="ghost">browse</Badge>
							<span className="grow">
								<span className="title">Discovery available</span>
								<span className="sub">Market rows can be read from the feed for this chain.</span>
							</span>
						</div>
						<div className="list-row">
							<Badge tone="ghost">paper_execute</Badge>
							<span className="grow">
								<span className="title">Paper order path</span>
								<span className="sub">
									The API accepts a paper order if risk and quote gates pass.
								</span>
							</span>
						</div>
						<div className="list-row">
							<Badge tone="ghost">candles</Badge>
							<span className="grow">
								<span className="title">OHLCV served</span>
								<span className="sub">
									The token page can request closed candles for this chain.
								</span>
							</span>
						</div>
						<div className="list-row">
							<Badge tone="ghost">live_execute</Badge>
							<span className="grow">
								<span className="title">Live signing</span>
								<span className="sub">
									No chain in this build advertises a signing path.
								</span>
							</span>
						</div>
					</div>
				</Panel>

				<Panel title="What this page cannot show" sub="missing read models">
					<div className="note warn">
						No RPC endpoint list, gas price, finality depth or reorg status is exposed by this
						build. Those wireframe sections are absent rather than filled with plausible
						numbers.
					</div>
					<div className="row mt-12">
						<ButtonLink href="/operations" size="sm">
							Service health
						</ButtonLink>
						<ButtonLink href={`/developer`} size="sm">
							API overview
						</ButtonLink>
					</div>
				</Panel>
			</div>
		</AppShell>
	);
}
