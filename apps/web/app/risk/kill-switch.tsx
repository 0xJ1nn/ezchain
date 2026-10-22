"use client";

import { useState } from "react";
import { Badge } from "@/components/ui";
import {
	PaperApiError,
	setKillSwitch,
	type KillSwitchState,
} from "@/lib/paper-api";

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
						<span className="strong">Strategy-scoped kill switch</span>
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
							{next ? "Confirm: halt new strategy risk" : "Confirm: release the switch"}
						</div>
						<p className="small muted mt-8">
							{next
								? "Armed, new strategy-attributed orders are refused before a quote. Manual paper orders and existing positions are not blocked; the switch does not sell or close anything."
								: "Releasing allows new strategy-attributed orders again. The API requires a reason for every release."}
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
