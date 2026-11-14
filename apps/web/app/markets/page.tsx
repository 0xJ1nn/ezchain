import Link from "next/link";
import { MarketTable } from "@/components/market-table";
import { AppShell } from "@/components/shell";
import { Badge, Panel } from "@/components/ui";
import {
	asApiError,
	getChains,
	getMarkets,
	isLiveMode,
	type ApiError,
	type Chain,
	type MarketList,
	type MarketSort,
	type MarketSortSemantics,
} from "@/lib/api";
import { ModeBanner } from "@/lib/live-banner";
import {
	ageLabel,
	changeText,
	formatUsdCompact,
	populationSummary,
	sourceLabel,
} from "@/lib/live-format";
import styles from "./markets.module.css";

export const dynamic = "force-dynamic";

type SearchParams = Record<string, string | string[] | undefined>;

const DEFAULT_LIMIT = 50;

// What the ordering actually covers. A single-chain read ranks inside that chain; only a
// feed-wide query ranks across chains; an all-chain browse is a per-chain round robin.
const SORT_SCOPE: Record<MarketSortSemantics, { label: string; badge: string; detail: string }> = {
	global_sort: {
		label: "global sort",
		badge: "GLOBAL SORT",
		detail: "Rows are ranked globally across the feed-wide query result.",
	},
	per_chain_sort: {
		label: "per-chain sort",
		badge: "PER-CHAIN SORT",
		detail:
			"Rows are ranked within the selected chain only. This is not an all-chain ranking.",
	},
	per_chain_round_robin: {
		label: "per-chain round robin",
		badge: "PER-CHAIN ROUND ROBIN",
		detail:
			"Each chain is sorted independently, then the chains are interleaved. This is not one global ranking.",
	},
};

const SORTS: { id: MarketSort; label: string }[] = [
	{ id: "trending", label: "24h volume (trending)" },
	{ id: "liquidity", label: "Liquidity" },
	{ id: "new", label: "Newest pair" },
	{ id: "change_h24", label: "24h change" },
	{ id: "pair_age", label: "Pair age (oldest first)" },
];

function one(value: string | string[] | undefined): string {
	return Array.isArray(value) ? (value[0] ?? "") : (value ?? "");
}

function isSort(value: string): value is MarketSort {
	return SORTS.some((sort) => sort.id === value);
}

function sortLabel(value: MarketSort): string {
	return SORTS.find((sort) => sort.id === value)?.label ?? value;
}

function readText(value: string, max = 80): string {
	return value.trim().slice(0, max);
}

// Keep filter parsing aligned with the API: absorb every realistic USD/percent value,
// but never echo an exponent the server would have to expand into a huge decimal string.
const FILTER_MAX = 1e18;
const FILTER_MIN = 1e-18;

function readDecimal(value: string, signed: boolean): string {
	const text = value.trim();
	// A filter belongs in a URL chip; bound the raw text before echoing it back.
	if (!text || text.length > 48) return "";
	const exponent = /[eE]([+-]?\d+)/.exec(text);
	if (exponent && Math.abs(Number(exponent[1])) > 18) return "";
	const parsed = Number(text);
	if (!Number.isFinite(parsed)) return "";
	if (!signed && parsed < 0) return "";
	if (Math.abs(parsed) > FILTER_MAX) return "";
	// Zero is a valid minimum. A nonzero value below the floor is not supported.
	if (parsed !== 0 && Math.abs(parsed) < FILTER_MIN) return "";
	return text;
}

function readUnsigned(value: string): string {
	return readDecimal(value, false);
}

function readSigned(value: string): string {
	return readDecimal(value, true);
}

function readLimit(value: string): number {
	const parsed = Number.parseInt(value, 10);
	if (!Number.isFinite(parsed)) return DEFAULT_LIMIT;
	return Math.min(100, Math.max(1, parsed));
}

function readOffset(value: string): number {
	const parsed = Number.parseInt(value, 10);
	if (!Number.isFinite(parsed) || parsed < 0) return 0;
	return parsed;
}

interface MarketParams {
	chain: string;
	q: string;
	sort: MarketSort;
	limit: number;
	offset: number;
	minLiquidityUsd: string;
	minVolume24hUsd: string;
	minChangeH24: string;
	maxChangeH24: string;
	minPairAgeMinutes: string;
	maxPairAgeMinutes: string;
	dex: string;
	starredOnly: boolean;
}

