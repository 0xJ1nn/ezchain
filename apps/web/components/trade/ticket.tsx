"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState, useTransition, type PointerEvent as ReactPointerEvent } from "react";
import { CostSheet } from "@/components/cost-sheet";
import {
	ApiError,
	postPaperOrder,
	type PaperOrder,
	type Quote,
	type Reason,
	type Side,
} from "@/lib/api";
import { ageLabel, formatAmount, formatPrice, formatUsd, quoteKindLabel, sourceLabel } from "@/lib/live-format";
import {
	priorityLabel,
	PRIORITY_OPTIONS,
	slippageLabel,
	SLIPPAGE_OPTIONS,
	useTicketTradeSettings,
} from "@/lib/trading-prefs";
import { fmt } from "./format";
import type { ExitLines } from "./types";
import styles from "./trade.module.css";

const HOLD_MS = 650;

type OrderType = "market" | "limit" | "stop";
type TicketState =
	| { kind: "idle" }
	| { kind: "submitting" }
	| { kind: "accepted"; order: PaperOrder }
	| { kind: "rejected"; order: PaperOrder; message: string }
	| { kind: "error"; code: string; message: string };

function newKey(): string {
	if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
	return `web-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function sameAmount(a: string, b: string): boolean {
	const x = Number(a);
	const y = Number(b);
	return Number.isFinite(x) && Number.isFinite(y) && x === y;
}

interface Stamp {
	label: string;
	sub: string;
	side: Side;
	out: boolean;
}

export function Ticket({
	chain,
	tokenAddress,
	symbol,
	side,
	amount,
	quote,
	quoteReason,
	now,
	maxUsd,
	lines,
	timeframe,
}: {
	chain: string;
	tokenAddress: string;
	symbol: string;
	side: Side;
	amount: string;
	quote: Quote | null;
	quoteReason: Reason | null;
	now: number;
	/** Buying power from the paper account, or null when unknown. */
	maxUsd: number | null;
	lines: ExitLines;
	timeframe?: string;
}) {
	const router = useRouter();
	const pathname = usePathname();
	const [, startTransition] = useTransition();
	const [localSide, setLocalSide] = useState<Side>(side);
	const [localAmount, setLocalAmount] = useState(amount);
	const [orderType, setOrderType] = useState<OrderType>("market");
	const [chip, setChip] = useState<number | null>(null);
	const [state, setState] = useState<TicketState>({ kind: "idle" });
	const [key, setKey] = useState<string | null>(null);
	const [stamp, setStamp] = useState<Stamp | null>(null);
	const [holding, setHolding] = useState(false);
	const holdTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
	const { prefs, setSlippage, setPriority } = useTicketTradeSettings();

	useEffect(() => {
		setLocalSide(side);
		setLocalAmount(amount);
	}, [side, amount]);

	// A new quote is a new trade: reset the hold, the stamp and the idempotency key.
	const quoteIdentity = quote
		? `${quote.side}|${quote.amount_in}|${quote.token_out}|${quote.source.observed_at}`
		: `no-quote|${side}|${amount}`;
	useEffect(() => {
		setKey(newKey());
		setState({ kind: "idle" });
		setStamp(null);
		setHolding(false);
		setChip(null);
	}, [quoteIdentity]);

	useEffect(() => () => {
		if (holdTimer.current) clearTimeout(holdTimer.current);
	}, []);

	const quoteUsd = quote?.side === "buy" ? quote.amount_in : amount;
	const matches = quote !== null && quote.side === localSide && sameAmount(localAmount, quoteUsd);
	const amountValid = /^\d+(\.\d{1,2})?$/.test(localAmount.trim()) && Number(localAmount) > 0;
	const disabled = !quote || !matches || !key || state.kind === "submitting" || state.kind === "accepted" || orderType !== "market";

	function preview(nextSide: Side, nextAmount: string) {
		if (!/^\d+(\.\d{1,2})?$/.test(nextAmount.trim()) || Number(nextAmount) <= 0) return;
		const query = new URLSearchParams({ side: nextSide, amount: nextAmount.trim() });
		if (timeframe) query.set("timeframe", timeframe);
		startTransition(() => router.push(`${pathname}?${query.toString()}`));
	}

	async function send() {
		if (!quote || !matches || !key || orderType !== "market") return;
		setState({ kind: "submitting" });
		try {
			const order = await postPaperOrder({
				chain,
				token_address: tokenAddress,
				side: quote.side === "sell" ? "sell" : "buy",
				amount_usd: localAmount,
				idempotency_key: key,
			});
			if (order.status === "rejected" || order.status === "cancelled") {
				setState({ kind: "rejected", order, message: order.reason ?? "the API returned no reason" });
				return;
			}
			setState({ kind: "accepted", order });
			const buy = order.side === "buy";
			setStamp({
				label: buy ? "PAPER FILLED" : "PAPER SOLD",
				sub: `${formatAmount(order.amount_usd)} USD · ${order.id}`,
				side: order.side === "sell" ? "sell" : "buy",
				out: false,
			});
			setTimeout(() => setStamp((current) => (current ? { ...current, out: true } : null)), 2200);
		} catch (error) {
			setState({
				kind: "error",
				code: error instanceof ApiError ? error.code : "WEB_ERROR",
				message: error instanceof Error ? error.message : String(error),
			});
		}
	}

	function stopHold() {
		if (holdTimer.current) clearTimeout(holdTimer.current);
		holdTimer.current = null;
		setHolding(false);
	}

	function onDown(event: ReactPointerEvent<HTMLButtonElement>) {
		if (disabled || event.button !== 0) return;
		event.preventDefault();
		event.currentTarget.setPointerCapture(event.pointerId);
		setHolding(true);
		holdTimer.current = setTimeout(() => {
			holdTimer.current = null;
			setHolding(false);
			void send();
		}, HOLD_MS);
	}

	function choosePreset(value: number) {
		const text = value.toFixed(2);
		setLocalAmount(text);
		setChip(value);
		preview(localSide, text);
	}

	function presetLabel(value: number): string {
		return `$${Number.isInteger(value) ? value : value.toFixed(2)}`;
	}

	const badges: Array<{ text: string; cls: string }> = [];
	if (lines.sl !== null && lines.entry !== null) {
		badges.push({ text: `SL ${((lines.sl / lines.entry - 1) * 100).toFixed(1)}%`, cls: styles.badgeRed });
	}
	if (lines.tp.length > 0) {
		badges.push({ text: `TP ×${lines.tp.length} · ${lines.tp.map((t) => `+${((t.p / (lines.entry ?? t.p) - 1) * 100).toFixed(0)}%`).join(" / ")}`, cls: styles.badgeGreen });
	}
	if (lines.trail !== null) {
		badges.push({ text: `Trail ${fmt(lines.trail)}`, cls: styles.badgeAmber });
	}

	return (
		<section id="ticket" className={`${styles.ticket} ${state.kind === "accepted" ? styles.thud : ""}`} data-testid="trade-ticket" aria-label="Trade ticket">
			<div className={styles.ticketHd}>
				<span className={`${styles.coin} ${styles.coinSm}`} aria-hidden="true">{symbol[0]}</span>
				<div>
					<b>{symbol}</b>
					<small>{chain} · {symbol} · {quote ? formatPrice(quote.price) : "—"}</small>
				</div>
				<span className={`${styles.deltaPill} ${styles.up}`}>
					{quote?.price_impact_pct ? `${quote.price_impact_pct}% impact` : "—"}
				</span>
			</div>

			<div className={styles.segSide} role="group" aria-label="Trade side">
				{(["buy", "sell"] as Side[]).map((option) => (
					<button
						key={option}
						type="button"
						className={localSide === option ? (option === "buy" ? styles.onBuy : styles.onSell) : undefined}
						aria-pressed={localSide === option}
						data-testid={`ticket-side-${option}`}
						onClick={() => { setLocalSide(option); preview(option, localAmount); }}
					>
						{option === "buy" ? "Buy" : "Sell"}
					</button>
				))}
			</div>

			<div className={styles.segSide} role="group" aria-label="Order type">
				{(["market", "limit", "stop"] as OrderType[]).map((option) => (
					<button
						key={option}
						type="button"
						className={orderType === option ? styles.onBuy : undefined}
						aria-pressed={orderType === option}
						data-testid={`ticket-type-${option}`}
						onClick={() => setOrderType(option)}
					>
						{option[0]!.toUpperCase() + option.slice(1)}
					</button>
				))}
			</div>

			<label className={styles.field}>
				<input
					name="amount"
					inputMode="decimal"
					value={localAmount}
					onChange={(e) => { setLocalAmount(e.target.value); setChip(null); }}
					aria-label="Amount in USD"
					data-testid="ticket-amount"
				/>
				<span className={styles.unit}>USD</span>
			</label>

			<div className={styles.lbl} data-testid="quick-buy-label">
				<span>Quick buy</span>
				<a className={styles.link} href="/settings/trading">Edit</a>
			</div>
			<div className={styles.chips} role="group" aria-label="Quick buy presets">
				{prefs.presets.map((value) => (
					<button
						key={value}
						type="button"
						className={chip === value ? styles.on : undefined}
						aria-pressed={chip === value}
						data-testid={`ticket-preset-${value}`}
						onClick={() => choosePreset(value)}
					>
						{presetLabel(value)}
					</button>
				))}
			</div>

			<div className={styles.tset} data-testid="ticket-trade-settings">
				<label className={styles.sel}>
					<span>Slippage</span>
					<b>
						{slippageLabel(prefs.slippage)}
						<select
							aria-label="Slippage"
							data-testid="ticket-slippage"
							value={prefs.slippage}
							onChange={(e) => setSlippage(e.target.value)}
						>
							{SLIPPAGE_OPTIONS.map((option) => (
								<option key={option} value={option}>{slippageLabel(option)}</option>
							))}
						</select>
					</b>
				</label>
				<label className={styles.sel}>
					<span>Priority fee</span>
					<b>
						{priorityLabel(prefs.priority)}
						<select
							aria-label="Priority fee"
							data-testid="ticket-priority"
							value={prefs.priority}
							onChange={(e) => setPriority(e.target.value)}
						>
							{PRIORITY_OPTIONS.map((option) => (
								<option key={option.id} value={option.id}>{option.label}</option>
							))}
						</select>
					</b>
				</label>
			</div>

			<div className={styles.quote} data-testid="ticket-quote">
				{quote && matches ? (
					<>
						<div className={styles.kv}><span>You get</span><b>{quote.side === "buy" ? `≈ ${formatAmount(quote.amount_out)} ${symbol}` : formatUsd(quote.amount_out)}</b></div>
						<div className={styles.kv}><span>Price</span><b>{formatPrice(quote.price)}</b></div>
						<div className={styles.kv}>
							<span>Price impact</span>
							<b title={quote.price_impact_pct ? "Reported by the venue for this quote" : "The venue did not report a price impact"}>
								{quote.price_impact_pct ? `${quote.price_impact_pct}%` : "—"}
							</b>
						</div>
						<div className={styles.kv}><span>Simulated fee</span><b>{formatUsd(quote.fee_usd)}</b></div>
						<div className={styles.kv}><span>Quote</span><b>{sourceLabel(quote.source.name)} · {ageLabel(quote.source.observed_at, now)}</b></div>
						<div className={styles.kv}><span>Kind</span><b>{quoteKindLabel(quote.kind)}</b></div>
					</>
				) : (
					<div className={styles.kv}><span>Quote</span><b>{quoteReason?.code ?? "NO_QUOTE"}</b></div>
				)}
			</div>

			{quote && matches && <CostSheet quote={quote} symbol={symbol} now={now} />}

			{!amountValid && <div className={styles.ticketFoot}>Enter a positive USD amount with at most two decimals.</div>}
			{orderType !== "market" && (
				<div className={styles.ticketFoot} role="status">
					Paper orders are market-only. Limit and stop are shown for reference and cannot be sent.
				</div>
			)}

			<div className={styles.exsum}>
				{badges.map((badge) => (
					<span key={badge.text} className={`${styles.badge} ${badge.cls}`}>{badge.text}</span>
				))}
				{badges.length === 0 && <span className={styles.badge}>No exits set</span>}
			</div>

			<div
				className={styles.prof}
				data-testid="ticket-profile"
				title="The API does not publish an auto profile for this token, so no exit profile is assumed."
			>
				<svg aria-hidden="true" viewBox="0 0 24 24" width="14" height="14">
					<use href="/icons/ion.svg#sparkles" />
				</svg>
				<span>Profile: <b>—</b> · exits: <b>—</b></span>
			</div>

			<button
				type="button"
				className={`${styles.btn} ${styles.xl} ${styles.block} ${localSide === "sell" ? styles.sell : styles.buy} ${holding ? styles.holding : ""} ${disabled ? styles.disabled : ""}`}
				style={{ ["--hold" as string]: `${HOLD_MS}ms` }}
				disabled={disabled}
				data-testid="trade-submit"
				data-hold-ms={HOLD_MS}
				aria-label={localSide === "sell" ? `Hold to sell ${symbol}` : `Hold to buy ${symbol}`}
				onPointerDown={onDown}
				onPointerUp={stopHold}
				onPointerCancel={stopHold}
				onPointerLeave={stopHold}
			>
				<i className={styles.fill} aria-hidden="true" />
				<b>
					{state.kind === "submitting" ? (
						<>Filling…</>
					) : (
						<>{localSide === "sell" ? "Hold to sell" : "Hold to buy"} {symbol}</>
					)}
				</b>
				<span className={styles.kv}>{formatUsd(localAmount)}</span>
			</button>

			<div className={styles.ticketFoot}>
				<span className={styles.modePaper}><span className={styles.xDot} />PAPER</span>
				Paper-filled at the mark. Slippage and priority fee apply in real mode.
			</div>

			{stamp && (
				<div
					className={`${styles.stamp} ${stamp.side === "sell" ? styles.stampSell : ""} ${styles.stampGo} ${stamp.out ? styles.stampOut : ""}`}
					data-testid="fill-stamp"
					role="status"
				>
					{stamp.label}
					<small>{stamp.sub}</small>
				</div>
			)}

			{state.kind === "accepted" && (
				<div className={`${styles.receipt} ${state.order.side === "sell" ? styles.receiptSell : ""}`} data-testid="fill-receipt">
					<span aria-hidden="true">✓</span>
					<div>
						Paper-filled <b>{formatUsd(state.order.amount_usd)}</b> {symbol}
						<br />
						<small>{state.order.id} · {state.order.quote_kind ?? "paper"}</small>
						<small>No funds moved and no transaction was signed.</small>
					</div>
					<small>now</small>
				</div>
			)}
			{state.kind === "rejected" && (
				<div className={styles.receipt} role="status" data-testid="ticket-error">
					Not filled · {state.message}
				</div>
			)}
			{state.kind === "error" && (
				<div className={styles.receipt} role="status" data-testid="ticket-error">
					Not filled · {state.code}: {state.message}
				</div>
			)}
			<span className={styles.srOnly}>Idempotency key {key ? key.slice(0, 8) : "—"}</span>
		</section>
	);
}
