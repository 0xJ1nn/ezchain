"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState, useTransition } from "react";
import { CostSheet } from "@/components/cost-sheet";
import { Badge } from "@/components/ui";
import {
	ApiError,
	postPaperOrder,
	type PaperOrder,
	type Quote,
	type Reason,
	type Side,
} from "@/lib/api";
import {
	armLabel,
	createProtectionArm,
	type PaperApiMode,
	webMode,
} from "@/lib/protection-api";
import {
	ageLabel,
	formatAmount,
	formatPrice,
	formatUsd,
	quoteKindLabel,
	sourceLabel,
} from "@/lib/live-format";
import styles from "./token.module.css";

type TicketState =
	| { kind: "idle" }
	| { kind: "submitting" }
	| { kind: "accepted"; order: PaperOrder }
	| { kind: "rejected"; order: PaperOrder; message: string }
	| { kind: "error"; code: string; message: string; retryable: boolean };

function newIdempotencyKey(): string {
	if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
		return crypto.randomUUID();
	}
	return `web-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function sameAmount(a: string, b: string): boolean {
	const left = Number(a);
	const right = Number(b);
	return Number.isFinite(left) && Number.isFinite(right) && left === right;
}

const PRESET_STORAGE_KEY = "ezchain.quickTradePresets";
const DEFAULT_PRESETS = [50, 100, 250];
const MAX_PRESETS = 6;

function normalizePresets(value: unknown): number[] {
	if (!Array.isArray(value)) return DEFAULT_PRESETS;
	const values = value
		.map((entry) => Number(entry))
		.filter((entry) => Number.isFinite(entry) && entry > 0);
	return values.length > 0 ? Array.from(new Set(values)).slice(0, MAX_PRESETS) : DEFAULT_PRESETS;
}

function readPresets(): number[] {
	if (typeof window === "undefined") return DEFAULT_PRESETS;
	try {
		const raw = window.localStorage.getItem(PRESET_STORAGE_KEY);
		return raw ? normalizePresets(JSON.parse(raw)) : DEFAULT_PRESETS;
	} catch {
		return DEFAULT_PRESETS;
	}
}

function writePresets(values: number[]) {
	if (typeof window === "undefined") return;
	try {
		window.localStorage.setItem(PRESET_STORAGE_KEY, JSON.stringify(values));
	} catch {
		// Storage is best-effort; the current session still shows the preset.
	}
}

export function QuickTradeBar({
	mode: modeProp,
	chain,
	tokenAddress,
	symbol,
	side,
	amount,
	quote,
	quoteReason,
	now,
}: {
	mode?: PaperApiMode;
	chain: string;
	tokenAddress: string;
	symbol: string;
	side: Side;
	amount: string;
	quote: Quote | null;
	quoteReason: Reason | null;
	/** Server render time, so the client hydrates the same age text. */
	now: number;
}) {
	const mode = modeProp ?? webMode();
	const router = useRouter();
	const pathname = usePathname();
	const [, startTransition] = useTransition();
	const [localSide, setLocalSide] = useState<Side>(side);
	const [localAmount, setLocalAmount] = useState(amount);
	const [formError, setFormError] = useState<string | null>(null);
	const [state, setState] = useState<TicketState>({ kind: "idle" });
	const [idempotencyKey, setIdempotencyKey] = useState<string | null>(null);
	const [presets, setPresets] = useState<number[]>(DEFAULT_PRESETS);
	const [customMode, setCustomMode] = useState(false);
	const [protectOpen, setProtectOpen] = useState(false);
	const [protectPct, setProtectPct] = useState("8");
	const [protectBusy, setProtectBusy] = useState(false);
	const [protectNotice, setProtectNotice] = useState<{ tone: "ok" | "err"; text: string } | null>(null);
	const [protectArmed, setProtectArmed] = useState(false);
	const inputRef = useRef<HTMLInputElement | null>(null);

	useEffect(() => {
		setPresets(readPresets());
	}, []);

	// The server selection is the source of truth. Back/forward and shared links must
	// move the controls with the URL, not leave them matching a quote the page no longer
	// renders.
	useEffect(() => {
		setLocalSide(side);
		setLocalAmount(amount);
	}, [side, amount]);

	const quoteIdentity = quote
		? `${quote.side}|${quote.amount_in}|${quote.token_out}|${quote.source.observed_at}`
		: `no-quote|${side}|${amount}`;

	useEffect(() => {
		setIdempotencyKey(newIdempotencyKey());
		setState({ kind: "idle" });
		// A new quote is a new trade. Never carry an armed notice, an open protect form,
		// a validation error or the custom highlight across from the previous fill.
		setProtectOpen(false);
		setProtectArmed(false);
		setProtectNotice(null);
		setFormError(null);
		setCustomMode(false);
	}, [quoteIdentity]);

	const quoteUsd = quote?.side === "buy" ? quote.amount_in : amount;
	const matches =
		quote !== null && quote.side === localSide && sameAmount(localAmount, quoteUsd);


	function previewWith(nextSide: Side, nextAmount: string) {
		if (!/^\d+(\.\d{1,2})?$/.test(nextAmount.trim()) || Number(nextAmount) <= 0) {
			setFormError("Enter a positive USD amount with at most 2 decimals.");
			return;
		}
		setFormError(null);
		setCustomMode(false);
		const remembered = Number(nextAmount);
		if (!presets.includes(remembered)) {
			const next = [...presets, remembered].slice(-MAX_PRESETS);
			setPresets(next);
			writePresets(next);
		}
		const query = new URLSearchParams({ side: nextSide, amount: nextAmount.trim() });
		startTransition(() => {
			router.push(`${pathname}?${query.toString()}`);
		});
	}

	function choosePreset(value: number) {
		const text = String(value);
		setLocalAmount(text);
		setCustomMode(false);
		setFormError(null);
		previewWith(localSide, text);
	}

	function chooseSide(next: Side) {
		setLocalSide(next);
		setFormError(null);
		previewWith(next, localAmount);
	}

	async function confirm() {
		if (!quote || !matches || !idempotencyKey) return;
		setState({ kind: "submitting" });
		try {
			const order = await postPaperOrder({
				chain,
				token_address: tokenAddress,
				side: quote.side === "sell" ? "sell" : "buy",
				amount_usd: localAmount,
				idempotency_key: idempotencyKey,
			});
			if (order.status === "rejected" || order.status === "cancelled") {
				setState({ kind: "rejected", order, message: order.reason ?? "the API returned no reason" });
			} else {
				setState({ kind: "accepted", order });
			}
		} catch (error) {
			const apiError = error instanceof ApiError ? error : null;
			setState({
				kind: "error",
				code: apiError?.code ?? "WEB_ERROR",
				message: error instanceof Error ? error.message : String(error),
				retryable: apiError?.retryable ?? false,
			});
		}
	}

	const submitting = state.kind === "submitting";
	const accepted = state.kind === "accepted";
	const disabled = !quote || !matches || !idempotencyKey || submitting || accepted;
	const blockReason = quoteReason
		? `${quoteReason.code}: ${quoteReason.message}`
		: !quote
			? "No quote has been rendered yet. Preview a quote first."
			: !matches
				? `The quote on this page is for ${quote.side} ${formatUsd(quoteUsd ?? amount)}. Preview at the current amount first.`
				: null;

	async function armProtection() {
		const lossPct = protectPct.trim();
		if (!/^\d+(\.\d{1,2})?$/.test(lossPct) || Number(lossPct) <= 0) {
			setProtectNotice({ tone: "err", text: "Enter a positive stop-loss percent." });
			return;
		}
		setProtectBusy(true);
		setProtectNotice(null);
		try {
			const arm = await createProtectionArm(
				{
					chain,
					token_address: tokenAddress,
					token_symbol: symbol,
					kind: "stop_loss",
					params: { loss_pct: lossPct },
				},
				mode,
			);
			setProtectNotice({ tone: "ok", text: `Armed ${armLabel(arm.kind)} for ${symbol}.` });
			setProtectArmed(true);
		} catch (error) {
			setProtectNotice({
				tone: "err",
				text: error instanceof Error ? error.message : "The arm could not be created.",
			});
		} finally {
			setProtectBusy(false);
		}
	}

	const showProtect = accepted && (state.kind === "accepted" ? state.order.side === "buy" : false);

	return (
		<div className="stack-sm" data-testid="quick-trade-bar">
			<div className="row" style={{ flexWrap: "wrap", gap: 8 }} role="group" aria-label="Quick trade presets">
				{presets.map((value) => (
					<button
						key={value}
						type="button"
						className={`btn sm${sameAmount(localAmount, String(value)) ? " primary" : ""}`}
						onClick={() => choosePreset(value)}
						aria-pressed={sameAmount(localAmount, String(value))}
						data-testid={`quick-trade-preset-${value}`}
					>
						{value}
					</button>
				))}
				<button
					type="button"
					className={`btn sm${customMode ? " primary" : ""}`}
					onClick={() => {
						setCustomMode(true);
						setTimeout(() => inputRef.current?.focus(), 0);
					}}
					aria-pressed={customMode}
					data-testid="quick-trade-preset-custom"
				>
					Custom
				</button>
			</div>

			<div className="seg" role="group" aria-label="Quick trade side">
				{(["buy", "sell"] as Side[]).map((option) => (
					<button
						key={option}
						type="button"
						aria-pressed={localSide === option}
						onClick={() => chooseSide(option)}
					>
						{option === "buy" ? "Buy" : "Sell"}
					</button>
				))}
			</div>

			<form
				className="row"
				style={{ alignItems: "flex-end", gap: 8 }}
				onSubmit={(event) => {
					event.preventDefault();
					previewWith(localSide, localAmount);
				}}
			>
				<label className="field" style={{ flex: 1 }}>
					<span>Amount USD · PAPER</span>
					<input
						ref={inputRef}
						className="input mono"
						name="quick-trade-amount"
						type="text"
						inputMode="decimal"
						value={localAmount}
						onChange={(event) => {
							setLocalAmount(event.target.value);
							setFormError(null);
						}}
						aria-label="Quick trade amount in USD"
						autoComplete="off"
					/>
				</label>
				<button
					className="btn"
					type="submit"
					data-testid="quick-trade-preview"
					aria-label="Preview quick trade quote"
				>
					Preview
				</button>
			</form>

			{formError && <span className="small err">{formError}</span>}

			<div className={`note${quoteReason ? " err" : ""}`}>
				{quoteReason ? (
					<>
						<Badge tone="err">{quoteReason.code}</Badge> {quoteReason.message}
					</>
				) : quote && matches ? (
					<span className="stack-sm">
						<span className="row">
							<span className="micro">{quote.side === "buy" ? "Expected out" : "Expected USD out"}</span>
							<span className="spacer" style={{ flex: 1 }} />
							<Badge tone="ghost">PAPER</Badge>
							<span className="mono">
								{quote.side === "buy"
									? `${formatAmount(quote.amount_out)} ${symbol}`
									: formatUsd(quote.amount_out)}
							</span>
						</span>
						<span className="row">
							<span className="micro">Price</span>
							<span className="spacer" style={{ flex: 1 }} />
							<Badge tone="ghost">PAPER</Badge>
							<span className="mono">{formatPrice(quote.price)}</span>
						</span>
						<span className="row">
							<span className="micro">Route · source · age</span>
							<span className="spacer" style={{ flex: 1 }} />
							<Badge tone="warn">{quoteKindLabel(quote.kind)}</Badge>
							<span className="mono small">
								{quote.route ?? "no route reported"} · {sourceLabel(quote.source.name)} ·{" "}
								{ageLabel(quote.source.observed_at, now)}
							</span>
						</span>
					</span>
				) : (
					<span>{blockReason}</span>
				)}
			</div>

			{quote && matches && <CostSheet quote={quote} symbol={symbol} now={now} />}

			<div className="row">
				<button
					className={`btn lg ${disabled ? "ghost" : "primary"}`}
					type="button"
					style={{ flex: 1 }}
					disabled={disabled}
					onClick={confirm}
					data-testid="quick-trade-submit"
					aria-label="Confirm paper order"
				>
					{submitting ? "Submitting…" : accepted ? "Order submitted" : "Confirm paper order"}
				</button>
				<Badge tone="ghost">PAPER</Badge>
			</div>

			<div className="small muted">
				{blockReason ??
					"Confirm POSTs /api/v1/paper/orders with this browser-generated idempotency key. Paper only — no keys, no signing."}
				{idempotencyKey && (
					<span className={`mono ${styles.idem}`}>idem {idempotencyKey.slice(0, 8)}…</span>
				)}
			</div>

			{state.kind === "accepted" && (
				<div className={`note ${styles.ticketResult}`}>
					<div className="row mb-8">
						<Badge tone="ok">PAPER FILL</Badge>
						<span className="mono">{state.order.id}</span>
						<Badge tone="ghost">{state.order.status}</Badge>
					</div>
					<div className="small">
						{state.order.side.toUpperCase()} {formatUsd(state.order.amount_usd)} ·{" "}
						{state.order.quote_kind ? quoteKindLabel(state.order.quote_kind) : "PAPER"} ·{" "}
						{state.order.filled_at ? `filled ${state.order.filled_at}` : `created ${state.order.created_at}`}
					</div>
					<div className="small muted mt-8">
						The API accepted this paper order. No funds moved and no transaction was signed.
					</div>
					{showProtect && (
						<div className="row mt-8">
							<button
								className="btn"
								type="button"
								onClick={() => setProtectOpen((value) => !value)}
								data-testid="quick-trade-protect"
								aria-label="Protect this position"
							>
								Protect this position
							</button>
						</div>
					)}
				</div>
			)}

			{protectOpen && (
				<div className="stack-sm" data-testid="quick-trade-protect-form">
					<div className="row mb-8">
						<Badge tone="ghost">PAPER</Badge>
						<span className="micro">Protect this position · stop-loss percent preset</span>
					</div>
					<label className="field">
						<span>Loss (%)</span>
						<input
							className="input mono"
							type="text"
							value={protectPct}
							onChange={(event) => setProtectPct(event.target.value)}
							aria-label="Loss (%)"
							autoComplete="off"
						/>
					</label>
					<div className="row">
						<button
							className="btn primary"
							type="button"
							disabled={protectBusy}
							onClick={armProtection}
							data-testid="quick-trade-arm"
							aria-label="Arm protection"
						>
							{protectBusy ? "Arming…" : "Arm protection"}
						</button>
					</div>
					{protectNotice && (
						<div className={`note ${protectNotice.tone === "ok" ? "ok" : "err"} mb-12`} role="status">
							{protectNotice.text}
						</div>
					)}
					{protectArmed && (
						<div className="small muted">Armed confirmation recorded. Unknown triggers stay —, never 0.</div>
					)}
				</div>
			)}

			{state.kind === "rejected" && (
				<div className={`note err ${styles.ticketResult}`}>
					<div className="row mb-8">
						<Badge tone="err">ORDER_REJECTED</Badge>
						<span className="mono">{state.order.id}</span>
					</div>
					<div className="small">{state.message}</div>
					<div className="small muted mt-8">Nothing was filled. The API reason is printed unchanged.</div>
				</div>
			)}

			{state.kind === "error" && (
				<div className={`note err ${styles.ticketResult}`}>
					<div className="row mb-8">
						<Badge tone="err">{state.code}</Badge>
						{state.retryable && <Badge tone="warn">retryable</Badge>}
					</div>
					<div className="small">{state.message}</div>
					<div className="small muted mt-8">
						Nothing was filled. Retrying keeps the same idempotency key, so the API can deduplicate.
					</div>
				</div>
			)}
		</div>
	);
}