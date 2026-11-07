import Link from "next/link";
import { notFound } from "next/navigation";
import type { ReactNode } from "react";
import { CostSheet } from "@/components/cost-sheet";
import { CandleChart, CandleEmptyState } from "@/components/candle-chart";
import { AppShell } from "@/components/shell";
import { Badge, Panel, Stat } from "@/components/ui";
import {
	asApiError,
	getChains,
	getQuote,
	getToken,
	getTokenCandles,
	type ApiError,
	type CandlesOut,
	type Chain,
	type Quote,
	type Reason,
	type Side,
} from "@/lib/api";
import { ModeBanner } from "@/lib/live-banner";
import {
	ageLabel,
	changeText,
	changeTone,
	formatAmount,
	formatUsdPrice,
	formatUsd,
	formatUsdCompact,
	initials,
	quoteKindLabel,
	shortAddress,
	sourceLabel,
} from "@/lib/live-format";
import { PaperTicket } from "./paper-ticket";
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

function reasonMessage(reason: Reason): string {
	switch (reason.code) {
		case "NO_SELL_ROUTE":
			return "No executable route at this size. Nothing can be sent; the ticket's confirm stays closed.";
		case "QUOTE_STALE":
			return "The API rejected this quote as stale. Preview again; an expired quote is never reused.";
		case "RATE_LIMITED":
			return "The quote provider is rate-limiting requests. No quote is shown rather than reusing an old one.";
		default:
			return reason.message;
	}
}

