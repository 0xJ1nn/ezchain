"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, useTransition } from "react";
import { Badge } from "@/components/ui";
import {
	ApiError,
	postPaperOrder,
	type PaperOrder,
	type Quote,
	type Reason,
	type Side,
} from "@/lib/api";
import { ageLabel, formatAmount, formatPrice, formatUsd, quoteKindLabel, sourceLabel } from "@/lib/live-format";
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

/**
 * Paper trade ticket. Amount and side choose the *server* quote (a URL navigation, so the
 * page re-fetches); Confirm only posts when the rendered quote matches the current
 * selection. The idempotency key is generated in the browser and reused on retry.
 *
 * There are no key fields: the API accepts a paper order only.
 */
export function PaperTicket({
	chain,
	tokenAddress,
	symbol,
	side,
	amount,
	quote,
	quoteReason,
}: {
	chain: string;
	tokenAddress: string;
	symbol: string;
	side: Side;
	amount: string;
	quote: Quote | null;
	quoteReason: Reason | null;
}) {
	const router = useRouter();
	const pathname = usePathname();
	const [pending, startTransition] = useTransition();
	const [localSide, setLocalSide] = useState<Side>(side);
	const [localAmount, setLocalAmount] = useState(amount);
	const [formError, setFormError] = useState<string | null>(null);
	const [state, setState] = useState<TicketState>({ kind: "idle" });
	const [idempotencyKey, setIdempotencyKey] = useState<string | null>(null);

	const quoteIdentity = quote
		? `${quote.side}|${quote.amount_in}|${quote.token_out}|${quote.source.observed_at}`
		: `no-quote|${side}|${amount}`;

	// New selection => new idempotency key. A retry of the same selection reuses it.
	useEffect(() => {
		setIdempotencyKey(newIdempotencyKey());
		setState({ kind: "idle" });
	}, [quoteIdentity]);

	const matches =
		quote !== null && quote.side === localSide && sameAmount(localAmount, quote.amount_in);
	const amountValid = /^\d+(\.\d{1,2})?$/.test(localAmount.trim()) && Number(localAmount) > 0;

	function preview() {
		if (!amountValid) {
			setFormError("Enter a positive USD amount with at most 2 decimals.");
			return;
		}
		setFormError(null);
		const query = new URLSearchParams({ side: localSide, amount: localAmount.trim() });
		startTransition(() => {
			router.push(`${pathname}?${query.toString()}`);
		});
	}

	async function confirm() {
		if (!quote || !matches || !idempotencyKey) return;
		setState({ kind: "submitting" });
		try {
			const order = await postPaperOrder({
				chain,
				token_address: tokenAddress,
				side: quote.side === "sell" ? "sell" : "buy",
				amount_usd: quote.amount_in,
				idempotency_key: idempotencyKey,
			});
			if (order.status === "rejected" || order.status === "cancelled") {
				setState({
					kind: "rejected",
					order,
					message: order.reason ?? "the API returned no reason",
				});
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
				? `The quote on this page is for ${quote.side} ${formatUsd(quote.amount_in)}. Preview at the current amount first.`
				: null;

	return (
		<div className="stack-sm">
			<form
				className="stack-sm"
				onSubmit={(event) => {
					event.preventDefault();
					preview();
				}}
			>
				<div className="seg" role="group" aria-label="Paper side">
					{(["buy", "sell"] as Side[]).map((option) => (
						<button
							key={option}
							type="button"
							aria-pressed={localSide === option}
							onClick={() => {
								setLocalSide(option);
								setFormError(null);
							}}
						>
							{option === "buy" ? "Buy" : "Sell"}
						</button>
					))}
				</div>
				<label className="field">
					<span>Amount USD · PAPER</span>
					<input
						className="input mono"
						name="amount"
						type="text"
						inputMode="decimal"
						value={localAmount}
						onChange={(event) => {
							setLocalAmount(event.target.value);
							setFormError(null);
						}}
						aria-label="Paper amount in USD"
						aria-describedby="paper-ticket-hint"
						autoComplete="off"
					/>
				</label>
				<div className="row">
					<button className="btn" type="submit" disabled={pending}>
						{pending ? "Previewing…" : "Preview quote"}
					</button>
					<span className="small muted">Re-quotes on the server at this size.</span>
				</div>
				{formError && <span className="small err">{formError}</span>}
			</form>

			<div className={`note${quoteReason ? " err" : ""}`}>
				{quoteReason ? (
					<>
						<Badge tone="err">{quoteReason.code}</Badge> {quoteReason.message}
					</>
				) : quote && matches ? (
					<span className="stack-sm">
						<span className="row">
							<span className="micro">Expected out</span>
							<span className="spacer" style={{ flex: 1 }} />
							<Badge tone="ghost">PAPER</Badge>
							<span className="mono">
								{formatAmount(quote.amount_out)} {symbol}
							</span>
						</span>
						<span className="row">
							<span className="micro">Price</span>
							<span className="spacer" style={{ flex: 1 }} />
							<Badge tone="ghost">PAPER</Badge>
							<span className="mono">{formatPrice(quote.price)}</span>
						</span>
						<span className="row">
							<span className="micro">Kind · source · age</span>
							<span className="spacer" style={{ flex: 1 }} />
							<Badge tone="warn">{quoteKindLabel(quote.kind)}</Badge>
							<span className="mono small">
								{sourceLabel(quote.source.name)} · {ageLabel(quote.source.observed_at)}
							</span>
						</span>
					</span>
				) : (
					<span>{blockReason}</span>
				)}
			</div>

			<div className="row">
				<button
					className={`btn lg ${disabled ? "ghost" : "primary"}`}
					type="button"
					style={{ flex: 1 }}
					disabled={disabled}
					onClick={confirm}
					aria-describedby="paper-ticket-hint"
				>
					{submitting ? "Submitting…" : accepted ? "Order submitted" : "Confirm paper order"}
				</button>
				<Badge tone="ghost">PAPER</Badge>
			</div>

			<div className="small muted" id="paper-ticket-hint">
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
				</div>
			)}

			{state.kind === "rejected" && (
				<div className={`note err ${styles.ticketResult}`}>
					<div className="row mb-8">
						<Badge tone="err">ORDER_REJECTED</Badge>
						<span className="mono">{state.order.id}</span>
					</div>
					<div className="small">{state.message}</div>
					<div className="small muted mt-8">
						Nothing was filled. The API reason is printed unchanged.
					</div>
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
						Nothing was filled. Retrying keeps the same idempotency key, so the API can
						deduplicate.
					</div>
				</div>
			)}
		</div>
	);
}
