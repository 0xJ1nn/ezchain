import Link from "next/link";
import { notFound } from "next/navigation";
import type { ReactNode } from "react";
import { CandleTimeframes } from "@/components/candle-timeframes";
import { AppShell } from "@/components/shell";
import { Badge, Panel } from "@/components/ui";
import { WatchlistToggle } from "@/components/watchlist-toggle";
import { TradeWorkspace } from "@/components/trade/workspace";
import {
	asApiError,
	getChains,
	getQuote,
	getToken,
	getTokenCandles,
	type ApiError,
	type CandleTimeframe,
	type Chain,
	type Quote,
	type Reason,
	type Side,
} from "@/lib/api";
import { TIMEFRAMES, parseCandleTimeframe } from "@/lib/candle-view";
import { ModeBanner } from "@/lib/live-banner";
import {
	ageLabel,
	changeText,
	changeTone,
	formatUsd,
	formatUsdCompact,
	formatUsdPrice,
	initials,
	quoteKindLabel,
	shortAddress,
	sourceLabel,
} from "@/lib/live-format";
import { getPaperFills, getPaperSnapshot, type PaperFillRecord, type PaperPosition } from "@/lib/paper-api";
import { webMode as protectionWebMode } from "@/lib/protection-api";
import styles from "./token.module.css";

export const dynamic = "force-dynamic";

type SearchParams = Record<string, string | string[] | undefined>;

function one(value: string | string[] | undefined): string {
	return Array.isArray(value) ? (value[0] ?? "") : (value ?? "");
}

function readAmount(value: string): string {
	const raw = value.trim();
	if (/^\d+(\.\d{1,2})?$/.test(raw) && Number(raw) > 0) return raw;
	return "100";
}

/** The safety object is rendered straight from the data layer; a missing field is unknown. */
const SAFETY_FIELDS = [
	{ key: "mint_authority", label: "Mint authority" },
	{ key: "freeze_authority", label: "Freeze authority" },
	{ key: "lp_custody", label: "LP custody" },
	{ key: "creator_history", label: "Creator history" },
] as const;

function safetyText(safety: Record<string, unknown>, key: string): string | null {
	const value = safety[key];
	return typeof value === "string" && value.trim() ? value : null;
}

function safetyReason(safety: Record<string, unknown>): string {
	const reason = safety.reason;
	return typeof reason === "string" && reason.trim() ? reason : "no reason reported";
}

function QuoteRow({
	k,
	v,
	paper = true,
	tone,
}: {
	k: string;
	v: ReactNode;
	paper?: boolean;
	tone?: "err" | "warn" | "ok";
}) {
	return (
		<tr>
			<td className="muted">{k}</td>
			<td className="right">
				{paper && <Badge tone="ghost">PAPER</Badge>}{" "}
				<span className={`mono${tone ? ` ${tone}` : ""}`}>{v}</span>
			</td>
		</tr>
	);
}

function TokenError({ error, chain, id }: { error: ApiError; chain: string; id: string }) {
	return (
		<AppShell active="markets">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">
						<Link href="/markets">Markets</Link> / {chain}
					</div>
					<h1>Token unavailable</h1>
					<p className="sub">
						{chain} / {shortAddress(id)}
					</p>
				</div>
			</header>
			<ModeBanner detail="token read failed" />
			<Panel title="No token snapshot" action={<Badge tone="err">{error.code}</Badge>}>
				<div className="note err">{error.message}</div>
				<p className="small muted mt-8">
					No price, liquidity or quote is shown. The page does not substitute a fixture
					or an older snapshot for a failed read.
				</p>
				<div className="row mt-12">
					<Link className="btn" href={`/tokens/${chain}/${id}`}>
						Retry
					</Link>
					<Link className="btn" href="/markets">
						Back to markets
					</Link>
				</div>
			</Panel>
		</AppShell>
	);
}

