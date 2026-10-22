import Link from "next/link";
import { AppShell } from "@/components/shell";
import { Badge, Panel } from "@/components/ui";
import {
	asApiError,
	getChains,
	getMarkets,
	isLiveMode,
	type ApiError,
	type Chain,
	type MarketRow,
	type MarketSort,
} from "@/lib/api";
import { ModeBanner } from "@/lib/live-banner";
import {
	ageLabel,
	chainInitial,
	changeText,
	changeTone,
	formatUsdPrice,
	formatUsdCompact,
	initials,
	pairAgeLabel,
	shortAddress,
	sourceLabel,
} from "@/lib/live-format";
import styles from "./markets.module.css";

export const dynamic = "force-dynamic";

type SearchParams = Record<string, string | string[] | undefined>;

const SORTS: { id: MarketSort; label: string }[] = [
	{ id: "trending", label: "24h volume (trending)" },
	{ id: "liquidity", label: "Liquidity" },
	{ id: "new", label: "Newest pair" },
];

function one(value: string | string[] | undefined): string {
	return Array.isArray(value) ? (value[0] ?? "") : (value ?? "");
}

function isSort(value: string): value is MarketSort {
	return value === "trending" || value === "liquidity" || value === "new";
}

function readLimit(value: string): number {
	const parsed = Number.parseInt(value, 10);
	if (!Number.isFinite(parsed)) return 50;
	return Math.min(100, Math.max(1, parsed));
}

interface MarketParams {
	chain: string;
	q: string;
	sort: MarketSort;
	limit: number;
}

function marketsHref(params: MarketParams, patch: Partial<MarketParams> = {}): string {
	const merged = { ...params, ...patch };
	const query = new URLSearchParams();
	if (merged.chain !== "all") query.set("chain", merged.chain);
	if (merged.q) query.set("q", merged.q);
	if (merged.sort !== "trending") query.set("sort", merged.sort);
	if (merged.limit !== 50) query.set("limit", String(merged.limit));
	const suffix = query.toString();
	return suffix ? `/markets?${suffix}` : "/markets";
}

function TokenCell({ row }: { row: MarketRow }) {
	return (
		<Link className="row" href={`/tokens/${row.chain}/${row.base_token}`}>
			<span className="token" style={{ width: 22, height: 22, fontSize: 8 }} aria-hidden="true">
				{initials(row.symbol)}
			</span>
			<span>
				<span className="strong">{row.symbol}</span>
				<span className="sub" style={{ display: "block" }}>
					{row.name} · {shortAddress(row.base_token)}
				</span>
			</span>
		</Link>
	);
}

function Change({ value }: { value: string | null }) {
	const tone = changeTone(value);
	return <span className={`mono small ${tone === "muted" ? "muted" : tone}`}>{changeText(value)}</span>;
}

function ErrorPanel({ error, retryHref }: { error: ApiError; retryHref: string }) {
	const rateLimited = error.code === "RATE_LIMITED" || error.status === 429;
	return (
		<Panel
			title={rateLimited ? "Rate limited" : "Markets unavailable"}
			action={<Badge tone="err">{error.code}</Badge>}
			footer={
				<div className="between">
					<span>Reason code and message come from the API unchanged.</span>
					<Link className="small" href={retryHref}>
						Retry →
					</Link>
				</div>
			}
		>
			<div className={`note ${rateLimited ? "warn" : "err"}`}>{error.message}</div>
			<p className="small muted mt-8">
				{rateLimited
					? "The upstream feed is throttling. No rows are shown rather than inventing a snapshot; wait and retry."
					: "No rows are shown for this request. The page does not substitute fixture data for a failed read."}
			</p>
			{error.retryable && (
				<p className="small muted mt-8">
					The API marks this failure retryable. Nothing was sent and no state changed.
				</p>
			)}
		</Panel>
	);
}