function marketsHref(params: MarketParams, patch: Partial<MarketParams> = {}): string {
	// Any filter change starts a new page unless the caller asks for a specific offset.
	const merged = { ...params, ...patch };
	if (!("offset" in patch)) merged.offset = 0;
	const query = new URLSearchParams();
	if (merged.chain !== "all") query.set("chain", merged.chain);
	if (merged.q) query.set("q", merged.q);
	if (merged.sort !== "trending") query.set("sort", merged.sort);
	if (merged.limit !== DEFAULT_LIMIT) query.set("limit", String(merged.limit));
	if (merged.offset > 0) query.set("offset", String(merged.offset));
	if (merged.minLiquidityUsd) query.set("min_liquidity_usd", merged.minLiquidityUsd);
	if (merged.minVolume24hUsd) query.set("min_volume_24h_usd", merged.minVolume24hUsd);
	if (merged.minChangeH24) query.set("min_change_h24", merged.minChangeH24);
	if (merged.maxChangeH24) query.set("max_change_h24", merged.maxChangeH24);
	if (merged.minPairAgeMinutes) query.set("min_pair_age_minutes", merged.minPairAgeMinutes);
	if (merged.maxPairAgeMinutes) query.set("max_pair_age_minutes", merged.maxPairAgeMinutes);
	if (merged.dex) query.set("dex", merged.dex);
	if (merged.starredOnly) query.set("starred", "1");
	const suffix = query.toString();
	return suffix ? `/markets?${suffix}` : "/markets";
}

interface FilterChip {
	key: keyof MarketParams;
	label: string;
	href: string;
}