export default async function TokenPage({
	params,
	searchParams,
}: {
	params: Promise<{ chain: string; id: string }>;
	searchParams: Promise<SearchParams>;
}) {
	const { chain, id } = await params;
	const raw = await searchParams;
	const side: Side = one(raw.side) === "sell" ? "sell" : "buy";
	const amount = readAmount(one(raw.amount));
	const timeframe = parseCandleTimeframe(raw.timeframe);
	const pagePath = `/tokens/${chain}/${id}`;
	const timeframeQuery = timeframe === "1h" ? "" : `&timeframe=${timeframe}`;
	const chartHref = (next: CandleTimeframe) => {
		const params = new URLSearchParams({ side, amount });
		if (next !== "1h") params.set("timeframe", next);
		return `${pagePath}?${params.toString()}`;
	};

	const tokenResult = await getToken(chain, id)
		.then((snapshot) => ({ ok: true as const, snapshot }))
		.catch((error: unknown) => ({ ok: false as const, error: asApiError(error) }));
	if (!tokenResult.ok) {
		if (
			tokenResult.error.code === "NOT_FOUND" ||
			tokenResult.error.code === "CHAIN_NOT_FOUND"
		) {
			notFound();
		}
		return <TokenError error={tokenResult.error} chain={chain} id={id} />;
	}
	const token = tokenResult.snapshot;

	const [quoteResult, chainsResult, candlesResult, snapshotResult, fillsResult] = await Promise.all([
		getQuote({ chain, token: token.address, side, amountUsd: amount })
			.then((quote) => ({ quote, reason: null as Reason | null }))
			.catch((error: unknown) => ({ quote: null as Quote | null, reason: asApiError(error).toReason() })),
		getChains().then((chains) => chains).catch(() => [] as Chain[]),
		getTokenCandles(chain, token.address, timeframe)
			.then((series) => ({ series: series as Awaited<ReturnType<typeof getTokenCandles>> | null, error: null as ApiError | null }))
			.catch((error: unknown) => ({ series: null, error: asApiError(error) })),
		getPaperSnapshot().then((snapshot) => snapshot).catch(() => null),
		getPaperFills(200).then((result) => result).catch(() => null),
	]);

	const quote = quoteResult.quote;
	const quoteReason = quoteResult.reason;
	const chainMeta = chainsResult.find((candidate) => candidate.id === chain);
	const chainLabel = chainMeta?.name ?? chain;
	const sourceName = token.source?.name;
	const renderedAt = Date.now();
	const account = snapshotResult?.account.ok ? snapshotResult.account.data : null;
	const positions: PaperPosition[] = snapshotResult?.positions.ok ? snapshotResult.positions.data : [];
	const fills: PaperFillRecord[] = fillsResult?.ok ? fillsResult.data : [];
	const volume = Number(token.volume_24h_usd);
	const volume24hUsd = Number.isFinite(volume) ? volume : null;

	const chartControl = (
		<CandleTimeframes options={TIMEFRAMES} active={timeframe} hrefFor={chartHref} />
	);

	return (
		<AppShell active="markets">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">
						<Link href="/markets">Markets</Link> /{" "}
						<Link href={`/markets?chain=${chain}`}>{chainLabel}</Link> / {token.symbol}
					</div>
					<div className="row">
						<span className="avatar-lg" style={{ fontSize: 11 }} aria-hidden="true">
							{initials(token.symbol)}
						</span>
						<div>
							<h1>
								{token.name}{" "}
								<span className="muted" style={{ fontWeight: 400 }}>
									{token.symbol}
								</span>
							</h1>
							<p className={`sub ${styles.address}`}>{token.address}</p>
						</div>
					</div>
				</div>
				<div className="page-actions">
					<WatchlistToggle
						chain={chain}
						baseToken={token.address}
						symbol={token.symbol}
						name={token.name}
					/>
					<div style={{ textAlign: "right" }}>
						<div className="mono" style={{ fontSize: 20, fontWeight: 600 }}>
							{formatUsdPrice(token.price_usd)}
						</div>
						<div className="small">
							<span className={`delta ${changeTone(token.price_change_h1) === "err" ? "down" : "up"}`}>
								{changeText(token.price_change_h1)} 1h
							</span>{" "}
							·{" "}
							<span className={`delta ${changeTone(token.price_change_h24) === "err" ? "down" : "up"}`}>
								{changeText(token.price_change_h24)} 24h
							</span>
						</div>
						<div className="small muted">
							{chainLabel} · {chainMeta?.tier ?? "tier unknown"} · DexScreener pair price
						</div>
					</div>
					<Badge tone="ghost">PAPER</Badge>
					{quote ? (
						<Badge tone={quote.kind === "paper_mark" ? "warn" : "ok"}>
							{quoteKindLabel(quote.kind)}
						</Badge>
					) : (
						<Badge tone="err">{quoteReason?.code ?? "NO_QUOTE"}</Badge>
					)}
					<Link className="btn" href={`/markets?chain=${chain}`}>
						Back to markets
					</Link>
				</div>
			</header>

			<ModeBanner
				source={sourceName}
				observedAt={token.source?.observed_at}
				stale={token.stale}
				detail={token.stale ? "no new position should open from this snapshot" : undefined}
			/>

			<TradeWorkspace
				chain={chain}
				tokenAddress={token.address}
				symbol={token.symbol}
				candles={candlesResult.series?.candles ?? []}
				timeframe={timeframe}
				side={side}
				amount={amount}
				quote={quote}
				quoteReason={quoteReason}
				now={renderedAt}
				volume24hUsd={volume24hUsd}
				candleSource={candlesResult.series?.source.name ?? null}
				candleObservedAt={candlesResult.series?.source.observed_at ?? null}
				candleCount={candlesResult.series?.candles.length ?? 0}
				candlesStale={candlesResult.series?.stale ?? false}
				account={account}
				positions={positions}
				fills={fills}
				mode={protectionWebMode()}
				timeframeControl={chartControl}
			/>

			<div className="split mt-16">
				<div className="stack">
					<div id="pairs">
						<Panel
							title="Pairs"
							sub={`${chainLabel} · source and age are the token snapshot's`}
							flush
							footer={
								<span>
									{sourceLabel(sourceName)} · observed {ageLabel(token.source?.observed_at)} ago ·{" "}
									{token.stale ? "the API marked this snapshot stale" : "not marked stale by the API"}.
									A pair price is discovery data, not an executable quote.
								</span>
							}
						>
							<div className="table-wrap">
								<table className="table compact">
									<thead>
										<tr>
											<th scope="col">Dex</th>
											<th scope="col">Pair</th>
											<th scope="col">Quote</th>
											<th scope="col" className="num">Price</th>
											<th scope="col" className="num">Liquidity</th>
											<th scope="col" className="num col-hide-sm">Vol 24h</th>
											<th scope="col">Source · age</th>
											<th scope="col">Open</th>
										</tr>
									</thead>
									<tbody>
										{token.pairs.map((pair) => (
											<tr key={pair.pair_address}>
												<td>{pair.dex}</td>
												<td className="mono small">{shortAddress(pair.pair_address)}</td>
												<td className="mono small">{shortAddress(pair.quote_token)}</td>
												<td className="num mono">{formatUsdPrice(pair.price_usd)}</td>
												<td className="num">{formatUsdCompact(pair.liquidity_usd)}</td>
												<td className="num col-hide-sm">{formatUsdCompact(pair.volume_24h_usd)}</td>
												<td className="small muted">
													{sourceLabel(sourceName)} · {ageLabel(token.source?.observed_at)}
												</td>
												<td>
													{pair.url ? (
														<a className="small" href={pair.url} target="_blank" rel="noopener noreferrer">
															DexScreener ↗
														</a>
													) : (
														<span className="small muted">—</span>
													)}
												</td>
											</tr>
										))}
									</tbody>
								</table>
							</div>
						</Panel>
					</div>

					<div id="safety">
						<Panel
							title="Safety"
							sub="rendered from the token snapshot's safety object"
							action={<Badge tone="warn">unknown</Badge>}
							flush
							footer={
								<>
									Unknown is not a pass. The page renders only what the data layer carries —
									mint and freeze authority, LP custody and creator history — and prints an em
									dash for every field the store does not have. The API risk policy is the
									execution gate. Safety reason: <span className="mono">{safetyReason(token.safety)}</span>.
								</>
							}
						>
							<div className="table-wrap">
								<table className="table compact">
									<thead>
										<tr>
											<th scope="col">Check</th>
											<th scope="col">Result</th>
											<th scope="col">Detail</th>
											<th scope="col">Source · age</th>
										</tr>
									</thead>
									<tbody>
										{SAFETY_FIELDS.map((field) => {
											const value = safetyText(token.safety, field.key);
											return (
												<tr key={field.key}>
													<td>{field.label}</td>
													<td>
														{value ? (
															<span className="mono small">{value}</span>
														) : (
															<Badge tone="warn">unknown</Badge>
														)}
													</td>
													<td className="small muted">{value ?? "— · no value is assumed"}</td>
													<td className="small muted">
														{sourceLabel(sourceName)} · {ageLabel(token.source?.observed_at)}
													</td>
												</tr>
											);
										})}
									</tbody>
								</table>
							</div>
						</Panel>
					</div>

					<Panel title="Source & freshness" sub="one observation, one age">
						<div className={styles.quoteMeta}>
							<div>
								<span className="micro">Source</span>
								<div className="mono">{sourceLabel(sourceName)}</div>
							</div>
							<div>
								<span className="micro">Observed</span>
								<div className="mono small">{token.source?.observed_at ?? "—"}</div>
							</div>
							<div>
								<span className="micro">Age</span>
								<div className="mono">{ageLabel(token.source?.observed_at)}</div>
							</div>
							<div>
								<span className="micro">API stale flag</span>
								<div>
									{token.stale ? <Badge tone="warn">STALE</Badge> : <Badge tone="ghost">not stale</Badge>}
								</div>
							</div>
						</div>
						<p className="small muted mt-12">
							The API owns freshness. This page prints the observation age and the stale
							flag; it does not invent a threshold of its own.
						</p>
					</Panel>
				</div>

				<div className="stack">
					<div id="quote">
					<Panel title="Market numbers" sub="from the token snapshot · PAPER">
						<div className="btn-group mb-12" role="group" aria-label="Quote side">
							{(["buy", "sell"] as Side[]).map((option) => (
								<Link
									key={option}
									className={`btn sm${side === option ? " primary" : ""}`}
									href={`${pagePath}?side=${option}&amount=${encodeURIComponent(amount)}${timeframeQuery}`}
									aria-current={side === option ? "true" : undefined}
								>
									{option === "buy" ? "Buy" : "Sell"}
								</Link>
							))}
						</div>
						<table className="table compact mb-12">
							<tbody>
								<QuoteRow k="Side" v={quote?.side ?? side} paper={false} />
								<QuoteRow k="Price" v={formatUsdPrice(token.price_usd)} />
								<QuoteRow
									k="Expected out"
									v={quote
										? quote.side === "buy"
											? `${new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(Number(quote.amount_out))} ${token.symbol}`
											: formatUsd(quote.amount_out)
										: "—"}
								/>
								<QuoteRow k="Liquidity" v={formatUsdCompact(token.liquidity_usd)} />
								<QuoteRow k="Volume 24h" v={formatUsdCompact(token.volume_24h_usd)} />
								<QuoteRow k="Route" v={quote?.route ?? "paper mark · no executable route"} />
								<QuoteRow k="Quote" v={quote ? quoteKindLabel(quote.kind) : (quoteReason?.code ?? "NO_QUOTE")} />
								<QuoteRow k="Quote source · age" v={quote ? `${sourceLabel(quote.source.name)} · ${ageLabel(quote.source.observed_at)} old` : "—"} />
							</tbody>
						</table>
						{quoteReason && (
							<div className="note err">
								<div className="row mb-8">
									<Badge tone="err">{quoteReason.code}</Badge>
									{quoteReason.retryable && <Badge tone="warn">retryable</Badge>}
								</div>
								<div className="small">{quoteReason.message}</div>
								<div className="small muted mt-8">Reason from the API: {quoteReason.message}</div>
							</div>
						)}
						{quote?.kind === "paper_mark" && (
							<div className="note warn">
								<strong>PAPER · MARK.</strong> This chain has no executable venue in P0. A
								fill would use the pair mark, subject to API risk caps. It is not a live route.
							</div>
						)}
					</Panel>
					</div>
				</div>
			</div>
		</AppShell>
	);
}