/**
 * The real safety object, straight from the data layer. The page renders only the
 * fields the token snapshot carries; a field the store does not have is "—" and a
 * badge that says unknown. Nothing is inferred, and nothing is filled from mock.
 */
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
	sub,
	paper = true,
	tone,
}: {
	k: string;
	v: ReactNode;
	sub?: string;
	paper?: boolean;
	tone?: "err" | "warn" | "ok";
}) {
	return (
		<tr>
			<td className="muted">{k}</td>
			<td className="right">
				{paper && <Badge tone="ghost">PAPER</Badge>}{" "}
				<span className={`mono${tone ? ` ${tone}` : ""}`}>{v}</span>
				{sub && <span className="sub"> {sub}</span>}
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
	const pagePath = `/tokens/${chain}/${id}`;

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

	const [quoteResult, chainsResult, candlesResult] = await Promise.all([
		getQuote({ chain, token: token.address, side, amountUsd: amount })
			.then((quote) => ({ quote, reason: null as Reason | null }))
			.catch((error: unknown) => ({ quote: null as Quote | null, reason: asApiError(error).toReason() })),
		getChains()
			.then((chains) => chains)
			.catch(() => [] as Chain[]),
		getTokenCandles(chain, token.address)
			.then((series) => ({ series: series as CandlesOut | null, error: null as ApiError | null }))
			.catch((error: unknown) => ({ series: null as CandlesOut | null, error: asApiError(error) })),
	]);

	const quote = quoteResult.quote;
	const quoteReason = quoteResult.reason;
	const chainMeta = chainsResult.find((candidate) => candidate.id === chain);
	const chainLabel = chainMeta?.name ?? chain;
	const sourceName = token.source?.name;

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

			<Panel flush className="mb-12">
				<div className="grid cols-4">
					<Stat
						label="Price"
						value={formatUsdPrice(token.price_usd)}
						meta={`${sourceLabel(sourceName)} · ${ageLabel(token.source?.observed_at)} old`}
						title="DexScreener pair price, not an executable quote at size"
					/>
					<Stat
						label="Liquidity"
						value={formatUsdCompact(token.liquidity_usd)}
						meta={`${token.pairs.length} pair${token.pairs.length === 1 ? "" : "s"} · pool TVL`}
					/>
					<Stat
						label="24h volume"
						value={formatUsdCompact(token.volume_24h_usd)}
						meta="pair volume, not depth"
					/>
					<Stat
						label="1h change"
						value={changeText(token.price_change_h1)}
						tone={changeTone(token.price_change_h1) === "err" ? "err" : "ok"}
						meta="as reported at the snapshot time"
					/>
					<Stat
						label="24h change"
						value={changeText(token.price_change_h24)}
						tone={changeTone(token.price_change_h24) === "err" ? "err" : "ok"}
						meta="as reported at the snapshot time"
					/>
					<Stat
						label="Safety"
						value={<Badge tone="warn">unknown</Badge>}
						meta={`${safetyReason(token.safety)} · not a pass`}
					/>
					<Stat
						label="Freshness"
						value={token.stale ? <Badge tone="warn">STALE</Badge> : <Badge tone="ghost">FRESH</Badge>}
						meta={`${sourceLabel(sourceName)} · ${ageLabel(token.source?.observed_at)}`}
					/>
					<Stat
						label="Quote route"
						value={quote ? quoteKindLabel(quote.kind) : (quoteReason?.code ?? "NO_QUOTE")}
						tone={quote ? undefined : "err"}
						meta={quote ? `${sourceLabel(quote.source.name)} · ${ageLabel(quote.source.observed_at)}` : "fail closed"}
					/>
				</div>
			</Panel>

			<div id="chart">
				<Panel
					title="Candles"
					sub={`${token.symbol} · closed OHLCV · 5m`}
					action={
						<>
							{candlesResult.series?.stale ? <Badge tone="warn">STALE</Badge> : null}
							{candlesResult.series?.source.name === "fixture" ? (
								<Badge tone="ghost">FIXTURE</Badge>
							) : null}
							<Badge tone="ghost">PAPER</Badge>
						</>
					}
					footer={
						candlesResult.series ? (
							<span>
								{sourceLabel(candlesResult.series.source.name)} · observed{" "}
								{ageLabel(candlesResult.series.source.observed_at)} ago ·{" "}
								{candlesResult.series.candles.length} closed bar
								{candlesResult.series.candles.length === 1 ? "" : "s"} ·{" "}
								{candlesResult.series.stale
									? "the API marked this series stale"
									: "not marked stale by the API"}
								. Closing prices are discovery data, not executable quotes.
							</span>
						) : (
							<span>
								The candle request failed with{" "}
								<span className="mono">{candlesResult.error?.code ?? "ERROR"}</span>. No
								chart is drawn from a failed read.
							</span>
						)
					}
				>
					{candlesResult.series && candlesResult.series.candles.length > 0 ? (
						<CandleChart
							candles={candlesResult.series.candles}
							symbol={token.symbol}
							timeframe={candlesResult.series.timeframe}
						/>
					) : (
						<CandleEmptyState
							reason={
								candlesResult.error?.code ??
								candlesResult.series?.reason ??
								"CANDLES_EMPTY"
							}
						/>
					)}
				</Panel>
			</div>

			<div className="split">
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
										<th scope="col" className="num">
											Price
										</th>
										<th scope="col" className="num">
											Liquidity
										</th>
										<th scope="col" className="num col-hide-sm">
											Vol 24h
										</th>
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
													<a
														className="small"
														href={pair.url}
														target="_blank"
														rel="noopener noreferrer"
													>
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
								execution gate. Safety reason:{" "}
								<span className="mono">{safetyReason(token.safety)}</span>.
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
												<td className="small muted">
													{value ?? "— · no value is assumed"}
												</td>
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
						<Panel
							title="Quote"
							sub={`${token.symbol} · paper quote`}
							action={
								quote ? (
									<Badge tone={quote.kind === "paper_mark" ? "warn" : "ok"}>
										{quoteKindLabel(quote.kind)}
									</Badge>
								) : (
									<Badge tone="err">{quoteReason?.code ?? "NO_QUOTE"}</Badge>
								)
							}
						>
							<div className="btn-group mb-12" role="group" aria-label="Quote side">
								{(["buy", "sell"] as Side[]).map((option) => (
									<Link
										key={option}
										className={`btn sm${side === option ? " primary" : ""}`}
										href={`${pagePath}?side=${option}&amount=${encodeURIComponent(amount)}`}
										aria-current={side === option ? "true" : undefined}
									>
										{option === "buy" ? "Buy" : "Sell"}
									</Link>
								))}
							</div>
							<form action={pagePath} method="get" className="stack-sm mb-12">
								<input type="hidden" name="side" value={side} />
								<label className="field">
									<span>Amount USD · PAPER</span>
									<input
										className="input mono"
										name="amount"
										type="text"
										inputMode="decimal"
										defaultValue={amount}
										aria-label="Quote amount in USD"
									/>
								</label>
								<div className="row">
									<button className="btn" type="submit">
										Preview quote
									</button>
									<span className="small muted">Server re-fetches the quote.</span>
								</div>
							</form>

							{quote && (
								<>
									<table className="table compact mb-12">
										<tbody>
											<QuoteRow k="Side" v={quote.side} paper={false} />
											<QuoteRow
												k="Amount in"
												v={
													quote.side === "buy"
														? formatUsd(quote.amount_in)
														: `${formatAmount(quote.amount_in)} ${token.symbol}`
												}
											/>
											<QuoteRow
												k="Expected out"
												v={
													quote.side === "buy"
														? `${formatAmount(quote.amount_out)} ${token.symbol}`
														: formatUsd(quote.amount_out)
												}
											/>
											<QuoteRow k="Price" v={formatUsdPrice(quote.price)} />
											<QuoteRow
												k="Price impact"
												v={
													quote.price_impact_pct
														? `${quote.price_impact_pct}%`
														: "—"
												}
												sub={quote.price_impact_pct ? "venue reported" : "not reported"}
											/>
											<QuoteRow k="Venue fee" v={formatUsd(quote.fee_usd)} />
											<QuoteRow
												k="Route"
												v={quote.route ?? "paper mark · no executable route"}
												paper={false}
												tone={quote.route ? undefined : "warn"}
											/>
											<QuoteRow k="Kind" v={quoteKindLabel(quote.kind)} paper={false} />
											<QuoteRow
												k="Source · age"
												v={`${sourceLabel(quote.source.name)} · ${ageLabel(quote.source.observed_at)}`}
												paper={false}
											/>
										</tbody>
									</table>
									<CostSheet quote={quote} symbol={token.symbol} />
									{quote.kind === "paper_mark" && (
										<div className="note warn">
											<strong>PAPER · MARK.</strong> This chain has no executable venue in
											P0. A fill would use the DexScreener pair mark, subject to API risk
											caps. It is not a live route.
										</div>
									)}
								</>
							)}

							{!quote && quoteReason && (
								<div className="note err">
									<div className="row mb-8">
										<Badge tone="err">{quoteReason.code}</Badge>
										{quoteReason.retryable && <Badge tone="warn">retryable</Badge>}
									</div>
									<div className="small">{reasonMessage(quoteReason)}</div>
									<div className="small muted mt-8">
										Reason from the API: {quoteReason.message}
									</div>
								</div>
							)}
						</Panel>
					</div>

					<div id="ticket">
					<Panel
						title="Paper trade ticket"
						action={<Badge tone="ghost">PAPER</Badge>}
						footer="Paper only. The ticket sends chain, token, side, USD amount and a browser-generated idempotency key. No key material is collected."
					>
						<PaperTicket
							chain={chain}
							tokenAddress={token.address}
							symbol={token.symbol}
							side={side}
							amount={amount}
							quote={quote}
							quoteReason={quoteReason}
						/>
					</Panel>
					</div>
				</div>
			</div>
		</AppShell>
	);
}
