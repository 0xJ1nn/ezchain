"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";
import { Badge } from "@/components/ui";
import {
	PaperApiError,
	setKillSwitch,
	type KillSwitchState,
} from "@/lib/paper-api";

// Both controls read the same persisted flag. The server snapshot is the first paint;
// a mutation from either control publishes the response here so the other one on the
// page shows the same state without a reload. Nothing is written on the server.
type KillSwitchListener = (state: KillSwitchState) => void;

const killSwitchListeners = new Set<KillSwitchListener>();

function publishKillSwitch(state: KillSwitchState) {
	for (const listener of killSwitchListeners) listener(state);
}

function useKillSwitchEvents(apply: KillSwitchListener) {
	useEffect(() => {
		killSwitchListeners.add(apply);
		return () => {
			killSwitchListeners.delete(apply);
		};
	}, [apply]);
}

export function KillSwitchControl({
	initial,
	initialError,
}: {
	initial: KillSwitchState | null;
	initialError: string | null;
}) {
	const [state, setState] = useState<KillSwitchState | null>(initial);
	const [asking, setAsking] = useState(false);
	const [reason, setReason] = useState("");
	const [pending, setPending] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const [notice, setNotice] = useState<string | null>(null);

	useKillSwitchEvents(setState);

	const active = state?.active === true;
	const next = !active;

	function begin() {
		setAsking(true);
		setReason("");
		setError(null);
		setNotice(null);
	}

	function cancel() {
		setAsking(false);
		setReason("");
		setError(null);
	}

	async function confirm() {
		if (pending) return;
		if (!reason.trim()) {
			setError("A reason is required. The API records it with the switch state.");
			return;
		}
		setPending(true);
		setError(null);
		try {
			const updated = await setKillSwitch(next, reason.trim());
			setState(updated);
			publishKillSwitch(updated);
			setAsking(false);
			setReason("");
			setNotice(
				`${updated.active ? "Armed" : "Released"} · persisted ${
					updated.updated_at ? `at ${updated.updated_at}` : "by the API"
				}`,
			);
		} catch (caught) {
			const apiError = caught instanceof PaperApiError ? caught : null;
			setError(
				apiError
					? `${apiError.code}: ${apiError.message}`
					: caught instanceof Error
						? caught.message
						: "The switch request failed.",
			);
		} finally {
			setPending(false);
		}
	}

	return (
		<div>
			<div className="between" style={{ gap: 12 }}>
				<div>
					<div className="row">
						{state ? (
							<Badge tone={state.active ? "err" : "ok"}>
								{state.active ? "ARMED" : "RELEASED"}
							</Badge>
						) : (
							<Badge tone="warn">STATE UNKNOWN</Badge>
						)}
						<span className="strong">Kill switch · global and strategy</span>
					</div>
					<div className="small muted mt-8">
						{state
							? `Reason: ${state.reason ?? "no reason recorded"} · updated ${
									state.updated_at ?? "unknown time"
								}`
							: "The API did not return the persisted flag. Nothing is guessed."}
					</div>
				</div>
				<button
					type="button"
					className={`btn ${next ? "danger" : "primary"}`}
					onClick={begin}
					disabled={asking || pending}
				>
					{next ? "Arm kill switch" : "Release kill switch"}
				</button>
			</div>

			{initialError && (
				<div className="note err mt-12" role="alert">
					{initialError}
				</div>
			)}

			{asking && (
				<div className="panel mt-12" style={{ boxShadow: "none" }}>
					<div className="panel-body">
						<div className="strong">
							{next ? "Confirm: halt new risk" : "Confirm: release the switch"}
						</div>
						<p className="small muted mt-8">
							{next
								? "Armed, every new risk-increasing order is refused before a quote, whoever places it, with reason KILL_SWITCH_ARMED. Reduce-only exits and existing positions still settle; the switch does not sell or close anything."
								: "Releasing allows new entries again. The API requires a reason for every release."}
						</p>
						<div className="field mt-12">
							<label htmlFor="kill-switch-reason">Reason for the operator log</label>
							<textarea
								id="kill-switch-reason"
								className="textarea"
								rows={2}
								value={reason}
								maxLength={200}
								onChange={(event) => setReason(event.target.value)}
								placeholder="Why are you changing the switch?"
							/>
							<span className="hint">
								Stored server-side with the flag. This build identifies the deployment,
								not an individual operator.
							</span>
						</div>
						{error && (
							<div className="note err mt-12" role="alert">
								{error}
							</div>
						)}
						<div className="row mt-12">
							<button
								type="button"
								className={`btn ${next ? "danger solid" : "primary"}`}
								onClick={confirm}
								disabled={pending}
							>
								{pending ? "Sending…" : next ? "Confirm arm" : "Confirm release"}
							</button>
							<button
								type="button"
								className="btn"
								onClick={cancel}
								disabled={pending}
							>
								Cancel
							</button>
						</div>
					</div>
				</div>
			)}

			{notice && !asking && (
				<div className="note mt-12" role="status">
					{notice}
				</div>
			)}
		</div>
	);
}

