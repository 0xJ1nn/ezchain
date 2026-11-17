"use client";

import { useEffect, useState, type ReactNode } from "react";
import type { Candle, Quote, Reason, Side } from "@/lib/api";
import type { PaperAccount, PaperFillRecord, PaperPosition } from "@/lib/paper-api";
import { ageLabel, sourceLabel } from "@/lib/live-format";
import type { PaperApiMode } from "@/lib/protection-api";
import { TradeChart } from "./chart";
import { Exits } from "./exits";
import { Moments } from "./moments";
import { Ticket } from "./ticket";
import type { ChartOverlays, ExitLines, FillMarker } from "./types";
import styles from "./trade.module.css";

const OVERLAYS: ChartOverlays = { e1: true, e2: true, vol: true, ath: true };

function number(value: string | null | undefined): number | null {
	if (value === null || value === undefined) return null;
	const parsed = Number(value);
	return Number.isFinite(parsed) ? parsed : null;
}

function indexForTime(candles: Candle[], at: string): number {
	const target = Date.parse(at);
	if (!Number.isFinite(target)) return candles.length - 1;
	let best = 0;
	let distance = Number.POSITIVE_INFINITY;
	candles.forEach((candle, i) => {
		const time = Date.parse(candle.close_time || candle.open_time);
		const gap = Math.abs(time - target);
		if (gap < distance) {
			distance = gap;
			best = i;
		}
	});
	return best;
}

export function TradeWorkspace({
	chain,
	tokenAddress,
	symbol,
	candles,
	timeframe,
	side,
	amount,
	quote,
	quoteReason,
	now,
	volume24hUsd,
	candleSource,
	candleObservedAt,
	candleCount,
	candlesStale,
	account,
	positions,
	fills,
	mode,
	timeframeControl,
}: {
	chain: string;
	tokenAddress: string;
	symbol: string;
	candles: Candle[];
	timeframe: string;
	side: Side;
	amount: string;
	quote: Quote | null;
	quoteReason: Reason | null;
	now: number;
	volume24hUsd: number | null;
	candleSource: string | null;
	candleObservedAt: string | null;
	candleCount: number;
	candlesStale: boolean;
	account: PaperAccount | null;
	positions: PaperPosition[];
	fills: PaperFillRecord[];
	mode: PaperApiMode;
	timeframeControl?: ReactNode;
}) {
	const closes = candles.map((c) => number(c.close)).filter((v): v is number => v !== null);
	const last = closes.length ? closes[closes.length - 1]! : null;
	const ath = candles.length ? Math.max(...candles.map((c) => number(c.high) ?? 0)) : null;

	const ownFills = fills.filter(
		(fill) => fill.chain === chain && fill.token_address === tokenAddress,
	);
	const markers: FillMarker[] = ownFills
		.map((fill) => ({
			index: indexForTime(candles, fill.created_at),
			side: (fill.side === "sell" ? "S" : "B") as "B" | "S",
		}))
		.filter((marker) => marker.index >= 0 && marker.index < candles.length);

	const position = positions.find(
		(p) => p.chain === chain && p.token_address === tokenAddress && number(p.quantity) !== null && (number(p.quantity) ?? 0) > 0,
	);
	const size = position
		? number(position.quantity)
		: (() => {
				const first = ownFills.find((fill) => fill.side === "buy");
				return first ? number(first.quantity) : null;
			})();
	const entry = position
		? number(position.avg_cost_usd)
		: (() => {
				const first = ownFills.find((fill) => fill.side === "buy");
				return first ? number(first.price_usd) : null;
			})();

	// Remember this market so /trade opens where the user last traded.
	useEffect(() => {
		try {
			window.localStorage.setItem(
				"ezchain.lastTradeMarket",
				JSON.stringify({ chain, id: tokenAddress }),
			);
		} catch {
			// Storage is best-effort; /trade still opens the default market.
		}
	}, [chain, tokenAddress]);

	const [lines, setLines] = useState<ExitLines>(() => {
		if (entry === null) return { entry: null, sl: null, tp: [], trail: null };
		return {
			entry,
			sl: entry * 0.972,
			tp: [
				{ p: entry * 1.04, q: 40 },
				{ p: entry * 1.07, q: 35 },
				{ p: entry * 1.1, q: 25 },
			],
			trail: last === null ? null : last * 0.99,
		};
	});

	const change5m =
		timeframe === "5m" && closes.length > 1
			? (closes[closes.length - 1]! / closes[closes.length - 2]! - 1) * 100
			: null;

	return (
		<div className={`${styles.screen} ${styles.trade}`} data-testid="trade-workspace">
			<div className={styles.left}>
			<div className={styles.chartMeta} data-testid="candle-meta">
				{candleSource === "fixture" ? <span className={styles.badge}>FIXTURE</span> : null}
				{candlesStale ? <span className={styles.badgeAmber}>STALE</span> : null}
				<span>
					{sourceLabel(candleSource)} · observed {ageLabel(candleObservedAt)} ago · {candleCount} closed
					bar{candleCount === 1 ? "" : "s"}. Closing prices are discovery data, not executable quotes.
				</span>
			</div>
			<TradeChart
				candles={candles}
				symbol={symbol}
				timeframe={timeframe}
				lines={lines}
				markers={markers}
				size={size}
				type="candles"
				overlays={OVERLAYS}
				volume24hUsd={volume24hUsd}
				onChange={setLines}
				timeframeControl={timeframeControl}
			/>
			</div>
			<div className={styles.right}>
				<Ticket
					chain={chain}
					tokenAddress={tokenAddress}
					symbol={symbol}
					side={side}
					amount={amount}
					quote={quote}
					quoteReason={quoteReason}
					now={now}
					maxUsd={number(account?.cash_usd)}
					lines={lines}
					timeframe={timeframe}
				/>
				<Exits
					chain={chain}
					tokenAddress={tokenAddress}
					symbol={symbol}
					lines={lines}
					onChange={setLines}
					size={size}
					bookUsd={number(account?.equity_usd)}
					last={last}
					mode={mode}
				/>
				<Moments
					symbol={symbol}
					price={last}
					change5m={change5m}
					ath={ath}
					recent={closes.slice(-64)}
				/>
			</div>
		</div>
	);
}