export default async function MarketsPage({
	searchParams,
}: {
	searchParams: Promise<SearchParams>;
}) {
	const raw = await searchParams;
	const params: MarketParams = {
		chain: one(raw.chain) || "all",
		q: one(raw.q).slice(0, 80),
		sort: isSort(one(raw.sort)) ? (one(raw.sort) as MarketSort) : "trending",
		limit: readLimit(one(raw.limit)),
	};

	const [chainsOutcome, marketsOutcome] = await Promise.all([
		getChains()
			.then((chains) => ({ ok: true as const, chains }))
			.catch((error: unknown) => ({ ok: false as const, error: asApiError(error) })),
		getMarkets({ chain: params.chain, q: params.q, sort: params.sort, limit: params.limit })
			.then((list) => ({ ok: true as const, list }))
			.catch((error: unknown) => ({ ok: false as const, error: asApiError(error) })),
	]);

	const chains: Chain[] = chainsOutcome.ok ? chainsOutcome.chains : [];
	const chainsError = chainsOutcome.ok ? null : chainsOutcome.error;
	const list = marketsOutcome.ok ? marketsOutcome.list : null;
	const error = marketsOutcome.ok ? null : marketsOutcome.error;
	const labelFor = (id: string) => chains.find((chain) => chain.id === id)?.name ?? id;
	const refreshHref = marketsHref(params);

	return (
		<AppShell active="markets">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">Overview / Markets</div>
					<h1>Markets</h1>
					<p className="sub">
						Multi-chain screener over the DexScreener feed. Every row carries its source
						and observation age; liquidity and volume are pair values, not depth at your
						size. Prices are discovery data, not executable quotes.
					</p>
				</div>
				<div className="page-actions">
					<Link className="btn" href={refreshHref}>
						Refresh
					</Link>
					<Link className="btn" href="/chains">
						Chains &amp; RPC
					</Link>
				</div>
			</header>

			<ModeBanner
				source={list?.source.name ?? "unavailable"}
				observedAt={list?.source.observed_at}
				stale={list?.stale ?? false}
			/>

			<div className={`tabs mb-12 ${styles.chainTabs}`} role="group" aria-label="Market chains">
				<Link
					className={`tab${params.chain === "all" ? " on" : ""}`}
					href={marketsHref(params, { chain: "all" })}
					aria-current={params.chain === "all" ? "page" : undefined}
				>
					All chains
				</Link>
				{chains.map((chain) => (
					<Link
						key={chain.id}
						className={`tab${params.chain === chain.id ? " on" : ""}`}
						href={marketsHref(params, { chain: chain.id })}
						aria-current={params.chain === chain.id ? "page" : undefined}
						title={`${chain.name} · ${chain.tier} · ${chain.capabilities.quote_kind}`}
					>
						{chain.name}
					</Link>
				))}
				{chainsError && (
					<span className="small muted" title={chainsError.message}>
						Chain registry unavailable · {chainsError.code}
					</span>
				)}
			</div>

			{error && <ErrorPanel error={error} retryHref={refreshHref} />}

			{!error && list && (
				<Panel
					flush
					action={
						list.stale ? (
							<Badge tone="warn">STALE</Badge>
						) : (
							<Badge tone="ghost">{isLiveMode() ? "live fetch" : "fixture data"}</Badge>
						)
					}
					footer={
						<div className="between">
							<span>
								{list.rows.length} rows · limit {params.limit} · source{" "}
								{sourceLabel(list.source.name)} · sorted by{" "}
								{SORTS.find((sort) => sort.id === params.sort)?.label.toLowerCase()}
							</span>
							<span>
								page 1/1 ·{" "}
								{list.stale
									? "the API marked this snapshot stale"
									: `observed ${ageLabel(list.source.observed_at)} ago`}
							</span>
						</div>
					}
				>
					<form className="table-toolbar" action="/markets" method="get" role="search">
						{params.chain !== "all" && <input type="hidden" name="chain" value={params.chain} />}
						{params.limit !== 50 && (
							<input type="hidden" name="limit" value={String(params.limit)} />
						)}
						<label className="field" style={{ minWidth: 220, flex: "1 1 240px" }}>
							<span className="small muted">Search symbol, name or address</span>
							<input
								className="input mono"
								type="search"
								name="q"
								defaultValue={params.q}
								placeholder="BONK or DezXAZ…"
								aria-label="Search symbol, name or address"
								maxLength={80}
							/>
						</label>
						<label className="field" style={{ minWidth: 190 }}>
							<span className="small muted">Sort</span>
							<select
								className="select"
								name="sort"
								defaultValue={params.sort}
								aria-label="Sort markets"
							>
								{SORTS.map((sort) => (
									<option key={sort.id} value={sort.id}>
										{sort.label}
									</option>
								))}
							</select>
						</label>
						<button className="btn" type="submit">
							Apply
						</button>
						{params.q && (
							<Link className="btn ghost sm" href={marketsHref(params, { q: "" })}>
								Clear search
							</Link>
						)}
						<span className={styles.toolbarNote}>
							Filters are URL query params; the server re-fetches on every change.
						</span>
					</form>

					<div className={`small muted ${styles.swipeHint}`}>Swipe to see more columns</div>

					{list.rows.length === 0 ? (
						<div className="panel-body">
							<div className="row mb-8">
								<Badge tone="warn">EMPTY_RESULT</Badge>
								<span className="small muted">No market rows matched this request.</span>
							</div>
							<p className="small muted">
								{params.q
									? `Nothing matched “${params.q}”${params.chain !== "all" ? ` on ${labelFor(params.chain)}` : ""}. DexScreener may not index this token yet.`
									: "The feed returned no pairs for this chain. That is an empty result, not a zero price."}
							</p>
							<div className="row mt-12">
								<Link className="btn sm" href={marketsHref(params, { q: "", chain: "all" })}>
									Reset filters
								</Link>
							</div>
						</div>
					) : (
						<div className="table-wrap">
							<table className="table">
								<thead>
									<tr>
										<th scope="col">Token</th>
										<th scope="col">Chain</th>
										<th scope="col" className="num">
											Price
										</th>
										<th scope="col" className="num col-hide-sm">
											1h
										</th>
										<th scope="col" className="num">
											24h
										</th>
										<th scope="col" className="num">
											Liquidity
										</th>
										<th scope="col" className="num col-hide-sm">
											Vol 24h
										</th>
										<th scope="col">Source · age</th>
										<th scope="col" className="col-hide-sm">
											Pair age
										</th>
										<th scope="col">Safety</th>
									</tr>
								</thead>
								<tbody>
									{list.rows.map((row) => (
										<tr key={`${row.chain}:${row.pair_address}`}>
											<td>
												<TokenCell row={row} />
											</td>
											<td>
												<span className="chain">
													<span className="sq">{chainInitial(labelFor(row.chain))}</span>
													{labelFor(row.chain)}
												</span>
											</td>
											<td className="num mono">{formatUsdPrice(row.price_usd)}</td>
											<td className="num col-hide-sm">
												<Change value={row.price_change_h1} />
											</td>
											<td className="num">
												<Change value={row.price_change_h24} />
											</td>
											<td className="num">{formatUsdCompact(row.liquidity_usd)}</td>
											<td className="num col-hide-sm">
												{formatUsdCompact(row.volume_24h_usd)}
											</td>
											<td>
												<span className="small muted">
													{sourceLabel(row.source.name)} · {ageLabel(row.source.observed_at)}
												</span>
											</td>
											<td className="small muted col-hide-sm">
												{pairAgeLabel(row.pair_created_at)}
											</td>
											<td>
												<Badge tone="warn" title="Safety is not implemented in P0">
													unknown
												</Badge>
											</td>
										</tr>
									))}
								</tbody>
							</table>
						</div>
					)}
				</Panel>
			)}

			{!error && list && list.rows.length > 0 && (
				<div className="split mt-16">
					<Panel title="How to read this table" sub="what each column is allowed to claim">
						<div className="stack-sm small muted">
							<div>
								• <span className="ink">Price</span> is the DexScreener pair price at the
								observed time, not an executable quote at your size.
							</div>
							<div>
								• <span className="ink">Liquidity</span> is pool TVL for the pair;{" "}
								<span className="ink">Vol 24h</span> is pair volume. Neither is a depth
								measurement.
							</div>
							<div>
								• <span className="ink">Source · age</span> is per row. The API marks the
								whole snapshot stale; this page does not invent a freshness threshold.
							</div>
							<div>
								• <span className="ink">Pair age</span> is listing age, not quote freshness.
								Rows younger than an hour say so.
							</div>
							<div>
								• <span className="ink">Safety</span> is unknown for every token in P0. A
								miss is not a pass; execution gates stay fail-closed.
							</div>
						</div>
					</Panel>
					<Panel title="Freshness & failure states" sub="reason codes, no silent fallback">
						<div className="stack-sm small muted">
							<div>
								<Badge tone={list.stale ? "warn" : "ghost"}>
									{list.stale ? "STALE" : "FRESH"}
								</Badge>{" "}
								the API&apos;s own stale flag for this snapshot.
							</div>
							<div>
								<Badge tone="err">RATE_LIMITED</Badge> an upstream 429. No rows are shown;
								retry after the backoff.
							</div>
							<div>
								<Badge tone="warn">EMPTY_RESULT</Badge> the request matched nothing. Unknown
								is not zero.
							</div>
							<div>
								<Badge tone="err">UPSTREAM_ERROR</Badge> or{" "}
								<Badge tone="err">UPSTREAM_UNREACHABLE</Badge> the API failed; the reason is
								printed and nothing is substituted.
							</div>
							<div>
								<Badge tone="ghost">FIXTURE</Badge> means local sample data.{" "}
								<Badge tone="ok">LIVE</Badge> means this response came from the API.
							</div>
						</div>
					</Panel>
				</div>
			)}
		</AppShell>
	);
}