// The top-bar control reads the same persisted flag as the risk page. It confirms in
// place so the operator never leaves the page they are working on, and the launcher
// reports UNKNOWN rather than guessing a state the API did not return.
export function TopbarKillSwitch({
	initial,
	initialError,
}: {
	initial: KillSwitchState | null;
	initialError: string | null;
}) {
	const [state, setState] = useState<KillSwitchState | null>(initial);
	const [asking, setAsking] = useState(false);
	const [reason, setReason] = useState("");
	const [pending, setPending] = useState(false);
	const [error, setError] = useState<string | null>(initialError);
	const [notice, setNotice] = useState<string | null>(null);
	const field = useRef<HTMLTextAreaElement | null>(null);
	const titleId = useId();

	useKillSwitchEvents(setState);

	const active = state?.active === true;
	const next = !active;
	const label = state ? (active ? "ARMED" : "RELEASED") : "UNKNOWN";
	// Scopes come from the response, never from the shape of the build. Without a state
	// the control names no scope and promises no action.
	const scopes = state?.scopes?.length ? state.scopes.join(" and ") : null;
	const description = state
		? `Kill switch: ${label}${scopes ? ` (${scopes})` : ""} — press k to ${
				next ? "arm" : "release"
			}`
		: "Kill switch: UNKNOWN — the API did not return the persisted state; press k for details";

	function begin() {
		setAsking(true);
		setReason("");
		setError(initialError);
		setNotice(null);
	}

	function cancel() {
		if (pending) return;
		setAsking(false);
		setReason("");
		setError(initialError);
	}

	async function confirm() {
		if (pending) return;
		if (!reason.trim()) {
			setError("A reason is required. The API records it with the switch state.");
			return;
		}
		setPending(true);
		setError(null);
		try {
			const updated = await setKillSwitch(next, reason.trim());
			setState(updated);
			publishKillSwitch(updated);
			setAsking(false);
			setReason("");
			const levels = updated.scopes?.length
				? updated.scopes.join(" and ")
				: "levels not reported by the API";
			setNotice(`${updated.active ? "Armed" : "Released"} · ${levels}`);
		} catch (caught) {
			const apiError = caught instanceof PaperApiError ? caught : null;
			setError(
				apiError
					? `${apiError.code}: ${apiError.message}`
					: caught instanceof Error
						? caught.message
						: "The switch request failed.",
			);
		} finally {
			setPending(false);
		}
	}

	useEffect(() => {
		if (!asking) return;
		field.current?.focus();
		function onKey(event: KeyboardEvent) {
			if (event.key !== "Escape" || pending) return;
			event.preventDefault();
			setAsking(false);
			setReason("");
			setError(initialError);
		}
		document.addEventListener("keydown", onKey);
		return () => document.removeEventListener("keydown", onKey);
	}, [asking, pending, initialError]);

	useEffect(() => {
		function onKey(event: KeyboardEvent) {
			if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
			if (event.key !== "k" && event.key !== "K") return;
			const target = event.target as HTMLElement | null;
			if (
				target &&
				(target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName))
			) {
				return;
			}
			if (asking) return;
			event.preventDefault();
			setAsking(true);
			setReason("");
			setError(initialError);
			setNotice(null);
		}
		document.addEventListener("keydown", onKey);
		return () => document.removeEventListener("keydown", onKey);
	}, [asking, initialError]);

	return (
		<div className="kill-wrap">
			<button
				type="button"
				className={`kill ${state ? (active ? "armed" : "released") : "unknown"}`}
				aria-haspopup="dialog"
				aria-expanded={asking}
				aria-controls={asking ? titleId : undefined}
				aria-label={`Kill switch: ${label}${scopes ? ` — ${scopes}` : ""}`}
				title={description}
				onClick={() => (asking ? cancel() : begin())}
			>
				<span className="kill-label" data-testid="topbar-kill-label">
					<span className="kill-name">Kill switch</span>
					<span className="kill-state">{label}</span>
				</span>
			</button>
			{notice && (
				<span className="kill-live" role="status" aria-live="polite">
					{notice}
				</span>
			)}

			{asking && (
				<div className="kill-pop" role="dialog" aria-labelledby={titleId}>
					<div className="strong" id={titleId}>
						{state
							? next
								? "Arm the kill switch?"
								: "Release the kill switch?"
							: "Kill switch state unavailable"}
					</div>
					{state ? (
						<>
							<p className="small muted mt-8">
								{next
									? "New risk-increasing orders are refused by the API with KILL_SWITCH_ARMED. Exits and reduce-only sells still settle."
									: "Releasing lets new entries through again. The API requires a reason."}{" "}
								{scopes
									? `Applies at the ${scopes} levels.`
									: "The API did not report which levels this flag governs."}
							</p>
							<div className="field mt-12">
								<label htmlFor="topbar-kill-reason">Reason for the operator log</label>
								<textarea
									id="topbar-kill-reason"
									className="textarea"
									rows={2}
									maxLength={200}
									value={reason}
									ref={field}
									onChange={(event) => setReason(event.target.value)}
									placeholder="Why are you changing the switch?"
								/>
								<span className="hint">Stored server-side with the flag.</span>
							</div>
							{error && (
								<div className="note err mt-12" role="alert">
									{error}
								</div>
							)}
							<div className="row mt-12">
								<button
									type="button"
									className={`btn ${next ? "danger solid" : "primary"}`}
									onClick={confirm}
									disabled={pending}
								>
									{pending ? "Sending…" : next ? "Confirm arm" : "Confirm release"}
								</button>
								<button
									type="button"
									className="btn"
									onClick={cancel}
									disabled={pending}
								>
									Cancel
								</button>
								<Link className="small" href="/risk">
									Risk page →
								</Link>
							</div>
						</>
					) : (
						<>
							<p className="small muted mt-8">
								The API did not return the persisted flag, so this control will not guess
								which action to take.
							</p>
							{error && (
								<div className="note err mt-12" role="alert">
									{error}
								</div>
							)}
							<div className="row mt-12">
								<Link className="btn" href="/risk">
									Open risk page
								</Link>
								<button type="button" className="btn" onClick={cancel}>
									Close
								</button>
							</div>
						</>
					)}
				</div>
			)}
		</div>
	);
}
