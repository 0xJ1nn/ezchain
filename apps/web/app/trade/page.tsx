import Link from "next/link";
import { notFound } from "next/navigation";
import { CandleEmptyState } from "@/components/candle-chart";
import { AppShell } from "@/components/shell";
import { Badge, Panel, Stat } from "@/components/ui";
import { TradeScreen } from "@/components/trade/trade-screen";
import {
	asApiError,
	getChains,
	getQuote,
	getToken,
	getTokenCandles,
	type ApiError,
	type CandleTimeframe,
	type CandlesOut,
	type Chain,
	type Quote,
	type Reason,
	type Side,
} from "@/lib/api";
import {
	DEFAULT_PANEL_TIMEFRAME,
	PANEL_TIMEFRAMES,
	parsePanelCandleTimeframe,
} from "@/lib/candle-view";
import { ModeBanner } from "@/lib/live-banner";
import {
	ageLabel,
	changeText,
	formatUsdCompact,
	formatUsdPrice,
	initials,
	quoteKindLabel,
	sourceLabel,
} from "@/lib/live-format";
import { webMode } from "@/lib/protection-api";

export const dynamic = "force-dynamic";

type SearchParams = Record<string, string | string[] | undefined>;

function one(value: string | string[] | undefined): string {
	return Array.isArray(value) ? (value[0] ?? "") : (value ?? "");
}

function readAmount(value: string): string {
	const raw = value.trim();
	return /^\d+(\.\d{1,2})?$/.test(raw) && Number(raw) > 0 ? raw : "100";
}

function TradePicker({ chain }: { chain: string }) {
	return (
		<AppShell active="markets">
			<header className="page-head">
				<div className="titles">
					<h1>Trade</h1>
					<p className="sub">One chart, one ticket, every exit on the screen.</p>
				</div>
			</header>
			<Panel title="Choose a token" sub="chain and token address" action={<Badge tone="ghost">PAPER</Badge>}>
				<form className="stack-sm" action="/trade" method="get">
					<label className="field">
						<span>Chain</span>
						<input className="input" name="chain" defaultValue={chain || "solana"} aria-label="Chain" />
					</label>
					<label className="field">
						<span>Token address</span>
						<input className="input mono" name="token" aria-label="Token address" autoComplete="off" />
					</label>
					<div className="row">
						<button className="btn primary" type="submit">Open trade screen</button>
						<Link className="btn" href="/markets">Browse markets</Link>
					</div>
				</form>
			</Panel>
		</AppShell>
	);
}

function TradeError({ error, chain, token }: { error: ApiError; chain: string; token: string }) {
	return (
		<AppShell active="markets">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs"><Link href="/markets">Markets</Link> / Trade</div>
					<h1>Token unavailable</h1>
					<p className="sub mono">{chain} / {token}</p>
				</div>
			</header>
			<ModeBanner detail="token read failed" />
			<Panel title="No token snapshot" action={<Badge tone="err">{error.code}</Badge>}>
				<div className="note err">{error.message}</div>
				<p className="small muted mt-8">No price, quote or chart is drawn from a failed read.</p>
				<div className="row mt-12">
					<Link className="btn" href={`/trade?chain=${chain}&token=${token}`}>Retry</Link>
					<Link className="btn" href="/markets">Back to markets</Link>
				</div>
			</Panel>
		</AppShell>
	);
}