function filterChips(params: MarketParams, labelFor: (id: string) => string): FilterChip[] {
	const chips: FilterChip[] = [];
	if (params.chain !== "all") {
		chips.push({
			key: "chain",
			label: `Chain: ${labelFor(params.chain)}`,
			href: marketsHref(params, { chain: "all" }),
		});
	}
	if (params.q) {
		chips.push({
			key: "q",
			label: `Search: “${params.q}”`,
			href: marketsHref(params, { q: "" }),
		});
	}
	if (params.minLiquidityUsd) {
		chips.push({
			key: "minLiquidityUsd",
			label: `Liquidity ≥ ${formatUsdCompact(params.minLiquidityUsd)}`,
			href: marketsHref(params, { minLiquidityUsd: "" }),
		});
	}
	if (params.minVolume24hUsd) {
		chips.push({
			key: "minVolume24hUsd",
			label: `Volume ≥ ${formatUsdCompact(params.minVolume24hUsd)}`,
			href: marketsHref(params, { minVolume24hUsd: "" }),
		});
	}
	if (params.minChangeH24) {
		chips.push({
			key: "minChangeH24",
			label: `24h change ≥ ${changeText(params.minChangeH24)}`,
			href: marketsHref(params, { minChangeH24: "" }),
		});
	}
	if (params.maxChangeH24) {
		chips.push({
			key: "maxChangeH24",
			label: `24h change ≤ ${changeText(params.maxChangeH24)}`,
			href: marketsHref(params, { maxChangeH24: "" }),
		});
	}
	if (params.minPairAgeMinutes) {
		chips.push({
			key: "minPairAgeMinutes",
			label: `Pair age ≥ ${params.minPairAgeMinutes}m`,
			href: marketsHref(params, { minPairAgeMinutes: "" }),
		});
	}
	if (params.maxPairAgeMinutes) {
		chips.push({
			key: "maxPairAgeMinutes",
			label: `Pair age ≤ ${params.maxPairAgeMinutes}m`,
			href: marketsHref(params, { maxPairAgeMinutes: "" }),
		});
	}
	if (params.dex) {
		chips.push({
			key: "dex",
			label: `DEX: ${params.dex}`,
			href: marketsHref(params, { dex: "" }),
		});
	}
	if (params.starredOnly) {
		chips.push({
			key: "starredOnly",
			label: "Starred only",
			href: marketsHref(params, { starredOnly: false }),
		});
	}
	if (params.sort !== "trending") {
		chips.push({
			key: "sort",
			label: `Sort: ${sortLabel(params.sort)}`,
			href: marketsHref(params, { sort: "trending" }),
		});
	}
	return chips;
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

function PopulationLine({ list }: { list: MarketList }) {
	return <span className="small muted">{populationSummary(list)}</span>;
}

export default async function MarketsPage({
	searchParams,
}: {
	searchParams: Promise<SearchParams>;
}) {
	const raw = await searchParams;
	const params: MarketParams = {
		chain: one(raw.chain) || "all",
		q: readText(one(raw.q)),
		sort: isSort(one(raw.sort)) ? (one(raw.sort) as MarketSort) : "trending",
		limit: readLimit(one(raw.limit)),
		offset: readOffset(one(raw.offset)),
		minLiquidityUsd: readUnsigned(one(raw.min_liquidity_usd)),
		minVolume24hUsd: readUnsigned(one(raw.min_volume_24h_usd)),
		minChangeH24: readSigned(one(raw.min_change_h24)),
		maxChangeH24: readSigned(one(raw.max_change_h24)),
		minPairAgeMinutes: readUnsigned(one(raw.min_pair_age_minutes)),
		maxPairAgeMinutes: readUnsigned(one(raw.max_pair_age_minutes)),
		dex: readText(one(raw.dex)),
		starredOnly: one(raw.starred) === "1" || one(raw.starred) === "true",
	};

	const [chainsOutcome, marketsOutcome] = await Promise.all([
		getChains()
			.then((chains) => ({ ok: true as const, chains }))
			.catch((error: unknown) => ({ ok: false as const, error: asApiError(error) })),
		getMarkets({
			chain: params.chain,
			q: params.q || undefined,
			sort: params.sort,
			limit: params.limit,
			offset: params.offset,
			minLiquidityUsd: params.minLiquidityUsd || undefined,
			minVolume24hUsd: params.minVolume24hUsd || undefined,
			minChangeH24: params.minChangeH24 || undefined,
			maxChangeH24: params.maxChangeH24 || undefined,
			minPairAgeMinutes: params.minPairAgeMinutes || undefined,
			maxPairAgeMinutes: params.maxPairAgeMinutes || undefined,
			dex: params.dex || undefined,
		})
			.then((list) => ({ ok: true as const, list }))
			.catch((error: unknown) => ({ ok: false as const, error: asApiError(error) })),
	]);

	const chains: Chain[] = chainsOutcome.ok ? chainsOutcome.chains : [];
	const chainsError = chainsOutcome.ok ? null : chainsOutcome.error;
	const list = marketsOutcome.ok ? marketsOutcome.list : null;
	const error = marketsOutcome.ok ? null : marketsOutcome.error;
	const labelFor = (id: string) => chains.find((chain) => chain.id === id)?.name ?? id;
	const chainNames = Object.fromEntries(chains.map((chain) => [chain.id, chain.name]));
	const now = Date.now();
	// Refresh keeps the current page and filters; only a filter change resets the page.
	const refreshHref = marketsHref(params, { offset: params.offset });
	const chips = filterChips(params, labelFor);
	const filterNames = chips.filter((chip) => chip.key !== "sort").map((chip) => chip.label);
	// A chain with no discovery seeds was never asked, so its empty page is unknown, not
	// an empty result. Coverage is per-chain evidence from the read, so this holds for a
	// single-chain browse and for a feed where every chain lacks seeds.
	const noSeeds =
		list?.coverage != null &&
		list.coverage.length > 0 &&
		list.coverage.every((item) => item.reason === "no_seeds");
	const semantics = SORT_SCOPE[list?.sort_semantics ?? "global_sort"];
	const semanticsTitle = semantics.detail;
	const total = list?.total ?? 0;
	const returned = list?.rows.length ?? 0;
	const start = returned === 0 ? 0 : params.offset + 1;
	const end = params.offset + returned;
	const page = Math.floor(params.offset / params.limit) + 1;
	const pageCount = Math.max(1, Math.ceil(total / params.limit));
	const hasPrevious = params.offset > 0;
	const nextOffset = list?.next_offset ?? null;
	// The API answers a page beyond the filtered total with an empty page. That is not
	// evidence that the filters matched nothing, so name the out-of-range page first.
	const pagePastEnd = total > 0 && params.offset >= total;

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
					<Link className="btn" href="/watchlist">
						Watchlist
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

			<div className={`row mb-12 ${styles.watchFilter}`} role="group" aria-label="Watchlist filter">
				<Link
					className={`chip ${styles.starredChip}`}
					href={marketsHref(params, { starredOnly: !params.starredOnly })}
					aria-current={params.starredOnly ? "page" : undefined}
					aria-label={params.starredOnly ? "Show all markets" : "Show starred markets only"}
				>
					<span aria-hidden="true">★</span> Starred only
				</Link>
				<span className="small muted">
					The star stores a token in this browser. The other filters and the pager remain
					server-side.
				</span>
			</div>

			{chips.length > 0 && (
				<div className={`row mb-12 ${styles.chips}`} aria-label="Active filters">
					{chips.map((chip) => (
						<Link
							key={chip.key}
							className={`chip ${styles.chip}`}
							href={chip.href}
							aria-label={`Remove filter ${chip.label}`}
						>
							{chip.label}
							<span aria-hidden="true">×</span>
						</Link>
					))}
					<Link className="small" href="/markets">
						Clear all
					</Link>
				</div>
			)}

			{error && <ErrorPanel error={error} retryHref={refreshHref} />}

			{!error && list && (
				<Panel
					flush
					action={
						<div className="row">
							<Badge tone="ghost" title={semanticsTitle}>
								{semantics.badge}
							</Badge>
							{list.stale ? (
								<Badge tone="warn">STALE</Badge>
							) : (
								<Badge tone="ghost">
									{isLiveMode() ? "live fetch" : "fixture data"}
								</Badge>
							)}
						</div>
					}
					footer={
						<div className={styles.footer}>
							<span>
								{returned > 0
									? `${returned} of ${total} markets · showing ${start}–${end}`
									: `0 of ${total} markets · no rows on this page`}{" "}
								· page {page} of {pageCount} · limit {list.limit} · source{" "}
								{sourceLabel(list.source.name)} · sorted by{" "}
								{sortLabel(list.sort).toLowerCase()}
							</span>
							<span>
								Order: {semantics.label} ·{" "}
								{list.stale
									? "the API marked this snapshot stale"
									: `observed ${ageLabel(list.source.observed_at)} ago`}
							</span>
						</div>
					}
				>
					<div className={styles.filterPanel} data-testid="market-filters">
						<div className={styles.filterSummary}>
							<span className="strong">Filters</span>
							<span>{chips.length} active</span>
							<span className="muted">
								{returned} of {total} markets
							</span>
						</div>
						<form
							className={`table-toolbar ${styles.filterForm}`}
							action="/markets"
							method="get"
							role="search"
						>
							{params.limit !== DEFAULT_LIMIT && (
								<input type="hidden" name="limit" value={String(params.limit)} />
							)}
							{params.starredOnly && <input type="hidden" name="starred" value="1" />}
							<label className="field" style={{ minWidth: 140 }}>
								<span className="small muted">Chain</span>
								<select
									className="select"
									name="chain"
									defaultValue={params.chain}
									aria-label="Filter by chain"
								>
									<option value="all">All chains</option>
									{chains.map((chain) => (
										<option key={chain.id} value={chain.id}>
											{chain.name}
										</option>
									))}
								</select>
							</label>
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
							<label className="field" style={{ minWidth: 130 }}>
								<span className="small muted">Min liquidity (USD)</span>
								<input
									className="input mono"
									type="number"
									inputMode="decimal"
									min="0"
									step="any"
									name="min_liquidity_usd"
									defaultValue={params.minLiquidityUsd}
									placeholder="100000"
									aria-label="Minimum liquidity in USD"
								/>
							</label>
							<label className="field" style={{ minWidth: 130 }}>
								<span className="small muted">Min volume 24h (USD)</span>
								<input
									className="input mono"
									type="number"
									inputMode="decimal"
									min="0"
									step="any"
									name="min_volume_24h_usd"
									defaultValue={params.minVolume24hUsd}
									placeholder="250000"
									aria-label="Minimum 24h volume in USD"
								/>
							</label>
							<label className="field" style={{ minWidth: 110 }}>
								<span className="small muted">24h change ≥ %</span>
								<input
									className="input mono"
									type="number"
									inputMode="decimal"
									step="any"
									name="min_change_h24"
									defaultValue={params.minChangeH24}
									placeholder="-100"
									aria-label="Minimum 24h change percent"
								/>
							</label>
							<label className="field" style={{ minWidth: 110 }}>
								<span className="small muted">24h change ≤ %</span>
								<input
									className="input mono"
									type="number"
									inputMode="decimal"
									step="any"
									name="max_change_h24"
									defaultValue={params.maxChangeH24}
									placeholder="100"
									aria-label="Maximum 24h change percent"
								/>
							</label>
							<label className="field" style={{ minWidth: 115 }}>
								<span className="small muted">Pair age ≥ min</span>
								<input
									className="input mono"
									type="number"
									inputMode="numeric"
									min="0"
									step="1"
									name="min_pair_age_minutes"
									defaultValue={params.minPairAgeMinutes}
									placeholder="60"
									aria-label="Minimum pair age in minutes"
								/>
							</label>
							<label className="field" style={{ minWidth: 115 }}>
								<span className="small muted">Pair age ≤ min</span>
								<input
									className="input mono"
									type="number"
									inputMode="numeric"
									min="0"
									step="1"
									name="max_pair_age_minutes"
									defaultValue={params.maxPairAgeMinutes}
									placeholder="1440"
									aria-label="Maximum pair age in minutes"
								/>
							</label>
							<label className="field" style={{ minWidth: 120 }}>
								<span className="small muted">DEX</span>
								<input
									className="input mono"
									name="dex"
									defaultValue={params.dex}
									placeholder="uniswap"
									aria-label="DEX"
									maxLength={80}
								/>
							</label>
							<button className="btn" type="submit">
								Apply
							</button>
							{chips.length > 0 && (
								<Link className="btn ghost sm" href="/markets">
									Reset filters
								</Link>
							)}
							<span className={styles.toolbarNote}>
								Filters are URL query params; the server re-fetches on every change.
								Unknown liquidity, volume, change or pair age is excluded when its filter
								is set — never treated as zero.
							</span>
						</form>
					</div>

					<div className={`small muted ${styles.swipeHint}`}>Swipe to see more columns</div>

					{list.rows.length === 0 && !params.starredOnly ? (
						<div className="panel-body">
							<div className="row mb-8">
								<Badge tone="warn">{noSeeds ? "NO_SEEDS" : "EMPTY_RESULT"}</Badge>
								<span className="small muted">
									{noSeeds
										? params.chain === "all"
											? "No chain in this feed has discovery seeds configured."
											: "This chain has no discovery seeds configured."
										: pagePastEnd
											? `Page ${page} is past the last result.`
											: "No market rows matched this request."}
								</span>
							</div>
							<p className="small muted">
								{noSeeds
									? params.chain === "all"
										? "Nothing was requested for this feed, so its population is unknown — not an empty result."
										: `Nothing was requested for ${labelFor(params.chain)}, so its population is unknown — not an empty result.`
									: pagePastEnd
										? `${total} row${total === 1 ? "" : "s"} matched${
												filterNames.length > 0
													? ` the active filters (${filterNames.join(" · ")})`
													: ""
											}, but this page is beyond the end. Use Prev, or reset the page to start over.`
										: params.q
											? `Nothing matched “${params.q}”${
													params.chain !== "all" ? ` on ${labelFor(params.chain)}` : ""
												}. DexScreener may not index this token yet.`
											: filterNames.length > 0
												? `No rows matched: ${filterNames.join(" · ")}. Unknown values are excluded by these filters, never treated as zero.`
												: "The feed returned no pairs for this chain. That is an empty result, not a zero price."}
							</p>
							<div className="row mt-12">
								{hasPrevious && (
									<Link
										className="btn sm"
										href={marketsHref(params, { offset: Math.max(0, params.offset - params.limit) })}
									>
										← Previous
									</Link>
								)}
								<Link className="btn sm" href="/markets">
									Reset filters
								</Link>
							</div>
						</div>
					) : (
						<MarketTable
							rows={list.rows}
							chainNames={chainNames}
							starredOnly={params.starredOnly}
							showAllHref={marketsHref(params, { starredOnly: false })}
							now={now}
						/>
					)}

					<div className={`table-foot ${styles.pager}`}>
						<PopulationLine list={list} />
						<div className="btn-group" role="navigation" aria-label="Market pages">
							{hasPrevious ? (
								<Link
									className="btn sm"
									rel="prev"
									href={marketsHref(params, {
										offset: Math.max(0, params.offset - params.limit),
									})}
								>
									← Prev
								</Link>
							) : (
								<span className="btn sm" aria-disabled="true">
									← Prev
								</span>
							)}
							<span className="btn sm" aria-current="page">
								Page {page} of {pageCount}
							</span>
							{nextOffset !== null ? (
								<Link
									className="btn sm"
									rel="next"
									href={marketsHref(params, { offset: nextOffset })}
								>
									Next →
								</Link>
							) : (
								<span className="btn sm" aria-disabled="true">
									Next →
								</span>
							)}
						</div>
					</div>
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
							<div>
								• <span className="ink">★</span> is a browser-local watchlist entry. It
								creates no account, alert or order; clearing site data removes it.
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
