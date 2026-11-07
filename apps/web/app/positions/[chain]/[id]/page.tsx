import Link from "next/link";
import { notFound } from "next/navigation";
import { Badge, Panel } from "@/components/ui";
import { CandleChart } from "@/components/candle-chart";
import { ModeBanner } from "@/lib/live-banner";
import { getCandles, getToken, type CandleSeries } from "@/lib/api";
import { sourceLabel, ageLabel } from "@/lib/live-format";

export const dynamic = "force-dynamic";

type SearchParams = Record<string, string | string[] | undefined>;

function one(value: string | string[] | undefined): string {
	return Array.isArray(value) ? (value[0] ?? "") : (value ?? "");
}

export default async function PositionDetailPage({
	params,
	searchParams,
}: {
	params: Promise<{ chain: string; id: string }>;
	searchParams: Promise<SearchParams>;
}) {
	const { chain, id } = await params;
	const raw = await searchParams;
	const timeframe = one(raw.tf) || "5m";

	const tokenResult = await getToken(chain, id)
		.then((snapshot) => ({ ok: true as const, snapshot }))
		.catch(() => ({ ok: false as const }));

	let series: CandleSeries | null = null;
	try {
		series = await getCandles({ chain, address: id, timeframe, limit: 120 });
	} catch {
		series = null;
	}

	if (!tokenResult.ok) {
		notFound();
	}
	const token = tokenResult.snapshot;

	let chartSeries = series;
	const chartMode = one(raw.chart);
	if (chartMode === "stale" && chartSeries) {
		chartSeries = { ...chartSeries, stale: true };
	}
	if (chartMode === "empty") {
		chartSeries = {
			chain,
			token_address: token.address,
			timeframe,
			pool_address: null,
			candles: [],
			source: chartSeries?.source ?? { name: "fixture", url: null, observed_at: new Date().toISOString() },
			stale: false,
			reason: "CANDLES_EMPTY",
		};
	}
	if (chartMode === "no-pool") {
		chartSeries = {
			chain,
			token_address: token.address,
			timeframe,
			pool_address: null,
			candles: [],
			source: chartSeries?.source ?? { name: "fixture", url: null, observed_at: new Date().toISOString() },
			stale: false,
			reason: "NO_POOL",
		};
	}

	return (
		<main id="main">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">
						<Link href="/positions">Positions</Link> / {chain} / {token.symbol}
					</div>
					<h1>{token.name}</h1>
					<p className="sub">{token.symbol}</p>
				</div>
				<div className="page-actions">
					<Badge tone="ghost">PAPER</Badge>
					<Link className="btn" href={`/tokens/${chain}/${id}`}>
						token page
					</Link>
				</div>
			</header>
			<ModeBanner
				source={token.source?.name ?? "fixture"}
				observedAt={token.source?.observed_at}
				stale={token.stale}
			/>
			<div id="position-meta">
				{token.symbol} · {chain} · {token.address}
			</div>
			{chartSeries && chartSeries.candles.length > 0 ? (
				<Panel
					title="Candles"
					sub={`${timeframe} · closed bars only`}
					flush
					footer={`${sourceLabel(chartSeries.source.name)} · ${ageLabel(chartSeries.source.observed_at)} · ${chartSeries.stale ? "STALE" : "not stale"}`}
				>
					<CandleChart series={chartSeries} />
				</Panel>
			) : (
				<Panel title="Candles" sub="no closed bars" flush>
					<div className="note">
						{chartSeries?.reason ?? "CANDLES_EMPTY"} · no candle data
					</div>
				</Panel>
			)}
		</main>
	);
}