export default async function TradePage({ searchParams }: { searchParams: Promise<SearchParams> }) {
	const raw = await searchParams;
	const chain = one(raw.chain).trim();
	const tokenAddress = one(raw.token).trim();
	if (!chain || !tokenAddress) return <TradePicker chain={chain} />;

	const side: Side = one(raw.side) === "sell" ? "sell" : "buy";
	const amount = readAmount(one(raw.amount));
	const timeframe = parsePanelCandleTimeframe(raw.timeframe) as CandleTimeframe;

	const tokenResult = await getToken(chain, tokenAddress)
		.then((snapshot) => ({ ok: true as const, snapshot }))
		.catch((error: unknown) => ({ ok: false as const, error: asApiError(error) }));
	if (!tokenResult.ok) {
		if (tokenResult.error.code === "NOT_FOUND" || tokenResult.error.code === "CHAIN_NOT_FOUND") {
			notFound();
		}
		return <TradeError error={tokenResult.error} chain={chain} token={tokenAddress} />;
	}
	const token = tokenResult.snapshot;

	const [quoteResult, candlesResult, chainsResult] = await Promise.all([
		getQuote({ chain, token: token.address, side, amountUsd: amount })
			.then((quote) => ({ quote, reason: null as Reason | null }))
			.catch((error: unknown) => ({
				quote: null as Quote | null,
				reason: asApiError(error).toReason(),
			})),
		getTokenCandles(chain, token.address, timeframe)
			.then((series) => ({ series: series as CandlesOut | null, error: null as ApiError | null }))
			.catch((error: unknown) => ({ series: null as CandlesOut | null, error: asApiError(error) })),
		getChains().catch(() => [] as Chain[]),
	]);

	const quote = quoteResult.quote;
	const quoteReason = quoteResult.reason;
	const chainMeta = chainsResult.find((candidate) => candidate.id === chain);
	const chainLabel = chainMeta?.name ?? chain;
	const series = candlesResult.series;

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
								{token.name} <span className="muted" style={{ fontWeight: 400 }}>{token.symbol}</span>
							</h1>
							<p className="sub small">{chainLabel} · paper trade</p>
						</div>
					</div>
				</div>
				<div className="page-actions">
					<div style={{ textAlign: "right" }}>
						<div className="mono" style={{ fontSize: 20, fontWeight: 600 }}>
							{formatUsdPrice(token.price_usd)}
						</div>
						<div className="small">{changeText(token.price_change_h1)} 1h</div>
						<div className="small muted">{sourceLabel(token.source?.name)} · {ageLabel(token.source?.observed_at)}</div>
					</div>
					<Badge tone="ghost">PAPER</Badge>
					{quote ? (
						<Badge tone={quote.kind === "paper_mark" ? "warn" : "ok"}>{quoteKindLabel(quote.kind)}</Badge>
					) : (
						<Badge tone="err">{quoteReason?.code ?? "NO_QUOTE"}</Badge>
					)}
					<Link className="btn" href={`/tokens/${chain}/${token.address}`}>Token detail</Link>
				</div>
			</header>

			<ModeBanner
				source={token.source?.name}
				observedAt={token.source?.observed_at}
				stale={token.stale}
				detail={token.stale ? "no new paper order should open from this snapshot" : undefined}
			/>

			<Panel flush className="mb-12">
				<div className="grid cols-4">
					<Stat label="Price" value={formatUsdPrice(token.price_usd)} meta={`${sourceLabel(token.source?.name)} · ${ageLabel(token.source?.observed_at)}`} />
					<Stat label="Liquidity" value={formatUsdCompact(token.liquidity_usd)} meta={`${token.pairs.length} pairs · pool TVL`} />
					<Stat label="24h volume" value={formatUsdCompact(token.volume_24h_usd)} meta="pair volume, not depth" />
					<Stat label="Quote route" value={quote ? quoteKindLabel(quote.kind) : (quoteReason?.code ?? "NO_QUOTE")} tone={quote ? undefined : "err"} meta={quote ? "paper quote at this size" : "fail closed"} />
				</div>
			</Panel>

			{series && series.candles.length > 0 ? (
				<TradeScreen
					chain={chain}
					tokenAddress={token.address}
					symbol={token.symbol}
					timeframe={series.timeframe}
					candles={series.candles}
					quote={quote}
					quoteReason={quoteReason}
					side={side}
					amount={amount}
					mode={webMode()}
				/>
			) : (
				<Panel title="Trade chart" sub={`${token.symbol} · ${timeframe}`}>
					<CandleEmptyState
						reason={candlesResult.error?.code ?? series?.reason ?? "CANDLES_EMPTY"}
					/>
				</Panel>
			)}

			<div className="row mt-12 small muted">
				<span>Timeframe</span>
				<div className="seg" role="group" aria-label="Trade timeframe">
					{PANEL_TIMEFRAMES.map((option) => {
						const query = new URLSearchParams({ chain, token: token.address, side, amount });
						if (option.value !== DEFAULT_PANEL_TIMEFRAME) query.set("timeframe", option.value);
						return (
							<Link
								key={option.value}
								className={`btn sm${option.value === timeframe ? " primary" : ""}`}
								aria-current={option.value === timeframe ? "page" : undefined}
								href={`/trade?${query.toString()}`}
							>
								{option.label}
							</Link>
						);
					})}
				</div>
			</div>
		</AppShell>
	);
}
