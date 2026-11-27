"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { Badge, Panel } from "@/components/ui";
import {
	ApiError,
	postPaperOrder,
	type Candle,
	type PaperOrder,
	type Quote,
	type Reason,
	type Side,
} from "@/lib/api";
import { formatUsd, formatUsdPrice, quoteKindLabel } from "@/lib/live-format";
import {
	createProtectionArm,
	type PaperApiMode,
	type ProtectionArmInput,
} from "@/lib/protection-api";
import { ExitsPanel, type ExitsNotice } from "./exits-panel";
import { HoldButton } from "./hold-button";
import { Moments } from "./moments";
import {
	TradeChart,
	type ChartType,
	type ExitLineKey,
	type TradeMarker,
} from "./trade-chart";

function dec(value: number): string {
	if (!Number.isFinite(value)) return "0";
	const text = value.toFixed(8).replace(/0+$/, "").replace(/\.$/, "");
	return text === "" || text === "-0" ? "0" : text;
}

function newIdempotencyKey(): string {
	if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
		return crypto.randomUUID();
	}
	return `web-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function baseEntry(quote: Quote | null, candles: Candle[]): number {
	const fromQuote = quote ? Number(quote.price) : Number.NaN;
	const fromSeries = candles.length > 0 ? Number(candles[candles.length - 1]!.close) : Number.NaN;
	const value = Number.isFinite(fromQuote) && fromQuote > 0 ? fromQuote : fromSeries;
	return Number.isFinite(value) && value > 0 ? value : 0;
}

/**
 * One Trade screen: the chart and the ticket share a single state. Dragging an exit
 * line writes the same number the panel shows; saving and confirming both go through the
 * paper clients. A fill is stamped only after the API accepts it.
 */
export function TradeScreen({
	chain,
	tokenAddress,
	symbol,
	timeframe,
	candles,
	quote,
	quoteReason,
	side,
	amount,
	mode,
}: {
	chain: string;
	tokenAddress: string;
	symbol: string;
	timeframe: string;
	candles: Candle[];
	quote: Quote | null;
	quoteReason: Reason | null;
	side: Side;
	amount: string;
	mode: PaperApiMode;
}) {
	const router = useRouter();
	const [localSide, setLocalSide] = useState<Side>(side);
	const [chartType, setChartType] = useState<ChartType>("candles");
	const [showEma20, setShowEma20] = useState(true);
	const [showEma50, setShowEma50] = useState(true);
	const [showVolume, setShowVolume] = useState(true);
	const [showAth, setShowAth] = useState(true);

	const entry = useMemo(() => baseEntry(quote, candles), [quote, candles]);
	const [stop, setStop] = useState(() => Number(dec(baseEntry(quote, candles) * 0.97)));
	const [targets, setTargets] = useState<number[]>(() => {
		const base = baseEntry(quote, candles);
		return [base * 1.04, base * 1.07, base * 1.1].map((value) => Number(dec(value)));
	});
	const [sizes, setSizes] = useState<string[]>(["40", "35", "25"]);
	const [trail, setTrail] = useState(() => Number(dec(baseEntry(quote, candles) * 0.99)));
	const [trailStart, setTrailStart] = useState(() => Number(dec(baseEntry(quote, candles) * 1.06)));
	const [trailDistance, setTrailDistance] = useState("1.5");

	const [exitsNotice, setExitsNotice] = useState<ExitsNotice | null>(null);
	const [savingExits, setSavingExits] = useState(false);
	const [fill, setFill] = useState<PaperOrder | null>(null);
	const [markers, setMarkers] = useState<TradeMarker[]>([]);
	const [ticketError, setTicketError] = useState<{ code: string; message: string } | null>(null);
	const [submitting, setSubmitting] = useState(false);
	const [idem, setIdem] = useState(() => newIdempotencyKey());

	useEffect(() => {
		setLocalSide(side);
	}, [side]);

	const quoteIdentity = quote
		? `${quote.side}|${quote.amount_in}|${quote.token_out}|${quote.source.observed_at}`
		: `no-quote|${side}|${amount}`;

	useEffect(() => {
		setIdem(newIdempotencyKey());
		setFill(null);
		setMarkers([]);
		setTicketError(null);
	}, [quoteIdentity]);

	const rewardRisk = useMemo(() => {
		const risk = entry - stop;
		if (!(risk > 0)) return "—";
		const reward = targets.reduce(
			(sum, target, index) => sum + Math.max(0, target - entry) * (Number(sizes[index]) / 100),
			0,
		);
		return `1 : ${(reward / risk).toFixed(1)}`;
	}, [entry, stop, targets, sizes]);

	const athPrice = useMemo(
		() => (candles.length > 0 ? Math.max(...candles.map((candle) => Number(candle.high) || 0)) : null),
		[candles],
	);

	function changeLine(key: ExitLineKey, price: number) {
		if (!(price > 0)) return;
		const rounded = Number(dec(price));
		if (key === "stop") setStop(Math.min(rounded, Number(dec(entry * 0.999))));
		else if (key === "trail") setTrail(rounded);
		else setTargets((current) => current.map((value, index) => (index === Number(key.slice(3)) ? rounded : value)));
	}

	async function saveExits() {
		setExitsNotice(null);
		if (!(stop > 0)) {
			setExitsNotice({ tone: "err", text: "Enter a positive stop price before saving." });
			return;
		}
		if (targets.some((target) => !(target > entry))) {
			setExitsNotice({ tone: "err", text: "Every target must sit above the entry price." });
			return;
		}
		const total = sizes.reduce((sum, size) => sum + Number(size), 0);
		if (total !== 100) {
			setExitsNotice({ tone: "err", text: "Target sizes must add up to 100 percent." });
			return;
		}
		const distance = Number(trailDistance);
		if (!(distance > 0)) {
			setExitsNotice({ tone: "err", text: "Enter a positive trailing distance." });
			return;
		}

		const inputs: ProtectionArmInput[] = [
			{
				chain,
				token_address: tokenAddress,
				token_symbol: symbol,
				kind: "stop_loss",
				params: { price_usd: dec(stop) },
			},
			...targets.map((target, index) => ({
				chain,
				token_address: tokenAddress,
				token_symbol: symbol,
				kind: "take_profit" as const,
				params: { price_usd: dec(target), size_pct: sizes[index]! },
			})),
			{
				chain,
				token_address: tokenAddress,
				token_symbol: symbol,
				kind: "trailing_stop",
				params: {
					activation_price_usd: dec(trailStart),
					distance_pct: trailDistance,
					price_usd: dec(trail),
				},
			},
		];

		setSavingExits(true);
		try {
			for (const input of inputs) await createProtectionArm(input, mode);
			setExitsNotice({
				tone: "ok",
				text: "Exits armed: paper stop, three targets and a trailing stop were stored.",
			});
		} catch (error) {
			setExitsNotice({
				tone: "err",
				text: error instanceof Error ? error.message : "The exits could not be stored.",
			});
		} finally {
			setSavingExits(false);
		}
	}

	async function confirmOrder() {
		if (!quote || submitting || fill) return;
		setSubmitting(true);
		setTicketError(null);
		try {
			const order = await postPaperOrder({
				chain,
				token_address: tokenAddress,
				side: localSide,
				amount_usd: amount,
				idempotency_key: idem,
			});
			if (order.status === "rejected" || order.status === "cancelled") {
				setTicketError({
					code: "ORDER_REJECTED",
					message: order.reason ?? "the API returned no reason",
				});
			} else {
				setFill(order);
				setMarkers((current) => [
					...current,
					{ index: candles.length - 1, side: localSide === "sell" ? "sell" : "buy" },
				]);
			}
		} catch (error) {
			const apiError = error instanceof ApiError ? error : null;
			setTicketError({
				code: apiError?.code ?? "WEB_ERROR",
				message: error instanceof Error ? error.message : String(error),
			});
		} finally {
			setSubmitting(false);
		}
	}

	function chooseSide(next: Side) {
		setLocalSide(next);
		const query = new URLSearchParams({ chain, token: tokenAddress, side: next, amount, timeframe });
		router.push(`/trade?${query.toString()}`);
	}

	const holdLabel = `Hold to ${localSide} ${symbol}`;
	const blockReason = quoteReason
		? `${quoteReason.code}: ${quoteReason.message}`
		: !quote
			? "No quote is rendered yet. Nothing can be sent."
			: null;
	const canSend = Boolean(quote && !quoteReason && !fill && entry > 0);

	return (
		<div className="stack" data-testid="trade-screen">
			<div className="split">
				<div className="stack">
					<Panel
						title="Trade chart"
						sub={`${symbol} · ${timeframe} · ${candles.length} closed bars`}
						action={
							<>
								<Badge tone="ghost">PAPER</Badge>
								<div className="seg" role="group" aria-label="Chart type">
									{(["candles", "line", "area"] as ChartType[]).map((type) => (
										<button
											key={type}
											type="button"
											className={chartType === type ? "on" : ""}
											aria-pressed={chartType === type}
											data-testid={`chart-type-${type}`}
											onClick={() => setChartType(type)}
										>
											{type[0]!.toUpperCase() + type.slice(1)}
										</button>
									))}
								</div>
							</>
						}
						footer="Closing bars come from the candle endpoint. EMA values and the series high are computed from those bars; exit lines are your own state, draggable or editable in the panel."
					>
						<TradeChart
							candles={candles}
							symbol={symbol}
							timeframe={timeframe}
							type={chartType}
							showEma20={showEma20}
							showEma50={showEma50}
							showVolume={showVolume}
							showAth={showAth}
							entry={entry}
							markers={markers}
							lines={{ stop, targets, trail }}
							onLineChange={changeLine}
						/>
						<div className="row mt-12" style={{ gap: 8, flexWrap: "wrap" }} role="group" aria-label="Chart indicators">
							{(
								[
									["chart-toggle-ema20", "EMA 20", showEma20, setShowEma20],
									["chart-toggle-ema50", "EMA 50", showEma50, setShowEma50],
									["chart-toggle-vol", "Volume", showVolume, setShowVolume],
									["chart-toggle-ath", "ATH", showAth, setShowAth],
								] as const
							).map(([testid, label, value, setter]) => (
								<button
									key={testid}
									type="button"
									className={`btn sm${value ? " primary" : ""}`}
									aria-pressed={value}
									data-testid={testid}
									onClick={() => setter(!value)}
								>
									{label}
								</button>
							))}
						</div>
					</Panel>
					<Moments athPrice={athPrice} />
				</div>

				<div className="stack">
					<Panel
						title="Ticket"
						sub={`${symbol} · paper order`}
						action={<Badge tone="ghost">PAPER</Badge>}
						footer="The key sends only after a held gesture. The stamp appears only when the paper order endpoint accepts the order. No key material is collected."
					>
						<div className="stack-sm">
							<div className="seg" role="group" aria-label="Ticket side">
								{(["buy", "sell"] as Side[]).map((option) => (
									<button
										key={option}
										type="button"
										aria-pressed={localSide === option}
										data-testid={`trade-side-${option}`}
										onClick={() => chooseSide(option)}
									>
										{option === "buy" ? "Buy" : "Sell"}
									</button>
								))}
							</div>

							<div className="row">
								<span className="micro">Entry mark</span>
								<span className="mono" data-testid="trade-entry">{dec(entry)}</span>
								<span className="small muted">{formatUsdPrice(dec(entry))}</span>
							</div>
							<div className="row">
								<span className="micro">Notional · PAPER</span>
								<span className="mono">{formatUsd(amount)}</span>
							</div>

							{blockReason && <div className="note err">{blockReason}</div>}

							<HoldButton
								label={holdLabel}
								side={localSide}
								disabled={!canSend || submitting}
								onConfirm={confirmOrder}
							/>

							{submitting && <div className="small muted">Sending the paper order…</div>}

							{ticketError && (
								<div className="note err" data-testid="trade-ticket-error" role="status">
									<Badge tone="err">{ticketError.code}</Badge>{" "}
									<span className="small">{ticketError.message}</span>
									<div className="small muted mt-8">Nothing was filled. Retrying keeps the same idempotency key.</div>
								</div>
							)}

							{fill && (
								<div className="note ok" data-testid="fill-stamp" role="status">
									<div className="row mb-8">
										<Badge tone="ok">PAPER FILL</Badge>
										<span className="mono">{fill.id}</span>
										<Badge tone="ghost">{fill.status}</Badge>
									</div>
									<div className="small">
										{fill.side.toUpperCase()} {formatUsd(fill.amount_usd)} ·{" "}
										{fill.quote_kind ? quoteKindLabel(fill.quote_kind) : "PAPER"} ·{" "}
										{fill.filled_at ? `filled ${fill.filled_at}` : `created ${fill.created_at}`}
									</div>
									<div className="small muted mt-8">
										The API accepted this paper order. No funds moved and no transaction was signed.
									</div>
								</div>
							)}

							<div className="small muted">
								Paper only · idempotency {idem.slice(0, 8)}… ·{" "}
								<Link href={`/tokens/${chain}/${tokenAddress}`}>Back to token</Link>
							</div>
						</div>
					</Panel>

					<ExitsPanel
						entry={entry}
						stop={stop}
						targets={targets}
						sizes={sizes}
						trail={trail}
						trailStart={trailStart}
						trailDistance={trailDistance}
						rewardRisk={rewardRisk}
						notice={exitsNotice}
						busy={savingExits}
						onStop={setStop}
						onTarget={(index, value) =>
							setTargets((current) => current.map((item, i) => (i === index ? value : item)))
						}
						onSize={(index, value) =>
							setSizes((current) => current.map((item, i) => (i === index ? value : item)))
						}
						onBreakeven={() => setStop(entry)}
						onTrail={setTrail}
						onTrailStart={setTrailStart}
						onTrailDistance={setTrailDistance}
						onSave={saveExits}
					/>
				</div>
			</div>
		</div>
	);
}
